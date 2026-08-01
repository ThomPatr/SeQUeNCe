from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sequence.components.memory import MemoryArray
from sequence.topology import node as topology_node
from sequence.topology.router_net_topo import RouterNetTopo

from simulator.first_RL.apps.link_calibration_app import LinkCalibrationApp
from simulator.first_RL.config import (
    NETWORK_CONFIG,
    NODE_HW,
    PHYSICAL_LINKS,
)
from simulator.first_RL.instrumentation.tracking import (
    instrument_resource_managers,
    patch_generation_classes,
)
from simulator.first_RL.metrics.link_metrics import (
    LINK_METRICS,
    reset_link_metrics,
)
from simulator.first_RL.physics.link_utils import normalize_link
from simulator.first_RL.physics.parameters import set_parameters
from simulator.first_RL.topology.custom_memory_array import (
    CustomMemoryArray,
)


# The replacement must be performed before RouterNetTopo creates the nodes.
topology_node.MemoryArray = CustomMemoryArray


DEFAULT_DURATION_S = 10.0
DEFAULT_INTERVAL_S = 0.05
DEFAULT_RESERVATION_DURATION_S = 1.0
DEFAULT_SETUP_MARGIN_S = 0.05
DEFAULT_MAX_PARALLEL_SESSIONS = 10
DEFAULT_MEMORY_SIZE = 1

# A low threshold avoids triggering purification during elementary-link
# calibration. The objective here is to measure raw link generation.
DEFAULT_TARGET_FIDELITY = 0.50


def canonical_link(node_a: str, node_b: str) -> tuple[str, str]:
    """Return the canonical representation used by LINK_METRICS."""
    return normalize_link(node_a, node_b)


def validate_link(node_a: str, node_b: str) -> tuple[str, str]:
    """
    Validate that the requested link belongs to the physical topology.
    """
    link = canonical_link(node_a, node_b)

    if link not in PHYSICAL_LINKS:
        available = "\n".join(
            f"  {left} -- {right}"
            for left, right in sorted(PHYSICAL_LINKS)
        )

        raise ValueError(
            f"{node_a} -- {node_b} is not present in PHYSICAL_LINKS.\n"
            f"Available physical links:\n{available}"
        )

    return link


def build_calibration_traffic(
    node_a: str,
    node_b: str,
    interval_s: float,
    memory_size: int,
    target_fidelity: float,
) -> dict[str, dict[str, dict[str, float | int]]]:
    """
    Build a direct, periodic and saturated traffic demand for one link.
    """
    if interval_s <= 0:
        raise ValueError("interval_s must be positive.")

    if memory_size <= 0:
        raise ValueError("memory_size must be positive.")

    if not 0.0 <= target_fidelity <= 1.0:
        raise ValueError("target_fidelity must belong to [0, 1].")

    return {
        node_a: {
            node_b: {
                "interval_s": interval_s,
                "memory_size": memory_size,
                "target_fidelity": target_fidelity,
            }
        }
    }


def get_link_statistics(
    node_a: str,
    node_b: str,
    duration_s: float,
) -> dict[str, Any]:
    """
    Extract the measurements of one calibrated physical link.
    """
    link = canonical_link(node_a, node_b)
    stats = LINK_METRICS.get(link)

    if stats is None:
        attempts = 0
        successes = 0
        observed_creations = 0
        pair_records = []
    else:
        attempts = int(stats.get("eg_attempts", 0))
        successes = int(stats.get("eg_successes", 0))
        observed_creations = int(
            stats.get("observed_creations", 0)
        )
        pair_records = stats.get("pair_records", [])

    p_generation = (
        successes / attempts
        if attempts > 0
        else 0.0
    )

    attempt_rate_per_second = (
        attempts / duration_s
        if duration_s > 0
        else 0.0
    )

    raw_creation_rate_pairs_per_second = (
    successes / duration_s
)

    completed_lifetimes_ps = [
        record["observed_lifetime_ps"]
        for record in pair_records
        if record.get("observed_lifetime_ps") is not None
    ]

    average_lifetime_s = (
        sum(completed_lifetimes_ps)
        / len(completed_lifetimes_ps)
        * 1e-12
        if completed_lifetimes_ps
        else None
    )

    return {
    "node_a": link[0],
    "node_b": link[1],
    "observation_time_s": duration_s,
    "eg_attempts": attempts,
    "eg_successes": successes,
    "observed_creations": observed_creations,
    "p_generation": p_generation,
    "attempt_rate_per_second": attempt_rate_per_second,
    "raw_creation_rate_pairs_per_second":
        raw_creation_rate_pairs_per_second,
    "completed_pair_records": len(pair_records),
    "average_observed_lifetime_s":
        average_lifetime_s,
}


def run_single_link_calibration(
    node_a: str,
    node_b: str,
    duration_s: float = DEFAULT_DURATION_S,
    interval_s: float = DEFAULT_INTERVAL_S,
    reservation_duration_s: float =
        DEFAULT_RESERVATION_DURATION_S,
    setup_margin_s: float = DEFAULT_SETUP_MARGIN_S,
    max_parallel_sessions: int =
        DEFAULT_MAX_PARALLEL_SESSIONS,
    memory_size: int = DEFAULT_MEMORY_SIZE,
    target_fidelity: float =
        DEFAULT_TARGET_FIDELITY,
) -> dict[str, Any]:
    """
    Run one independent SeQUeNCe simulation for a physical link.

    Only a direct source-destination flow is installed. Since the two
    endpoints are adjacent, no end-to-end swapping is required.
    """
    link = validate_link(node_a, node_b)

    if duration_s <= 0:
        raise ValueError("duration_s must be positive.")

    print(
        "\n"
        "============================================================\n"
        f"CALIBRATING LINK: {link[0]} <-> {link[1]}\n"
        "============================================================"
    )

    print(f"Duration             : {duration_s:.3f} s")
    print(f"Request interval     : {interval_s:.6f} s")
    print(f"Pairs per request    : {memory_size}")
    print(f"Max parallel sessions: {max_parallel_sessions}")
    print(f"Target fidelity      : {target_fidelity:.3f}")

    reset_link_metrics()

    # Each calibration uses a fresh topology and a fresh Timeline.
    network_topology = RouterNetTopo(str(NETWORK_CONFIG))
    timeline = network_topology.get_timeline()

    timeline.stop_time = int(duration_s * 1e12)
    timeline.show_progress = True

    routers = {
        node.name: node
        for node in network_topology.get_nodes_by_type(
            RouterNetTopo.QUANTUM_ROUTER
        )
    }

    if node_a not in routers:
        raise KeyError(f"Unknown router: {node_a}")

    if node_b not in routers:
        raise KeyError(f"Unknown router: {node_b}")

    # Apply exactly the same hardware and channel configuration used by
    # the complete SeQUeNCe experiment.
    set_parameters(
        topology=network_topology,
        use_random_coherence=True,
    )

    for endpoint in link:
        memory_array = routers[
            endpoint
        ].get_components_by_type(MemoryArray)[0]

        if len(memory_array) == 0:
            raise RuntimeError(
                f"Router {endpoint} has no quantum memories."
            )

        print(
            f"[MEMORY] {endpoint}: "
            f"{type(memory_array[0]).__name__}, "
            f"size={len(memory_array)}, "
            f"coherence="
            f"{NODE_HW[endpoint]['memo_expire']} s"
        )

    # Count real entanglement-generation protocol executions and track
    # elementary-pair creation/reset events.
    patch_generation_classes()
    instrument_resource_managers(network_topology)

    calibration_traffic = build_calibration_traffic(
        node_a=node_a,
        node_b=node_b,
        interval_s=interval_s,
        memory_size=memory_size,
        target_fidelity=target_fidelity,
    )

    setup_margin_s = 0.05

    application = LinkCalibrationApp(
        node=routers[node_a],
        destination=node_b,
        reservation_start_s=setup_margin_s,
        reservation_end_s=duration_s,
        reserved_memories=10,
        target_fidelity=0.5,
    )

    application.schedule_request()



    timeline.init()

    print("\n[CALIBRATION] Simulation started.")
    timeline.run()
    print("\n[CALIBRATION] Simulation completed.")

    # Effective time during which the reservation is active.
    active_duration_s = duration_s - setup_margin_s

    if active_duration_s <= 0:
        raise ValueError(
            "duration_s must be greater than setup_margin_s."
        )

    result = get_link_statistics(
        node_a=node_a,
        node_b=node_b,
        duration_s=active_duration_s,
    )

    attempts = int(result["eg_attempts"])
    successes = int(result["eg_successes"])

    p_generation = (
        successes / attempts
        if attempts > 0
        else 0.0
    )

    raw_creation_rate = (
        successes / active_duration_s
    )

    service_capacity = (
        application.delivered_pairs
        / active_duration_s
    )

    result.update(
        {
            "simulation_duration_s": duration_s,
            "setup_margin_s": setup_margin_s,
            "active_duration_s": active_duration_s,
            "p_generation": p_generation,
            "raw_creation_rate_pairs_per_second":
                raw_creation_rate,
            "delivered_pairs":
                application.delivered_pairs,
            "service_capacity_pairs_per_second":
                service_capacity,
        }
    )

    result["traffic"] = {
        "reservation_start_s": setup_margin_s,
        "reservation_end_s": duration_s,
        "reserved_memories": max_parallel_sessions,
        "target_fidelity": target_fidelity,
    }

    result["application_results"] = {
        "reservation_approved":
            application.reservation_approved,
        "delivered_pairs":
            application.delivered_pairs,
    }

    print_calibration_result(result)

    return result


def print_calibration_result(result: dict) -> None:
    print(
        "\n"
        "================ CALIBRATION RESULT ================\n"
    )

    print(
        f"Link              : "
        f"{result['node_a']} <-> {result['node_b']}"
    )
    print(
        f"Observation time  : "
        f"{result['active_duration_s']:.3f} s"
    )
    print(
        f"EG attempts       : "
        f"{result['eg_attempts']}"
    )
    print(
        f"EG successes      : "
        f"{result['eg_successes']}"
    )
    print(
        f"Observed creations: "
        f"{result['observed_creations']}"
    )
    print(
        f"p_gen             : "
        f"{result['p_generation']:.8f}"
    )
    print(
        f"Attempt rate      : "
        f"{result['attempt_rate_per_second']:.6f} "
        f"attempts/s"
    )
    print(
        f"Raw creation rate : "
        f"{result['raw_creation_rate_pairs_per_second']:.6f} "
        f"pairs/s"
    )
    print(
        f"Delivered pairs   : "
        f"{result['delivered_pairs']}"
    )
    print(
        f"Service capacity  : "
        f"{result['service_capacity_pairs_per_second']:.6f} "
        f"pairs/s"
    )

    average_lifetime = result.get(
        "average_observed_lifetime_s"
    )

    if average_lifetime is None:
        print("Average lifetime  : None")
    else:
        print(
            f"Average lifetime  : "
            f"{average_lifetime:.6f} s"
        )

    traffic = result.get("traffic", {})

    print("\nCalibration reservation:")
    print(
        f"  reservation start : "
        f"{traffic.get('reservation_start_s', 0.0):.6f} s"
    )
    print(
        f"  reservation end   : "
        f"{traffic.get('reservation_end_s', 0.0):.6f} s"
    )
    print(
        f"  reserved memories : "
        f"{traffic.get('reserved_memories', 0)}"
    )
    print(
        f"  target fidelity   : "
        f"{traffic.get('target_fidelity', 0.0):.6f}"
    )

    app_results = result.get(
        "application_results",
        {},
    )

    print("\nApplication:")
    print(
        f"  reservation approved : "
        f"{app_results.get('reservation_approved')}"
    )
    print(
        f"  delivered pairs      : "
        f"{app_results.get('delivered_pairs', 0)}"
    )

def save_single_result(
    result: dict[str, Any],
    output_directory: str | Path,
) -> Path:
    output_path = Path(output_directory)
    output_path.mkdir(parents=True, exist_ok=True)

    filename = (
        f"{result['node_a']}__{result['node_b']}.json"
    )

    result_file = output_path / filename

    with result_file.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(result, file, indent=4)

    print(f"[OUTPUT] Result saved in {result_file}")

    return result_file


def save_aggregate_capacities(
    results: list[dict[str, Any]],
    output_file: str | Path,
) -> Path:
    """
    Save the format expected by the classical optimization loader.
    """
    aggregate = {}

    for result in results:
        key = f"{result['node_a']}-{result['node_b']}"

        aggregate[key] = {
        "node_a": result["node_a"],
        "node_b": result["node_b"],
        "observation_time_s":
            result["observation_time_s"],
        "active_duration_s":
            result["active_duration_s"],
        "eg_attempts":
            result["eg_attempts"],
        "eg_successes":
            result["eg_successes"],
        "p_generation":
            result["p_generation"],
        "attempt_rate_per_second":
            result["attempt_rate_per_second"],
        "raw_creation_rate_pairs_per_second":
            result["raw_creation_rate_pairs_per_second"],
        "delivered_pairs":
            result["delivered_pairs"],
        "service_capacity_pairs_per_second":
            result["service_capacity_pairs_per_second"],
    }
    output_path = Path(output_file)
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(aggregate, file, indent=4)

    print(
        f"[OUTPUT] Aggregate capacities saved in "
        f"{output_path}"
    )

    return output_path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure the elementary entanglement-generation capacity "
            "of one or all physical SeQUeNCe links."
        )
    )

    parser.add_argument(
        "--node-a",
        type=str,
        help="First endpoint of a single link.",
    )

    parser.add_argument(
        "--node-b",
        type=str,
        help="Second endpoint of a single link.",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Calibrate all physical links sequentially.",
    )

    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help="Simulation duration for each link in seconds.",
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help="Time between application arrivals in seconds.",
    )

    parser.add_argument(
        "--parallel",
        type=int,
        default=DEFAULT_MAX_PARALLEL_SESSIONS,
        help="Maximum concurrent sessions for the calibration flow.",
    )

    parser.add_argument(
        "--reservation-duration",
        type=float,
        default=DEFAULT_RESERVATION_DURATION_S,
        help="Duration of each reservation window in seconds.",
    )

    parser.add_argument(
        "--setup-margin",
        type=float,
        default=DEFAULT_SETUP_MARGIN_S,
        help="Delay between request creation and reservation start.",
    )

    parser.add_argument(
        "--memory-size",
        type=int,
        default=DEFAULT_MEMORY_SIZE,
        help="Number of EPR pairs requested per arrival.",
    )

    parser.add_argument(
        "--target-fidelity",
        type=float,
        default=DEFAULT_TARGET_FIDELITY,
        help="Low calibration threshold used to avoid purification.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    script_directory = Path(__file__).resolve().parent

    output_directory = (
        script_directory
        / "calibration_results"
    )

    aggregate_file = (
        script_directory.parent
        / "classic_opt"
        / "generated"
        / "empirical_link_capacities.json"
    )

    if args.all:
        links_to_calibrate = sorted(PHYSICAL_LINKS)

    else:
        if args.node_a is None or args.node_b is None:
            raise ValueError(
                "Specify both --node-a and --node-b, "
                "or use --all."
            )

        links_to_calibrate = [
            validate_link(args.node_a, args.node_b)
        ]

    all_results = []
    
    for node_a, node_b in links_to_calibrate:
        result = run_single_link_calibration(
            node_a=node_a,
            node_b=node_b,
            duration_s=args.duration,
            interval_s=args.interval,
            reservation_duration_s=
                args.reservation_duration,
            setup_margin_s=args.setup_margin,
            max_parallel_sessions=args.parallel,
            memory_size=args.memory_size,
            target_fidelity=args.target_fidelity,
        )

        all_results.append(result)

        save_single_result(
            result=result,
            output_directory=output_directory,
        )

    save_aggregate_capacities(
        results=all_results,
        output_file=aggregate_file,
    )


if __name__ == "__main__":
    main()