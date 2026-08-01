from __future__ import annotations

from copy import deepcopy


def scale_poisson_traffic(
    traffic_matrix: dict,
    rho: float,
) -> dict:
    """
    Scale a Poisson traffic matrix by multiplying every request
    arrival rate by the load factor rho.

    For each flow d:

        lambda_d(rho) = rho * lambda_d

    while memory_size and target_fidelity remain unchanged.

    Therefore, the average offered traffic becomes:

        h_d(rho) = rho * lambda_d * memory_size.
    """
    rho = float(rho)

    if rho <= 0:
        raise ValueError(
            "rho must be strictly positive."
        )

    scaled_traffic = deepcopy(traffic_matrix)

    for source_name, destinations in (
        scaled_traffic.items()
    ):
        for destination_name, demand in (
            destinations.items()
        ):
            if (
                "arrival_rate_requests_per_s"
                not in demand
            ):
                raise KeyError(
                    "Missing arrival_rate_requests_per_s "
                    f"for flow {source_name}"
                    f"->{destination_name}."
                )

            base_arrival_rate = float(
                demand[
                    "arrival_rate_requests_per_s"
                ]
            )

            if base_arrival_rate <= 0:
                raise ValueError(
                    "The base arrival rate must be positive "
                    f"for flow {source_name}"
                    f"->{destination_name}."
                )

            demand[
                "base_arrival_rate_requests_per_s"
            ] = base_arrival_rate

            demand[
                "arrival_rate_requests_per_s"
            ] = rho * base_arrival_rate

            demand["load_factor"] = rho

    return scaled_traffic
def compute_configured_offered_traffic(
    traffic_matrix: dict,
) -> float:
    """
    Return the aggregate average offered traffic in pairs/s.

    For each flow:

        h_d = lambda_d * memory_size.
    """
    total_rate = 0.0

    for destinations in traffic_matrix.values():
        for demand in destinations.values():
            arrival_rate = float(
                demand[
                    "arrival_rate_requests_per_s"
                ]
            )

            memory_size = int(
                demand["memory_size"]
            )

            total_rate += (
                arrival_rate * memory_size
            )

    return total_rate