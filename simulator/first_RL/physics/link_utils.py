def endpoint_name(x):
    """
    Return the name of an endpoint.

    The input can be either a SeQUeNCe object with a .name attribute
    or an already available string.
    """
    return x.name if hasattr(x, "name") else str(x)


def ps_to_s(x_ps):
    """
    Convert picoseconds to seconds.

    SeQUeNCe internally represents simulation time in picoseconds.
    """
    return x_ps * 1e-12


def normalize_link(a, b):
    """
    Return a canonical undirected representation of a link.

    This avoids treating (A, B) and (B, A) as two different links.
    """
    a_name = endpoint_name(a)
    b_name = endpoint_name(b)
    return tuple(sorted((a_name, b_name)))


def logical_link_from_qchannel(qc):
    """
    Map a physical quantum channel involving a BSM node to the logical
    router-router link.

    In SeQUeNCe, an elementary router-router quantum link is often
    represented by two physical quantum channels connected through a BSM node.
    This function recovers the logical link between the two routers.
    """
    a = endpoint_name(qc.sender)
    b = endpoint_name(qc.receiver)

    for endpoint in (a, b):
        if isinstance(endpoint, str) and endpoint.startswith("BSM."):
            parts = endpoint.split(".")
            if len(parts) >= 3:
                return normalize_link(parts[1], parts[2])

    return normalize_link(a, b)


def effective_attenuation_db_per_km(
    base_alpha_db_per_km: float,
    extra_loss_db: float,
    distance_m: float,
) -> float:
    """
    Convert an additional lumped loss into an equivalent attenuation per km.

    The returned value is:

        alpha_eff = alpha_base + extra_loss / distance_km

    This is useful when a fixed device loss must be absorbed into the
    channel attenuation parameter.
    """
    distance_km = max(distance_m / 1000.0, 1e-9)
    return base_alpha_db_per_km + extra_loss_db / distance_km