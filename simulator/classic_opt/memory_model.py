from __future__ import annotations

import json
from pathlib import Path


def load_node_memory_capacities(
    input_file: str | Path,
    node_to_index: dict[str, int],
) -> dict[int, float]:
    """
    Load the number of physical quantum memories available at each node.

    Expected JSON format:

    {
        "nice": 30,
        "monaco": 30,
        "valrose": 30
    }
    """
    input_path = Path(input_file)

    if not input_path.exists():
        raise FileNotFoundError(
            f"Node-memory capacity file not found: {input_path}"
        )

    with input_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        raw_data = json.load(file)

    capacities: dict[int, float] = {}

    for node_name, capacity_value in raw_data.items():
        if node_name not in node_to_index:
            raise KeyError(
                f"Unknown node '{node_name}' in {input_path}."
            )

        capacity = float(capacity_value)

        if capacity <= 0:
            raise ValueError(
                f"Memory capacity for node {node_name} "
                f"must be positive. Received {capacity}."
            )

        capacities[
            node_to_index[node_name]
        ] = capacity

    missing_nodes = [
        node_name
        for node_name in node_to_index
        if node_to_index[node_name] not in capacities
    ]

    if missing_nodes:
        raise KeyError(
            "Missing memory capacities for nodes: "
            + ", ".join(sorted(missing_nodes))
        )

    return capacities


def load_empirical_flow_memory_costs(
    input_file: str | Path,
    node_to_index: dict[str, int],
) -> dict[
    tuple[int, int],
    dict[int, float],
]:
    """
    Load empirical memory-seconds consumed per delivered end-to-end pair.

    Expected JSON format:

    {
        "metadata": {...},
        "flows": {
            "valrose-menton": {
                "valid": true,
                "source": "valrose",
                "destination": "menton",
                "nodes": {
                    "nice": {
                        "memory_seconds_per_delivered_pair": 1.99
                    }
                }
            }
        }
    }
    """
    input_path = Path(input_file)

    if not input_path.exists():
        raise FileNotFoundError(
            f"Flow-memory cost file not found: {input_path}"
        )

    with input_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        raw_data = json.load(file)

    raw_flows = raw_data.get(
        "flows",
        raw_data,
    )

    flow_memory_costs: dict[
        tuple[int, int],
        dict[int, float],
    ] = {}

    for flow_name, flow_record in raw_flows.items():
        if not flow_record.get(
            "valid",
            False,
        ):
            continue

        source_name = flow_record.get(
            "source"
        )

        destination_name = flow_record.get(
            "destination"
        )

        if (
            source_name is None
            or destination_name is None
        ):
            source_name, destination_name = (
                flow_name.split(
                    "-",
                    maxsplit=1,
                )
            )

        if source_name not in node_to_index:
            raise KeyError(
                f"Unknown source node '{source_name}' "
                f"in {input_path}."
            )

        if destination_name not in node_to_index:
            raise KeyError(
                f"Unknown destination node '{destination_name}' "
                f"in {input_path}."
            )

        flow_key = (
            node_to_index[source_name],
            node_to_index[destination_name],
        )

        node_costs: dict[int, float] = {}

        for node_name, node_record in (
            flow_record.get(
                "nodes",
                {},
            ).items()
        ):
            if node_name not in node_to_index:
                raise KeyError(
                    f"Unknown node '{node_name}' "
                    f"for flow {flow_name}."
                )

            memory_cost = float(
                node_record[
                    "memory_seconds_per_delivered_pair"
                ]
            )

            if memory_cost < 0:
                raise ValueError(
                    "Memory cost cannot be negative: "
                    f"flow={flow_name}, "
                    f"node={node_name}, "
                    f"value={memory_cost}."
                )

            node_costs[
                node_to_index[node_name]
            ] = memory_cost

        if not node_costs:
            raise ValueError(
                f"No node-memory costs found for flow {flow_name}."
            )

        flow_memory_costs[
            flow_key
        ] = node_costs

    if not flow_memory_costs:
        raise ValueError(
            f"No valid flow-memory costs were loaded from {input_path}."
        )

    return flow_memory_costs