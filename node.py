from __future__ import annotations

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


# Projected growth rates are derived from GDP per capita (economic_output / population):
# richer nodes grow slower, poorer nodes grow faster, bounded by a floor and a ceiling.
ECONOMIC_GROWTH_FLOOR = 0.015
ECONOMIC_GROWTH_CEILING = 0.05
POPULATION_GROWTH_FLOOR = 0.005
POPULATION_GROWTH_CEILING = 0.035

# Calibration constant: the GDP per capita at which the base rate sits exactly halfway
# between floor and ceiling. Tune this alongside whatever scale economic_output ends up using.
GDP_PER_CAPITA_SCALE = 0.1

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
    country: str | None = None
    terrain: Terrain = Terrain.PLAINS
    connected_tiles: list[str] = field(default_factory=list)
    building_options: list[bool] = field(default_factory=lambda: [False] * len(BuildingType))
    economic_output: float = 0.0
    economic_growth_rate: float = 0.0
    population: int = 0
    population_growth_rate: float = 0.0
    military_deployments: list[MilitaryDeployment] = field(default_factory=list)
    projected_economic_growth_rate: float = 0.0
    projected_population_growth_rate: float = 0.0

    def get_id(self) -> str:
        return self.id

    def get_country(self) -> str | None:
        return self.country

    def get_terrain(self) -> Terrain:
        return self.terrain

    def get_connected_tiles(self) -> list[str]:
        return self.connected_tiles

    def get_connection_count(self) -> int:
        return len(self.connected_tiles)

    def get_building_options(self) -> list[bool]:
        return self.building_options

    def has_building(self, building_type: BuildingType) -> bool:
        return self.building_options[building_type.value - 1]

    def get_available_buildings(self) -> list[BuildingType]:
        return [b for b in BuildingType if self.has_building(b)]

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

    def get_projected_economic_growth_rate(self) -> float:
        return self.projected_economic_growth_rate

    def get_projected_population_growth_rate(self) -> float:
        return self.projected_population_growth_rate

    def calculate_projected_economic_growth_rate(self) -> float:
        saturation = self.get_gdp_per_capita() / (self.get_gdp_per_capita() + GDP_PER_CAPITA_SCALE)
        base_rate = ECONOMIC_GROWTH_CEILING - (ECONOMIC_GROWTH_CEILING - ECONOMIC_GROWTH_FLOOR) * saturation
        modifier = sum(ECONOMIC_GROWTH_BUILDING_MODIFIERS.get(b, 0.0) for b in self.get_available_buildings())
        return min(ECONOMIC_GROWTH_CEILING, max(ECONOMIC_GROWTH_FLOOR, base_rate + modifier))

    def calculate_projected_population_growth_rate(self) -> float:
        saturation = self.get_gdp_per_capita() / (self.get_gdp_per_capita() + GDP_PER_CAPITA_SCALE)
        base_rate = POPULATION_GROWTH_CEILING - (POPULATION_GROWTH_CEILING - POPULATION_GROWTH_FLOOR) * saturation
        modifier = sum(POPULATION_GROWTH_BUILDING_MODIFIERS.get(b, 0.0) for b in self.get_available_buildings())
        return min(POPULATION_GROWTH_CEILING, max(POPULATION_GROWTH_FLOOR, base_rate + modifier))

    def update_projected_growth_rates(self) -> None:
        self.projected_economic_growth_rate = self.calculate_projected_economic_growth_rate()
        self.projected_population_growth_rate = self.calculate_projected_population_growth_rate()

    def advance_year(self) -> None:
        self.population = max(0, round(self.population * (1 + self.population_growth_rate)))
        self.economic_output = max(0.0, self.economic_output * (1 + self.economic_growth_rate))
        self.update_projected_growth_rates()
