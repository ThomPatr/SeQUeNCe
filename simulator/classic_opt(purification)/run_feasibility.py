from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from simulator.first_RL.config import NETWORK_CONFIG, TRAFFIC_MATRIX
from simulator.classic_opt.fidelity_model import load_link_fidelities
from simulator.classic_opt.path_mcf import load_empirical_link_capacities, print_feasibility_summary, solve_path_mcf
from simulator.classic_opt.topology_parser import build_topology_from_sequence_json
from simulator.classic_opt.traffic_parser import print_traffic_demands, save_traffic_demands, sequence_traffic_to_demands


def run_load_sweep(graph, all_paths, node_to_index, link_capacities, link_fidelities, swap_probability: float, output_directory: Path, max_purification_rounds: int = 3, start_factor: float = 0.1, end_factor: float = 22.0, step: float = 1.0) -> list[dict]:
    sweep_results = []
    load_factors = np.round(np.arange(start_factor, end_factor + step / 2, step), 10)
    for load_factor in load_factors:
        print("\n====================================================")
        print(f"LOAD SWEEP: rho = {load_factor:.2f}")
        print("====================================================")
        scaled_demands = sequence_traffic_to_demands(traffic_matrix=TRAFFIC_MATRIX, node_to_index=node_to_index, scale_factor=float(load_factor))
        result = solve_path_mcf(graph=graph, all_paths=all_paths, demands=scaled_demands, link_capacities=link_capacities, swap_probability=swap_probability, output_directory=output_directory, model_name=f"traffic_load_{load_factor:.2f}", export_model=False, link_fidelities=link_fidelities, max_purification_rounds=max_purification_rounds, use_fidelity_constraints=True)
        feasible = bool(result.get("feasible", False))
        offered_traffic = sum(float(demand["volume"]) for demand in scaled_demands)
        if feasible:
            link_statistics = result.get("link_statistics", [])
            maximum_utilization = max((float(link["utilization"]) for link in link_statistics), default=0.0)
            bottleneck = max(link_statistics, key=lambda link: link["utilization"], default=None)
            objective, average_hop_count = result.get("objective"), result.get("average_hop_count")
            bottleneck_edge = tuple(bottleneck["edge"]) if bottleneck is not None else None
        else:
            maximum_utilization = bottleneck_edge = objective = average_hop_count = None
        sweep_results.append({"load_factor": float(load_factor), "feasible": feasible, "offered_traffic_pairs_per_second": offered_traffic, "objective": objective, "average_hop_count": average_hop_count, "maximum_link_utilization": maximum_utilization, "bottleneck_edge": bottleneck_edge, "status": result.get("status")})
        print(f"[SWEEP] rho={load_factor:.2f}, offered={offered_traffic:.6f} pairs/s, feasible={feasible}")
        if feasible:
            print(f"[SWEEP] max utilization={100 * maximum_utilization:.4f}%")
            print(f"[SWEEP] bottleneck={bottleneck_edge}")
        else:
            print(f"[SWEEP] Traffic becomes infeasible at rho={load_factor:.4f}")
            break
    return sweep_results


def save_load_sweep_results(sweep_results: list[dict], output_file: str | Path) -> None:
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["load_factor", "feasible", "offered_traffic_pairs_per_second", "objective", "average_hop_count", "maximum_link_utilization", "bottleneck_edge", "status"]
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for result in sweep_results:
            row = result.copy()
            if row["bottleneck_edge"] is not None: row["bottleneck_edge"] = f"{row['bottleneck_edge'][0]}-{row['bottleneck_edge'][1]}"
            writer.writerow(row)
    print(f"[SWEEP] Summary saved in {output_path}")


def print_load_sweep_summary(sweep_results: list[dict]) -> None:
    feasible_results = [result for result in sweep_results if result["feasible"]]
    infeasible_results = [result for result in sweep_results if not result["feasible"]]
    print("\n================ LOAD SWEEP SUMMARY ================\n")
    if feasible_results:
        maximum_feasible = max(feasible_results, key=lambda result: result["load_factor"])
        print(f"Maximum tested feasible load factor: {maximum_feasible['load_factor']:.2f}")
        print(f"Corresponding offered traffic: {maximum_feasible['offered_traffic_pairs_per_second']:.6f} pairs/s")
        if maximum_feasible["maximum_link_utilization"] is not None: print(f"Maximum link utilization: {100 * maximum_feasible['maximum_link_utilization']:.4f}%")
    if infeasible_results:
        first_infeasible = min(infeasible_results, key=lambda result: result["load_factor"])
        print(f"First infeasible load factor: {first_infeasible['load_factor']:.2f}")
    else: print("No infeasible point was found in the tested range.")


def main() -> None:
    baseline_directory = Path(__file__).resolve().parent
    generated_directory = baseline_directory / "generated"
    solutions_directory = baseline_directory / "solutions"
    generated_directory.mkdir(parents=True, exist_ok=True)
    solutions_directory.mkdir(parents=True, exist_ok=True)

    topology_output_file = generated_directory / "topology_db.json"
    traffic_output_file = generated_directory / "traffic_db.json"
    capacity_file = generated_directory / "empirical_link_capacities.json"
    fidelity_file = baseline_directory / "link_fidelities.json"

    k_paths, directed, traffic_scale_factor = 1, False, 1.0
    swap_probability, max_purification_rounds = 0.64, 3

    graph, all_paths, node_to_index, index_to_node = build_topology_from_sequence_json(sequence_topology_path=NETWORK_CONFIG, output_json_path=topology_output_file, k_paths=k_paths, directed=directed)
    print("\n[TOPOLOGY] Node mapping:")
    for node_index, node_name in index_to_node.items(): print(f"  {node_index}: {node_name}")

    demands = sequence_traffic_to_demands(traffic_matrix=TRAFFIC_MATRIX, node_to_index=node_to_index, scale_factor=traffic_scale_factor)
    save_traffic_demands(demands=demands, json_output_path=traffic_output_file)
    print_traffic_demands(demands)

    link_capacities = load_empirical_link_capacities(capacity_file_path=capacity_file, node_to_index=node_to_index, directed=directed)
    link_fidelities = load_link_fidelities(fidelity_file_path=fidelity_file, node_to_index=node_to_index)
  
    print("\n================ EMPIRICAL LINK CAPACITIES ================\n")
    for edge, capacity in sorted(link_capacities.items()): print(f"{index_to_node[edge[0]]} <-> {index_to_node[edge[1]]}: {capacity:.6f} pairs/s")
    print("\n================ ELEMENTARY LINK FIDELITIES ================\n")
    for edge, fidelity in sorted(link_fidelities.items()): print(f"{index_to_node[edge[0]]} <-> {index_to_node[edge[1]]}: {fidelity:.6f}")

    result = solve_path_mcf(graph=graph, all_paths=all_paths, demands=demands, link_capacities=link_capacities, swap_probability=swap_probability, output_directory=solutions_directory, export_model=True, model_name="traffic_feasibility", link_fidelities=link_fidelities, max_purification_rounds=max_purification_rounds, use_fidelity_constraints=True)
    print_feasibility_summary(result)

    sweep_results = run_load_sweep(graph=graph, all_paths=all_paths, node_to_index=node_to_index, link_capacities=link_capacities, link_fidelities=link_fidelities, swap_probability=swap_probability, output_directory=solutions_directory, max_purification_rounds=max_purification_rounds, start_factor=0.1, end_factor=22.0, step=1.0)
    save_load_sweep_results(sweep_results=sweep_results, output_file=solutions_directory / "load_sweep_summary.csv")
    print_load_sweep_summary(sweep_results)


if __name__ == "__main__": main()