from typing import TYPE_CHECKING
from collections import defaultdict, deque

from sequence.kernel.process import Process
from sequence.kernel.event import Event
from metrics.link_metrics import register_pair_creation, register_pair_discard

if TYPE_CHECKING:
    from sequence.resource_management.memory_manager import MemoryInfo


class NodeTrafficApp:
    """
    Persistent end-to-end traffic generator.

    Main ideas:
    - each flow src->dst remains active over time
    - multiple sessions per flow can run in parallel
    - each completed session is replaced by a new one
    - failed or rejected sessions are retried
    - no application-level finite traffic horizon
    """

    def __init__(
        self,
        node,
        traffic_demands: dict,
        start_offset_s: float = 0.0,
        parallel_sessions_per_flow: int = 2,
        reservation_duration_s: float = 2.0,
        retry_delay_s: float = 0.2,
        monitor_period_s: float = 0.2,
    ):
        self.node = node
        self.node.set_app(self)

        self.traffic_demands = traffic_demands
        self.start_offset_ps = int(start_offset_s * 1e12)

        self.parallel_sessions_per_flow = parallel_sessions_per_flow
        self.reservation_duration_ps = int(reservation_duration_s * 1e12)
        self.retry_delay_ps = int(retry_delay_s * 1e12)
        self.monitor_period_ps = int(monitor_period_s * 1e12)

        self.global_session_id = 0

        # flow state: dst -> active session ids
        self.flow_active_sessions = {dst: set() for dst in traffic_demands.keys()}

        # all live sessions
        self.active_sessions = {}

        # completed session summaries
        self.history = []

        # pending reservation results are matched FIFO by destination
        self.pending_reservation_ids_by_dst = defaultdict(deque)

    def schedule_initial_events(self):
        for i, dst in enumerate(self.traffic_demands.keys()):
            event_time = self.start_offset_ps + int(i * 1e11)

            process = Process(self, "bootstrap_flow", [dst])
            event = Event(event_time, process)
            self.node.timeline.schedule(event)

    def bootstrap_flow(self, dst: str):
        self._fill_parallel_slots(dst)
        self._schedule_monitor(dst)

    def _schedule_monitor(self, dst: str):
        now = self.node.timeline.now()
        process = Process(self, "monitor_flow", [dst])
        event = Event(now + self.monitor_period_ps, process)
        self.node.timeline.schedule(event)

    def monitor_flow(self, dst: str):
        """
        Periodically enforce the desired number of parallel active sessions.
        """
        self._fill_parallel_slots(dst)
        self._schedule_monitor(dst)

    def _fill_parallel_slots(self, dst: str):
        while len(self.flow_active_sessions[dst]) < self.parallel_sessions_per_flow:
            self._start_new_session(dst)

    def _start_new_session(self, dst: str):
        now = self.node.timeline.now()
        demand = self.traffic_demands[dst]

        self.global_session_id += 1
        session_id = self.global_session_id

        requested_pairs = demand["memory_size"]
        target_fidelity = demand["target_fidelity"]

        # still needed for SeQUeNCe reservation protocol, but no longer used
        # as the main application-level success criterion
        setup_margin_ps = int(1e12)   # 0.2 s, puoi aumentarlo se serve
        start_time = now + setup_margin_ps
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
            "retry_count": 0,
        }

        self.flow_active_sessions[dst].add(session_id)
        self.pending_reservation_ids_by_dst[dst].append(session_id)

        print(
            f"\n[{self.node.name}] SESSION {session_id} to {dst} CREATED at {now * 1e-12:.6f}s "
            f"(requested_pairs={requested_pairs}, target_fidelity={target_fidelity:.4f})"
        )

        self.node.network_manager.request(
            dst,
            start_time=start_time,
            end_time=end_time,
            memory_size=requested_pairs,
            target_fidelity=target_fidelity
        )

        # safety check / timeout for this reservation attempt
        process = Process(self, "check_session_progress", [session_id])
        event = Event(end_time, process)
        self.node.timeline.schedule(event)

    def check_session_progress(self, session_id: int):
        """
        Called at the end of the technical reservation interval.
        If the session is incomplete, we close it and immediately trigger a retry.
        """
        if session_id not in self.active_sessions:
            return

        session = self.active_sessions[session_id]
        if session["closed"]:
            return

        requested = session["requested_pairs"]
        delivered = session["delivered_pairs"]

        if session["approved"] is False:
            self._close_session(session_id, close_reason="reservation_rejected")
            self._schedule_retry(session["dst"])
            return

        if delivered >= requested:
            self._close_session(session_id, close_reason="completed")
            self._fill_parallel_slots(session["dst"])
            return

        # approved but not completed in this attempt horizon
        self._close_session(session_id, close_reason="partial_or_timeout")
        self._schedule_retry(session["dst"])

    def _schedule_retry(self, dst: str):
        now = self.node.timeline.now()
        process = Process(self, "_fill_parallel_slots", [dst])
        event = Event(now + self.retry_delay_ps, process)
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
            if data["fidelities"] else 0.0
        )

        if data["delivery_times_ps"]:
            avg_latency_ps = sum(
                t - data["created_at_ps"] for t in data["delivery_times_ps"]
            ) / len(data["delivery_times_ps"])
            avg_latency_s = avg_latency_ps * 1e-12
        else:
            avg_latency_s = None

        summary = {
            "session_id": session_id,
            "approved": data["approved"],
            "delivered_pairs": delivered,
            "requested_pairs": requested,
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

    def get_reservation_result(self, _reservation, result: bool):
        now_s = self.node.timeline.now() * 1e-12

        # destination-based FIFO matching
        # find the oldest pending session with approved is None
        matched_session_id = None
        matched_dst = None

        for dst, queue in self.pending_reservation_ids_by_dst.items():
            while queue:
                sid = queue[0]
                if sid not in self.active_sessions or self.active_sessions[sid]["closed"]:
                    queue.popleft()
                    continue
                if self.active_sessions[sid]["approved"] is None:
                    matched_session_id = sid
                    matched_dst = dst
                    queue.popleft()
                    break
                queue.popleft()

            if matched_session_id is not None:
                break

        if matched_session_id is None:
            print(f"[{self.node.name}][{now_s:.6f}s] Reservation result received, but no pending session found.")
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

        ent_time = info.entangle_time if info.entangle_time is not None and info.entangle_time >= 0 else now_ps
        register_pair_creation(
            local_node=self.node.name,
            memory=info.memory,
            remote_node=info.remote_node,
            fidelity=info.fidelity,
            entangle_time_ps=ent_time
        )

        # choose among active, approved, incomplete sessions for the matching destination
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

        # oldest live approved session first
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

        # consume pair immediately
        register_pair_discard(
            local_node=self.node.name,
            memory=info.memory,
            discard_time_ps=now_ps,
            fidelity_before_reset=info.fidelity,
            reason="consumed_by_app"
        )

        self.node.resource_manager.update(None, info.memory, "RAW")

        # immediate completion check
        if session["delivered_pairs"] >= session["requested_pairs"]:
            self._close_session(session_id, close_reason="completed")
            self._fill_parallel_slots(session["dst"])