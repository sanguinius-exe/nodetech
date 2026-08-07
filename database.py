from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from country import Country, GovernmentType
from division import AirForceDivision, Division, DivisionType, seed_division_id_counter
from node import BuildingType, ExtractionSiteType, MilitaryDeployment, Node, ResourceType, Terrain

DEFAULT_SAVE_DIR = Path.home() / "proppunk game files"


def ensure_save_dir() -> Path:
    DEFAULT_SAVE_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_SAVE_DIR


def resolve_save_path(name_or_path: str) -> Path:
    """Resolve a bare world name into DEFAULT_SAVE_DIR/<name>.json.

    A string containing a path separator (or an absolute path) is treated
    as an explicit file location and returned as-is.
    """
    path = Path(name_or_path)
    if path.is_absolute() or len(path.parts) > 1:
        return path
    if path.suffix != ".json":
        path = path.with_suffix(".json")
    return ensure_save_dir() / path


def list_worlds() -> list[str]:
    ensure_save_dir()
    return sorted(p.stem for p in DEFAULT_SAVE_DIR.glob("*.json"))


def rename_world(old_name: str, new_name: str) -> Path:
    old_path = resolve_save_path(old_name)
    new_path = resolve_save_path(new_name)
    if not old_path.exists():
        raise FileNotFoundError(old_path)
    if new_path.exists():
        raise FileExistsError(new_path)
    old_path.rename(new_path)
    return new_path


def _division_to_dict(division: Division) -> dict[str, Any]:
    result = {
        "id": division.id,
        "name": division.name,
        "division_type": division.division_type.name,
        "manpower": division.manpower,
        "supply_requirement": division.supply_requirement,
        "morale": division.morale,
        "location": division.location,
    }
    if isinstance(division, AirForceDivision):
        result["aircraft_type"] = division.aircraft_type
        result["equipment_rating"] = division.equipment_rating
        result["aircraft_count"] = division.aircraft_count
        result["range"] = division.range
    return result


def _division_from_dict(data: dict[str, Any]) -> Division:
    if data["division_type"] == "AIR_FORCE":
        return AirForceDivision(
            id=data["id"],
            name=data.get("name", data["id"]),
            division_type=DivisionType.AIR_FORCE,
            manpower=data["manpower"],
            supply_requirement=data["supply_requirement"],
            morale=data["morale"],
            location=data["location"],
            aircraft_type=data.get("aircraft_type", ""),
            equipment_rating=data.get("equipment_rating", 0.0),
            aircraft_count=data.get("aircraft_count", 0),
            range=data.get("range", 0.0),
        )
    return Division(
        id=data["id"],
        name=data.get("name", data["id"]),
        division_type=DivisionType[data["division_type"]],
        manpower=data["manpower"],
        supply_requirement=data["supply_requirement"],
        morale=data["morale"],
        location=data["location"],
    )


def _deployment_to_dict(deployment: MilitaryDeployment) -> dict[str, Any]:
    return {
        "country": deployment.country,
        "divisions": [_division_to_dict(d) for d in deployment.divisions],
    }


def _deployment_from_dict(data: dict[str, Any]) -> MilitaryDeployment:
    return MilitaryDeployment(
        country=data["country"],
        divisions=[_division_from_dict(d) for d in data["divisions"]],
    )


def _node_to_dict(node: Node) -> dict[str, Any]:
    return {
        "id": node.id,
        "x": node.x,
        "y": node.y,
        "country": node.country,
        "terrain": node.terrain.name,
        "connected_tiles": node.connected_tiles,
        "building_options": node.building_options,
        "resources": node.resources,
        "extraction_sites": node.extraction_sites,
        "economic_output": node.economic_output,
        "economic_growth_rate": node.economic_growth_rate,
        "population": node.population,
        "population_growth_rate": node.population_growth_rate,
        "military_deployments": [_deployment_to_dict(d) for d in node.military_deployments],
        "projected_population_growth_rate": node.projected_population_growth_rate,
    }


def _node_from_dict(data: dict[str, Any]) -> Node:
    return Node(
        id=data["id"],
        x=data.get("x", 0),
        y=data.get("y", 0),
        country=data["country"],
        terrain=Terrain[data["terrain"]],
        connected_tiles=data["connected_tiles"],
        building_options=data["building_options"],
        resources=data.get("resources", [False] * len(ResourceType)),
        extraction_sites=data.get("extraction_sites", [False] * len(ExtractionSiteType)),
        economic_output=data["economic_output"],
        economic_growth_rate=data["economic_growth_rate"],
        population=data["population"],
        population_growth_rate=data["population_growth_rate"],
        military_deployments=[_deployment_from_dict(d) for d in data["military_deployments"]],
        projected_population_growth_rate=data.get("projected_population_growth_rate", 0.0),
    )


def _country_to_dict(country: Country) -> dict[str, Any]:
    return {
        "name": country.name,
        "nodes": country.nodes,
        "government_type": country.government_type.name,
        "treasury": country.treasury,
        "stability": country.stability,
        "economic_output": country.economic_output,
        "population": country.population,
        "reserve_divisions": [_division_to_dict(d) for d in country.reserve_divisions],
    }


def _country_from_dict(data: dict[str, Any]) -> Country:
    return Country(
        name=data["name"],
        nodes=data["nodes"],
        government_type=GovernmentType[data["government_type"]],
        treasury=data["treasury"],
        stability=data["stability"],
        economic_output=data["economic_output"],
        population=data["population"],
        reserve_divisions=[_division_from_dict(d) for d in data.get("reserve_divisions", [])],
    )


def save_world(world: Any, path: str) -> None:
    data = {
        "year": world.year,
        "start_year": world.start_year,
        "width": world.width,
        "height": world.height,
        "nodes": {node_id: _node_to_dict(node) for node_id, node in world.nodes.items()},
        "countries": {name: _country_to_dict(country) for name, country in world.countries.items()},
    }
    Path(path).write_text(json.dumps(data, indent=2))


def _division_number_for_country(division_id: str, country: str) -> int | None:
    prefix = f"{country}_div_"
    if division_id.startswith(prefix) and division_id[len(prefix) :].isdigit():
        return int(division_id[len(prefix) :])
    return None


def _seed_division_id_counters(world: Any) -> None:
    max_by_country: dict[str, int] = {}

    def note(division_id: str, country: str) -> None:
        number = _division_number_for_country(division_id, country)
        if number is not None:
            max_by_country[country] = max(max_by_country.get(country, 0), number)

    for node in world.nodes.values():
        for deployment in node.military_deployments:
            for division in deployment.divisions:
                note(division.id, deployment.country)
    for country in world.countries.values():
        for division in country.reserve_divisions:
            note(division.id, country.name)

    for country, max_number in max_by_country.items():
        seed_division_id_counter(country, max_number + 1)


def load_into_world(world: Any, path: str) -> None:
    data = json.loads(Path(path).read_text())

    world.nodes.clear()
    for node_id, node_data in data.get("nodes", {}).items():
        world.nodes[node_id] = _node_from_dict(node_data)

    world.countries.clear()
    for name, country_data in data.get("countries", {}).items():
        world.countries[name] = _country_from_dict(country_data)

    world.year = data.get("year", 0)
    world.start_year = data.get("start_year", world.year)
    # Old saves predate grid dimensions/positions - fall back to a generous default so the
    # world stays usable for adding new nodes; existing nodes just default to (0, 0).
    world.width = data.get("width", 100)
    world.height = data.get("height", 100)

    _seed_division_id_counters(world)
