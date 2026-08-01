from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np


RHO_PATTERN = re.compile(r"rho[_-](\d+(?:\.\d+)?)", re.IGNORECASE)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def parse_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_index_to_node(topology_file: Path) -> dict[int, str]:
    """Load the integer-to-router-name mapping used by the LP."""
    raw = load_json(topology_file)

    if isinstance(raw, dict) and "index_to_node" in raw:
        return {int(index): str(name) for index, name in raw["index_to_node"].items()}

    if isinstance(raw, dict) and "node_to_index" in raw:
        return {int(index): str(name) for name, index in raw["node_to_index"].items()}

    if isinstance(raw, dict) and isinstance(raw.get("nodes"), list):
        mapping: dict[int, str] = {}
        for node in raw["nodes"]:
            if not isinstance(node, dict):
                continue
            index = node.get("id", node.get("index"))
            name = node.get("name", node.get("label"))
            if index is not None and name is not None:
                mapping[int(index)] = str(name)
        if mapping:
            return mapping

    raise KeyError(f"Unable to find a node-index mapping in {topology_file}.")


def resolve_lp_node_name(value: Any, index_to_node: dict[int, str]) -> str:
    """Convert an LP node identifier to the SeQUeNCe router name."""
    if isinstance(value, str) and not value.strip().lstrip("-").isdigit():
        return value

    try:
        index = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid LP node identifier: {value!r}") from error

    if index not in index_to_node:
        raise KeyError(f"Unknown LP node index: {index}")

    return index_to_node[index]


def extract_rho(path: Path, summary: dict | None = None) -> float:
    if summary is not None and "rho" in summary:
        return float(summary["rho"])

    match = RHO_PATTERN.search(path.name)
    if match is None:
        match = RHO_PATTERN.search(str(path))

    if match is None:
        raise ValueError(f"Unable to infer rho from {path}")

    return float(match.group(1))


def find_sequence_runs(sequence_results_dir: Path) -> list[dict[str, Any]]:
    """
    Discover SeQUeNCe experiment directories.

    Expected files in each directory:
        summary.json
        flow_statistics.json
        flow_protocol_statistics.json
        link_statistics.json
        traffic_statistics.json
        session_history.json
    """
    runs: list[dict[str, Any]] = []

    for summary_file in sequence_results_dir.rglob("summary.json"):
        run_dir = summary_file.parent
        summary = load_json(summary_file)

        files = {
            "summary": summary_file,
            "flow_statistics": run_dir / "flow_statistics.json",
            "flow_protocol_statistics": run_dir / "flow_protocol_statistics.json",
            "link_statistics": run_dir / "link_statistics.json",
            "traffic_statistics": run_dir / "traffic_statistics.json",
            "session_history": run_dir / "session_history.json",
        }

        runs.append(
            {
                "rho": extract_rho(run_dir, summary),
                "directory": run_dir,
                "summary": summary,
                "files": files,
            }
        )

    return sorted(runs, key=lambda item: item["rho"])


def load_lp_sweep(lp_sweep_file: Path) -> list[dict[str, Any]]:
    rows = load_csv(lp_sweep_file)
    results = []

    for row in rows:
        results.append(
            {
                "rho": float(row["load_factor"]),
                "feasible": str(row["feasible"]).lower() == "true",
                "offered_traffic": parse_optional_float(
                    row.get("offered_traffic_pairs_per_second")
                ),
                "objective": parse_optional_float(row.get("objective")),
                "average_hop_count": parse_optional_float(
                    row.get("average_hop_count")
                ),
                "maximum_link_utilization": parse_optional_float(
                    row.get("maximum_link_utilization")
                ),
                "maximum_node_memory_utilization": parse_optional_float(
                    row.get("maximum_node_memory_utilization")
                ),
                "link_bottleneck_edge": row.get(
                    "link_bottleneck_edge",
                    row.get("bottleneck_edge"),
                ),
                "memory_bottleneck_node": row.get(
                    "memory_bottleneck_node"
                ),
                "status": row.get("status"),
            }
        )

    return sorted(results, key=lambda item: item["rho"])


def save_figure(fig: plt.Figure, output_dir: Path, name: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    png_file = output_dir / f"{name}.png"
    pdf_file = output_dir / f"{name}.pdf"
    fig.tight_layout()
    fig.savefig(png_file, dpi=300, bbox_inches="tight")
    fig.savefig(pdf_file, bbox_inches="tight")
    plt.close(fig)
    print(f"[PLOT] {png_file}")
    print(f"[PLOT] {pdf_file}")


def nearest_lp_record(
    lp_rows: list[dict[str, Any]],
    rho: float,
    tolerance: float = 1e-6,
) -> dict[str, Any] | None:
    candidates = [
        row
        for row in lp_rows
        if abs(row["rho"] - rho) <= tolerance
    ]

    if candidates:
        return candidates[0]

    if not lp_rows:
        return None

    return min(lp_rows, key=lambda row: abs(row["rho"] - rho))


def estimate_lp_saturation_rho(lp_rows: list[dict[str, Any]]) -> float | None:
    """Estimate the continuous LP saturation point from resource utilization."""
    estimates = []

    for row in lp_rows:
        if not row.get("feasible", False):
            continue

        utilizations = [
            float(value)
            for value in (
                row.get("maximum_link_utilization"),
                row.get("maximum_node_memory_utilization"),
            )
            if value is not None and float(value) > 0.0
        ]

        if utilizations:
            estimates.append(float(row["rho"]) / max(utilizations))

    return min(estimates) if estimates else None


def plot_delivery_ratio_lp_vs_sequence(
    sequence_runs: list[dict[str, Any]],
    lp_rows: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    """
    Plot the measured SeQUeNCe delivery ratio together with the LP
    full-demand feasibility boundary.

    The LP does not predict a post-saturation delivery ratio because its
    demand constraints require the complete traffic matrix to be served.
    Therefore, the figure shows:
      - the measured SeQUeNCe delivery ratio;
      - the LP feasible and infeasible operating regions;
      - the estimated LP saturation threshold.
    """
    sequence_rho = [
        float(run["rho"])
        for run in sequence_runs
    ]
    sequence_delivery_ratio = [
        float(
            run["summary"].get(
                "offered_delivery_ratio",
                0.0,
            )
        )
        for run in sequence_runs
    ]

    saturation_rho = estimate_lp_saturation_rho(
        lp_rows
    )

    if saturation_rho is None:
        print(
            "[SKIP] Unable to estimate the LP "
            "saturation point."
        )
        return

    all_rho_values = [
        *sequence_rho,
        *[
            float(row["rho"])
            for row in lp_rows
        ],
    ]

    x_min = min(all_rho_values + [0.0])
    x_max = max(all_rho_values)

    x_margin = max(
        0.1,
        0.03 * (x_max - x_min),
    )
    plot_min = max(
        0.0,
        x_min - x_margin,
    )
    plot_max = x_max + x_margin

    fig, ax = plt.subplots(
        figsize=(9, 5.2)
    )

    ax.axvspan(
        plot_min,
        saturation_rho,
        alpha=0.12,
        label="LP feasible region",
    )

    ax.axvspan(
        saturation_rho,
        plot_max,
        alpha=0.08,
        hatch="//",
        label="LP infeasible region",
    )

    ax.plot(
        sequence_rho,
        sequence_delivery_ratio,
        marker="o",
        linewidth=2.2,
        label="SeQUeNCe measured delivery ratio",
    )

    ax.axvline(
        saturation_rho,
        linestyle="--",
        linewidth=1.8,
        label=(
            rf"LP full-demand feasibility limit "
            rf"$\rho^*={saturation_rho:.2f}$"
        ),
    )

    ax.text(
        (
            plot_min
            + saturation_rho
        )
        / 2.0,
        1.015,
        "LP: full demand feasible",
        ha="center",
        va="bottom",
        fontsize=10,
    )

    ax.text(
        (
            saturation_rho
            + plot_max
        )
        / 2.0,
        1.015,
        "LP: full demand infeasible",
        ha="center",
        va="bottom",
        fontsize=10,
    )

    ax.set_xlabel(
        r"Traffic load factor $\rho$"
    )
    ax.set_ylabel(
        "SeQUeNCe delivery ratio"
    )
    ax.set_xlim(
        plot_min,
        plot_max,
    )
    ax.set_ylim(
        0.0,
        1.08,
    )
    ax.grid(
        True,
        alpha=0.3,
    )
    ax.legend(
        loc="lower left",
    )
    ax.set_title(
        "End-to-end delivery ratio and LP feasibility boundary"
    )

    save_figure(
        fig,
        output_dir,
        "01_delivery_ratio_and_lp_feasibility",
    )


def plot_throughput_vs_offered_traffic(
    sequence_runs: list[dict[str, Any]],
    lp_rows: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    """
    Compare LP offered traffic with the throughput delivered by SeQUeNCe,
    while highlighting the LP feasible and infeasible operating regions.
    """

    sequence_rho = [
        float(run["rho"])
        for run in sequence_runs
    ]

    sequence_throughput = [
        float(
            run["summary"].get(
                "throughput_pairs_per_s",
                0.0,
            )
        )
        for run in sequence_runs
    ]

    lp_valid_rows = [
        row
        for row in lp_rows
        if row.get("offered_traffic") is not None
    ]

    lp_rho = [
        float(row["rho"])
        for row in lp_valid_rows
    ]

    lp_offered = [
        float(row["offered_traffic"])
        for row in lp_valid_rows
    ]

    saturation_rho = estimate_lp_saturation_rho(
        lp_rows
    )

    if saturation_rho is None:
        print(
            "[SKIP] Unable to estimate the LP "
            "saturation point."
        )
        return

    all_rho_values = [
        *sequence_rho,
        *lp_rho,
    ]

    x_min = min(all_rho_values + [0.0])
    x_max = max(all_rho_values)

    x_margin = max(
        0.1,
        0.03 * (x_max - x_min),
    )

    plot_min = max(
        0.0,
        x_min - x_margin,
    )

    plot_max = x_max + x_margin

    # Create the figure only once.
    fig, ax = plt.subplots(
        figsize=(9, 5.2)
    )

    # Feasible operating region.
    ax.axvspan(
        plot_min,
        saturation_rho,
        alpha=0.12,
        zorder=0,
    )

    # Infeasible operating region.
    ax.axvspan(
        saturation_rho,
        plot_max,
        alpha=0.08,
        hatch="//",
        zorder=0,
    )

    # LP feasibility boundary.
    ax.axvline(
        saturation_rho,
        linestyle="--",
        linewidth=1.8,
        label=(
            rf"LP full-demand feasibility limit "
            rf"$\rho^*={saturation_rho:.2f}$"
        ),
        zorder=2,
    )

    # SeQUeNCe delivered throughput.
    ax.plot(
        sequence_rho,
        sequence_throughput,
        marker="o",
        linewidth=2.0,
        label="SeQUeNCe delivered throughput",
        zorder=3,
    )

    # LP offered traffic only.
    ax.plot(
        lp_rho,
        lp_offered,
        marker="s",
        linestyle=":",
        linewidth=2.0,
        label="LP offered traffic",
        zorder=3,
    )

    maximum_y = max(
        sequence_throughput
        + lp_offered
        + [1.0]
    )

    y_max = maximum_y * 1.10

    ax.text(
        (
            plot_min
            + saturation_rho
        )
        / 2.0,
        y_max * 0.97,
        "LP: full demand feasible",
        ha="center",
        va="top",
        fontsize=10,
    )

    ax.text(
        (
            saturation_rho
            + plot_max
        )
        / 2.0,
        y_max * 0.97,
        "LP: full demand infeasible",
        ha="center",
        va="top",
        fontsize=10,
    )

    ax.set_xlabel(
        r"Traffic load factor $\rho$"
    )

    ax.set_ylabel(
        "Pairs/s"
    )

    ax.set_xlim(
        plot_min,
        plot_max,
    )

    ax.set_ylim(
        0.0,
        y_max,
    )

    ax.grid(
        True,
        alpha=0.3,
    )

    ax.legend(
        loc="lower right",
    )

    ax.set_title(
        "LP offered traffic and SeQUeNCe delivered throughput"
    )

    save_figure(
        fig,
        output_dir,
        "02_throughput_vs_offered_traffic",
    )

def plot_max_link_utilization(
    sequence_runs: list[dict[str, Any]],
    lp_rows: list[dict[str, Any]],
    link_capacities_file: Path | None,
    output_dir: Path,
) -> None:
    """
    LP maximum utilization is read directly from load_sweep_summary.csv.

    SeQUeNCe utilization is estimated as:
        observed success_rate_pairs_per_s / calibrated link capacity.
    """
    capacities: dict[str, float] = {}

    if link_capacities_file is not None and link_capacities_file.exists():
        raw_capacities = load_json(link_capacities_file)

        for key, value in raw_capacities.items():
            if isinstance(value, dict):
                capacity = value.get(
                    "service_capacity_pairs_per_second",
                    value.get("capacity_pairs_per_second"),
                )
                node_a = value.get("node_a")
                node_b = value.get("node_b")

                if node_a is not None and node_b is not None and capacity is not None:
                    capacities["-".join(sorted((str(node_a), str(node_b))))] = float(
                        capacity
                    )
                elif capacity is not None:
                    capacities[key] = float(capacity)
            else:
                capacities[key] = float(value)

    sequence_rho = []
    sequence_max_utilization = []

    for run in sequence_runs:
        link_file = run["files"]["link_statistics"]

        if not link_file.exists() or not capacities:
            continue

        link_records = load_json(link_file)
        utilizations = []

        for record in link_records:
            node_a = str(record["node_a"])
            node_b = str(record["node_b"])
            key = "-".join(sorted((node_a, node_b)))
            capacity = capacities.get(key)

            if capacity is None or capacity <= 0:
                continue

            observed_rate = record.get(
                "success_rate_pairs_per_s",
                record.get("raw_creation_rate_pairs_per_second", 0.0),
            )
            utilizations.append(float(observed_rate) / capacity)

        if utilizations:
            sequence_rho.append(run["rho"])
            sequence_max_utilization.append(max(utilizations))

    lp_rho = [
        row["rho"]
        for row in lp_rows
        if row["maximum_link_utilization"] is not None
    ]
    lp_utilization = [
        float(row["maximum_link_utilization"])
        for row in lp_rows
        if row["maximum_link_utilization"] is not None
    ]

    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.plot(
        lp_rho,
        np.array(lp_utilization) * 100.0,
        marker="o",
        label="LP maximum link utilization",
    )

    if sequence_rho:
        ax.plot(
            sequence_rho,
            np.array(sequence_max_utilization) * 100.0,
            marker="s",
            label="SeQUeNCe maximum observed utilization",
        )

    ax.axhline(100.0, linestyle="--", label="Capacity limit")
    ax.set_xlabel(r"Traffic load factor $\rho$")
    ax.set_ylabel("Maximum link utilization (%)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    ax.set_title("Maximum physical-link utilization")
    save_figure(fig, output_dir, "03_maximum_link_utilization")


def plot_flow_throughput_at_rho(
    sequence_runs: list[dict[str, Any]],
    lp_results_dir: Path,
    selected_rho: float,
    output_dir: Path,
    index_to_node: dict[int, str],
) -> None:
    """Compare identical named source-destination flows in LP and SeQUeNCe."""
    sequence_run = min(sequence_runs, key=lambda run: abs(run["rho"] - selected_rho))
    actual_rho = float(sequence_run["rho"])
    flow_file = sequence_run["files"]["flow_statistics"]

    if not flow_file.exists():
        print("[SKIP] flow_statistics.json not found.")
        return

    sequence_rates = {
        f"{record['source']}->{record['destination']}": float(record.get("throughput_pairs_per_s", 0.0))
        for record in load_json(flow_file)
    }

    lp_result_file = lp_results_dir / f"traffic_load_{actual_rho:.2f}_result.json"
    if not lp_result_file.exists():
        print(f"[SKIP] Exact LP result file not found: {lp_result_file}")
        return

    lp_result = load_json(lp_result_file)
    if not lp_result.get("feasible", False):
        print(f"[SKIP] LP is infeasible at rho={actual_rho:.2f}; no flow allocation exists.")
        return

    lp_rates: dict[str, float] = {}
    for route in lp_result.get("routing", []):
        source_name = resolve_lp_node_name(route.get("source"), index_to_node)
        destination_name = resolve_lp_node_name(route.get("target"), index_to_node)
        flow_key = f"{source_name}->{destination_name}"
        lp_rates[flow_key] = lp_rates.get(flow_key, 0.0) + float(
            route.get("allocated_end_to_end_rate", 0.0)
        )

    common_flows = sorted(set(sequence_rates) & set(lp_rates))
    missing_in_lp = sorted(set(sequence_rates) - set(lp_rates))
    missing_in_sequence = sorted(set(lp_rates) - set(sequence_rates))

    if missing_in_lp:
        print("[WARNING] Flows missing in LP:", missing_in_lp)
    if missing_in_sequence:
        print("[WARNING] Flows missing in SeQUeNCe:", missing_in_sequence)
    if not common_flows:
        print("[SKIP] No common source-destination flows were found.")
        return

    positions = np.arange(len(common_flows))
    width = 0.38
    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.bar(
        positions - width / 2,
        [lp_rates[flow] for flow in common_flows],
        width,
        label="LP allocated rate",
    )
    ax.bar(
        positions + width / 2,
        [sequence_rates[flow] for flow in common_flows],
        width,
        label="SeQUeNCe throughput",
    )
    ax.set_xticks(positions)
    ax.set_xticklabels(common_flows, rotation=35, ha="right")
    ax.set_ylabel("Pairs/s")
    ax.set_title(rf"Flow-level throughput comparison at $\rho={actual_rho:.2f}$")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    save_figure(fig, output_dir, f"04_flow_throughput_rho_{actual_rho:.2f}")


def plot_flow_delivery_ratio_heatmap(
    sequence_runs: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    """
    Heatmap-like matrix using Matplotlib imshow:
    rows = flows, columns = rho, values = delivery ratio.
    """
    values: dict[str, dict[float, float]] = {}

    for run in sequence_runs:
        flow_file = run["files"]["flow_statistics"]
        if not flow_file.exists():
            continue

        for record in load_json(flow_file):
            flow = f"{record['source']}->{record['destination']}"
            values.setdefault(flow, {})[run["rho"]] = float(
                record.get("delivery_ratio", 0.0)
            )

    if not values:
        print("[SKIP] No flow statistics available for delivery-ratio matrix.")
        return

    flows = sorted(values)
    rhos = sorted({run["rho"] for run in sequence_runs})
    matrix = np.full((len(flows), len(rhos)), np.nan)

    for row_index, flow in enumerate(flows):
        for column_index, rho in enumerate(rhos):
            if rho in values[flow]:
                matrix[row_index, column_index] = values[flow][rho]

    fig, ax = plt.subplots(figsize=(10, 5.8))
    image = ax.imshow(matrix, aspect="auto", vmin=0.0, vmax=1.0)
    ax.set_xticks(np.arange(len(rhos)))
    ax.set_xticklabels([f"{rho:g}" for rho in rhos])
    ax.set_yticks(np.arange(len(flows)))
    ax.set_yticklabels(flows)
    ax.set_xlabel(r"Traffic load factor $\rho$")
    ax.set_ylabel("Flow")
    ax.set_title("SeQUeNCe delivery ratio by flow and traffic load")
    fig.colorbar(image, ax=ax, label="Delivery ratio")
    save_figure(fig, output_dir, "05_flow_delivery_ratio_matrix")


def plot_latency_and_fidelity(
    sequence_runs: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    rhos = [run["rho"] for run in sequence_runs]
    latency = [
        parse_optional_float(run["summary"].get("average_latency_s"))
        for run in sequence_runs
    ]
    fidelity = [
        parse_optional_float(
            run["summary"].get("average_delivered_fidelity")
        )
        for run in sequence_runs
    ]

    latency_x = [rho for rho, value in zip(rhos, latency) if value is not None]
    latency_y = [value for value in latency if value is not None]

    if latency_x:
        fig, ax = plt.subplots(figsize=(9, 5.2))
        ax.plot(latency_x, latency_y, marker="o")
        ax.set_xlabel(r"Traffic load factor $\rho$")
        ax.set_ylabel("Average latency (s)")
        ax.grid(True, alpha=0.3)
        ax.set_title("SeQUeNCe end-to-end latency under increasing load")
        save_figure(fig, output_dir, "06_average_latency_vs_rho")

    fidelity_x = [rho for rho, value in zip(rhos, fidelity) if value is not None]
    fidelity_y = [value for value in fidelity if value is not None]

    if fidelity_x:
        fig, ax = plt.subplots(figsize=(9, 5.2))
        ax.plot(fidelity_x, fidelity_y, marker="o")
        ax.set_xlabel(r"Traffic load factor $\rho$")
        ax.set_ylabel("Average delivered fidelity")
        ax.grid(True, alpha=0.3)
        ax.set_title("SeQUeNCe delivered fidelity under increasing load")
        save_figure(fig, output_dir, "07_average_fidelity_vs_rho")


def plot_protocol_statistics(
    sequence_runs: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    """
    Plot normalized swapping cost and observed swapping success probability.

    The swapping-cost metric is:

        total swapping attempts / total delivered end-to-end pairs

    This is more informative than the absolute number of attempts because it
    shows how much protocol work is required, on average, to deliver one
    end-to-end Bell pair at each traffic load.
    """
    rhos = []
    attempts_per_delivered_pair = []
    success_probability = []

    for run in sequence_runs:
        protocol_file = run["files"]["flow_protocol_statistics"]

        if not protocol_file.exists():
            continue

        records = load_json(protocol_file)
        total_attempts = 0
        total_successes = 0

        for record in records:
            swapping = record.get("swapping", {})
            total_attempts += int(
                swapping.get("attempts", 0)
            )
            total_successes += int(
                swapping.get("successes", 0)
            )

        total_delivered_pairs = int(
            run["summary"].get(
                "total_delivered_pairs",
                0,
            )
        )

        if total_delivered_pairs <= 0:
            print(
                f"[WARNING] No delivered pairs at "
                f"rho={float(run['rho']):.2f}; "
                "normalized swapping cost skipped."
            )
            continue

        rhos.append(float(run["rho"]))
        attempts_per_delivered_pair.append(
            total_attempts
            / total_delivered_pairs
        )
        success_probability.append(
            total_successes
            / total_attempts
            if total_attempts > 0
            else 0.0
        )

    if not rhos:
        print("[SKIP] No protocol statistics available.")
        return

    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.plot(
        rhos,
        attempts_per_delivered_pair,
        marker="o",
        linewidth=2.0,
    )
    ax.set_xlabel(r"Traffic load factor $\rho$")
    ax.set_ylabel(
        "Swapping attempts per delivered pair"
    )
    ax.grid(True, alpha=0.3)
    ax.set_title(
        "Swapping cost per delivered end-to-end Bell pair"
    )
    save_figure(
        fig,
        output_dir,
        "08_swapping_attempts_per_delivered_pair",
    )

    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.plot(
        rhos,
        np.array(success_probability) * 100.0,
        marker="o",
        linewidth=2.0,
    )
    ax.set_xlabel(r"Traffic load factor $\rho$")
    ax.set_ylabel(
        "Swapping success probability (%)"
    )
    ax.grid(True, alpha=0.3)
    ax.set_title(
        "Observed swapping success probability"
    )
    save_figure(
        fig,
        output_dir,
        "09_swapping_success_probability",
    )


def plot_lp_resource_utilizations(
    lp_rows: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    valid_rows = [
        row
        for row in lp_rows
        if row["maximum_link_utilization"] is not None
    ]

    if not valid_rows:
        return

    rhos = [row["rho"] for row in valid_rows]
    link_utilization = [
        100.0 * float(row["maximum_link_utilization"])
        for row in valid_rows
    ]

    memory_rows = [
        row
        for row in valid_rows
        if row["maximum_node_memory_utilization"] is not None
    ]

    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.plot(
        rhos,
        link_utilization,
        marker="o",
        label="Maximum link utilization",
    )

    if memory_rows:
        ax.plot(
            [row["rho"] for row in memory_rows],
            [
                100.0 * float(row["maximum_node_memory_utilization"])
                for row in memory_rows
            ],
            marker="s",
            label="Maximum memory utilization",
        )

    ax.axhline(100.0, linestyle="--", label="Capacity limit")
    ax.set_xlabel(r"Traffic load factor $\rho$")
    ax.set_ylabel("Utilization (%)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    ax.set_title("LP resource utilization and bottleneck activation")
    save_figure(fig, output_dir, "10_lp_link_and_memory_utilization")


def save_comparison_table(
    sequence_runs: list[dict[str, Any]],
    lp_rows: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "comparison_by_rho.csv"

    fieldnames = [
        "rho",
        "lp_feasible",
        "lp_offered_traffic_pairs_per_s",
        "lp_max_link_utilization",
        "lp_max_memory_utilization",
        "sequence_offered_traffic_pairs_per_s",
        "sequence_throughput_pairs_per_s",
        "sequence_delivery_ratio",
        "sequence_completion_probability",
        "sequence_average_fidelity",
        "sequence_average_latency_s",
    ]

    with output_file.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for run in sequence_runs:
            lp_record = nearest_lp_record(lp_rows, run["rho"])
            summary = run["summary"]

            writer.writerow(
                {
                    "rho": run["rho"],
                    "lp_feasible": (
                        lp_record["feasible"]
                        if lp_record is not None
                        else None
                    ),
                    "lp_offered_traffic_pairs_per_s": (
                        lp_record["offered_traffic"]
                        if lp_record is not None
                        else None
                    ),
                    "lp_max_link_utilization": (
                        lp_record["maximum_link_utilization"]
                        if lp_record is not None
                        else None
                    ),
                    "lp_max_memory_utilization": (
                        lp_record["maximum_node_memory_utilization"]
                        if lp_record is not None
                        else None
                    ),
                    "sequence_offered_traffic_pairs_per_s": summary.get(
                        "configured_offered_traffic_pairs_per_s"
                    ),
                    "sequence_throughput_pairs_per_s": summary.get(
                        "throughput_pairs_per_s"
                    ),
                    "sequence_delivery_ratio": summary.get(
                        "offered_delivery_ratio"
                    ),
                    "sequence_completion_probability": summary.get(
                        "completion_probability"
                    ),
                    "sequence_average_fidelity": summary.get(
                        "average_delivered_fidelity"
                    ),
                    "sequence_average_latency_s": summary.get(
                        "average_latency_s"
                    ),
                }
            )

    print(f"[TABLE] {output_file}")



def _canonical_link_name(node_a: Any, node_b: Any) -> str:
    return "-".join(sorted((str(node_a), str(node_b))))


def _load_capacity_map(link_capacities_file: Path | None) -> dict[str, float]:
    capacities: dict[str, float] = {}

    if link_capacities_file is None or not link_capacities_file.exists():
        return capacities

    raw = load_json(link_capacities_file)

    for key, value in raw.items():
        if isinstance(value, dict):
            capacity = value.get(
                "service_capacity_pairs_per_second",
                value.get(
                    "capacity_pairs_per_second",
                    value.get("capacity"),
                ),
            )
            node_a = value.get("node_a")
            node_b = value.get("node_b")

            if capacity is None:
                continue

            if node_a is not None and node_b is not None:
                capacities[_canonical_link_name(node_a, node_b)] = float(capacity)
            else:
                capacities[str(key)] = float(capacity)
        else:
            capacities[str(key)] = float(value)

    return capacities


def plot_physical_link_capacities(
    link_capacities_file: Path | None,
    output_dir: Path,
) -> None:
    capacities = _load_capacity_map(link_capacities_file)

    if not capacities:
        print("[SKIP] Link-capacity file unavailable or empty.")
        return

    ordered = sorted(capacities.items(), key=lambda item: item[1])
    labels = [item[0] for item in ordered]
    values = [item[1] for item in ordered]
    positions = np.arange(len(labels))

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(positions, values)
    ax.set_yticks(positions)
    ax.set_yticklabels(labels)
    ax.set_xlabel("Service capacity (pairs/s)")
    ax.set_ylabel("Physical link")
    ax.set_title("Empirically calibrated physical-link capacities")
    ax.grid(True, axis="x", alpha=0.3)
    save_figure(fig, output_dir, "00_physical_link_capacities")


def _selected_sequence_run(
    sequence_runs: list[dict[str, Any]],
    selected_rho: float,
) -> dict[str, Any]:
    return min(
        sequence_runs,
        key=lambda run: abs(run["rho"] - selected_rho),
    )


def plot_memory_characterization(
    sequence_runs: list[dict[str, Any]],
    selected_rho: float,
    output_dir: Path,
) -> None:
    run = _selected_sequence_run(sequence_runs, selected_rho)
    run_dir = run["directory"]

    statistics_file = run_dir / "memory_occupancy_statistics.json"
    records_file = run_dir / "memory_occupancy_records.json"

    if statistics_file.exists():
        statistics = load_json(statistics_file)
        node_statistics = statistics.get("node_statistics", [])

        if node_statistics:
            ordered = sorted(
                node_statistics,
                key=lambda item: float(item.get("average_holding_time_s", 0.0)),
            )
            labels = [str(item["node"]) for item in ordered]
            values = [
                float(item.get("average_holding_time_s", 0.0))
                for item in ordered
            ]
            positions = np.arange(len(labels))

            fig, ax = plt.subplots(figsize=(9, 5.5))
            ax.barh(positions, values)
            ax.set_yticks(positions)
            ax.set_yticklabels(labels)
            ax.set_xlabel("Average holding time (s)")
            ax.set_ylabel("Node")
            ax.set_title(
                rf"Average quantum-memory holding time at $\rho={run['rho']:.2f}$"
            )
            ax.grid(True, axis="x", alpha=0.3)
            save_figure(fig, output_dir, "11_average_memory_holding_time_by_node")

    if not records_file.exists():
        print("[SKIP] memory_occupancy_records.json not found.")
        return

    records = load_json(records_file)
    simulation_duration_s = float(
        run["summary"].get("simulation_duration_s", 0.0)
    )

    if simulation_duration_s <= 0:
        print("[SKIP] Invalid simulation duration for memory occupancy.")
        return

    memory_seconds_by_node: dict[str, float] = {}

    for record in records:
        node = str(record["node"])
        memory_seconds_by_node[node] = (
            memory_seconds_by_node.get(node, 0.0)
            + float(record.get("duration_s", 0.0))
        )

    average_occupancy = {
        node: total_seconds / simulation_duration_s
        for node, total_seconds in memory_seconds_by_node.items()
    }

    if not average_occupancy:
        print("[SKIP] No memory-occupancy records available.")
        return

    ordered = sorted(average_occupancy.items(), key=lambda item: item[1])
    labels = [item[0] for item in ordered]
    values = [item[1] for item in ordered]
    positions = np.arange(len(labels))

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.barh(positions, values)
    ax.set_yticks(positions)
    ax.set_yticklabels(labels)
    ax.set_xlabel("Average occupied memories")
    ax.set_ylabel("Node")
    ax.set_title(
        rf"Average quantum-memory occupancy at $\rho={run['rho']:.2f}$"
    )
    ax.grid(True, axis="x", alpha=0.3)
    save_figure(fig, output_dir, "12_average_memory_occupancy_by_node")


def save_flow_baseline_table_and_plots(
    sequence_runs: list[dict[str, Any]],
    selected_rho: float,
    output_dir: Path,
) -> None:
    run = _selected_sequence_run(sequence_runs, selected_rho)
    flow_file = run["files"]["flow_statistics"]

    if not flow_file.exists():
        print("[SKIP] flow_statistics.json not found.")
        return

    records = load_json(flow_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    table_file = output_dir / f"baseline_flow_table_rho_{run['rho']:.2f}.csv"

    fieldnames = [
        "flow",
        "completion_probability",
        "throughput_pairs_per_s",
        "average_fidelity",
        "average_latency_s",
        "delivery_ratio",
    ]

    with table_file.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for item in records:
            writer.writerow(
                {
                    "flow": f"{item['source']}->{item['destination']}",
                    "completion_probability": item.get(
                        "completion_probability"
                    ),
                    "throughput_pairs_per_s": item.get(
                        "throughput_pairs_per_s"
                    ),
                    "average_fidelity": item.get("average_fidelity"),
                    "average_latency_s": item.get("average_latency_s"),
                    "delivery_ratio": item.get("delivery_ratio"),
                }
            )

    print(f"[TABLE] {table_file}")

    flows = [f"{item['source']}->{item['destination']}" for item in records]
    positions = np.arange(len(flows))

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.bar(
        positions,
        [
            100.0 * float(item.get("completion_probability", 0.0))
            for item in records
        ],
    )
    ax.set_xticks(positions)
    ax.set_xticklabels(flows, rotation=35, ha="right")
    ax.set_ylabel("Completion probability (%)")
    ax.set_title(
        rf"Flow completion probability at $\rho={run['rho']:.2f}$"
    )
    ax.grid(True, axis="y", alpha=0.3)
    save_figure(fig, output_dir, "13_flow_completion_probability")

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.bar(
        positions,
        [
            float(item.get("throughput_pairs_per_s", 0.0))
            for item in records
        ],
    )
    ax.set_xticks(positions)
    ax.set_xticklabels(flows, rotation=35, ha="right")
    ax.set_ylabel("Throughput (pairs/s)")
    ax.set_title(
        rf"Flow throughput at $\rho={run['rho']:.2f}$"
    )
    ax.grid(True, axis="y", alpha=0.3)
    save_figure(fig, output_dir, "14_flow_throughput_sequence")


def plot_protocol_statistics_by_flow(
    sequence_runs: list[dict[str, Any]],
    selected_rho: float,
    output_dir: Path,
) -> None:
    run = _selected_sequence_run(sequence_runs, selected_rho)
    protocol_file = run["files"]["flow_protocol_statistics"]

    if not protocol_file.exists():
        print("[SKIP] flow_protocol_statistics.json not found.")
        return

    records = load_json(protocol_file)

    if not records:
        print("[SKIP] Empty flow protocol statistics.")
        return

    flows = [f"{item['source']}->{item['destination']}" for item in records]
    positions = np.arange(len(flows))

    swapping_attempts = [
        int(item.get("swapping", {}).get("attempts", 0))
        for item in records
    ]
    swapping_probability = [
        100.0 * float(
            item.get("swapping", {}).get("success_probability", 0.0)
        )
        for item in records
    ]

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.bar(positions, swapping_attempts)
    ax.set_xticks(positions)
    ax.set_xticklabels(flows, rotation=35, ha="right")
    ax.set_ylabel("Swapping attempts")
    ax.set_title(
        rf"Swapping attempts by flow at $\rho={run['rho']:.2f}$"
    )
    ax.grid(True, axis="y", alpha=0.3)
    save_figure(fig, output_dir, "15_swapping_attempts_by_flow")

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.bar(positions, swapping_probability)
    ax.set_xticks(positions)
    ax.set_xticklabels(flows, rotation=35, ha="right")
    ax.set_ylabel("Success probability (%)")
    ax.set_ylim(0.0, 105.0)
    ax.set_title(
        rf"Swapping success probability by flow at $\rho={run['rho']:.2f}$"
    )
    ax.grid(True, axis="y", alpha=0.3)
    save_figure(fig, output_dir, "16_swapping_success_probability_by_flow")

    purification_attempts = [
        int(item.get("purification", {}).get("attempts", 0))
        for item in records
    ]
    purification_gain = [
        item.get("purification", {}).get("average_fidelity_gain")
        for item in records
    ]

    if any(value > 0 for value in purification_attempts):
        fig, ax = plt.subplots(figsize=(11, 5.5))
        ax.bar(positions, purification_attempts)
        ax.set_xticks(positions)
        ax.set_xticklabels(flows, rotation=35, ha="right")
        ax.set_ylabel("Purification attempts")
        ax.set_title(
            rf"Purification attempts by flow at $\rho={run['rho']:.2f}$"
        )
        ax.grid(True, axis="y", alpha=0.3)
        save_figure(fig, output_dir, "17_purification_attempts_by_flow")

    if any(value is not None for value in purification_gain):
        plotted = [
            float(value) if value is not None else 0.0
            for value in purification_gain
        ]
        fig, ax = plt.subplots(figsize=(11, 5.5))
        ax.bar(positions, plotted)
        ax.set_xticks(positions)
        ax.set_xticklabels(flows, rotation=35, ha="right")
        ax.set_ylabel("Average fidelity gain")
        ax.set_title(
            rf"Average purification fidelity gain at $\rho={run['rho']:.2f}$"
        )
        ax.grid(True, axis="y", alpha=0.3)
        save_figure(fig, output_dir, "18_purification_fidelity_gain_by_flow")


def save_lp_saturation_summary(
    lp_rows: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    """Save the LP saturation threshold without obsolete objective/feasibility plots."""
    saturation_rho = estimate_lp_saturation_rho(lp_rows)
    if saturation_rho is None:
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "lp_saturation_summary.csv"
    feasible_rho = [float(row["rho"]) for row in lp_rows if row.get("feasible", False)]
    infeasible_rho = [float(row["rho"]) for row in lp_rows if not row.get("feasible", False)]

    with output_file.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "estimated_saturation_rho",
                "maximum_tested_feasible_rho",
                "first_tested_infeasible_rho",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "estimated_saturation_rho": saturation_rho,
                "maximum_tested_feasible_rho": max(feasible_rho) if feasible_rho else None,
                "first_tested_infeasible_rho": min(infeasible_rho) if infeasible_rho else None,
            }
        )

    print(f"[TABLE] {output_file}")


def _find_lp_result_file(
    lp_results_dir: Path,
    selected_rho: float,
) -> Path | None:
    candidates = [
        lp_results_dir / f"traffic_load_{selected_rho:.2f}_result.json",
        lp_results_dir / "traffic_without_purification_result.json",
        lp_results_dir / "traffic_feasibility_result.json",
    ]
    return next((path for path in candidates if path.exists()), None)


def plot_link_utilization_scatter(
    sequence_runs: list[dict[str, Any]],
    lp_results_dir: Path,
    link_capacities_file: Path | None,
    selected_rho: float,
    output_dir: Path,
) -> None:
    run = _selected_sequence_run(sequence_runs, selected_rho)
    lp_file = _find_lp_result_file(lp_results_dir, run["rho"])

    if lp_file is None:
        print("[SKIP] LP result JSON unavailable for utilization scatter.")
        return

    sequence_link_file = run["files"]["link_statistics"]

    if not sequence_link_file.exists():
        print("[SKIP] SeQUeNCe link_statistics.json unavailable.")
        return

    capacities = _load_capacity_map(link_capacities_file)

    if not capacities:
        print("[SKIP] Link capacities unavailable for utilization scatter.")
        return

    lp_result = load_json(lp_file)
    lp_utilization = {
        _canonical_link_name(*item["edge"]): float(item["utilization"])
        for item in lp_result.get("link_statistics", [])
    }

    sequence_utilization: dict[str, float] = {}

    for item in load_json(sequence_link_file):
        key = _canonical_link_name(item["node_a"], item["node_b"])
        capacity = capacities.get(key)

        if capacity is None or capacity <= 0:
            continue

        observed_rate = float(
            item.get(
                "success_rate_pairs_per_s",
                item.get("raw_creation_rate_pairs_per_second", 0.0),
            )
        )
        sequence_utilization[key] = observed_rate / capacity

    common_links = sorted(set(lp_utilization) & set(sequence_utilization))

    if not common_links:
        print("[SKIP] No common links for utilization scatter.")
        return

    x_values = [100.0 * lp_utilization[key] for key in common_links]
    y_values = [100.0 * sequence_utilization[key] for key in common_links]
    maximum = max(x_values + y_values + [1.0])

    fig, ax = plt.subplots(figsize=(7.2, 6.2))
    ax.scatter(x_values, y_values)

    for key, x_value, y_value in zip(common_links, x_values, y_values):
        ax.annotate(
            key,
            (x_value, y_value),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=8,
        )

    ax.plot([0, maximum], [0, maximum], linestyle="--", label="Perfect agreement")
    ax.set_xlim(0, maximum * 1.05)
    ax.set_ylim(0, maximum * 1.05)
    ax.set_xlabel("LP link utilization (%)")
    ax.set_ylabel("SeQUeNCe measured utilization (%)")
    ax.set_title(
        rf"Link-utilization validation at $\rho={run['rho']:.2f}$"
    )
    ax.grid(True, alpha=0.3)
    ax.legend()
    save_figure(fig, output_dir, "21_link_utilization_lp_vs_sequence_scatter")


def save_bottleneck_comparison(
    sequence_runs: list[dict[str, Any]],
    lp_rows: list[dict[str, Any]],
    link_capacities_file: Path | None,
    output_dir: Path,
) -> None:
    capacities = _load_capacity_map(link_capacities_file)

    if not capacities:
        print("[SKIP] Link capacities unavailable for bottleneck table.")
        return

    output_file = output_dir / "bottleneck_comparison.csv"
    output_dir.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", newline="", encoding="utf-8") as file:
        fieldnames = [
            "rho",
            "lp_bottleneck",
            "sequence_bottleneck",
            "same_bottleneck",
            "lp_max_utilization",
            "sequence_max_utilization",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for run in sequence_runs:
            link_file = run["files"]["link_statistics"]
            if not link_file.exists():
                continue

            sequence_records = []

            for item in load_json(link_file):
                key = _canonical_link_name(item["node_a"], item["node_b"])
                capacity = capacities.get(key)

                if capacity is None or capacity <= 0:
                    continue

                observed_rate = float(
                    item.get(
                        "success_rate_pairs_per_s",
                        item.get("raw_creation_rate_pairs_per_second", 0.0),
                    )
                )
                sequence_records.append(
                    {
                        "link": key,
                        "utilization": observed_rate / capacity,
                    }
                )

            if not sequence_records:
                continue

            sequence_bottleneck = max(
                sequence_records,
                key=lambda item: item["utilization"],
            )
            lp_record = nearest_lp_record(lp_rows, run["rho"])

            if lp_record is None:
                continue

            lp_link = str(lp_record.get("link_bottleneck_edge") or "")
            sequence_link = sequence_bottleneck["link"]

            writer.writerow(
                {
                    "rho": run["rho"],
                    "lp_bottleneck": lp_link,
                    "sequence_bottleneck": sequence_link,
                    "same_bottleneck": (
                        _canonical_link_name(*lp_link.split("-", 1))
                        == sequence_link
                        if "-" in lp_link
                        else False
                    ),
                    "lp_max_utilization": lp_record.get(
                        "maximum_link_utilization"
                    ),
                    "sequence_max_utilization": sequence_bottleneck[
                        "utilization"
                    ],
                }
            )

    print(f"[TABLE] {output_file}")

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate LP vs SeQUeNCe comparison plots from the LP sweep CSV "
            "and the per-rho SeQUeNCe result directories."
        )
    )
    parser.add_argument(
        "--lp-sweep",
        type=Path,
        required=True,
        help="Path to load_sweep_summary.csv.",
    )
    parser.add_argument(
        "--lp-results-dir",
        type=Path,
        required=True,
        help="Directory containing traffic_load_<rho>_result.json files.",
    )
    parser.add_argument(
        "--sequence-results-dir",
        type=Path,
        required=True,
        help="Root directory containing one SeQUeNCe result folder per rho.",
    )
    parser.add_argument(
        "--link-capacities",
        type=Path,
        default=None,
        help="Optional empirical_link_capacities.json.",
    )
    parser.add_argument(
        "--topology-db",
        type=Path,
        required=True,
        help="Path to topology_db.json used to map LP indices to router names.",
    )
    parser.add_argument(
        "--selected-rho",
        type=float,
        default=1.0,
        help="Rho used for the flow-level grouped bar chart.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("comparison_plots"),
        help="Directory in which PNG, PDF and CSV outputs are saved.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    lp_rows = load_lp_sweep(args.lp_sweep)
    sequence_runs = find_sequence_runs(args.sequence_results_dir)
    index_to_node = load_index_to_node(args.topology_db)

    if not lp_rows:
        raise RuntimeError("No LP sweep records were found.")

    if not sequence_runs:
        raise RuntimeError(
            "No SeQUeNCe runs containing summary.json were found."
        )

    print(f"[INPUT] LP sweep records: {len(lp_rows)}")
    print(f"[INPUT] SeQUeNCe runs: {len(sequence_runs)}")

    save_comparison_table(
        sequence_runs=sequence_runs,
        lp_rows=lp_rows,
        output_dir=args.output_dir,
    )
    plot_delivery_ratio_lp_vs_sequence(
        sequence_runs=sequence_runs,
        lp_rows=lp_rows,
        output_dir=args.output_dir,
    )
    plot_throughput_vs_offered_traffic(
        sequence_runs=sequence_runs,
        lp_rows=lp_rows,
        output_dir=args.output_dir,
    )
    plot_max_link_utilization(
        sequence_runs=sequence_runs,
        lp_rows=lp_rows,
        link_capacities_file=args.link_capacities,
        output_dir=args.output_dir,
    )
    plot_flow_throughput_at_rho(
        sequence_runs=sequence_runs,
        lp_results_dir=args.lp_results_dir,
        selected_rho=args.selected_rho,
        output_dir=args.output_dir,
        index_to_node=index_to_node,
    )
    plot_flow_delivery_ratio_heatmap(
        sequence_runs=sequence_runs,
        output_dir=args.output_dir,
    )
    plot_latency_and_fidelity(
        sequence_runs=sequence_runs,
        output_dir=args.output_dir,
    )
    plot_protocol_statistics(
        sequence_runs=sequence_runs,
        output_dir=args.output_dir,
    )
    plot_lp_resource_utilizations(
        lp_rows=lp_rows,
        output_dir=args.output_dir,
    )

    plot_physical_link_capacities(
        link_capacities_file=args.link_capacities,
        output_dir=args.output_dir,
    )
    plot_memory_characterization(
        sequence_runs=sequence_runs,
        selected_rho=args.selected_rho,
        output_dir=args.output_dir,
    )
    save_flow_baseline_table_and_plots(
        sequence_runs=sequence_runs,
        selected_rho=args.selected_rho,
        output_dir=args.output_dir,
    )
    plot_protocol_statistics_by_flow(
        sequence_runs=sequence_runs,
        selected_rho=args.selected_rho,
        output_dir=args.output_dir,
    )
    save_lp_saturation_summary(
        lp_rows=lp_rows,
        output_dir=args.output_dir,
    )
    save_bottleneck_comparison(
        sequence_runs=sequence_runs,
        lp_rows=lp_rows,
        link_capacities_file=args.link_capacities,
        output_dir=args.output_dir,
    )

    print("\n[DONE] Comparative plots generated successfully.")


if __name__ == "__main__":
    main()