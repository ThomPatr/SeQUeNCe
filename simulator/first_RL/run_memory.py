from __future__ import annotations

import argparse
import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from sequence.components.memory import MemoryArray
from sequence.topology import node as topology_node
from sequence.topology.router_net_topo import RouterNetTopo

from simulator.first_RL.apps.node_traffic_app import NodeTrafficApp
from simulator.first_RL.config import NETWORK_CONFIG, TRAFFIC_MATRIX
from simulator.first_RL.instrumentation.tracking import (
    instrument_resource_managers,
    patch_generation_classes,
)
from simulator.first_RL.metrics.link_metrics import reset_link_metrics
from simulator.first_RL.metrics.memory_occupancy_metrics import (
    close_open_memory_occupations,
    get_memory_occupancy_records,
    reset_memory_occupancy_metrics,
)
from simulator.first_RL.metrics.protocol_metrics import reset_protocol_metrics
from simulator.first_RL.physics.parameters import set_parameters
from simulator.first_RL.topology.custom_memory_array import CustomMemoryArray


topology_node.MemoryArray = CustomMemoryArray

DEFAULT_DURATION_S = 120.0
DEFAULT_RHO = 1.0
DEFAULT_BASE_SEED = 1000
DEFAULT_MAX_PARALLEL_SESSIONS = 10
DEFAULT_RESERVATION_DURATION_S = 5.0
DEFAULT_RESERVATION_SETUP_MARGIN_S = 1.0
DEFAULT_TARGET_FIDELITY: float | None = None


def save_json(data: Any, output_file: str | Path) -> Path:
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)
    print(f"[OUTPUT] Flow memory costs saved in {output_path}")
    return output_path


def scale_single_demand(demand: dict[str, Any], rho: float) -> dict[str, Any]:
    if rho <= 0:
        raise ValueError("rho must be positive.")

    scaled = deepcopy(demand)

    if "interval_s" in scaled:
        interval_s = float(scaled["interval_s"])
        if interval_s <= 0:
            raise ValueError(f"Invalid interval_s={interval_s} in traffic demand.")
        scaled["interval_s"] = interval_s / rho
    elif "arrival_rate" in scaled:
        scaled["arrival_rate"] = float(scaled["arrival_rate"]) * rho
    elif "arrival_rate_requests_per_s" in scaled:
        scaled["arrival_rate_requests_per_s"] = float(
            scaled["arrival_rate_requests_per_s"]
        ) * rho
    else:
        raise KeyError(
            "The traffic demand contains neither interval_s nor an "
            "arrival-rate field, so it cannot be scaled by rho."
        )

    return scaled


def build_fresh_topology(
    duration_s: float,
) -> tuple[RouterNetTopo, Any, dict[str, Any]]:
    if duration_s <= 0:
        raise ValueError("duration_s must be positive.")

    topology = RouterNetTopo(str(NETWORK_CONFIG))
    timeline = topology.get_timeline()
    timeline.stop_time = int(duration_s * 1e12)
    timeline.show_progress = True

    routers = {
        node.name: node
        for node in topology.get_nodes_by_type(
            RouterNetTopo.QUANTUM_ROUTER
        )
    }

    set_parameters(topology=topology, use_random_coherence=True)
    patch_generation_classes()
    instrument_resource_managers(topology)

    return topology, timeline, routers


def disable_purification(routers: dict[str, Any]) -> None:
    for router in routers.values():
        router.network_manager.enable_purification = False


def get_delivered_pairs(
    application: NodeTrafficApp,
    destination: str,
) -> int:
    return sum(
        int(session.get("delivered_pairs", 0))
        for session in application.history
        if session.get("dst") == destination
    )


def aggregate_memory_seconds_by_node(
    records: list[dict[str, Any]],
) -> dict[str, float]:
    memory_seconds_by_node: dict[str, float] = defaultdict(float)

    for record in records:
        node_name = str(record["node"])
        duration_s = float(record.get("duration_s", 0.0))
        if duration_s < 0:
            raise ValueError(
                f"Negative memory occupation duration for node {node_name}."
            )
        memory_seconds_by_node[node_name] += duration_s

    return dict(memory_seconds_by_node)


def run_single_flow(
    source: str,
    destination: str,
    demand: dict[str, Any],
    duration_s: float,
    rho: float,
    seed: int,
    max_parallel_sessions: int,
    reservation_duration_s: float,
    reservation_setup_margin_s: float,
    target_fidelity_override: float | None,
) -> dict[str, Any]:
    print("\n" + "=" * 72)
    print(f"FLOW MEMORY CALIBRATION: {source} -> {destination}")
    print("=" * 72)

    reset_link_metrics()
    reset_protocol_metrics()
    reset_memory_occupancy_metrics()

    _, timeline, routers = build_fresh_topology(duration_s=duration_s)

    if source not in routers:
        raise KeyError(f"Unknown source router: {source}")
    if destination not in routers:
        raise KeyError(f"Unknown destination router: {destination}")

    disable_purification(routers)

    scaled_demand = scale_single_demand(demand=demand, rho=rho)
    if target_fidelity_override is not None:
        scaled_demand["target_fidelity"] = float(target_fidelity_override)

    application = NodeTrafficApp(
        node=routers[source],
        traffic_demands={destination: scaled_demand},
        start_offset_s=0.0,
        max_parallel_sessions_per_flow=max_parallel_sessions,
        reservation_duration_s=reservation_duration_s,
        reservation_setup_margin_s=reservation_setup_margin_s,
        random_seed=seed,
        verbose=False,
    )

    application.schedule_initial_events()

    timeline.init()
    timeline.run()

    close_open_memory_occupations(
        end_time_ps=timeline.now(),
        reason="simulation_end",
    )

    records = get_memory_occupancy_records()
    delivered_pairs = get_delivered_pairs(
        application=application,
        destination=destination,
    )
    memory_seconds_by_node = aggregate_memory_seconds_by_node(records)

    if delivered_pairs <= 0:
        print(
            f"[FLOW] {source}-{destination}: no delivered pairs; "
            "the memory-cost coefficient is undefined."
        )
        return {
            "valid": False,
            "reason": "no_delivered_pairs",
            "source": source,
            "destination": destination,
            "delivered_pairs": 0,
            "memory_occupancy_records": len(records),
            "nodes": {},
            "configuration": {
                "duration_s": duration_s,
                "rho": rho,
                "seed": seed,
                "target_fidelity": scaled_demand.get("target_fidelity"),
            },
        }

    node_results = {
        node_name: {
            "total_memory_seconds": total_memory_seconds,
            "memory_seconds_per_delivered_pair": (
                total_memory_seconds / delivered_pairs
            ),
        }
        for node_name, total_memory_seconds
        in sorted(memory_seconds_by_node.items())
    }

    print(
        f"[FLOW] {source}-{destination}: delivered={delivered_pairs}, "
        f"records={len(records)}"
    )

    for node_name, node_record in node_results.items():
        print(
            f"  {node_name}: "
            f"{node_record['memory_seconds_per_delivered_pair']:.9f} "
            "memory-s/delivered-pair"
        )

    return {
        "valid": True,
        "source": source,
        "destination": destination,
        "delivered_pairs": delivered_pairs,
        "memory_occupancy_records": len(records),
        "nodes": node_results,
        "configuration": {
            "duration_s": duration_s,
            "rho": rho,
            "seed": seed,
            "target_fidelity": scaled_demand.get("target_fidelity"),
        },
    }


def calibrate_flow_memory_costs(
    duration_s: float,
    rho: float,
    base_seed: int,
    target_fidelity: float | None,
    max_parallel_sessions: int,
    reservation_duration_s: float,
    reservation_setup_margin_s: float,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    flow_index = 0

    for source, destinations in TRAFFIC_MATRIX.items():
        for destination, demand in destinations.items():
            flow_result = run_single_flow(
                source=source,
                destination=destination,
                demand=demand,
                duration_s=duration_s,
                rho=rho,
                seed=base_seed + flow_index,
                max_parallel_sessions=max_parallel_sessions,
                reservation_duration_s=reservation_duration_s,
                reservation_setup_margin_s=reservation_setup_margin_s,
                target_fidelity_override=target_fidelity,
            )
            results[f"{source}-{destination}"] = flow_result
            flow_index += 1

    return results


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run isolated SeQUeNCe simulations for every traffic flow "
            "and generate only empirical_flow_memory_costs.json."
        )
    )
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_S)
    parser.add_argument("--rho", type=float, default=DEFAULT_RHO)
    parser.add_argument("--seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument(
        "--target-fidelity",
        type=float,
        default=DEFAULT_TARGET_FIDELITY,
        help=(
            "Optional common target fidelity. If omitted, each flow "
            "keeps the value configured in TRAFFIC_MATRIX."
        ),
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=DEFAULT_MAX_PARALLEL_SESSIONS,
    )
    parser.add_argument(
        "--reservation-duration",
        type=float,
        default=DEFAULT_RESERVATION_DURATION_S,
    )
    parser.add_argument(
        "--reservation-setup-margin",
        type=float,
        default=DEFAULT_RESERVATION_SETUP_MARGIN_S,
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if args.target_fidelity is not None and not 0.0 <= args.target_fidelity <= 1.0:
        raise ValueError("target_fidelity must belong to [0, 1].")

    script_directory = Path(__file__).resolve().parent
    output_file = (
        args.output
        if args.output is not None
        else (
            script_directory.parent
            / "classic_opt"
            / "generated"
            / "empirical_flow_memory_costs.json"
        )
    )

    results = calibrate_flow_memory_costs(
        duration_s=args.duration,
        rho=args.rho,
        base_seed=args.seed,
        target_fidelity=args.target_fidelity,
        max_parallel_sessions=args.parallel,
        reservation_duration_s=args.reservation_duration,
        reservation_setup_margin_s=args.reservation_setup_margin,
    )

    valid_flows = sum(
        1 for result in results.values() if result.get("valid", False)
    )
    invalid_flows = len(results) - valid_flows

    output = {
        "metadata": {
            "description": (
                "Empirical occupied memory-seconds per delivered "
                "end-to-end Bell pair, measured with purification disabled."
            ),
            "duration_s_per_flow": args.duration,
            "rho": args.rho,
            "base_seed": args.seed,
            "target_fidelity_override": args.target_fidelity,
            "valid_flows": valid_flows,
            "invalid_flows": invalid_flows,
        },
        "flows": results,
    }

    save_json(data=output, output_file=output_file)

    print("\n" + "=" * 72)
    print("FLOW MEMORY-COST CALIBRATION COMPLETED")
    print("=" * 72)
    print(f"Valid flows  : {valid_flows}")
    print(f"Invalid flows: {invalid_flows}")
    print(f"Output       : {output_file}")


if __name__ == "__main__":
    main()