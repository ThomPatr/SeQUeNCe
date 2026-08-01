from __future__ import annotations
from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import TYPE_CHECKING, List, Dict, Type, Optional
from collections.abc import Callable
from simulator.first_RL.metrics.protocol_metrics import (
    record_purification_attempt,
    record_purification_failure,
    record_purification_success,
)
from sequence.entanglement_management.entanglement_protocol import EntanglementProtocol
from sequence.utils.log import logger
from ...constants import KET_STATE_FORMALISM
from ...message import Message

if TYPE_CHECKING:
    from ...components.memory import Memory
    from ...topology.node import Node


class BBPSSWMsgType(Enum):
    """Defines possible message types for entanglement purification"""

    PURIFICATION_RES = auto()


class BBPSSWMessage(Message):
    """Message used by entanglement purification protocols.

    This message contains all information passed between purification protocol instances.

    Attributes:
        msg_type (BBPSSWMsgType): defines the message type.
        receiver (str): name of destination protocol instance.
    """

    def __init__(self, msg_type: BBPSSWMsgType, receiver: str, meas_res: int, protocol_type: str='bbpssw'):
        super().__init__(msg_type, receiver)
        self.meas_res = meas_res
        self.protocol_type = protocol_type

    def __str__(self):
        return f"\"BBPSSW: type={self.msg_type}, meas_res={self.meas_res}\""


class BBPSSWProtocol(
    EntanglementProtocol,
    ABC,
):
    _registry: dict[
        str,
        type["BBPSSWProtocol"],
    ] = {}

    _global_formalism: str = (
        KET_STATE_FORMALISM
    )

    def __init__(
        self,
        owner: Node,
        name: str,
        kept_memo: Memory,
        meas_memo: Memory,
        **kwargs,
    ):
        """
        Constructor for the purification protocol.

        Args:
            owner:
                Node to which the protocol is attached.
            name:
                Protocol instance name.
            kept_memo:
                Memory kept after successful purification.
            meas_memo:
                Memory measured and discarded.
        """
        assert kept_memo != meas_memo

        super().__init__(
            owner,
            name,
        )

        self.memories: list[Memory] = [
            kept_memo,
            meas_memo,
        ]

        self.kept_memo: Memory = kept_memo
        self.meas_memo: Memory = meas_memo

        self.remote_node_name: str = ""
        self.remote_protocol_name: str = ""
        self.remote_memories: list[str] = []

        self.protocol_type = "bbpssw"
        self.meas_res = None

        if self.meas_memo is None:
            self.memories.pop()

        # ====================================================
        # FLOW-LEVEL TRACKING STATE
        # ====================================================

        self.tracking_source = getattr(
            self,
            "tracking_source",
            kwargs.get(
                "tracking_source",
                None,
            ),
        )

        self.tracking_destination = getattr(
            self,
            "tracking_destination",
            kwargs.get(
                "tracking_destination",
                None,
            ),
        )

        self.tracking_reservation_identity = getattr(
            self,
            "tracking_reservation_identity",
            kwargs.get(
                "tracking_reservation_identity",
                None,
            ),
        )

        self._tracking_started = False
        self._tracking_completed = False
        self._tracking_input_fidelity = None
        self._tracking_output_fidelity = None
    def _is_tracking_observer(self) -> bool:
        """
        Count each distributed BBPSSW operation exactly once.

        The endpoint with the lexicographically smaller node name
        is selected as the canonical observer.
        """
        if not self.remote_node_name:
            return False

        return (
            str(self.owner.name)
            < str(self.remote_node_name)
        )
    @classmethod
    def get_formalism(cls) -> str:
        """
        Return the currently selected BBPSSW formalism.
        """
        return cls._global_formalism

    @classmethod
    def set_formalism(
        cls,
        formalism: str,
    ) -> None:
        """
        Select one of the registered BBPSSW implementations.
        """
        if formalism not in cls._registry:
            raise ValueError(
                f"Formalism '{formalism}' is not registered. "
                f"Available formalisms: "
                f"{list(cls._registry.keys())}"
            )

        cls._global_formalism = formalism

    @classmethod
    def register(
        cls,
        name: str,
        protocol_class: (
            type["BBPSSWProtocol"] | None
        ) = None,
    ):
        """
        Register a concrete BBPSSW protocol implementation.

        Decorator usage:

            @BBPSSWProtocol.register("formalism")
            class ConcreteBBPSSW(BBPSSWProtocol):
                ...

        Direct usage:

            BBPSSWProtocol.register(
                "formalism",
                ConcreteBBPSSW,
            )
        """
        if protocol_class is not None:
            if name in cls._registry:
                raise ValueError(
                    f"'{name}' is already registered."
                )

            cls._registry[name] = protocol_class
            return None

        def decorator(
            protocol_cls: type["BBPSSWProtocol"],
        ) -> type["BBPSSWProtocol"]:
            if name in cls._registry:
                raise ValueError(
                    f"'{name}' is already registered."
                )

            cls._registry[name] = protocol_cls
            return protocol_cls

        return decorator

    @classmethod
    def create(
        cls,
        owner: "Node",
        name: str,
        kept_memo: "Memory",
        meas_memo: "Memory",
        **kwargs,
    ) -> "BBPSSWProtocol":
        """
        Instantiate the concrete BBPSSW implementation associated
        with the active formalism.
        """
        protocol_name = cls.get_formalism()

        try:
            protocol_class = cls._registry[
                protocol_name
            ]
        except KeyError as error:
            raise ValueError(
                f"Protocol class '{protocol_name}' "
                "is not registered. "
                f"Available protocols: "
                f"{list(cls._registry.keys())}"
            ) from error

        return protocol_class(
            owner,
            name,
            kept_memo,
            meas_memo,
            **kwargs,
        )

    @classmethod
    def list_protocols(
        cls,
    ) -> list[str]:
        """
        Return all registered BBPSSW implementations.
        """
        return list(
            cls._registry.keys()
        )

    @classmethod
    def clear_global_formalism(
        cls,
    ) -> None:
        """
        Reset the selected formalism to the default one.
        """
        cls._global_formalism = (
            KET_STATE_FORMALISM
        )
    
    def is_ready(self) -> bool:
        """
        Return True once the remote protocol has been associated.
        """
        return self.remote_node_name != ""

    def set_others(
        self,
        protocol: str,
        node: str,
        memories: list[str],
    ) -> None:
        """
        Associate this local purification instance with the
        corresponding remote protocol.
        """
        self.remote_node_name = node
        self.remote_protocol_name = protocol
        self.remote_memories = memories

    @abstractmethod
    def received_message(
        self,
        src: str,
        msg: BBPSSWMessage,
    ) -> None:
        """
        Process the purification result received from the remote node.
        """
        raise NotImplementedError

    def _tracking_flow(
        self,
    ) -> tuple[str, str] | None:
        source = getattr(
            self,
            "tracking_source",
            None,
        )

        destination = getattr(
            self,
            "tracking_destination",
            None,
        )

        if (
            source is None
            or destination is None
        ):
            return None

        return (
            str(source),
            str(destination),
        )

    def _record_tracking_attempt(
        self,
    ) -> None:
        """
        Record one purification attempt.

        This method is idempotent: calling it more than once for the
        same protocol instance does not duplicate the attempt.
        """
        if self._tracking_started:
            return

        self._tracking_started = True

        fidelity = getattr(
            self.kept_memo,
            "fidelity",
            None,
        )

        self._tracking_input_fidelity = (
            float(fidelity)
            if fidelity is not None
            else None
        )

        flow = self._tracking_flow()

        if (
            flow is None
            or not self._is_tracking_observer()
        ):
            return

        record_purification_attempt(
            source=flow[0],
            destination=flow[1],
            input_fidelity=(
                self._tracking_input_fidelity
            ),
            node_name=self.owner.name,
        )

    def _record_tracking_success(
        self,
        output_fidelity: float | None = None,
    ) -> None:
        """
        Record successful completion of purification.

        This method must be called exactly where the concrete BBPSSW
        implementation determines that the protocol succeeded.
        """
        if self._tracking_completed:
            return

        self._tracking_completed = True

        if output_fidelity is None:
            output_fidelity = getattr(
                self.kept_memo,
                "fidelity",
                None,
            )

        self._tracking_output_fidelity = (
            float(output_fidelity)
            if output_fidelity is not None
            else None
        )

        flow = self._tracking_flow()

        if (
            flow is None
            or not self._is_tracking_observer()
        ):
            return

        record_purification_success(
            source=flow[0],
            destination=flow[1],
            input_fidelity=(
                self._tracking_input_fidelity
            ),
            output_fidelity=(
                self._tracking_output_fidelity
            ),
            node_name=self.owner.name,
        )

    def _record_tracking_failure(
        self,
    ) -> None:
        """
        Record unsuccessful completion of purification.
        """
        if self._tracking_completed:
            return

        self._tracking_completed = True

        flow = self._tracking_flow()

        if (
            flow is None
            or not self._is_tracking_observer()
        ):
            return

        record_purification_failure(
            source=flow[0],
            destination=flow[1],
            input_fidelity=(
                self._tracking_input_fidelity
            ),
            node_name=self.owner.name,
        )

    @abstractmethod
    def start(
        self,
    ) -> None:
        """
        Validate and start the entanglement-purification protocol.

        Every concrete implementation must call:

            super().start()

        before executing its protocol-specific operations.
        """
        logger.info(
            f"{self.owner.name} protocol start "
            f"with partner {self.remote_node_name}"
        )

        kept_entangled_node = (
            self.kept_memo.entangled_memory[
                "node_id"
            ]
        )

        measured_entangled_node = (
            self.meas_memo.entangled_memory[
                "node_id"
            ]
        )

        assert self.is_ready(), (
            "Protocol is not ready to start. "
            "Remote node not set; use set_others()."
        )

        assert (
            kept_entangled_node
            == measured_entangled_node
        ), (
            "Mismatch of entangled memories "
            f"{kept_entangled_node} and "
            f"{measured_entangled_node} "
            f"on node {self.owner.name}."
        )

        assert self.kept_memo.fidelity > 0.5, (
            "Fidelity of kept memory is too low: "
            f"{self.kept_memo.fidelity}."
        )

        assert self.meas_memo.fidelity > 0.5, (
            "Fidelity of measurement memory is too low: "
            f"{self.meas_memo.fidelity}."
        )

        # Record only after all preconditions have passed.
        self._record_tracking_attempt()

    def memory_expire(
        self,
        memory: "Memory",
    ) -> None:
        """
        Handle expiration of a memory involved in purification.
        """
        assert memory in self.memories, (
            f"Memory {memory.name} is not part "
            f"of protocol {self.name}."
        )

        # If the protocol had already started and did not finish,
        # expiration makes this attempt unsuccessful.
        if (
            self._tracking_started
            and not self._tracking_completed
        ):
            self._record_tracking_failure()

        if self.meas_memo is None:
            self.update_resource_manager(
                memory,
                "RAW",
            )
        else:
            for protocol_memory in self.memories:
                self.update_resource_manager(
                    protocol_memory,
                    "RAW",
                )