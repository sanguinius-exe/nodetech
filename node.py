from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto

from division import Division


class Terrain(Enum):
    PLAINS = auto()
    FOREST = auto()
    HILLS = auto()
    MOUNTAIN = auto()
    DESERT = auto()
    WATER = auto()
    URBAN = auto()


class BuildingType(Enum):
    FARM = auto()
    MINE = auto()
    FACTORY = auto()
    BARRACKS = auto()
    PORT = auto()
    UNIVERSITY = auto()
    HOSPITAL = auto()
    POWER_PLANT = auto()


class ResourceType(Enum):
    BASIC_MATERIALS = auto()  # iron, steel, and other basic materials
    RARE_EARTH_METALS = auto()
    OIL_GAS = auto()


class ExtractionSiteType(Enum):
    BASIC_MATERIALS_MINE = auto()
    RARE_EARTH_MINE = auto()
    OIL_RIG = auto()


# Each extraction site can only be built on a node that already has the matching resource.
EXTRACTION_SITE_RESOURCE_REQUIREMENTS: dict[ExtractionSiteType, ResourceType] = {
    ExtractionSiteType.BASIC_MATERIALS_MINE: ResourceType.BASIC_MATERIALS,
    ExtractionSiteType.RARE_EARTH_MINE: ResourceType.RARE_EARTH_METALS,
    ExtractionSiteType.OIL_RIG: ResourceType.OIL_GAS,
}


# Growth rates are derived from GDP per capita (economic_output / population): richer nodes
# grow slower, poorer nodes grow faster, bounded by a floor and a ceiling. Economic growth is
# recalculated from this every year and drives the simulation directly; population growth is
# still set manually (population_growth_rate), with this used only as an informational forecast.
ECONOMIC_GROWTH_FLOOR = 0.015
ECONOMIC_GROWTH_CEILING = 0.05
POPULATION_GROWTH_FLOOR = 0.005
POPULATION_GROWTH_CEILING = 0.035

# The growth curve is an S-curve (logistic) in GDP per capita: nodes below GDP_PER_CAPITA_LOW
# sit near the ceiling, nodes above GDP_PER_CAPITA_HIGH sit near the floor, and the transition
# happens smoothly between the two. Tune these alongside whatever scale economic_output ends up using.
GDP_PER_CAPITA_LOW = 200.0
GDP_PER_CAPITA_HIGH = 1500.0
GDP_PER_CAPITA_MIDPOINT = (GDP_PER_CAPITA_LOW + GDP_PER_CAPITA_HIGH) / 2
GDP_PER_CAPITA_STEEPNESS = (GDP_PER_CAPITA_HIGH - GDP_PER_CAPITA_LOW) / 4


def _growth_rate_from_gdp_per_capita(gdp_per_capita: float, floor: float, ceiling: float) -> float:
    sigmoid = 1.0 / (1.0 + math.exp(-(gdp_per_capita - GDP_PER_CAPITA_MIDPOINT) / GDP_PER_CAPITA_STEEPNESS))
    return ceiling - (ceiling - floor) * sigmoid

ECONOMIC_GROWTH_BUILDING_MODIFIERS: dict[BuildingType, float] = {
    BuildingType.FARM: 0.001,
    BuildingType.MINE: 0.003,
    BuildingType.FACTORY: 0.005,
    BuildingType.BARRACKS: -0.001,
    BuildingType.PORT: 0.003,
    BuildingType.UNIVERSITY: 0.002,
    BuildingType.HOSPITAL: 0.001,
    BuildingType.POWER_PLANT: 0.004,
}

POPULATION_GROWTH_BUILDING_MODIFIERS: dict[BuildingType, float] = {
    BuildingType.FARM: 0.003,
    BuildingType.MINE: -0.0005,
    BuildingType.FACTORY: -0.001,
    BuildingType.BARRACKS: -0.002,
    BuildingType.PORT: 0.001,
    BuildingType.UNIVERSITY: 0.0005,
    BuildingType.HOSPITAL: 0.004,
    BuildingType.POWER_PLANT: 0.0,
}

# Extraction sites are a much bigger economic boon than ordinary buildings (the strongest
# building modifier above is FACTORY at 0.005) - raw resource exports are a major growth driver.
ECONOMIC_GROWTH_EXTRACTION_SITE_MODIFIERS: dict[ExtractionSiteType, float] = {
    ExtractionSiteType.BASIC_MATERIALS_MINE: 0.015,
    ExtractionSiteType.RARE_EARTH_MINE: 0.025,
    ExtractionSiteType.OIL_RIG: 0.02,
}

# A node's local supply first covers whatever's stationed on it directly; anything left over
# is what a rail network can pool to cover shortfalls elsewhere (see main.py's
# apply_supply_shortfalls). Population and GDP are the dominant factors - a thriving, populous
# node can support (and export) far more supply than a small one; BARRACKS adds a flat bonus on
# top, the same way it already nudges growth rates.
NODE_BASE_SUPPLY = 5.0
POPULATION_SUPPLY_PER_CAPITA = 0.005
GDP_SUPPLY_PER_OUTPUT = 0.01
BARRACKS_SUPPLY_BONUS = 5.0


@dataclass
class MilitaryDeployment:
    country: str
    divisions: list[Division] = field(default_factory=list)

    def get_divisions(self) -> list[Division]:
        return self.divisions

    def get_strength(self) -> int:
        return sum(d.manpower for d in self.divisions)


@dataclass
class Node:
    id: str
    x: int = 0
    y: int = 0
    country: str | None = None
    terrain: Terrain = Terrain.PLAINS
    connected_tiles: list[str] = field(default_factory=list)
    rail_connected_tiles: list[str] = field(default_factory=list)
    building_options: list[bool] = field(default_factory=lambda: [False] * len(BuildingType))
    resources: list[bool] = field(default_factory=lambda: [False] * len(ResourceType))
    extraction_sites: list[bool] = field(default_factory=lambda: [False] * len(ExtractionSiteType))
    economic_output: float = 0.0
    economic_growth_rate: float = 0.0
    population: int = 0
    population_growth_rate: float = 0.0
    military_deployments: list[MilitaryDeployment] = field(default_factory=list)
    projected_population_growth_rate: float = 0.0

    def get_id(self) -> str:
        return self.id

    def get_x(self) -> int:
        return self.x

    def get_y(self) -> int:
        return self.y

    def get_position(self) -> tuple[int, int]:
        return (self.x, self.y)

    def get_country(self) -> str | None:
        return self.country

    def get_terrain(self) -> Terrain:
        return self.terrain

    def get_connected_tiles(self) -> list[str]:
        return self.connected_tiles

    def get_connection_count(self) -> int:
        return len(self.connected_tiles)

    def get_rail_connected_tiles(self) -> list[str]:
        return self.rail_connected_tiles

    def get_local_supply(self) -> float:
        bonus = BARRACKS_SUPPLY_BONUS if self.has_building(BuildingType.BARRACKS) else 0.0
        return (
            NODE_BASE_SUPPLY
            + self.population * POPULATION_SUPPLY_PER_CAPITA
            + self.economic_output * GDP_SUPPLY_PER_OUTPUT
            + bonus
        )

    def get_building_options(self) -> list[bool]:
        return self.building_options

    def has_building(self, building_type: BuildingType) -> bool:
        return self.building_options[building_type.value - 1]

    def get_available_buildings(self) -> list[BuildingType]:
        return [b for b in BuildingType if self.has_building(b)]

    def get_resources(self) -> list[bool]:
        return self.resources

    def has_resource(self, resource_type: ResourceType) -> bool:
        return self.resources[resource_type.value - 1]

    def get_available_resources(self) -> list[ResourceType]:
        return [r for r in ResourceType if self.has_resource(r)]

    def get_extraction_sites(self) -> list[bool]:
        return self.extraction_sites

    def has_extraction_site(self, site_type: ExtractionSiteType) -> bool:
        return self.extraction_sites[site_type.value - 1]

    def get_available_extraction_sites(self) -> list[ExtractionSiteType]:
        return [s for s in ExtractionSiteType if self.has_extraction_site(s)]

    def can_build_extraction_site(self, site_type: ExtractionSiteType) -> bool:
        return self.has_resource(EXTRACTION_SITE_RESOURCE_REQUIREMENTS[site_type])

    def get_economic_output(self) -> float:
        return self.economic_output

    def get_economic_growth_rate(self) -> float:
        return self.economic_growth_rate

    def get_population(self) -> int:
        return self.population

    def get_population_growth_rate(self) -> float:
        return self.population_growth_rate

    def get_military_deployments(self) -> list[MilitaryDeployment]:
        return self.military_deployments

    def get_deployments_by_country(self, country: str) -> list[MilitaryDeployment]:
        return [d for d in self.military_deployments if d.country == country]

    def get_total_military_strength(self) -> int:
        return sum(d.get_strength() for d in self.military_deployments)

    def get_gdp_per_capita(self) -> float:
        if self.population <= 0:
            return 0.0
        return self.economic_output / self.population

    def get_projected_population_growth_rate(self) -> float:
        return self.projected_population_growth_rate

    def calculate_economic_growth_rate(self) -> float:
        base_rate = _growth_rate_from_gdp_per_capita(
            self.get_gdp_per_capita(), ECONOMIC_GROWTH_FLOOR, ECONOMIC_GROWTH_CEILING
        )
        modifier = sum(ECONOMIC_GROWTH_BUILDING_MODIFIERS.get(b, 0.0) for b in self.get_available_buildings())
        modifier += sum(
            ECONOMIC_GROWTH_EXTRACTION_SITE_MODIFIERS.get(s, 0.0) for s in self.get_available_extraction_sites()
        )
        return min(ECONOMIC_GROWTH_CEILING, max(ECONOMIC_GROWTH_FLOOR, base_rate + modifier))

    def calculate_projected_population_growth_rate(self) -> float:
        base_rate = _growth_rate_from_gdp_per_capita(
            self.get_gdp_per_capita(), POPULATION_GROWTH_FLOOR, POPULATION_GROWTH_CEILING
        )
        modifier = sum(POPULATION_GROWTH_BUILDING_MODIFIERS.get(b, 0.0) for b in self.get_available_buildings())
        return min(POPULATION_GROWTH_CEILING, max(POPULATION_GROWTH_FLOOR, base_rate + modifier))

    def update_projected_population_growth_rate(self) -> None:
        self.projected_population_growth_rate = self.calculate_projected_population_growth_rate()

    def advance_year(self) -> None:
        self.economic_growth_rate = self.calculate_economic_growth_rate()
        self.economic_output = max(0.0, self.economic_output * (1 + self.economic_growth_rate))
        self.population = max(0, round(self.population * (1 + self.population_growth_rate)))
        self.update_projected_population_growth_rate()
