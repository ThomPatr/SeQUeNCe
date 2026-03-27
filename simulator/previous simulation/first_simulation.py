from sequence.topology.router_net_topo import RouterNetTopo
from sequence.kernel.process import Process
from sequence.kernel.event import Event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sequence.network_management.reservation import Reservation
    from sequence.resource_management.memory_manager import MemoryInfo

class PeriodicApp:
    def __init__(self, node, other: str, memory_size=10, target_fidelity=0.9):
        self.node = node # The quantum router this application is associated with
        self.node.set_app(self) # Register this application with the node so it can receive callbacks
        self.other = other # The name of the other node we want to establish entanglement with
        self.memory_size = memory_size
        self.target_fidelity = target_fidelity # The minimum fidelity we require for the entangled pairs
        self.request_id = 0
        self.delivered_pairs = 0
    def start(self):
        now = self.node.timeline.now() # Current simulation time in picoseconds (1e-12 seconds)
        nm = self.node.network_manager # The Network Manager component of the node, responsible for handling requests and managing resources
        self.request_id += 1
        self.delivered_pairs = 0

        print(f"\n===== REQUEST {self.request_id} START =====")
        # request a 1-second reservation window starting 1 second in the future
        nm.request(
            self.other,
            start_time=now + int(1e12),
            end_time=now + int(3e12),
            memory_size=self.memory_size, #fo each time window, we want to reserve 5 memories for entanglement generation 
            target_fidelity=self.target_fidelity
        )

        # schedule the next request 2 seconds later
        process = Process(self, "start", [])
        event = Event(now + int(2e12), process)
        self.node.timeline.schedule(event)
    """
    The previous method follow this schema: 
    time →

            0s        1s        2s        3s        4s
            |---------|---------|---------|---------|

            setup     request1
                        window

                                request2
                                window

                                         request3
                                         window
    """

    def get_reservation_result(self, reservation: "Reservation", result: bool):
        now_s = self.node.timeline.now() * 1e-12
        if result:
            print(f"[{now_s:.6f}s] Reservation approved on {self.node.name}")
        else:
            print(f"[{now_s:.6f}s] Reservation failed on {self.node.name}") #reservation fail if we cannot have 5 entanglement at the end of the reservation window, for example if the BSM node cannot produce enough pairs or if the fidelity is too low

    def get_memory(self, info):
        if info.state == "ENTANGLED" and info.remote_node == self.other:
            now_s = self.node.timeline.now() * 1e-12

            self.delivered_pairs += 1

            print(
                f"[{now_s:.6f}s] Request {self.request_id} -> pair {self.delivered_pairs} "
                f"memory={info.index} fidelity={info.fidelity}"
            )

            self.node.resource_manager.update(None, info.memory, "RAW")


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
    print(f"\n=== {router.name} memories ===")
    print("Index\tState\t\tRemote Node\tFidelity\tEntangle Time (s)")
    for i, info in enumerate(router.resource_manager.memory_manager):
        ent_time_s = (
            info.entangle_time * 1e-12
            if info.entangle_time is not None and info.entangle_time >= 0
            else None
        )
        print(
            f"{i}\t{info.state}\t\t{info.remote_node}\t\t" # print the table of the memories status at the end of the simulation, with the index, state, remote node, fidelity and entangle time in seconds (if the memory is entangled)
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