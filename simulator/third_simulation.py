from sequence.topology.router_net_topo import RouterNetTopo
from sequence.kernel.process import Process
from sequence.kernel.event import Event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sequence.resource_management.memory_manager import MemoryInfo


class TrafficApp:
    def __init__(self, node, other: str, app_name: str,
                 memory_size=5, target_fidelity=0.75,
                 request_interval_s=1.0, start_delay_s=0.0, max_requests=10):
        self.node = node
        self.node.set_app(self)
        self.other = other
        self.app_name = app_name
        self.memory_size = memory_size
        self.target_fidelity = target_fidelity
        self.request_interval_ps = int(request_interval_s * 1e12)
        self.start_delay_ps = int(start_delay_s * 1e12)
        self.max_requests = max_requests

        self.request_id = 0
        self.active_requests = {}
        self.history = []

    def start(self):
        now = self.node.timeline.now()

        if self.request_id >= self.max_requests:
            return

        self.request_id += 1
        req_id = self.request_id

        start_time = now + int(5e11)   # request window starts 0.5 s later
        end_time = now + int(1e12)     # window length = 0.5 s

        self.active_requests[req_id] = {
            "request_id": req_id,
            "created_at_ps": now,
            "window_start_ps": start_time,
            "window_end_ps": end_time,
            "approved": None,
            "delivered_pairs": 0,
            "fidelities": [],
            "delivery_times_ps": [],
            "counted_memories": set()
        }

        print(
            f"\n[{self.app_name}] REQUEST {req_id} CREATED at {now * 1e-12:.6f}s "
            f"(window: {start_time * 1e-12:.6f}s -> {end_time * 1e-12:.6f}s)"
        )

        nm = self.node.network_manager
        nm.request(
            self.other,
            start_time=start_time,
            end_time=end_time,
            memory_size=self.memory_size,
            target_fidelity=self.target_fidelity
        )

        summary_process = Process(self, "finalize_request", [req_id])
        summary_event = Event(end_time, summary_process)
        self.node.timeline.schedule(summary_event)

        next_process = Process(self, "start", [])
        next_event = Event(now + self.request_interval_ps, next_process)
        self.node.timeline.schedule(next_event)

    def get_reservation_result(self, _reservation, result: bool):
        now_s = self.node.timeline.now() * 1e-12

        pending_ids = [rid for rid, data in self.active_requests.items() if data["approved"] is None]
        if not pending_ids:
            print(f"[{self.app_name}][{now_s:.6f}s] Reservation result received, but no pending request found.")
            return

        req_id = min(pending_ids)
        self.active_requests[req_id]["approved"] = result

        status = "APPROVED" if result else "FAILED"
        print(f"[{self.app_name}][{now_s:.6f}s] Reservation {status} for request {req_id}")
    
    def get_other_reservation(self, _reservation):
        """
        Called when receiving a reservation request from another node.
        No explicit responder-side logic is needed in this experiment.
        """
        pass

    def get_memory(self, info: "MemoryInfo"):
        print(f"[DEBUG {self.app_name}] get_memory called: state={info.state}, remote={info.remote_node}, fidelity={info.fidelity}, index={info.index}")
        if info.remote_node != self.other or info.fidelity <= 0 or info.state == "RAW":
            return

        now_ps = self.node.timeline.now()
        now_s = now_ps * 1e-12

        candidate_ids = []
        for rid, data in self.active_requests.items():
            if data["window_start_ps"] <= now_ps <= data["window_end_ps"]:
                candidate_ids.append(rid)

        if not candidate_ids:
            return

        req_id = min(candidate_ids)
        req_data = self.active_requests[req_id]

        if req_data["delivered_pairs"] >= self.memory_size:
            return

        if info.index in req_data["counted_memories"]:
            return

        req_data["counted_memories"].add(info.index)
        req_data["delivered_pairs"] += 1
        req_data["fidelities"].append(info.fidelity)
        req_data["delivery_times_ps"].append(now_ps)

        print(
            f"[{self.app_name}][{now_s:.6f}s] Request {req_id} -> delivered "
            f"{req_data['delivered_pairs']}/{self.memory_size} "
            f"(memory={info.index}, fidelity={info.fidelity:.6f})"
        )

        self.node.resource_manager.update(None, info.memory, "RAW")

    def finalize_request(self, req_id: int):
        if req_id not in self.active_requests:
            return

        data = self.active_requests.pop(req_id)
        approved = data["approved"]
        delivered = data["delivered_pairs"]

        avg_fidelity = (
            sum(data["fidelities"]) / len(data["fidelities"])
            if data["fidelities"] else 0.0
        )

        avg_latency_s = None
        if data["delivery_times_ps"]:
            avg_latency_ps = sum(
                t - data["window_start_ps"] for t in data["delivery_times_ps"]
            ) / len(data["delivery_times_ps"])
            avg_latency_s = avg_latency_ps * 1e-12

        summary = {
            "request_id": req_id,
            "approved": approved,
            "delivered_pairs": delivered,
            "requested_pairs": self.memory_size,
            "avg_fidelity": avg_fidelity,
            "avg_latency_s": avg_latency_s,
            "src": self.node.name,
            "dst": self.other,
        }
        self.history.append(summary)

        print(f"[{self.app_name}] SUMMARY request {req_id}")
        print(f"approved     : {approved}")
        print(f"delivered    : {delivered}/{self.memory_size}")
        print(f"avg fidelity : {avg_fidelity:.6f}")
        print(f"avg latency  : {avg_latency_s if avg_latency_s is not None else 'None'}")

    def print_global_summary(self):
        print("\n================ GLOBAL SUMMARY ================")
        if not self.history:
            print("No completed requests.")
            print("===============================================")
            return

        total_requests = len(self.history)
        approved_requests = sum(1 for x in self.history if x["approved"])
        total_delivered = sum(x["delivered_pairs"] for x in self.history)

        avg_fidelity_all = (
            sum(x["avg_fidelity"] for x in self.history) / total_requests
            if total_requests > 0 else 0.0
        )

        valid_latencies = [x["avg_latency_s"] for x in self.history if x["avg_latency_s"] is not None]
        avg_latency_all = (
            sum(valid_latencies) / len(valid_latencies)
            if valid_latencies else None
        )

        print(f"total requests       : {total_requests}")
        print(f"approved requests    : {approved_requests}")
        print(f"total delivered pairs: {total_delivered}")
        print(f"avg fidelity overall : {avg_fidelity_all:.6f}")
        if avg_latency_all is not None:
            print(f"avg latency overall  : {avg_latency_all:.6f} s")
        else:
            print("avg latency overall  : None")
        print("===============================================")


def compute_flow_statistics(apps):
    print("\n================ FLOW STATISTICS ================\n")

    for app in apps:
        history = app.history

        if not history:
            print(f"{app.app_name}: no data\n")
            continue

        total_requests = len(history)

        approved_requests = sum(1 for x in history if x["approved"])
        completed_requests = sum(
            1 for x in history
            if x["approved"] and x["delivered_pairs"] == x["requested_pairs"]
        )

        total_delivered = sum(x["delivered_pairs"] for x in history)
        total_requested = sum(x["requested_pairs"] for x in history)

        avg_delivery_ratio = (
            total_delivered / total_requested
            if total_requested > 0 else 0
        )

        avg_fidelity = (
            sum(x["avg_fidelity"] for x in history) / total_requests
            if total_requests > 0 else 0
        )

        latencies = [x["avg_latency_s"] for x in history if x["avg_latency_s"] is not None]
        avg_latency = sum(latencies) / len(latencies) if latencies else None

        print(f"Flow: {app.app_name}")
        print(f"  total requests        : {total_requests}")
        print(f"  approve rate          : {approved_requests / total_requests:.2f}")
        print(f"  success rate          : +{completed_requests / total_requests:.2f}")
        print(f"  avg delivery ratio    : {avg_delivery_ratio:.2f}")
        print(f"  avg fidelity          : {avg_fidelity:.4f}")

        if avg_latency is not None:
            print(f"  avg latency           : {avg_latency:.6f} s")
        else:
            print(f"  avg latency           : None")

        print("------------------------------------------------\n")

def set_parameters(topology: RouterNetTopo):
    MEMO_FREQ = 8e3
    MEMO_EXPIRE = 0.02
    MEMO_EFFICIENCY = 0.30
    MEMO_FIDELITY = 0.85

    for node in topology.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER):
        memory_array = node.get_components_by_type("MemoryArray")[0]
        memory_array.update_memory_params("frequency", MEMO_FREQ)
        memory_array.update_memory_params("coherence_time", MEMO_EXPIRE)
        memory_array.update_memory_params("efficiency", MEMO_EFFICIENCY)
        memory_array.update_memory_params("raw_fidelity", MEMO_FIDELITY)

    DETECTOR_EFFICIENCY = 0.40
    DETECTOR_COUNT_RATE = 2e7
    DETECTOR_RESOLUTION = 50

    for node in topology.get_nodes_by_type(RouterNetTopo.BSM_NODE):
        bsm = node.get_components_by_type("SingleAtomBSM")[0]
        bsm.update_detectors_params("efficiency", DETECTOR_EFFICIENCY)
        bsm.update_detectors_params("count_rate", DETECTOR_COUNT_RATE)
        bsm.update_detectors_params("time_resolution", DETECTOR_RESOLUTION)

    ATTENUATION = 0.00025
    QC_FREQ = 5e6

    for qc in topology.get_qchannels():
        qc.attenuation = ATTENUATION
        qc.frequency = QC_FREQ


def print_memory_status(router):
    print(f"\n=== {router.name} memories (non-RAW only) ===")
    print("Index\tState\t\tRemote Node\tFidelity\tEntangle Time (s)")
    for i, info in enumerate(router.resource_manager.memory_manager):
        if info.state != "RAW":
            ent_time_s = (
                info.entangle_time * 1e-12
                if info.entangle_time is not None and info.entangle_time >= 0
                else None
            )
            print(
                f"{i}\t{info.state}\t\t{info.remote_node}\t\t"
                f"{info.fidelity}\t\t{ent_time_s}"
            )

def main():
    network_config = r"C:\Users\thoma\OneDrive\Desktop\Double degree\Internship\Internsheep\Simulator\SeQUeNCe\simulator\topo_star.json"
    network_topo = RouterNetTopo(network_config)
    tl = network_topo.get_timeline()

    set_parameters(network_topo)

    routers = {
        node.name: node
        for node in network_topo.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER)
    }

    # traffic matrix: (source, destination, request interval in seconds)
    traffic_matrix = [
        ("valrose", "sophia", 1.0),
        ("sophia", "antibes", 1.2),
        ("antibes", "grasse", 1.5),
        ("grasse", "valrose", 1.0),
    ]

    apps = []

    for i, (src, dst, interval_s) in enumerate(traffic_matrix):
        app = TrafficApp(
            node=routers[src],
            other=dst,
            app_name=f"{src}->{dst}",
            memory_size=5,
            target_fidelity=0.80,
            request_interval_s=interval_s,
            start_delay_s=0.2 * i,
            max_requests=10
        )
        apps.append(app)

    # schedule first event for each traffic flow
    for i, app in enumerate(apps):
        first_process = Process(app, "start", [])
        first_event = Event(int(app.start_delay_ps), first_process)
        tl.schedule(first_event)

    tl.init()
    tl.run()
    
    compute_flow_statistics(apps)
    for app in apps:
        print(f"\n=== FINAL HISTORY FOR {app.app_name} ===")
        for item in app.history:
            print(item)


if __name__ == "__main__":
    main()