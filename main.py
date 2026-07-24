from __future__ import annotations

import shlex
from pathlib import Path

import database
from country import Country, GovernmentType
from division import AirForceDivision, Division, DivisionType
from node import BuildingType, MilitaryDeployment, Node, Terrain

HELP_TEXT = "See README.md for the full list of commands."


class World:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.countries: dict[str, Country] = {}
        self.year: int = 0
        self.save_path: str | None = None

    def add_node(self, node: Node) -> None:
        self.nodes[node.id] = node

    def get_node(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)

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
        f"  Country: {node.get_country() or 'unclaimed'}",
        f"  Terrain: {node.get_terrain().name}",
        f"  Connected tiles: {', '.join(node.get_connected_tiles()) or 'none'}",
        f"  Buildings: {', '.join(b.name for b in node.get_available_buildings()) or 'none'}",
        f"  Economic output: {node.get_economic_output()} (growth {node.get_economic_growth_rate():+.2%}, "
        f"projected {node.get_projected_economic_growth_rate():+.2%})",
        f"  Population: {node.get_population()} (growth {node.get_population_growth_rate():+.2%}, "
        f"projected {node.get_projected_population_growth_rate():+.2%})",
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


def format_country(country: Country) -> str:
    lines = [
        f"Country: {country.get_name()}",
        f"  Government: {country.get_government_type().name}",
        f"  Treasury: {country.get_treasury()}",
        f"  Stability: {country.get_stability()}",
        f"  Nodes ({country.get_node_count()}): {', '.join(country.get_nodes()) or 'none'}",
        f"  Reserve divisions ({len(country.get_reserve_divisions())}): "
        f"{', '.join(d.get_name() for d in country.get_reserve_divisions()) or 'none'}",
    ]
    return "\n".join(lines)


def get_country_divisions(world: World, country_name: str) -> list[Division]:
    divisions: list[Division] = []
    for node in world.nodes.values():
        for deployment in node.get_deployments_by_country(country_name):
            divisions.extend(deployment.get_divisions())
    return divisions


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


def cmd_create(world: World, args: list[str]) -> None:
    if len(args) != 1:
        print("Usage: create <id>")
        return
    node_id = args[0]
    if node_id in world.nodes:
        print(f"Node '{node_id}' already exists.")
        return
    world.add_node(Node(id=node_id))
    print(f"Created node '{node_id}'.")


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
    if id2 not in n1.connected_tiles:
        n1.connected_tiles.append(id2)
    if id1 not in n2.connected_tiles:
        n2.connected_tiles.append(id1)
    print(f"Connected '{id1}' <-> '{id2}'.")


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
    print(format_country(country))


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
        divisions = get_country_divisions(world, country.get_name()) + country.get_reserve_divisions()
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
            f"  {country.get_name()}: projected economic growth "
            f"{country.get_projected_economic_growth_rate():+.2%}, "
            f"projected population growth {country.get_projected_population_growth_rate():+.2%}"
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
    divisions = get_country_divisions(world, country_name) + country.get_reserve_divisions()
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


def cmd_seteconomygrowth(world: World, args: list[str]) -> None:
    if len(args) != 2:
        print("Usage: seteconomygrowth <id> <rate>")
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
    node.economic_growth_rate = rate
    print(f"Node '{node_id}' economic growth rate set to {rate:+.2%}.")


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


def find_division_by_name(world: World, country_name: str, name: str) -> Division | None:
    for division in get_country_divisions(world, country_name):
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


def refresh_country_stats(world: World) -> None:
    for country in world.countries.values():
        country.update_economic_output(world.nodes)
        country.update_population(world.nodes)
        country.update_projected_growth_rates(world.nodes)


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
    if len(args) != 1:
        print("Usage: new-world <name>")
        return
    name = args[0]
    path = database.resolve_save_path(name)
    if path.exists():
        print(f"A world named '{name}' already exists at '{path}'. Use 'open {name}' to load it.")
        return
    world.nodes.clear()
    world.countries.clear()
    world.year = 0
    world.save_path = str(path)
    database.save_world(world, str(path))
    print(f"Created new world '{name}' at '{path}'.")


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
            print(f"  {node.get_id()} - {node.get_country() or 'unclaimed'} ({node.get_terrain().name})")
    elif command == "create":
        cmd_create(world, args)
    elif command == "view":
        cmd_view(world, args)
    elif command == "connect":
        cmd_connect(world, args)
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
    elif command == "seteconomygrowth":
        cmd_seteconomygrowth(world, args)
    elif command == "build":
        cmd_build(world, args, enable=True)
    elif command == "unbuild":
        cmd_build(world, args, enable=False)
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
    elif command == "buildings":
        print(", ".join(b.name for b in BuildingType))
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


def main() -> None:
    world = World()
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
