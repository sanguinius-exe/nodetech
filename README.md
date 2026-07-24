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

## Commands

### General

| Command         | Description                       |
| --------------- | --------------------------------- |
| `help`          | Show a short pointer to this list |
| `quit` / `exit` | Exit the game                     |

### Save/load

Start here: create a new world, or load one you already have.

| Command                              | Description                                                            |
| ------------------------------------ | ---------------------------------------------------------------------- |
| `new-world <name>`                   | Create a fresh world and save it as `<name>.json`                      |
| `open <name or path>`                | Load a world from a save file, replacing the current one               |
| `list-worlds`                        | List all saved worlds                                                  |
| `rename-world <old_name> <new_name>` | Rename a saved world                                                   |
| `save [name or path]`                | Save the world to a file; reuses the last opened/saved path if omitted |

Worlds are saved as JSON via [database.py](database.py), which stores every node (including its nested military deployments and divisions) and every country.

`new-world`, `rename-world`, `list-worlds`, `open`, and `save` all accept a bare name (e.g. `alpha`) instead of a full path — these are automatically resolved to `~/proppunk game files/<name>.json`, so every world you create lives together in that one folder (created automatically the first time it's needed). Pass an explicit path instead (containing a `/`) to save/load outside that folder.

`open` reads a save file and populates the running `World`; `save` overwrites the file it was last opened from (or pass a new name/path to save elsewhere).

### Countries

Once you have a world loaded, start a country.

| Command                                | Description                                       |
| -------------------------------------- | ------------------------------------------------- |
| `create-country <name> [government]`   | Create a new country                              |
| `view-country <name>`                  | View details of a country                         |
| `list-countries`                       | List all countries                                |
| `setgovernment <country> <government>` | Set a country's government type                   |
| `country-status <country>`             | Show a country's GDP, population, and other stats |

### Nodes

Nodes are the map tiles a country controls, and where its economy, population, and military live.

| Command                           | Description                                                  |
| --------------------------------- | ------------------------------------------------------------ |
| `create <id>`                     | Create a new node                                            |
| `list`                            | List all nodes                                               |
| `view <id>`                       | View details of a node, including its projected growth rates |
| `connect <id1> <id2>`             | Connect two nodes                                            |
| `setcountry <id> <country>`       | Set a node's controlling country                             |
| `setterrain <id> <terrain>`       | Set terrain type                                             |
| `setpopulation <id> <population>` | Set a node's population                                      |
| `setpopgrowth <id> <rate>`        | Set a node's population growth rate                          |
| `seteconomy <id> <output>`        | Set a node's economic output                                 |
| `seteconomygrowth <id> <rate>`    | Set a node's economic growth rate                            |
| `build <id> <building>`           | Enable a building                                            |
| `unbuild <id> <building>`         | Disable a building                                           |

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

| Command           | Description                                                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------------------------------ |
| `world status`    | List every country with its GDP and population                                                                     |
| `world divisions` | List every country's divisions (deployed and in reserve), grouped by country                                       |
| `projections`     | List every country's projected economic and population growth rates                                                |
| `advance-year`    | Advance the game by one year                                                                                       |
| `forceupdate`     | Recalculate every country's GDP, population, and projected growth rates from its nodes, without advancing the year |

### Reference lists

Lookup tables for valid values used by the commands above.

| Command          | Description                     |
| ---------------- | ------------------------------- |
| `buildings`      | List available building types   |
| `terrains`       | List available terrain types    |
| `division-types` | List available division types   |
| `governments`    | List available government types |
