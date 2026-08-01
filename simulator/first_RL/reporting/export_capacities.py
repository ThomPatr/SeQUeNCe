from __future__ import annotations

import json
from pathlib import Path

from simulator.first_RL.metrics.link_metrics import LINK_METRICS


def export_empirical_link_capacities(
    observation_time_s: float,
    output_file: str | Path,
) -> dict:
    """
    Export empirical elementary-link capacities measured in SeQUeNCe.

    For every physical link:

        p_gen = successes / attempts

        attempt_rate = attempts / observation_time

        capacity = successes / observation_time

    Capacity unit:
        successfully generated elementary EPR pairs per second.
    """
    if observation_time_s <= 0:
        raise ValueError(
            "observation_time_s must be greater than zero."
        )

    exported_data = {}

    for link, statistics in sorted(LINK_METRICS.items()):
        attempts = int(statistics["eg_attempts"])
        successes = int(statistics["eg_successes"])

        p_generation = (
            successes / attempts
            if attempts > 0
            else 0.0
        )

        attempt_rate_per_second = (
            attempts / observation_time_s
        )

        

        link_name = f"{link[0]}-{link[1]}"

        exported_data[link_name] = {
            "node_a": link[0],
            "node_b": link[1],
            "observation_time_s": observation_time_s,
            "eg_attempts": attempts,
            "eg_successes": successes,
            "p_generation": p_generation,
            "attempt_rate_per_second":
                attempt_rate_per_second,
           
                
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
        json.dump(
            exported_data,
            file,
            indent=4,
        )

    print(
        f"[CAPACITY] Empirical capacities saved in "
        f"{output_path}"
    )

    print(
        "\n================ EMPIRICAL LINK CAPACITIES ================\n"
    )

    for link_name, data in exported_data.items():
        print(f"Link {link_name}")
        print(
            f"  attempts      : "
            f"{data['eg_attempts']}"
        )
        print(
            f"  successes     : "
            f"{data['eg_successes']}"
        )
        print(
            f"  p_gen         : "
            f"{data['p_generation']:.8f}"
        )
        print(
            f"  attempt rate  : "
            f"{data['attempt_rate_per_second']:.6f} attempts/s"
        )
        print(
            f"  capacity      : "
            f"{data['capacity_pairs_per_second']:.6f} pairs/s"
        )
        print()

    return exported_data