from types import MethodType

from sequence.topology.router_net_topo import RouterNetTopo
from metrics.link_metrics import register_entanglement_attempt, is_canonical_observer
from physics.link_utils import endpoint_name
from metrics.link_metrics import (
    is_canonical_observer,
    register_entanglement_attempt,
    register_pair_creation,
    register_pair_discard,
)


def extract_remote_node_from_protocol(protocol):
    for attr in ["remote_node_name", "remote_node", "other", "other_name"]:
        if hasattr(protocol, attr):
            value = getattr(protocol, attr)
            if isinstance(value, str):
                return value
            if hasattr(value, "name"):
                return value.name
    return None


def looks_like_generation_protocol(protocol) -> bool:
    cls_name = protocol.__class__.__name__.lower()
    mod_name = protocol.__class__.__module__.lower()

    return (
        "generation" in cls_name
        or "entanglementgeneration" in cls_name
        or "generation" in mod_name
    )


def patch_generation_protocol(protocol):
    if getattr(protocol, "_eg_metrics_patched", False):
        return

    if not hasattr(protocol, "start"):
        return

    original_start = protocol.start

    def wrapped_start(*args, **kwargs):
        owner_name = endpoint_name(protocol.owner)
        remote_name = extract_remote_node_from_protocol(protocol)

        if remote_name is not None and is_canonical_observer(owner_name, remote_name):
            register_entanglement_attempt(owner_name, remote_name, attempts=1)

        return original_start(*args, **kwargs)

    protocol.start = MethodType(wrapped_start, protocol) if not hasattr(original_start, "__self__") else wrapped_start
    protocol._eg_metrics_patched = True


def instrument_generation_protocols(topology: RouterNetTopo):
    all_nodes = []
    all_nodes.extend(topology.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER))
    all_nodes.extend(topology.get_nodes_by_type(RouterNetTopo.BSM_NODE))

    patched = 0
    for node in all_nodes:
        if not hasattr(node, "protocols"):
            continue
        for protocol in node.protocols:
            if looks_like_generation_protocol(protocol):
                patch_generation_protocol(protocol)
                patched += 1

    print(f"[SETUP] Patched {patched} entanglement-generation protocols for EG attempt counting.")


def patch_resource_manager_update(node):
    rm = node.resource_manager

    if getattr(rm, "_metrics_patched", False):
        return

    original_update = rm.update

    def wrapped_update(protocol, memory, state, *args, **kwargs):
        now_ps = node.timeline.now()

        old_remote_node = getattr(memory, "remote_node", None)
        old_fidelity = getattr(memory, "fidelity", 0)
        old_entangle_time = getattr(memory, "entangle_time", None)

        result = original_update(protocol, memory, state, *args, **kwargs)

        new_remote_node = getattr(memory, "remote_node", None)
        new_fidelity = getattr(memory, "fidelity", 0)
        new_entangle_time = getattr(memory, "entangle_time", None)

        if state != "RAW":
            if new_remote_node is not None and new_fidelity > 0:
                ent_time = new_entangle_time if new_entangle_time is not None and new_entangle_time >= 0 else now_ps
                register_pair_creation(
                    local_node=node.name,
                    memory=memory,
                    remote_node=new_remote_node,
                    fidelity=new_fidelity,
                    entangle_time_ps=ent_time
                )

        if state == "RAW":
            if old_remote_node is not None:
                register_pair_discard(
                    local_node=node.name,
                    memory=memory,
                    discard_time_ps=now_ps,
                    fidelity_before_reset=old_fidelity,
                    reason="reset_to_RAW"
                )

        return result

    rm.update = wrapped_update
    rm._metrics_patched = True


def instrument_resource_managers(topology: RouterNetTopo):
    routers = topology.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER)
    for node in routers:
        patch_resource_manager_update(node)
    print(f"[SETUP] Patched {len(routers)} resource managers for pair lifecycle tracking.")




def extract_remote_node_from_protocol(protocol):
    for attr in ["remote_node_name", "remote_node", "other", "other_name"]:
        if hasattr(protocol, attr):
            value = getattr(protocol, attr)
            if isinstance(value, str):
                return value
            if hasattr(value, "name"):
                return value.name
    return None


def patch_generation_class(protocol_cls):
    """
    Patch the start() method of a generation protocol class so that each
    real EG attempt is counted.
    """
    if getattr(protocol_cls, "_eg_class_patched", False):
        return

    original_start = protocol_cls.start

    def wrapped_start(self, *args, __original_start=original_start, **kwargs):
        owner_name = self.owner.name if hasattr(self.owner, "name") else str(self.owner)
        remote_name = extract_remote_node_from_protocol(self)

        if remote_name is not None:
            # count only once per logical link
            if is_canonical_observer(owner_name, remote_name):
                register_entanglement_attempt(owner_name, remote_name, attempts=1)

        return __original_start(self, *args, **kwargs)

    protocol_cls.start = wrapped_start
    protocol_cls._eg_class_patched = True


def patch_generation_classes():
    patched = 0

    try:
        from sequence.entanglement_management.generation.barret_kok import BarretKokA
        patch_generation_class(BarretKokA)
        patched += 1
    except Exception as e:
        print(f"[SETUP] Could not patch BarretKokA: {e}")

    try:
        from sequence.entanglement_management.generation.single_heralded import SingleHeraldedA
        patch_generation_class(SingleHeraldedA)
        patched += 1
    except Exception as e:
        print(f"[SETUP] Could not patch SingleHeraldedA: {e}")

    print(f"[SETUP] Patched {patched} generation classes for EG attempt counting.")