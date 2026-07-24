from __future__ import annotations

import itertools
from dataclasses import dataclass
from enum import Enum, auto


class DivisionType(Enum):
    INFANTRY = auto()
    ARMOR = auto()
    ARTILLERY = auto()
    CAVALRY = auto()
    AIRBORNE = auto()
    ENGINEER = auto()
    LOGISTICS = auto()
    AIR_FORCE = auto()


_id_counters: dict[str, itertools.count] = {}


def next_division_id(country: str) -> str:
    """IDs are scoped per country, e.g. 'Fedran Republic_div_1', so each country's numbering is independent."""
    counter = _id_counters.setdefault(country, itertools.count(1))
    return f"{country}_div_{next(counter)}"


def seed_division_id_counter(country: str, next_value: int) -> None:
    """Point a country's ID generator past IDs already in use, e.g. after loading a save file."""
    _id_counters[country] = itertools.count(next_value)


@dataclass
class Division:
    id: str
    name: str
    division_type: DivisionType
    manpower: int
    supply_requirement: float
    morale: float = 100.0
    location: str | None = None

    @classmethod
    def create(
        cls,
        country: str,
        name: str,
        division_type: DivisionType,
        manpower: int,
        supply_requirement: float,
        morale: float = 100.0,
        location: str | None = None,
    ) -> Division:
        return cls(
            id=next_division_id(country),
            name=name,
            division_type=division_type,
            manpower=manpower,
            supply_requirement=supply_requirement,
            morale=morale,
            location=location,
        )

    def get_id(self) -> str:
        return self.id

    def get_name(self) -> str:
        return self.name

    def get_division_type(self) -> DivisionType:
        return self.division_type

    def get_manpower(self) -> int:
        return self.manpower

    def get_supply_requirement(self) -> float:
        return self.supply_requirement

    def get_morale(self) -> float:
        return self.morale

    def get_location(self) -> str | None:
        return self.location


@dataclass
class AirForceDivision(Division):
    aircraft_type: str = ""
    equipment_rating: float = 0.0
    aircraft_count: int = 0
    range: float = 0.0

    @classmethod
    def create_air_force(
        cls,
        country: str,
        name: str,
        manpower: int,
        supply_requirement: float,
        aircraft_type: str,
        equipment_rating: float,
        aircraft_count: int,
        range: float,
        morale: float = 100.0,
        location: str | None = None,
    ) -> AirForceDivision:
        return cls(
            id=next_division_id(country),
            name=name,
            division_type=DivisionType.AIR_FORCE,
            manpower=manpower,
            supply_requirement=supply_requirement,
            morale=morale,
            location=location,
            aircraft_type=aircraft_type,
            equipment_rating=equipment_rating,
            aircraft_count=aircraft_count,
            range=range,
        )

    def get_aircraft_type(self) -> str:
        return self.aircraft_type

    def get_equipment_rating(self) -> float:
        return self.equipment_rating

    def get_aircraft_count(self) -> int:
        return self.aircraft_count

    def get_range(self) -> float:
        return self.range
