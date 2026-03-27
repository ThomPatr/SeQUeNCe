from sequence.topology.router_net_topo import RouterNetTopo
from sequence.kernel.process import Process
from sequence.kernel.event import Event
import math
import random
from typing import TYPE_CHECKING
from collections import defaultdict
from types import MethodType
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

        # free memory after counting
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

def ps_to_s(x_ps):
    return x_ps * 1e-12

def normalize_link(a, b):
    a_name = endpoint_name(a)
    b_name = endpoint_name(b)
    return tuple(sorted((a_name, b_name)))


def logical_link_from_qchannel(qc):
    """
    Map a physical qchannel involving a BSM node back to the logical router-router link.
    Example:
      valrose <-> BSM.valrose.sophia   -> ('sophia', 'valrose')
      sophia  <-> BSM.valrose.sophia   -> ('sophia', 'valrose')
    """
    a = endpoint_name(qc.sender)
    b = endpoint_name(qc.receiver)

    if isinstance(a, str) and a.startswith("BSM."):
        parts = a.split(".")
        if len(parts) >= 3:
            return normalize_link(parts[1], parts[2])

    if isinstance(b, str) and b.startswith("BSM."):
        parts = b.split(".")
        if len(parts) >= 3:
            return normalize_link(parts[1], parts[2])

    return normalize_link(a, b)


def effective_attenuation_db_per_km(base_alpha_db_per_km: float,
                                    extra_loss_db: float,
                                    distance_m: float) -> float:
    """
    Convert fixed connector/splice loss into an equivalent distributed attenuation.
    alpha_eff [dB/km] = alpha_base [dB/km] + extra_loss_db / distance_km
    """
    distance_km = max(distance_m / 1000.0, 1e-9)
    return base_alpha_db_per_km + extra_loss_db / distance_km


def set_parameters(topology: RouterNetTopo):
    # -------------------------------------------------
    # 1) Intrinsic node hardware parameters
    # raw_fidelity stays a property of the memory hardware
    # -------------------------------------------------
    NODE_HW = {
        "valrose": {"memo_freq": 8e3, "memo_expire": 0.05,  "memo_eff": 0.45, "base_fidelity": 0.90},
        "ecov":    {"memo_freq": 8e3, "memo_expire": 0.06,  "memo_eff": 0.47, "base_fidelity": 0.91},
        "sophia":  {"memo_freq": 8e3, "memo_expire": 0.05,  "memo_eff": 0.46, "base_fidelity": 0.90},
        "antibes": {"memo_freq": 8e3, "memo_expire": 0.045, "memo_eff": 0.44, "base_fidelity": 0.89},
        "grasse":  {"memo_freq": 8e3, "memo_expire": 0.04,  "memo_eff": 0.42, "base_fidelity": 0.88},
    }

    # Apply memory hardware parameters
    for node in topology.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER):
        hw = NODE_HW[node.name]
        memory_array = node.get_components_by_type("MemoryArray")[0]

        memory_array.update_memory_params("frequency", hw["memo_freq"])
        memory_array.update_memory_params("coherence_time", hw["memo_expire"])
        memory_array.update_memory_params("efficiency", hw["memo_eff"])
        memory_array.update_memory_params("raw_fidelity", hw["base_fidelity"])

        print(
            f"[SETUP] Node {node.name}: "
            f"freq={hw['memo_freq']}, "
            f"coherence={hw['memo_expire']}, "
            f"eff={hw['memo_eff']}, "
            f"raw_fidelity={hw['base_fidelity']:.4f}"
        )

    # -------------------------------------------------
    # 2) Detector / BSM hardware parameters
    # -------------------------------------------------
    DETECTOR_EFFICIENCY = 0.55
    DETECTOR_COUNT_RATE = 2e7
    DETECTOR_RESOLUTION = 50  # ps

    for node in topology.get_nodes_by_type(RouterNetTopo.BSM_NODE):
        bsm = node.get_components_by_type("SingleAtomBSM")[0]
        bsm.update_detectors_params("efficiency", DETECTOR_EFFICIENCY)
        bsm.update_detectors_params("count_rate", DETECTOR_COUNT_RATE)
        bsm.update_detectors_params("time_resolution", DETECTOR_RESOLUTION)

    # -------------------------------------------------
    # 3) Link-level physical parameters
    # Fiber baseline at 1550 nm + extra losses
    # -------------------------------------------------
    # base_alpha_db_per_km = nominal fiber attenuation
    # extra_loss_db = lumped connector/splice/imperfection loss
    LINK_PHYSICS = {
        normalize_link("valrose", "sophia"): {
            "base_alpha_db_per_km": 0.18,
            "extra_loss_db": 0.10,
        },
        normalize_link("sophia", "ecov"): {
            "base_alpha_db_per_km": 0.22,
            "extra_loss_db": 0.10,
        },
        normalize_link("sophia", "antibes"): {
            "base_alpha_db_per_km": 0.20,
            "extra_loss_db": 0.03,
        },
        normalize_link("sophia", "grasse"): {
            "base_alpha_db_per_km": 0.28,
            "extra_loss_db": 0.08,
        },
    }

    
    QC_FREQ = 5e6

    # Apply effective attenuation to each physical qchannel segment
    for qc in topology.get_qchannels():
        logical_key = logical_link_from_qchannel(qc)

        if logical_key in LINK_PHYSICS:
            params = LINK_PHYSICS[logical_key]
            distance_m = qc.distance

            alpha_eff_db_per_km = effective_attenuation_db_per_km(
                params["base_alpha_db_per_km"],
                params["extra_loss_db"],
                distance_m
            )

            # SeQUeNCe expects attenuation in dB/m
            qc.attenuation = alpha_eff_db_per_km / 1000.0
            qc.frequency = QC_FREQ

            print(
                f"[SETUP] QLink {endpoint_name(qc.sender)} <-> {endpoint_name(qc.receiver)} "
                f"(logical {logical_key}): "
                f"distance={distance_m} m, "
                f"alpha_eff={alpha_eff_db_per_km:.4f} dB/km, "
                f"attenuation={qc.attenuation:.8f} dB/m, "
                f"freq={qc.frequency}"
            )
        else:
            # fallback only if a link is not explicitly listed
            qc.frequency = QC_FREQ
            print(
                f"[SETUP] QLink {endpoint_name(qc.sender)} <-> {endpoint_name(qc.receiver)} "
                f"(logical {logical_key}): using existing attenuation={qc.attenuation}"
            )
# ============================================================
# GLOBAL LINK METRICS
# ============================================================

LINK_METRICS = defaultdict(lambda: {
    "eg_attempts": 0,          # physical EG attempts
    "eg_successes": 0,         # observed successful elementary entanglement creations
    "pair_records": []         # completed local canonical pair lifecycles
})

# Active canonical pair records
# key = (canonical_local_node, memory_name)
ACTIVE_PAIRS = {}


def canonical_side(node_a: str, node_b: str) -> str:
    return min(node_a, node_b)


def is_canonical_observer(local_node: str, remote_node: str) -> bool:
    return local_node == canonical_side(local_node, remote_node)


def register_entanglement_attempt(src: str, dst: str, attempts: int = 1):
    key = normalize_link(src, dst)
    LINK_METRICS[key]["eg_attempts"] += attempts


def register_pair_creation(local_node: str, memory, remote_node: str, fidelity: float, entangle_time_ps: int):
    """
    Register a successful elementary entanglement creation.
    Count it only on the canonical side of the logical link to avoid double counting.
    """
    if remote_node is None or fidelity <= 0:
        return

    if not is_canonical_observer(local_node, remote_node):
        return

    link_key = normalize_link(local_node, remote_node)
    pair_key = (local_node, memory.name)

    if pair_key in ACTIVE_PAIRS:
        # already tracking this memory lifecycle
        return

    record = {
        "link": link_key,
        "local_node": local_node,
        "remote_node": remote_node,
        "memory_name": memory.name,
        "creation_time_ps": entangle_time_ps,
        "discard_time_ps": None,
        "observed_lifetime_ps": None,
        "fidelity_at_creation": fidelity,
        "fidelity_at_discard": None,
        "discard_reason": None,
    }

    ACTIVE_PAIRS[pair_key] = record
    LINK_METRICS[link_key]["eg_successes"] += 1


def register_pair_discard(local_node: str, memory, discard_time_ps: int,
                          fidelity_before_reset: float, reason: str = "reset_to_RAW"):
    """
    Register the end of a previously created canonical elementary pair lifecycle.
    """
    pair_key = (local_node, memory.name)

    if pair_key not in ACTIVE_PAIRS:
        return

    record = ACTIVE_PAIRS.pop(pair_key)
    record["discard_time_ps"] = discard_time_ps
    record["observed_lifetime_ps"] = discard_time_ps - record["creation_time_ps"]
    record["fidelity_at_discard"] = fidelity_before_reset
    record["discard_reason"] = reason

    LINK_METRICS[record["link"]]["pair_records"].append(record)


def compute_link_physics_statistics():
    print("\n================ LINK PHYSICS STATISTICS ================\n")

    for link in sorted(LINK_METRICS.keys()):
        stats = LINK_METRICS[link]

        attempts = stats["eg_attempts"]
        successes = stats["eg_successes"]
        records = stats["pair_records"]

        p_gen = (successes / attempts) if attempts > 0 else None

        lifetimes = [r["observed_lifetime_ps"] for r in records if r["observed_lifetime_ps"] is not None]
        avg_lifetime_s = (sum(lifetimes) / len(lifetimes) * 1e-12) if lifetimes else None

        f_create = [r["fidelity_at_creation"] for r in records if r["fidelity_at_creation"] is not None]
        f_discard = [r["fidelity_at_discard"] for r in records if r["fidelity_at_discard"] is not None]

        avg_f_create = (sum(f_create) / len(f_create)) if f_create else None
        avg_f_discard = (sum(f_discard) / len(f_discard)) if f_discard else None

        fid_drops = []
        for r in records:
            if r["fidelity_at_creation"] is not None and r["fidelity_at_discard"] is not None:
                fid_drops.append(r["fidelity_at_creation"] - r["fidelity_at_discard"])

        avg_fid_drop = (sum(fid_drops) / len(fid_drops)) if fid_drops else None

        print(f"Link: {link[0]} <-> {link[1]}")
        print(f"  eg_attempts             : {attempts}")
        print(f"  eg_successes            : {successes}")
        print(f"  p_gen                   : {p_gen:.6f}" if p_gen is not None else "  p_gen                   : None")
        print(f"  completed pair records  : {len(records)}")
        print(f"  avg lifetime            : {avg_lifetime_s:.6f} s" if avg_lifetime_s is not None else "  avg lifetime            : None")
        print(f"  avg fidelity creation   : {avg_f_create:.6f}" if avg_f_create is not None else "  avg fidelity creation   : None")
        print(f"  avg fidelity discard    : {avg_f_discard:.6f}" if avg_f_discard is not None else "  avg fidelity discard    : None")
        print(f"  avg fidelity drop       : {avg_fid_drop:.6f}" if avg_fid_drop is not None else "  avg fidelity drop       : None")
        print("---------------------------------------------------------")

def extract_remote_node_from_protocol(protocol):
    """
    Try to infer the remote node name from a SeQUeNCe entanglement generation protocol.
    This is intentionally defensive because different versions may expose different fields.
    """
    for attr in ["remote_node_name", "remote_node", "other", "other_name"]:
        if hasattr(protocol, attr):
            value = getattr(protocol, attr)
            if isinstance(value, str):
                return value
            if hasattr(value, "name"):
                return value.name
    return None


def looks_like_generation_protocol(protocol) -> bool:
    cls_name = protocol.__class__.__name__.lower()
    mod_name = protocol.__class__.__module__.lower()

    return (
        "generation" in cls_name
        or "entanglementgeneration" in cls_name
        or "generation" in mod_name
    )


def patch_generation_protocol(protocol):
    """
    Monkey-patch protocol.start() to count real physical EG attempts.
    """
    if getattr(protocol, "_eg_metrics_patched", False):
        return

    if not hasattr(protocol, "start"):
        return

    original_start = protocol.start

    def wrapped_start(*args, **kwargs):
        owner_name = endpoint_name(protocol.owner)
        remote_name = extract_remote_node_from_protocol(protocol)

        if remote_name is not None:
            # count only on canonical side to avoid double counting mirrored protocol starts
            if is_canonical_observer(owner_name, remote_name):
                register_entanglement_attempt(owner_name, remote_name, attempts=1)

        return original_start(*args, **kwargs)

    protocol.start = MethodType(wrapped_start, protocol) if not hasattr(original_start, "__self__") else wrapped_start
    protocol._eg_metrics_patched = True


def instrument_generation_protocols(topology: RouterNetTopo):
    """
    Walk all nodes and patch entanglement generation protocols to count physical attempts.
    Call this after tl.init() and before tl.run().
    """
    all_nodes = []
    all_nodes.extend(topology.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER))
    all_nodes.extend(topology.get_nodes_by_type(RouterNetTopo.BSM_NODE))

    patched = 0
    for node in all_nodes:
        if not hasattr(node, "protocols"):
            continue
        for protocol in node.protocols:
            if looks_like_generation_protocol(protocol):
                patch_generation_protocol(protocol)
                patched += 1

    print(f"[SETUP] Patched {patched} entanglement-generation protocols for EG attempt counting.")

def patch_resource_manager_update(node):
    """
    Patch node.resource_manager.update so that we can observe:
    - creation of elementary entanglement
    - discard/reset to RAW
    This works for all routers, including transit nodes.
    """
    rm = node.resource_manager

    if getattr(rm, "_metrics_patched", False):
        return

    original_update = rm.update

    def wrapped_update(protocol, memory, state, *args, **kwargs):
        now_ps = node.timeline.now()

        # Snapshot BEFORE update
        old_remote_node = getattr(memory, "remote_node", None)
        old_fidelity = getattr(memory, "fidelity", 0)
        old_entangle_time = getattr(memory, "entangle_time", None)
        old_name = getattr(memory, "name", None)

        # Call original update
        result = original_update(protocol, memory, state, *args, **kwargs)

        # Snapshot AFTER update
        new_remote_node = getattr(memory, "remote_node", None)
        new_fidelity = getattr(memory, "fidelity", 0)
        new_entangle_time = getattr(memory, "entangle_time", None)

        # 1) Creation: memory becomes useful and is associated with a remote node
        if state != "RAW":
            if new_remote_node is not None and new_fidelity > 0:
                ent_time = new_entangle_time if new_entangle_time is not None and new_entangle_time >= 0 else now_ps
                register_pair_creation(
                    local_node=node.name,
                    memory=memory,
                    remote_node=new_remote_node,
                    fidelity=new_fidelity,
                    entangle_time_ps=ent_time
                )

        # 2) Discard/reset: memory goes to RAW
        if state == "RAW":
            if old_remote_node is not None:
                register_pair_discard(
                    local_node=node.name,
                    memory=memory,
                    discard_time_ps=now_ps,
                    fidelity_before_reset=old_fidelity,
                    reason="reset_to_RAW"
                )

        return result

    rm.update = wrapped_update
    rm._metrics_patched = True


def instrument_resource_managers(topology: RouterNetTopo):
    routers = topology.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER)
    for node in routers:
        patch_resource_manager_update(node)
    print(f"[SETUP] Patched {len(routers)} resource managers for pair lifecycle tracking.")

def main():
    network_config = r"C:\Users\thoma\OneDrive\Desktop\Double degree\Internship\Internsheep\Simulator\SeQUeNCe\simulator\topo_star.json"
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
    instrument_resource_managers(network_topo)
    instrument_generation_protocols(network_topo)
    tl.run()

    compute_node_flow_statistics(apps)
    compute_link_physics_statistics()
    for app in apps:
        print(f"\n=== FINAL HISTORY FOR NODE {app.node.name} ===")
        for item in app.history:
            print(item)

if __name__ == "__main__":
    main()