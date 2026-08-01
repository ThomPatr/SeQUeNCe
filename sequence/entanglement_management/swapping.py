"""Code for entanglement swapping.

This module defines code for entanglement swapping.
Success is pre-determined based on network parameters.
The entanglement swapping protocol is an asymmetric protocol:

* The EntanglementSwappingA instance initiates the protocol and performs the swapping operation.
* The EntanglementSwappingB instance waits for the swapping result from EntanglementSwappingA.

The swapping results decides the following operations of EntanglementSwappingB.
Also defined in this module is the message type used by these protocols.
"""

from enum import Enum, auto
from typing import TYPE_CHECKING
from functools import lru_cache

if TYPE_CHECKING:
    from ..components.memory import Memory
    from ..topology.node import Node

from ..message import Message
from .entanglement_protocol import EntanglementProtocol
from ..utils import log
from ..components.circuit import Circuit
from ..resource_management.memory_manager import MemoryInfo
from simulator.first_RL.metrics.protocol_metrics import (
    record_swapping_attempt,
    record_swapping_failure,
    record_swapping_success,
)
class SwappingMsgType(Enum):
    """Defines possible message types for entanglement generation."""

    SWAP_RES = auto()


class EntanglementSwappingMessage(Message):
    """Message used by entanglement swapping protocols.

    This message contains all information passed between swapping protocol instances.

    Attributes:
        msg_type (SwappingMsgType): defines the message type.
        receiver (str): name of destination protocol instance.
        fidelity (float): fidelity of the newly swapped memory pair.
        remote_node (str): name of the distant node holding the entangled memory of the new pair.
        remote_memo (int): index of the entangled memory on the remote node.
        expire_time (int): expiration time of the new memory pair.
    """

    def __init__(self, msg_type: SwappingMsgType, receiver: str, **kwargs):
        Message.__init__(self, msg_type, receiver)
        if self.msg_type is SwappingMsgType.SWAP_RES:
            self.fidelity = kwargs.get("fidelity")
            self.remote_node = kwargs.get("remote_node")
            self.remote_memo = kwargs.get("remote_memo")
            self.expire_time = kwargs.get("expire_time")
            self.meas_res = kwargs.get("meas_res")
        else:
            raise Exception("Entanglement swapping protocol create unkown type of message: %s" % str(msg_type))

    def __str__(self):
        if self.msg_type == SwappingMsgType.SWAP_RES:
            return "EntanglementSwappingMessage: msg_type: {}; fidelity: {:.2f}; remote_node: {}; remote_memo: {}; ".format(
                self.msg_type, self.fidelity, self.remote_node, self.remote_memo)


class EntanglementSwappingA(EntanglementProtocol):
    """Entanglement-swapping protocol executed by the intermediate router."""

    circuit = Circuit(2)
    circuit.cx(0, 1)
    circuit.h(0)
    circuit.measure(0)
    circuit.measure(1)

    def __init__(
        self,
        owner: "Node",
        name: str,
        left_memo: "Memory",
        right_memo: "Memory",
        success_prob: float = 0.64,
        degradation: float = 0.95,
    ):
        assert left_memo is not right_memo

        super().__init__(owner, name)

        self.memories = [left_memo, right_memo]
        self.left_memo = left_memo
        self.right_memo = right_memo

        self.left_node = left_memo.entangled_memory["node_id"]
        self.left_remote_memo = left_memo.entangled_memory["memo_id"]
        self.right_node = right_memo.entangled_memory["node_id"]
        self.right_remote_memo = right_memo.entangled_memory["memo_id"]

        self.success_prob = float(success_prob)
        self.degradation = float(degradation)
        self.is_success = False

        self.left_protocol_name = None
        self.right_protocol_name = None

        # Existing tracking information.
        self.tracking_source = None
        self.tracking_destination = None
        self._tracking_started = False
        self._tracking_input_fidelity = None

        # Filled by es_rule_action_A when the protocol is created.
        self.rl_controller = None
        self.rl_decision_key = None
        self.rl_reservation = None
        self._rl_result_notified = False

    def is_ready(self) -> bool:
        return (
            self.left_protocol_name is not None
            and self.right_protocol_name is not None
        )

    def set_others(
        self,
        protocol: str,
        node: str,
        memories: list[str],
    ) -> None:
        if node == self.left_node:
            self.left_protocol_name = protocol
        elif node == self.right_node:
            self.right_protocol_name = protocol
        else:
            raise ValueError(
                f"Cannot pair protocol {self.name} "
                f"with {protocol} on node {node}."
            )

    # ------------------------------------------------------------------
    # Tracking
    # ------------------------------------------------------------------

    def _tracking_flow(self):
        if (
            self.tracking_source is None
            or self.tracking_destination is None
        ):
            return None

        return (
            self.tracking_source,
            self.tracking_destination,
        )

    def _record_tracking_attempt(self) -> None:
        if self._tracking_started:
            return

        self._tracking_started = True
        self._tracking_input_fidelity = (
            float(self.left_memo.fidelity),
            float(self.right_memo.fidelity),
        )

        flow = self._tracking_flow()

        if flow is not None:
            record_swapping_attempt(
                source=flow[0],
                destination=flow[1],
                node_name=self.owner.name,
            )

    def _record_tracking_success(
        self,
        output_fidelity: float,
    ) -> None:
        flow = self._tracking_flow()

        if flow is not None:
            record_swapping_success(
                source=flow[0],
                destination=flow[1],
                output_fidelity=float(output_fidelity),
                node_name=self.owner.name,
            )

    def _record_tracking_failure(self) -> None:
        flow = self._tracking_flow()

        if flow is not None:
            record_swapping_failure(
                source=flow[0],
                destination=flow[1],
                node_name=self.owner.name,
            )

    # ------------------------------------------------------------------
    # Reinforcement-learning callbacks
    # ------------------------------------------------------------------

    def _is_end_to_end(self) -> bool:
        """Return True when the new pair connects reservation endpoints."""

        reservation = self.rl_reservation

        if reservation is None:
            return False

        source = getattr(
            reservation,
            "initiator",
            None,
        )
        destination = getattr(
            reservation,
            "responder",
            None,
        )

        if source is None or destination is None:
            return False

        swapped_endpoints = {
            str(self.left_node),
            str(self.right_node),
        }
        reservation_endpoints = {
            str(source),
            str(destination),
        }

        return swapped_endpoints == reservation_endpoints

    def _notify_rl_result(
        self,
        success: bool,
        output_fidelity: float | None = None,
    ) -> None:
        """Close the pending SWAP transition exactly once."""

        if self._rl_result_notified:
            return

        controller = self.rl_controller
        decision_key = self.rl_decision_key

        if controller is None or decision_key is None:
            return

        self._rl_result_notified = True

        controller.on_swap_result(
            decision_key=decision_key,
            node=self.owner,
            success=bool(success),
            output_fidelity=(
                float(output_fidelity)
                if output_fidelity is not None
                else None
            ),
            end_to_end=(
                bool(success)
                and self._is_end_to_end()
            ),
            protocol=self,
            reservation=self.rl_reservation,
            terminal=True,
        )

        self.rl_decision_key = None

    def _notify_rl_interruption(
        self,
        reason: str,
    ) -> None:
        """Close a pending SWAP that cannot produce a normal result."""

        if self._rl_result_notified:
            return

        controller = self.rl_controller
        decision_key = self.rl_decision_key

        if controller is None or decision_key is None:
            return

        self._rl_result_notified = True

        controller.close_swap_as_terminal(
            decision_key=decision_key,
            node=self.owner,
            reason=reason,
        )

        self.rl_decision_key = None

    # ------------------------------------------------------------------
    # Protocol execution
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Execute the BSM and send the result to both remote nodes."""

        log.logger.info(
            f"{self.owner.name} middle protocol {self.name} "
            f"starts with endpoints "
            f"{self.left_node} and {self.right_node}"
        )

        assert self.left_memo.fidelity > 0
        assert self.right_memo.fidelity > 0
        assert (
            self.left_memo.entangled_memory["node_id"]
            == self.left_node
        )
        assert (
            self.right_memo.entangled_memory["node_id"]
            == self.right_node
        )

        self._record_tracking_attempt()

        swap_succeeded = (
            self.owner.get_generator().random()
            < self.success_probability()
        )

        if swap_succeeded:
            fidelity = self.updated_fidelity(
                self.left_memo.fidelity,
                self.right_memo.fidelity,
            )

            self.is_success = True
            self._record_tracking_success(fidelity)

            expire_time = min(
                self.left_memo.get_expire_time(),
                self.right_memo.get_expire_time(),
            )

            measurement_sample = (
                self.owner.get_generator().random()
            )

            measurement_result = (
                self.owner.timeline.quantum_manager.run_circuit(
                    self.circuit,
                    [
                        self.left_memo.qstate_key,
                        self.right_memo.qstate_key,
                    ],
                    measurement_sample,
                )
            )

            measurement_result = [
                measurement_result[
                    self.left_memo.qstate_key
                ],
                measurement_result[
                    self.right_memo.qstate_key
                ],
            ]

            log.logger.info(
                f"{self.name} swapping succeeded, "
                f"meas_res={measurement_result[0]},"
                f"{measurement_result[1]}, "
                f"fidelity={fidelity}"
            )

            msg_l = EntanglementSwappingMessage(
                SwappingMsgType.SWAP_RES,
                self.left_protocol_name,
                fidelity=fidelity,
                remote_node=self.right_node,
                remote_memo=self.right_remote_memo,
                expire_time=expire_time,
                meas_res=[],
            )

            msg_r = EntanglementSwappingMessage(
                SwappingMsgType.SWAP_RES,
                self.right_protocol_name,
                fidelity=fidelity,
                remote_node=self.left_node,
                remote_memo=self.left_remote_memo,
                expire_time=expire_time,
                meas_res=measurement_result,
            )

            self._notify_rl_result(
                success=True,
                output_fidelity=fidelity,
            )

        else:
            log.logger.info(
                f"{self.name} swapping failed"
            )

            self._record_tracking_failure()

            msg_l = EntanglementSwappingMessage(
                SwappingMsgType.SWAP_RES,
                self.left_protocol_name,
                fidelity=0,
            )

            msg_r = EntanglementSwappingMessage(
                SwappingMsgType.SWAP_RES,
                self.right_protocol_name,
                fidelity=0,
            )

            self._notify_rl_result(
                success=False,
                output_fidelity=None,
            )

        self.owner.send_message(
            self.left_node,
            msg_l,
        )
        self.owner.send_message(
            self.right_node,
            msg_r,
        )

        self.update_resource_manager(
            self.left_memo,
            MemoryInfo.RAW,
        )
        self.update_resource_manager(
            self.right_memo,
            MemoryInfo.RAW,
        )

    def success_probability(self) -> float:
        return self.success_prob

    @lru_cache(maxsize=128)
    def updated_fidelity(
        self,
        f1: float,
        f2: float,
    ) -> float:
        return float(
            f1 * f2 * self.degradation
        )

    def received_message(
        self,
        src: str,
        msg: "Message",
    ) -> None:
        raise RuntimeError(
            f"EntanglementSwappingA protocol "
            f"'{self.name}' should not receive messages."
        )

    def memory_expire(
        self,
        memory: "Memory",
    ) -> None:
        """Release local and remote resources after memory expiration."""

        assert not self.is_ready()

        self._notify_rl_interruption(
            reason="memory_expired"
        )

        if self.left_protocol_name:
            self.release_remote_protocol(
                self.left_node
            )
        else:
            self.release_remote_memory(
                self.left_node,
                self.left_remote_memo,
            )

        if self.right_protocol_name:
            self.release_remote_protocol(
                self.right_node
            )
        else:
            self.release_remote_memory(
                self.right_node,
                self.right_remote_memo,
            )

        for memo in self.memories:
            state = (
                MemoryInfo.RAW
                if memo is memory
                else MemoryInfo.ENTANGLED
            )

            self.update_resource_manager(
                memo,
                state,
            )

    def release_remote_protocol(
        self,
        remote_node: str,
    ) -> None:
        self.owner.resource_manager.release_remote_protocol(
            remote_node,
            self,
        )

    def release_remote_memory(
        self,
        remote_node: str,
        remote_memo: str,
    ) -> None:
        self.owner.resource_manager.release_remote_memory(
            remote_node,
            remote_memo,
        )
class EntanglementSwappingB(EntanglementProtocol):
    """Entanglement swapping protocol for end router.

    The entanglement swapping protocol is an asymmetric protocol.
    EntanglementSwappingB should be instantiated on the end nodes, where it waits for swapping results from the middle node.

    Variables:
        EntanglementSwappingB.x_cir (Circuit): circuit that corrects state with an x gate.
        EntanglementSwappingB.z_cir (Circuit): circuit that corrects state with z gate.
        EntanglementSwappingB.x_z_cir (Circuit): circuit that corrects state with an x and z gate.

    Attributes:
        own (QuantumRouter): node that protocol instance is attached to.
        name (str): name of protocol instance.
        memory (Memory): memory to swap.
        remote_protocol_name (str): name of another protocol to communicate with for swapping.
        remote_node_name (str): name of node hosting the other protocol.
    """

    x_cir = Circuit(1)
    x_cir.x(0)

    z_cir = Circuit(1)
    z_cir.z(0)

    x_z_cir = Circuit(1)
    x_z_cir.x(0)
    x_z_cir.z(0)

    def __init__(self, owner: "Node", name: str, hold_memo: "Memory"):
        """Constructor for entanglement swapping B protocol.

        Args:
            own (Node): node protocol instance is attached to.
            name (str): name of protocol instance.
            hold_memo (Memory): memory entangled with a memory on middle node.
        """

        EntanglementProtocol.__init__(self, owner, name)

        self.memories = [hold_memo]
        self.memory = hold_memo
        self.remote_protocol_name = None
        self.remote_node_name = None

    def is_ready(self) -> bool:
        return self.remote_protocol_name is not None

    def set_others(self, protocol: str, node: str, memories: list[str]) -> None:
        """Method to set other entanglement protocol instance.

        Args:
            protocol (str): other protocol name.
            node (str): other node name.
            memories (list[str]): the list of memory names used on other node.
        """
        self.remote_node_name = node
        self.remote_protocol_name = protocol

    def received_message(self, src: str, msg: "EntanglementSwappingMessage") -> None:
        """Method to receive messages from EntanglementSwappingA.

        Args:
            src (str): name of node sending message.
            msg (EntanglementSwappingMesssage): message sent.

        Side Effects:
            Will invoke `update_resource_manager` method.
        """

        log.logger.debug(self.owner.name + f" protocol received_message from node {src}, fidelity={msg.fidelity}")

        assert src == self.remote_node_name

        if msg.fidelity > 0 and self.owner.timeline.now() < msg.expire_time:
            if msg.meas_res == [1, 0]:
                self.owner.timeline.quantum_manager.run_circuit(self.z_cir, [self.memory.qstate_key])
            elif msg.meas_res == [0, 1]:
                self.owner.timeline.quantum_manager.run_circuit(self.x_cir, [self.memory.qstate_key])
            elif msg.meas_res == [1, 1]:
                self.owner.timeline.quantum_manager.run_circuit(self.x_z_cir, [self.memory.qstate_key])

            self.memory.fidelity = msg.fidelity
            self.memory.entangled_memory["node_id"] = msg.remote_node
            self.memory.entangled_memory["memo_id"] = msg.remote_memo
            self.memory.update_expire_time(msg.expire_time)
            self.update_resource_manager(self.memory, MemoryInfo.ENTANGLED)
        else:
            self.update_resource_manager(self.memory, MemoryInfo.RAW)

    def start(self) -> None:
        log.logger.debug(f"{self.owner.name} end protocol start with partner {self.remote_node_name}")

    def memory_expire(self, memory: "Memory") -> None:
        """Method to deal with expired memories.

        Args:
            memory (Memory): memory that expired.

        Side Effects:
            Will update memory in attached resource manager.
        """

        self.update_resource_manager(self.memory, MemoryInfo.RAW)

    def release(self) -> None:
        self.update_resource_manager(self.memory, MemoryInfo.ENTANGLED)
