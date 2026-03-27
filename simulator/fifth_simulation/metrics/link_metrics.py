from collections import defaultdict
from config import PHYSICAL_LINKS
from physics.link_utils import normalize_link

LINK_METRICS = defaultdict(lambda: {
    "eg_attempts": 0,
    "eg_successes": 0,
    "observed_creations": 0,
    "pair_records": []
})

ACTIVE_PAIRS = {}


def canonical_side(node_a: str, node_b: str) -> str:
    return min(node_a, node_b)


def is_canonical_observer(local_node: str, remote_node: str) -> bool:
    return local_node == canonical_side(local_node, remote_node)


def register_entanglement_attempt(src: str, dst: str, attempts: int = 1):
    key = normalize_link(src, dst)
    LINK_METRICS[key]["eg_attempts"] += attempts


def register_pair_creation(local_node: str, memory, remote_node: str, fidelity: float, entangle_time_ps: int):
    if remote_node is None or fidelity <= 0:
        return

    if not is_canonical_observer(local_node, remote_node):
        return

    link_key = normalize_link(local_node, remote_node)
    pair_key = (local_node, memory.name)

    if pair_key in ACTIVE_PAIRS:
        return

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
        "is_elementary": link_key in PHYSICAL_LINKS,
    }

    ACTIVE_PAIRS[pair_key] = record
    LINK_METRICS[link_key]["observed_creations"] += 1

    if link_key in PHYSICAL_LINKS:
        LINK_METRICS[link_key]["eg_successes"] += 1

def register_pair_discard(local_node: str, memory, discard_time_ps: int,
                          fidelity_before_reset: float, reason: str = "reset_to_RAW"):
    pair_key = (local_node, memory.name)

    if pair_key not in ACTIVE_PAIRS:
        return

    record = ACTIVE_PAIRS.pop(pair_key)
    record["discard_time_ps"] = discard_time_ps
    record["observed_lifetime_ps"] = discard_time_ps - record["creation_time_ps"]
    record["fidelity_at_discard"] = fidelity_before_reset
    record["discard_reason"] = reason

    LINK_METRICS[record["link"]]["pair_records"].append(record)