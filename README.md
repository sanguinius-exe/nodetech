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

| Command | Description |
| --- | --- |
| `help` | Show a short pointer to this list |
| `quit` / `exit` | Exit the game |

### Nodes

| Command | Description |
| --- | --- |
| `list` | List all nodes |
| `create <id>` | Create a new node |
| `view <id>` | View details of a node, including its projected growth rates |
| `connect <id1> <id2>` | Connect two nodes |
| `setcountry <id> <country>` | Set a node's controlling country |
| `setterrain <id> <terrain>` | Set terrain type |
| `setpopulation <id> <population>` | Set a node's population |
| `setpopgrowth <id> <rate>` | Set a node's population growth rate |
| `seteconomy <id> <output>` | Set a node's economic output |
| `seteconomygrowth <id> <rate>` | Set a node's economic growth rate |
| `build <id> <building>` | Enable a building |
| `unbuild <id> <building>` | Disable a building |

### Military

| Command | Description |
| --- | --- |
| `deploy <id> <country> <div_type> <manpower> <supply>` | Deploy a division |
| `country-divisions <country>` | List all divisions deployed by a country |

### Countries

| Command | Description |
| --- | --- |
| `create-country <name> [government]` | Create a new country |
| `view-country <name>` | View details of a country |
| `list-countries` | List all countries |
| `setgovernment <country> <government>` | Set a country's government type |
| `country-status <country>` | Show a country's GDP, population, and other stats |

### World & simulation

| Command | Description |
| --- | --- |
| `world status` | List every country with its GDP and population |
| `projections` | List every country's projected economic and population growth rates |
| `advance-year` | Advance the game by one year |

### Reference lists

| Command | Description |
| --- | --- |
| `buildings` | List available building types |
| `terrains` | List available terrain types |
| `division-types` | List available division types |
| `governments` | List available government types |

### Save/load

| Command | Description |
| --- | --- |
| `new-world <name>` | Create a fresh world and save it as `<name>.json` |
| `rename-world <old_name> <new_name>` | Rename a saved world |
| `list-worlds` | List all saved worlds |
| `open <name or path>` | Load a world from a save file, replacing the current one |
| `save [name or path]` | Save the world to a file; reuses the last opened/saved path if omitted |

## Save files

Worlds are saved as JSON via [database.py](database.py), which stores every node (including its nested military deployments and divisions) and every country.

`new-world`, `rename-world`, `list-worlds`, `open`, and `save` all accept a bare name (e.g. `alpha`) instead of a full path — these are automatically resolved to `~/proppunk game files/<name>.json`, so every world you create lives together in that one folder (created automatically the first time it's needed). Pass an explicit path instead (containing a `/`) to save/load outside that folder.

`open` reads a save file and populates the running `World`; `save` overwrites the file it was last opened from (or pass a new name/path to save elsewhere).
