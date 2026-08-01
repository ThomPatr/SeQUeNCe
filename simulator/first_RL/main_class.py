from __future__ import annotations

from pathlib import Path

from sequence.components.memory import MemoryArray
from sequence.topology import node as topology_node
from sequence.topology.router_net_topo import RouterNetTopo
from simulator.first_RL.apps.node_traffic_app import NodeTrafficApp
from simulator.first_RL.apps.traffic_utils import (compute_configured_offered_traffic,scale_poisson_traffic,)
from simulator.first_RL.config import (NETWORK_CONFIG,TRAFFIC_MATRIX,)
from simulator.first_RL.instrumentation.tracking import ( instrument_resource_managers, patch_generation_classes,)
from simulator.first_RL.metrics.link_metrics import (reset_link_metrics,)
from simulator.first_RL.metrics.protocol_metrics import (reset_protocol_metrics,initialize_flow_protocol_metrics,)
from simulator.first_RL.physics.parameters import ( set_parameters,)
from simulator.first_RL.reporting.statistics import ( compute_link_physics_statistics, compute_node_flow_statistics,)
from simulator.first_RL.topology.custom_memory_array import (CustomMemoryArray,)
from simulator.first_RL.reporting.result_exporter import (export_classical_sequence_results,)

topology_node.MemoryArray = CustomMemoryArray


DEFAULT_LOAD_FACTOR = 5.0
DEFAULT_TRAFFIC_SEED = 2000
DEFAULT_SIMULATION_DURATION_S = 30.0
DEFAULT_ENABLE_PURIFICATION = False

def run_classical_sequence_experiment( rho: float = DEFAULT_LOAD_FACTOR, base_traffic_seed: int = DEFAULT_TRAFFIC_SEED, simulation_duration_s: float =  DEFAULT_SIMULATION_DURATION_S, enable_purification: bool =DEFAULT_ENABLE_PURIFICATION,verbose: bool = False,):
    """
    Run one SeQUeNCe experiment without Reinforcement Learning.

    The experiment is uniquely determined by:

        - load factor rho;
        - base traffic seed;
        - hardware and topology configuration.

    Each source application receives a deterministic but different
    random seed derived from base_traffic_seed.
    """
    if rho <= 0:
        raise ValueError("rho must be strictly positive." )

    if simulation_duration_s <= 0:
        raise ValueError("simulation_duration_s must be positive." )

    reset_link_metrics()
    reset_protocol_metrics()
    # ============================================================
    # 1. Scale the Poisson traffic matrix
    # ============================================================

    simulation_traffic = scale_poisson_traffic(traffic_matrix=TRAFFIC_MATRIX,rho=rho, )
    initialize_flow_protocol_metrics(simulation_traffic)
    configured_offered_traffic = ( compute_configured_offered_traffic(  simulation_traffic ))

    print( "\n" "================ EXPERIMENT CONFIGURATION ================\n")

    print(f"Load factor rho         : {rho:.4f}")
    print( f"Base traffic seed       : " f"{base_traffic_seed}")
    print(f"Simulation duration     : "f"{simulation_duration_s:.3f} s")
    print( f"Configured offered load : " f"{configured_offered_traffic:.6f} pairs/s" )

    # ============================================================
    # 2. Load topology and timeline
    # ============================================================
    
    network_topology = RouterNetTopo( str(NETWORK_CONFIG) )

    timeline = network_topology.get_timeline()

    timeline.stop_time = int( simulation_duration_s * 1e12)

    routers = { node.name: node
        for node in network_topology.get_nodes_by_type(  RouterNetTopo.QUANTUM_ROUTER)
    }
    for router in routers.values():
        router.network_manager.enable_purification = False
    # ============================================================
    # 3. Apply hardware and physical-link parameters
    # ============================================================

    set_parameters( topology=network_topology, use_random_coherence=True,)
    print("\n" "================ MEMORY CONFIGURATION ================\n" )

    for node in routers.values():
        memory_array = (node.get_components_by_type(  MemoryArray )[0] )
        print(
            f"{node.name}: "
            f"{type(memory_array[0]).__name__}, "
            f"size={len(memory_array)}"
        )

    # ============================================================
    # 4. Install instrumentation
    # ============================================================

    patch_generation_classes()
    instrument_resource_managers( network_topology )

    # ============================================================
    # 5. Create one Poisson application per source node
    # ============================================================

    applications = []

    for app_index, source_name in enumerate(
        simulation_traffic
    ):
        application_seed = (
            base_traffic_seed + app_index
        )

        application = NodeTrafficApp(
            node=routers[source_name],
            traffic_demands=(
                simulation_traffic[source_name]
            ),
            start_offset_s=0.5 * app_index,

            # Correct constructor parameter name.
            max_parallel_sessions_per_flow=10,

            reservation_duration_s=5.0,
            reservation_setup_margin_s=1.0,
            random_seed=application_seed,
            enable_purification=(enable_purification),
            verbose=verbose,
        )

        application.schedule_initial_events()
        applications.append(application)

        print(
            f"[TRAFFIC] source={source_name}, "
            f"seed={application_seed}"
        )

    # ============================================================
    # 6. Run simulation
    # ============================================================

    timeline.init()

    print(
        "\n"
        "================ RUNNING SEQUENCE BASELINE ================\n"
    )

    timeline.run()

    print(
        "\n"
        "================ SIMULATION COMPLETED ================\n"
    )

    # ============================================================
    # 7. Print statistics
    # ============================================================

    for application in applications:
        application.print_traffic_statistics()

    compute_node_flow_statistics(
        applications
    )

    compute_link_physics_statistics()

    results_directory = (
        Path(__file__).resolve().parent
        / "results"
        / "classical_sequence"
    )

    output_files = export_classical_sequence_results(
            applications=applications,
            rho=rho,
            base_traffic_seed=base_traffic_seed,
            simulation_duration_s=simulation_duration_s,
            configured_offered_traffic=(
                configured_offered_traffic
            ),
            base_output_directory=results_directory,
        )

    return {
        "rho": rho,
        "base_traffic_seed":
            base_traffic_seed,
        "simulation_duration_s":
            simulation_duration_s,
        "configured_offered_traffic_pairs_per_second":
            configured_offered_traffic,
        "applications":
            applications,
        "network_topology":
            network_topology,
        "output_files":
            output_files,
    }


def main():
    run_classical_sequence_experiment(
        rho=3.20,
        base_traffic_seed=1000,
        simulation_duration_s=30.0,
        enable_purification=False,
        verbose=False,
    )


if __name__ == "__main__":
    main()