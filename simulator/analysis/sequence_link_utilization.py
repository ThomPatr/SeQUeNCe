from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(data: Any, path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)
    return output_path


def canonical_link(node_a: str, node_b: str) -> tuple[str, str]:
    return tuple(sorted((str(node_a), str(node_b))))


def load_empirical_capacities(
    capacity_file: str | Path,
) -> dict[tuple[str, str], float]:
    raw_data = load_json(capacity_file)
    capacities: dict[tuple[str, str], float] = {}

    for key, record in raw_data.items():
        if isinstance(record, dict):
            node_a = record.get("node_a")
            node_b = record.get("node_b")

            if node_a is None or node_b is None:
                node_a, node_b = str(key).split("-", maxsplit=1)

            capacity = record.get(
                "service_capacity_pairs_per_second",
                record.get(
                    "capacity_pairs_per_second",
                    record.get("raw_creation_rate_pairs_per_second"),
                ),
            )
        else:
            node_a, node_b = str(key).split("-", maxsplit=1)
            capacity = record

        if capacity is None:
            raise KeyError(f"No capacity found for link {key!r}.")

        capacity = float(capacity)

        if capacity <= 0:
            raise ValueError(
                f"Capacity must be positive for link {key!r}."
            )

        capacities[canonical_link(node_a, node_b)] = capacity

    return capacities


def add_utilization_to_link_statistics(
    records: list[dict[str, Any]],
    capacities: dict[tuple[str, str], float],
    strict: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    updated_records = []
    warnings = []

    for record in records:
        node_a = str(record["node_a"])
        node_b = str(record["node_b"])
        link = canonical_link(node_a, node_b)
        capacity = capacities.get(link)

        if capacity is None:
            warnings.append(
                f"Skipping non-physical or uncalibrated link "
                f"{node_a}<->{node_b}."
            )
            continue

        generated_rate = record.get(
            "generated_rate_pairs_per_second",
            record.get("success_rate_pairs_per_s"),
        )

        if generated_rate is None:
            message = (
                f"No generated/success rate found for "
                f"{node_a}<->{node_b}."
            )

            if strict:
                raise KeyError(message)

            warnings.append(message)
            updated_records.append(record.copy())
            continue

        generated_rate = float(generated_rate)
        utilization = generated_rate / capacity

        updated_records.append(
            {
                **record,
                "capacity_pairs_per_second": capacity,
                "generated_rate_pairs_per_second": generated_rate,
                "utilization": utilization,
                "utilization_percent": 100.0 * utilization,
                "utilization_definition": (
                    "generated_rate_pairs_per_second / "
                    "capacity_pairs_per_second"
                ),
            }
        )

    return updated_records, warnings


def process_single_file(
    link_statistics_file: str | Path,
    capacity_file: str | Path,
    output_file: str | Path | None = None,
    overwrite: bool = False,
    strict: bool = True,
) -> Path:
    input_path = Path(link_statistics_file)
    records = load_json(input_path)

    if not isinstance(records, list):
        raise TypeError(f"{input_path} must contain a JSON list.")

    capacities = load_empirical_capacities(capacity_file)
    updated_records, warnings = add_utilization_to_link_statistics(
        records=records,
        capacities=capacities,
        strict=strict,
    )

    if output_file is not None:
        output_path = Path(output_file)
    elif overwrite:
        output_path = input_path
    else:
        output_path = (
            input_path.parent
            / "link_statistics_with_utilization.json"
        )

    save_json(updated_records, output_path)

    print(f"[UPDATED] {input_path} -> {output_path}")

    for warning in warnings:
        print(f"[WARNING] {warning}")

    return output_path


def process_results_directory(
    results_directory: str | Path,
    capacity_file: str | Path,
    overwrite: bool = False,
    strict: bool = True,
) -> list[Path]:
    root = Path(results_directory)
    input_files = sorted(root.rglob("link_statistics.json"))

    if not input_files:
        raise FileNotFoundError(
            f"No link_statistics.json files found under {root}."
        )

    output_files = []

    for input_file in input_files:
        output_files.append(
            process_single_file(
                link_statistics_file=input_file,
                capacity_file=capacity_file,
                overwrite=overwrite,
                strict=strict,
            )
        )

    return output_files


def load_index_to_node(
    topology_file: str | Path,
) -> dict[int, str]:
    raw_data = load_json(topology_file)

    if isinstance(raw_data, dict) and "index_to_node" in raw_data:
        return {
            int(index): str(node_name)
            for index, node_name in raw_data["index_to_node"].items()
        }

    if isinstance(raw_data, dict) and "node_to_index" in raw_data:
        return {
            int(index): str(node_name)
            for node_name, index in raw_data["node_to_index"].items()
        }

    if isinstance(raw_data, dict) and isinstance(
        raw_data.get("nodes"),
        list,
    ):
        mapping = {}

        for record in raw_data["nodes"]:
            if not isinstance(record, dict):
                continue

            index = record.get("id", record.get("index"))
            node_name = record.get("name", record.get("label"))

            if index is not None and node_name is not None:
                mapping[int(index)] = str(node_name)

        if mapping:
            return mapping

    raise KeyError(
        f"No node-index mapping found in {topology_file}."
    )


def resolve_lp_node_name(
    value: Any,
    index_to_node: dict[int, str],
) -> str:
    if isinstance(value, str):
        stripped = value.strip()

        if not stripped.lstrip("-").isdigit():
            return stripped

    node_index = int(value)

    if node_index not in index_to_node:
        raise KeyError(f"Unknown LP node index: {node_index}.")

    return index_to_node[node_index]


def load_lp_link_utilizations(
    lp_result_file: str | Path,
    topology_file: str | Path,
) -> dict[tuple[str, str], float]:
    lp_result = load_json(lp_result_file)

    if not lp_result.get("feasible", False):
        raise ValueError(
            f"The LP result is infeasible: {lp_result_file}."
        )

    index_to_node = load_index_to_node(topology_file)
    utilizations: dict[tuple[str, str], float] = {}

    for record in lp_result.get("link_statistics", []):
        edge = record.get("edge")

        if not isinstance(edge, list) or len(edge) != 2:
            raise ValueError(f"Invalid LP edge record: {record}.")

        node_a = resolve_lp_node_name(edge[0], index_to_node)
        node_b = resolve_lp_node_name(edge[1], index_to_node)

        utilizations[canonical_link(node_a, node_b)] = float(
            record["utilization"]
        )

    return utilizations


def load_sequence_link_utilizations(
    link_statistics_file: str | Path,
) -> dict[tuple[str, str], float]:
    records = load_json(link_statistics_file)
    utilizations: dict[tuple[str, str], float] = {}

    for record in records:
        if "utilization" not in record:
            continue

        utilizations[
            canonical_link(
                record["node_a"],
                record["node_b"],
            )
        ] = float(
            record["utilization"]
        )

    return utilizations


def create_utilization_scatter(
    sequence_link_statistics_file: str | Path,
    lp_result_file: str | Path,
    topology_file: str | Path,
    output_file: str | Path,
    title_suffix: str | None = None,
) -> Path:
    sequence_utilization = load_sequence_link_utilizations(
        sequence_link_statistics_file
    )
    lp_utilization = load_lp_link_utilizations(
        lp_result_file=lp_result_file,
        topology_file=topology_file,
    )

    common_links = sorted(
        set(sequence_utilization) & set(lp_utilization)
    )

    if not common_links:
        raise ValueError(
            "No common physical links were found between LP and SeQUeNCe."
        )

    lp_values = [
        100.0 * lp_utilization[link]
        for link in common_links
    ]
    sequence_values = [
        100.0 * sequence_utilization[link]
        for link in common_links
    ]

    maximum_value = max(lp_values + sequence_values + [1.0])

    figure, axis = plt.subplots(figsize=(7.4, 6.2))
    axis.scatter(lp_values, sequence_values)

    for link, lp_value, sequence_value in zip(
        common_links,
        lp_values,
        sequence_values,
    ):
        axis.annotate(
            f"{link[0]}-{link[1]}",
            (lp_value, sequence_value),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=8,
        )

    axis.plot(
        [0.0, maximum_value],
        [0.0, maximum_value],
        linestyle="--",
        label="Perfect agreement",
    )

    title = "Link utilization: LP prediction vs SeQUeNCe"

    if title_suffix:
        title += f" ({title_suffix})"

    axis.set_title(title)
    axis.set_xlabel("LP link utilization (%)")
    axis.set_ylabel("SeQUeNCe link utilization (%)")
    axis.set_xlim(0.0, maximum_value * 1.05)
    axis.set_ylim(0.0, maximum_value * 1.05)
    axis.grid(True, alpha=0.3)
    axis.legend()

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    figure.tight_layout()
    figure.savefig(output_path, dpi=300, bbox_inches="tight")

    pdf_path = output_path.with_suffix(".pdf")
    figure.savefig(pdf_path, bbox_inches="tight")
    plt.close(figure)

    print(f"[PLOT] {output_path}")
    print(f"[PLOT] {pdf_path}")

    return output_path


def infer_rho_from_directory(
    directory: str | Path,
) -> float | None:
    directory_path = Path(directory)
    summary_file = directory_path / "summary.json"

    if summary_file.exists():
        summary = load_json(summary_file)

        if "rho" in summary:
            return float(summary["rho"])

    match = re.search(
        r"rho[_-](\d+(?:\.\d+)?)",
        directory_path.name,
        re.IGNORECASE,
    )

    return float(match.group(1)) if match else None


def create_scatter_plots_for_results_directory(
    results_directory: str | Path,
    lp_results_directory: str | Path,
    topology_file: str | Path,
    plot_output_directory: str | Path,
    overwritten: bool,
) -> list[Path]:
    root = Path(results_directory)
    statistics_filename = (
        "link_statistics.json"
        if overwritten
        else "link_statistics_with_utilization.json"
    )
    sequence_files = sorted(root.rglob(statistics_filename))
    plot_files = []

    for sequence_file in sequence_files:
        rho = infer_rho_from_directory(sequence_file.parent)

        if rho is None:
            print(
                f"[WARNING] Cannot infer rho for {sequence_file.parent}."
            )
            continue

        lp_result_file = (
            Path(lp_results_directory)
            / f"traffic_load_{rho:.2f}_result.json"
        )

        if not lp_result_file.exists():
            print(
                f"[WARNING] No exact LP result for rho={rho:.2f}."
            )
            continue

        lp_result = load_json(lp_result_file)

        if not lp_result.get("feasible", False):
            print(f"[SKIP] LP infeasible at rho={rho:.2f}.")
            continue

        output_file = (
            Path(plot_output_directory)
            / f"link_utilization_scatter_rho_{rho:.2f}.png"
        )

        plot_files.append(
            create_utilization_scatter(
                sequence_link_statistics_file=sequence_file,
                lp_result_file=lp_result_file,
                topology_file=topology_file,
                output_file=output_file,
                title_suffix=f"rho={rho:.2f}",
            )
        )

    return plot_files


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Add LP-comparable utilization to existing SeQUeNCe "
            "link statistics and optionally create LP-vs-SeQUeNCe "
            "scatter plots."
        )
    )

    input_group = parser.add_mutually_exclusive_group(required=True)

    input_group.add_argument(
        "--input-file",
        type=Path,
        help="Process one link_statistics.json file.",
    )

    input_group.add_argument(
        "--results-dir",
        type=Path,
        help=(
            "Recursively process every link_statistics.json "
            "inside a SeQUeNCe results directory."
        ),
    )

    parser.add_argument(
        "--capacity-file",
        type=Path,
        required=True,
        help=(
            "Path to empirical_link_capacities.json used by the LP."
        ),
    )

    parser.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Output JSON path when processing one file.",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite each original link_statistics.json.",
    )

    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Continue when a capacity or generated rate is missing.",
    )

    parser.add_argument(
        "--lp-result-file",
        type=Path,
        default=None,
        help=(
            "Detailed LP result JSON for one scatter plot."
        ),
    )

    parser.add_argument(
        "--lp-results-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing traffic_load_<rho>_result.json files."
        ),
    )

    parser.add_argument(
        "--topology-db",
        type=Path,
        default=None,
        help=(
            "Path to topology_db.json used to map LP node indices."
        ),
    )

    parser.add_argument(
        "--plot-output",
        type=Path,
        default=None,
        help="PNG output path for one scatter plot.",
    )

    parser.add_argument(
        "--plot-output-dir",
        type=Path,
        default=Path("utilization_scatter_plots"),
        help="Directory for scatter plots generated for all rho values.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    strict = not args.allow_missing

    if args.input_file is not None:
        updated_file = process_single_file(
            link_statistics_file=args.input_file,
            capacity_file=args.capacity_file,
            output_file=args.output_file,
            overwrite=args.overwrite,
            strict=strict,
        )

        if args.lp_result_file is not None or args.topology_db is not None:
            if args.lp_result_file is None:
                raise ValueError(
                    "--lp-result-file is required for plotting."
                )

            if args.topology_db is None:
                raise ValueError(
                    "--topology-db is required for plotting."
                )

            output_file = (
                args.plot_output
                if args.plot_output is not None
                else (
                    updated_file.parent
                    / "link_utilization_scatter.png"
                )
            )

            create_utilization_scatter(
                sequence_link_statistics_file=updated_file,
                lp_result_file=args.lp_result_file,
                topology_file=args.topology_db,
                output_file=output_file,
            )

        return

    output_files = process_results_directory(
        results_directory=args.results_dir,
        capacity_file=args.capacity_file,
        overwrite=args.overwrite,
        strict=strict,
    )

    print(f"[DONE] Processed {len(output_files)} files.")

    if args.lp_results_dir is not None or args.topology_db is not None:
        if args.lp_results_dir is None:
            raise ValueError(
                "--lp-results-dir is required for plotting all rho values."
            )

        if args.topology_db is None:
            raise ValueError(
                "--topology-db is required for plotting all rho values."
            )

        plot_files = create_scatter_plots_for_results_directory(
            results_directory=args.results_dir,
            lp_results_directory=args.lp_results_dir,
            topology_file=args.topology_db,
            plot_output_directory=args.plot_output_dir,
            overwritten=args.overwrite,
        )

        print(f"[DONE] Generated {len(plot_files)} scatter plots.")


if __name__ == "__main__":
    main()