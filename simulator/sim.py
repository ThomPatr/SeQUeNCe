from sequence.topology.router_net_topo import RouterNetTopo
from sequence.kernel.process import Process
from sequence.kernel.event import Event
import math
import random
from typing import TYPE_CHECKING

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

        self.active_requests = {}   # req_id -> metadata
        self.history = []

    def schedule_initial_events(self):
        """Schedule the first request generation event for each outgoing destination."""
        for i, dst in enumerate(self.traffic_demands.keys()):
            process = Process(self, "start_flow_request", [dst])
            # small staggering to avoid all flows starting at exactly the same time
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

        # keep windows for Step 1
        start_time = now + int(5e11)   # +0.5 s
        end_time = now + int(1e12)     # +1.0 s => window length 0.5 s

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

        # summary at end of request window
        summary_process = Process(self, "finalize_request", [req_id])
        summary_event = Event(end_time, summary_process)
        self.node.timeline.schedule(summary_event)

        # schedule next request for the same destination
        next_process = Process(self, "start_flow_request", [dst])
        next_event = Event(now + interval_ps, next_process)
        self.node.timeline.schedule(next_event)

    def get_reservation_result(self, _reservation, result: bool):
        now_s = self.node.timeline.now() * 1e-12

        # among active requests waiting for approval, choose the oldest one
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
        """Required by SeQUeNCe when this node is the remote side of another node's request."""
        pass

    def get_memory(self, info: "MemoryInfo"):
        now_ps = self.node.timeline.now()
        now_s = now_ps * 1e-12

        # Ignore useless updates
        if info.state == "RAW" or info.fidelity <= 0 or info.remote_node is None:
            return

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

        # choose oldest matching active request
        req_id = min(candidate_ids)
        req_data = self.active_requests[req_id]

        # avoid double-counting same memory index within same request
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

        # free memory after counting, as in previous experiments
        self.node.resource_manager.update(None, info.memory, "RAW")

    def finalize_request(self, req_id: int):
        if req_id not in self.active_requests:
            return

        data = self.active_requests.pop(req_id)

        approved = data["approved"]
        delivered = data["delivered_pairs"]

        if data["fidelities"]:
            avg_fidelity = sum(data["fidelities"]) / len(data["fidelities"])
        else:
            avg_fidelity = 0.0

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
def compute_node_flow_statistics(apps):
    print("\n================ NODE FLOW STATISTICS ================\n")

    for app in apps:
        if not app.history:
            print(f"Node: {app.node.name}")
            print("  no completed requests")
            print("-----------------------------------------------------\n")
            continue

        print(f"Node: {app.node.name}")

        destinations = sorted(set(x["dst"] for x in app.history))

        for dst in destinations:
            flow_hist = [x for x in app.history if x["dst"] == dst]

            total_requests = len(flow_hist)

            approved_requests = sum(1 for x in flow_hist if x["approved"])

            completed_requests = sum(
                1 for x in flow_hist
                if x["approved"] and x["delivered_pairs"] == x["requested_pairs"]
            )

            total_delivered = sum(x["delivered_pairs"] for x in flow_hist)
            total_requested = sum(x["requested_pairs"] for x in flow_hist)

            approval_rate = approved_requests / total_requests if total_requests > 0 else 0.0
            completion_rate = completed_requests / total_requests if total_requests > 0 else 0.0
            avg_delivery_ratio = total_delivered / total_requested if total_requested > 0 else 0.0

            # Fidelity only over requests that actually delivered at least one pair
            successful_requests = [x for x in flow_hist if x["delivered_pairs"] > 0]
            if successful_requests:
                avg_fidelity_success_only = (
                    sum(x["avg_fidelity"] for x in successful_requests) / len(successful_requests)
                )
            else:
                avg_fidelity_success_only = None

            # Latency only over requests that actually delivered at least one pair
            valid_latencies = [
                x["avg_latency_s"] for x in successful_requests
                if x["avg_latency_s"] is not None
            ]
            avg_latency_success_only = (
                sum(valid_latencies) / len(valid_latencies)
                if valid_latencies else None
            )

            print(f"  Flow {app.node.name} -> {dst}")
            print(f"    total requests                 : {total_requests}")
            print(f"    approval rate                  : {approval_rate:.2f}")
            print(f"    completion rate                : {completion_rate:.2f}")
            print(f"    avg delivery ratio             : {avg_delivery_ratio:.2f}")

            if avg_fidelity_success_only is not None:
                print(f"    avg fidelity (successful only) : {avg_fidelity_success_only:.4f}")
            else:
                print(f"    avg fidelity (successful only) : None")

            if avg_latency_success_only is not None:
                print(f"    avg latency (successful only)  : {avg_latency_success_only:.6f} s")
            else:
                print(f"    avg latency (successful only)  : None")

            print()

        print("-----------------------------------------------------\n")
def endpoint_name(x):
    return x.name if hasattr(x, "name") else x

def normalize_link(a, b):
    a_name = endpoint_name(a)
    b_name = endpoint_name(b)
    return tuple(sorted((a_name, b_name)))


# Secondary physical effects per link 
# 1)base_alpha_db_per_km: nominal fiber attenuation at 1550 nm
# 2)extra_loss_db: connectors / splices / discontinuities lumped as fixed loss
# 3)dispersion_ps_per_nm_km: chromatic dispersion proxy
# 4)pmd_ps_sqrt_km: PMD proxy
LINK_SECONDARY_PARAMS = {
    normalize_link("valrose", "sophia"): {
        "base_alpha_db_per_km": 0.18,
        "extra_loss_db": 0.10,
        "dispersion_ps_per_nm_km": 18.0,
        "pmd_ps_sqrt_km": 0.04,
    },
    normalize_link("sophia", "ecov"): {
        "base_alpha_db_per_km": 0.22,
        "extra_loss_db": 0.05,
        "dispersion_ps_per_nm_km": 18.0,
        "pmd_ps_sqrt_km": 0.04,
    },
    normalize_link("sophia", "antibes"): {
        "base_alpha_db_per_km": 0.20,
        "extra_loss_db": 0.03,
        "dispersion_ps_per_nm_km": 18.0,
        "pmd_ps_sqrt_km": 0.04,
    },
    normalize_link("sophia", "grasse"): {
        "base_alpha_db_per_km": 0.24,
        "extra_loss_db": 0.08,
        "dispersion_ps_per_nm_km": 18.0,
        "pmd_ps_sqrt_km": 0.04,
    },
}


def effective_attenuation_db_per_km(base_alpha_db_per_km: float,
                                    extra_loss_db: float,
                                    distance_m: float) -> float:
    """Convert fixed link loss into an equivalent distributed attenuation."""
    distance_km = max(distance_m / 1000.0, 1e-9)
    return base_alpha_db_per_km + extra_loss_db / distance_km


def dispersion_penalty(distance_m: float,
                       dispersion_ps_per_nm_km: float,
                       k_disp: float = 0.002) -> float:
    """
    Small deterministic fidelity penalty caused by chromatic dispersion,not a full optical model.
    """
    distance_km = distance_m / 1000.0
    penalty = k_disp * dispersion_ps_per_nm_km * distance_km / 100.0
    return max(0.0, min(penalty, 0.05))


def pmd_noise(distance_m: float,
              pmd_ps_sqrt_km: float,
              scale: float = 0.01) -> float:
    """
    Stochastic fidelity perturbation caused by PMD.
    Returns a small Gaussian term to subtract from fidelity.
    """
    distance_km = distance_m / 1000.0
    sigma = scale * pmd_ps_sqrt_km * math.sqrt(distance_km)
    noise = random.gauss(0.0, sigma)
    return max(0.0, min(abs(noise), 0.02))

def set_parameters(topology: RouterNetTopo):
    
    # Node-level hardware parameters

    NODE_HW = {
        "valrose": {"memo_freq": 8e3, "memo_expire": 0.05, "memo_eff": 0.45, "base_fidelity": 0.90},
        "ecov":    {"memo_freq": 8e3, "memo_expire": 0.06, "memo_eff": 0.47, "base_fidelity": 0.91},
        "sophia":  {"memo_freq": 8e3, "memo_expire": 0.05, "memo_eff": 0.46, "base_fidelity": 0.90},
        "antibes": {"memo_freq": 8e3, "memo_expire": 0.045, "memo_eff": 0.44, "base_fidelity": 0.89},
        "grasse":  {"memo_freq": 8e3, "memo_expire": 0.04, "memo_eff": 0.42, "base_fidelity": 0.88},
    }

    # link-level fidelity penalties from secondary parameters
    # assign to each node a  penalty derived from its incident links
    node_penalties = {name: [] for name in NODE_HW.keys()}

    for qc in topology.get_qchannels():
        a = qc.sender
        b = qc.receiver
        key = normalize_link(a, b)

        if key in LINK_SECONDARY_PARAMS:
            params = LINK_SECONDARY_PARAMS[key]
            distance_m = qc.distance

            # Effective attenuation = nominal fiber attenuation + discontinuity loss spread across the link
            alpha_eff_db_per_km = effective_attenuation_db_per_km(
                params["base_alpha_db_per_km"],
                params["extra_loss_db"],
                distance_m
            )

            # SeQUeNCe expects attenuation in dB/m
            qc.attenuation = alpha_eff_db_per_km / 1000.0

            # Quantum channel usage rate
            qc.frequency = 5e6

            # Secondary effects mapped into a fidelity penalty
            pen_disp = dispersion_penalty(distance_m, params["dispersion_ps_per_nm_km"])
            pen_pmd = pmd_noise(distance_m, params["pmd_ps_sqrt_km"])
            total_penalty = pen_disp + pen_pmd

            node_penalties[a].append(total_penalty)
            node_penalties[b].append(total_penalty)

    # Apply node memory parameters
    for node in topology.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER):
        hw = NODE_HW[node.name]
        memory_array = node.get_components_by_type("MemoryArray")[0]

        # conservative node penalty = average penalty of attached links
        if node_penalties[node.name]:
            avg_link_penalty = sum(node_penalties[node.name]) / len(node_penalties[node.name])
        else:
            avg_link_penalty = 0.0

        effective_raw_fidelity = max(0.75, min(hw["base_fidelity"] - avg_link_penalty, 0.99))

        memory_array.update_memory_params("frequency", hw["memo_freq"])
        memory_array.update_memory_params("coherence_time", hw["memo_expire"])
        memory_array.update_memory_params("efficiency", hw["memo_eff"])
        memory_array.update_memory_params("raw_fidelity", effective_raw_fidelity)

        print(
            f"[SETUP] Node {node.name}: "
            f"freq={hw['memo_freq']}, "
            f"coherence={hw['memo_expire']}, "
            f"eff={hw['memo_eff']}, "
            f"raw_fidelity={effective_raw_fidelity:.4f}"
        )


    # Detector parameters (maybe node-specific?)
  
    DETECTOR_EFFICIENCY = 0.55
    DETECTOR_COUNT_RATE = 2e7
    DETECTOR_RESOLUTION = 50

    for node in topology.get_nodes_by_type(RouterNetTopo.BSM_NODE):
        bsm = node.get_components_by_type("SingleAtomBSM")[0]
        bsm.update_detectors_params("efficiency", DETECTOR_EFFICIENCY)
        bsm.update_detectors_params("count_rate", DETECTOR_COUNT_RATE)
        bsm.update_detectors_params("time_resolution", DETECTOR_RESOLUTION)

    # Print effective link setup
    for qc in topology.get_qchannels():
        print(
            f"[SETUP] QLink {qc.sender} <-> {qc.receiver}: "
            f"distance={qc.distance} m, attenuation={qc.attenuation:.8f} dB/m, freq={qc.frequency}"
        )

def main():
    network_config = r"C:\Users\thoma\OneDrive\Desktop\Double degree\Internship\Internsheep\Simulator\SeQUeNCe\simulator\ideal_topo_star.json"
    network_topo = RouterNetTopo(network_config)
    tl = network_topo.get_timeline()

    set_parameters(network_topo)

    routers = {
        node.name: node
        for node in network_topo.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER)
    }

    TRAFFIC_MATRIX = {
        "valrose": {
            "sophia":  {"interval_s": 1.0, "memory_size": 5, "target_fidelity": 0.75},
            "antibes": {"interval_s": 1.4, "memory_size": 4, "target_fidelity": 0.75},
            "grasse":  {"interval_s": 1.8, "memory_size": 3, "target_fidelity": 0.75},
        },
        "sophia": {
            "valrose": {"interval_s": 1.1, "memory_size": 5, "target_fidelity": 0.75},
            "antibes": {"interval_s": 1.2, "memory_size": 5, "target_fidelity": 0.75},
            "grasse":  {"interval_s": 1.6, "memory_size": 4, "target_fidelity": 0.75},
        },
        "antibes": {
            "valrose": {"interval_s": 1.5, "memory_size": 4, "target_fidelity": 0.75},
            "sophia":  {"interval_s": 1.0, "memory_size": 5, "target_fidelity": 0.75},
            "grasse":  {"interval_s": 1.3, "memory_size": 5, "target_fidelity": 0.75},
        },
        "grasse": {
            "valrose": {"interval_s": 1.0, "memory_size": 5, "target_fidelity": 0.75},
            "sophia":  {"interval_s": 1.7, "memory_size": 4, "target_fidelity": 0.75},
            "antibes": {"interval_s": 1.4, "memory_size": 4, "target_fidelity": 0.75},
        }
    }

    apps = []
    peripheral_nodes = ["valrose", "sophia", "antibes", "grasse"]

    for i, node_name in enumerate(peripheral_nodes):
        app = NodeTrafficApp(
            node=routers[node_name],
            traffic_demands=TRAFFIC_MATRIX[node_name],
            start_offset_s=0.2 * i,
            max_requests_per_flow=4
        )
        app.schedule_initial_events()
        apps.append(app)

    tl.init()
    tl.run()

    compute_node_flow_statistics(apps)

    for app in apps:
        print(f"\n=== FINAL HISTORY FOR NODE {app.node.name} ===")
        for item in app.history:
            print(item)

if __name__ == "__main__":
    main()