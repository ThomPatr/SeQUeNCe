from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from simulator.classic_opt.memory_model import (
    load_empirical_flow_memory_costs,
    load_node_memory_capacities,
)
from simulator.classic_opt.path_mcf import load_empirical_link_capacities, print_feasibility_summary, solve_path_mcf
from simulator.classic_opt.topology_parser import build_topology_from_sequence_json
from simulator.classic_opt.traffic_parser import print_traffic_demands, save_traffic_demands, sequence_traffic_to_demands
from simulator.first_RL.config import NETWORK_CONFIG, TRAFFIC_MATRIX


def run_load_sweep(
    graph,
    all_paths,
    node_to_index,
    link_capacities,
    node_memory_capacities,
    flow_memory_costs,
    swap_probability: float,
    output_directory: Path,
   
    start_factor: float = 0.1,
    end_factor: float = 22.0,
    step: float = 1.0,
) -> list[dict]:
    sweep_results = []
    load_factors = np.round(np.arange(start_factor, end_factor + step / 2, step), 10)

    for load_factor in load_factors:
        print("\n====================================================")
        print(f"LOAD SWEEP: rho = {load_factor:.2f}")
        print("====================================================")

        scaled_demands = sequence_traffic_to_demands(
            traffic_matrix=TRAFFIC_MATRIX,
            node_to_index=node_to_index,
            scale_factor=float(load_factor),
        )

        result = solve_path_mcf(
            graph=graph,
            all_paths=all_paths,
            demands=scaled_demands,
            link_capacities=link_capacities,
            node_memory_capacities=node_memory_capacities,
            flow_memory_costs=flow_memory_costs,
            swap_probability=swap_probability,
            output_directory=output_directory,
            
            model_name=f"traffic_load_{load_factor:.2f}",
            export_model=False,
        )

        feasible = bool(result.get("feasible", False))
        offered_traffic = sum(float(demand["volume"]) for demand in scaled_demands)

        if feasible:
            maximum_link_utilization = result.get("maximum_link_utilization", 0.0)
            link_bottleneck_edge = result.get("link_bottleneck_edge")
            maximum_node_memory_utilization = result.get("maximum_node_memory_utilization", 0.0)
            memory_bottleneck_node = result.get("memory_bottleneck_node")
            objective = result.get("objective")
            average_hop_count = result.get("average_hop_count")
        else:
            maximum_link_utilization = None
            link_bottleneck_edge = None
            maximum_node_memory_utilization = None
            memory_bottleneck_node = None
            objective = None
            average_hop_count = None

        sweep_record = {
            "load_factor": float(load_factor),
            "feasible": feasible,
            "offered_traffic_pairs_per_second": offered_traffic,
            "objective": objective,
            "average_hop_count": average_hop_count,
            "maximum_link_utilization": maximum_link_utilization,
            "link_bottleneck_edge": link_bottleneck_edge,
            "maximum_node_memory_utilization": maximum_node_memory_utilization,
            "memory_bottleneck_node": memory_bottleneck_node,
            "status": result.get("status"),
        }

        sweep_results.append(sweep_record)

        print(
            f"[SWEEP] rho={load_factor:.2f}, offered={offered_traffic:.6f} pairs/s, "
            f"feasible={feasible}"
        )

        if feasible:
            print(f"[SWEEP] max link utilization={100 * maximum_link_utilization:.4f}%")
            print(f"[SWEEP] link bottleneck={link_bottleneck_edge}")
            print(f"[SWEEP] max memory utilization={100 * maximum_node_memory_utilization:.4f}%")
            print(f"[SWEEP] memory bottleneck={memory_bottleneck_node}")
        else:
            print(f"[SWEEP] Traffic becomes infeasible at rho={load_factor:.4f}")
            break

    return sweep_results


def save_load_sweep_results(sweep_results: list[dict], output_file: str | Path) -> None:
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "load_factor",
        "feasible",
        "offered_traffic_pairs_per_second",
        "objective",
        "average_hop_count",
        "maximum_link_utilization",
        "link_bottleneck_edge",
        "maximum_node_memory_utilization",
        "memory_bottleneck_node",
        "status",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()

        for result in sweep_results:
            row = result.copy()
            edge = row.get("link_bottleneck_edge")

            if edge is not None:
                row["link_bottleneck_edge"] = f"{edge[0]}-{edge[1]}"

            writer.writerow(row)

    print(f"[SWEEP] Summary saved in {output_path}")


def print_load_sweep_summary(sweep_results: list[dict]) -> None:
    feasible_results = [result for result in sweep_results if result["feasible"]]
    infeasible_results = [result for result in sweep_results if not result["feasible"]]

    print("\n================ LOAD SWEEP SUMMARY ================\n")

    if feasible_results:
        maximum_feasible = max(feasible_results, key=lambda result: result["load_factor"])

        print(f"Maximum tested feasible load factor: {maximum_feasible['load_factor']:.2f}")
        print(
            f"Corresponding offered traffic: "
            f"{maximum_feasible['offered_traffic_pairs_per_second']:.6f} pairs/s"
        )

        if maximum_feasible["maximum_link_utilization"] is not None:
            print(
                f"Maximum link utilization: "
                f"{100 * maximum_feasible['maximum_link_utilization']:.4f}%"
            )
            print(f"Link bottleneck: {maximum_feasible['link_bottleneck_edge']}")

        if maximum_feasible["maximum_node_memory_utilization"] is not None:
            print(
                f"Maximum memory utilization: "
                f"{100 * maximum_feasible['maximum_node_memory_utilization']:.4f}%"
            )
            print(f"Memory bottleneck node: {maximum_feasible['memory_bottleneck_node']}")

    if infeasible_results:
        first_infeasible = min(infeasible_results, key=lambda result: result["load_factor"])
        print(f"First infeasible load factor: {first_infeasible['load_factor']:.2f}")
    else:
        print("No infeasible point was found in the tested range.")


def main() -> None:
    baseline_directory = Path(__file__).resolve().parent
    generated_directory = baseline_directory / "generated"
    solutions_directory = baseline_directory / "solutions"

    generated_directory.mkdir(parents=True, exist_ok=True)
    solutions_directory.mkdir(parents=True, exist_ok=True)

    topology_output_file = generated_directory / "topology_db.json"
    traffic_output_file = generated_directory / "traffic_db.json"
    link_capacity_file = generated_directory / "empirical_link_capacities.json"
    node_memory_capacity_file = generated_directory / "empirical_node_memory_capacities.json"
    flow_memory_cost_file = (
    generated_directory
    / "empirical_flow_memory_costs.json"
)

    k_paths = 1
    directed = False
    traffic_scale_factor = 1.0
    swap_probability = 0.64
    default_holding_time_s = 1.0

    graph, all_paths, node_to_index, index_to_node = build_topology_from_sequence_json(
        sequence_topology_path=NETWORK_CONFIG,
        output_json_path=topology_output_file,
        k_paths=k_paths,
        directed=directed,
    )

    print("\n[TOPOLOGY] Node mapping:")

    for node_index, node_name in index_to_node.items():
        print(f"  {node_index}: {node_name}")

    demands = sequence_traffic_to_demands(
        traffic_matrix=TRAFFIC_MATRIX,
        node_to_index=node_to_index,
        scale_factor=traffic_scale_factor,
    )

    save_traffic_demands(demands=demands, json_output_path=traffic_output_file)
    print_traffic_demands(demands)

    link_capacities = load_empirical_link_capacities(
        capacity_file_path=link_capacity_file,
        node_to_index=node_to_index,
        directed=directed,
    )

    node_memory_capacities = load_node_memory_capacities(
        input_file=node_memory_capacity_file,
        node_to_index=node_to_index,
    )

    flow_memory_costs = (
    load_empirical_flow_memory_costs(
        input_file=(
            flow_memory_cost_file
        ),
        node_to_index=node_to_index,
    )
)
    print("\n================ EMPIRICAL LINK CAPACITIES ================\n")

    for edge, capacity in sorted(link_capacities.items()):
        print(
            f"{index_to_node[edge[0]]} <-> {index_to_node[edge[1]]}: "
            f"{capacity:.6f} pairs/s"
        )

    print("\n================ NODE MEMORY CAPACITIES ================\n")

    for node_index, capacity in sorted(node_memory_capacities.items()):
        print(f"{index_to_node[node_index]}: {capacity:.0f} memories")

    result = solve_path_mcf(
    graph=graph,
    all_paths=all_paths,
    demands=demands,
    link_capacities=link_capacities,
    node_memory_capacities=(
        node_memory_capacities
    ),
    flow_memory_costs=(
        flow_memory_costs
    ),
    swap_probability=(
        swap_probability
    ),
    output_directory=(
        solutions_directory
    ),
    export_model=True,
    model_name=(
        "traffic_without_purification"
    ),
)

    print_feasibility_summary(result)

    sweep_results = run_load_sweep(
        graph=graph,
        all_paths=all_paths,
        node_to_index=node_to_index,
        link_capacities=link_capacities,
        node_memory_capacities=node_memory_capacities,
        flow_memory_costs= flow_memory_costs,
        swap_probability=swap_probability,
        output_directory=solutions_directory,
        start_factor=0.1,
        end_factor=22.0,
        step=0.1,
    )

    save_load_sweep_results(
        sweep_results=sweep_results,
        output_file=solutions_directory / "load_sweep_summary.csv",
    )

    print_load_sweep_summary(sweep_results)


if __name__ == "__main__":
    main()