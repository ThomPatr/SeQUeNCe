from sequence.topology.router_net_topo import RouterNetTopo
from sequence.components.memory import MemoryArray
from sequence.topology import node as topology_node

from simulator.first_RL.config import NETWORK_CONFIG, TRAFFIC_MATRIX, PERIPHERAL_NODES
from simulator.first_RL.apps.node_traffic_app import NodeTrafficApp
from simulator.first_RL.physics.parameters import set_parameters
from simulator.first_RL.instrumentation.tracking import patch_generation_classes, instrument_resource_managers
from simulator.first_RL.reporting.statistics import compute_node_flow_statistics, compute_link_physics_statistics
from simulator.first_RL.topology.custom_memory_array import CustomMemoryArray
from simulator.first_RL.RL.dqn_swap_controller import DQNSwapController
topology_node.MemoryArray = CustomMemoryArray
def main():
    # Patch the MemoryArray class used in the topology with our custom version
    network_topo = RouterNetTopo(str(NETWORK_CONFIG))
    tl = network_topo.get_timeline()
    print(f"[DEBUG] config file = {NETWORK_CONFIG}")
    print(f"[DEBUG] timeline stop_time (ps) = {tl.stop_time}")
    print(f"[DEBUG] timeline stop_time (s) = {tl.stop_time * 1e-12}")
    set_parameters(network_topo)
    swap_controller = DQNSwapController(
    model_path="simulator/first_RL/RL/dqn_wait_swap",
    target_fidelity=0.75
)

    for node in network_topo.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER):
        node.rl_swap_controller = swap_controller
    for node in network_topo.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER):
        memory_array = node.get_components_by_type(MemoryArray)[0]
        print(f"{node.name}: {type(memory_array[0])}")

    routers = {
        node.name: node
        for node in network_topo.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER)
    }

    apps = []
    for i, node_name in enumerate(TRAFFIC_MATRIX.keys()):
        app = NodeTrafficApp(
            node=routers[node_name],
            traffic_demands=TRAFFIC_MATRIX[node_name],
            start_offset_s=0.5 * i,
            parallel_sessions_per_flow=2,
            parallel_stagger_s=0.2,
            reservation_duration_s=5.0,
            reservation_setup_margin_s=1.0,
            retry_delay_s=1.0,
        )
        app.schedule_initial_events()
        apps.append(app)

    # instrumentation first
    instrument_resource_managers(network_topo)
    patch_generation_classes()

    # then initialize protocols
    tl.init()

    # run simulation
    tl.run()

    compute_node_flow_statistics(apps)
    compute_link_physics_statistics()

    for app in apps:
        print(f"\n=== FINAL HISTORY FOR NODE {app.node.name} ===")
        for item in app.history:
            print(item)


if __name__ == "__main__":
    main()