from __future__ import annotations

from collections import defaultdict
from statistics import mean, median

from simulator.first_RL.metrics.memory_occupancy_metrics import (
    get_memory_occupancy_records,
)


def _percentile(
    values: list[float],
    probability: float,
) -> float | None:
    if not values:
        return None

    ordered = sorted(values)

    position = (
        probability
        * (len(ordered) - 1)
    )

    lower = int(position)
    upper = min(
        lower + 1,
        len(ordered) - 1,
    )

    weight = position - lower

    return (
        ordered[lower] * (1.0 - weight)
        + ordered[upper] * weight
    )


def build_memory_occupancy_statistics() -> dict:
    records = get_memory_occupancy_records()

    by_node = defaultdict(list)
    by_flow_node = defaultdict(list)

    for record in records:
        duration_s = float(
            record["duration_s"]
        )

        node_name = record["node"]

        by_node[node_name].append(
            duration_s
        )

        source = record.get("source")
        destination = record.get(
            "destination"
        )

        if (
            source is not None
            and destination is not None
        ):
            by_flow_node[
                (
                    source,
                    destination,
                    node_name,
                )
            ].append(
                duration_s
            )

    node_statistics = []

    for node_name, durations in sorted(
        by_node.items()
    ):
        node_statistics.append(
            {
                "node":
                    node_name,
                "samples":
                    len(durations),
                "average_holding_time_s":
                    mean(durations),
                "median_holding_time_s":
                    median(durations),
                "p95_holding_time_s":
                    _percentile(
                        durations,
                        0.95,
                    ),
                "maximum_holding_time_s":
                    max(durations),
            }
        )

    flow_node_statistics = []

    for (
        source,
        destination,
        node_name,
    ), durations in sorted(
        by_flow_node.items()
    ):
        flow_node_statistics.append(
            {
                "source":
                    source,
                "destination":
                    destination,
                "node":
                    node_name,
                "samples":
                    len(durations),
                "average_holding_time_s":
                    mean(durations),
                "median_holding_time_s":
                    median(durations),
                "p95_holding_time_s":
                    _percentile(
                        durations,
                        0.95,
                    ),
                "maximum_holding_time_s":
                    max(durations),
            }
        )

    return {
        "records":
            records,
        "node_statistics":
            node_statistics,
        "flow_node_statistics":
            flow_node_statistics,
    }