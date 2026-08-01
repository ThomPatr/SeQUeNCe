from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeAlias


Demand: TypeAlias = dict[str, int | float | str]


def sequence_traffic_to_demands(
    traffic_matrix,
    node_to_index,
    scale_factor=1.0,
):
    demands = []

    for source_name, destinations in (
        traffic_matrix.items()
    ):
        for target_name, parameters in (
            destinations.items()
        ):
            if (
                "arrival_rate_requests_per_s"
                in parameters
            ):
                base_arrival_rate = float(
                    parameters[
                        "arrival_rate_requests_per_s"
                    ]
                )

            elif "mean_interarrival_s" in parameters:
                base_arrival_rate = (
                    1.0
                    / float(
                        parameters[
                            "mean_interarrival_s"
                        ]
                    )
                )

            elif "interval_s" in parameters:
                base_arrival_rate = (
                    1.0
                    / float(
                        parameters["interval_s"]
                    )
                )

            else:
                raise KeyError(
                    f"Missing arrival configuration for "
                    f"{source_name}->{target_name}."
                )

            arrival_rate = (
                scale_factor
                * base_arrival_rate
            )

            memory_size = int(
                parameters["memory_size"]
            )

            average_demand = (
                arrival_rate
                * memory_size
            )

            demands.append(
                {
                    "source":
                        node_to_index[source_name],
                    "target":
                        node_to_index[target_name],
                    "source_name":
                        source_name,
                    "target_name":
                        target_name,
                    "arrival_rate_requests_per_s":
                        arrival_rate,
                    "mean_interarrival_s":
                        1.0 / arrival_rate,
                    "memory_size":
                        memory_size,
                    "volume":
                        average_demand,
                    "target_fidelity":
                        float(
                            parameters[
                                "target_fidelity"
                            ]
                        ),
                }
            )

    return demands


def save_traffic_demands(
    demands: list[Demand],
    json_output_path: str | Path,
) -> None:
    output_path = Path(json_output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(demands, file, indent=4)

    print(
        f"[TRAFFIC] Saved {len(demands)} demands "
        f"in {output_path}."
    )


def print_traffic_demands(demands: list[dict]) -> None:
    print("\n================ TRAFFIC DEMANDS ================\n")
    total_offered_traffic = 0.0

    for demand_index, demand in enumerate(demands):
        source = demand.get("source_name", demand["source"])
        target = demand.get("target_name", demand["target"])
        volume = float(demand.get("volume", 0.0))
        memory_size = int(demand.get("memory_size", 1))
        target_fidelity = demand.get("target_fidelity")
        arrival_rate = demand.get("arrival_rate_requests_per_s")
        interval_s = demand.get("interval_s")

        if arrival_rate is None and interval_s is not None and float(interval_s) > 0.0:
            arrival_rate = 1.0 / float(interval_s)

        if interval_s is None and arrival_rate is not None and float(arrival_rate) > 0.0:
            interval_s = 1.0 / float(arrival_rate)

        total_offered_traffic += volume
        print(f"Demand {demand_index}: {source} -> {target}")

        if arrival_rate is not None:
            print(f"  arrival rate     : {float(arrival_rate):.6f} requests/s")

        if interval_s is not None:
            print(f"  mean interarrival: {float(interval_s):.6f} s")

        print(f"  memory size      : {memory_size}")
        print(f"  offered traffic  : {volume:.6f} pairs/s")

        if target_fidelity is not None:
            print(f"  target fidelity  : {float(target_fidelity):.6f}")

    print(f"\nTotal offered traffic: {total_offered_traffic:.6f} pairs/s")