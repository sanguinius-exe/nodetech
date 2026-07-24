from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from country import Country, GovernmentType
from division import Division, DivisionType
from node import BuildingType, MilitaryDeployment, Node, Terrain

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
    return {
        "id": division.id,
        "division_type": division.division_type.name,
        "manpower": division.manpower,
        "supply_requirement": division.supply_requirement,
        "morale": division.morale,
        "location": division.location,
    }


def _division_from_dict(data: dict[str, Any]) -> Division:
    return Division(
        id=data["id"],
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
        "country": node.country,
        "terrain": node.terrain.name,
        "connected_tiles": node.connected_tiles,
        "building_options": node.building_options,
        "economic_output": node.economic_output,
        "economic_growth_rate": node.economic_growth_rate,
        "population": node.population,
        "population_growth_rate": node.population_growth_rate,
        "military_deployments": [_deployment_to_dict(d) for d in node.military_deployments],
        "projected_economic_growth_rate": node.projected_economic_growth_rate,
        "projected_population_growth_rate": node.projected_population_growth_rate,
    }


def _node_from_dict(data: dict[str, Any]) -> Node:
    return Node(
        id=data["id"],
        country=data["country"],
        terrain=Terrain[data["terrain"]],
        connected_tiles=data["connected_tiles"],
        building_options=data["building_options"],
        economic_output=data["economic_output"],
        economic_growth_rate=data["economic_growth_rate"],
        population=data["population"],
        population_growth_rate=data["population_growth_rate"],
        military_deployments=[_deployment_from_dict(d) for d in data["military_deployments"]],
        projected_economic_growth_rate=data.get("projected_economic_growth_rate", 0.0),
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
        "projected_economic_growth_rate": country.projected_economic_growth_rate,
        "projected_population_growth_rate": country.projected_population_growth_rate,
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
        projected_economic_growth_rate=data.get("projected_economic_growth_rate", 0.0),
        projected_population_growth_rate=data.get("projected_population_growth_rate", 0.0),
    )


def save_world(world: Any, path: str) -> None:
    data = {
        "year": world.year,
        "nodes": {node_id: _node_to_dict(node) for node_id, node in world.nodes.items()},
        "countries": {name: _country_to_dict(country) for name, country in world.countries.items()},
    }
    Path(path).write_text(json.dumps(data, indent=2))


def load_into_world(world: Any, path: str) -> None:
    data = json.loads(Path(path).read_text())

    world.nodes.clear()
    for node_id, node_data in data.get("nodes", {}).items():
        world.nodes[node_id] = _node_from_dict(node_data)

    world.countries.clear()
    for name, country_data in data.get("countries", {}).items():
        world.countries[name] = _country_from_dict(country_data)

    world.year = data.get("year", 0)
