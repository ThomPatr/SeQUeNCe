from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TypeAlias
import cplex
import networkx as nx
from cplex.exceptions import CplexSolverError

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

def ordered_path_nodes(
    edge_path: list,
    source: int,
    target: int,
) -> list[int]:
    """
    Convert an ordered edge path into an ordered node path.

    Example:
        [(0, 1), (1, 4), (4, 9)]
        -> [0, 1, 4, 9]
    """
    if not edge_path:
        if source == target:
            return [source]

        raise ValueError(
            f"Empty path for demand {source}->{target}."
        )

    remaining_edges = [
        (int(edge[0]), int(edge[1]))
        for edge in edge_path
    ]

    nodes = [int(source)]
    current = int(source)

    while remaining_edges:
        matching_index = None
        next_node = None

        for edge_index, (node_a, node_b) in enumerate(
            remaining_edges
        ):
            if node_a == current:
                matching_index = edge_index
                next_node = node_b
                break

            if node_b == current:
                matching_index = edge_index
                next_node = node_a
                break

        if matching_index is None:
            raise ValueError(
                "The edge sequence does not form a continuous path "
                f"from {source} to {target}: {edge_path}"
            )

        remaining_edges.pop(matching_index)
        nodes.append(int(next_node))
        current = int(next_node)

    if nodes[-1] != int(target):
        raise ValueError(
            f"Path terminates at {nodes[-1]} instead of {target}: "
            f"{edge_path}"
        )

    return nodes


def solve_path_mcf(
    graph: nx.Graph,
    all_paths: PathsDictionary,
    demands: list[Demand],
    link_capacities: CapacityMap,
    node_memory_capacities: dict[int, float],
    flow_memory_costs: dict[
        tuple[int, int],
        dict[int, float],
    ],
    swap_probability: float,
    output_directory: str | Path = "solutions",
    model_name: str = "quantum_path_mcf",
    export_model: bool = True,
) -> dict:
    """
    Solve the path-based MCF LP without purification.

    The model includes fixed or candidate paths, probabilistic swapping
    consumption and empirical quantum-memory capacity constraints.
    """
    if not demands:
        raise ValueError("The traffic demand list is empty.")

    if not 0 < swap_probability <= 1:
        raise ValueError("swap_probability must belong to (0, 1].")

    
    directed = graph.is_directed()
    output_path = Path(output_directory)
    output_path.mkdir(parents=True, exist_ok=True)

    # ============================================================
    # Input validation
    # ============================================================

    for demand in demands:
        source = int(demand["source"])
        target = int(demand["target"])
        key = (source, target)

        if key not in all_paths or not all_paths[key]:
            raise KeyError(f"No candidate path was provided for demand {key}.")

    for graph_edge in graph.edges:
        edge = normalize_edge(tuple(graph_edge), directed)

        if edge not in link_capacities:
            raise KeyError(f"No capacity was provided for link {edge}.")

        if link_capacities[edge] <= 0:
            raise ValueError(f"The capacity of link {edge} must be positive.")

    for graph_node in graph.nodes:
        node_index = int(graph_node)

        if node_index not in node_memory_capacities:
            raise KeyError(f"No quantum-memory capacity was provided for node {node_index}.")

        if node_memory_capacities[node_index] <= 0:
            raise ValueError(f"Quantum-memory capacity must be positive for node {node_index}.")
    for demand in demands:
        source = int(
            demand["source"]
        )

        target = int(
            demand["target"]
        )

        flow_key = (
            source,
            target,
        )

        if flow_key not in flow_memory_costs:
            raise KeyError(
                "No empirical flow-memory costs "
                f"provided for demand "
                f"{source}->{target}."
            )

    # ============================================================
    # CPLEX model and decision variables
    # ============================================================

    problem = cplex.Cplex()
    problem.objective.set_sense(problem.objective.sense.minimize)

    variable_map: dict[tuple[int, int], str] = {}
    variable_metadata: dict[str, dict] = {}

    for demand_index, demand in enumerate(demands):
        source = int(demand["source"])
        target = int(demand["target"])

        for path_index, edge_path in enumerate(all_paths[(source, target)]):
            node_path = ordered_path_nodes(edge_path=edge_path, source=source, target=target)
            hop_count = len(edge_path)
            swap_count = max(0, hop_count - 1)
            swap_success = swap_probability**swap_count
            swap_consumption_factor = swap_probability ** (-swap_count)

            normalized_edges = [normalize_edge(tuple(edge), directed) for edge in edge_path]
            edge_factors = {edge: swap_consumption_factor for edge in normalized_edges}
            objective_coefficient = hop_count * swap_consumption_factor
            variable_name = f"x_d{demand_index}_p{path_index}"

            problem.variables.add(
                obj=[objective_coefficient],
                lb=[0.0],
                ub=[cplex.infinity],
                types=[problem.variables.type.continuous],
                names=[variable_name],
            )

            variable_map[(demand_index, path_index)] = variable_name
            flow_key = (
                source,
                target,
            )

            empirical_node_costs = (
                flow_memory_costs[
                    flow_key
                ]
            )

            node_memory_costs = {}

            for node_index in node_path:
                if node_index not in empirical_node_costs:
                    raise KeyError(
                        "No empirical memory cost for "
                        f"flow {source}->{target} "
                        f"at node {node_index}. "
                        f"Path nodes: {node_path}. "
                        f"Available nodes: "
                        f"{sorted(empirical_node_costs)}."
                    )

                memory_cost = float(
                    empirical_node_costs[
                        node_index
                    ]
                )

                if memory_cost < 0:
                    raise ValueError(
                        "Memory cost cannot be negative "
                        f"for flow {source}->{target}, "
                        f"node {node_index}."
                    )

                node_memory_costs[
                    node_index
                ] = memory_cost

            variable_metadata[variable_name] = {
                "demand_index": demand_index,
                "source": source,
                "target": target,
                "path_index": path_index,
                "path": edge_path,
                "node_path": node_path,
                "hop_count": hop_count,
                "swap_count": swap_count,
                "swap_success_probability": swap_success,
                "swap_consumption_factor": swap_consumption_factor,
                "edge_capacity_consumption_factors": {
                    f"{edge[0]}-{edge[1]}": factor for edge, factor in edge_factors.items()
                },
                "node_memory_costs": {
                    str(node_index): memory_cost
                    for node_index, memory_cost
                    in node_memory_costs.items()
                },

                "objective_coefficient": objective_coefficient,
                "target_fidelity": (
                    float(demand["target_fidelity"]) if "target_fidelity" in demand else None
                ),
            }

    # ============================================================
    # Demand satisfaction constraints
    # ============================================================

    for demand_index, demand in enumerate(demands):
        source = int(demand["source"])
        target = int(demand["target"])
        volume = float(demand["volume"])

        variable_names = [
            variable_map[(demand_index, path_index)]
            for path_index in range(len(all_paths[(source, target)]))
        ]

        problem.linear_constraints.add(
            lin_expr=[cplex.SparsePair(ind=variable_names, val=[1.0] * len(variable_names))],
            senses=["E"],
            rhs=[volume],
            names=[f"demand_{source}_{target}_{demand_index}"],
        )

    # ============================================================
    # Physical-link capacity constraints
    # ============================================================

    for graph_edge in graph.edges:
        edge = normalize_edge(tuple(graph_edge), directed)
        factor_key = f"{edge[0]}-{edge[1]}"
        variables_using_edge = []
        capacity_coefficients = []

        for variable_name, metadata in variable_metadata.items():
            edge_factors = metadata["edge_capacity_consumption_factors"]

            if factor_key not in edge_factors:
                continue

            variables_using_edge.append(variable_name)
            capacity_coefficients.append(float(edge_factors[factor_key]))

        problem.linear_constraints.add(
            lin_expr=[
                cplex.SparsePair(ind=variables_using_edge, val=capacity_coefficients)
            ],
            senses=["L"],
            rhs=[float(link_capacities[edge])],
            names=[f"capacity_{edge[0]}_{edge[1]}"],
        )

    # ============================================================
    # Node quantum-memory capacity constraints
    # ============================================================

    memory_constraint_metadata = {}

    for graph_node in graph.nodes:
        node_index = int(graph_node)
        variables_using_node = []
        memory_coefficients = []
        coefficient_records = []

        for variable_name, metadata in variable_metadata.items():
            if node_index not in metadata["node_path"]:
                continue

            memory_cost = float(
    metadata[
        "node_memory_costs"
    ][str(node_index)]
)
           

            # Each empirical record refers to one physical memory.
            
            memory_occupancy_factor = (
    memory_cost
)
            variables_using_node.append(variable_name)
            memory_coefficients.append(memory_occupancy_factor)
            coefficient_records.append(
                {
                    "variable": variable_name,
                    "source": metadata["source"],
                    "target": metadata["target"],
                    "path_index": metadata["path_index"],
                    
        
                    "memory_occupancy_factor": memory_occupancy_factor,
                }
            )

        if not variables_using_node:
            continue

        memory_capacity = float(node_memory_capacities[node_index])

        problem.linear_constraints.add(
            lin_expr=[
                cplex.SparsePair(ind=variables_using_node, val=memory_coefficients)
            ],
            senses=["L"],
            rhs=[memory_capacity],
            names=[f"memory_capacity_{node_index}"],
        )

        memory_constraint_metadata[node_index] = {
            "memory_capacity": memory_capacity,
            "coefficients": coefficient_records,
        }

    # ============================================================
    # Export model and solve
    # ============================================================

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
        result = {
            "feasible": False,
            "status_code": status_code,
            "status": status_string,
            "reason": (
                "The traffic matrix is not feasible under the link and "
                "node-memory capacities."
            ),
        }

        result_file = output_path / f"{model_name}_result.json"

        with result_file.open("w", encoding="utf-8") as file:
            json.dump(result, file, indent=4)

        return result

    # ============================================================
    # Extract routing solution
    # ============================================================

    variable_names = list(variable_metadata)
    variable_values = problem.solution.get_values(variable_names)
    variable_solution = dict(zip(variable_names, variable_values))

    routing = []
    link_loads: CapacityMap = {
        normalize_edge(tuple(edge), directed): 0.0 for edge in graph.edges
    }
    node_memory_loads = {int(node): 0.0 for node in graph.nodes}
    node_flow_contributions: dict[int, list[dict]] = {
        int(node): [] for node in graph.nodes
    }

    total_carried_traffic = 0.0
    weighted_hop_sum = 0.0

    for variable_name, allocated_rate in variable_solution.items():
        if allocated_rate <= 1e-9:
            continue

        metadata = variable_metadata[variable_name]
        per_edge_loads = {}

        for path_edge in metadata["path"]:
            edge = normalize_edge(tuple(path_edge), directed)
            factor_key = f"{edge[0]}-{edge[1]}"
            factor = float(metadata["edge_capacity_consumption_factors"][factor_key])
            elementary_rate = allocated_rate * factor

            link_loads[edge] += elementary_rate
            per_edge_loads[factor_key] = elementary_rate

        per_node_memory_loads = {}

        for node_index in metadata["node_path"]:
            memory_cost = float(
    metadata[
        "node_memory_costs"
    ][str(node_index)]
)

            memory_occupancy = (
                allocated_rate
                * memory_cost
            )

            node_memory_loads[node_index] += memory_occupancy
            per_node_memory_loads[str(node_index)] = memory_occupancy

            node_flow_contributions[
                    node_index
                ].append(
                    {
                        "source":
                            metadata["source"],
                        "target":
                            metadata["target"],
                        "path_index":
                            metadata["path_index"],
                        "allocated_rate_pairs_per_s":
                            allocated_rate,
                        "memory_seconds_per_delivered_pair":
                            memory_cost,
                        "expected_memory_occupancy":
                            memory_occupancy,
                    }
)
        routing.append(
            {
                **metadata,
                "allocated_end_to_end_rate": allocated_rate,
                "expected_elementary_rate_by_link": per_edge_loads,
                "expected_memory_occupancy_by_node": per_node_memory_loads,
            }
        )

        total_carried_traffic += allocated_rate
        weighted_hop_sum += allocated_rate * metadata["hop_count"]

    # ============================================================
    # Link statistics
    # ============================================================

    link_statistics = []

    for edge in sorted(link_loads):
        capacity = float(link_capacities[edge])
        load = float(link_loads[edge])
        utilization = load / capacity if capacity > 0 else math.inf

        link_statistics.append(
            {
                "edge": list(edge),
                "capacity_pairs_per_second": capacity,
                "load_pairs_per_second": load,
                "utilization": utilization,
                "residual_capacity_pairs_per_second": capacity - load,
            }
        )

    # ============================================================
    # Node memory statistics
    # ============================================================

    node_statistics = []

    for node_index in sorted(node_memory_loads):
        capacity = float(node_memory_capacities[node_index])
        load = float(node_memory_loads[node_index])
        utilization = load / capacity if capacity > 0 else math.inf

        node_statistics.append(
            {
                "node": node_index,
                "memory_capacity": capacity,
                "expected_memory_occupancy": load,
                "memory_utilization": utilization,
                "residual_memory_capacity": capacity - load,
                "flow_contributions": node_flow_contributions[node_index],
            }
        )

    maximum_link_utilization = max(
        (record["utilization"] for record in link_statistics),
        default=0.0,
    )
    link_bottleneck = max(
        link_statistics,
        key=lambda record: record["utilization"],
        default=None,
    )
    maximum_node_memory_utilization = max(
        (record["memory_utilization"] for record in node_statistics),
        default=0.0,
    )
    memory_bottleneck = max(
        node_statistics,
        key=lambda record: record["memory_utilization"],
        default=None,
    )

    total_offered_traffic = sum(float(demand["volume"]) for demand in demands)
    average_hop_count = (
        weighted_hop_sum / total_carried_traffic if total_carried_traffic > 0 else None
    )

    result = {
        "feasible": True,
        "status_code": status_code,
        "status": status_string,
        "objective": problem.solution.get_objective_value(),
        "number_of_nodes": graph.number_of_nodes(),
        "number_of_links": graph.number_of_edges(),
        "swap_probability": swap_probability,
        "purification_enabled": False,
        "offered_traffic_pairs_per_second": total_offered_traffic,
        "carried_traffic_pairs_per_second": total_carried_traffic,
        "average_hop_count": average_hop_count,
        "maximum_link_utilization": maximum_link_utilization,
        "link_bottleneck_edge": (
            link_bottleneck["edge"] if link_bottleneck is not None else None
        ),
        "maximum_node_memory_utilization": maximum_node_memory_utilization,
        "memory_bottleneck_node": (
            memory_bottleneck["node"] if memory_bottleneck is not None else None
        ),
        "demands": demands,
        "routing": routing,
        "link_statistics": link_statistics,
        "node_statistics": node_statistics,
    }

    if export_model:
        problem.solution.write(str(output_path / f"{model_name}.sol"))

    result_file = output_path / f"{model_name}_result.json"

    with result_file.open("w", encoding="utf-8") as file:
        json.dump(result, file, indent=4)

    print("[RESULT] The traffic matrix is feasible.")
    print(f"[RESULT] Objective: {result['objective']:.6f}")
    print(f"[RESULT] Offered traffic: {total_offered_traffic:.6f} pairs/s")
    print(f"[RESULT] Max link utilization: {100 * maximum_link_utilization:.4f}%")
    print(f"[RESULT] Max memory utilization: {100 * maximum_node_memory_utilization:.4f}%")
    print(f"[RESULT] Results saved in {result_file}.")

    return result


def print_feasibility_summary(
    result: dict,
) -> None:
    """Print a compact summary of the optimization result."""
    print(
        "\n"
        "================ FEASIBILITY SUMMARY ================\n"
    )

    if not result.get(
        "feasible",
        False,
    ):
        print(
            "Traffic matrix: INFEASIBLE"
        )

        print(
            f"Status: {result.get('status')}"
        )

        print(
            f"Reason: {result.get('reason')}"
        )

        return

    print(
        "Traffic matrix: FEASIBLE"
    )

    print(
        f"Offered traffic : "
        f"{result['offered_traffic_pairs_per_second']:.4f} "
        f"pairs/s"
    )

    print(
        f"Carried traffic : "
        f"{result['carried_traffic_pairs_per_second']:.4f} "
        f"pairs/s"
    )

    if (
        result["average_hop_count"]
        is not None
    ):
        print(
            f"Average hops    : "
            f"{result['average_hop_count']:.4f}"
        )

    print(
        "\nLink utilization:"
    )

    for statistics in sorted(
        result["link_statistics"],
        key=lambda item:
            item["utilization"],
        reverse=True,
    ):
        node_a, node_b = (
            statistics["edge"]
        )

        print(
            f"  {node_a} <-> {node_b}: "
            f"load="
            f"{statistics['load_pairs_per_second']:.4f}, "
            f"capacity="
            f"{statistics['capacity_pairs_per_second']:.4f}, "
            f"utilization="
            f"{100 * statistics['utilization']:.2f}%"
        )

    print(
        "\nNode quantum-memory utilization:"
    )

    for statistics in sorted(
        result["node_statistics"],
        key=lambda item:
            item["memory_utilization"],
        reverse=True,
    ):
        print(
            f"  node {statistics['node']}: "
            f"occupancy="
            f"{statistics['expected_memory_occupancy']:.4f}, "
            f"capacity="
            f"{statistics['memory_capacity']:.4f}, "
            f"utilization="
            f"{100 * statistics['memory_utilization']:.2f}%"
        )

    print(
        "\nBottlenecks:"
    )

    print(
        f"  link   : "
        f"{result.get('link_bottleneck_edge')}"
    )

    print(
        f"  memory : "
        f"{result.get('memory_bottleneck_node')}"
    )