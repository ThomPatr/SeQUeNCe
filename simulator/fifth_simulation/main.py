from sequence.topology.router_net_topo import RouterNetTopo

from config import NETWORK_CONFIG, TRAFFIC_MATRIX, PERIPHERAL_NODES
from apps.node_traffic_app import NodeTrafficApp
from physics.parameters import set_parameters
from instrumentation.tracking import patch_generation_classes, instrument_resource_managers
from reporting.statistics import compute_node_flow_statistics, compute_link_physics_statistics
from sequence.components import memory
from topology.custom_memory_array import CustomMemoryArray

def main():
    memory.MemoryArray = CustomMemoryArray  # Patch the MemoryArray class used in the topology with our custom version
    network_topo = RouterNetTopo(str(NETWORK_CONFIG))
    tl = network_topo.get_timeline()

    set_parameters(network_topo, use_random_coherence=True)
    for node in network_topo.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER):
        memory_array = node.get_components_by_type("MemoryArray")[0]
        print(f"{node.name}: {type(memory_array[0])}")

    routers = {
        node.name: node
        for node in network_topo.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER)
    }

    apps = []
    for i, node_name in enumerate(PERIPHERAL_NODES):
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
    patch_generation_classes()
    tl.run()

    compute_node_flow_statistics(apps)
    compute_link_physics_statistics()

    for app in apps:
        print(f"\n=== FINAL HISTORY FOR NODE {app.node.name} ===")
        for item in app.history:
            print(item)


if __name__ == "__main__":
    main()