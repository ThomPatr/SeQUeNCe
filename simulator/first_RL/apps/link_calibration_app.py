from typing import TYPE_CHECKING

from sequence.kernel.event import Event
from sequence.kernel.process import Process
from simulator.first_RL.metrics.link_metrics import (
    register_pair_creation,
    register_pair_discard,
)
if TYPE_CHECKING:
    from sequence.resource_management.memory_manager import MemoryInfo


class LinkCalibrationApp:
    """
    Saturated direct-link calibration application.

    A single long reservation is created. Every end-to-end elementary
    pair delivered to the application is immediately consumed and the
    corresponding memory is reset to RAW, allowing continuous generation.
    """

    def __init__(
        self,
        node,
        destination: str,
        reservation_start_s: float,
        reservation_end_s: float,
        reserved_memories: int = 10,
        target_fidelity: float = 0.5,
    ):
        self.node = node
        self.node.set_app(self)

        self.destination = destination
        self.reservation_start_ps = int(
            reservation_start_s * 1e12
        )
        self.reservation_end_ps = int(
            reservation_end_s * 1e12
        )

        self.reserved_memories = reserved_memories
        self.target_fidelity = target_fidelity

        self.reservation_approved = None

        self.delivered_pairs = 0
        self.delivery_times_ps = []
        self.delivered_fidelities = []
        self.counted_states = set()

    def schedule_request(self):
        process = Process(self, "start_request", [])
        event = Event(0, process)
        self.node.timeline.schedule(event)

    def start_request(self):
        self.node.network_manager.request(
            self.destination,
            start_time=self.reservation_start_ps,
            end_time=self.reservation_end_ps,
            memory_size=self.reserved_memories,
            target_fidelity=self.target_fidelity,
            identity=1,
        )

    def get_reservation_result(self, reservation, result: bool):
        self.reservation_approved = result

        print(
            f"[CALIBRATION] Reservation "
            f"{'APPROVED' if result else 'REJECTED'}: "
            f"{self.node.name} -> {self.destination}"
        )

    def get_other_reservation(self, reservation):
        pass

    def get_memory(self, info: "MemoryInfo"):
        if info.state == "RAW":
            return

        if info.remote_node != self.destination:
            return

        if info.fidelity is None or info.fidelity <= 0:
            return

        now_ps = self.node.timeline.now()

        entangle_time_ps = (
            info.entangle_time
            if (
                info.entangle_time is not None
                and info.entangle_time >= 0
            )
            else now_ps
        )

        state_key = (
            info.memory.name,
            entangle_time_ps,
        )

        if state_key in self.counted_states:
            return

        self.counted_states.add(state_key)

        # Record the successfully generated pair before consuming it.
        register_pair_creation(
            local_node=self.node.name,
            memory=info.memory,
            remote_node=info.remote_node,
            fidelity=info.fidelity,
            entangle_time_ps=entangle_time_ps,
        )

        self.delivered_pairs += 1
        self.delivery_times_ps.append(now_ps)
        self.delivered_fidelities.append(info.fidelity)

        register_pair_discard(
            local_node=self.node.name,
            memory=info.memory,
            discard_time_ps=now_ps,
            fidelity_before_reset=info.fidelity,
            reason="consumed_by_calibration_app",
        )

        self.node.resource_manager.update(
            None,
            info.memory,
            "RAW",
        )