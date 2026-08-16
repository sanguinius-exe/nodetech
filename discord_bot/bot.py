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


async def run_admin_line(interaction: discord.Interaction, line: str) -> None:
    """Shared body for every state-changing command: gate on Manage Server, defer (game commands
    run in a thread and can take a moment - see game_bridge.run_command_async), run, reply."""
    if not is_admin(interaction):
        await interaction.response.send_message("Only server admins (Manage Server) can run this.", ephemeral=True)
        return
    await interaction.response.defer()
    output = await game_bridge.run_command_async(interaction.guild_id, line)
    await send_chunks(interaction, output)


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
    if not is_admin(interaction):
        await interaction.response.send_message("Only server admins (Manage Server) can run this.", ephemeral=True)
        return
    await interaction.response.defer()
    content = await file.read()
    try:
        message = await game_bridge.import_world_async(interaction.guild_id, content)
    except ValueError as e:
        await interaction.followup.send(f"Import failed: {e}")
        return
    await interaction.followup.send(message)


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
async def setcountry(interaction: discord.Interaction, node_id: str, country: str) -> None:
    await run_admin_line(interaction, game_bridge.build_line("setcountry", node_id, country))


# --- Admin: military ---------------------------------------------------------------------------


@tree.command(description="Create and deploy a division to a node (admins only)")
@app_commands.choices(division_type=DIVISION_TYPE_CHOICES)
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
async def move_division(interaction: discord.Interaction, country: str, name: str, destination_id: str) -> None:
    line = game_bridge.build_line("move-division", country, name, destination_id)
    await run_admin_line(interaction, line)


@tree.command(description="Attack with every division a country has at one node (admins only)")
async def group_attack(interaction: discord.Interaction, country: str, origin_id: str, destination_id: str) -> None:
    line = game_bridge.build_line("group-attack", country, origin_id, destination_id)
    await run_admin_line(interaction, line)


@tree.command(description="Put two countries at war (admins only)")
async def declare_war(interaction: discord.Interaction, country_a: str, country_b: str) -> None:
    await run_admin_line(interaction, game_bridge.build_line("declare-war", country_a, country_b))


@tree.command(description="End a war between two countries (admins only)")
async def make_peace(interaction: discord.Interaction, country_a: str, country_b: str) -> None:
    await run_admin_line(interaction, game_bridge.build_line("make-peace", country_a, country_b))


@tree.command(description="Set a division's equipment cap (admins only)")
async def set_equipment(interaction: discord.Interaction, country: str, name: str, rating: float) -> None:
    line = game_bridge.build_line("set-equipment", country, name, str(rating))
    await run_admin_line(interaction, line)


@tree.command(description="Restore a division to full manpower, morale, and equipment (admins only)")
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
async def assign(interaction: discord.Interaction, member: discord.Member, country: str) -> None:
    if not is_admin(interaction):
        await interaction.response.send_message("Only server admins (Manage Server) can run this.", ephemeral=True)
        return
    game_bridge.assign_country(interaction.guild_id, member.id, country)
    await interaction.response.send_message(f"{member.mention} now controls **{country}**.")


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
async def status(interaction: discord.Interaction, country: Optional[str] = None) -> None:
    name = await resolve_country(interaction, country)
    if name is None:
        return
    await run_open_line(interaction, game_bridge.build_line("country-status", name))


@tree.command(description="View a node's full details")
async def view(interaction: discord.Interaction, node_id: str) -> None:
    await run_open_line(interaction, game_bridge.build_line("view", node_id))


@tree.command(description="List a country's divisions, deployed and in reserve")
@app_commands.describe(country="Country name (defaults to your assigned country)")
async def divisions(interaction: discord.Interaction, country: Optional[str] = None) -> None:
    name = await resolve_country(interaction, country)
    if name is None:
        return
    await run_open_line(interaction, game_bridge.build_line("country-divisions", name))


@tree.command(description="List a country's nodes, with position, terrain, population, and output")
@app_commands.describe(country="Country name (defaults to your assigned country)")
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


@tree.command(name="map", description="Render the world map as an image")
async def map_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    world_obj = game_bridge.get_world(interaction.guild_id)
    if not world_obj.nodes:
        await interaction.followup.send("No nodes yet.")
        return
    # Rendering is synchronous CPU work, same reasoning as run_command_async: offload it so it
    # can't stall the bot's event loop, and hold the guild's lock so it can't race a command
    # that's mutating the same World mid-render.
    async with game_bridge.get_lock(interaction.guild_id):
        buffer = await asyncio.to_thread(map_render.render_map, world_obj)
    await interaction.followup.send(file=discord.File(buffer, filename="map.png"))


@client.event
async def on_ready() -> None:
    await tree.sync()
    print(f"Logged in as {client.user}")


client.run(TOKEN)
