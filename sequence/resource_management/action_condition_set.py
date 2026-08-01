"""
This provides actions, condition, and request functions for generation, purification, and swapping to be used by the
resource manager.

## Function Descriptions

1. Action: an action function defines what action is taken when a rule condition is met.
    This includes creating an instance of the relevant protocol.
2. Condition: a condition function states requirements that must be met on the local node before a rule can execute.
    Conditions
3. Match: a match function states requirements on what protocols may be paired during the request handshake in the resource manager.
    Paired protocols coordinate the execution of a specific entanglement primitive (generation, purification, or swapping).

## Function Signatures

Action Signature: def action(memories_info: MemoryInfo, args: Arguments) -> ActionReturn
- Args:
    - memories_info: a list of MemoryInfo objects that satisfy the corresponding condition function
    - args: other arguments specific to each action function
- Returns:
    - ActionReturn: a tuple of (protocol, destination, request_func, request_args)
        - protocol: the protocol to be paired and executed
        - destination: the destination(s) of the request, or None if the protocol does not send a request
        - request_func: the request function to be used, or None if the protocol does not send a request
        - request_args: the arguments for the request function

Condition Signature: def condition(memory_info: MemoryInfo, manager: MemoryManager, args: Arguments) -> list[MemoryInfo]
- Args:
    - memory_info: the MemoryInfo object to be checked
    - manager: the memory manager of the local, to get other memory info from if necessary
    - args: other arguments specific to each condition function
- Returns:
    - list[MemoryInfo]: a list of MemoryInfo objects that satisfy the condition.

Match Signature: def request(protocols: list[EntanglementProtocol], args: Arguments) -> EntanglementProtocol | None
- Args:
    - protocols: the list of protocols awaiting pairing on the remote node receiving the request
    - args: other arguments specific to each request function
- Returns:
    - EntanglementProtocol | None: the protocol to pair on the other node, or None if no protocol is selected

Actions, Conditions, and Match Functions must follow the above signatures. If a function does not use a parameter, it
should prefix the unused parameter with `_`. For example, `_manager` should be used for a Condition function if the
memory manager is not required.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, cast

from ..components.memory import Memory
from ..entanglement_management.entanglement_protocol import EntanglementProtocol
from ..entanglement_management.generation import EntanglementGenerationA
from ..entanglement_management.purification import BBPSSWProtocol
from ..entanglement_management.swapping import EntanglementSwappingA, EntanglementSwappingB
from simulator.first_RL.metrics.protocol_metrics import (record_purification_candidate,)
if TYPE_CHECKING:
    from ..topology.node import Node
    from .memory_manager import MemoryInfo, MemoryManager

Arguments = dict[str, Any]
RequestFunction = Callable[[list[EntanglementProtocol], Arguments], EntanglementProtocol | None]
ActionReturn = tuple[EntanglementProtocol, list[str | None], list[RequestFunction | None], list[dict[str, Any] | None]]

TempNode = cast("Node", cast(object, None))
TempMemory = cast("Memory", cast(object, None))

def attach_flow_tracking(
    protocol: EntanglementProtocol,
    reservation,
) -> None:
    """
    Associate a local protocol instance with the end-to-end flow
    that created it.

    The flow is identified by the reservation initiator and responder.
    """
    if reservation is None:
        protocol.tracking_source = None
        protocol.tracking_destination = None
        protocol.tracking_reservation_identity = None
        return

    protocol.tracking_source = getattr(
        reservation,
        "initiator",
        None,
    )

    protocol.tracking_destination = getattr(
        reservation,
        "responder",
        None,
    )

    protocol.tracking_reservation_identity = getattr(
        reservation,
        "identity",
        None,
    )
# Entanglement Generation Action-Condition-Match
def eg_rule_action_await(memories_info: list[MemoryInfo], args: Arguments) -> ActionReturn:
    """Action function used to create an entanglement generation protocol instance and await a Resource Manager request.

    Rules with this action are created on all nodes other than the initiator, i.e., where args['index'] > 0.
    The initiator node always creates a request using `eg_rule_action_request`.
    
    Args:
        memories_info: the list of memory info that satisfy the condition function
        args: the arguments defined in the rule; should contain "mid", "path", and "index"

    Returns:
        ActionReturn: the protocol to be executed, None, None, None.
            (this protocol does not send a Resource Manager request, but waits for a request from another node)
    """
    memories: list[Memory] = [info.memory for info in memories_info]
    memory: Memory = memories[0]
    mid = args["mid"]
    path = args["path"]
    index = args["index"]
    protocol = EntanglementGenerationA.create(owner=TempNode, name=f"EGA.{memory.name}",
                                              middle=mid, other=path[index - 1], memory=memory)
    
    return protocol, [None], [None], [None]


def eg_rule_action_request(memories_info: list[MemoryInfo], args: Arguments) -> ActionReturn:
    """Action function used to create an entanglement generation protocol instance and send a Resource Manager request.

    Rules with this action are created on all nodes other than the responder, i.e., where args['index'] < len(args['path']) - 1.
    The responder always awaits a request using `eg_rule_action_await`.
    
    Args:
        memories_info: the list of memory info that satisfy the condition function
        args: the arguments defined in the rule.
            Should contain "mid" (the name of the corresponding BSM node), "path",
            and "index" (the index of the current node in the path).

    Returns:
        ActionReturn: the protocol to be executed, the destination of the request, the request function,
            and the arguments for request function
    """
    mid = args["mid"]
    path = args["path"]
    index = args["index"]
    memories = [info.memory for info in memories_info]
    memory = memories[0]
    protocol = EntanglementGenerationA.create(TempNode, "EGA." + memory.name, mid, path[index + 1], memory)
    req_args = {"name": args["name"], "reservation": args["reservation"]}
    return protocol, [path[index + 1]], [eg_match_func], [req_args]


def eg_rule_condition(memory_info: MemoryInfo, _manager: MemoryManager, args: Arguments) -> list[MemoryInfo]:
    """Condition function used by entanglement generation protocol on nodes.
    
    Args:
        memory_info: the memory info to be checked
        _manager: the memory manager (not used in this condition function)
        args: the arguments defined in the rule
    Returns:
        list[MemoryInfo]: the list of MemoryInfo that satisfy the condition
    """
    memory_indices = args["memory_indices"]
    if memory_info.state == "RAW" and memory_info.index in memory_indices:
        return [memory_info]
    else:
        return []


def eg_match_func(protocols: list[EntanglementProtocol], args: Arguments) -> EntanglementGenerationA | None:
    """Function used by `eg_rule_action_request` function for selecting generation protocols on the remote node.

    Args:
        protocols: protocols awaiting pairing on the node receiving the request
        args: arguments from the node which sent the request

    Returns:
        EntanglementGenerationA | None: the protocol to pair on the other node, or None if no protocol is selected.
    """
    name = args["name"]
    reservation = args["reservation"]
    for protocol in protocols:
        # Select correct EntanglementGenerationA protocol.
        if (isinstance(protocol, EntanglementGenerationA)
                and protocol.remote_node_name == name
                and protocol.rule is not None
                and protocol.rule.get_reservation() == reservation):
            return protocol
    return None


def ep_rule_action_request(
    memories_info: list[MemoryInfo],
    args: Arguments,
) -> ActionReturn:
    """
    Create the requesting-side BBPSSW protocol and associate it
    with the end-to-end reservation flow.
    """
    memories = [
        info.memory
        for info in memories_info
    ]

    name = (
        f"EP.{memories[0].name}."
        f"{memories[1].name}"
    )

    protocol = BBPSSWProtocol.create(
        TempNode,
        name,
        memories[0],
        memories[1],
    )

    reservation = args.get(
        "reservation"
    )

    attach_flow_tracking(
        protocol,
        reservation,
    )

    destinations = [
        memories_info[0].remote_node
    ]

    request_functions: list[
        RequestFunction | None
    ] = [
        ep_match_func
    ]

    request_arguments = [
    {
        "remote_kept":
            memories_info[0].remote_memo,
        "remote_meas":
            memories_info[1].remote_memo,
        "tracking_source":
            getattr(
                protocol,
                "tracking_source",
                None,
            ),
        "tracking_destination":
            getattr(
                protocol,
                "tracking_destination",
                None,
            ),
        "tracking_reservation_identity":
            getattr(
                protocol,
                "tracking_reservation_identity",
                None,
            ),
    }
]

    return (
        protocol,
        destinations,
        request_functions,
        request_arguments,
    )

def ep_rule_action_await(
    memories_info: list[MemoryInfo],
    args: Arguments,
) -> ActionReturn:
    """
    Create a waiting-side BBPSSW protocol and associate it with
    the corresponding end-to-end reservation.
    """
    memories = [
        info.memory
        for info in memories_info
    ]

    name = f"EP.{memories[0].name}"

    protocol = BBPSSWProtocol.create(
        TempNode,
        name,
        memories[0],
        TempMemory,
    )

    reservation = args.get(
        "reservation"
    )

    attach_flow_tracking(
        protocol,
        reservation,
    )

    return (
        protocol,
        [None],
        [None],
        [None],
    )
def _get_node_name_from_memory_info(
    memory_info: MemoryInfo,
) -> str | None:
    node = _get_node_from_memory(
        memory_info.memory
    )

    if node is None:
        return None

    return node.name
def ep_rule_condition_request(
    kept_memory: MemoryInfo,
    memory_manager: MemoryManager,
    args: Arguments,
) -> list[MemoryInfo]:
    """
    Condition function used by the BBPSSW purification protocol
    on nodes other than the initiator.
    """
    memory_indices = args["memory_indices"]
    reservation = args["reservation"]
    purification_mode = args["purification_mode"]

    def print_purification_conflict(kept: MemoryInfo, measured: MemoryInfo) -> None:
        kept_memory_object = getattr(kept, "memory", None)
        measured_memory_object = getattr(measured, "memory", None)

        kept_name = getattr(
            kept_memory_object,
            "name",
            f"memory_index_{getattr(kept, 'index', 'unknown')}",
        )
        measured_name = getattr(
            measured_memory_object,
            "name",
            f"memory_index_{getattr(measured, 'index', 'unknown')}",
        )

        print("\n[PURIFICATION-CONFLICT]")
        print(f"kept local memory      = {kept_name}")
        print(f"measured local memory  = {measured_name}")
        print(f"kept local index       = {getattr(kept, 'index', None)}")
        print(f"measured local index   = {getattr(measured, 'index', None)}")
        print(f"kept remote node       = {getattr(kept, 'remote_node', None)}")
        print(f"measured remote node   = {getattr(measured, 'remote_node', None)}")
        print(f"kept remote memory     = {getattr(kept, 'remote_memo', None)}")
        print(f"measured remote memory = {getattr(measured, 'remote_memo', None)}")
        print(f"kept state             = {getattr(kept, 'state', None)}")
        print(f"measured state         = {getattr(measured, 'state', None)}")
        print(f"kept fidelity          = {getattr(kept, 'fidelity', None)}")
        print(f"measured fidelity      = {getattr(measured, 'fidelity', None)}")
        print(f"kept entangle time     = {getattr(kept, 'entangle_time', None)}")
        print(f"measured entangle time = {getattr(measured, 'entangle_time', None)}")

    if purification_mode == "until_target":
        if (
            kept_memory.index in memory_indices
            and kept_memory.state in ["ENTANGLED", "PURIFIED"]
            and kept_memory.fidelity < reservation.fidelity
        ):
            for measured_memory in memory_manager:
                if (
                    measured_memory != kept_memory
                    and measured_memory.index in memory_indices
                    and measured_memory.state in ["ENTANGLED", "PURIFIED"]
                    and measured_memory.remote_node == kept_memory.remote_node
                    and measured_memory.fidelity == kept_memory.fidelity
                ):
                    if kept_memory.remote_memo == measured_memory.remote_memo:
                        print_purification_conflict(kept_memory, measured_memory)
                        raise AssertionError(
                            "Purification selected two local memories that reference the same remote memory."
                        )

                    source = getattr(reservation, "initiator", None)
                    destination = getattr(reservation, "responder", None)

                    if source is not None and destination is not None:
                        record_purification_candidate(
    source=reservation.initiator,
    destination=reservation.responder,
    node_name=_get_node_name_from_memory_info(
        kept_memory
    ),
)

                    return [kept_memory, measured_memory]

    elif purification_mode == "once":
        if (
            kept_memory.index in memory_indices
            and kept_memory.state == "ENTANGLED"
            and kept_memory.fidelity < reservation.fidelity
        ):
            for measured_memory in memory_manager:
                if (
                    measured_memory != kept_memory
                    and measured_memory.index in memory_indices
                    and measured_memory.state == "ENTANGLED"
                    and measured_memory.remote_node == kept_memory.remote_node
                    and measured_memory.fidelity == kept_memory.fidelity
                ):
                    if kept_memory.remote_memo == measured_memory.remote_memo:
                        print_purification_conflict(kept_memory, measured_memory)
                        raise AssertionError(
                            "Purification selected two local memories that reference the same remote memory."
                        )

                    source = getattr(reservation, "initiator", None)
                    destination = getattr(reservation, "responder", None)

                    if source is not None and destination is not None:
                        record_purification_candidate(
    source=reservation.initiator,
    destination=reservation.responder,
    node_name=_get_node_name_from_memory_info(
        kept_memory
    ),
)
                    return [kept_memory, measured_memory]
    
    return []



def ep_rule_condition_await(memory_info: MemoryInfo, _manager: MemoryManager, args: Arguments) -> list[MemoryInfo]:
    """Condition function used by BBPSSW protocol on nodes except the responder (see `ep_rule_action_await`).

    Args:
        memory_info: the memory info to be checked
        _manager: the memory manager to get other memory info (not used in this condition function)
        args: the arguments defined in the rule should contain "memory_indices", "fidelity", and "purification_mode"

    Returns:
        list[MemoryInfo]: the list of memory info that satisfy the condition.
    """
    memory_indices = args["memory_indices"]
    fidelity = args["fidelity"]
    purification_mode = args["purification_mode"]

    if purification_mode == "until_target":
        if  (memory_info.index in memory_indices
                and memory_info.state in ["ENTANGLED", "PURIFIED"]
                and memory_info.fidelity < fidelity):
            return [memory_info]

    elif purification_mode == "once":
        if (memory_info.index in memory_indices
                and memory_info.state == "ENTANGLED"
                and memory_info.fidelity < fidelity):
            return [memory_info]

    return []


def ep_match_func(protocols: list[EntanglementProtocol], args: Arguments) -> BBPSSWProtocol | None:
    """Function used by `ep_rule_action_request` for selecting purification protocols on the remote node.

    Note that only one memory will be assigned to each protocol instance within the `ep_rule_action_await` function;
    when two memories (protocols) are matched to the requesting protocol, both memories will be reassigned to one
    protocol instance and the other will be removed.

    Args:
        protocols (list): a list of waiting protocols
        args (dict): the arguments for the match function.
            Should contain "remote_kept" (the kept memory on the remote node) and "remote_meas"
            (the measured memory on the remote node).

    Returns:
        BBPSSWProtocol | None: the protocol to pair on the other node (or None if no protocol is selected).
    """
    remote_kept = args["remote_kept"]
    remote_meas = args["remote_meas"]

    _protocols = []
    for protocol in protocols:
        if not isinstance(protocol, BBPSSWProtocol):
            continue

        if protocol.kept_memo.name == remote_kept:
            _protocols.insert(0, protocol)
        if protocol.kept_memo.name == remote_meas:
            _protocols.insert(1, protocol)

    if len(_protocols) != 2:
        return None

    protocols.remove(_protocols[1])
    _protocols[1].rule.protocols.remove(_protocols[1])
    _protocols[1].kept_memo.detach(_protocols[1])
    _protocols[0].meas_memo = _protocols[1].kept_memo
    _protocols[0].memories = [_protocols[0].kept_memo, _protocols[0].meas_memo]
    _protocols[0].name = _protocols[0].name + "." + _protocols[0].meas_memo.name
    _protocols[0].meas_memo.attach(_protocols[0])
    _protocols[0].tracking_source = args.get(
    "tracking_source"
    )

    _protocols[0].tracking_destination = args.get(
        "tracking_destination"
    )

    _protocols[0].tracking_reservation_identity = args.get(
        "tracking_reservation_identity"
    )
    return _protocols[0]


def es_rule_action_A(
    memories_info: list[MemoryInfo],
    args: Arguments,
) -> ActionReturn:
    """Create EntanglementSwappingA on an interior node."""

    if len(memories_info) != 2:
        raise ValueError(
            "EntanglementSwappingA requires exactly two memories."
        )

    memories = [
        memory_info.memory
        for memory_info in memories_info
    ]

    reservation = args.get("reservation")

    protocol = EntanglementSwappingA(
        TempNode,
        f"ESA.{memories[0].name}.{memories[1].name}",
        memories[0],
        memories[1],
        success_prob=0.64,
    )

    attach_flow_tracking(
        protocol,
        reservation,
    )

    # Retrieve the temporary RL metadata saved by the condition.
    protocol.rl_decision_key = getattr(
        memories[0],
        "rl_decision_key",
        None,
    )

    protocol.rl_controller = getattr(
        memories[0],
        "rl_controller",
        None,
    )

    protocol.rl_reservation = getattr(
        memories[0],
        "rl_reservation",
        reservation,
    )

    # Remove temporary attributes to avoid reusing stale decisions.
    for memory in memories:
        for attribute in (
            "rl_decision_key",
            "rl_controller",
            "rl_reservation",
        ):
            if hasattr(memory, attribute):
                delattr(memory, attribute)

    dsts = [
        memory_info.remote_node
        for memory_info in memories_info
    ]

    req_funcs: list[RequestFunction | None] = [
        es_match_func,
        es_match_func,
    ]

    req_args = [
        {
            "target_memo": memories_info[0].remote_memo,
            "tracking_source": protocol.tracking_source,
            "tracking_destination": protocol.tracking_destination,
            "tracking_reservation_identity": getattr(
                protocol,
                "tracking_reservation_identity",
                None,
            ),
        },
        {
            "target_memo": memories_info[1].remote_memo,
            "tracking_source": protocol.tracking_source,
            "tracking_destination": protocol.tracking_destination,
            "tracking_reservation_identity": getattr(
                protocol,
                "tracking_reservation_identity",
                None,
            ),
        },
    ]

    return protocol, dsts, req_funcs, req_args


def es_rule_action_B(memories_info: list[MemoryInfo], args: Arguments) -> ActionReturn:
    """Action function used to create an EntanglementSwappingB protocol instance on all nodes.

    `es_rule_action_B` always awaits the resource manager request for entanglement swapping.
    
    Args:
        memories_info: a list of memory info
        _args: the arguments defined in the rule (not used in this action function)
    
    Returns:
        ActionReturn: the protocol to be executed, None, None, None
            (this protocol does not send a Resource Manager request, but will wait for the request from the other node)
    """
    memories = [info.memory for info in memories_info]
    memory = memories[0]
    protocol = EntanglementSwappingB(TempNode, "ESB." + memory.name, memory)
    reservation = args.get("reservation")
    attach_flow_tracking(protocol, reservation)
    return protocol, [None], [None], [None]

def _get_node_from_memory(memory: Memory) -> Node | None:
    """
    Recover the QuantumRouter node from a memory object.

    Memory names are usually like:
        sophia.MemoryArray[0]
    so the node name is before ".MemoryArray".
    """
    try:
        node_name = memory.name.split(".MemoryArray")[0]
        return memory.timeline.get_entity_by_name(node_name)
    except Exception:
        return None


def _rl_allows_swapping(
    memory_info_1: MemoryInfo,
    memory_info_2: MemoryInfo,
    reservation=None,
) -> tuple[bool, Hashable | None]:
    """Ask the local RL controller whether swapping can be executed."""

    node = _get_node_from_memory(memory_info_1.memory)

    if node is None:
        return True, None

    controller = getattr(node, "rl_swap_controller", None)

    if controller is None:
        return True, None

    execute_swap, decision_key = controller.decide(
        node=node,
        left_memory=memory_info_1.memory,
        right_memory=memory_info_2.memory,
        reservation=reservation,
    )

    return bool(execute_swap), decision_key


def _evaluate_rl_swap(
    memory_info_1: MemoryInfo,
    memory_info_2: MemoryInfo,
    reservation=None,
) -> bool:
    """
    Evaluate the RL decision and temporarily attach the decision metadata
    to the selected memories so that es_rule_action_A can retrieve it.
    """

    execute_swap, decision_key = _rl_allows_swapping(
        memory_info_1=memory_info_1,
        memory_info_2=memory_info_2,
        reservation=reservation,
    )

    if not execute_swap:
        return False

    node = _get_node_from_memory(memory_info_1.memory)
    controller = (
        getattr(node, "rl_swap_controller", None)
        if node is not None
        else None
    )

    for memory_info in (memory_info_1, memory_info_2):
        memory_info.memory.rl_decision_key = decision_key
        memory_info.memory.rl_controller = controller
        memory_info.memory.rl_reservation = reservation

    return True


def es_rule_condition_A(
    memory_info: MemoryInfo,
    memory_manager: MemoryManager,
    args: Arguments,
) -> list[MemoryInfo]:
    """Condition for EntanglementSwappingA on interior nodes."""

    memory_indices = args["memory_indices"]
    remote_left_node = args["left"]
    remote_right_node = args["right"]
    minimum_fidelity = args["fidelity"]
    reservation = args.get("reservation")

    valid_states = {"ENTANGLED", "PURIFIED"}

    def is_valid_candidate(
        info: MemoryInfo,
        expected_remote: str,
    ) -> bool:
        return (
            info.state in valid_states
            and info.index in memory_indices
            and info.remote_node == expected_remote
            and info.fidelity >= minimum_fidelity
        )

    # memory_info represents the left side.
    if is_valid_candidate(memory_info, remote_left_node):
        for memory_info_2 in memory_manager:
            if (
                memory_info_2 is not memory_info
                and is_valid_candidate(
                    memory_info_2,
                    remote_right_node,
                )
            ):
                if not _evaluate_rl_swap(
                    memory_info_1=memory_info,
                    memory_info_2=memory_info_2,
                    reservation=reservation,
                ):
                    return []

                return [memory_info, memory_info_2]

    # memory_info represents the right side.
    if is_valid_candidate(memory_info, remote_right_node):
        for memory_info_2 in memory_manager:
            if (
                memory_info_2 is not memory_info
                and is_valid_candidate(
                    memory_info_2,
                    remote_left_node,
                )
            ):
                if not _evaluate_rl_swap(
                    memory_info_1=memory_info,
                    memory_info_2=memory_info_2,
                    reservation=reservation,
                ):
                    return []

                return [memory_info, memory_info_2]

    return []
"""
def es_rule_condition_A(memory_info: MemoryInfo, memory_manager: MemoryManager, args: Arguments) -> list[MemoryInfo]:
    Condition function used for the EntanglementSwappingA protocol on all interior nodes (see `es_rule_action_A`).
    
    Args:
        memory_info: the memory info to be checked
        memory_manager: the memory manager to get other memory info
        args: the arguments defined in the rule.
            Should contain "memory_indices", "left", "right", and "fidelity"
    
    Returns:
        list[MemoryInfo]: a list of two memory info (memory_info, memory_info_2) that satisfy the condition
    
    memory_indices = args["memory_indices"]
    remote_left_node = args["left"]
    remote_right_node = args["right"]
    fidelity = args["fidelity"]

    # case 1: memory_info is the "left hand side" memory
    # the first memory is the "left hand side" memory during swapping
    if (memory_info.state in ["ENTANGLED", "PURIFIED"]
            and memory_info.index in memory_indices
            and memory_info.remote_node == remote_left_node
            and memory_info.fidelity >= fidelity):
        for memory_info_2 in memory_manager:
            # the second memory is the "right hand side" memory during swapping
            if (memory_info_2.state in ["ENTANGLED", "PURIFIED"]
                    and memory_info_2.index in memory_indices
                    and memory_info_2.remote_node == remote_right_node
                    and memory_info_2.fidelity >= fidelity):
                return [memory_info, memory_info_2]
    
    # case 2: memory_info is the "right hand side" memory
    # the first memory is the "right hand side" memory during swapping
    if (memory_info.state in ["ENTANGLED", "PURIFIED"]
            and memory_info.index in memory_indices
            and memory_info.remote_node == remote_right_node
            and memory_info.fidelity >= fidelity):
        for memory_info_2 in memory_manager:
            # the second memory is the "left hand side" memory during swapping
            if (memory_info_2.state in ["ENTANGLED", "PURIFIED"]
                    and memory_info_2.index in memory_indices
                    and memory_info_2.remote_node == remote_left_node
                    and memory_info_2.fidelity >= fidelity):
                return [memory_info, memory_info_2]
    
    return []

"""
def es_rule_condition_B_end(memory_info: MemoryInfo, _manager: MemoryManager, args: Arguments) -> list[MemoryInfo]:
    """Condition function used by the EntanglementSwappingB protocol on either the responder or initiator nodes.
    
    Args:
        memory_info: the memory info to be checked
        _manager: the memory manager to get other memory info (not used in this condition function)
        args: the arguments defined in the rule.
            Should contain "memory_indices", "target_remote", and "fidelity".

    Returns:
        list[MemoryInfo]: the list of memory info that satisfy the condition
    """
    memory_indices = args["memory_indices"]
    target_remote = args["target_remote"]  # A - B - C. For A: B is the remote node, C is the target remote
    fidelity = args["fidelity"]
    if (memory_info.state in ["ENTANGLED", "PURIFIED"]
            and memory_info.index in memory_indices
            and memory_info.remote_node != target_remote
            and memory_info.fidelity >= fidelity):
        return [memory_info]
    else:
        return []


def es_rule_condition_B(memory_info: MemoryInfo, _manager: MemoryManager, args: Arguments) -> list[MemoryInfo]:
    """Condition function used by the EntanglementSwappingB protocol on interior nodes of a path.
    
    Args:
        memory_info: the memory info to be checked
        _manager: the memory manager to get other memory info (not used in this condition function)
        args: the arguments defined in the rule.
            Should contain "memory_indices", "left", "right", and "fidelity".
    
    Returns:
        list[MemoryInfo]: the list of memory info that satisfy the condition
    """
    memory_indices = args["memory_indices"]
    left = args["left"]
    right = args["right"]
    fidelity = args["fidelity"]
    if (memory_info.state in ["ENTANGLED", "PURIFIED"]
            and memory_info.index in memory_indices
            and memory_info.remote_node not in [left, right]
            and memory_info.fidelity >= fidelity):
        return [memory_info]
    else:
        return []


def es_match_func(protocols: list[EntanglementProtocol], args: Arguments) -> EntanglementSwappingB | None:
    """Function used by `es_rule_action_A` for selecting swapping protocols on the remote node.

    Args:
        protocols (list): a list of waiting protocols
        args (dict): the arguments defined in the rule.
            Should contain "target_memo".

    Returns:
        EntanglementSwappingB | None: the protocol to pair on the other node (or None if no protocol is selected).
    """
    target_memo = args["target_memo"]
    for protocol in protocols:
        if isinstance(protocol, EntanglementSwappingB) and protocol.memory.name == target_memo:
            protocol.tracking_source = args.get("tracking_source")
            protocol.tracking_destination = args.get("tracking_destination")
            protocol.tracking_reservation_identity = args.get("tracking_reservation_identity")
            return protocol
    return None
