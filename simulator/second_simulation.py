from sequence.topology.router_net_topo import RouterNetTopo
from sequence.kernel.process import Process
from sequence.kernel.event import Event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sequence.network_management.reservation import Reservation
    from sequence.resource_management.memory_manager import MemoryInfo

class PeriodicApp:
    def __init__(self, node, other: str, memory_size=5, target_fidelity=0.9):
        self.node = node
        self.node.set_app(self)

        self.other = other
        self.memory_size = memory_size
        self.target_fidelity = target_fidelity

        # request tracking
        self.request_id = 0
        self.active_requests = {}   # req_id -> dict with metadata
        self.history = []           # list of completed requests

    def start(self):
        now = self.node.timeline.now()

        # create a new logical request id
        self.request_id += 1
        req_id = self.request_id

        start_time = now + int(1e12)
        end_time = now + int(2e12)

        self.active_requests[req_id] = {
            "request_id": req_id,
            "created_at_ps": now,
            "window_start_ps": start_time,
            "window_end_ps": end_time,
            "approved": None,
            "delivered_pairs": 0,
            "fidelities": [],
            "delivery_times_ps": []
        }

        print(
            f"\n===== REQUEST {req_id} CREATED at {now * 1e-12:.6f}s "
            f"(window: {start_time * 1e-12:.6f}s → {end_time * 1e-12:.6f}s) ====="
        )

        nm = self.node.network_manager
        nm.request(
            self.other,
            start_time=start_time,
            end_time=end_time,
            memory_size=self.memory_size,
            target_fidelity=self.target_fidelity
        )

        # schedule summary of this request exactly at the end of the reservation window
        summary_process = Process(self, "finalize_request", [req_id])
        summary_event = Event(end_time, summary_process)
        self.node.timeline.schedule(summary_event)

        # schedule next request 2 seconds later
        next_process = Process(self, "start", [])
        next_event = Event(now + int(2e12), next_process)
        self.node.timeline.schedule(next_event)

    def get_reservation_result(self, _reservation, result: bool):
        now_ps = self.node.timeline.now()
        now_s = now_ps * 1e-12

        # associate the reservation result with the latest request that has approved=None
        pending_ids = [rid for rid, data in self.active_requests.items() if data["approved"] is None]
        if not pending_ids:
            print(f"[{now_s:.6f}s] Reservation result received, but no pending request found.")
            return

        req_id = min(pending_ids)
        self.active_requests[req_id]["approved"] = result

        if result:
            print(f"[{now_s:.6f}s] Reservation APPROVED for request {req_id}")
        else:
            print(f"[{now_s:.6f}s] Reservation FAILED for request {req_id}")

    def get_memory(self, info):
        """
        Called by the Resource Manager when a memory state is updated.
        We attribute the delivered pair to the currently active request window.
        """
        if info.state == "ENTANGLED" and info.remote_node == self.other:
            now_ps = self.node.timeline.now()
            now_s = now_ps * 1e-12

            # find the request whose reservation window currently contains now_ps
            candidate_ids = []
            for rid, data in self.active_requests.items():
                if data["window_start_ps"] <= now_ps <= data["window_end_ps"]:
                    candidate_ids.append(rid)

            if not candidate_ids:
                print(
                    f"[{now_s:.6f}s] ENTANGLED memory received but no active request window found. "
                    f"memory={info.index}, remote={info.remote_node}, fidelity={info.fidelity}"
                )
            else:
                req_id = min(candidate_ids)
                req_data = self.active_requests[req_id]
                req_data["delivered_pairs"] += 1
                req_data["fidelities"].append(info.fidelity)
                req_data["delivery_times_ps"].append(now_ps)

                print(
                    f"[{now_s:.6f}s] Request {req_id} -> delivered pair "
                    f"{req_data['delivered_pairs']}/{self.memory_size} "
                    f"(memory={info.index}, fidelity={info.fidelity:.6f})"
                )

            # release the memory so it can be reused
            self.node.resource_manager.update(None, info.memory, "RAW")

    def finalize_request(self, req_id: int):
        """
        Called at the end of the reservation window to print a summary.
        """
        if req_id not in self.active_requests:
            return

        data = self.active_requests.pop(req_id)
        approved = data["approved"]
        delivered = data["delivered_pairs"]

        if data["fidelities"]:
            avg_fidelity = sum(data["fidelities"]) / len(data["fidelities"])
        else:
            avg_fidelity = 0.0

        # latency relative to window start
        if data["delivery_times_ps"]:
            avg_latency_ps = sum(t - data["window_start_ps"] for t in data["delivery_times_ps"]) / len(data["delivery_times_ps"])
            avg_latency_s = avg_latency_ps * 1e-12
        else:
            avg_latency_s = None

        summary = {
            "request_id": req_id,
            "approved": approved,
            "delivered_pairs": delivered,
            "requested_pairs": self.memory_size,
            "avg_fidelity": avg_fidelity,
            "avg_latency_s": avg_latency_s,
            "window_start_s": data["window_start_ps"] * 1e-12,
            "window_end_s": data["window_end_ps"] * 1e-12
        }

        self.history.append(summary)

        print(f"----- REQUEST {req_id} SUMMARY -----")
        print(f"approved       : {approved}")
        print(f"delivered      : {delivered}/{self.memory_size}")
        print(f"avg fidelity   : {avg_fidelity:.6f}")
        if avg_latency_s is not None:
            print(f"avg latency    : {avg_latency_s:.6f} s")
        else:
            print("avg latency    : None")
        print("-----------------------------------")

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

def set_parameters(topology: RouterNetTopo):
    # ---------- memory parameters ----------
    MEMO_FREQ = 20e3 # The frequency at which the quantum memories can attempt to generate entanglement (20 kHz)
    MEMO_EXPIRE = 1 #For now, the dechoerence is set to 0 so the entanglement doesn't expire. Next step is to give more complexity!!
    MEMO_EFFICIENCY = 0.6 # The probability that an attempt to generate entanglement succeeds (100% efficiency for this first experiment, next step will provides more realistic parameters)
    MEMO_FIDELITY = 0.9349367588934053 #initial fidelity

    for node in topology.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER): #setting parameters for all the quantum routers in the topology
        memory_array = node.get_components_by_type("MemoryArray")[0]
        memory_array.update_memory_params("frequency", MEMO_FREQ)
        memory_array.update_memory_params("coherence_time", MEMO_EXPIRE)
        memory_array.update_memory_params("efficiency", MEMO_EFFICIENCY)
        memory_array.update_memory_params("raw_fidelity", MEMO_FIDELITY)

    # ---------- detector parameters ----------
    DETECTOR_EFFICIENCY = 0.7 # The probability that a photon arriving at the detector is successfully detected (90% efficiency for this first experiment); P(detected|photon arrives) = 0.9
    #Remember that the generation require two photons to be detected, so the overall success probability of generating an entangled pair is the product of the efficiencies of the two detectors: P(success) = P(detected|photon arrives)^2 = 0.9^2 = 0.81 (81% success probability for generating an entangled pair in this first experiment)
    DETECTOR_COUNT_RATE = 5e7 # The maximum rate at which the detector can register photons (50 million counts per second for this first experiment); 
    DETECTOR_RESOLUTION = 10 # temporal resolution of the detctor in picoseconds (100 ps for this first experiment)

    for node in topology.get_nodes_by_type(RouterNetTopo.BSM_NODE):
        bsm = node.get_components_by_type("SingleAtomBSM")[0]
        bsm.update_detectors_params("efficiency", DETECTOR_EFFICIENCY)
        bsm.update_detectors_params("count_rate", DETECTOR_COUNT_RATE)
        bsm.update_detectors_params("time_resolution", DETECTOR_RESOLUTION)

    # ---------- quantum channel parameters ----------
    ATTENUATION = 0.00018  # the absorbtion probability per kilometer of the quantum channel (for this first experiment, we set it to a very low value to ensure that we can generate enough entanglement)
    QC_FREQ = 1e7 # maximum frequency at which the quantum channel can be used to attempt to generate entanglement (10^11 events/s)

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
    network_config = r"C:\Users\thoma\OneDrive\Desktop\Double degree\Internship\Internsheep\Simulator\SeQUeNCe\simulator\topo.json"
    network_topo = RouterNetTopo(network_config)
    tl = network_topo.get_timeline()

    set_parameters(network_topo)

    # get the routers
    routers = {
        node.name: node
        for node in network_topo.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER)
    }

    valrose = routers["valrose"]
    ecov = routers["ecov"]
    sophia = routers["sophia"]

    # application on valrose requesting end-to-end entanglement with sophia
    app = PeriodicApp(valrose, "sophia", memory_size=25, target_fidelity=0.9)

    # start the first application event at time 0
    first_process = Process(app, "start", [])
    first_event = Event(0, first_process)
    tl.schedule(first_event)

    tl.init()
    tl.run()

    # print final state of the memories
    print_memory_status(valrose)
    print_memory_status(ecov)
    print_memory_status(sophia)


if __name__ == "__main__":
    main()