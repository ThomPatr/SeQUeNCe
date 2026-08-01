from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from collections import defaultdict
from statistics import mean, median

from simulator.first_RL.metrics.memory_occupancy_metrics import ( get_memory_occupancy_records,)
import numpy as np

from simulator.first_RL.metrics.link_metrics import (ACTIVE_PAIRS,  LINK_METRICS,)
from simulator.first_RL.metrics.protocol_metrics import (
    build_flow_protocol_statistics,
)

def _make_json_serializable(value: Any) -> Any:
    """
    Convert common NumPy, tuple and set values into JSON-compatible types.
    """
    if isinstance(value, dict):
        return {str(key): _make_json_serializable(item)for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_make_json_serializable(item) for item in value]

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        return float(value)

    if isinstance(value, np.ndarray):
        return value.tolist()

    return value


def save_json(data: Any,output_file: str | Path,) -> Path:
    """
    Save arbitrary JSON-compatible experiment data.
    """
    output_path = Path(output_file)
    output_path.parent.mkdir( parents=True,  exist_ok=True,)

    serializable_data = _make_json_serializable( data)

    with output_path.open("w",encoding="utf-8",) as file: json.dump(serializable_data,file, indent=4,)
    return output_path

def _percentile(
    values: list[float],
    probability: float,
) -> float | None:
    if not values:
        return None

    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower

    return (
        ordered[lower] * (1.0 - weight)
        + ordered[upper] * weight
    )


def build_traffic_statistics( applications, simulation_duration_s: float,) -> list[dict]:
    """
    Build one traffic record for every source-destination flow.
    """
    records = []

    for application in applications:
        source = application.node.name

        for destination, statistics in ( application.flow_statistics.items()):
            
            demand = application.traffic_demands[ destination ]
            
            arrival_rate = (application._get_arrival_rate( destination))
            
            memory_size = int(demand["memory_size"])
            
            arrivals = int(statistics["arrival_events"])
            
            admitted = int(  statistics["admitted_requests"])
            
            blocked = int( statistics["blocked_requests"] )
            
            requested_pairs = int( statistics["requested_pairs"])
            
            admitted_pairs = int( statistics["admitted_pairs"] )
            
            blocking_probability = (  blocked / arrivals  if arrivals > 0 else 0.0)
            
            admission_probability = ( admitted / arrivals if arrivals > 0 else 0.0 )
            
            observed_arrival_rate = (  arrivals / simulation_duration_s  if simulation_duration_s > 0  else 0.0 )
            
            configured_offered_rate = ( arrival_rate * memory_size)
            
            observed_offered_rate = ( observed_arrival_rate * memory_size )
            
            records.append(
                {
                    "source": source,
                    "destination": destination,
                    "arrival_rate_requests_per_s":arrival_rate,
                    "mean_interarrival_s":  1.0 / arrival_rate,
                    "memory_size": memory_size,
                    "target_fidelity": float  (demand[ "target_fidelity" ]  ),
                    "configured_offered_rate_pairs_per_s":configured_offered_rate,
                    "observed_arrival_rate_requests_per_s": observed_arrival_rate,
                    "observed_offered_rate_pairs_per_s": observed_offered_rate,
                    "arrival_events": arrivals,
                    "admitted_requests": admitted,
                    "blocked_requests": blocked,
                    "admission_probability": admission_probability,
                    "blocking_probability": blocking_probability,
                    "requested_pairs": requested_pairs,
                    "admitted_pairs": admitted_pairs,
                }
            )

    return records


def build_flow_statistics(applications,simulation_duration_s: float,) -> list[dict]:
    """
    Aggregate completed, failed and blocked sessions for every flow.
    """
    records = []

    for application in applications:
        source = application.node.name
        destinations = sorted(  application.traffic_demands.keys())
        for destination in destinations:
            flow_history = [
                item
                for item in application.history
                if item["dst"] == destination
            ]

            total_sessions = len(flow_history)

            blocked_sessions = sum(1 for item in flow_history if (item["close_reason"] == "arrival_blocked" ))

            admitted_sessions = ( total_sessions - blocked_sessions)

            approved_sessions = sum( 1  for item in flow_history  if item["approved"] is True )

            completed_sessions = sum(  1  for item in flow_history  if item["completed"] )

            requested_pairs = sum(  int(item["requested_pairs"])  for item in flow_history)

            delivered_pairs = sum(   int(item["delivered_pairs"])   for item in flow_history)
            
            delivery_ratio = (  delivered_pairs / requested_pairs  if requested_pairs > 0  else 0.0)
            
            completion_probability = (  completed_sessions  / admitted_sessions  if admitted_sessions > 0  else 0.0 )
            
            approved_with_delivery = [   item   for item in flow_history   if item["delivered_pairs"] > 0 ]
            
            fidelities = [  float(item["avg_fidelity"]) for item in approved_with_delivery]
            
            latencies = [  float(item["avg_latency_s"]) for item in approved_with_delivery  if item["avg_latency_s"] is not None ]
            
            average_fidelity = (  sum(fidelities) / len(fidelities)  if fidelities  else None )
            
            average_latency_s = (  sum(latencies) / len(latencies)  if latencies  else None )
            
            throughput_pairs_per_s = (  delivered_pairs   / simulation_duration_s  if simulation_duration_s > 0  else 0.0 )
            
            records.append(
                {
                    "source": source,
                    "destination": destination,
                    "total_session_records":
                        total_sessions,
                    "admitted_sessions":
                        admitted_sessions,
                    "blocked_sessions":
                        blocked_sessions,
                    "approved_sessions":
                        approved_sessions,
                    "completed_sessions":
                        completed_sessions,
                    "completion_probability":
                        completion_probability,
                    "requested_pairs":
                        requested_pairs,
                    "delivered_pairs":
                        delivered_pairs,
                    "delivery_ratio":
                        delivery_ratio,
                    "throughput_pairs_per_s":
                        throughput_pairs_per_s,
                    "average_fidelity":
                        average_fidelity,
                    "average_latency_s":
                        average_latency_s,
                }
            )

    return records


def build_link_statistics( simulation_duration_s: float,) -> list[dict]:
    """
    Export observed link-level generation and pair-lifecycle metrics.
    """
    records = []

    for link, statistics in sorted( LINK_METRICS.items() ):
        attempts = int( statistics.get( "eg_attempts", 0 ))

        successes = int( statistics.get(  "eg_successes", 0, ))

        observed_creations = int( statistics.get(    "observed_creations",  0,))

        pair_records = statistics.get( "pair_records", [],)

        generation_probability = (  successes / attempts  if attempts > 0  else None)

        attempt_rate = ( attempts / simulation_duration_s if simulation_duration_s > 0 else 0.0)

        success_rate = (  successes / simulation_duration_s if simulation_duration_s > 0 else 0.0)

        lifetimes_s = [ record["observed_lifetime_ps"] * 1e-12 for record in pair_records if ( record.get(  "observed_lifetime_ps" ) is not None ) ]

        fidelity_at_creation = [ float( record["fidelity_at_creation"] ) for record in pair_records if (  record.get(  "fidelity_at_creation" ) is not None ) ]

        fidelity_at_discard = [ float(  record["fidelity_at_discard"]  ) for record in pair_records if (   record.get(  "fidelity_at_discard"  ) is not None ) ]

        average_lifetime_s = (  sum(lifetimes_s) / len(lifetimes_s)  if lifetimes_s  else None )

        average_fidelity_creation = ( sum(fidelity_at_creation)  / len(fidelity_at_creation)  if fidelity_at_creation  else None )

        average_fidelity_discard = ( sum(fidelity_at_discard) / len(fidelity_at_discard) if fidelity_at_discard else None )

        records.append(
            {
                "node_a": link[0],
                "node_b": link[1],
                "eg_attempts":attempts,
                "eg_successes":successes,
                "observed_creations":observed_creations,
                "generation_probability": generation_probability,
                "attempt_rate_per_s": attempt_rate,
                "success_rate_pairs_per_s":success_rate,
                "completed_pair_records":len(pair_records),
                "average_observed_lifetime_s":average_lifetime_s,
                "average_fidelity_at_creation":average_fidelity_creation,
                "average_fidelity_at_discard":average_fidelity_discard,
            }
        )

    return records


def build_session_history( applications,) -> list[dict]:
    """
    Merge the session histories of all source applications.
    """
    records = []
    for application in applications:
        for session in application.history:
            records.append({ **session,  "application_node":application.node.name,  } )
    return records

def build_memory_occupancy_statistics() -> dict:
    """
    Build empirical memory-holding-time statistics.

    Records closed at simulation end are treated as right-censored
    observations and are excluded from the holding-time averages.
    """
    records = get_memory_occupancy_records()
    by_node: dict[str, list[float]] = defaultdict(list)
    by_flow_node: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    completed_records = []
    censored_records = []

    for record in records:
        if record.get("reason") == "simulation_end":
            censored_records.append(record)
            continue

        duration_s = float(record.get("duration_s", 0.0))

        if duration_s < 0:
            continue

        completed_records.append(record)
        node_name = str(record["node"])
        by_node[node_name].append(duration_s)

        source = record.get("source")
        destination = record.get("destination")

        if source is not None and destination is not None:
            by_flow_node[(str(source), str(destination), node_name)].append(duration_s)

    node_statistics = []

    for node_name, durations in sorted(by_node.items()):
        node_statistics.append(
            {
                "node": node_name,
                "samples": len(durations),
                "average_holding_time_s": mean(durations),
                "median_holding_time_s": median(durations),
                "p95_holding_time_s": _percentile(durations, 0.95),
                "maximum_holding_time_s": max(durations),
            }
        )

    flow_node_statistics = []

    for (source, destination, node_name), durations in sorted(by_flow_node.items()):
        flow_node_statistics.append(
            {
                "source": source,
                "destination": destination,
                "node": node_name,
                "samples": len(durations),
                "average_holding_time_s": mean(durations),
                "median_holding_time_s": median(durations),
                "p95_holding_time_s": _percentile(durations, 0.95),
                "maximum_holding_time_s": max(durations),
            }
        )

    all_durations = [duration for durations in by_node.values() for duration in durations]

    return {
        "total_records": len(records),
        "completed_records": len(completed_records),
        "censored_records_at_end": len(censored_records),
        "active_records_at_end": len(censored_records),
        "global_average_holding_time_s": mean(all_durations) if all_durations else None,
        "node_statistics": node_statistics,
        "flow_node_statistics": flow_node_statistics,
        "records": records,
    }
def build_empirical_memory_holding_times(memory_statistics: dict, minimum_samples: int = 20) -> dict:
    """
    Build the compact flow-node holding-time database used by the LP.

    If a flow-node pair has too few samples, the node-wide average is
    used. The global mean is used as a final fallback.
    """
    node_fallbacks = {
        record["node"]: {
            "samples": int(record["samples"]),
            "average_holding_time_s": float(record["average_holding_time_s"]),
        }
        for record in memory_statistics["node_statistics"]
    }

    global_average = memory_statistics.get("global_average_holding_time_s")
    flows: dict[str, dict[str, dict]] = defaultdict(dict)

    for record in memory_statistics["flow_node_statistics"]:
        source = record["source"]
        destination = record["destination"]
        node_name = record["node"]
        samples = int(record["samples"])
        flow_name = f"{source}-{destination}"

        if samples >= minimum_samples:
            holding_time_s = float(record["average_holding_time_s"])
            value_source = "flow_node"
        elif node_name in node_fallbacks:
            holding_time_s = float(node_fallbacks[node_name]["average_holding_time_s"])
            value_source = "node_fallback"
        else:
            holding_time_s = float(global_average) if global_average is not None else None
            value_source = "global_fallback"

        flows[flow_name][node_name] = {
            "holding_time_s": holding_time_s,
            "samples": samples,
            "source": value_source,
        }

    return {
        "minimum_samples": minimum_samples,
        "global_average_holding_time_s": global_average,
        "node_fallbacks": node_fallbacks,
        "flows": dict(flows),
    }
def build_experiment_summary( rho: float, base_traffic_seed: int, simulation_duration_s: float,  memory_statistics: dict,configured_offered_traffic: float,  traffic_statistics: list[dict],  flow_statistics: list[dict],) -> dict:
    """
    Build a compact experiment-level summary.
    """
    total_arrivals = sum( item["arrival_events"] for item in traffic_statistics)

    total_admitted_requests = sum( item["admitted_requests"] for item in traffic_statistics)

    total_blocked_requests = sum( item["blocked_requests"] for item in traffic_statistics)

    total_requested_pairs = sum(item["requested_pairs"]for item in traffic_statistics)

    total_admitted_pairs = sum( item["admitted_pairs"] for item in traffic_statistics)

    total_delivered_pairs = sum(  item["delivered_pairs"] for item in flow_statistics)

    total_completed_sessions = sum( item["completed_sessions"] for item in flow_statistics)

    total_admitted_sessions = sum( item["admitted_sessions"] for item in flow_statistics)

    blocking_probability = (  total_blocked_requests / total_arrivals if total_arrivals > 0 else 0.0)

    completion_probability = (  total_completed_sessions / total_admitted_sessions if total_admitted_sessions > 0 else 0.0)

    offered_delivery_ratio = ( total_delivered_pairs / total_requested_pairs if total_requested_pairs > 0 else 0.0)

    admitted_delivery_ratio = (  total_delivered_pairs  / total_admitted_pairs  if total_admitted_pairs > 0  else 0.0)

    throughput_pairs_per_s = ( total_delivered_pairs / simulation_duration_s if simulation_duration_s > 0 else 0.0)

    valid_fidelities = [ item["average_fidelity"] for item in flow_statistics if item["average_fidelity"] is not None]

    valid_latencies = [  item["average_latency_s"]  for item in flow_statistics  if item["average_latency_s"] is not None ]

    average_fidelity = ( sum(valid_fidelities)  / len(valid_fidelities)  if valid_fidelities  else None )

    average_latency_s = (   sum(valid_latencies)  / len(valid_latencies)  if valid_latencies else None)

    return {
        "rho": float(rho),
        "base_traffic_seed":
            int(base_traffic_seed),
        "simulation_duration_s":
            float(simulation_duration_s),
        "configured_offered_traffic_pairs_per_s":
            float(configured_offered_traffic),

        "total_arrival_events":
            total_arrivals,
        "total_admitted_requests":
            total_admitted_requests,
        "total_blocked_requests":
            total_blocked_requests,
        "blocking_probability":
            blocking_probability,

        "total_requested_pairs":
            total_requested_pairs,
        "total_admitted_pairs":
            total_admitted_pairs,
        "total_delivered_pairs":
            total_delivered_pairs,

        "offered_delivery_ratio":
            offered_delivery_ratio,
        "admitted_delivery_ratio":
            admitted_delivery_ratio,

        "throughput_pairs_per_s":
            throughput_pairs_per_s,

        "total_admitted_sessions":
            total_admitted_sessions,
        "total_completed_sessions":
            total_completed_sessions,
        "completion_probability":
            completion_probability,

        "average_delivered_fidelity":
            average_fidelity,
        "average_latency_s":
            average_latency_s,
        "completed_memory_occupancy_records":
            memory_statistics[
            "completed_records"
            ],

        "censored_memory_occupancy_records":
            memory_statistics[
            "censored_records_at_end"
            ],

        "global_average_memory_holding_time_s":
            memory_statistics[
            "global_average_holding_time_s"
             ],
        "open_pair_records_at_end":
            len(ACTIVE_PAIRS),
    }

def export_classical_sequence_results(applications, rho: float, base_traffic_seed: int, simulation_duration_s: float, configured_offered_traffic: float, base_output_directory: str | Path,) -> dict[str, Path]:
    """
    Export all results produced by one SeQUeNCe baseline experiment.
    """
    experiment_name = ( f"rho_{rho:.2f}_seed_{base_traffic_seed}")

    experiment_directory = ( Path(base_output_directory) / experiment_name)

    experiment_directory.mkdir(  parents=True,  exist_ok=True,)

    traffic_statistics = build_traffic_statistics(  applications=applications,  simulation_duration_s= simulation_duration_s,)

    flow_statistics = build_flow_statistics( applications=applications,simulation_duration_s=  simulation_duration_s,)

    link_statistics = build_link_statistics(simulation_duration_s=  simulation_duration_s,)

    session_history = build_session_history(applications=applications,)
    memory_statistics = (
    build_memory_occupancy_statistics()
    )

    empirical_memory_holding_times = (
        build_empirical_memory_holding_times(
            memory_statistics=memory_statistics,
            minimum_samples=20,
        )
    )

    summary = build_experiment_summary(
    rho=rho,
    base_traffic_seed=
        base_traffic_seed,
    simulation_duration_s=
        simulation_duration_s,
    configured_offered_traffic=
        configured_offered_traffic,
    traffic_statistics=
        traffic_statistics,
    flow_statistics=
        flow_statistics,
    memory_statistics=
        memory_statistics,
)
    flow_protocol_statistics = (
    build_flow_protocol_statistics()
)
    output_files = {
    "summary": save_json(summary, experiment_directory / "summary.json"),
    "traffic_statistics": save_json(traffic_statistics, experiment_directory / "traffic_statistics.json"),
    "flow_statistics": save_json(flow_statistics, experiment_directory / "flow_statistics.json"),
    "link_statistics": save_json(link_statistics, experiment_directory / "link_statistics.json"),
    "session_history": save_json(session_history, experiment_directory / "session_history.json"),
    "flow_protocol_statistics": save_json(
        flow_protocol_statistics,
        experiment_directory / "flow_protocol_statistics.json",
    ),
    "memory_occupancy_statistics": save_json(
        {
            "total_records": memory_statistics["total_records"],
            "completed_records": memory_statistics["completed_records"],
            "censored_records_at_end": memory_statistics["censored_records_at_end"],
            "global_average_holding_time_s": memory_statistics["global_average_holding_time_s"],
            "node_statistics": memory_statistics["node_statistics"],
            "flow_node_statistics": memory_statistics["flow_node_statistics"],
        },
        experiment_directory / "memory_occupancy_statistics.json",
    ),
    "memory_occupancy_records": save_json(
        memory_statistics["records"],
        experiment_directory / "memory_occupancy_records.json",
    ),
    "empirical_memory_holding_times": save_json(
        empirical_memory_holding_times,
        experiment_directory / "empirical_memory_holding_times.json",
    ),
}

    print( f"\n[RESULTS] Experiment saved in: " f"{experiment_directory}")

    for name, path in output_files.items():
        print(  f"[RESULTS] {name}: {path}" )
    lp_generated_directory = Path(__file__).resolve().parents[2] / "classic_opt" / "generated"

    lp_holding_time_file = save_json(
        empirical_memory_holding_times,
        lp_generated_directory / "empirical_memory_holding_times.json",
    )

    output_files["lp_empirical_memory_holding_times"] = lp_holding_time_file
    return output_files