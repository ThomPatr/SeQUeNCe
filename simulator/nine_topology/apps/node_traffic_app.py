from typing import TYPE_CHECKING
from collections import defaultdict, deque

from sequence.kernel.process import Process
from sequence.kernel.event import Event

from simulator.first_RL.metrics.link_metrics import (
    register_pair_creation,
    register_pair_discard
)

if TYPE_CHECKING:
    from sequence.resource_management.memory_manager import MemoryInfo


class NodeTrafficApp:
    """
    Persistent end-to-end traffic generator with controlled parallelism.

    Main ideas:
    - each src->dst flow remains active over time;
    - multiple sessions per flow can overlap in time;
    - parallel sessions are staggered to avoid same-timestep protocol collisions;
    - completed, failed, or timed-out sessions are replaced automatically;
    - EPR pairs delivered to the application are consumed immediately.
    """

    def __init__(
        self,
        node,
        traffic_demands: dict,
        start_offset_s: float = 0.0,
        parallel_sessions_per_flow: int = 2,
        parallel_stagger_s: float = 0.2,
        reservation_duration_s: float = 5.0,
        reservation_setup_margin_s: float = 1.0,
        retry_delay_s: float = 1.0,
        monitor_period_s: float = 0.5,
    ):
        self.node = node
        self.node.set_app(self)

        self.traffic_demands = traffic_demands
        self.start_offset_ps = int(start_offset_s * 1e12)

        self.parallel_sessions_per_flow = parallel_sessions_per_flow
        self.parallel_stagger_ps = int(parallel_stagger_s * 1e12)
        self.reservation_duration_ps = int(reservation_duration_s * 1e12)
        self.reservation_setup_margin_ps = int(reservation_setup_margin_s * 1e12)
        self.retry_delay_ps = int(retry_delay_s * 1e12)
        self.monitor_period_ps = int(monitor_period_s * 1e12)

        self.global_session_id = 0

        self.flow_active_sessions = {
            dst: set() for dst in traffic_demands.keys()
        }

        self.active_sessions = {}
        self.history = []

        self.pending_reservation_ids_by_dst = defaultdict(deque)

    def schedule_initial_events(self):
        """
        Bootstrap all flows.

        Parallel sessions are not created at the exact same timestamp.
        They are staggered by parallel_stagger_s but remain overlapping
        because reservation windows are much longer.
        """
        for flow_idx, dst in enumerate(self.traffic_demands.keys()):
            for slot_idx in range(self.parallel_sessions_per_flow):
                event_time = (
                    self.start_offset_ps
                    + int(flow_idx * 5e11)
                    + slot_idx * self.parallel_stagger_ps
                )

                process = Process(self, "_start_new_session", [dst])
                event = Event(event_time, process)
                self.node.timeline.schedule(event)

            monitor_time = (
                self.start_offset_ps
                + int(flow_idx * 5e11)
                + self.monitor_period_ps
            )

            monitor_process = Process(self, "monitor_flow", [dst])
            monitor_event = Event(monitor_time, monitor_process)
            self.node.timeline.schedule(monitor_event)

    def monitor_flow(self, dst: str):
        """
        Periodically checks whether the flow still has the desired number
        of active sessions. If some sessions completed or failed, it refills
        the available slots.
        """
        self._fill_parallel_slots(dst)

        process = Process(self, "monitor_flow", [dst])
        event = Event(self.node.timeline.now() + self.monitor_period_ps, process)
        self.node.timeline.schedule(event)

    def _count_active_sessions_for_flow(self, dst: str) -> int:
        return sum(
            1 for sid in self.flow_active_sessions[dst]
            if sid in self.active_sessions
        )

    def _fill_parallel_slots(self, dst: str):
        """
        Creates new sessions until the number of active sessions for this flow
        reaches parallel_sessions_per_flow.
        """
        missing = (
            self.parallel_sessions_per_flow
            - self._count_active_sessions_for_flow(dst)
        )

        if missing <= 0:
            return

        now = self.node.timeline.now()

        for slot_idx in range(missing):
            process = Process(self, "_start_new_session", [dst])
            event_time = now + slot_idx * self.parallel_stagger_ps
            event = Event(event_time, process)
            self.node.timeline.schedule(event)

    def _start_new_session(self, dst: str):
        """
        Starts a new end-to-end entanglement session for a given destination.
        """
        if self._count_active_sessions_for_flow(dst) >= self.parallel_sessions_per_flow:
            return

        now = self.node.timeline.now()
        demand = self.traffic_demands[dst]

        self.global_session_id += 1
        session_id = self.global_session_id

        requested_pairs = demand["memory_size"]
        target_fidelity = demand["target_fidelity"]

        start_time = now + self.reservation_setup_margin_ps
        end_time = start_time + self.reservation_duration_ps

        self.active_sessions[session_id] = {
            "session_id": session_id,
            "src": self.node.name,
            "dst": dst,
            "created_at_ps": now,
            "reservation_start_ps": start_time,
            "reservation_end_ps": end_time,
            "approved": None,
            "requested_pairs": requested_pairs,
            "delivered_pairs": 0,
            "fidelities": [],
            "delivery_times_ps": [],
            "counted_memories": set(),
            "first_delivery_ps": None,
            "last_delivery_ps": None,
            "closed": False,
            "close_reason": None,
        }

        self.flow_active_sessions[dst].add(session_id)
        self.pending_reservation_ids_by_dst[dst].append(session_id)

        print(
            f"\n[{self.node.name}] SESSION {session_id} to {dst} CREATED "
            f"at {now * 1e-12:.6f}s "
            f"(requested_pairs={requested_pairs}, "
            f"target_fidelity={target_fidelity:.4f}, "
            f"reservation={start_time * 1e-12:.6f}s->{end_time * 1e-12:.6f}s)"
        )

        self.node.network_manager.request(
            dst,
            start_time=start_time,
            end_time=end_time,
            memory_size=requested_pairs,
            target_fidelity=target_fidelity
        )

        process = Process(self, "check_session_progress", [session_id])
        event = Event(end_time, process)
        self.node.timeline.schedule(event)

    def check_session_progress(self, session_id: int):
        """
        Called at the end of the technical reservation interval.

        If the session has completed, it is closed as completed.
        If it is incomplete or rejected, it is closed and the flow is refilled.
        """
        if session_id not in self.active_sessions:
            return

        session = self.active_sessions[session_id]

        if session["closed"]:
            return

        requested = session["requested_pairs"]
        delivered = session["delivered_pairs"]
        dst = session["dst"]

        if session["approved"] is False:
            self._close_session(session_id, close_reason="reservation_rejected")
            self._schedule_retry(dst)
            return

        if delivered >= requested:
            self._close_session(session_id, close_reason="completed")
            self._schedule_retry(dst, delay_ps=self.parallel_stagger_ps)
            return

        self._close_session(session_id, close_reason="partial_or_timeout")
        self._schedule_retry(dst)

    def _schedule_retry(self, dst: str, delay_ps: int | None = None):
        if delay_ps is None:
            delay_ps = self.retry_delay_ps

        process = Process(self, "_fill_parallel_slots", [dst])
        event = Event(self.node.timeline.now() + delay_ps, process)
        self.node.timeline.schedule(event)

    def _close_session(self, session_id: int, close_reason: str):
        if session_id not in self.active_sessions:
            return

        data = self.active_sessions[session_id]

        if data["closed"]:
            return

        data["closed"] = True
        data["close_reason"] = close_reason

        delivered = data["delivered_pairs"]
        requested = data["requested_pairs"]

        avg_fidelity = (
            sum(data["fidelities"]) / len(data["fidelities"])
            if data["fidelities"]
            else 0.0
        )

        if data["delivery_times_ps"]:
            avg_latency_ps = sum(
                t - data["created_at_ps"]
                for t in data["delivery_times_ps"]
            ) / len(data["delivery_times_ps"])
            avg_latency_s = avg_latency_ps * 1e-12
        else:
            avg_latency_s = None

        summary = {
            "session_id": session_id,
            "approved": data["approved"],
            "delivered_pairs": delivered,
            "requested_pairs": requested,
            "delivery_ratio": delivered / requested if requested > 0 else 0.0,
            "completed": delivered >= requested,
            "avg_fidelity": avg_fidelity,
            "avg_latency_s": avg_latency_s,
            "src": data["src"],
            "dst": data["dst"],
            "close_reason": close_reason,
        }

        self.history.append(summary)

        print(f"[{self.node.name}] SESSION SUMMARY {session_id} -> {data['dst']}")
        print(f"approved     : {data['approved']}")
        print(f"delivered    : {delivered}/{requested}")
        print(f"avg fidelity : {avg_fidelity:.6f}")
        print(f"avg latency  : {avg_latency_s if avg_latency_s is not None else 'None'}")
        print(f"close reason : {close_reason}")

        dst = data["dst"]
        self.flow_active_sessions[dst].discard(session_id)
        del self.active_sessions[session_id]

    def get_reservation_result(self, reservation, result: bool):
        """
        Called by SeQUeNCe when the RSVP reservation result is available.

        We try to match the result to the oldest pending session toward
        the corresponding destination.
        """
        now_s = self.node.timeline.now() * 1e-12

        matched_session_id = None
        matched_dst = None

        reservation_dst = None
        for attr in ["responder", "dst", "dest", "destination"]:
            if hasattr(reservation, attr):
                reservation_dst = getattr(reservation, attr)
                break

        if reservation_dst in self.pending_reservation_ids_by_dst:
            queues_to_check = [reservation_dst]
        else:
            queues_to_check = list(self.pending_reservation_ids_by_dst.keys())

        for dst in queues_to_check:
            queue = self.pending_reservation_ids_by_dst[dst]

            while queue:
                sid = queue.popleft()

                if sid not in self.active_sessions:
                    continue

                if self.active_sessions[sid]["closed"]:
                    continue

                if self.active_sessions[sid]["approved"] is None:
                    matched_session_id = sid
                    matched_dst = dst
                    break

            if matched_session_id is not None:
                break

        if matched_session_id is None:
            print(
                f"[{self.node.name}][{now_s:.6f}s] Reservation result received, "
                f"but no pending session found."
            )
            return

        self.active_sessions[matched_session_id]["approved"] = result
        status = "APPROVED" if result else "FAILED"

        print(
            f"[{self.node.name}][{now_s:.6f}s] Reservation {status} "
            f"for session {matched_session_id} -> {matched_dst}"
        )

    def get_other_reservation(self, reservation):
        pass

    def get_memory(self, info: "MemoryInfo"):
        now_ps = self.node.timeline.now()
        now_s = now_ps * 1e-12

        if info.state == "RAW":
            register_pair_discard(
                local_node=self.node.name,
                memory=info.memory,
                discard_time_ps=now_ps,
                fidelity_before_reset=info.fidelity if info.fidelity is not None else 0.0,
                reason="memory_update_to_RAW"
            )
            return

        if info.fidelity <= 0 or info.remote_node is None:
            return

        ent_time = (
            info.entangle_time
            if info.entangle_time is not None and info.entangle_time >= 0
            else now_ps
        )

        register_pair_creation(
            local_node=self.node.name,
            memory=info.memory,
            remote_node=info.remote_node,
            fidelity=info.fidelity,
            entangle_time_ps=ent_time
        )

        candidate_ids = []

        for sid, data in self.active_sessions.items():
            if data["closed"]:
                continue

            if data["approved"] is not True:
                continue

            if data["dst"] != info.remote_node:
                continue

            if data["delivered_pairs"] >= data["requested_pairs"]:
                continue

            candidate_ids.append(sid)

        if not candidate_ids:
            return

        session_id = min(candidate_ids)
        session = self.active_sessions[session_id]

        if info.index in session["counted_memories"]:
            return

        session["counted_memories"].add(info.index)
        session["delivered_pairs"] += 1
        session["fidelities"].append(info.fidelity)
        session["delivery_times_ps"].append(now_ps)

        if session["first_delivery_ps"] is None:
            session["first_delivery_ps"] = now_ps

        session["last_delivery_ps"] = now_ps

        print(
            f"[{self.node.name}][{now_s:.6f}s] Session {session_id} -> {session['dst']} "
            f"delivered {session['delivered_pairs']}/{session['requested_pairs']} "
            f"(memory={info.index}, remote={info.remote_node}, fidelity={info.fidelity:.6f})"
        )

        register_pair_discard(
            local_node=self.node.name,
            memory=info.memory,
            discard_time_ps=now_ps,
            fidelity_before_reset=info.fidelity,
            reason="consumed_by_app"
        )

        self.node.resource_manager.update(None, info.memory, "RAW")

        if session_id in self.active_sessions:
            if session["delivered_pairs"] >= session["requested_pairs"]:
                dst = session["dst"]
                self._close_session(session_id, close_reason="completed")
                self._schedule_retry(dst, delay_ps=self.parallel_stagger_ps)