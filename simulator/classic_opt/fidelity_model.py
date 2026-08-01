from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from math import prod
from typing import Any
import json
from pathlib import Path

Edge = tuple[int, int]
def load_link_fidelities(
    fidelity_file_path: str | Path,
    node_to_index: dict[str, int],
) -> dict[Edge, float]:
    """
    Load elementary-link fidelities from JSON.

    Expected format:

    {
        "antibes-cagnes": 0.88,
        "cagnes-nice": 0.88
    }
    """
    input_path = Path(fidelity_file_path)

    if not input_path.exists():
        raise FileNotFoundError(
            f"Fidelity file not found: {input_path}"
        )

    with input_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        raw_data = json.load(file)

    link_fidelities: dict[Edge, float] = {}

    for link_name, fidelity_value in raw_data.items():
        node_a_name, node_b_name = link_name.split(
            "-",
            maxsplit=1,
        )

        if node_a_name not in node_to_index:
            raise KeyError(
                f"Unknown node '{node_a_name}' "
                f"in fidelity file."
            )

        if node_b_name not in node_to_index:
            raise KeyError(
                f"Unknown node '{node_b_name}' "
                f"in fidelity file."
            )

        edge = normalize_edge(
            node_to_index[node_a_name],
            node_to_index[node_b_name],
        )

        fidelity = float(fidelity_value)

        validate_fidelity(fidelity)

        if edge in link_fidelities:
            raise ValueError(
                f"Duplicate fidelity entry for edge {edge}."
            )

        link_fidelities[edge] = fidelity

    return link_fidelities

@dataclass(frozen=True)
class LinkPurificationProfile:
    edge: Edge
    rounds: int
    initial_fidelity: float
    output_fidelity: float
    expected_raw_pairs: float
    round_success_probabilities: tuple[float, ...]


@dataclass(frozen=True)
class PathPurificationProfile:
    path: tuple[int, ...]
    target_fidelity: float
    estimated_path_fidelity: float
    link_profiles: tuple[LinkPurificationProfile, ...]
    link_consumption_factors: dict[Edge, float]
    objective_cost: float
    total_expected_raw_link_pairs: float
    admissible: bool

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["path"] = list(self.path)
        result["link_profiles"] = [{**asdict(profile), "edge": list(profile.edge)} for profile in self.link_profiles]
        result["link_consumption_factors"] = {f"{edge[0]}-{edge[1]}": factor for edge, factor in self.link_consumption_factors.items()}
        return result


def normalize_edge(node_a: int, node_b: int) -> Edge:
    return tuple(sorted((int(node_a), int(node_b))))


def path_edges(path: list[int] | tuple[int, ...]) -> list[Edge]:
    return [normalize_edge(node_a, node_b) for node_a, node_b in zip(path[:-1], path[1:])]


def validate_fidelity(fidelity: float) -> None:
    if not 0.25 <= fidelity <= 1.0:
        raise ValueError(f"Werner-state fidelity must belong to [0.25, 1.0]. Received {fidelity}.")


def fidelity_to_werner(fidelity: float) -> float:
    validate_fidelity(fidelity)
    return (4.0 * fidelity - 1.0) / 3.0


def werner_to_fidelity(werner_parameter: float) -> float:
    return (1.0 + 3.0 * werner_parameter) / 4.0


def bbpssw_success_probability(fidelity: float) -> float:
    validate_fidelity(fidelity)
    return fidelity**2 + 2.0 * fidelity * (1.0 - fidelity) / 3.0 + 5.0 * ((1.0 - fidelity) / 3.0) ** 2


def bbpssw_output_fidelity(fidelity: float) -> float:
    probability = bbpssw_success_probability(fidelity)

    if probability <= 0.0:
        raise ValueError("Invalid zero BBPSSW success probability.")

    return (fidelity**2 + ((1.0 - fidelity) / 3.0) ** 2) / probability


def build_link_profiles(edge: Edge, initial_fidelity: float, max_rounds: int) -> list[LinkPurificationProfile]:
    if max_rounds < 0:
        raise ValueError("max_rounds cannot be negative.")

    validate_fidelity(initial_fidelity)

    profiles = [
        LinkPurificationProfile(
            edge=edge,
            rounds=0,
            initial_fidelity=initial_fidelity,
            output_fidelity=initial_fidelity,
            expected_raw_pairs=1.0,
            round_success_probabilities=(),
        )
    ]

    current_fidelity = initial_fidelity
    expected_raw_pairs = 1.0
    probabilities: list[float] = []

    for round_index in range(1, max_rounds + 1):
        success_probability = bbpssw_success_probability(current_fidelity)
        output_fidelity = bbpssw_output_fidelity(current_fidelity)
        expected_raw_pairs *= 2.0 / success_probability
        probabilities.append(success_probability)

        profiles.append(
            LinkPurificationProfile(
                edge=edge,
                rounds=round_index,
                initial_fidelity=initial_fidelity,
                output_fidelity=output_fidelity,
                expected_raw_pairs=expected_raw_pairs,
                round_success_probabilities=tuple(probabilities),
            )
        )

        current_fidelity = output_fidelity

    return profiles

def compute_link_capacity_factor(
    profile: PathPurificationProfile,
    edge: Edge,
    swap_probability: float,
) -> float:
    if not 0.0 < swap_probability <= 1.0:
        raise ValueError(
            "swap_probability must belong to (0, 1]."
        )

    number_of_links = max(
        0,
        len(profile.path) - 1,
    )

    number_of_swaps = max(
        0,
        number_of_links - 1,
    )

    purification_factor = (
        profile.link_consumption_factors[
            edge
        ]
    )

    return (
        purification_factor
        / (
            swap_probability
            ** number_of_swaps
        )
    )
def compute_werner_path_fidelity(link_profiles: list[LinkPurificationProfile] | tuple[LinkPurificationProfile, ...]) -> float:
    if not link_profiles:
        return 1.0

    path_werner = prod(fidelity_to_werner(profile.output_fidelity) for profile in link_profiles)
    return werner_to_fidelity(path_werner)


def build_best_path_profile(path: list[int], link_fidelities: dict[Edge, float], target_fidelity: float, max_purification_rounds: int) -> PathPurificationProfile | None:
    """
    Enumerate the allowed purification rounds independently for each
    elementary link and return the least-cost configuration satisfying
    the Werner end-to-end fidelity target.

    The cost is the sum of the expected raw-pair consumption factors
    across the links of the fixed path.
    """
    validate_fidelity(target_fidelity)
    edges = path_edges(path)

    if not edges:
        return PathPurificationProfile(
            path=tuple(path),
            target_fidelity=target_fidelity,
            estimated_path_fidelity=1.0,
            link_profiles=(),
            link_consumption_factors={},
            objective_cost=0.0,
            total_expected_raw_link_pairs=0.0,
            admissible=True,
        )

    profiles_per_edge = []

    for edge in edges:
        if edge not in link_fidelities:
            raise KeyError(f"No elementary fidelity provided for edge {edge}.")

        profiles_per_edge.append(
            build_link_profiles(
                edge=edge,
                initial_fidelity=float(link_fidelities[edge]),
                max_rounds=max_purification_rounds,
            )
        )

    best_profile = None
    best_cost = None

    for configuration in product(*profiles_per_edge):
        path_fidelity = compute_werner_path_fidelity(configuration)

        if path_fidelity + 1e-12 < target_fidelity:
            continue

        link_factors = {profile.edge: profile.expected_raw_pairs for profile in configuration}
        total_cost = sum(link_factors.values())
        rounds_tuple = tuple(profile.rounds for profile in configuration)
        selection_key = (total_cost, sum(rounds_tuple), rounds_tuple)

        if best_cost is not None and selection_key >= best_cost:
            continue

        best_cost = selection_key
        best_profile = PathPurificationProfile(
            path=tuple(path),
            target_fidelity=target_fidelity,
            estimated_path_fidelity=path_fidelity,
            link_profiles=tuple(configuration),
            link_consumption_factors=link_factors,
            objective_cost=total_cost,
            total_expected_raw_link_pairs=total_cost,
            admissible=True,
        )

    return best_profile