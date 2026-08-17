"""Bridges the Discord bot to the core game engine (main.py et al.) - the same World/run_command
the CLI and the web terminal already use, just with one World per Discord guild instead of one
World per process, and print() output captured into a string instead of hitting a real terminal.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import shlex
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import main as game  # noqa: E402
from main import World  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)

_worlds: dict[int, World] = {}
_assignments: dict[int, dict[int, str]] = {}
_locks: dict[int, asyncio.Lock] = {}
_permissions: dict[int, dict[str, list[int]]] = {}
_pending_snapshots: dict[int, bytes] = {}


def get_lock(guild_id: int) -> asyncio.Lock:
    """One lock per guild, not one global lock - different guilds' worlds are fully independent,
    so there's no reason a slow command in one server should make another server wait."""
    if guild_id not in _locks:
        _locks[guild_id] = asyncio.Lock()
    return _locks[guild_id]


def _world_path(guild_id: int) -> Path:
    return DATA_DIR / f"{guild_id}.json"


def _assignments_path(guild_id: int) -> Path:
    return DATA_DIR / f"{guild_id}_players.json"


def _permissions_path(guild_id: int) -> Path:
    return DATA_DIR / f"{guild_id}_permissions.json"


def _backup_path(guild_id: int) -> Path:
    return DATA_DIR / f"{guild_id}_previous.json"


def get_world(guild_id: int) -> World:
    """The in-memory World for this guild, loading it from disk on first use if a save already
    exists there, or starting a fresh empty one otherwise."""
    if guild_id not in _worlds:
        world = World()
        path = _world_path(guild_id)
        if path.exists():
            database.load_into_world(world, str(path))
        _worlds[guild_id] = world
    return _worlds[guild_id]


def save_world(guild_id: int) -> None:
    world = _worlds.get(guild_id)
    if world is not None:
        database.save_world(world, str(_world_path(guild_id)))


def _snapshot_before_switch(guild_id: int) -> bytes | None:
    """Reads the guild's current save file, right before new-world/import is about to replace
    it - a copy goes to _backup_path() as a local fallback, and the bytes are returned so the
    caller can also queue them up (via _pending_snapshots) for the bot to offer as an automatic
    download. None if there was nothing to back up yet (a guild that's never had a world)."""
    path = _world_path(guild_id)
    if not path.exists():
        return None
    content = path.read_bytes()
    _backup_path(guild_id).write_bytes(content)
    return content


def get_pending_snapshot(guild_id: int) -> bytes | None:
    """The previous world's save bytes, if new-world just replaced one - consumed (popped) on
    read so a stale snapshot from an earlier switch can't leak into an unrelated later check."""
    return _pending_snapshots.pop(guild_id, None)


def get_assignments(guild_id: int) -> dict[int, str]:
    if guild_id not in _assignments:
        path = _assignments_path(guild_id)
        if path.exists():
            _assignments[guild_id] = {int(k): v for k, v in json.loads(path.read_text()).items()}
        else:
            _assignments[guild_id] = {}
    return _assignments[guild_id]


def assign_country(guild_id: int, user_id: int, country_name: str) -> None:
    assignments = get_assignments(guild_id)
    assignments[user_id] = country_name
    _assignments_path(guild_id).write_text(json.dumps(assignments))


def get_assigned_country(guild_id: int, user_id: int) -> str | None:
    return get_assignments(guild_id).get(user_id)


def get_command_permissions(guild_id: int) -> dict[str, list[int]]:
    """command name -> role IDs granted to run it, on top of (never instead of) Manage Server -
    see bot.py's has_permission()."""
    if guild_id not in _permissions:
        path = _permissions_path(guild_id)
        if path.exists():
            _permissions[guild_id] = json.loads(path.read_text())
        else:
            _permissions[guild_id] = {}
    return _permissions[guild_id]


def _save_permissions(guild_id: int) -> None:
    _permissions_path(guild_id).write_text(json.dumps(get_command_permissions(guild_id)))


def permit_role(guild_id: int, command_name: str, role_id: int) -> None:
    roles = get_command_permissions(guild_id).setdefault(command_name, [])
    if role_id not in roles:
        roles.append(role_id)
        _save_permissions(guild_id)


def revoke_role(guild_id: int, command_name: str, role_id: int) -> bool:
    """Whether the role actually had explicit permission to remove - False means /revoke has
    nothing to do (it wasn't granted in the first place, not an error)."""
    perms = get_command_permissions(guild_id)
    roles = perms.get(command_name, [])
    if role_id not in roles:
        return False
    roles.remove(role_id)
    if not roles:
        del perms[command_name]
    _save_permissions(guild_id)
    return True


def role_has_command_permission(guild_id: int, command_name: str, role_ids: list[int]) -> bool:
    granted = get_command_permissions(guild_id).get(command_name, [])
    return any(role_id in granted for role_id in role_ids)


def build_line(command: str, *args: str) -> str:
    """Assembles a command line from a command name and already-separate argument values (e.g.
    from Discord slash-command parameters), quoting each one so a country/division name with
    spaces or a stray quote character can't be mis-split by run_command's own shlex parsing."""
    return " ".join([command, *(shlex.quote(a) for a in args)])


# save/open/list-worlds/rename-world all name worlds by a string in the shared
# ~/proppunk game files/ directory - fine for one CLI user, but two guilds picking the same
# world name would collide, and open/rename-world could read or clobber another guild's save.
# Each guild already has its own world, auto-saved by guild ID after every command, so these
# don't have a coherent per-guild meaning; new-world is handled separately below, entirely
# in-memory, instead of touching that shared directory at all.
_UNAVAILABLE_COMMANDS = {"save", "open", "list-worlds", "rename-world"}


def _reset_world(guild_id: int, world: World, line: str) -> str:
    try:
        args = shlex.split(line)[1:]
    except ValueError as e:
        return f"Error parsing command: {e}"
    if len(args) not in (3, 4):
        return "Usage: new-world <name> <width> <height> [start_year]"
    name, width_str, height_str = args[0], args[1], args[2]
    try:
        width, height = int(width_str), int(height_str)
    except ValueError:
        return "Width and height must be integers."
    if width <= 0 or height <= 0:
        return "Width and height must be positive."
    start_year = 0
    if len(args) == 4:
        try:
            start_year = int(args[3])
        except ValueError:
            return "Start year must be an integer."

    snapshot = _snapshot_before_switch(guild_id)
    if snapshot is not None:
        _pending_snapshots[guild_id] = snapshot

    world.nodes.clear()
    world.countries.clear()
    world.wars.clear()
    world.width = width
    world.height = height
    world.year = start_year
    world.start_year = start_year
    world.save_path = None
    save_world(guild_id)
    return f"Reset this server's world to '{name}' ({width}x{height} grid, starting year {start_year})."


def import_world(guild_id: int, content: bytes) -> tuple[str, bytes | None]:
    """Loads `content` (the raw bytes of an uploaded save file) as this guild's new world,
    replacing whatever it had before - parsed into a temp file first, via the same
    database.load_into_world the CLI's 'open' uses, so a corrupt or non-nodetech upload raises
    instead of silently clobbering the guild's last-known-good save (and never gets treated as a
    real switch, so nothing gets snapshotted for a failed attempt). Player assignments are
    cleared too, since they name countries that may not exist in the new world at all.

    Returns (confirmation message, the outgoing world's save bytes if one existed) - the bytes
    are the same thing _reset_world() queues into _pending_snapshots for new-world, just handed
    back directly here since import already has its own dedicated call path rather than going
    through the generic string-only run_command()."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"Not a text/JSON file: {e}") from e

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        tmp.write(text)
        tmp_path = tmp.name
    try:
        world = World()
        database.load_into_world(world, tmp_path)
    except (json.JSONDecodeError, KeyError, ValueError, AttributeError, TypeError) as e:
        raise ValueError(f"Couldn't read that as a nodetech save file: {e}") from e
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    previous = _snapshot_before_switch(guild_id)

    world.save_path = None
    _worlds[guild_id] = world
    save_world(guild_id)

    _assignments[guild_id] = {}
    _assignments_path(guild_id).unlink(missing_ok=True)

    message = f"Imported world: {len(world.nodes)} nodes, {len(world.countries)} countries. Player assignments were reset."
    return message, previous


async def import_world_async(guild_id: int, content: bytes) -> tuple[str, bytes | None]:
    async with get_lock(guild_id):
        return await asyncio.to_thread(import_world, guild_id, content)


def export_world(guild_id: int) -> bytes:
    """The current guild's world as JSON bytes, ready to send as a Discord attachment - saves
    first to guarantee the file on disk actually matches the in-memory World (covers a guild
    that's never had a command run yet, which wouldn't have written anything to disk otherwise),
    then just reads it back rather than re-serializing separately."""
    get_world(guild_id)
    save_world(guild_id)
    return _world_path(guild_id).read_bytes()


async def export_world_async(guild_id: int) -> bytes:
    async with get_lock(guild_id):
        return await asyncio.to_thread(export_world, guild_id)


def export_country_report(guild_id: int, country_name: str) -> str:
    """Markdown report text for one country - the same content main.py's `export-country`
    writes to disk, generated directly here instead of going through that command (and its
    shared ~/proppunk game files/<name>_report.md path and process-global _last_export_path)
    so two guilds exporting around the same time can never race or collide on either. Raises
    ValueError if the country doesn't exist."""
    world = get_world(guild_id)
    country = world.get_country(country_name)
    if country is None:
        raise ValueError(f"No such country '{country_name}'.")
    return game._country_report_markdown(world, country)


def export_world_report(guild_id: int) -> str:
    """Markdown report text for the whole world - see export_country_report for why this
    bypasses main.py's `export-world` command and its shared save-dir path entirely."""
    return game._world_report_markdown(get_world(guild_id))


def run_command(guild_id: int, line: str) -> str:
    """Runs one command line against this guild's World, capturing whatever it prints (the same
    trick the web terminal uses, redirecting Python's stdout instead of a real terminal) and
    auto-saving afterward so a crash between commands can't lose state."""
    world = get_world(guild_id)
    try:
        first_word = shlex.split(line)[0].lower()
    except (ValueError, IndexError):
        first_word = ""

    if first_word == "new-world":
        return _reset_world(guild_id, world, line)
    if first_word in _UNAVAILABLE_COMMANDS:
        return (
            f"'{first_word}' isn't available here - this server already has its own world, "
            f"auto-saved after every command. Use 'new-world' to reset it."
        )

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        game.run_command(world, line)
    save_world(guild_id)
    return buffer.getvalue() or "(no output)"


async def run_command_async(guild_id: int, line: str) -> str:
    """The version Discord command handlers should actually call: run_command() is a blocking,
    synchronous call (main.py's game logic doesn't know about asyncio), so running it directly
    in a handler would stall the bot's single event loop - and every other guild's commands
    along with it - for as long as it takes. Offloading it to a thread fixes that, but then two
    overlapping commands for the *same* guild really could race on its World, so the per-guild
    lock still has to wrap this, even though nothing else needs one."""
    async with get_lock(guild_id):
        return await asyncio.to_thread(run_command, guild_id, line)
