# nodetech
node technology for proppunk [proof of concept]

See [ARCHITECTURE.md](ARCHITECTURE.md) for technical documentation on how the code is put together.

## Running the terminal

Directly, no install needed:

```bash
python3 main.py
```

Or install it as a `nodetech` command (recommended if you'll run it often):

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/nodetech
```

Once installed, activate the virtual environment (`source .venv/bin/activate`) and you can just type `nodetech` from anywhere in this project.

Tab-completion works for commands and most arguments (node IDs, country names, building/resource/terrain/division/government names, saved world names) when run in a real terminal — it's not active when piping input in (e.g. `echo commands | python3 main.py`).

## Running in the browser

The same game also runs entirely client-side via [Pyodide](https://pyodide.org/) (CPython compiled to WebAssembly) — no install, no server-side process, and it's the actual `main.py`/`node.py`/`country.py`/`division.py`/`database.py` executing, not a reimplementation.

```bash
python3 -m http.server 8000
```

Run that from the repo root, then open `http://localhost:8000/web/index.html`. It has to be served over HTTP — opening `web/index.html` directly as a `file://` path won't work, since the page fetches the `.py` source files from the server on load.

It supports the same commands as the CLI, typed into the same kind of terminal, plus:

- **Load save**: pick a `.json` save file from your computer to load it into the running world.
- **Download current save**: saves the current world and downloads it as a file.
- `map` renders inline in a panel next to the terminal instead of opening a separate window.

Game state only exists in the browser tab's memory — reloading the page starts a fresh session, so download a save first if you want to keep one.

## Commands

### General

| Command         | Description                       |
| --------------- | --------------------------------- |
| `help`          | Show a short pointer to this list |
| `quit` / `exit` | Exit the game                     |

### Save/load

Start here: create a new world, or load one you already have.

| Command                                          | Description                                                                                                                          |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| `new-world <name> <width> <height> [start_year]` | Create a fresh world with a `<width> x <height>` grid, save it as `<name>.json`, optionally starting at a given year (defaults to 0) |
| `open <name or path>`                            | Load a world from a save file, replacing the current one                                                                             |
| `list-worlds`                                    | List all saved worlds                                                                                                                |
| `rename-world <old_name> <new_name>`             | Rename a saved world                                                                                                                 |
| `save [name or path]`                            | Save the world to a file; reuses the last opened/saved path if omitted                                                               |

Worlds are saved as JSON via [database.py](database.py), which stores every node (including its nested military deployments and divisions) and every country.

`new-world`, `rename-world`, `list-worlds`, `open`, and `save` all accept a bare name (e.g. `alpha`) instead of a full path — these are automatically resolved to `~/proppunk game files/<name>.json`, so every world you create lives together in that one folder (created automatically the first time it's needed). Pass an explicit path instead (containing a `/`) to save/load outside that folder.

`open` reads a save file and populates the running `World`; `save` overwrites the file it was last opened from (or pass a new name/path to save elsewhere).

### Countries

Once you have a world loaded, start a country.

| Command                                | Description                                                                                              |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `create-country <name> [government]`   | Create a new country                                                                                     |
| `view-country <name>`                  | View a country's GDP, population, and growth rates (live, summed from its nodes), and its division count |
| `list-countries`                       | List all countries                                                                                       |
| `setgovernment <country> <government>` | Set a country's government type                                                                          |
| `country-status <country>`             | Show a country's GDP, population, and other stats                                                        |

### Nodes

Nodes are the map tiles a country controls, and where its economy, population, and military live. Every world is a grid (`<width> x <height>`, set by `new-world`); every node occupies exactly one `(x, y)` slot on it.

| Command                           | Description                                                                                           |
| --------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `create <id> <x> <y>`             | Create a new node at a grid position                                                                  |
| `list`                            | List all nodes, with their positions                                                                  |
| `map`                             | Generate an interactive HTML map of the grid (colored by owning country) and open it in your browser  |
| `view <id>`                       | View details of a node, including its position, economic growth rate, and projected population growth |
| `connect <id1> <id2>`             | Connect two nodes                                                                                     |
| `disconnect <id1> <id2>`          | Remove the connection between two nodes                                                               |
| `setcountry <id> <country>`       | Set a node's controlling country                                                                      |
| `unsetcountry <id>`               | Clear a node's controlling country, making it unclaimed                                               |
| `setterrain <id> <terrain>`       | Set terrain type                                                                                      |
| `setpopulation <id> <population>` | Set a node's population                                                                               |
| `setpopgrowth <id> <rate>`        | Set a node's population growth rate                                                                   |
| `seteconomy <id> <output>`        | Set a node's economic output                                                                          |
| `build <id> <building>`           | Enable a building                                                                                     |
| `unbuild <id> <building>`         | Disable a building                                                                                    |
| `addresource <id> <resource>`     | Add a resource to a node                                                                              |
| `removeresource <id> <resource>`  | Remove a resource from a node                                                                         |
| `build-extraction <id> <site>`    | Build an extraction site — only allowed if the node has the resource that site requires               |
| `unbuild-extraction <id> <site>`  | Remove an extraction site                                                                             |

A node's position is **required at creation and can't be changed afterward** — `create` rejects a position outside the grid or already occupied by another node (one node per slot). Creating a node **automatically connects it** to any existing node in an orthogonally adjacent slot (up/down/left/right, no diagonals) — the same bidirectional connection `connect` creates manually. `connect`/`disconnect` are still there for links that aren't grid-adjacent, or to remove an auto-created connection.

A node's **economic growth rate isn't set manually** — it's always calculated live from the node's current GDP per capita (richer nodes grow slower, poorer nodes faster, along an S-curve floor/ceiling) plus a modifier per enabled building, and it updates instantly whenever you check `view` or `projections`, not just on `advance-year`. Population growth is still set directly with `setpopgrowth`; a node's *projected* population growth (shown in `view`) is the same kind of live automatic forecast, just informational rather than something that drives the simulation.

**Resources** (`BASIC_MATERIALS`, `RARE_EARTH_METALS`, `OIL_GAS`) represent what a node has naturally. **Extraction sites** (`BASIC_MATERIALS_MINE`, `RARE_EARTH_MINE`, `OIL_RIG`) are structures you build to exploit a resource — each one requires its matching resource to already be present on the node (`build-extraction` refuses otherwise) — and once built, give a much larger economic growth boost than an ordinary building.

`map` writes a colored, interactive map to `~/proppunk game files/map.html` and opens it in your default browser — one tile per grid slot, colored by owning country, with a legend and a side panel that shows every country's live GDP/population by default and swaps to a tile's own stats (position, country, terrain, population, economic output) when you hover it.

### Military

| Command                                                                                                                     | Description                                                             |
| --------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `deploy <id> <country> <name> <div_type> <manpower> <supply>`                                                               | Create a named division and deploy it directly to a node                |
| `create-division <country> <name> <div_type> <manpower> <supply>`                                                           | Create a named division in a country's reserve (unassigned to any node) |
| `create-airforce-division <country> <name> <manpower> <supply> <aircraft_type> <equipment_rating> <aircraft_count> <range>` | Create a named air force division in reserve                            |
| `deploy-airforce <id> <country> <name> <manpower> <supply> <aircraft_type> <equipment_rating> <aircraft_count> <range>`     | Create a named air force division and deploy it directly to a node      |
| `deploy-reserve <country> <name> <id>`                                                                                      | Deploy an existing reserve division (by name) to a node                 |
| `country-divisions <country>`                                                                                               | List all of a country's divisions, deployed and in reserve              |

Every division has a player-given **name** (must be unique within its own country, e.g. two countries can each have a "1st Infantry") — this is what you use in commands like `deploy-reserve`. Each division also has an internal **ID** (e.g. `Fedran Republic_div_1`), scoped to the deploying country and shown alongside the name in listings, but you shouldn't need to type it.

`AIR_FORCE` is a division type, but it needs extra details a regular division doesn't have (aircraft type, equipment rating, aircraft count, range), so it's created with its own commands (`create-airforce-division`/`deploy-airforce`) instead of the generic `create-division`/`deploy` — using the generic commands with `air_force` as the type will point you to the right one. Once created, an air force division behaves exactly like any other division everywhere else (`deploy-reserve`, `country-divisions`, `view`, save/load) — its extra stats just show up alongside the normal ones.

### World & simulation

| Command           | Description                                                                                                                                   |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `world status`    | List every country with its GDP and population                                                                                                |
| `world divisions` | List every country's divisions (deployed and in reserve), grouped by country                                                                  |
| `projections`     | List every country's economic growth rate and projected population growth rate (calculated live)                                              |
| `advance-year`    | Advance the game by one year                                                                                                                  |
| `year`            | Show the current year and how many years have passed since the world started                                                                  |
| `forceupdate`     | Recalculate every country's GDP and population from its nodes, without advancing the year (growth rates are already live and don't need this) |
| `apply-supply`    | Run one supply iteration (pool rail-connected local supply against deployed divisions' demand, penalize/recover morale and equipment) without advancing the year |

### Reference lists

Lookup tables for valid values used by the commands above.

| Command            | Description                          |
| ------------------ | ------------------------------------ |
| `buildings`        | List available building types        |
| `resources`        | List available resource types        |
| `extraction-sites` | List available extraction site types |
| `terrains`         | List available terrain types         |
| `division-types`   | List available division types        |
| `governments`      | List available government types      |
