from typing import TYPE_CHECKING

from sequence.kernel.process import Process
from sequence.kernel.event import Event
from metrics.link_metrics import register_pair_creation, register_pair_discard
if TYPE_CHECKING:
    from sequence.resource_management.memory_manager import MemoryInfo


class NodeTrafficApp:
    def __init__(self, node, traffic_demands: dict, start_offset_s=0.0, max_requests_per_flow=8):
        self.node = node
        self.node.set_app(self)
        self.traffic_demands = traffic_demands
        self.start_offset_ps = int(start_offset_s * 1e12)
        self.max_requests_per_flow = max_requests_per_flow

        self.global_request_id = 0
        self.flow_request_counts = {dst: 0 for dst in traffic_demands.keys()}
        self.active_requests = {}
        self.history = []

    def schedule_initial_events(self):
        for i, dst in enumerate(self.traffic_demands.keys()):
            process = Process(self, "start_flow_request", [dst])
            event_time = self.start_offset_ps + int(i * 1e11)
            event = Event(event_time, process)
            self.node.timeline.schedule(event)

    def start_flow_request(self, dst: str):
        now = self.node.timeline.now()

        if self.flow_request_counts[dst] >= self.max_requests_per_flow:
            return

        demand = self.traffic_demands[dst]
        interval_ps = int(demand["interval_s"] * 1e12)
        memory_size = demand["memory_size"]
        target_fidelity = demand["target_fidelity"]

        self.global_request_id += 1
        req_id = self.global_request_id
        self.flow_request_counts[dst] += 1

        start_time = now + int(5e11)
        end_time = now + int(1e12)

        self.active_requests[req_id] = {
            "request_id": req_id,
            "src": self.node.name,
            "dst": dst,
            "created_at_ps": now,
            "window_start_ps": start_time,
            "window_end_ps": end_time,
            "approved": None,
            "requested_pairs": memory_size,
            "delivered_pairs": 0,
            "fidelities": [],
            "delivery_times_ps": [],
            "counted_memories": set(),
        }

        print(
            f"\n[{self.node.name}] REQUEST {req_id} to {dst} CREATED at {now * 1e-12:.6f}s "
            f"(window: {start_time * 1e-12:.6f}s -> {end_time * 1e-12:.6f}s)"
        )

        nm = self.node.network_manager
        nm.request(
            dst,
            start_time=start_time,
            end_time=end_time,
            memory_size=memory_size,
            target_fidelity=target_fidelity
        )

        summary_process = Process(self, "finalize_request", [req_id])
        summary_event = Event(end_time, summary_process)
        self.node.timeline.schedule(summary_event)

        next_process = Process(self, "start_flow_request", [dst])
        next_event = Event(now + interval_ps, next_process)
        self.node.timeline.schedule(next_event)

    def get_reservation_result(self, _reservation, result: bool):
        now_s = self.node.timeline.now() * 1e-12

        pending_ids = [
            rid for rid, data in self.active_requests.items()
            if data["approved"] is None
        ]

        if not pending_ids:
            print(f"[{self.node.name}][{now_s:.6f}s] Reservation result received, but no pending request found.")
            return

        req_id = min(pending_ids)
        self.active_requests[req_id]["approved"] = result
        dst = self.active_requests[req_id]["dst"]

        status = "APPROVED" if result else "FAILED"
        print(f"[{self.node.name}][{now_s:.6f}s] Reservation {status} for request {req_id} -> {dst}")

    def get_other_reservation(self, reservation):
        pass

    def get_memory(self, info: "MemoryInfo"):
        now_ps = self.node.timeline.now()
        now_s = now_ps * 1e-12

        # If RAW, it may be a discard/reset event
        if info.state == "RAW":
            register_pair_discard(
                local_node=self.node.name,
                memory=info.memory,
                discard_time_ps=now_ps,
                fidelity_before_reset=info.fidelity if info.fidelity is not None else 0.0,
                reason="memory_update_to_RAW"
            )
            return

        # Ignore useless updates
        if info.fidelity <= 0 or info.remote_node is None:
            return

        # Register observed creation of an entangled pair
        ent_time = info.entangle_time if info.entangle_time is not None and info.entangle_time >= 0 else now_ps
        register_pair_creation(
            local_node=self.node.name,
            memory=info.memory,
            remote_node=info.remote_node,
            fidelity=info.fidelity,
            entangle_time_ps=ent_time
        )

        # Candidate requests are:
        # - approved
        # - active in current window
        # - destination matches the remote entangled node
        # - not already full
        candidate_ids = []
        for rid, data in self.active_requests.items():
            if not data["approved"]:
                continue
            if not (data["window_start_ps"] <= now_ps <= data["window_end_ps"]):
                continue
            if data["dst"] != info.remote_node:
                continue
            if data["delivered_pairs"] >= data["requested_pairs"]:
                continue
            candidate_ids.append(rid)

        if not candidate_ids:
            return

        req_id = min(candidate_ids)
        req_data = self.active_requests[req_id]

        if info.index in req_data["counted_memories"]:
            return

        req_data["counted_memories"].add(info.index)
        req_data["delivered_pairs"] += 1
        req_data["fidelities"].append(info.fidelity)
        req_data["delivery_times_ps"].append(now_ps)

        print(
            f"[{self.node.name}][{now_s:.6f}s] Request {req_id} -> {req_data['dst']} "
            f"delivered {req_data['delivered_pairs']}/{req_data['requested_pairs']} "
            f"(memory={info.index}, remote={info.remote_node}, fidelity={info.fidelity:.6f})"
        )

        # Register discard before reset
        register_pair_discard(
            local_node=self.node.name,
            memory=info.memory,
            discard_time_ps=now_ps,
            fidelity_before_reset=info.fidelity,
            reason="consumed_by_app"
        )

        self.node.resource_manager.update(None, info.memory, "RAW")

    def finalize_request(self, req_id: int):
        if req_id not in self.active_requests:
            return

        data = self.active_requests.pop(req_id)

        approved = data["approved"]
        delivered = data["delivered_pairs"]

        avg_fidelity = sum(data["fidelities"]) / len(data["fidelities"]) if data["fidelities"] else 0.0

        if data["delivery_times_ps"]:
            avg_latency_ps = sum(
                t - data["window_start_ps"] for t in data["delivery_times_ps"]
            ) / len(data["delivery_times_ps"])
            avg_latency_s = avg_latency_ps * 1e-12
        else:
            avg_latency_s = None

        summary = {
            "request_id": req_id,
            "approved": approved,
            "delivered_pairs": delivered,
            "requested_pairs": data["requested_pairs"],
            "avg_fidelity": avg_fidelity,
            "avg_latency_s": avg_latency_s,
            "src": data["src"],
            "dst": data["dst"],
        }
        self.history.append(summary)

        print(f"[{self.node.name}] SUMMARY request {req_id} -> {data['dst']}")
        print(f"approved     : {approved}")
        print(f"delivered    : {delivered}/{data['requested_pairs']}")
        print(f"avg fidelity : {avg_fidelity:.6f}")
        print(f"avg latency  : {avg_latency_s if avg_latency_s is not None else 'None'}")