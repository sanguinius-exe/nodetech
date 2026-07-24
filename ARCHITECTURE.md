# Architecture

Technical documentation for how nodetech is put together. For the command reference, see [README.md](README.md).

## Overview

nodetech is a single-process, in-memory text game. Everything lives in one `World` object for the lifetime of a terminal session; there is no live database connection — state is only persisted when you explicitly `save`, and only loaded when you explicitly `open` or `new-world`.

```
main.py        terminal loop, command parsing/dispatch, World container
node.py        Node, MilitaryDeployment, Terrain, BuildingType
country.py     Country, GovernmentType
division.py    Division, DivisionType
database.py    JSON (de)serialization, save-file location/naming
```

Dependency direction is strictly one-way: `division.py` has no imports from this project; `node.py` imports `division.py`; `country.py` imports `node.py`; `database.py` imports all three model modules; `main.py` imports `database.py` and all three model modules. Nothing imports `main.py`, so there is no circular-import risk anywhere in the graph.

## Data model

All model classes (`Node`, `Country`, `Division`, `MilitaryDeployment`) are plain `@dataclass`es with public fields. Each also exposes `get_*` accessor methods — these exist purely by convention (established early in the project) rather than for encapsulation; code elsewhere in the project reads/writes the dataclass fields directly just as often as it calls the getters.

### `Division` ([division.py](division.py))

The smallest unit of military strength.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | `str` | Unique within its `MilitaryDeployment` (`div_1`, `div_2`, ... assigned by `main.py` at deploy time) |
| `division_type` | `DivisionType` | INFANTRY / ARMOR / ARTILLERY / CAVALRY / AIRBORNE / ENGINEER / LOGISTICS |
| `manpower` | `int` | Number of men in the division |
| `supply_requirement` | `float` | How much supply the division consumes (not yet consumed by any game logic — stored for future use) |
| `morale` | `float` | Defaults to `100.0` |
| `location` | `str \| None` | The `Node.id` the division is currently stationed at |

### `MilitaryDeployment` ([node.py](node.py))

A grouping of divisions belonging to one country, attached to one `Node`. A single `Node` can hold multiple deployments (e.g. a contested tile with both a defending and an occupying force), one per country present.

- `country: str` — the deploying country's name (matches `Country.name`, not necessarily the node's owner — a country can deploy divisions onto territory it doesn't control)
- `divisions: list[Division]`
- `get_strength()` sums `manpower` across its divisions

### `Node` ([node.py](node.py))

A single map tile — the core unit the whole game is built on.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | `str` | Unique tile identifier, chosen by the player at `create` time |
| `country` | `str \| None` | Name of the controlling `Country`, or `None` if unclaimed |
| `terrain` | `Terrain` | PLAINS / FOREST / HILLS / MOUNTAIN / DESERT / WATER / URBAN |
| `connected_tiles` | `list[str]` | IDs of adjacent/linked nodes (bidirectional; `connect` maintains both sides) |
| `building_options` | `list[bool]` | One flag per `BuildingType`, indexed by `BuildingType.value - 1` (see below) |
| `economic_output` / `economic_growth_rate` | `float` | Current output and its annual compounding rate |
| `population` / `population_growth_rate` | `int` / `float` | Current population and its annual compounding rate |
| `military_deployments` | `list[MilitaryDeployment]` | All deployments currently on this tile, from any country |

**`building_options` indexing**: this is a parallel boolean array, not a set of building names. `BuildingType` is an `Enum` using `auto()`, so members are numbered 1..N in declaration order (`FARM=1, MINE=2, ...`). `Node.has_building(bt)` reads `building_options[bt.value - 1]`; `main.py`'s `build`/`unbuild` commands write to that same index. If `BuildingType` gains or loses a member, every existing `building_options` list (including ones in save files) shifts out of alignment — see "Known limitations" below.

**`Node.advance_year()`** is the per-tile simulation step:
```python
population = max(0, round(population * (1 + population_growth_rate)))
economic_output = max(0.0, economic_output * (1 + economic_growth_rate))
```
Growth compounds once per call; it does not know about the calendar, only about how many times it's been invoked.

### `Country` ([country.py](country.py))

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | `str` | Primary key by convention — `World.countries` is keyed by this, so a rename requires care (see below) |
| `nodes` | `list[str]` | IDs of nodes this country controls. **This is the country's only structural link to the map** — it is a list of `Node.id` strings, not `Node` object references. Anything that needs the actual `Node` must look it up in `World.nodes` |
| `government_type` | `GovernmentType` | DEMOCRACY / REPUBLIC / MONARCHY / DICTATORSHIP / OLIGARCHY / THEOCRACY / ANARCHY |
| `treasury` | `float` | Stored but not currently read or written by any game logic beyond initialization |
| `stability` | `float` | Same — reserved for future use |
| `economic_output` | `float` | **A cached snapshot**, not a live value — see below |
| `population` | `int` | Same |

**Why `economic_output`/`population` are snapshots, not live properties**: `Country` cannot compute these on its own because it only holds node *IDs* — it needs the actual node data, which only `World` has. So `Country` exposes pure calculation methods that take the node lookup as a parameter:

```python
country.calculate_economic_output(all_nodes: dict[str, Node]) -> float   # sum of owned nodes' economic_output
country.calculate_population(all_nodes: dict[str, Node]) -> int          # sum of owned nodes' population
```

...and separate mutating methods that call those and store the result on the country:

```python
country.update_economic_output(all_nodes)   # self.economic_output = self.calculate_economic_output(all_nodes)
country.update_population(all_nodes)
```

`main.py`'s `advance_year()` calls `update_economic_output`/`update_population` for every country, once per year, right after advancing every node. **This means a country's displayed GDP/population (`world status`, `country-status`) only reflects reality as of the last `advance-year` call** — if you `seteconomy` a node or reassign it to a different country and immediately check `country-status`, you'll see stale numbers until the next `advance-year`.

## `World` ([main.py](main.py))

The in-memory container for one game session:

```python
class World:
    nodes: dict[str, Node]        # keyed by Node.id
    countries: dict[str, Country] # keyed by Country.name
    year: int                     # starts at 0, incremented by advance_year()
    save_path: str | None         # last path used by `open` or `save`, for bare `save`
```

There is exactly one `World` instance per process, created in `main()` and threaded through every command handler. Nothing about the design prevents holding multiple `World` instances (e.g. for a future multi-game-at-once mode), but the terminal loop only ever drives one at a time — `new-world`/`open` both **replace the contents of the current `World` in place** (`.clear()` + repopulate) rather than constructing a new one, which is why `world.save_path` and any other bookkeeping on the `World` object survive across those operations except where explicitly overwritten.

### Node/Country cross-references

Both directions of the Node↔Country relationship are maintained as plain ID strings, kept in sync manually by `cmd_setcountry`:

```python
old_country.nodes.remove(node_id)   # detach from previous owner, if any
node.country = country_name          # Node -> Country (by name)
country.nodes.append(node_id)        # Country -> Node (by id)
```

There is no automatic consistency check elsewhere — if code ever mutates `node.country` or `country.nodes` directly instead of going through `cmd_setcountry`, the two sides can drift out of sync.

## Command dispatch ([main.py](main.py))

`run_command(world, raw)` is a single long `if/elif` chain keyed on the first whitespace-separated token (parsed with `shlex.split`, so quoted multi-word arguments like `"Fedran Republic"` work). Each branch either handles trivial cases inline (`buildings`, `terrains`, `division-types`, `governments`, `list`, `list-countries`) or delegates to a `cmd_*` function that does its own argument-count validation and prints a `Usage: ...` line on mismatch. There is no shared argparse-style layer — every command hand-rolls its own validation, so error messages and behavior on bad input are consistent by convention, not by shared code.

`world status` is the one multi-word command: it's dispatched by matching `command == "world"` and then checking `args[0] == "status"`, rather than being registered as its own token.

The loop itself (`main()`) is a trivial `input()` → `run_command()` → repeat cycle; `run_command` returns `False` on `quit`/`exit` to end it. `EOFError`/`KeyboardInterrupt` on `input()` also end the loop gracefully.

## Persistence ([database.py](database.py))

### Save file format

A save file is one JSON object:

```jsonc
{
  "year": 2,
  "nodes": {
    "<node_id>": {
      "id": "...", "country": "...", "terrain": "HILLS",
      "connected_tiles": ["..."],
      "building_options": [true, false, ...],   // positional, see building_options above
      "economic_output": 550.0, "economic_growth_rate": 0.1,
      "population": 10500, "population_growth_rate": 0.05,
      "military_deployments": [
        {
          "country": "...",
          "divisions": [
            {"id": "div_1", "division_type": "INFANTRY", "manpower": 5000,
             "supply_requirement": 12.5, "morale": 100.0, "location": "<node_id>"}
          ]
        }
      ]
    }
  },
  "countries": {
    "<country_name>": {
      "name": "...", "nodes": ["<node_id>", ...], "government_type": "MONARCHY",
      "treasury": 0.0, "stability": 50.0, "economic_output": 550.0, "population": 10500
    }
  }
}
```

Enums are stored by member **name** (`"HILLS"`, not `1`), so the encoding is stable across reordering `auto()` values in the enum definitions — but renaming an enum member breaks old save files (there's no migration layer).

`database.py` has no knowledge of `World` — its functions are written against duck-typed objects with `.nodes`, `.countries`, `.year` attributes (matching dicts of the right shapes), specifically so it doesn't need to import `main.py` and create a cycle:

- `save_world(world, path)` — serializes and writes, always overwriting whatever is at `path`.
- `load_into_world(world, path)` — reads JSON, then `.clear()`s and repopulates `world.nodes` / `world.countries` / `world.year` **in place**. It mutates the object you pass in rather than returning a new one, which is why `cmd_open` can call it on the live `World` and have the running session immediately reflect the loaded file.

Deserialization is intentionally strict: it indexes required dict keys directly (`data["id"]`, etc.) rather than using `.get()` with defaults, so a hand-edited or corrupted save file fails loudly with a `KeyError` (caught and reported by `cmd_open`) instead of silently producing a half-populated `Node`.

### Save file location

```python
DEFAULT_SAVE_DIR = Path.home() / "proppunk game files"
```

`resolve_save_path(name_or_path)` is the single choke point every save-related command routes through:

- A bare name (`"alpha"`, no `/` in it) → resolved to `DEFAULT_SAVE_DIR / "alpha.json"` (creating the directory if needed). `.json` is appended automatically if the name doesn't already end in it.
- Anything containing a path separator, or an absolute path → returned unchanged. This is the escape hatch for saving/loading outside the managed folder.

`list_worlds()` globs `DEFAULT_SAVE_DIR/*.json` and returns stems (no extension). `rename_world(old, new)` resolves both names through the same function and does a plain `Path.rename`, refusing to clobber an existing target (`FileExistsError`) or rename something that doesn't exist (`FileNotFoundError`) — `main.py`'s `cmd_rename_world` translates both into user-facing messages, and additionally repoints `world.save_path` if the world you just renamed happens to be the one currently loaded.

## Packaging ([pyproject.toml](pyproject.toml))

Flat module layout (no `src/` package directory) declared via `[tool.setuptools] py-modules`. `[project.scripts] nodetech = "main:main"` gives `pip install -e .` a `nodetech` console entry point. Targets Python ≥3.9; every module opens with `from __future__ import annotations` specifically so PEP 604 union syntax (`str | None`) and builtin generics (`list[str]`) work as annotations without needing 3.10.

## Known limitations / things a future contributor should know

- **No migrations**: adding/removing/reordering `Enum` members (`BuildingType` especially, due to positional `building_options`) or dataclass fields will break existing save files with no warning beyond a `KeyError`/`ValueError` at load time.
- **Country stats are eventually-consistent, not live**: see the `Country.economic_output`/`population` explanation above. `world status` / `country-status` can lie until the next `advance-year`.
- **No validation that a country's `nodes` list matches reality**: it's hand-maintained by `cmd_setcountry`; direct field mutation elsewhere would desync it from `Node.country`.
- **`Division.supply_requirement` and `Country.treasury`/`stability` are inert**: modeled and persisted, but no game logic currently reads or changes them based on gameplay (only player commands set them directly).
- **Single global `World`**: the terminal only ever manages one game at a time in memory; `new-world`/`open` overwrite it rather than switching between multiple loaded worlds.
