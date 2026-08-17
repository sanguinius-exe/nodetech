# nodetech Discord bot

A thin Discord front-end for the game - it imports `main.py`/`node.py`/`country.py`/`division.py`/
`database.py` from the repo root directly and calls the exact same `run_command()` the CLI and
web terminal use. One `World` per Discord server (guild), auto-saved to `discord_bot/data/` after
every command that changes it.

## Commands

Anyone with the **Manage Server** permission can run the admin commands below; everyone else gets
the read-only ones. That split isn't a new restriction the game didn't already have - every read
command here is already visible to any CLI/web-terminal user with zero access control, so players
can freely check any country, not just their own.

Manage Server holders can additionally grant specific roles access to specific admin commands
with `/permit` (e.g. let a "Moderator" role run `/advance_year` without needing full server
admin) - see the permissions section below.

Country and node name fields autocomplete against the server's actual world state as you type -
no more typos into a plain text field for something that already exists.

**Admin - world setup**
- `/newworld <name> <width> <height> [start_year]` - reset this server's world to a fresh grid
- `/import <file>` - load an uploaded save file as this server's world, replacing what's there.
  Accepts the same JSON format the CLI's `save` command and the web terminal's "Download current
  save" button produce (not `export-country`/`export-world`'s markdown reports - those aren't
  reloadable). Resets any `/assign`ed players too, since they name countries that may not exist
  in the new world.
- `/create_country <name> [government]`
- `/create_node <id> <x> <y>`
- `/setcountry <node_id> <country>`

Switching worlds (via `/newworld`, `/import`, or `/admin new-world ...`) is never silently
destructive: whatever world was just replaced gets posted back automatically as a downloadable
`.json`, and a copy also lands at `data/<guild_id>_previous.json` locally - either one can be
fed straight back in through `/import` to undo the switch. Nothing gets backed up on a guild's
very first `/newworld`/`/import`, since there's no prior world yet to lose.

**Admin - military**
- `/deploy <node_id> <country> <name> <division_type> <manpower> <supply>`
- `/move_division <country> <name> <destination_id>` - attacks instead of relocating if the
  destination is enemy territory
- `/group_attack <country> <origin_id> <destination_id>` - attacks with every division that
  country has at one node, as a single force
- `/declare_war <country_a> <country_b>` / `/make_peace <country_a> <country_b>`
- `/set_equipment <country> <name> <rating>` / `/recover <country> <name>`
- `/advance_year`
- `/assign <member> <country>` - bind a Discord member to a country, so `/status`/`/divisions`
  default to it for them
- `/admin <command>` - raw passthrough for anything above that doesn't have its own slash
  command yet (`setterrain`, `build`, `create-division`, `deploy-reserve`, `create-airforce-division`,
  `deploy-airforce`, ...) - the full CLI command set works here, exactly as documented in the
  repo root's README.md

**Admin - permissions** (these three always require actual Manage Server, never a `/permit`-ed
role - otherwise a granted role could grant itself more access than it was actually given)
- `/permit <command> <role>` - let a role use one specific admin command, in addition to (never
  instead of) Manage Server
- `/revoke <command> <role>` - remove a role's grant for a command
- `/permissions` - list every command-specific role grant currently set in this server

**Everyone - read-only**
- `/status [country]` - GDP, population, government, treasury, stability, reserve count
- `/divisions [country]` - every division, deployed and in reserve, with manpower/morale/equipment
- `/nodes [country]` - every node a country owns, with position, terrain, population, and output
- `/view <node_id>` - a node's full details
- `/world` - every country's GDP/population/nodes in one table
- `/wars` - every war currently in progress
- `/map [country]` - the world grid as a PNG, styled to match the CLI/web terminal's map (dark
  background, gridlines between tiles, a "Year N" badge, a wrapped legend) - one tile per node
  colored by owning country, same palette as `assign_country_colors()`. Pass `country` to zoom
  to just that country's own territory plus a couple tiles of surrounding context, instead of
  the whole grid.
- `/botstatus` - the bot process itself: uptime, gateway latency, server count, and the git
  commit it was deployed from (handy for confirming a `git pull` + restart actually took)
- `/export` - download this server's world as a JSON save file - the counterpart to `/import`,
  for backups or moving a world to a different server

## Setup

1. **Create the bot**: [Discord Developer Portal](https://discord.com/developers/applications) →
   New Application → Bot → Reset Token, copy it. No privileged intents are needed.
2. **Invite it**: OAuth2 → URL Generator → scopes `bot` and `applications.commands` → permissions
   `Send Messages`, `Use Slash Commands`, `Attach Files` → open the generated URL and add it to
   your server.
3. **Configure**: `cp .env.example .env` and paste the token into `.env`. Never commit this file
   (it's already gitignored) or paste the token anywhere else.
4. **Install and run**:
   ```
   pip install -r requirements.txt
   python bot.py
   ```

New commands show up almost immediately - the bot syncs its command tree to every server it's
in individually (see "How it fits together" below) rather than relying on Discord's global sync,
which can otherwise take up to an hour to reach clients.

## How it fits together

- `game_bridge.py` owns one `World` per guild in memory, loads it from `data/<guild_id>.json` on
  first use, and auto-saves after every command. `run_command_async()` is what commands should
  actually call: the game logic is synchronous/blocking, so it runs in a thread (via
  `asyncio.to_thread`) rather than stalling the bot's single event loop - and every other guild's
  commands along with it - while a slow command runs. A per-guild `asyncio.Lock` (not one global
  lock) still guards against two overlapping commands racing on the *same* guild's `World`.
- `save`/`open`/`list-worlds`/`rename-world` are disabled - they name worlds by a string in the
  shared `~/proppunk game files/` directory, which doesn't have a coherent per-guild meaning once
  multiple servers exist (two guilds naming a world the same thing would collide, and `open`
  could read another guild's save). `new-world` is instead handled entirely in-memory, per guild.
- `map_render.py` renders the grid with Pillow, reusing `main.py`'s own
  `assign_country_colors()`/`MAP_TILE_COLORS`/`MAP_UNCLAIMED_COLOR`/`MAP_EMPTY_COLOR` rather than
  a second color palette that could drift out of sync with the CLI/web terminal's map, and
  mirrors `MAP_CSS_TEMPLATE`'s look (background, tile gaps, year badge, legend) using Pillow's
  bundled scalable font (`ImageFont.load_default(size=...)`) rather than its old tiny bitmap one.
  `_country_bounds()` computes the crop for `/map`'s optional `country` filter - that country's
  own nodes' bounding box, padded and clamped to the grid - and the legend only lists countries
  actually visible in whatever region ends up rendered, not every country in the world. The
  canvas widens past the map's own width when needed so a long title/small crop can't overlap
  the year badge.
- `on_ready`/`on_guild_join` copy the global command tree into a guild-specific override and sync
  *that* (`tree.copy_global_to(guild=...)` + `tree.sync(guild=...)`) instead of a plain global
  `tree.sync()`, so command updates show up in seconds rather than waiting on Discord's global
  propagation.
- `has_permission()` in bot.py is what every admin command actually checks (Manage Server, or a
  role granted via `/permit`, persisted per guild in `game_bridge.py`'s `_permissions` /
  `data/<guild_id>_permissions.json`) - `/permit`/`/revoke`/`/permissions` themselves call
  `is_admin()` directly instead, so a granted role can never touch the grants themselves.
- `_snapshot_before_switch()` in `game_bridge.py` is what `new-world`/`import` call right before
  actually replacing a guild's world - it reads whatever's currently at `data/<guild_id>.json`
  (nothing, on a guild's first switch), copies it to `data/<guild_id>_previous.json`, and hands
  the bytes back so `/newworld`/`/import`/`/admin new-world ...` can also post them as a Discord
  file. `_reset_world()` queues its copy into `_pending_snapshots` for `run_admin_line` to pick
  up afterward (since it's funneled through the generic string-only `run_command()`); `import_world()`
  returns its copy directly, since `/import` already has its own dedicated call path.

## Not yet built

- Per-*country* access control - `/permit` controls which commands a role can run, not which
  countries they can run them on, so any admin (or permitted role) can still act as any country
  (this mirrors how the CLI/web terminal have always worked: the country name is just an
  argument, trusted as given).
- An explicit confirmation prompt before `/newworld` or `/import` replace the current world -
  both still act instantly with no "are you sure?" step, though the switch is no longer
  destructive since the outgoing world is backed up automatically first.
