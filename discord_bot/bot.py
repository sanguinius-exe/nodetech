"""Discord front-end for nodetech - a thin wrapper around main.py's run_command(), same as the
CLI and the web terminal. Anyone with the "Manage Server" permission can change world state
(create the world, move divisions, declare war, advance the year, ...); everyone else gets
read-only status checks. That split isn't a new restriction the game didn't already have - every
read command (view-country, world status, ...) is already visible to any caller with zero access
control - it's just gating who can change state.

Most actions have their own slash command for real Discord UX (argument names, choices for fixed
enums); /admin stays as a raw passthrough for the rest of the CLI's command set that doesn't have
a dedicated one yet.
"""

from __future__ import annotations

import asyncio
import io
import math
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

import game_bridge
import map_render
from country import GovernmentType
from division import DivisionType

load_dotenv()

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("Set DISCORD_TOKEN (see .env.example) before running the bot.")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

START_TIME = datetime.now(timezone.utc)


def _get_git_commit() -> str:
    """The short commit hash this running instance was deployed from - handy for confirming a
    deploy actually took (pull + restart) rather than guessing from when the process started."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


GIT_COMMIT = _get_git_commit()


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    days, total = divmod(total, 86400)
    hours, total = divmod(total, 3600)
    minutes, secs = divmod(total, 60)
    parts = [f"{days}d"] if days else []
    parts += [f"{hours}h"] if hours or days else []
    parts += [f"{minutes}m"] if minutes or hours or days else []
    parts.append(f"{secs}s")
    return " ".join(parts)

GOVERNMENT_CHOICES = [app_commands.Choice(name=g.name, value=g.name) for g in GovernmentType]
# deploy/create-division reject AIR_FORCE (it needs aircraft-specific args deploy-airforce takes
# instead) - main.py enforces this itself, but excluding it here means the dropdown never offers
# a choice that's guaranteed to fail.
DIVISION_TYPE_CHOICES = [app_commands.Choice(name=d.name, value=d.name) for d in DivisionType if d.name != "AIR_FORCE"]


def is_admin(interaction: discord.Interaction) -> bool:
    return isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild


# Every command gated by has_permission() below - the ones /permit/-able. /permit, /revoke, and
# /permissions themselves are deliberately NOT in here and stay hard-gated to is_admin() alone
# (see their definitions), so a role granted access to one admin command can never use that
# access to grant itself - or anyone else - more.
ADMIN_COMMAND_NAMES = {
    "newworld",
    "import",
    "create_country",
    "create_node",
    "setcountry",
    "deploy",
    "move_division",
    "group_attack",
    "declare_war",
    "make_peace",
    "set_equipment",
    "recover",
    "advance_year",
    "admin",
    "assign",
}


def has_permission(interaction: discord.Interaction) -> bool:
    """Manage Server always passes. Otherwise, checks whether any role the caller has was
    granted this specific command via /permit - additive on top of Manage Server, never a
    replacement for it, so a misconfigured grant (or none at all) can never lock server admins
    out of their own bot."""
    if is_admin(interaction):
        return True
    if not isinstance(interaction.user, discord.Member) or interaction.command is None:
        return False
    role_ids = [role.id for role in interaction.user.roles]
    return game_bridge.role_has_command_permission(interaction.guild_id, interaction.command.qualified_name, role_ids)


async def country_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    world = game_bridge.get_world(interaction.guild_id)
    matches = [name for name in world.countries if current.lower() in name.lower()]
    return [app_commands.Choice(name=name, value=name) for name in matches[:25]]


async def node_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    world = game_bridge.get_world(interaction.guild_id)
    matches = [node_id for node_id in world.nodes if current.lower() in node_id.lower()]
    return [app_commands.Choice(name=node_id, value=node_id) for node_id in matches[:25]]


def chunk_for_discord(text: str) -> list[str]:
    """Discord caps messages at 2000 characters; split long output into a handful of code
    blocks instead of truncating it."""
    lines = text.splitlines() or [text]
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = current + line + "\n"
        if len(candidate) > 1900:
            chunks.append(current)
            current = line + "\n"
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [f"```\n{c}\n```" for c in chunks]


async def send_chunks(interaction: discord.Interaction, text: str) -> None:
    chunks = chunk_for_discord(text)
    await interaction.followup.send(chunks[0])
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk)


async def send_switch_backup(interaction: discord.Interaction) -> None:
    """If the command that just ran replaced the world (new-world, whether via /newworld or the
    raw /admin passthrough), game_bridge queued up the outgoing world's save bytes - post them
    as an automatic backup download so switching worlds never silently loses the old one, even
    though a local copy also lands at data/<guild_id>_previous.json."""
    snapshot = game_bridge.get_pending_snapshot(interaction.guild_id)
    if snapshot is None:
        return
    file = discord.File(io.BytesIO(snapshot), filename=f"nodetech_{interaction.guild_id}_previous.json")
    await interaction.followup.send("Backup of the world that was just replaced:", file=file)


async def run_admin_line(interaction: discord.Interaction, line: str) -> None:
    """Shared body for every state-changing command: gate on Manage Server (or a /permit-ed
    role), defer (game commands run in a thread and can take a moment - see
    game_bridge.run_command_async), run, reply."""
    if not has_permission(interaction):
        await interaction.response.send_message(
            "You don't have permission to run this - it needs Manage Server, or a role an admin "
            "has granted with /permit.",
            ephemeral=True,
        )
        return
    await interaction.response.defer()
    output = await game_bridge.run_command_async(interaction.guild_id, line)
    await send_chunks(interaction, output)
    await send_switch_backup(interaction)


async def run_open_line(interaction: discord.Interaction, line: str) -> None:
    """Shared body for every read-only command - same as run_admin_line minus the permission
    gate, since these are already visible to anyone in the CLI/web terminal too."""
    await interaction.response.defer()
    output = await game_bridge.run_command_async(interaction.guild_id, line)
    await send_chunks(interaction, output)


# --- Admin: world setup -----------------------------------------------------------------------


@tree.command(description="Reset this server's world to a fresh grid (admins only)")
@app_commands.describe(name="A label for the world (not used for file naming - each server already has its own)")
async def newworld(interaction: discord.Interaction, name: str, width: int, height: int, start_year: int = 0) -> None:
    line = game_bridge.build_line("new-world", name, str(width), str(height), str(start_year))
    await run_admin_line(interaction, line)


@tree.command(name="import", description="Load a save file as this server's world, replacing what's there (admins only)")
@app_commands.describe(file="A nodetech save file (.json) - from the CLI, web terminal, or another server's export")
async def import_command(interaction: discord.Interaction, file: discord.Attachment) -> None:
    if not has_permission(interaction):
        await interaction.response.send_message(
            "You don't have permission to run this - it needs Manage Server, or a role an admin "
            "has granted with /permit.",
            ephemeral=True,
        )
        return
    await interaction.response.defer()
    content = await file.read()
    try:
        message, previous = await game_bridge.import_world_async(interaction.guild_id, content)
    except ValueError as e:
        await interaction.followup.send(f"Import failed: {e}")
        return
    await interaction.followup.send(message)
    if previous is not None:
        backup_file = discord.File(io.BytesIO(previous), filename=f"nodetech_{interaction.guild_id}_previous.json")
        await interaction.followup.send("Backup of the world that was just replaced:", file=backup_file)


@tree.command(description="Create a new country (admins only)")
@app_commands.choices(government=GOVERNMENT_CHOICES)
async def create_country(
    interaction: discord.Interaction, name: str, government: Optional[app_commands.Choice[str]] = None
) -> None:
    args = [name] if government is None else [name, government.value]
    await run_admin_line(interaction, game_bridge.build_line("create-country", *args))


@tree.command(description="Create a new node/tile at a grid position (admins only)")
async def create_node(interaction: discord.Interaction, node_id: str, x: int, y: int) -> None:
    await run_admin_line(interaction, game_bridge.build_line("create", node_id, str(x), str(y)))


@tree.command(description="Set a node's controlling country (admins only)")
@app_commands.autocomplete(node_id=node_autocomplete, country=country_autocomplete)
async def setcountry(interaction: discord.Interaction, node_id: str, country: str) -> None:
    await run_admin_line(interaction, game_bridge.build_line("setcountry", node_id, country))


# --- Admin: military ---------------------------------------------------------------------------


@tree.command(description="Create and deploy a division to a node (admins only)")
@app_commands.choices(division_type=DIVISION_TYPE_CHOICES)
@app_commands.autocomplete(node_id=node_autocomplete, country=country_autocomplete)
async def deploy(
    interaction: discord.Interaction,
    node_id: str,
    country: str,
    name: str,
    division_type: app_commands.Choice[str],
    manpower: int,
    supply: float,
) -> None:
    line = game_bridge.build_line(
        "deploy", node_id, country, name, division_type.value, str(manpower), str(supply)
    )
    await run_admin_line(interaction, line)


@tree.command(description="Move a division to another node - attacks if it's enemy territory (admins only)")
@app_commands.autocomplete(country=country_autocomplete, destination_id=node_autocomplete)
async def move_division(interaction: discord.Interaction, country: str, name: str, destination_id: str) -> None:
    line = game_bridge.build_line("move-division", country, name, destination_id)
    await run_admin_line(interaction, line)


@tree.command(description="Attack with every division a country has at one node (admins only)")
@app_commands.autocomplete(country=country_autocomplete, origin_id=node_autocomplete, destination_id=node_autocomplete)
async def group_attack(interaction: discord.Interaction, country: str, origin_id: str, destination_id: str) -> None:
    line = game_bridge.build_line("group-attack", country, origin_id, destination_id)
    await run_admin_line(interaction, line)


@tree.command(description="Put two countries at war (admins only)")
@app_commands.autocomplete(country_a=country_autocomplete, country_b=country_autocomplete)
async def declare_war(interaction: discord.Interaction, country_a: str, country_b: str) -> None:
    await run_admin_line(interaction, game_bridge.build_line("declare-war", country_a, country_b))


@tree.command(description="End a war between two countries (admins only)")
@app_commands.autocomplete(country_a=country_autocomplete, country_b=country_autocomplete)
async def make_peace(interaction: discord.Interaction, country_a: str, country_b: str) -> None:
    await run_admin_line(interaction, game_bridge.build_line("make-peace", country_a, country_b))


@tree.command(description="Set a division's equipment cap (admins only)")
@app_commands.autocomplete(country=country_autocomplete)
async def set_equipment(interaction: discord.Interaction, country: str, name: str, rating: float) -> None:
    line = game_bridge.build_line("set-equipment", country, name, str(rating))
    await run_admin_line(interaction, line)


@tree.command(description="Restore a division to full manpower, morale, and equipment (admins only)")
@app_commands.autocomplete(country=country_autocomplete)
async def recover(interaction: discord.Interaction, country: str, name: str) -> None:
    await run_admin_line(interaction, game_bridge.build_line("recover", country, name))


@tree.command(description="Advance the world by one year (admins only)")
async def advance_year(interaction: discord.Interaction) -> None:
    await run_admin_line(interaction, "advance-year")


@tree.command(description="Run a raw game command for anything without its own slash command (admins only)")
@app_commands.describe(command="The full command line, exactly as you'd type it in the CLI")
async def admin(interaction: discord.Interaction, command: str) -> None:
    await run_admin_line(interaction, command)


@tree.command(description="Assign a Discord member to a country (admins only)")
@app_commands.describe(member="The player", country="The country they'll control")
@app_commands.autocomplete(country=country_autocomplete)
async def assign(interaction: discord.Interaction, member: discord.Member, country: str) -> None:
    if not has_permission(interaction):
        await interaction.response.send_message(
            "You don't have permission to run this - it needs Manage Server, or a role an admin "
            "has granted with /permit.",
            ephemeral=True,
        )
        return
    game_bridge.assign_country(interaction.guild_id, member.id, country)
    await interaction.response.send_message(f"{member.mention} now controls **{country}**.")


async def admin_command_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    matches = sorted(n for n in ADMIN_COMMAND_NAMES if current.lower() in n.lower())
    return [app_commands.Choice(name=n, value=n) for n in matches[:25]]


# permit/revoke/permissions are deliberately gated on is_admin() directly, never has_permission()
# - letting a /permit-ed role manage permissions itself would let it grant itself (or anyone)
# more access than it was actually given, which defeats the whole point of scoping it in the
# first place. Only real Manage Server holders can touch this.


@tree.command(description="Let a role use an admin command, in addition to Manage Server (admins only)")
@app_commands.describe(command="Which admin command to grant", role="The role to grant it to")
@app_commands.autocomplete(command=admin_command_autocomplete)
async def permit(interaction: discord.Interaction, command: str, role: discord.Role) -> None:
    if not is_admin(interaction):
        await interaction.response.send_message("Only server admins (Manage Server) can run this.", ephemeral=True)
        return
    if command not in ADMIN_COMMAND_NAMES:
        await interaction.response.send_message(
            f"'{command}' isn't a permit-able admin command. Use the autocomplete list.", ephemeral=True
        )
        return
    game_bridge.permit_role(interaction.guild_id, command, role.id)
    await interaction.response.send_message(f"{role.mention} can now use `/{command}` (in addition to Manage Server).")


@tree.command(description="Remove a role's granted permission for a command (admins only)")
@app_commands.describe(command="Which admin command to revoke", role="The role to revoke it from")
@app_commands.autocomplete(command=admin_command_autocomplete)
async def revoke(interaction: discord.Interaction, command: str, role: discord.Role) -> None:
    if not is_admin(interaction):
        await interaction.response.send_message("Only server admins (Manage Server) can run this.", ephemeral=True)
        return
    removed = game_bridge.revoke_role(interaction.guild_id, command, role.id)
    if not removed:
        await interaction.response.send_message(
            f"{role.mention} didn't have an explicit grant for `/{command}` (Manage Server holders can always use it).",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(f"Removed {role.mention}'s permission for `/{command}`.")


@tree.command(description="List every command-specific role permission granted in this server")
async def permissions(interaction: discord.Interaction) -> None:
    perms = game_bridge.get_command_permissions(interaction.guild_id)
    if not perms:
        await interaction.response.send_message(
            "No command-specific role grants yet - only Manage Server can run admin commands."
        )
        return
    lines = [f"`/{command}`: {', '.join(f'<@&{role_id}>' for role_id in role_ids)}" for command, role_ids in perms.items()]
    await interaction.response.send_message("\n".join(lines))


# --- Everyone: read-only -----------------------------------------------------------------------


async def resolve_country(interaction: discord.Interaction, country: Optional[str]) -> Optional[str]:
    """The country name a read command should use: whatever was passed explicitly, or the
    caller's /assign-ed one. Sends the "you don't have one assigned" reply itself and returns
    None if neither is available, so callers can just bail out on a None return."""
    name = country or game_bridge.get_assigned_country(interaction.guild_id, interaction.user.id)
    if not name:
        await interaction.response.send_message(
            "You don't have a country assigned yet - ask an admin to run /assign, or pass a country name.",
            ephemeral=True,
        )
        return None
    return name


@tree.command(description="Check a country's status - yours by default, or name one")
@app_commands.describe(country="Country name (defaults to your assigned country)")
@app_commands.autocomplete(country=country_autocomplete)
async def status(interaction: discord.Interaction, country: Optional[str] = None) -> None:
    name = await resolve_country(interaction, country)
    if name is None:
        return
    await run_open_line(interaction, game_bridge.build_line("country-status", name))


@tree.command(description="View a node's full details")
@app_commands.autocomplete(node_id=node_autocomplete)
async def view(interaction: discord.Interaction, node_id: str) -> None:
    await run_open_line(interaction, game_bridge.build_line("view", node_id))


@tree.command(description="List a country's divisions, deployed and in reserve")
@app_commands.describe(country="Country name (defaults to your assigned country)")
@app_commands.autocomplete(country=country_autocomplete)
async def divisions(interaction: discord.Interaction, country: Optional[str] = None) -> None:
    name = await resolve_country(interaction, country)
    if name is None:
        return
    await run_open_line(interaction, game_bridge.build_line("country-divisions", name))


@tree.command(description="List a country's nodes, with position, terrain, population, and output")
@app_commands.describe(country="Country name (defaults to your assigned country)")
@app_commands.autocomplete(country=country_autocomplete)
async def nodes(interaction: discord.Interaction, country: Optional[str] = None) -> None:
    name = await resolve_country(interaction, country)
    if name is None:
        return
    await run_open_line(interaction, game_bridge.build_line("country-nodes", name))


@tree.command(description="List every country and its GDP/population/nodes")
async def world(interaction: discord.Interaction) -> None:
    await run_open_line(interaction, "world status")


@tree.command(description="List every war currently in progress")
async def wars(interaction: discord.Interaction) -> None:
    await run_open_line(interaction, "wars")


@tree.command(name="botstatus", description="Report the bot's own status - uptime, latency, servers, deployed commit")
async def bot_status(interaction: discord.Interaction) -> None:
    uptime = datetime.now(timezone.utc) - START_TIME
    latency_ms = None if math.isnan(client.latency) else round(client.latency * 1000)

    embed = discord.Embed(title="Bot status", color=discord.Color.blurple())
    embed.add_field(name="Uptime", value=_format_duration(uptime.total_seconds()), inline=True)
    embed.add_field(name="Latency", value=f"{latency_ms} ms" if latency_ms is not None else "n/a", inline=True)
    embed.add_field(name="Servers", value=str(len(client.guilds)), inline=True)
    embed.add_field(name="Started", value=f"<t:{int(START_TIME.timestamp())}:f>", inline=False)
    embed.set_footer(text=f"nodetech @ {GIT_COMMIT} - discord.py {discord.__version__}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="map", description="Render the world map as an image, optionally zoomed to one country")
@app_commands.describe(country="Zoom to just this country's territory (plus a bit of surrounding context)")
@app_commands.autocomplete(country=country_autocomplete)
async def map_command(interaction: discord.Interaction, country: Optional[str] = None) -> None:
    await interaction.response.defer()
    world_obj = game_bridge.get_world(interaction.guild_id)
    if not world_obj.nodes:
        await interaction.followup.send("No nodes yet.")
        return
    title = interaction.guild.name if interaction.guild else None
    # Rendering is synchronous CPU work, same reasoning as run_command_async: offload it so it
    # can't stall the bot's event loop, and hold the guild's lock so it can't race a command
    # that's mutating the same World mid-render.
    async with game_bridge.get_lock(interaction.guild_id):
        try:
            buffer = await asyncio.to_thread(map_render.render_map, world_obj, title, country)
        except ValueError as e:
            await interaction.followup.send(str(e))
            return
    await interaction.followup.send(file=discord.File(buffer, filename="map.png"))


@tree.command(description="Download this server's world as a save file")
async def export(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    async with game_bridge.get_lock(interaction.guild_id):
        content = await asyncio.to_thread(game_bridge.export_world, interaction.guild_id)
    file = discord.File(io.BytesIO(content), filename=f"nodetech_{interaction.guild_id}.json")
    await interaction.followup.send("This server's world:", file=file)


async def _sync_to_guild(guild: discord.Guild) -> None:
    # Global command syncs can take up to an hour to reach clients; copying the global tree into
    # a guild-specific override and syncing *that* instead shows up there almost immediately -
    # much better for actually using (and developing) the bot day to day.
    tree.copy_global_to(guild=guild)
    await tree.sync(guild=guild)


@client.event
async def on_ready() -> None:
    for guild in client.guilds:
        await _sync_to_guild(guild)
    print(f"Logged in as {client.user} ({len(client.guilds)} server(s), commands synced)")


@client.event
async def on_guild_join(guild: discord.Guild) -> None:
    await _sync_to_guild(guild)


client.run(TOKEN)
