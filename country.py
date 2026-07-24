from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from division import Division
from node import Node


class GovernmentType(Enum):
    DEMOCRACY = auto()
    REPUBLIC = auto()
    MONARCHY = auto()
    DICTATORSHIP = auto()
    OLIGARCHY = auto()
    THEOCRACY = auto()
    ANARCHY = auto()


@dataclass
class Country:
    name: str
    nodes: list[str] = field(default_factory=list)
    government_type: GovernmentType = GovernmentType.REPUBLIC
    treasury: float = 0.0
    stability: float = 50.0
    economic_output: float = 0.0
    population: int = 0
    projected_economic_growth_rate: float = 0.0
    projected_population_growth_rate: float = 0.0
    reserve_divisions: list[Division] = field(default_factory=list)

    def get_name(self) -> str:
        return self.name

    def get_nodes(self) -> list[str]:
        return self.nodes

    def get_node_count(self) -> int:
        return len(self.nodes)

    def get_government_type(self) -> GovernmentType:
        return self.government_type

    def get_treasury(self) -> float:
        return self.treasury

    def get_stability(self) -> float:
        return self.stability

    def get_economic_output(self) -> float:
        return self.economic_output

    def get_population(self) -> int:
        return self.population

    def get_projected_economic_growth_rate(self) -> float:
        return self.projected_economic_growth_rate

    def get_projected_population_growth_rate(self) -> float:
        return self.projected_population_growth_rate

    def get_reserve_divisions(self) -> list[Division]:
        return self.reserve_divisions

    def find_reserve_division(self, name: str) -> Division | None:
        return next((d for d in self.reserve_divisions if d.name == name), None)

    def remove_reserve_division(self, name: str) -> Division | None:
        division = self.find_reserve_division(name)
        if division is not None:
            self.reserve_divisions.remove(division)
        return division

    def calculate_economic_output(self, all_nodes: dict[str, Node]) -> float:
        return sum(all_nodes[node_id].get_economic_output() for node_id in self.nodes if node_id in all_nodes)

    def calculate_population(self, all_nodes: dict[str, Node]) -> int:
        return sum(all_nodes[node_id].get_population() for node_id in self.nodes if node_id in all_nodes)

    def calculate_projected_economic_growth_rate(self, all_nodes: dict[str, Node]) -> float:
        total_output = self.calculate_economic_output(all_nodes)
        if total_output <= 0:
            return 0.0
        weighted_sum = sum(
            all_nodes[node_id].get_economic_output() * all_nodes[node_id].get_projected_economic_growth_rate()
            for node_id in self.nodes
            if node_id in all_nodes
        )
        return weighted_sum / total_output

    def calculate_projected_population_growth_rate(self, all_nodes: dict[str, Node]) -> float:
        total_population = self.calculate_population(all_nodes)
        if total_population <= 0:
            return 0.0
        weighted_sum = sum(
            all_nodes[node_id].get_population() * all_nodes[node_id].get_projected_population_growth_rate()
            for node_id in self.nodes
            if node_id in all_nodes
        )
        return weighted_sum / total_population

    def update_economic_output(self, all_nodes: dict[str, Node]) -> None:
        self.economic_output = self.calculate_economic_output(all_nodes)

    def update_population(self, all_nodes: dict[str, Node]) -> None:
        self.population = self.calculate_population(all_nodes)

    def update_projected_growth_rates(self, all_nodes: dict[str, Node]) -> None:
        self.projected_economic_growth_rate = self.calculate_projected_economic_growth_rate(all_nodes)
        self.projected_population_growth_rate = self.calculate_projected_population_growth_rate(all_nodes)
