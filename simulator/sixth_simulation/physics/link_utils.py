def endpoint_name(x):
    return x.name if hasattr(x, "name") else x


def ps_to_s(x_ps):
    return x_ps * 1e-12


def normalize_link(a, b):
    a_name = endpoint_name(a)
    b_name = endpoint_name(b)
    return tuple(sorted((a_name, b_name)))


def logical_link_from_qchannel(qc):
    """
    Map a physical qchannel involving a BSM node back to the logical router-router link.
    """
    a = endpoint_name(qc.sender)
    b = endpoint_name(qc.receiver)

    if isinstance(a, str) and a.startswith("BSM."):
        parts = a.split(".")
        if len(parts) >= 3:
            return normalize_link(parts[1], parts[2])

    if isinstance(b, str) and b.startswith("BSM."):
        parts = b.split(".")
        if len(parts) >= 3:
            return normalize_link(parts[1], parts[2])

    return normalize_link(a, b)


def effective_attenuation_db_per_km(base_alpha_db_per_km: float,
                                    extra_loss_db: float,
                                    distance_m: float) -> float:
    distance_km = max(distance_m / 1000.0, 1e-9)
    return base_alpha_db_per_km + extra_loss_db / distance_km