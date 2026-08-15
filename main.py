from __future__ import annotations

import json
import re
import shlex
import webbrowser
from pathlib import Path

try:
    import readline
except ImportError:
    readline = None  # not available on this platform; tab-completion is simply skipped

import database
from country import Country, GovernmentType
from division import AirForceDivision, Division, DivisionType
from node import (
    EXTRACTION_SITE_RESOURCE_REQUIREMENTS,
    BuildingType,
    ExtractionSiteType,
    MilitaryDeployment,
    Node,
    ResourceType,
    Terrain,
)

HELP_TEXT = "See README.md for the full list of commands."

COMMAND_NAMES = sorted(
    [
        "quit",
        "exit",
        "help",
        "list",
        "map",
        "create",
        "view",
        "connect",
        "disconnect",
        "setcountry",
        "setterrain",
        "setpopulation",
        "setpopgrowth",
        "seteconomy",
        "build",
        "unbuild",
        "addresource",
        "removeresource",
        "build-extraction",
        "unbuild-extraction",
        "deploy",
        "create-division",
        "create-airforce-division",
        "deploy-airforce",
        "deploy-reserve",
        "move-division",
        "buildings",
        "resources",
        "extraction-sites",
        "terrains",
        "division-types",
        "create-country",
        "view-country",
        "list-countries",
        "setgovernment",
        "governments",
        "advance-year",
        "set-year",
        "year",
        "forceupdate",
        "world",
        "projections",
        "country-divisions",
        "country-status",
        "open",
        "save",
        "new-world",
        "rename-world",
        "list-worlds",
    ]
)

# For each command, what kind of value is expected at each argument position (0-indexed,
# after the command itself). "node" / "country" / "world_name" are resolved dynamically
# against the live World (or saved worlds on disk); a list is a fixed vocabulary; missing
# positions (including free-text ones like names/numbers) get no suggestions.
ARG_COMPLETIONS: dict[str, list[str | list[str]]] = {
    "view": ["node"],
    "connect": ["node", "node"],
    "disconnect": ["node", "node"],
    "setcountry": ["node", "country"],
    "setterrain": ["node", [t.name.lower() for t in Terrain]],
    "setpopulation": ["node"],
    "setpopgrowth": ["node"],
    "seteconomy": ["node"],
    "build": ["node", [b.name.lower() for b in BuildingType]],
    "unbuild": ["node", [b.name.lower() for b in BuildingType]],
    "addresource": ["node", [r.name.lower() for r in ResourceType]],
    "removeresource": ["node", [r.name.lower() for r in ResourceType]],
    "build-extraction": ["node", [s.name.lower() for s in ExtractionSiteType]],
    "unbuild-extraction": ["node", [s.name.lower() for s in ExtractionSiteType]],
    "deploy": ["node", "country", [], [d.name.lower() for d in DivisionType]],
    "create-division": ["country", [], [d.name.lower() for d in DivisionType]],
    "create-airforce-division": ["country"],
    "deploy-airforce": ["node", "country"],
    "deploy-reserve": ["country", [], "node"],
    "move-division": ["country", [], "node"],
    "create-country": [[], [g.name.lower() for g in GovernmentType]],
    "view-country": ["country"],
    "setgovernment": ["country", [g.name.lower() for g in GovernmentType]],
    "country-divisions": ["country"],
    "country-status": ["country"],
    "world": [["status", "divisions"]],
    "open": ["world_name"],
    "save": ["world_name"],
    "rename-world": ["world_name"],
}


DEFAULT_GRID_SIZE = 10


class World:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.countries: dict[str, Country] = {}
        self.year: int = 0
        self.start_year: int = 0
        self.save_path: str | None = None
        self.width: int = DEFAULT_GRID_SIZE
        self.height: int = DEFAULT_GRID_SIZE

    def add_node(self, node: Node) -> None:
        self.nodes[node.id] = node

    def get_node(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)

    def get_node_at(self, x: int, y: int) -> Node | None:
        for node in self.nodes.values():
            if node.x == x and node.y == y:
                return node
        return None

    def add_country(self, country: Country) -> None:
        self.countries[country.name] = country

    def get_country(self, name: str) -> Country | None:
        return self.countries.get(name)


def format_division_summary(division: Division) -> str:
    return (
        f"{division.get_name()} [{division.get_id()}]: {division.get_division_type().name}, "
        f"{division.get_manpower()} men, supply {division.get_supply_requirement()}, "
        f"morale {division.get_morale()}"
    )


def format_division_extra(division: Division) -> str | None:
    if isinstance(division, AirForceDivision):
        return (
            f"{division.get_aircraft_count()}x {division.get_aircraft_type()}, "
            f"equipment rating {division.get_equipment_rating()}, range {division.get_range()}"
        )
    return None


def format_node(node: Node) -> str:
    lines = [
        f"Node: {node.get_id()}",
        f"  Position: ({node.get_x()}, {node.get_y()})",
        f"  Country: {node.get_country() or 'unclaimed'}",
        f"  Terrain: {node.get_terrain().name}",
        f"  Connected tiles: {', '.join(node.get_connected_tiles()) or 'none'}",
        f"  Buildings: {', '.join(b.name for b in node.get_available_buildings()) or 'none'}",
        f"  Resources: {', '.join(r.name for r in node.get_available_resources()) or 'none'}",
        f"  Extraction sites: {', '.join(s.name for s in node.get_available_extraction_sites()) or 'none'}",
        f"  Economic output: {node.get_economic_output()} (growth {node.calculate_economic_growth_rate():+.2%})",
        f"  Population: {node.get_population()} (growth {node.get_population_growth_rate():+.2%}, "
        f"projected {node.calculate_projected_population_growth_rate():+.2%})",
        "  Military deployments:",
    ]
    deployments = node.get_military_deployments()
    if not deployments:
        lines.append("    none")
    else:
        for dep in deployments:
            lines.append(f"    {dep.country}: {dep.get_strength()} men across {len(dep.get_divisions())} division(s)")
            for division in dep.get_divisions():
                lines.append(f"      {format_division_summary(division)}")
                extra = format_division_extra(division)
                if extra:
                    lines.append(f"        {extra}")
    return "\n".join(lines)


def get_country_divisions(all_nodes: dict[str, Node], country_name: str) -> list[Division]:
    divisions: list[Division] = []
    for node in all_nodes.values():
        for deployment in node.get_deployments_by_country(country_name):
            divisions.extend(deployment.get_divisions())
    return divisions


def format_country(country: Country, all_nodes: dict[str, Node]) -> str:
    reserve_count = len(country.get_reserve_divisions())
    total_divisions = len(get_country_divisions(all_nodes, country.get_name())) + reserve_count
    lines = [
        f"Country: {country.get_name()}",
        f"  Government: {country.get_government_type().name}",
        f"  Stability: {country.get_stability()}",
        f"  GDP: {country.calculate_economic_output(all_nodes):.2f} "
        f"(growth {country.calculate_economic_growth_rate(all_nodes):+.2%})",
        f"  Population: {country.calculate_population(all_nodes)} "
        f"(projected growth {country.calculate_projected_population_growth_rate(all_nodes):+.2%})",
        f"  Nodes ({country.get_node_count()}): {', '.join(country.get_nodes()) or 'none'}",
        f"  Divisions: {total_divisions} ({reserve_count} reserve)",
    ]
    return "\n".join(lines)


def format_division_line(division: Division) -> str:
    location = division.get_location() or "reserve"
    line = f"  {format_division_summary(division)}, location {location}"
    extra = format_division_extra(division)
    if extra:
        line += f"\n      {extra}"
    return line


def format_country_status(country: Country) -> str:
    lines = [
        f"Country: {country.get_name()}",
        f"  GDP: {country.get_economic_output():.2f}",
        f"  Population: {country.get_population()}",
        f"  Government: {country.get_government_type().name}",
        f"  Treasury: {country.get_treasury()}",
        f"  Stability: {country.get_stability()}",
        f"  Reserve divisions: {len(country.get_reserve_divisions())}",
    ]
    return "\n".join(lines)


def connect_nodes(n1: Node, n2: Node) -> None:
    if n2.id not in n1.connected_tiles:
        n1.connected_tiles.append(n2.id)
    if n1.id not in n2.connected_tiles:
        n2.connected_tiles.append(n1.id)


def auto_connect_grid_neighbors(world: World, node: Node) -> None:
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        neighbor = world.get_node_at(node.x + dx, node.y + dy)
        if neighbor is not None:
            connect_nodes(node, neighbor)


def cmd_create(world: World, args: list[str]) -> None:
    if len(args) != 3:
        print("Usage: create <id> <x> <y>")
        return
    node_id, x_str, y_str = args
    if node_id in world.nodes:
        print(f"Node '{node_id}' already exists.")
        return
    try:
        x = int(x_str)
        y = int(y_str)
    except ValueError:
        print("x and y must be integers.")
        return
    if not (0 <= x < world.width and 0 <= y < world.height):
        print(f"Position ({x}, {y}) is outside the grid (0-{world.width - 1}, 0-{world.height - 1}).")
        return
    occupant = world.get_node_at(x, y)
    if occupant is not None:
        print(f"Position ({x}, {y}) is already occupied by '{occupant.id}'.")
        return
    node = Node(id=node_id, x=x, y=y)
    world.add_node(node)
    auto_connect_grid_neighbors(world, node)
    print(f"Created node '{node_id}' at ({x}, {y}).")


MAP_TILE_COLORS = [
    "#e74c3c",  # red
    "#2ecc71",  # green
    "#f1c40f",  # yellow
    "#3498db",  # blue
    "#9b59b6",  # magenta
    "#1abc9c",  # teal
    "#ff8a80",  # bright red
    "#8affab",  # bright green
    "#fff176",  # bright yellow
    "#82b1ff",  # bright blue
    "#ea80fc",  # bright magenta
    "#84ffff",  # bright teal
]
MAP_UNCLAIMED_COLOR = "#5a5a63"
MAP_EMPTY_COLOR = "#232326"


def assign_country_colors(world: World) -> dict[str, str]:
    return {name: MAP_TILE_COLORS[i % len(MAP_TILE_COLORS)] for i, name in enumerate(world.countries)}


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _format_compact_number(n: float) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:.0f}"

# Plain (non-f) templates for the CSS/JS blocks, so their braces never collide with Python's
# f-string interpolation - dynamic bits are filled in with simple __TOKEN__ substitution instead.
MAP_CSS_TEMPLATE = """
  .map-page {
    position: relative;
    background: #1c1c1f;
    color: #eee;
    font-family: system-ui, sans-serif;
    padding: 24px;
    display: flex;
    flex-direction: column;
    align-items: center;
  }
  h1 { font-size: 18px; font-weight: 600; margin: 0 0 16px; }
  h2 { font-size: 13px; font-weight: 600; margin: 0 0 12px; color: #999; text-transform: uppercase; letter-spacing: 0.04em; }
  .year-badge {
    position: absolute;
    top: 24px;
    right: 24px;
    background: #26262a;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 13px;
    font-weight: 600;
  }
  .layout { display: flex; gap: 24px; align-items: flex-start; }
  .map {
    display: flex;
    flex-direction: column;
    align-items: center;
    background: #1c1c1f;
    padding: 1px;
    flex-shrink: 0;
  }
  .map-row { display: flex; gap: 1px; margin-bottom: 1px; }
  .map-row:last-child { margin-bottom: 0; }
  .tile { flex-shrink: 0; }
  .tile:not(.empty):hover { outline: 2px solid #fff; outline-offset: -2px; cursor: default; }
  .tile.empty { background: __EMPTY_COLOR__; }
  .legend { margin-top: 20px; display: flex; flex-wrap: wrap; gap: 12px 20px; font-size: 13px; }
  .legend-item { display: flex; align-items: center; gap: 6px; }
  .swatch { width: 12px; height: 12px; border-radius: 2px; display: inline-block; flex-shrink: 0; }
  .panel { width: 260px; flex-shrink: 0; background: #26262a; border-radius: 8px; padding: 16px; min-height: 120px; }
  .stat { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; padding: 4px 0; border-bottom: 1px solid #35353a; }
  .stat:last-child { border-bottom: none; }
  .stat span:first-child { color: #999; }
  .country-row { display: flex; align-items: center; gap: 8px; font-size: 13px; padding: 6px 0; border-bottom: 1px solid #35353a; }
  .country-row:last-child { border-bottom: none; }
  .country-name { flex: 1; }
  .country-stats { color: #999; font-size: 12px; white-space: nowrap; }
"""

MAP_JS = """
(function () {
  var panel = document.getElementById('panel');
  var defaultHTML = panel.innerHTML;
  var mapEl = document.querySelector('.map');

  function renderTile(tile) {
    panel.innerHTML =
      '<h2>' + tile.dataset.id + '</h2>' +
      '<div class="stat"><span>Position</span><span>(' + tile.dataset.x + ', ' + tile.dataset.y + ')</span></div>' +
      '<div class="stat"><span>Country</span><span>' + tile.dataset.country + '</span></div>' +
      '<div class="stat"><span>Terrain</span><span>' + tile.dataset.terrain + '</span></div>' +
      '<div class="stat"><span>Population</span><span>' + tile.dataset.population + '</span></div>' +
      '<div class="stat"><span>Economic output</span><span>' + tile.dataset.economic + '</span></div>';
  }

  mapEl.addEventListener('mouseover', function (e) {
    var tile = e.target.closest('.tile');
    if (!tile || !tile.dataset.id) return;
    renderTile(tile);
  });

  mapEl.addEventListener('mouseleave', function () {
    panel.innerHTML = defaultHTML;
  });
})();
"""


def build_map_html(world: World) -> str:
    save_name = Path(world.save_path).stem if world.save_path else "unsaved world"
    country_colors = assign_country_colors(world)
    grid: dict[tuple[int, int], Node] = {(n.x, n.y): n for n in world.nodes.values()}
    tile_size = max(6, min(40, 2000 // max(world.width, world.height)))

    def tile_html(x: int, y: int) -> str:
        node = grid.get((x, y))
        if node is None:
            return f'<div class="tile empty" style="width:{tile_size}px;height:{tile_size}px"></div>'
        color = country_colors[node.country] if node.country else MAP_UNCLAIMED_COLOR
        return (
            f'<div class="tile" style="width:{tile_size}px;height:{tile_size}px;background:{color}" '
            f'data-id="{_escape_html(node.id)}" data-x="{node.x}" data-y="{node.y}" '
            f'data-country="{_escape_html(node.country or "unclaimed")}" '
            f'data-terrain="{node.terrain.name}" '
            f'data-population="{node.population:,}" '
            f'data-economic="{node.economic_output:,.0f}"></div>'
        )

    rows = []
    for y in range(world.height):
        tiles = [tile_html(x, y) for x in range(world.width)]
        rows.append(f'<div class="map-row">{"".join(tiles)}</div>')

    legend_items = [
        f'<div class="legend-item"><span class="swatch" style="background:{color}"></span>{_escape_html(name)}</div>'
        for name, color in country_colors.items()
    ]
    legend_items.append(
        f'<div class="legend-item"><span class="swatch" style="background:{MAP_UNCLAIMED_COLOR}"></span>unclaimed</div>'
    )

    country_rows = [
        f'<div class="country-row">'
        f'<span class="swatch" style="background:{country_colors[name]}"></span>'
        f'<span class="country-name">{_escape_html(name)}</span>'
        f'<span class="country-stats">GDP {_format_compact_number(country.calculate_economic_output(world.nodes))} '
        f"&middot; Pop {_format_compact_number(country.calculate_population(world.nodes))}</span>"
        f"</div>"
        for name, country in world.countries.items()
    ]
    default_panel_body = "".join(country_rows) if country_rows else "<p>No countries yet.</p>"

    css = MAP_CSS_TEMPLATE.replace("__EMPTY_COLOR__", MAP_EMPTY_COLOR)

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{_escape_html(save_name)}</title>
<style>{css}</style>
</head>
<body class="map-page">
  <h1>{_escape_html(save_name)}</h1>
  <div class="year-badge">Year {world.year}</div>
  <div class="layout">
    <div class="map">
      {"".join(rows)}
    </div>
    <div class="panel" id="panel">
      <h2>Countries</h2>
      {default_panel_body}
    </div>
  </div>
  <div class="legend">
    {"".join(legend_items)}
  </div>
  <script>{MAP_JS}</script>
</body>
</html>
"""


def cmd_map(world: World) -> None:
    if not world.nodes:
        print("No nodes yet.")
        return
    html = build_map_html(world)
    path = database.ensure_save_dir() / "map.html"
    path.write_text(html)
    webbrowser.open(path.as_uri())
    print(f"Map written to '{path}' and opened in your browser.")


def cmd_view(world: World, args: list[str]) -> None:
    if len(args) != 1:
        print("Usage: view <id>")
        return
    node = world.get_node(args[0])
    if node is None:
        print(f"No such node '{args[0]}'.")
        return
    print(format_node(node))


def cmd_connect(world: World, args: list[str]) -> None:
    if len(args) != 2:
        print("Usage: connect <id1> <id2>")
        return
    id1, id2 = args
    n1, n2 = world.get_node(id1), world.get_node(id2)
    if n1 is None or n2 is None:
        print("Both nodes must exist.")
        return
    connect_nodes(n1, n2)
    print(f"Connected '{id1}' <-> '{id2}'.")


def cmd_disconnect(world: World, args: list[str]) -> None:
    if len(args) != 2:
        print("Usage: disconnect <id1> <id2>")
        return
    id1, id2 = args
    n1, n2 = world.get_node(id1), world.get_node(id2)
    if n1 is None or n2 is None:
        print("Both nodes must exist.")
        return
    if id2 in n1.connected_tiles:
        n1.connected_tiles.remove(id2)
    if id1 in n2.connected_tiles:
        n2.connected_tiles.remove(id1)
    print(f"Disconnected '{id1}' <-> '{id2}'.")


def cmd_setcountry(world: World, args: list[str]) -> None:
    if len(args) != 2:
        print("Usage: setcountry <id> <country>")
        return
    node_id, country_name = args
    node = world.get_node(node_id)
    if node is None:
        print(f"No such node '{node_id}'.")
        return
    country = world.get_country(country_name)
    if country is None:
        print(f"No such country '{country_name}'. Use 'create-country' first.")
        return

    old_country = world.get_country(node.country) if node.country else None
    if old_country is not None and node_id in old_country.nodes:
        old_country.nodes.remove(node_id)

    node.country = country_name
    if node_id not in country.nodes:
        country.nodes.append(node_id)
    print(f"Node '{node_id}' is now controlled by '{country_name}'.")


def cmd_create_country(world: World, args: list[str]) -> None:
    if len(args) not in (1, 2):
        print("Usage: create-country <name> [government]")
        return
    name = args[0]
    if name in world.countries:
        print(f"Country '{name}' already exists.")
        return
    government_type = GovernmentType.REPUBLIC
    if len(args) == 2:
        try:
            government_type = GovernmentType[args[1].upper()]
        except KeyError:
            print(f"Unknown government type '{args[1]}'. Use 'governments' to list options.")
            return
    world.add_country(Country(name=name, government_type=government_type))
    print(f"Created country '{name}' ({government_type.name}).")


def cmd_view_country(world: World, args: list[str]) -> None:
    if len(args) != 1:
        print("Usage: view-country <name>")
        return
    country = world.get_country(args[0])
    if country is None:
        print(f"No such country '{args[0]}'.")
        return
    print(format_country(country, world.nodes))


def cmd_world_status(world: World) -> None:
    if not world.countries:
        print("No countries yet.")
        return
    for country in world.countries.values():
        print(f"  {country.get_name()}: GDP {country.get_economic_output():.2f}, population {country.get_population()}")


def cmd_world_divisions(world: World) -> None:
    if not world.countries:
        print("No countries yet.")
        return
    for country in world.countries.values():
        divisions = get_country_divisions(world.nodes, country.get_name()) + country.get_reserve_divisions()
        print(f"{country.get_name()}:")
        if not divisions:
            print("  none")
            continue
        for division in divisions:
            print(format_division_line(division))


def cmd_projections(world: World) -> None:
    if not world.countries:
        print("No countries yet.")
        return
    for country in world.countries.values():
        print(
            f"  {country.get_name()}: economic growth "
            f"{country.calculate_economic_growth_rate(world.nodes):+.2%}, "
            f"projected population growth {country.calculate_projected_population_growth_rate(world.nodes):+.2%}"
        )


def cmd_country_divisions(world: World, args: list[str]) -> None:
    if len(args) != 1:
        print("Usage: country-divisions <country>")
        return
    country_name = args[0]
    country = world.get_country(country_name)
    if country is None:
        print(f"No such country '{country_name}'.")
        return
    divisions = get_country_divisions(world.nodes, country_name) + country.get_reserve_divisions()
    if not divisions:
        print(f"'{country_name}' has no divisions.")
        return
    for division in divisions:
        print(format_division_line(division))


def cmd_country_status(world: World, args: list[str]) -> None:
    if len(args) != 1:
        print("Usage: country-status <country>")
        return
    country = world.get_country(args[0])
    if country is None:
        print(f"No such country '{args[0]}'.")
        return
    print(format_country_status(country))


def cmd_setgovernment(world: World, args: list[str]) -> None:
    if len(args) != 2:
        print("Usage: setgovernment <country> <government>")
        return
    country_name, government_name = args
    country = world.get_country(country_name)
    if country is None:
        print(f"No such country '{country_name}'.")
        return
    try:
        government_type = GovernmentType[government_name.upper()]
    except KeyError:
        print(f"Unknown government type '{government_name}'. Use 'governments' to list options.")
        return
    country.government_type = government_type
    print(f"'{country_name}' government set to {government_type.name}.")


def cmd_setpopulation(world: World, args: list[str]) -> None:
    if len(args) != 2:
        print("Usage: setpopulation <id> <population>")
        return
    node_id, population_str = args
    node = world.get_node(node_id)
    if node is None:
        print(f"No such node '{node_id}'.")
        return
    try:
        population = int(population_str)
    except ValueError:
        print("Population must be an integer.")
        return
    node.population = population
    print(f"Node '{node_id}' population set to {population}.")


def cmd_setpopgrowth(world: World, args: list[str]) -> None:
    if len(args) != 2:
        print("Usage: setpopgrowth <id> <rate>")
        return
    node_id, rate_str = args
    node = world.get_node(node_id)
    if node is None:
        print(f"No such node '{node_id}'.")
        return
    try:
        rate = float(rate_str)
    except ValueError:
        print("Growth rate must be a number (e.g. 0.05 for 5%).")
        return
    node.population_growth_rate = rate
    print(f"Node '{node_id}' population growth rate set to {rate:+.2%}.")


def cmd_seteconomy(world: World, args: list[str]) -> None:
    if len(args) != 2:
        print("Usage: seteconomy <id> <output>")
        return
    node_id, output_str = args
    node = world.get_node(node_id)
    if node is None:
        print(f"No such node '{node_id}'.")
        return
    try:
        output = float(output_str)
    except ValueError:
        print("Economic output must be a number.")
        return
    node.economic_output = output
    print(f"Node '{node_id}' economic output set to {output}.")


def cmd_setterrain(world: World, args: list[str]) -> None:
    if len(args) != 2:
        print("Usage: setterrain <id> <terrain>")
        return
    node_id, terrain_name = args
    node = world.get_node(node_id)
    if node is None:
        print(f"No such node '{node_id}'.")
        return
    try:
        terrain = Terrain[terrain_name.upper()]
    except KeyError:
        print(f"Unknown terrain '{terrain_name}'. Use 'terrains' to list options.")
        return
    node.terrain = terrain
    print(f"Node '{node_id}' terrain set to {terrain.name}.")


def cmd_build(world: World, args: list[str], enable: bool) -> None:
    verb = "build" if enable else "unbuild"
    if len(args) != 2:
        print(f"Usage: {verb} <id> <building>")
        return
    node_id, building_name = args
    node = world.get_node(node_id)
    if node is None:
        print(f"No such node '{node_id}'.")
        return
    try:
        building = BuildingType[building_name.upper()]
    except KeyError:
        print(f"Unknown building '{building_name}'. Use 'buildings' to list options.")
        return
    node.building_options[building.value - 1] = enable
    print(f"{building.name} {'enabled' if enable else 'disabled'} at '{node_id}'.")


def cmd_resource(world: World, args: list[str], enable: bool) -> None:
    verb = "addresource" if enable else "removeresource"
    if len(args) != 2:
        print(f"Usage: {verb} <id> <resource>")
        return
    node_id, resource_name = args
    node = world.get_node(node_id)
    if node is None:
        print(f"No such node '{node_id}'.")
        return
    try:
        resource = ResourceType[resource_name.upper()]
    except KeyError:
        print(f"Unknown resource '{resource_name}'. Use 'resources' to list options.")
        return
    node.resources[resource.value - 1] = enable
    print(f"{resource.name} {'added to' if enable else 'removed from'} '{node_id}'.")


def cmd_extraction(world: World, args: list[str], enable: bool) -> None:
    verb = "build-extraction" if enable else "unbuild-extraction"
    if len(args) != 2:
        print(f"Usage: {verb} <id> <site>")
        return
    node_id, site_name = args
    node = world.get_node(node_id)
    if node is None:
        print(f"No such node '{node_id}'.")
        return
    try:
        site = ExtractionSiteType[site_name.upper()]
    except KeyError:
        print(f"Unknown extraction site '{site_name}'. Use 'extraction-sites' to list options.")
        return
    if enable and not node.can_build_extraction_site(site):
        required = EXTRACTION_SITE_RESOURCE_REQUIREMENTS[site]
        print(f"'{node_id}' doesn't have {required.name}, so a {site.name} can't be built there.")
        return
    node.extraction_sites[site.value - 1] = enable
    print(f"{site.name} {'built at' if enable else 'removed from'} '{node_id}'.")


def find_division_by_name(world: World, country_name: str, name: str) -> Division | None:
    for division in get_country_divisions(world.nodes, country_name):
        if division.get_name() == name:
            return division
    country = world.get_country(country_name)
    if country is not None:
        found = country.find_reserve_division(name)
        if found is not None:
            return found
    return None


def cmd_deploy(world: World, args: list[str]) -> None:
    if len(args) != 6:
        print("Usage: deploy <id> <country> <name> <division_type> <manpower> <supply_requirement>")
        return
    node_id, country, name, division_type_str, manpower_str, supply_str = args
    node = world.get_node(node_id)
    if node is None:
        print(f"No such node '{node_id}'.")
        return
    if find_division_by_name(world, country, name) is not None:
        print(f"'{country}' already has a division named '{name}'.")
        return
    try:
        division_type = DivisionType[division_type_str.upper()]
    except KeyError:
        print(f"Unknown division type '{division_type_str}'. Use 'division-types' to list options.")
        return
    if division_type == DivisionType.AIR_FORCE:
        print("Use 'deploy-airforce' for AIR_FORCE divisions (they need aircraft details).")
        return
    try:
        manpower = int(manpower_str)
        supply_requirement = float(supply_str)
    except ValueError:
        print("Manpower must be an integer and supply requirement must be a number.")
        return
    deployment = next((d for d in node.military_deployments if d.country == country), None)
    if deployment is None:
        deployment = MilitaryDeployment(country=country)
        node.military_deployments.append(deployment)
    division = Division.create(
        country=country,
        name=name,
        division_type=division_type,
        manpower=manpower,
        supply_requirement=supply_requirement,
        location=node_id,
    )
    deployment.divisions.append(division)
    print(f"Deployed {division_type.name} division '{name}' ({manpower} men) for {country} at '{node_id}'.")


def cmd_create_division(world: World, args: list[str]) -> None:
    if len(args) != 5:
        print("Usage: create-division <country> <name> <division_type> <manpower> <supply_requirement>")
        return
    country_name, name, division_type_str, manpower_str, supply_str = args
    country = world.get_country(country_name)
    if country is None:
        print(f"No such country '{country_name}'.")
        return
    if find_division_by_name(world, country_name, name) is not None:
        print(f"'{country_name}' already has a division named '{name}'.")
        return
    try:
        division_type = DivisionType[division_type_str.upper()]
    except KeyError:
        print(f"Unknown division type '{division_type_str}'. Use 'division-types' to list options.")
        return
    if division_type == DivisionType.AIR_FORCE:
        print("Use 'create-airforce-division' for AIR_FORCE divisions (they need aircraft details).")
        return
    try:
        manpower = int(manpower_str)
        supply_requirement = float(supply_str)
    except ValueError:
        print("Manpower must be an integer and supply requirement must be a number.")
        return
    division = Division.create(
        country=country_name,
        name=name,
        division_type=division_type,
        manpower=manpower,
        supply_requirement=supply_requirement,
        location=None,
    )
    country.reserve_divisions.append(division)
    print(f"Created {division_type.name} division '{name}' ({manpower} men) in reserve for {country_name}.")


def _parse_airforce_args(
    manpower_str: str, supply_str: str, rating_str: str, count_str: str, range_str: str
) -> tuple[int, float, float, int, float] | None:
    try:
        return (
            int(manpower_str),
            float(supply_str),
            float(rating_str),
            int(count_str),
            float(range_str),
        )
    except ValueError:
        return None


def cmd_create_airforce_division(world: World, args: list[str]) -> None:
    if len(args) != 8:
        print(
            "Usage: create-airforce-division <country> <name> <manpower> <supply_requirement> "
            "<aircraft_type> <equipment_rating> <aircraft_count> <range>"
        )
        return
    country_name, name, manpower_str, supply_str, aircraft_type, rating_str, count_str, range_str = args
    country = world.get_country(country_name)
    if country is None:
        print(f"No such country '{country_name}'.")
        return
    if find_division_by_name(world, country_name, name) is not None:
        print(f"'{country_name}' already has a division named '{name}'.")
        return
    parsed = _parse_airforce_args(manpower_str, supply_str, rating_str, count_str, range_str)
    if parsed is None:
        print("Manpower and aircraft count must be integers; supply, equipment rating, and range must be numbers.")
        return
    manpower, supply_requirement, equipment_rating, aircraft_count, aircraft_range = parsed
    division = AirForceDivision.create_air_force(
        country=country_name,
        name=name,
        manpower=manpower,
        supply_requirement=supply_requirement,
        aircraft_type=aircraft_type,
        equipment_rating=equipment_rating,
        aircraft_count=aircraft_count,
        range=aircraft_range,
        location=None,
    )
    country.reserve_divisions.append(division)
    print(f"Created AIR_FORCE division '{name}' ({aircraft_count}x {aircraft_type}) in reserve for {country_name}.")


def cmd_deploy_airforce(world: World, args: list[str]) -> None:
    if len(args) != 9:
        print(
            "Usage: deploy-airforce <id> <country> <name> <manpower> <supply_requirement> "
            "<aircraft_type> <equipment_rating> <aircraft_count> <range>"
        )
        return
    node_id, country_name, name, manpower_str, supply_str, aircraft_type, rating_str, count_str, range_str = args
    node = world.get_node(node_id)
    if node is None:
        print(f"No such node '{node_id}'.")
        return
    if find_division_by_name(world, country_name, name) is not None:
        print(f"'{country_name}' already has a division named '{name}'.")
        return
    parsed = _parse_airforce_args(manpower_str, supply_str, rating_str, count_str, range_str)
    if parsed is None:
        print("Manpower and aircraft count must be integers; supply, equipment rating, and range must be numbers.")
        return
    manpower, supply_requirement, equipment_rating, aircraft_count, aircraft_range = parsed
    deployment = next((d for d in node.military_deployments if d.country == country_name), None)
    if deployment is None:
        deployment = MilitaryDeployment(country=country_name)
        node.military_deployments.append(deployment)
    division = AirForceDivision.create_air_force(
        country=country_name,
        name=name,
        manpower=manpower,
        supply_requirement=supply_requirement,
        aircraft_type=aircraft_type,
        equipment_rating=equipment_rating,
        aircraft_count=aircraft_count,
        range=aircraft_range,
        location=node_id,
    )
    deployment.divisions.append(division)
    print(f"Deployed AIR_FORCE division '{name}' ({aircraft_count}x {aircraft_type}) for {country_name} at '{node_id}'.")


def cmd_deploy_reserve(world: World, args: list[str]) -> None:
    if len(args) != 3:
        print("Usage: deploy-reserve <country> <name> <node_id>")
        return
    country_name, name, node_id = args
    country = world.get_country(country_name)
    if country is None:
        print(f"No such country '{country_name}'.")
        return
    node = world.get_node(node_id)
    if node is None:
        print(f"No such node '{node_id}'.")
        return
    division = country.remove_reserve_division(name)
    if division is None:
        print(f"'{country_name}' has no reserve division named '{name}'.")
        return
    division.location = node_id
    deployment = next((d for d in node.military_deployments if d.country == country_name), None)
    if deployment is None:
        deployment = MilitaryDeployment(country=country_name)
        node.military_deployments.append(deployment)
    deployment.divisions.append(division)
    print(f"Deployed reserve division '{name}' for {country_name} to '{node_id}'.")


def cmd_move_division(world: World, args: list[str]) -> None:
    if len(args) != 3:
        print("Usage: move-division <country> <name> <destination_id>")
        return
    country_name, name, destination_id = args
    if world.get_country(country_name) is None:
        print(f"No such country '{country_name}'.")
        return
    destination = world.get_node(destination_id)
    if destination is None:
        print(f"No such node '{destination_id}'.")
        return
    division = find_division_by_name(world, country_name, name)
    if division is None:
        print(f"'{country_name}' has no division named '{name}'.")
        return
    if division.location is None:
        print(f"'{name}' is in reserve, not deployed - use 'deploy-reserve' to send it somewhere first.")
        return
    if division.location == destination_id:
        print(f"'{name}' is already at '{destination_id}'.")
        return

    origin_id = division.location
    origin = world.get_node(origin_id)
    if origin is not None:
        deployment = next((d for d in origin.military_deployments if d.country == country_name), None)
        if deployment is not None and division in deployment.divisions:
            deployment.divisions.remove(division)
            if not deployment.divisions:
                origin.military_deployments.remove(deployment)

    new_deployment = next((d for d in destination.military_deployments if d.country == country_name), None)
    if new_deployment is None:
        new_deployment = MilitaryDeployment(country=country_name)
        destination.military_deployments.append(new_deployment)
    new_deployment.divisions.append(division)
    division.location = destination_id
    print(f"Moved '{name}' from '{origin_id}' to '{destination_id}'.")


def refresh_country_stats(world: World) -> None:
    for country in world.countries.values():
        country.update_economic_output(world.nodes)
        country.update_population(world.nodes)


def advance_year(world: World) -> None:
    world.year += 1
    for node in world.nodes.values():
        node.advance_year()
    refresh_country_stats(world)


def cmd_open(world: World, args: list[str]) -> None:
    if len(args) != 1:
        print("Usage: open <name or path>")
        return
    path = database.resolve_save_path(args[0])
    try:
        database.load_into_world(world, str(path))
    except FileNotFoundError:
        print(f"No such world '{args[0]}'.")
        return
    except (KeyError, ValueError) as e:
        print(f"Failed to load '{path}': {e}")
        return
    world.save_path = str(path)
    print(f"Loaded world from '{path}' ({len(world.nodes)} nodes, {len(world.countries)} countries).")


def cmd_save(world: World, args: list[str]) -> None:
    if len(args) > 1:
        print("Usage: save [name or path]")
        return
    if args:
        path = database.resolve_save_path(args[0])
    elif world.save_path is not None:
        path = Path(world.save_path)
    else:
        print("No file to save to yet. Use 'save <name>' to choose one.")
        return
    database.save_world(world, str(path))
    world.save_path = str(path)
    print(f"Saved world to '{path}'.")


def cmd_new_world(world: World, args: list[str]) -> None:
    if len(args) not in (3, 4):
        print("Usage: new-world <name> <width> <height> [start_year]")
        return
    name = args[0]
    try:
        width = int(args[1])
        height = int(args[2])
    except ValueError:
        print("Width and height must be integers.")
        return
    if width <= 0 or height <= 0:
        print("Width and height must be positive.")
        return
    start_year = 0
    if len(args) == 4:
        try:
            start_year = int(args[3])
        except ValueError:
            print("Start year must be an integer.")
            return
    path = database.resolve_save_path(name)
    if path.exists():
        print(f"A world named '{name}' already exists at '{path}'. Use 'open {name}' to load it.")
        return
    world.nodes.clear()
    world.countries.clear()
    world.width = width
    world.height = height
    world.year = start_year
    world.start_year = start_year
    world.save_path = str(path)
    database.save_world(world, str(path))
    print(f"Created new world '{name}' at '{path}' ({width}x{height} grid, starting year {start_year}).")


def cmd_rename_world(world: World, args: list[str]) -> None:
    if len(args) != 2:
        print("Usage: rename-world <old_name> <new_name>")
        return
    old_name, new_name = args
    old_path = database.resolve_save_path(old_name)
    try:
        new_path = database.rename_world(old_name, new_name)
    except FileNotFoundError:
        print(f"No such world '{old_name}'.")
        return
    except FileExistsError:
        print(f"A world named '{new_name}' already exists.")
        return
    if world.save_path == str(old_path):
        world.save_path = str(new_path)
    print(f"Renamed world '{old_name}' to '{new_name}'.")


def cmd_list_worlds() -> None:
    names = database.list_worlds()
    if not names:
        print(f"No saved worlds yet in '{database.DEFAULT_SAVE_DIR}'.")
        return
    for name in names:
        print(f"  {name}")


def run_command(world: World, raw: str) -> bool:
    """Execute one command line. Returns False if the game should exit."""
    try:
        parts = shlex.split(raw)
    except ValueError as e:
        print(f"Error parsing command: {e}")
        return True

    if not parts:
        return True

    command, *args = parts
    command = command.lower()

    if command in ("quit", "exit"):
        return False
    elif command == "help":
        print(HELP_TEXT)
    elif command == "list":
        if not world.nodes:
            print("No nodes yet.")
        for node in world.nodes.values():
            print(
                f"  {node.get_id()} ({node.get_x()}, {node.get_y()}) - "
                f"{node.get_country() or 'unclaimed'} ({node.get_terrain().name})"
            )
    elif command == "map":
        cmd_map(world)
    elif command == "create":
        cmd_create(world, args)
    elif command == "view":
        cmd_view(world, args)
    elif command == "connect":
        cmd_connect(world, args)
    elif command == "disconnect":
        cmd_disconnect(world, args)
    elif command == "setcountry":
        cmd_setcountry(world, args)
    elif command == "setterrain":
        cmd_setterrain(world, args)
    elif command == "setpopulation":
        cmd_setpopulation(world, args)
    elif command == "setpopgrowth":
        cmd_setpopgrowth(world, args)
    elif command == "seteconomy":
        cmd_seteconomy(world, args)
    elif command == "build":
        cmd_build(world, args, enable=True)
    elif command == "unbuild":
        cmd_build(world, args, enable=False)
    elif command == "addresource":
        cmd_resource(world, args, enable=True)
    elif command == "removeresource":
        cmd_resource(world, args, enable=False)
    elif command == "build-extraction":
        cmd_extraction(world, args, enable=True)
    elif command == "unbuild-extraction":
        cmd_extraction(world, args, enable=False)
    elif command == "deploy":
        cmd_deploy(world, args)
    elif command == "create-division":
        cmd_create_division(world, args)
    elif command == "create-airforce-division":
        cmd_create_airforce_division(world, args)
    elif command == "deploy-airforce":
        cmd_deploy_airforce(world, args)
    elif command == "deploy-reserve":
        cmd_deploy_reserve(world, args)
    elif command == "move-division":
        cmd_move_division(world, args)
    elif command == "buildings":
        print(", ".join(b.name for b in BuildingType))
    elif command == "resources":
        print(", ".join(r.name for r in ResourceType))
    elif command == "extraction-sites":
        print(", ".join(s.name for s in ExtractionSiteType))
    elif command == "terrains":
        print(", ".join(t.name for t in Terrain))
    elif command == "division-types":
        print(", ".join(d.name for d in DivisionType))
    elif command == "create-country":
        cmd_create_country(world, args)
    elif command == "view-country":
        cmd_view_country(world, args)
    elif command == "list-countries":
        if not world.countries:
            print("No countries yet.")
        for country in world.countries.values():
            print(f"  {country.get_name()} - {country.get_government_type().name} ({country.get_node_count()} nodes)")
    elif command == "setgovernment":
        cmd_setgovernment(world, args)
    elif command == "governments":
        print(", ".join(g.name for g in GovernmentType))
    elif command == "advance-year":
        advance_year(world)
        print(f"Year advanced to {world.year}.")
    elif command == "set-year":
        if len(args) != 1:
            print("Usage: set-year <year>")
        else:
            try:
                new_year = int(args[0])
            except ValueError:
                print("Year must be an integer.")
            else:
                world.year = new_year
                print(f"Year set to {world.year}.")
    elif command == "year":
        print(f"Year: {world.year} ({world.year - world.start_year} years since the file started)")
    elif command == "forceupdate":
        refresh_country_stats(world)
        print(f"Recalculated stats for {len(world.countries)} countries.")
    elif command == "world":
        if args and args[0] == "status":
            cmd_world_status(world)
        elif args and args[0] == "divisions":
            cmd_world_divisions(world)
        else:
            print("Usage: world status | world divisions")
    elif command == "projections":
        cmd_projections(world)
    elif command == "country-divisions":
        cmd_country_divisions(world, args)
    elif command == "country-status":
        cmd_country_status(world, args)
    elif command == "open":
        cmd_open(world, args)
    elif command == "save":
        cmd_save(world, args)
    elif command == "new-world":
        cmd_new_world(world, args)
    elif command == "rename-world":
        cmd_rename_world(world, args)
    elif command == "list-worlds":
        cmd_list_worlds()
    else:
        print(f"Unknown command '{command}'. Type 'help' for a list of commands.")

    return True


def _completion_options(world: World, before_text: str) -> list[str]:
    """The raw candidate pool for the word that comes right after `before_text` - shared by
    make_completer() (readline, for the local CLI) and get_completions() (for frontends that
    aren't readline-driven, like the web terminal), so both stay in sync with COMMAND_NAMES/
    ARG_COMPLETIONS automatically instead of maintaining two copies of this lookup."""
    try:
        typed = shlex.split(before_text)
    except ValueError:
        # an unterminated quote is being typed right now; best-effort fallback
        typed = before_text.replace('"', "").replace("'", "").split()

    if not typed:
        return COMMAND_NAMES
    command = typed[0].lower()
    arg_index = len(typed) - 1
    spec = ARG_COMPLETIONS.get(command)
    kind = spec[arg_index] if spec and arg_index < len(spec) else []
    if kind == "node":
        return list(world.nodes.keys())
    elif kind == "country":
        return list(world.countries.keys())
    elif kind == "world_name":
        return database.list_worlds()
    elif isinstance(kind, list):
        return kind
    return []


def get_completions(world: World, line: str, cursor_pos: int) -> list[str]:
    """Every completion candidate for the partial word ending at cursor_pos in `line`, sorted
    and deduplicated. Frontend-agnostic (no readline dependency) for use by, e.g., the web
    terminal, which drives this off an <input>'s value/selectionStart instead."""
    prefix_text = line[:cursor_pos]
    word_match = re.search(r"\S*$", prefix_text)
    partial = word_match.group(0) if word_match else ""
    before_text = prefix_text[: len(prefix_text) - len(partial)]
    partial = partial.lstrip("\"'")
    options = _completion_options(world, before_text)
    return sorted({o for o in options if o.lower().startswith(partial.lower())})


def get_map_info_panel_data(world: World) -> str:
    """A JSON-serializable summary for the web terminal's info panel: world-level stats plus
    one row per country (name, government, live GDP/population/growth, node and division
    counts). Returned as a JSON string, not a raw dict, so Pyodide callers get a plain JS
    object via JSON.parse instead of having to work with a PyProxy."""
    countries = []
    for country in world.countries.values():
        countries.append(
            {
                "name": country.name,
                "government": country.government_type.name,
                "gdp": country.calculate_economic_output(world.nodes),
                "population": country.calculate_population(world.nodes),
                "growth": country.calculate_economic_growth_rate(world.nodes),
                "popGrowth": country.calculate_projected_population_growth_rate(world.nodes),
                "nodes": len(country.nodes),
                "divisions": len(get_country_divisions(world.nodes, country.name)) + len(country.reserve_divisions),
            }
        )
    save_name = Path(world.save_path).stem if world.save_path else "unsaved world"
    return json.dumps(
        {
            "name": save_name,
            "year": world.year,
            "yearsElapsed": world.year - world.start_year,
            "width": world.width,
            "height": world.height,
            "nodeCount": len(world.nodes),
            "countryCount": len(world.countries),
            "countries": countries,
        }
    )


def get_tile_info(world: World, node_id: str) -> str:
    """A JSON-serializable full detail dump for one node - everything `view` would show, for
    the web terminal's hover panel. Returns JSON "null" if the node doesn't exist."""
    node = world.nodes.get(node_id)
    if node is None:
        return json.dumps(None)
    return json.dumps(
        {
            "id": node.id,
            "x": node.x,
            "y": node.y,
            "country": node.country,
            "terrain": node.terrain.name,
            "connectedTiles": node.connected_tiles,
            "buildings": [b.name for b in node.get_available_buildings()],
            "resources": [r.name for r in node.get_available_resources()],
            "extractionSites": [s.name for s in node.get_available_extraction_sites()],
            "economicOutput": node.economic_output,
            "economicGrowth": node.calculate_economic_growth_rate(),
            "population": node.population,
            "populationGrowth": node.population_growth_rate,
            "projectedPopulationGrowth": node.calculate_projected_population_growth_rate(),
            "deployments": [
                {
                    "country": dep.country,
                    "divisions": [
                        {
                            "name": d.name,
                            "type": d.division_type.name,
                            "manpower": d.manpower,
                            "morale": d.morale,
                        }
                        for d in dep.divisions
                    ],
                }
                for dep in node.military_deployments
            ],
        }
    )


def make_completer(world: World):
    def completer(text: str, state: int) -> str | None:
        buffer = readline.get_line_buffer()
        prefix_text = buffer[: readline.get_begidx()]
        options = _completion_options(world, prefix_text)
        matches = [o for o in options if o.lower().startswith(text.lower())]
        return matches[state] if state < len(matches) else None

    return completer


def setup_tab_completion(world: World) -> None:
    if readline is None:
        return
    readline.set_completer(make_completer(world))
    readline.set_completer_delims(readline.get_completer_delims().replace("-", ""))
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


def main() -> None:
    world = World()
    setup_tab_completion(world)
    print("=== nodetech terminal ===")
    print("Type 'help' for a list of commands.")

    while True:
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not run_command(world, raw):
            break

    print("Goodbye.")


if __name__ == "__main__":
    main()
