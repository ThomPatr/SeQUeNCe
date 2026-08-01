from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any


def _new_flow_record() -> dict[str, Any]:
    return {
        "swapping": {
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "success_probability": 0.0,
            "nodes": {},
        },
        "purification": {
            "candidates": 0,
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "success_probability": 0.0,
            "input_fidelities": [],
            "output_fidelities": [],
            "average_input_fidelity": None,
            "average_output_fidelity": None,
            "average_fidelity_gain": None,
            "nodes": {},
        },
    }


FLOW_PROTOCOL_METRICS = defaultdict(
    _new_flow_record
)


def normalize_flow(
    source: str,
    destination: str,
) -> tuple[str, str]:
    """
    Preserve the direction of the application flow.
    """
    return str(source), str(destination)


def reset_protocol_metrics() -> None:
    FLOW_PROTOCOL_METRICS.clear()

def initialize_flow_protocol_metrics(
    traffic_matrix: dict,
) -> None:
    for source, destinations in (
        traffic_matrix.items()
    ):
        for destination in destinations:
            flow = normalize_flow(
                source,
                destination,
            )

            _ = FLOW_PROTOCOL_METRICS[
                flow
            ]
def _increment_node_counter(
    operation_metrics: dict,
    node_name: str | None,
    counter_name: str,
) -> None:
    if node_name is None:
        return

    node_name = str(node_name)

    if node_name not in operation_metrics["nodes"]:
        operation_metrics["nodes"][node_name] = {
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "candidates": 0,
        }

    operation_metrics["nodes"][node_name][
        counter_name
    ] += 1


# ============================================================
# SWAPPING
# ============================================================

def record_swapping_attempt(
    source: str,
    destination: str,
    node_name: str | None = None,
) -> None:
    flow = normalize_flow(
        source,
        destination,
    )

    metrics = FLOW_PROTOCOL_METRICS[
        flow
    ]["swapping"]

    metrics["attempts"] += 1

    _increment_node_counter(
        operation_metrics=metrics,
        node_name=node_name,
        counter_name="attempts",
    )


def record_swapping_success(source: str, destination: str, output_fidelity: float | None = None, node_name: str | None = None) -> None:
    flow = normalize_flow(source, destination)
    metrics = FLOW_PROTOCOL_METRICS[flow]["swapping"]
    metrics["successes"] += 1

    _increment_node_counter(operation_metrics=metrics, node_name=node_name, counter_name="successes")


def record_swapping_failure(
    source: str,
    destination: str,
    node_name: str | None = None,
) -> None:
    flow = normalize_flow(
        source,
        destination,
    )

    metrics = FLOW_PROTOCOL_METRICS[
        flow
    ]["swapping"]

    metrics["failures"] += 1

    _increment_node_counter(
        operation_metrics=metrics,
        node_name=node_name,
        counter_name="failures",
    )


# ============================================================
# PURIFICATION
# ============================================================

def record_purification_candidate(
    source: str,
    destination: str,
    node_name: str | None = None,
) -> None:
    flow = normalize_flow(
        source,
        destination,
    )

    metrics = FLOW_PROTOCOL_METRICS[
        flow
    ]["purification"]

    metrics["candidates"] += 1

    _increment_node_counter(
        operation_metrics=metrics,
        node_name=node_name,
        counter_name="candidates",
    )


def record_purification_attempt(
    source: str,
    destination: str,
    input_fidelity: float | None,
    node_name: str | None = None,
) -> None:
    flow = normalize_flow(
        source,
        destination,
    )

    metrics = FLOW_PROTOCOL_METRICS[
        flow
    ]["purification"]

    metrics["attempts"] += 1

    if input_fidelity is not None:
        metrics["input_fidelities"].append(
            float(input_fidelity)
        )

    _increment_node_counter(
        operation_metrics=metrics,
        node_name=node_name,
        counter_name="attempts",
    )


def record_purification_success(
    source: str,
    destination: str,
    input_fidelity: float | None,
    output_fidelity: float | None,
    node_name: str | None = None,
) -> None:
    flow = normalize_flow(
        source,
        destination,
    )

    metrics = FLOW_PROTOCOL_METRICS[
        flow
    ]["purification"]

    metrics["successes"] += 1

    # Add input fidelity only when the attempt tracker
    # did not already record it.
    if (
        input_fidelity is not None
        and (
            not metrics["input_fidelities"]
            or metrics["input_fidelities"][-1]
            != float(input_fidelity)
        )
    ):
        metrics["input_fidelities"].append(
            float(input_fidelity)
        )

    if output_fidelity is not None:
        metrics["output_fidelities"].append(
            float(output_fidelity)
        )

    _increment_node_counter(
        operation_metrics=metrics,
        node_name=node_name,
        counter_name="successes",
    )


def record_purification_failure(
    source: str,
    destination: str,
    input_fidelity: float | None,
    node_name: str | None = None,
) -> None:
    flow = normalize_flow(
        source,
        destination,
    )

    metrics = FLOW_PROTOCOL_METRICS[
        flow
    ]["purification"]

    metrics["failures"] += 1

    if (
        input_fidelity is not None
        and (
            not metrics["input_fidelities"]
            or metrics["input_fidelities"][-1]
            != float(input_fidelity)
        )
    ):
        metrics["input_fidelities"].append(
            float(input_fidelity)
        )

    _increment_node_counter(
        operation_metrics=metrics,
        node_name=node_name,
        counter_name="failures",
    )


# ============================================================
# EXPORT
# ============================================================

def build_flow_protocol_statistics() -> list[dict]:
    """
    Build one serializable record for every application flow.
    """
    results = []

    for (
        source,
        destination,
    ), raw_metrics in sorted(
        FLOW_PROTOCOL_METRICS.items()
    ):
        metrics = deepcopy(
            raw_metrics
        )

        swapping = metrics["swapping"]
        purification = metrics["purification"]

        swapping_attempts = int(
            swapping["attempts"]
        )

        swapping["success_probability"] = (
            swapping["successes"]
            / swapping_attempts
            if swapping_attempts > 0
            else 0.0
        )
        
        purification_attempts = int(
            purification["attempts"]
        )

        purification[
            "success_probability"
        ] = (
            purification["successes"]
            / purification_attempts
            if purification_attempts > 0
            else 0.0
        )

        input_fidelities = purification[
            "input_fidelities"
        ]

        output_fidelities = purification[
            "output_fidelities"
        ]

        average_input = (
            sum(input_fidelities)
            / len(input_fidelities)
            if input_fidelities
            else None
        )

        average_output = (
            sum(output_fidelities)
            / len(output_fidelities)
            if output_fidelities
            else None
        )

        average_gain = (
            average_output - average_input
            if (
                average_input is not None
                and average_output is not None
            )
            else None
        )

        purification[
            "average_input_fidelity"
        ] = average_input

        purification[
            "average_output_fidelity"
        ] = average_output

        purification[
            "average_fidelity_gain"
        ] = average_gain

        results.append(
            {
                "source": source,
                "destination": destination,
                "swapping": swapping,
                "purification": purification,
            }
        )

    return results