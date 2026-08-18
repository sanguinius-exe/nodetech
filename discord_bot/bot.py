"""Discord front-end for nodetech - a thin wrapper around main.py's run_command(), same as the
CLI and the web terminal. Anyone with the "Manage Server" permission can change world state
(create the world, move divisions, declare war, advance the year, ...); everyone else gets
read-only status checks. That split isn't a new restriction the game didn't already have - every
read command (view-country, world status, ...) is already visible to any caller with zero access
control - it's just gating who can change state.

Every CLI command gets its own slash command for real Discord UX (argument names, choices for
fixed enums, autocomplete on node/country IDs) - except the four that are file/save-model
concepts with no per-guild equivalent (open, save, list-worlds, rename-world: each guild already
has exactly one world, auto-saved after every command) and help/quit/exit, which don't apply to
slash commands at all. /admin is a raw passthrough covering anything that still falls outside
that - new CLI commands land there automatically until they get a dedicated one of their own.
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
from node import BuildingType, ExtractionSiteType, ResourceType, Terrain

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
TERRAIN_CHOICES = [app_commands.Choice(name=t.name, value=t.name) for t in Terrain]
BUILDING_CHOICES = [app_commands.Choice(name=b.name, value=b.name) for b in BuildingType]
RESOURCE_CHOICES = [app_commands.Choice(name=r.name, value=r.name) for r in ResourceType]
EXTRACTION_SITE_CHOICES = [app_commands.Choice(name=s.name, value=s.name) for s in ExtractionSiteType]

# How many nodes /list shows before truncating - a full unbounded dump (main.py's own `list`
# has no such cap) would mean one Discord message per ~35 nodes, so a world the size of a real
# generated map (tens of thousands of nodes) would flood the channel with hundreds of messages
# in a row. /nodes <country> covers a full per-country listing without this limit.
LIST_NODES_DISPLAY_CAP = 60


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
    "unsetcountry",
    "connect",
    "disconnect",
    "build_railroad",
    "remove_railroad",
    "setterrain",
    "setpopulation",
    "setpopgrowth",
    "seteconomy",
    "build",
    "unbuild",
    "addresource",
    "removeresource",
    "build_extraction",
    "unbuild_extraction",
    "setgovernment",
    "deploy",
    "create_division",
    "create_airforce_division",
    "deploy_airforce",
    "deploy_reserve",
    "move_division",
    "group_attack",
    "declare_war",
    "make_peace",
    "set_equipment",
    "recover",
    "advance_year",
    "set_year",
    "forceupdate",
    "apply_supply",
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


@tree.command(description="Clear a node's controlling country, making it unclaimed (admins only)")
@app_commands.autocomplete(node_id=node_autocomplete)
async def unsetcountry(interaction: discord.Interaction, node_id: str) -> None:
    await run_admin_line(interaction, game_bridge.build_line("unsetcountry", node_id))


@tree.command(description="Connect two nodes (admins only)")
@app_commands.autocomplete(node_id_1=node_autocomplete, node_id_2=node_autocomplete)
async def connect(interaction: discord.Interaction, node_id_1: str, node_id_2: str) -> None:
    await run_admin_line(interaction, game_bridge.build_line("connect", node_id_1, node_id_2))


@tree.command(description="Remove the connection between two nodes (admins only)")
@app_commands.autocomplete(node_id_1=node_autocomplete, node_id_2=node_autocomplete)
async def disconnect(interaction: discord.Interaction, node_id_1: str, node_id_2: str) -> None:
    await run_admin_line(interaction, game_bridge.build_line("disconnect", node_id_1, node_id_2))


@tree.command(description="Build a railroad between two already-connected nodes (admins only)")
@app_commands.autocomplete(node_id_1=node_autocomplete, node_id_2=node_autocomplete)
async def build_railroad(interaction: discord.Interaction, node_id_1: str, node_id_2: str) -> None:
    await run_admin_line(interaction, game_bridge.build_line("build-railroad", node_id_1, node_id_2))


@tree.command(description="Remove the railroad between two nodes (admins only)")
@app_commands.autocomplete(node_id_1=node_autocomplete, node_id_2=node_autocomplete)
async def remove_railroad(interaction: discord.Interaction, node_id_1: str, node_id_2: str) -> None:
    await run_admin_line(interaction, game_bridge.build_line("remove-railroad", node_id_1, node_id_2))


@tree.command(description="Set a node's terrain type (admins only)")
@app_commands.choices(terrain=TERRAIN_CHOICES)
@app_commands.autocomplete(node_id=node_autocomplete)
async def setterrain(interaction: discord.Interaction, node_id: str, terrain: app_commands.Choice[str]) -> None:
    await run_admin_line(interaction, game_bridge.build_line("setterrain", node_id, terrain.value))


@tree.command(description="Set a node's population (admins only)")
@app_commands.autocomplete(node_id=node_autocomplete)
async def setpopulation(interaction: discord.Interaction, node_id: str, population: int) -> None:
    await run_admin_line(interaction, game_bridge.build_line("setpopulation", node_id, str(population)))


@tree.command(description="Set a node's population growth rate (admins only)")
@app_commands.describe(rate="e.g. 0.05 for 5%")
@app_commands.autocomplete(node_id=node_autocomplete)
async def setpopgrowth(interaction: discord.Interaction, node_id: str, rate: float) -> None:
    await run_admin_line(interaction, game_bridge.build_line("setpopgrowth", node_id, str(rate)))


@tree.command(description="Set a node's economic output (admins only)")
@app_commands.autocomplete(node_id=node_autocomplete)
async def seteconomy(interaction: discord.Interaction, node_id: str, output: float) -> None:
    await run_admin_line(interaction, game_bridge.build_line("seteconomy", node_id, str(output)))


@tree.command(description="Enable a building at a node (admins only)")
@app_commands.choices(building=BUILDING_CHOICES)
@app_commands.autocomplete(node_id=node_autocomplete)
async def build(interaction: discord.Interaction, node_id: str, building: app_commands.Choice[str]) -> None:
    await run_admin_line(interaction, game_bridge.build_line("build", node_id, building.value))


@tree.command(description="Disable a building at a node (admins only)")
@app_commands.choices(building=BUILDING_CHOICES)
@app_commands.autocomplete(node_id=node_autocomplete)
async def unbuild(interaction: discord.Interaction, node_id: str, building: app_commands.Choice[str]) -> None:
    await run_admin_line(interaction, game_bridge.build_line("unbuild", node_id, building.value))


@tree.command(description="Add a resource to a node (admins only)")
@app_commands.choices(resource=RESOURCE_CHOICES)
@app_commands.autocomplete(node_id=node_autocomplete)
async def addresource(interaction: discord.Interaction, node_id: str, resource: app_commands.Choice[str]) -> None:
    await run_admin_line(interaction, game_bridge.build_line("addresource", node_id, resource.value))


@tree.command(description="Remove a resource from a node (admins only)")
@app_commands.choices(resource=RESOURCE_CHOICES)
@app_commands.autocomplete(node_id=node_autocomplete)
async def removeresource(interaction: discord.Interaction, node_id: str, resource: app_commands.Choice[str]) -> None:
    await run_admin_line(interaction, game_bridge.build_line("removeresource", node_id, resource.value))


@tree.command(description="Build an extraction site at a node - needs the matching resource (admins only)")
@app_commands.choices(site=EXTRACTION_SITE_CHOICES)
@app_commands.autocomplete(node_id=node_autocomplete)
async def build_extraction(interaction: discord.Interaction, node_id: str, site: app_commands.Choice[str]) -> None:
    await run_admin_line(interaction, game_bridge.build_line("build-extraction", node_id, site.value))


@tree.command(description="Remove an extraction site from a node (admins only)")
@app_commands.choices(site=EXTRACTION_SITE_CHOICES)
@app_commands.autocomplete(node_id=node_autocomplete)
async def unbuild_extraction(interaction: discord.Interaction, node_id: str, site: app_commands.Choice[str]) -> None:
    await run_admin_line(interaction, game_bridge.build_line("unbuild-extraction", node_id, site.value))


@tree.command(description="Set a country's government type (admins only)")
@app_commands.choices(government=GOVERNMENT_CHOICES)
@app_commands.autocomplete(country=country_autocomplete)
async def setgovernment(interaction: discord.Interaction, country: str, government: app_commands.Choice[str]) -> None:
    await run_admin_line(interaction, game_bridge.build_line("setgovernment", country, government.value))


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


@tree.command(description="Create a division in reserve, not deployed to any node (admins only)")
@app_commands.choices(division_type=DIVISION_TYPE_CHOICES)
@app_commands.autocomplete(country=country_autocomplete)
async def create_division(
    interaction: discord.Interaction,
    country: str,
    name: str,
    division_type: app_commands.Choice[str],
    manpower: int,
    supply: float,
) -> None:
    line = game_bridge.build_line("create-division", country, name, division_type.value, str(manpower), str(supply))
    await run_admin_line(interaction, line)


@tree.command(description="Create an AIR_FORCE division in reserve (admins only)")
@app_commands.autocomplete(country=country_autocomplete)
async def create_airforce_division(
    interaction: discord.Interaction,
    country: str,
    name: str,
    manpower: int,
    supply: float,
    aircraft_type: str,
    equipment_rating: float,
    aircraft_count: int,
    aircraft_range: float,
) -> None:
    line = game_bridge.build_line(
        "create-airforce-division",
        country,
        name,
        str(manpower),
        str(supply),
        aircraft_type,
        str(equipment_rating),
        str(aircraft_count),
        str(aircraft_range),
    )
    await run_admin_line(interaction, line)


@tree.command(description="Create and deploy an AIR_FORCE division to a node (admins only)")
@app_commands.autocomplete(node_id=node_autocomplete, country=country_autocomplete)
async def deploy_airforce(
    interaction: discord.Interaction,
    node_id: str,
    country: str,
    name: str,
    manpower: int,
    supply: float,
    aircraft_type: str,
    equipment_rating: float,
    aircraft_count: int,
    aircraft_range: float,
) -> None:
    line = game_bridge.build_line(
        "deploy-airforce",
        node_id,
        country,
        name,
        str(manpower),
        str(supply),
        aircraft_type,
        str(equipment_rating),
        str(aircraft_count),
        str(aircraft_range),
    )
    await run_admin_line(interaction, line)


@tree.command(description="Deploy an existing reserve division to a node (admins only)")
@app_commands.autocomplete(country=country_autocomplete, node_id=node_autocomplete)
async def deploy_reserve(interaction: discord.Interaction, country: str, name: str, node_id: str) -> None:
    await run_admin_line(interaction, game_bridge.build_line("deploy-reserve", country, name, node_id))


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


@tree.command(description="Set the current year to a specific value (admins only)")
async def set_year(interaction: discord.Interaction, year: int) -> None:
    await run_admin_line(interaction, game_bridge.build_line("set-year", str(year)))


@tree.command(description="Recalculate every country's GDP/population from its nodes without advancing the year (admins only)")
async def forceupdate(interaction: discord.Interaction) -> None:
    await run_admin_line(interaction, "forceupdate")


@tree.command(description="Run one supply iteration now (penalize/recover morale and equipment) without advancing the year")
async def apply_supply(interaction: discord.Interaction) -> None:
    await run_admin_line(interaction, "apply-supply")


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


@tree.command(description="A country's full details - government, stability, GDP, population, every node ID, divisions")
@app_commands.autocomplete(country=country_autocomplete)
async def view_country(interaction: discord.Interaction, country: str) -> None:
    await run_open_line(interaction, game_bridge.build_line("view-country", country))


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


@tree.command(name="list", description="List nodes (capped - use /nodes <country> for a complete per-country list)")
async def list_nodes(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    world_obj = game_bridge.get_world(interaction.guild_id)
    if not world_obj.nodes:
        await interaction.followup.send("No nodes yet.")
        return
    all_nodes = list(world_obj.nodes.values())
    lines = [
        f"  {n.id} ({n.x}, {n.y}) - {n.country or 'unclaimed'} ({n.terrain.name})"
        for n in all_nodes[:LIST_NODES_DISPLAY_CAP]
    ]
    if len(all_nodes) > LIST_NODES_DISPLAY_CAP:
        lines.append(
            f"\n...and {len(all_nodes) - LIST_NODES_DISPLAY_CAP} more ({len(all_nodes)} total). "
            "Use /nodes <country> for a complete, uncapped list."
        )
    await send_chunks(interaction, "\n".join(lines))


@tree.command(description="List every country, with its government type and node count")
async def list_countries(interaction: discord.Interaction) -> None:
    await run_open_line(interaction, "list-countries")


@tree.command(description="List every country and its GDP/population/nodes")
async def world(interaction: discord.Interaction) -> None:
    await run_open_line(interaction, "world status")


@tree.command(description="List every country's economic and projected population growth rate")
async def projections(interaction: discord.Interaction) -> None:
    await run_open_line(interaction, "projections")


@tree.command(description="Show the current year and years elapsed since the world started")
async def year(interaction: discord.Interaction) -> None:
    await run_open_line(interaction, "year")


@tree.command(description="List every war currently in progress")
async def wars(interaction: discord.Interaction) -> None:
    await run_open_line(interaction, "wars")


# --- Everyone: reference lists ------------------------------------------------------------------


@tree.command(description="List available building types")
async def buildings(interaction: discord.Interaction) -> None:
    await run_open_line(interaction, "buildings")


@tree.command(description="List available resource types")
async def resources(interaction: discord.Interaction) -> None:
    await run_open_line(interaction, "resources")


@tree.command(description="List available extraction site types")
async def extraction_sites(interaction: discord.Interaction) -> None:
    await run_open_line(interaction, "extraction-sites")


@tree.command(description="List available terrain types")
async def terrains(interaction: discord.Interaction) -> None:
    await run_open_line(interaction, "terrains")


@tree.command(description="List available division types")
async def division_types(interaction: discord.Interaction) -> None:
    await run_open_line(interaction, "division-types")


@tree.command(description="List available government types")
async def governments(interaction: discord.Interaction) -> None:
    await run_open_line(interaction, "governments")


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


HEATMAP_METRIC_CHOICES = [
    app_commands.Choice(name="Population", value="population"),
    app_commands.Choice(name="GDP", value="gdp"),
]


@tree.command(description="Render a heatmap of one country's population or GDP, tile by tile")
@app_commands.describe(country="Which country to show", metric="Population (default) or GDP")
@app_commands.autocomplete(country=country_autocomplete)
@app_commands.choices(metric=HEATMAP_METRIC_CHOICES)
async def heatmap(
    interaction: discord.Interaction, country: str, metric: Optional[app_commands.Choice[str]] = None
) -> None:
    await interaction.response.defer()
    world_obj = game_bridge.get_world(interaction.guild_id)
    if not world_obj.nodes:
        await interaction.followup.send("No nodes yet.")
        return
    title = interaction.guild.name if interaction.guild else None
    metric_value = metric.value if metric else "population"
    # Same reasoning as /map: offload the render and hold the guild's lock so it can't race a
    # command mutating the same World mid-render.
    async with game_bridge.get_lock(interaction.guild_id):
        try:
            buffer = await asyncio.to_thread(map_render.render_heatmap, world_obj, country, metric_value, title)
        except ValueError as e:
            await interaction.followup.send(str(e))
            return
    await interaction.followup.send(file=discord.File(buffer, filename="heatmap.png"))


@tree.command(description="Download this server's world as a save file")
async def export(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    async with game_bridge.get_lock(interaction.guild_id):
        content = await asyncio.to_thread(game_bridge.export_world, interaction.guild_id)
    file = discord.File(io.BytesIO(content), filename=f"nodetech_{interaction.guild_id}.json")
    await interaction.followup.send("This server's world:", file=file)


# main.py's own `export-country`/`export-world` write a markdown report to disk and are named
# for the CLI's save/load model - these two are the Discord equivalents, but pulled from
# game_bridge.export_country_report()/export_world_report() (which build the report text
# directly) rather than running those commands verbatim, so two guilds exporting at the same
# time can never collide on the shared ~/proppunk game files/ path or main.py's single
# process-global "last export" pointer the CLI/web terminal rely on instead.


@tree.command(description="Download a country's full report (GDP, population, nodes, divisions) as a markdown file")
@app_commands.autocomplete(country=country_autocomplete)
async def export_country(interaction: discord.Interaction, country: str) -> None:
    await interaction.response.defer()
    async with game_bridge.get_lock(interaction.guild_id):
        try:
            content = await asyncio.to_thread(game_bridge.export_country_report, interaction.guild_id, country)
        except ValueError as e:
            await interaction.followup.send(str(e))
            return
    file = discord.File(io.BytesIO(content.encode()), filename=f"{country}_report.md")
    await interaction.followup.send(file=file)


@tree.command(description="Download a report of every country's GDP/population/nodes as a markdown file")
async def world_report(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    async with game_bridge.get_lock(interaction.guild_id):
        content = await asyncio.to_thread(game_bridge.export_world_report, interaction.guild_id)
    file = discord.File(io.BytesIO(content.encode()), filename=f"nodetech_{interaction.guild_id}_report.md")
    await interaction.followup.send(file=file)


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
