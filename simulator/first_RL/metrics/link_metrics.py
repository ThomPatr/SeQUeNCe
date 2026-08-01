from collections import defaultdict

from simulator.first_RL.config import PHYSICAL_LINKS
from simulator.first_RL.physics.link_utils import normalize_link


LINK_METRICS = defaultdict(lambda: {
    "eg_attempts": 0,
    "eg_successes": 0,
    "observed_creations": 0,
    "pair_records": [],
})

ACTIVE_PAIRS = {}



def canonical_side(node_a: str, node_b: str) -> str:
    """
    Return the canonical observer of a link.

    Since an entangled pair is visible from both endpoint nodes, only one side
    is allowed to record link-level metrics. This avoids double counting.
    """
    return min(node_a, node_b)


def is_canonical_observer(local_node: str, remote_node: str) -> bool:
    """
    Check whether local_node is the canonical endpoint of the link.
    """
    if remote_node is None:
        return False
    return local_node == canonical_side(local_node, remote_node)


def register_entanglement_attempt(src: str, dst: str, attempts: int = 1):
    """
    Register an elementary entanglement-generation attempt on a physical link.
    """
    if src is None or dst is None or attempts <= 0:
        return

    key = normalize_link(src, dst)
    LINK_METRICS[key]["eg_attempts"] += attempts


def _pair_key(local_node: str, memory) -> tuple[str, str]:
    """
    Build a unique key for a local memory record.
    """
    return local_node, memory.name


def register_pair_creation(
    local_node: str,
    memory,
    remote_node: str,
    fidelity: float,
    entangle_time_ps: int,
):
    """
    Register the creation of an entangled pair.

    If the same local memory already has an active record, the previous
    record is closed before the new one is created. This prevents one
    memory record from incorrectly spanning multiple entanglement cycles.
    """
    if remote_node is None or fidelity <= 0:
        return

    if not is_canonical_observer(
        local_node,
        remote_node,
    ):
        return

    link_key = normalize_link(
        local_node,
        remote_node,
    )

    pair_key = (
        local_node,
        memory.name,
    )

    # Close a previous still-open record for the same memory.
    if pair_key in ACTIVE_PAIRS:
        previous = ACTIVE_PAIRS.pop(pair_key)

        previous["discard_time_ps"] = entangle_time_ps

        previous["observed_lifetime_ps"] = max(
            0,
            entangle_time_ps
            - previous["creation_time_ps"],
        )

        previous["fidelity_at_discard"] = None

        previous["discard_reason"] = (
            "replaced_by_new_creation"
        )

        LINK_METRICS[
            previous["link"]
        ]["pair_records"].append(previous)

    record = {
        "link": link_key,
        "local_node": local_node,
        "remote_node": remote_node,
        "memory_name": memory.name,
        "creation_time_ps": entangle_time_ps,
        "discard_time_ps": None,
        "observed_lifetime_ps": None,
        "fidelity_at_creation": fidelity,
        "fidelity_at_discard": None,
        "discard_reason": None,
        "is_elementary":
            link_key in PHYSICAL_LINKS,
    }

    ACTIVE_PAIRS[pair_key] = record

    LINK_METRICS[
        link_key
    ]["observed_creations"] += 1

    if link_key in PHYSICAL_LINKS:
        LINK_METRICS[
            link_key
        ]["eg_successes"] += 1


def register_pair_discard(
    local_node: str,
    memory,
    discard_time_ps: int,
    fidelity_before_reset: float,
    reason: str = "reset_to_RAW",
):
    """
    Register the discard/reset/consumption of an active entangled pair.
    """
    pair_key = _pair_key(local_node, memory)

    if pair_key not in ACTIVE_PAIRS:
        return

    record = ACTIVE_PAIRS.pop(pair_key)

    record["discard_time_ps"] = discard_time_ps
    record["observed_lifetime_ps"] = max(
        0,
        discard_time_ps - record["creation_time_ps"],
    )
    record["fidelity_at_discard"] = fidelity_before_reset
    record["discard_reason"] = reason

    LINK_METRICS[record["link"]]["pair_records"].append(record)


def get_link_metrics():
    """
    Return the global link metrics dictionary.
    """
    return LINK_METRICS


def get_active_pairs():
    """
    Return the currently active pairs.
    """
    return ACTIVE_PAIRS


def reset_link_metrics():
    """
    Reset all metrics before a new simulation run.
    """
    LINK_METRICS.clear()
    ACTIVE_PAIRS.clear()