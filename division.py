from __future__ import annotations

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


@dataclass
class Division:
    id: str
    division_type: DivisionType
    manpower: int
    supply_requirement: float
    morale: float = 100.0
    location: str | None = None

    def get_id(self) -> str:
        return self.id

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
