from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TypeAlias
import cplex
import networkx as nx
from cplex.exceptions import CplexSolverError
from simulator.classic_opt.fidelity_model import PathPurificationProfile, build_best_path_profile, compute_link_capacity_factor
from simulator.classic_opt.topology_parser import Edge, EdgePath, PathsDictionary, normalize_edge


Demand: TypeAlias = dict[str, int | float]
CapacityMap: TypeAlias = dict[Edge, float]


def build_uniform_capacities(graph: nx.Graph, capacity_pairs_per_second: float) -> CapacityMap:
    """
    Assign the same capacity to every link.

    This function is useful only for an initial sanity check.
    """
    if capacity_pairs_per_second <= 0:
        raise ValueError("capacity_pairs_per_second must be positive.")

    directed = graph.is_directed()

    return {normalize_edge(tuple(edge), directed): float(capacity_pairs_per_second) for edge in graph.edges}




def load_empirical_link_capacities(capacity_file_path: str | Path, node_to_index: dict[str, int], directed: bool=False) -> CapacityMap:
    """
    Load the link capacities measured by main_link_calibration.py.

    Expected aggregate JSON format:

    {
        "antibes-cagnes": {
            "node_a": "antibes",
            "node_b": "cagnes",
            "service_capacity_pairs_per_second": 168.14
        }
    }
    """
    input_path = Path(capacity_file_path)

    if not input_path.exists():
        raise FileNotFoundError(f'Capacity file not found: {input_path}')

    with input_path.open("r", encoding="utf-8") as file:
        raw_data = json.load(file)

    capacities: CapacityMap = {}

    for link_name, link_data in raw_data.items():
        node_a_name = link_data["node_a"]
        node_b_name = link_data["node_b"]

        if node_a_name not in node_to_index:
            raise KeyError(f"Unknown node '{node_a_name}' in capacity file.")

        if node_b_name not in node_to_index:
            raise KeyError(f"Unknown node '{node_b_name}' in capacity file.")

        node_a = node_to_index[node_a_name]
        node_b = node_to_index[node_b_name]

        edge = normalize_edge((node_a, node_b), directed)

        capacity = float(link_data['service_capacity_pairs_per_second'])

        if capacity <= 0:
            raise ValueError(f'Invalid calibrated capacity for link {link_name}: {capacity}')

        if edge in capacities:
            raise ValueError(f'Duplicate calibrated capacity for edge {edge}.')

        capacities[edge] = capacity

    return capacities


def path_swap_success_probability(path: EdgePath, swap_probability: float) -> float:
    """
    Compute the probability that all swapping operations on a path succeed.

    A path with h elementary links requires h - 1 swapping operations:

        P_swap(p) = q^(h - 1)
    """
    if not 0 < swap_probability <= 1:
        raise ValueError('swap_probability must belong to the interval (0, 1].')

    number_of_swaps = max(0, len(path) - 1)

    return swap_probability ** number_of_swaps


def path_capacity_consumption_factor(path: EdgePath, swap_probability: float) -> float:
    """
    Estimate the number of elementary resources needed per successfully
    delivered end-to-end pair:

        a_p = 1 / P_swap(p)
    """
    success_probability = path_swap_success_probability(path, swap_probability)

    return 1.0 / success_probability


def edge_path_to_node_path(path: EdgePath) -> list[int]:
    """Convert an edge path [(u,v), (v,w), ...] into the node path [u,v,w,...]."""
    if not path:
        return []
    nodes = [int(path[0][0]), int(path[0][1])]
    for edge in path[1:]:
        node_a, node_b = int(edge[0]), int(edge[1])
        if nodes[-1] == node_a:
            nodes.append(node_b)
        elif nodes[-1] == node_b:
            nodes.append(node_a)
        else:
            raise ValueError(f"Non-contiguous edge path: {path}")
    return nodes


def solve_path_mcf(graph: nx.Graph, all_paths: PathsDictionary, demands: list[Demand], link_capacities: CapacityMap, swap_probability: float, output_directory: str | Path="solutions", model_name: str="quantum_path_mcf", export_model: bool=True, link_fidelities: dict[Edge, float] | None=None, max_purification_rounds: int=3, use_fidelity_constraints: bool=False) -> dict:
    """Solve the path-based MCF LP with optional Werner-fidelity and BBPSSW purification profiles."""
    if not demands:
        raise ValueError("The traffic demand list is empty.")
    if not 0 < swap_probability <= 1:
        raise ValueError("swap_probability must belong to (0, 1].")
    if use_fidelity_constraints and link_fidelities is None:
        raise ValueError("link_fidelities is required when fidelity constraints are enabled.")
    if max_purification_rounds < 0:
        raise ValueError("max_purification_rounds cannot be negative.")

    directed = graph.is_directed()
    output_path = Path(output_directory)
    output_path.mkdir(parents=True, exist_ok=True)

    for demand in demands:
        key = (int(demand["source"]), int(demand["target"]))
        if key not in all_paths or not all_paths[key]:
            raise KeyError(f"No candidate path was provided for demand {key}.")
        if use_fidelity_constraints and "target_fidelity" not in demand:
            raise KeyError(f"Demand {key} has no target_fidelity.")

    for graph_edge in graph.edges:
        edge = normalize_edge(tuple(graph_edge), directed)
        if edge not in link_capacities:
            raise KeyError(f"No capacity was provided for link {edge}.")
        if link_capacities[edge] <= 0:
            raise ValueError(f"The capacity of link {edge} must be positive.")

    # Precompute the least-cost Werner/BBPSSW profile for every demand-path pair.
    path_profiles: dict[tuple[int, int], PathPurificationProfile] = {}
    profile_records: list[dict] = []
    fidelity_infeasible_demands: list[dict] = []

    if use_fidelity_constraints:
        normalized_link_fidelities = {normalize_edge(tuple(edge), directed): float(value) for edge, value in link_fidelities.items()}
        for demand_index, demand in enumerate(demands):
            source, target = int(demand["source"]), int(demand["target"])
            admissible_count = 0
            for path_index, path in enumerate(all_paths[(source, target)]):
                node_path = edge_path_to_node_path(path)
                profile = build_best_path_profile(path=node_path, link_fidelities=normalized_link_fidelities, target_fidelity=float(demand["target_fidelity"]), max_purification_rounds=max_purification_rounds)
                if profile is None:
                    continue
                path_profiles[(demand_index, path_index)] = profile
                admissible_count += 1
                profile_records.append({"demand_index": demand_index, "path_index": path_index, "source": source, "target": target, "edge_path": [list(edge) for edge in path], **profile.to_dict()})
            if admissible_count == 0:
                fidelity_infeasible_demands.append({"demand_index": demand_index, "source": source, "target": target, "target_fidelity": float(demand["target_fidelity"]), "max_purification_rounds": max_purification_rounds})

        profiles_file = output_path / f"{model_name}_fidelity_profiles.json"
        with profiles_file.open("w", encoding="utf-8") as file:
            json.dump(profile_records, file, indent=4)

        if fidelity_infeasible_demands:
            result = {"feasible": False, "status": "fidelity_infeasible", "reason": "At least one demand has no fidelity-admissible path.", "fidelity_infeasible_demands": fidelity_infeasible_demands, "fidelity_profiles_file": str(profiles_file)}
            result_file = output_path / f"{model_name}_result.json"
            with result_file.open("w", encoding="utf-8") as file:
                json.dump(result, file, indent=4)
            return result

    problem = cplex.Cplex()
    problem.objective.set_sense(problem.objective.sense.minimize)

    variable_map: dict[tuple[int, int], str] = {}
    variable_metadata: dict[str, dict] = {}

    # Decision variables.
    for demand_index, demand in enumerate(demands):
        source, target = int(demand["source"]), int(demand["target"])
        for path_index, path in enumerate(all_paths[(source, target)]):
            if use_fidelity_constraints and (demand_index, path_index) not in path_profiles:
                continue

            variable_name = f"x_d{demand_index}_p{path_index}"
            swap_success = path_swap_success_probability(path, swap_probability)

            if use_fidelity_constraints:
                profile = path_profiles[(demand_index, path_index)]
                edge_factors = {normalize_edge(tuple(edge), directed): compute_link_capacity_factor(profile=profile, edge=normalize_edge(tuple(edge), directed), swap_probability=swap_probability) for edge in path}
                objective_coefficient = sum(edge_factors.values())
                estimated_path_fidelity = profile.estimated_path_fidelity
                purification_profile = profile.to_dict()
            else:
                uniform_factor = path_capacity_consumption_factor(path, swap_probability)
                edge_factors = {normalize_edge(tuple(edge), directed): uniform_factor for edge in path}
                objective_coefficient = len(path) * uniform_factor
                estimated_path_fidelity = None
                purification_profile = None

            problem.variables.add(obj=[objective_coefficient], lb=[0.0], ub=[cplex.infinity], types=[problem.variables.type.continuous], names=[variable_name])
            variable_map[(demand_index, path_index)] = variable_name
            variable_metadata[variable_name] = {"demand_index": demand_index, "source": source, "target": target, "path_index": path_index, "path": path, "hop_count": len(path), "swap_count": max(0, len(path) - 1), "swap_success_probability": swap_success, "edge_capacity_consumption_factors": {f"{edge[0]}-{edge[1]}": factor for edge, factor in edge_factors.items()}, "objective_coefficient": objective_coefficient, "target_fidelity": float(demand["target_fidelity"]) if "target_fidelity" in demand else None, "estimated_path_fidelity": estimated_path_fidelity, "purification_profile": purification_profile}

    # Demand satisfaction.
    for demand_index, demand in enumerate(demands):
        source, target, volume = int(demand["source"]), int(demand["target"]), float(demand["volume"])
        variable_names = [variable_map[(demand_index, path_index)] for path_index in range(len(all_paths[(source, target)])) if (demand_index, path_index) in variable_map]
        if not variable_names:
            result = {"feasible": False, "status": "fidelity_infeasible", "reason": f"No admissible variable exists for demand {source}->{target}.", "demand_index": demand_index}
            result_file = output_path / f"{model_name}_result.json"
            with result_file.open("w", encoding="utf-8") as file:
                json.dump(result, file, indent=4)
            return result
        problem.linear_constraints.add(lin_expr=[cplex.SparsePair(ind=variable_names, val=[1.0] * len(variable_names))], senses=["E"], rhs=[volume], names=[f"demand_{source}_{target}_{demand_index}"])

    # Link capacities.
    for graph_edge in graph.edges:
        edge = normalize_edge(tuple(graph_edge), directed)
        variables_using_edge: list[str] = []
        capacity_coefficients: list[float] = []

        for variable_name, metadata in variable_metadata.items():
            factor_key = f"{edge[0]}-{edge[1]}"
            if factor_key not in metadata["edge_capacity_consumption_factors"]:
                continue
            variables_using_edge.append(variable_name)
            capacity_coefficients.append(float(metadata["edge_capacity_consumption_factors"][factor_key]))

        problem.linear_constraints.add(lin_expr=[cplex.SparsePair(ind=variables_using_edge, val=capacity_coefficients)], senses=["L"], rhs=[float(link_capacities[edge])], names=[f"capacity_{edge[0]}_{edge[1]}"])

    model_file = None
    if export_model:
        model_file = output_path / f"{model_name}.lp"
        problem.write(str(model_file))
        print(f"[CPLEX] Model saved in {model_file}.")
    print("[CPLEX] Solving the traffic feasibility problem...")

    try:
        problem.solve()
    except CplexSolverError as error:
        return {"feasible": False, "status": "CPLEX_ERROR", "reason": str(error)}

    status_code = problem.solution.get_status()
    status_string = problem.solution.get_status_string()
    print(f"[CPLEX] Status code: {status_code}")
    print(f"[CPLEX] Status: {status_string}")

    if not problem.solution.is_primal_feasible():
        result = {"feasible": False, "status_code": status_code, "status": status_string, "reason": "The traffic matrix is not capacity-feasible.", "fidelity_profiles": profile_records}
        result_file = output_path / f"{model_name}_result.json"
        with result_file.open("w", encoding="utf-8") as file:
            json.dump(result, file, indent=4)
        return result

    variable_names = list(variable_metadata)
    variable_values = problem.solution.get_values(variable_names)
    routing: list[dict] = []
    link_loads: CapacityMap = {normalize_edge(tuple(edge), directed): 0.0 for edge in graph.edges}
    total_carried_traffic = 0.0
    weighted_hop_sum = 0.0

    for variable_name, allocated_rate in zip(variable_names, variable_values):
        if allocated_rate <= 1e-9:
            continue
        metadata = variable_metadata[variable_name]
        per_edge_loads = {}
        for path_edge in metadata["path"]:
            edge = normalize_edge(tuple(path_edge), directed)
            factor = float(metadata["edge_capacity_consumption_factors"][f"{edge[0]}-{edge[1]}"])
            elementary_rate = allocated_rate * factor
            link_loads[edge] += elementary_rate
            per_edge_loads[f"{edge[0]}-{edge[1]}"] = elementary_rate
        routing.append({**metadata, "allocated_end_to_end_rate": allocated_rate, "expected_elementary_rate_by_link": per_edge_loads})
        total_carried_traffic += allocated_rate
        weighted_hop_sum += allocated_rate * metadata["hop_count"]

    link_statistics = []
    for edge in sorted(link_loads):
        capacity, load = float(link_capacities[edge]), float(link_loads[edge])
        utilization = load / capacity if capacity > 0 else math.inf
        link_statistics.append({"edge": list(edge), "capacity_pairs_per_second": capacity, "load_pairs_per_second": load, "utilization": utilization, "residual_capacity_pairs_per_second": capacity - load})

    total_offered_traffic = sum(float(demand["volume"]) for demand in demands)
    average_hop_count = weighted_hop_sum / total_carried_traffic if total_carried_traffic > 0 else None
    result = {"feasible": True, "status_code": status_code, "status": status_string, "objective": problem.solution.get_objective_value(), "number_of_nodes": graph.number_of_nodes(), "number_of_links": graph.number_of_edges(), "swap_probability": swap_probability, "use_fidelity_constraints": use_fidelity_constraints, "max_purification_rounds": max_purification_rounds, "offered_traffic_pairs_per_second": total_offered_traffic, "carried_traffic_pairs_per_second": total_carried_traffic, "average_hop_count": average_hop_count, "demands": demands, "routing": routing, "link_statistics": link_statistics, "fidelity_profiles": profile_records}

    if export_model:
        problem.solution.write(str(output_path / f"{model_name}.sol"))

    result_file = output_path / f"{model_name}_result.json"
    with result_file.open("w", encoding="utf-8") as file:
        json.dump(result, file, indent=4)

    print("[RESULT] The traffic matrix is feasible.")
    print(f"[RESULT] Objective: {result['objective']:.6f}")
    print(f"[RESULT] Offered traffic: {total_offered_traffic:.6f} pairs/s")
    if average_hop_count is not None:
        print(f"[RESULT] Average hop count: {average_hop_count:.4f}")
    print(f"[RESULT] Results saved in {result_file}.")
    return result


def print_feasibility_summary(result: dict) -> None:
    """Print a compact summary of the optimization result."""
    print("\n================ FEASIBILITY SUMMARY ================\n")
    if not result.get("feasible", False):
        print("Traffic matrix: INFEASIBLE")
        print(f"Status: {result.get('status')}")
        print(f"Reason: {result.get('reason')}")
        return

    print("Traffic matrix: FEASIBLE")
    print(f"Offered traffic : {result['offered_traffic_pairs_per_second']:.4f} pairs/s")
    print(f"Carried traffic : {result['carried_traffic_pairs_per_second']:.4f} pairs/s")
    if result["average_hop_count"] is not None:
        print(f"Average hops    : {result['average_hop_count']:.4f}")

    print("\nLink utilization:")
    for statistics in sorted(result["link_statistics"], key=lambda item: item["utilization"], reverse=True):
        node_a, node_b = statistics["edge"]
        print(f"  {node_a} <-> {node_b}: load={statistics['load_pairs_per_second']:.4f}, capacity={statistics['capacity_pairs_per_second']:.4f}, utilization={100 * statistics['utilization']:.2f}%")