from sequence.topology.router_net_topo import RouterNetTopo

from simulator.first_RL.metrics.memory_occupancy_metrics import (
    register_memory_occupation_end,
    register_memory_occupation_start,
)
from simulator.first_RL.metrics.link_metrics import (
    is_canonical_observer,
    register_entanglement_attempt,
    register_pair_creation,
    register_pair_discard,
)


def endpoint_name(obj) -> str:
    return str(getattr(obj, "name", obj))


def extract_remote_node_from_protocol(protocol) -> str | None:
    for attribute in (
        "remote_node_name",
        "remote_node",
        "other",
        "other_name",
    ):
        value = getattr(protocol, attribute, None)

        if isinstance(value, str):
            return value

        if value is not None and hasattr(value, "name"):
            return str(value.name)

    return None


def patch_generation_class(protocol_class) -> None:
    """Count elementary entanglement-generation attempts."""

    if getattr(protocol_class, "_eg_class_patched", False):
        return

    original_start = protocol_class.start

    def wrapped_start(self, *args, **kwargs):
        local_node = endpoint_name(self.owner)
        remote_node = extract_remote_node_from_protocol(self)

        if (
            remote_node is not None
            and is_canonical_observer(local_node, remote_node)
        ):
            register_entanglement_attempt(
                local_node,
                remote_node,
                attempts=1,
            )

        return original_start(self, *args, **kwargs)

    protocol_class.start = wrapped_start
    protocol_class._eg_class_patched = True


def patch_generation_classes() -> None:
    """Patch dynamically created generation-protocol instances."""

    patched = 0

    try:
        from sequence.entanglement_management.generation.barret_kok import (
            BarretKokA,
        )

        patch_generation_class(BarretKokA)
        patched += 1
    except Exception as error:
        print(f"[SETUP] Could not patch BarretKokA: {error}")

    try:
        from sequence.entanglement_management.generation.single_heralded import (
            SingleHeraldedA,
        )

        patch_generation_class(SingleHeraldedA)
        patched += 1
    except Exception as error:
        print(f"[SETUP] Could not patch SingleHeraldedA: {error}")

    print(
        f"[SETUP] Patched {patched} generation classes "
        f"for EG attempt counting."
    )


def _memory_remote_node(memory) -> str | None:
    entangled_memory = getattr(memory, "entangled_memory", {})

    if isinstance(entangled_memory, dict):
        remote_node = entangled_memory.get("node_id")

        if remote_node is not None:
            return str(remote_node)

    remote_node = getattr(memory, "remote_node", None)

    return (
        str(remote_node)
        if remote_node is not None
        else None
    )


def _memory_fidelity(memory) -> float:
    return float(getattr(memory, "fidelity", 0.0) or 0.0)


def _normalize_state(state) -> str:
    """Normalize strings and MemoryInfo enum-like states."""

    state_name = getattr(state, "name", state)
    state_name = str(state_name).upper()

    if "." in state_name:
        state_name = state_name.rsplit(".", 1)[-1]

    return state_name


def _reservation_metadata(protocol):
    reservation = getattr(
        protocol,
        "tracking_reservation",
        None,
    )

    if reservation is None:
        reservation = getattr(
            protocol,
            "reservation",
            None,
        )

    source = getattr(
        protocol,
        "tracking_source",
        None,
    )
    destination = getattr(
        protocol,
        "tracking_destination",
        None,
    )
    reservation_identity = getattr(
        protocol,
        "tracking_reservation_identity",
        None,
    )

    if reservation is not None:
        if source is None:
            source = getattr(
                reservation,
                "initiator",
                None,
            )

        if destination is None:
            destination = getattr(
                reservation,
                "responder",
                None,
            )

        if reservation_identity is None:
            reservation_identity = getattr(
                reservation,
                "identity",
                None,
            )

            if reservation_identity is None:
                reservation_identity = id(reservation)

    return (
        source,
        destination,
        reservation_identity,
    )


def _notify_rl_memory_invalidated(
    node,
    memory,
    reason: str,
) -> None:
    """Close pending RL decisions that use an invalidated memory."""

    controller = getattr(
        node,
        "rl_swap_controller",
        None,
    )

    if controller is None:
        return

    controller.on_memory_invalidated(
        node=node,
        memory=memory,
        reason=reason,
    )


def patch_resource_manager_update(node) -> None:
    """
    Track pair lifecycle and notify the RL controller when a memory is reset.
    """

    resource_manager = node.resource_manager

    if getattr(resource_manager, "_metrics_patched", False):
        return

    original_update = resource_manager.update

    def wrapped_update(
        protocol,
        memory,
        state,
        *args,
        **kwargs,
    ):
        now_ps = int(node.timeline.now())
        normalized_state = _normalize_state(state)

        old_remote_node = _memory_remote_node(memory)
        old_fidelity = _memory_fidelity(memory)

        result = original_update(
            protocol,
            memory,
            state,
            *args,
            **kwargs,
        )

        new_remote_node = _memory_remote_node(memory)
        new_fidelity = _memory_fidelity(memory)

        (
            source,
            destination,
            reservation_identity,
        ) = _reservation_metadata(protocol)

        if normalized_state in {"ENTANGLED", "PURIFIED"}:
            if (
                new_remote_node is not None
                and new_fidelity > 0
            ):
                register_pair_creation(
                    local_node=node.name,
                    memory=memory,
                    remote_node=new_remote_node,
                    fidelity=new_fidelity,
                    entangle_time_ps=now_ps,
                )

                register_memory_occupation_start(
                    node_name=node.name,
                    memory=memory,
                    start_time_ps=now_ps,
                    source=source,
                    destination=destination,
                    reservation_identity=reservation_identity,
                    state=normalized_state,
                )

        elif normalized_state == "RAW":
            if old_remote_node is not None:
                register_pair_discard(
                    local_node=node.name,
                    memory=memory,
                    discard_time_ps=now_ps,
                    fidelity_before_reset=old_fidelity,
                    reason="reset_to_RAW",
                )

            register_memory_occupation_end(
                node_name=node.name,
                memory=memory,
                end_time_ps=now_ps,
                reason="reset_to_RAW",
                final_state="RAW",
            )

            # A normal swapping result may already have closed its pending
            # SWAP transition. The controller safely ignores missing keys.
            # Pending WAIT decisions involving this memory are terminated here.
            if old_remote_node is not None:
                _notify_rl_memory_invalidated(
                    node=node,
                    memory=memory,
                    reason="pair_discarded",
                )

        return result

    resource_manager.update = wrapped_update
    resource_manager._metrics_patched = True


def instrument_resource_managers(
    topology: RouterNetTopo,
) -> None:
    routers = topology.get_nodes_by_type(
        RouterNetTopo.QUANTUM_ROUTER
    )

    for node in routers:
        patch_resource_manager_update(node)

    print(
        f"[SETUP] Patched {len(routers)} resource managers "
        f"for pair lifecycle and RL tracking."
    )


def instrument_tracking(
    topology: RouterNetTopo,
) -> None:
    """Install all instrumentation before starting the simulation."""

    patch_generation_classes()
    instrument_resource_managers(topology)