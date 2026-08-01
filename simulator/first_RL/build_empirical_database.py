from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

from sequence.components.memory import MemoryArray
from sequence.topology import node as topology_node
from sequence.topology.router_net_topo import RouterNetTopo

from simulator.first_RL.apps.link_calibration_app import LinkCalibrationApp
from simulator.first_RL.apps.node_traffic_app import NodeTrafficApp
from simulator.first_RL.apps.traffic_utils import compute_configured_offered_traffic, scale_poisson_traffic
from simulator.first_RL.config import NETWORK_CONFIG, NODE_HW, PHYSICAL_LINKS, TRAFFIC_MATRIX
from simulator.first_RL.instrumentation.tracking import instrument_resource_managers, patch_generation_classes
from simulator.first_RL.metrics.link_metrics import LINK_METRICS, reset_link_metrics
from simulator.first_RL.metrics.memory_occupancy_metrics import close_open_memory_occupations, get_memory_occupancy_records, reset_memory_occupancy_metrics
from simulator.first_RL.physics.link_utils import normalize_link
from simulator.first_RL.physics.parameters import set_parameters
from simulator.first_RL.topology.custom_memory_array import CustomMemoryArray

try:
    from simulator.first_RL.metrics.protocol_metrics import build_flow_protocol_statistics, reset_protocol_metrics
except ImportError:
    build_flow_protocol_statistics = None

    def reset_protocol_metrics() -> None:
        return None


topology_node.MemoryArray = CustomMemoryArray

DEFAULT_LINK_DURATION_S = 10.0
DEFAULT_NETWORK_DURATION_S = 300.0
DEFAULT_SETUP_MARGIN_S = 0.05
DEFAULT_RESERVED_MEMORIES = 10
DEFAULT_TARGET_FIDELITY = 0.65
DEFAULT_LOAD_FACTOR = 1.0
DEFAULT_TRAFFIC_SEED = 1000
DEFAULT_MIN_SAMPLES = 20


def _json_dump(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)
    print(f"[OUTPUT] {path}")


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def canonical_link(node_a: str, node_b: str) -> tuple[str, str]:
    return normalize_link(node_a, node_b)


def _build_fresh_topology(duration_s: float) -> tuple[RouterNetTopo, Any, dict[str, Any]]:
    topology = RouterNetTopo(str(NETWORK_CONFIG))
    timeline = topology.get_timeline()
    timeline.stop_time = int(duration_s * 1e12)
    timeline.show_progress = True
    routers = {node.name: node for node in topology.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER)}
    set_parameters(topology=topology, use_random_coherence=True)
    patch_generation_classes()
    instrument_resource_managers(topology)
    return topology, timeline, routers


def _disable_purification(routers: dict[str, Any]) -> None:
    for router in routers.values():
        if hasattr(router.network_manager, "enable_purification"):
            router.network_manager.enable_purification = False


def _extract_link_result(node_a: str, node_b: str, active_duration_s: float, application: LinkCalibrationApp) -> dict[str, Any]:
    link = canonical_link(node_a, node_b)
    stats = LINK_METRICS.get(link, {})
    attempts = int(stats.get("eg_attempts", 0))
    successes = int(stats.get("eg_successes", 0))
    observed_creations = int(stats.get("observed_creations", 0))
    pair_records = stats.get("pair_records", [])
    completed_lifetimes_ps = [record["observed_lifetime_ps"] for record in pair_records if record.get("observed_lifetime_ps") is not None]
    average_lifetime_s = mean(completed_lifetimes_ps) * 1e-12 if completed_lifetimes_ps else None
    return {
        "node_a": link[0],
        "node_b": link[1],
        "active_duration_s": active_duration_s,
        "eg_attempts": attempts,
        "eg_successes": successes,
        "observed_creations": observed_creations,
        "p_generation": successes / attempts if attempts else 0.0,
        "attempt_rate_per_second": attempts / active_duration_s if active_duration_s > 0 else 0.0,
        "raw_creation_rate_pairs_per_second": successes / active_duration_s if active_duration_s > 0 else 0.0,
        "delivered_pairs": int(application.delivered_pairs),
        "service_capacity_pairs_per_second": application.delivered_pairs / active_duration_s if active_duration_s > 0 else 0.0,
        "average_observed_lifetime_s": average_lifetime_s,
    }


def calibrate_all_links(duration_s: float, setup_margin_s: float, reserved_memories: int, target_fidelity: float) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for node_a, node_b in sorted(PHYSICAL_LINKS):
        print("\n" + "=" * 72)
        print(f"PHASE A — LINK CALIBRATION: {node_a} <-> {node_b}")
        print("=" * 72)
        reset_link_metrics()
        reset_memory_occupancy_metrics()
        reset_protocol_metrics()
        _, timeline, routers = _build_fresh_topology(duration_s)
        _disable_purification(routers)
        for endpoint in (node_a, node_b):
            memory_array = routers[endpoint].get_components_by_type(MemoryArray)[0]
            if len(memory_array) == 0:
                raise RuntimeError(f"Router {endpoint} has no quantum memories.")
            print(f"[MEMORY] {endpoint}: size={len(memory_array)}, coherence={NODE_HW[endpoint]['memo_expire']} s")
        application = LinkCalibrationApp(node=routers[node_a], destination=node_b, reservation_start_s=setup_margin_s, reservation_end_s=duration_s, reserved_memories=reserved_memories, target_fidelity=target_fidelity)
        application.schedule_request()
        timeline.init()
        timeline.run()
        close_open_memory_occupations(end_time_ps=timeline.now(), reason="simulation_end")
        active_duration_s = duration_s - setup_margin_s
        if active_duration_s <= 0:
            raise ValueError("duration_s must be greater than setup_margin_s.")
        result = _extract_link_result(node_a=node_a, node_b=node_b, active_duration_s=active_duration_s, application=application)
        key = f"{result['node_a']}-{result['node_b']}"
        results[key] = result
        print(f"[LINK] p_gen={result['p_generation']:.8f}, capacity={result['service_capacity_pairs_per_second']:.6f} pairs/s")
    return results


def _build_memory_statistics(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_node: dict[str, list[float]] = defaultdict(list)
    by_flow_node: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    censored = 0
    for record in records:
        if record.get("reason") == "simulation_end":
            censored += 1
            continue
        duration_s = float(record["duration_s"])
        if duration_s < 0:
            continue
        node_name = str(record["node"])
        by_node[node_name].append(duration_s)
        source = record.get("source")
        destination = record.get("destination")
        if source is not None and destination is not None:
            by_flow_node[(str(source), str(destination), node_name)].append(duration_s)
    node_statistics = []
    for node_name, durations in sorted(by_node.items()):
        node_statistics.append({"node": node_name, "samples": len(durations), "average_holding_time_s": mean(durations), "median_holding_time_s": median(durations), "p95_holding_time_s": _percentile(durations, 0.95), "maximum_holding_time_s": max(durations)})
    flow_node_statistics = []
    for (source, destination, node_name), durations in sorted(by_flow_node.items()):
        flow_node_statistics.append({"source": source, "destination": destination, "node": node_name, "samples": len(durations), "average_holding_time_s": mean(durations), "median_holding_time_s": median(durations), "p95_holding_time_s": _percentile(durations, 0.95), "maximum_holding_time_s": max(durations)})
    global_durations = [duration for durations in by_node.values() for duration in durations]
    return {"total_completed_records": len(global_durations), "censored_records_at_simulation_end": censored, "global_average_holding_time_s": mean(global_durations) if global_durations else None, "node_statistics": node_statistics, "flow_node_statistics": flow_node_statistics}


def _build_compact_holding_time_database(memory_statistics: dict[str, Any], min_samples: int) -> dict[str, Any]:
    node_fallbacks = {record["node"]: {"samples": record["samples"], "average_holding_time_s": record["average_holding_time_s"]} for record in memory_statistics["node_statistics"]}
    global_average = memory_statistics.get("global_average_holding_time_s")
    flows: dict[str, dict[str, Any]] = defaultdict(dict)
    for record in memory_statistics["flow_node_statistics"]:
        flow_key = f"{record['source']}-{record['destination']}"
        if int(record["samples"]) >= min_samples:
            holding_time = float(record["average_holding_time_s"])
            source = "flow_node"
        else:
            fallback = node_fallbacks.get(record["node"])
            holding_time = float(fallback["average_holding_time_s"]) if fallback is not None else global_average
            source = "node_fallback" if fallback is not None else "global_fallback"
        flows[flow_key][record["node"]] = {"holding_time_s": holding_time, "samples": int(record["samples"]), "source": source}
    return {"minimum_samples": min_samples, "global_average_holding_time_s": global_average, "node_fallbacks": node_fallbacks, "flows": dict(flows)}


def _extract_application_statistics(applications: list[NodeTrafficApp]) -> list[dict[str, Any]]:
    results = []
    for application in applications:
        for destination, stats in application.flow_statistics.items():
            arrivals = int(stats.get("arrival_events", 0))
            admitted = int(stats.get("admitted_requests", 0))
            blocked = int(stats.get("blocked_requests", 0))
            requested_pairs = int(stats.get("requested_pairs", 0))
            delivered_pairs = int(stats.get("delivered_pairs", 0))
            results.append({"source": application.node.name, "destination": destination, "arrival_events": arrivals, "admitted_requests": admitted, "blocked_requests": blocked, "blocking_probability": blocked / arrivals if arrivals else 0.0, "requested_pairs": requested_pairs, "delivered_pairs": delivered_pairs, "delivery_ratio": delivered_pairs / requested_pairs if requested_pairs else 0.0})
    return results


def run_full_network_calibration(duration_s: float, rho: float, base_traffic_seed: int, target_fidelity: float, max_parallel_sessions: int, reservation_duration_s: float, reservation_setup_margin_s: float) -> dict[str, Any]:
    print("\n" + "=" * 72)
    print("PHASE B — FULL-NETWORK MEMORY-OCCUPANCY CALIBRATION")
    print("=" * 72)
    reset_link_metrics()
    reset_memory_occupancy_metrics()
    reset_protocol_metrics()
    _, timeline, routers = _build_fresh_topology(duration_s)
    _disable_purification(routers)
    simulation_traffic = scale_poisson_traffic(TRAFFIC_MATRIX, rho)
    configured_offered_traffic = compute_configured_offered_traffic(simulation_traffic)
    applications: list[NodeTrafficApp] = []
    for app_index, source_name in enumerate(simulation_traffic):
        application = NodeTrafficApp(node=routers[source_name], traffic_demands=simulation_traffic[source_name], start_offset_s=0.5 * app_index, max_parallel_sessions_per_flow=max_parallel_sessions, reservation_duration_s=reservation_duration_s, reservation_setup_margin_s=reservation_setup_margin_s, random_seed=base_traffic_seed + app_index, verbose=False)
        application.schedule_initial_events()
        applications.append(application)
    timeline.init()
    timeline.run()
    close_open_memory_occupations(end_time_ps=timeline.now(), reason="simulation_end")
    records = get_memory_occupancy_records()
    memory_statistics = _build_memory_statistics(records)
    application_statistics = _extract_application_statistics(applications)
    protocol_statistics = []
    if build_flow_protocol_statistics is not None:
        try:
            protocol_statistics = build_flow_protocol_statistics()
        except TypeError:
            protocol_statistics = build_flow_protocol_statistics(applications)
    node_memory_capacities = {}
    for node_name, router in routers.items():
        memory_array = router.get_components_by_type(MemoryArray)[0]
        node_memory_capacities[node_name] = len(memory_array)
    return {"configuration": {"simulation_duration_s": duration_s, "rho": rho, "base_traffic_seed": base_traffic_seed, "target_fidelity": target_fidelity, "enable_purification": False, "configured_offered_traffic_pairs_per_second": configured_offered_traffic}, "node_memory_capacities": node_memory_capacities, "memory_occupancy_records": records, "memory_statistics": memory_statistics, "application_statistics": application_statistics, "protocol_statistics": protocol_statistics}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the empirical databases required by the LP model. The script performs isolated saturated link calibration and a full-network calibration for memory holding times.")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--link-duration", type=float, default=DEFAULT_LINK_DURATION_S)
    parser.add_argument("--network-duration", type=float, default=DEFAULT_NETWORK_DURATION_S)
    parser.add_argument("--rho", type=float, default=DEFAULT_LOAD_FACTOR)
    parser.add_argument("--seed", type=int, default=DEFAULT_TRAFFIC_SEED)
    parser.add_argument("--target-fidelity", type=float, default=DEFAULT_TARGET_FIDELITY)
    parser.add_argument("--setup-margin", type=float, default=DEFAULT_SETUP_MARGIN_S)
    parser.add_argument("--reserved-memories", type=int, default=DEFAULT_RESERVED_MEMORIES)
    parser.add_argument("--parallel", type=int, default=10)
    parser.add_argument("--reservation-duration", type=float, default=5.0)
    parser.add_argument("--reservation-setup-margin", type=float, default=1.0)
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    parser.add_argument("--skip-links", action="store_true")
    parser.add_argument("--skip-network", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    script_directory = Path(__file__).resolve().parent
    output_directory = args.output if args.output is not None else script_directory.parent / "classic_opt" / "generated"
    output_directory.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"link_calibration_executed": not args.skip_links, "network_calibration_executed": not args.skip_network, "outputs": {}}
    if not args.skip_links:
        link_results = calibrate_all_links(duration_s=args.link_duration, setup_margin_s=args.setup_margin, reserved_memories=args.reserved_memories, target_fidelity=min(args.target_fidelity, 0.50))
        link_path = output_directory / "empirical_link_capacities.json"
        _json_dump(link_results, link_path)
        manifest["outputs"]["empirical_link_capacities"] = str(link_path)
    if not args.skip_network:
        network_results = run_full_network_calibration(duration_s=args.network_duration, rho=args.rho, base_traffic_seed=args.seed, target_fidelity=args.target_fidelity, max_parallel_sessions=args.parallel, reservation_duration_s=args.reservation_duration, reservation_setup_margin_s=args.reservation_setup_margin)
        memory_stats_path = output_directory / "memory_occupancy_statistics.json"
        _json_dump(network_results["memory_statistics"], memory_stats_path)
        holding_db = _build_compact_holding_time_database(network_results["memory_statistics"], min_samples=args.min_samples)
        holding_path = output_directory / "empirical_memory_holding_times.json"
        _json_dump(holding_db, holding_path)
        capacities_path = output_directory / "empirical_node_memory_capacities.json"
        _json_dump(network_results["node_memory_capacities"], capacities_path)
        applications_path = output_directory / "empirical_application_statistics.json"
        _json_dump(network_results["application_statistics"], applications_path)
        protocols_path = output_directory / "empirical_protocol_statistics.json"
        _json_dump(network_results["protocol_statistics"], protocols_path)
        raw_records_path = output_directory / "memory_occupancy_records.json"
        _json_dump(network_results["memory_occupancy_records"], raw_records_path)
        manifest["configuration"] = network_results["configuration"]
        manifest["outputs"].update({"memory_occupancy_statistics": str(memory_stats_path), "empirical_memory_holding_times": str(holding_path), "empirical_node_memory_capacities": str(capacities_path), "empirical_application_statistics": str(applications_path), "empirical_protocol_statistics": str(protocols_path), "memory_occupancy_records": str(raw_records_path)})
    manifest_path = output_directory / "empirical_database_manifest.json"
    _json_dump(manifest, manifest_path)
    print("\n" + "=" * 72)
    print("EMPIRICAL DATABASE BUILD COMPLETED")
    print("=" * 72)
    print(f"Output directory: {output_directory}")


if __name__ == "__main__":
    main()