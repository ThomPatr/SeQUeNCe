from __future__ import annotations

import json
from pathlib import Path
from typing import TypeAlias

import networkx as nx


NodeId: TypeAlias = int
Edge: TypeAlias = tuple[NodeId, NodeId]
EdgePath: TypeAlias = list[Edge]
PathsDictionary: TypeAlias = dict[
    tuple[NodeId, NodeId],
    list[EdgePath],
]


def normalize_edge(edge: Edge, directed: bool) -> Edge:
    if directed:
        return edge

    return tuple(sorted(edge))


def build_topology_from_sequence_json(
    sequence_topology_path: str | Path,
    output_json_path: str | Path,
    k_paths: int = 3,
    directed: bool = False,
) -> tuple[
    nx.Graph,
    PathsDictionary,
    dict[str, int],
    dict[int, str],
]:
    """
    Build the optimization graph directly from a SeQUeNCe topology JSON.

    The nodes are converted from names to integer identifiers because the
    current optimization modules use numerical node IDs.

    The routing cost of each quantum connection is its physical distance.
    """

    if k_paths <= 0:
        raise ValueError("k_paths must be greater than zero.")

    topology_path = Path(sequence_topology_path)

    with topology_path.open("r", encoding="utf-8") as file:
        sequence_data = json.load(file)

    node_names = [
        node_data["name"]
        for node_data in sequence_data["nodes"]
    ]

    node_to_index = {
        node_name: index
        for index, node_name in enumerate(node_names)
    }

    index_to_node = {
        index: node_name
        for node_name, index in node_to_index.items()
    }

    graph = nx.DiGraph() if directed else nx.Graph()
    graph.add_nodes_from(range(len(node_names)))

    for connection in sequence_data["qconnections"]:
        node_1_name = connection["node1"]
        node_2_name = connection["node2"]

        if node_1_name not in node_to_index:
            raise KeyError(
                f"Unknown node in qconnections: {node_1_name}"
            )

        if node_2_name not in node_to_index:
            raise KeyError(
                f"Unknown node in qconnections: {node_2_name}"
            )

        node_1 = node_to_index[node_1_name]
        node_2 = node_to_index[node_2_name]

        distance_m = float(connection["distance"])
        attenuation_db_per_m = float(
            connection.get("attenuation", 0.0)
        )

        if distance_m <= 0:
            raise ValueError(
                f"Invalid quantum-link distance for "
                f"{node_1_name}<->{node_2_name}: {distance_m}"
            )

        graph.add_edge(
            node_1,
            node_2,
            weight=distance_m,
            cost=distance_m,
            distance_m=distance_m,
            attenuation_db_per_m=attenuation_db_per_m,
            node1_name=node_1_name,
            node2_name=node_2_name,
        )

    all_paths: PathsDictionary = {}

    for source in graph.nodes:
        for target in graph.nodes:
            if source == target:
                continue

            all_paths[(source, target)] = []

            try:
                path_generator = nx.shortest_simple_paths(
                    graph,
                    source=source,
                    target=target,
                    weight="weight",
                )

                for node_path in path_generator:
                    edge_path = [
                        (
                            node_path[index],
                            node_path[index + 1],
                        )
                        for index in range(
                            len(node_path) - 1
                        )
                    ]

                    all_paths[(source, target)].append(
                        edge_path
                    )

                    if (
                        len(all_paths[(source, target)])
                        >= k_paths
                    ):
                        break

            except (
                nx.NetworkXNoPath,
                nx.NodeNotFound,
            ):
                all_paths[(source, target)] = []

    serialized_paths = {
        f"{source}-{target}": paths
        for (source, target), paths in all_paths.items()
    }

    output_data = {
        "directed": directed,
        "node_to_index": node_to_index,
        "index_to_node": {
            str(index): name
            for index, name in index_to_node.items()
        },
        "graph": nx.node_link_data(graph),
        "all_paths": serialized_paths,
    }

    output_path = Path(output_json_path)
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(output_data, file, indent=4)

    print(
        f"[TOPOLOGY] Loaded SeQUeNCe topology from "
        f"{topology_path}"
    )
    print(
        f"[TOPOLOGY] Nodes: {graph.number_of_nodes()}"
    )
    print(
        f"[TOPOLOGY] Quantum links: "
        f"{graph.number_of_edges()}"
    )
    print(
        f"[TOPOLOGY] Computed up to {k_paths} paths "
        f"per ordered node pair."
    )
    print(f"[TOPOLOGY] Data saved in {output_path}")

    return (
        graph,
        all_paths,
        node_to_index,
        index_to_node,
    )