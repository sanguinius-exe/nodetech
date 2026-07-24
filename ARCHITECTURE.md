# Architecture

Technical documentation for how nodetech is put together. For the command reference, see [README.md](README.md).

## Overview

nodetech is a single-process, in-memory text game. Everything lives in one `World` object for the lifetime of a terminal session; there is no live database connection — state is only persisted when you explicitly `save`, and only loaded when you explicitly `open` or `new-world`.

```
main.py        terminal loop, command parsing/dispatch, World container
node.py        Node, MilitaryDeployment, Terrain, BuildingType, ResourceType, ExtractionSiteType
country.py     Country, GovernmentType
division.py    Division, AirForceDivision, DivisionType, ID generation
database.py    JSON (de)serialization, save-file location/naming
```

Dependency direction is strictly one-way: `division.py` has no imports from this project; `node.py` imports `division.py`; `country.py` imports `division.py` and `node.py`; `database.py` imports all three model modules; `main.py` imports `database.py` and all three model modules. Nothing imports `main.py`, so there is no circular-import risk anywhere in the graph.

## Data model

All model classes (`Node`, `Country`, `Division`, `MilitaryDeployment`) are plain `@dataclass`es with public fields. Each also exposes `get_*` accessor methods — these exist purely by convention (established early in the project) rather than for encapsulation; code elsewhere in the project reads/writes the dataclass fields directly just as often as it calls the getters.

### `Division` ([division.py](division.py))

The smallest unit of military strength.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | `str` | Internal identifier, auto-generated — see "Division IDs and names" below |
| `name` | `str` | Player-given, and the **primary way divisions are referenced** in commands (must be unique within its own country — see below) |
| `division_type` | `DivisionType` | INFANTRY / ARMOR / ARTILLERY / CAVALRY / AIRBORNE / ENGINEER / LOGISTICS / AIR_FORCE |
| `manpower` | `int` | Number of men in the division |
| `supply_requirement` | `float` | How much supply the division consumes (not yet consumed by any game logic — stored for future use) |
| `morale` | `float` | Defaults to `100.0` |
| `location` | `str \| None` | The `Node.id` the division is currently stationed at, or `None` if it's in a country's reserve |

Divisions are constructed exclusively through `Division.create(...)`, never the raw dataclass constructor directly — this is what guarantees a properly generated `id`.

#### Division IDs and names

There are two distinct identifiers, serving different purposes:

- **`name`** is what the player types and what's shown first in every listing. It's required at creation time and must be unique *within the same country* — `find_division_by_name()` in `main.py` checks both a country's on-map divisions and its reserve before allowing a `deploy`/`create-division`/`create-airforce-division` to proceed. Two different countries can each have a division named `"1st Infantry"` with no conflict, since uniqueness is scoped per country.
- **`id`** (e.g. `Fedran Republic_div_3`) exists so every division has a globally-safe internal key even if names collide across countries, and is generated automatically — the player never supplies it.

ID generation lives in `division.py` as module-level state, scoped **per country**:

```python
_id_counters: dict[str, itertools.count] = {}

def next_division_id(country: str) -> str:
    counter = _id_counters.setdefault(country, itertools.count(1))
    return f"{country}_div_{next(counter)}"
```

Each country's counter is independent, so `"Fedran Republic"` and `"Astoria"` both mint IDs starting at `_div_1` without colliding — the country name is baked directly into the ID string. Because this counter is in-process global state (not stored on `World`), it must be **reseeded on load** or a freshly loaded save's next-created division could reuse an ID already present in the file. `database.load_into_world()` handles this by scanning every division in the loaded file (across all nodes' deployments and all countries' reserves), finding the highest `_div_N` suffix per country, and calling `seed_division_id_counter(country, max + 1)` before returning. `new-world`/`create-country` don't need any special handling here since a country with no prior divisions naturally starts its counter at 1 the first time `next_division_id` is called for it.

#### `AirForceDivision` — a real subclass, not a flag

```python
@dataclass
class AirForceDivision(Division):
    aircraft_type: str = ""
    equipment_rating: float = 0.0
    aircraft_count: int = 0
    range: float = 0.0
```

This is genuine inheritance (`isinstance(division, Division)` is `True` for an `AirForceDivision`), not a `Division` with an `AIR_FORCE` enum tag and unused extra fields. That's deliberate: it means every piece of code that already works with `Division` generically — `MilitaryDeployment.divisions`, `Country.reserve_divisions`, `deploy-reserve`'s name lookup, `country-divisions`, `world divisions`, JSON save/load — handles an `AirForceDivision` with zero special-casing. The only places that *do* know about the subtype are:

- **Creation**: `create-airforce-division`/`deploy-airforce` (their own commands, since the constructor needs 4 extra required arguments that don't fit the generic `create-division`/`deploy` signature). Using the generic commands with `air_force` as the type is explicitly rejected in `cmd_deploy`/`cmd_create_division` with a message pointing at the right command, rather than silently creating an air force division with empty/zero aircraft fields.
- **Display**: `format_division_extra()` in `main.py` does an `isinstance` check to print the extra aircraft stats as an indented detail line under the division's normal summary line.
- **Persistence**: `database.py`'s `_division_to_dict`/`_division_from_dict` check `isinstance(division, AirForceDivision)` / `data["division_type"] == "AIR_FORCE"` respectively to include/reconstruct the extra fields.

### `MilitaryDeployment` ([node.py](node.py))

A grouping of divisions belonging to one country, attached to one `Node`. A single `Node` can hold multiple deployments (e.g. a contested tile with both a defending and an occupying force), one per country present.

- `country: str` — the deploying country's name (matches `Country.name`, not necessarily the node's owner — a country can deploy divisions onto territory it doesn't control)
- `divisions: list[Division]`
- `get_strength()` sums `manpower` across its divisions

### Reserve divisions

A `Division` doesn't have to be on a node. `Country.reserve_divisions: list[Division]` holds divisions that exist (and are fully constructed, with an ID and a name) but aren't assigned anywhere (`location=None`). `create-division`/`create-airforce-division` create directly into this list; `deploy-reserve` (`Country.remove_reserve_division(name)`) pulls one out by name, sets its `location`, and appends it to the appropriate node's `MilitaryDeployment` (creating that deployment if the country doesn't already have one on that node) — the same division object just moves from one list to another, it isn't recreated. `deploy`/`deploy-airforce` skip the reserve entirely and create+place a division on a node in one step.

### `Node` ([node.py](node.py))

A single map tile — the core unit the whole game is built on.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | `str` | Unique tile identifier, chosen by the player at `create` time |
| `country` | `str \| None` | Name of the controlling `Country`, or `None` if unclaimed |
| `terrain` | `Terrain` | PLAINS / FOREST / HILLS / MOUNTAIN / DESERT / WATER / URBAN |
| `connected_tiles` | `list[str]` | IDs of adjacent/linked nodes (bidirectional; `connect` maintains both sides) |
| `building_options` | `list[bool]` | One flag per `BuildingType`, indexed by `BuildingType.value - 1` (see below) |
| `resources` | `list[bool]` | One flag per `ResourceType` (`BASIC_MATERIALS` / `RARE_EARTH_METALS` / `OIL_GAS`), same positional-array pattern as `building_options` |
| `extraction_sites` | `list[bool]` | One flag per `ExtractionSiteType`, same positional-array pattern — see "Resources and extraction sites" below |
| `economic_output` / `economic_growth_rate` | `float` | Current output, and its growth rate — **auto-calculated**, not player-set. See below |
| `population` / `population_growth_rate` | `int` / `float` | Current population and its annual compounding rate — this one *is* set manually (`setpopgrowth`) |
| `military_deployments` | `list[MilitaryDeployment]` | All deployments currently on this tile, from any country |
| `projected_population_growth_rate` | `float` | A simulation-derived *forecast* for population growth — informational only, doesn't drive anything. There is no equivalent "projected" field for economic growth, since `economic_growth_rate` itself now plays that role — see below |

**`building_options`/`resources`/`extraction_sites` indexing**: these are parallel boolean arrays, not sets of names. Each corresponding `Enum` uses `auto()`, so members are numbered 1..N in declaration order (`FARM=1, MINE=2, ...`). `Node.has_building(bt)`/`has_resource(rt)`/`has_extraction_site(st)` read `<list>[x.value - 1]`; the corresponding `build`/`unbuild`, `addresource`/`removeresource`, `build-extraction`/`unbuild-extraction` commands write to that same index. If any of these enums gains or loses a member, every existing list of that kind (including ones in save files) shifts out of alignment — see "Known limitations" below.

#### Resources and extraction sites

`ResourceType` (`BASIC_MATERIALS`, `RARE_EARTH_METALS`, `OIL_GAS`) represents what a node naturally has; it's set directly with `addresource`/`removeresource` — there's no procedural generation or terrain linkage, it's purely player-declared. `ExtractionSiteType` (`BASIC_MATERIALS_MINE`, `RARE_EARTH_MINE`, `OIL_RIG`) represents a structure built to exploit a resource, gated by `EXTRACTION_SITE_RESOURCE_REQUIREMENTS: dict[ExtractionSiteType, ResourceType]` — a fixed 1:1 mapping from each site type to the resource it requires:

```python
EXTRACTION_SITE_RESOURCE_REQUIREMENTS = {
    ExtractionSiteType.BASIC_MATERIALS_MINE: ResourceType.BASIC_MATERIALS,
    ExtractionSiteType.RARE_EARTH_MINE: ResourceType.RARE_EARTH_METALS,
    ExtractionSiteType.OIL_RIG: ResourceType.OIL_GAS,
}
```

`Node.can_build_extraction_site(site_type)` checks `has_resource(EXTRACTION_SITE_RESOURCE_REQUIREMENTS[site_type])`; `cmd_extraction` in `main.py` calls this before allowing `build-extraction` to proceed (removal via `unbuild-extraction` is never gated). Once built, an extraction site contributes to economic growth via `ECONOMIC_GROWTH_EXTRACTION_SITE_MODIFIERS` — a separate, much larger modifier table than the one for ordinary buildings (its smallest entry is triple the strongest building modifier), so resource extraction is meant to be a significant, deliberate economic strategy rather than an incremental optimization like a building. There is no equivalent population-growth modifier for extraction sites; they only affect the economic side.

#### How growth rates are calculated

`Node.calculate_economic_growth_rate()`/`calculate_projected_population_growth_rate()` both derive a growth rate from the node's current GDP per capita (`economic_output / population`, or `0.0` if `population <= 0`) via the shared `_growth_rate_from_gdp_per_capita()` helper. The model: richer nodes grow slower, poorer nodes grow faster, following an **S-curve (logistic function)** bounded by a floor and ceiling —

```python
sigmoid = 1.0 / (1.0 + math.exp(-(gdp_per_capita - GDP_PER_CAPITA_MIDPOINT) / GDP_PER_CAPITA_STEEPNESS))
base_rate = ceiling - (ceiling - floor) * sigmoid
```

`GDP_PER_CAPITA_LOW`/`GDP_PER_CAPITA_HIGH` (`200`/`1500`) mark where the curve is meant to sit near the ceiling and near the floor respectively; `GDP_PER_CAPITA_MIDPOINT` is their average (`850`, the curve's inflection point — exactly halfway between floor and ceiling) and `GDP_PER_CAPITA_STEEPNESS` is a quarter of their span (`325`), which places the curve's steepest transition roughly within `[LOW, HIGH]` (at `LOW`/`HIGH` the sigmoid is at ≈12%/≈88% of its full swing; it asymptotically approaches but never exactly reaches 0%/100% beyond that). All four are flagged as tunable since the game has no fixed definition of what a unit of `economic_output` represents. Economic growth is bounded `[1.5%, 5%]`; population growth `[0.5%, 3.5%]` (narrower, since population is assumed to respond less elastically to wealth than output does). Each building the node has enabled nudges the rate up or down via a flat per-building modifier (`ECONOMIC_GROWTH_BUILDING_MODIFIERS`/`POPULATION_GROWTH_BUILDING_MODIFIERS`, e.g. `FACTORY` boosts economic growth but slightly dampens population growth); each extraction site adds a much larger economic-only modifier (`ECONOMIC_GROWTH_EXTRACTION_SITE_MODIFIERS`, population growth is untouched). The combined result is re-clamped into the floor/ceiling range afterward, so a stack of modifiers can't push a node's rate outside the intended band.

**Economic and population growth are handled asymmetrically**, and this is deliberate: economic growth used to have its own manually-set `economic_growth_rate` (with a `seteconomygrowth` command), separate from a purely informational `projected_economic_growth_rate` calculated by this same formula. That distinction was collapsed — `economic_growth_rate` is now *always* the freshly-calculated value; there's no manual override and no separate "projected" field for it anymore. Population kept the older two-field design: `population_growth_rate` is still player-set and is what actually grows the population, while `projected_population_growth_rate` remains a separate, non-driving forecast calculated with the same kind of formula.

**Calculation is live, not just an annual side effect.** `calculate_economic_growth_rate()`/`calculate_projected_population_growth_rate()` are pure functions of the node's *current* state — nothing about them is tied to `advance_year()`. `main.py`'s `format_node()` (used by `view`) calls them directly every time, so the displayed rate always reflects whatever the node's population/economic output/buildings/extraction sites currently are, even if you've never called `advance-year` or just changed something with `seteconomy`/`build`/`build-extraction`. The stored `economic_growth_rate` field still exists and is still what `advance_year()` uses to actually grow `economic_output` (see below) — but nothing reads that stored field for *display* purposes anymore; display always recalculates.

**`Node.advance_year()`** is the per-tile simulation step:
```python
economic_growth_rate = calculate_economic_growth_rate()      # computed fresh from *current* GDP/capita, buildings, extraction sites
economic_output = max(0.0, economic_output * (1 + economic_growth_rate))
population = max(0, round(population * (1 + population_growth_rate)))
update_projected_population_growth_rate()                    # recalculated using the just-updated population/economic_output
```
Growth compounds once per call; it does not know about the calendar, only about how many times it's been invoked. `economic_growth_rate` is computed and applied within the same step, so after `advance_year()` returns, the stored field reflects the rate that was *just used* to grow this year's `economic_output` — a historical record now, since (per above) `view` doesn't actually read it.

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
| `reserve_divisions` | `list[Division]` | Divisions belonging to this country that aren't assigned to any node |

Note there's no stored `economic_growth_rate`/`projected_population_growth_rate` field on `Country` — those were removed once every place that displayed them (`projections`, `view-country`) switched to computing live instead (see below). `economic_output`/`population` remain genuinely cached, though, since summing every node is comparatively cheap either way and several commands (`world status`, `country-status`) are fine reading a periodically-refreshed value rather than needing it live on every keystroke.

**Why `economic_output`/`population` are cached snapshots, not live properties**: `Country` cannot compute these on its own because it only holds node *IDs* — it needs the actual node data, which only `World` has. So `Country` exposes pure calculation methods that take the node lookup as a parameter:

```python
country.calculate_economic_output(all_nodes: dict[str, Node]) -> float   # sum of owned nodes' economic_output
country.calculate_population(all_nodes: dict[str, Node]) -> int          # sum of owned nodes' population
country.calculate_economic_growth_rate(all_nodes) -> float               # GDP-weighted average of owned nodes' (live) economic_growth_rate
country.calculate_projected_population_growth_rate(all_nodes) -> float   # population-weighted average of owned nodes' (live) projected population growth
```

The two rate-calculation methods still exist and are still used — just never cached. `Country.calculate_economic_growth_rate()`/`calculate_projected_population_growth_rate()` call each owned node's own `calculate_*` method directly (not a stored field), so the whole chain from `projections`/`view-country` down to each node is live end to end. Both are weighted (by each node's economic output / population respectively) rather than a plain average, so a country's aggregate growth is actually dominated by its biggest/wealthiest nodes rather than treating a tiny outpost the same as its capital. If the country owns no nodes (or they sum to zero), the calculation returns `0.0` rather than dividing by zero.

`economic_output`/`population`, in contrast, still use the store-and-refresh pattern:

```python
country.update_economic_output(all_nodes)
country.update_population(all_nodes)
```

`main.py`'s `refresh_country_stats(world)` calls both for every country; both `advance_year()` (automatically, once per year, right after advancing every node) and the standalone `forceupdate` command call `refresh_country_stats()`. **This means a country's displayed GDP/population (`world status`, `country-status`) only reflects reality as of the last `advance-year` or `forceupdate` call** — if you `seteconomy` a node or reassign it to a different country, you'll see stale GDP/population until one of those two runs. Growth rates (`projections`, `view-country`) don't have this problem since they're always computed fresh.

## `World` ([main.py](main.py))

The in-memory container for one game session:

```python
class World:
    nodes: dict[str, Node]        # keyed by Node.id
    countries: dict[str, Country] # keyed by Country.name
    year: int                     # defaults to 0, or a value passed to `new-world <name> [start_year]`; incremented by advance_year()
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

`world status` and `world divisions` are the two multi-word commands: both dispatched by matching `command == "world"` and then checking `args[0]`, rather than being registered as their own tokens.

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
      "resources": [true, false, false],        // positional, one per ResourceType
      "extraction_sites": [false, false, true],  // positional, one per ExtractionSiteType
      "economic_output": 550.0, "economic_growth_rate": 0.038,
      "population": 10500, "population_growth_rate": 0.05,
      "projected_population_growth_rate": 0.025,
      "military_deployments": [
        {
          "country": "...",
          "divisions": [
            {"id": "Fedran Republic_div_1", "name": "1st Infantry", "division_type": "INFANTRY",
             "manpower": 5000, "supply_requirement": 12.5, "morale": 100.0, "location": "<node_id>"},
            {"id": "Fedran Republic_div_2", "name": "1st Air Wing", "division_type": "AIR_FORCE",
             "manpower": 400, "supply_requirement": 18.0, "morale": 100.0, "location": "<node_id>",
             "aircraft_type": "F-16", "equipment_rating": 8.5, "aircraft_count": 24, "range": 1200.0}
          ]
        }
      ]
    }
  },
  "countries": {
    "<country_name>": {
      "name": "...", "nodes": ["<node_id>", ...], "government_type": "MONARCHY",
      "treasury": 0.0, "stability": 50.0, "economic_output": 550.0, "population": 10500,
      "reserve_divisions": [ /* same division shape as above, "location": null */ ]
    }
  }
}
```

Enums are stored by member **name** (`"HILLS"`, not `1`), so the encoding is stable across reordering `auto()` values in the enum definitions — but renaming an enum member breaks old save files (there's no migration layer). A division's extra `aircraft_*`/`range` keys are only present when `division_type` is `"AIR_FORCE"`; `_division_from_dict` branches on that field to decide whether to construct a plain `Division` or an `AirForceDivision`.

`database.py` has no knowledge of `World` — its functions are written against duck-typed objects with `.nodes`, `.countries`, `.year` attributes (matching dicts of the right shapes), specifically so it doesn't need to import `main.py` and create a cycle:

- `save_world(world, path)` — serializes and writes, always overwriting whatever is at `path`.
- `load_into_world(world, path)` — reads JSON, then `.clear()`s and repopulates `world.nodes` / `world.countries` / `world.year` **in place**, then reseeds the per-country division ID counters (see "Division IDs and names" above). It mutates the object you pass in rather than returning a new one, which is why `cmd_open` can call it on the live `World` and have the running session immediately reflect the loaded file.

Deserialization is intentionally strict for fields that predate save-format evolution: it indexes required dict keys directly (`data["id"]`, etc.) rather than using `.get()` with defaults, so a hand-edited or badly corrupted save file fails loudly with a `KeyError` (caught and reported by `cmd_open`) instead of silently producing a half-populated `Node`. Fields added *after* the format already existed (`name`, `resources`, `extraction_sites`, `projected_population_growth_rate`, `reserve_divisions`) use `.get()` with sensible defaults instead, specifically so older save files without them still load cleanly — a save from before `ResourceType`/`ExtractionSiteType` existed loads with every resource/extraction-site flag defaulted to `False`. `Country`'s old, now-removed `economic_growth_rate`/`projected_economic_growth_rate`/`projected_population_growth_rate` keys, if present in an older save, are simply ignored on load rather than causing an error — `Country` no longer has fields for them.

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

- **No migrations for structural changes**: adding/removing/reordering `Enum` members (`BuildingType`/`ResourceType`/`ExtractionSiteType` especially, due to their positional boolean arrays) will break existing save files with no warning beyond a `KeyError`/`ValueError` at load time. Purely additive dataclass fields are handled gracefully (see "Deserialization" above), but anything that changes the *meaning* of existing data is not.
- **Country GDP/population are eventually-consistent, not live**: see the `Country` snapshot explanation above. `world status` / `country-status` can lie until the next `advance-year` or `forceupdate`. Growth rates (`projections`, `view-country`) don't have this problem — they're always computed live.
- **No validation that a country's `nodes` list matches reality**: it's hand-maintained by `cmd_setcountry`; direct field mutation elsewhere would desync it from `Node.country`.
- **`Division.supply_requirement` and `Country.treasury`/`stability` are inert**: modeled and persisted, but no game logic currently reads or changes them based on gameplay (only player commands set them directly).
- **Resources aren't tied to terrain or generated procedurally**: `addresource`/`removeresource` set them directly; a `DESERT` node can have `OIL_GAS` if you say so. There's no simulation linking resource placement to terrain type.
- **No combat**: divisions (including air force ones) can occupy the same node from opposing countries with nothing resolving the conflict.
- **Single global `World`**: the terminal only ever manages one game at a time in memory; `new-world`/`open` overwrite it rather than switching between multiple loaded worlds.
- **Division names are only unique per-country, not globally**: this is intentional (two countries can each field a "1st Infantry"), but means a division "name" alone is never a safe global key outside the context of a known country.
