from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import torch

from sequence.components.memory import MemoryArray
from sequence.topology import node as topology_node
from sequence.topology.router_net_topo import RouterNetTopo

from simulator.first_RL.RL.dqn_swap_controller import DQNSwapController
from simulator.first_RL.RL.online_dqn_agent import OnlineDQNAgent
from simulator.first_RL.apps.node_traffic_app import NodeTrafficApp
from simulator.first_RL.apps.traffic_utils import compute_configured_offered_traffic, scale_poisson_traffic
from simulator.first_RL.config import NETWORK_CONFIG, TRAFFIC_MATRIX
from simulator.first_RL.instrumentation.tracking import instrument_tracking
from simulator.first_RL.metrics.link_metrics import reset_link_metrics
from simulator.first_RL.metrics.protocol_metrics import initialize_flow_protocol_metrics, reset_protocol_metrics
from simulator.first_RL.physics.parameters import set_parameters
from simulator.first_RL.reporting.statistics import compute_link_physics_statistics, compute_node_flow_statistics
from simulator.first_RL.topology.custom_memory_array import CustomMemoryArray


topology_node.MemoryArray = CustomMemoryArray

DEFAULT_LOAD_FACTOR = 5.0
DEFAULT_TRAFFIC_SEED = 2000
DEFAULT_SIMULATION_DURATION_S = 30.0
DEFAULT_ENABLE_PURIFICATION = False

DEFAULT_TARGET_FIDELITY = 0.75

DEFAULT_OBSERVATION_DIMENSION = 9
DEFAULT_ACTION_DIMENSION = 2
DEFAULT_GAMMA = 0.95
DEFAULT_BATCH_SIZE = 32
DEFAULT_LEARNING_RATE = 1e-3
DEFAULT_INITIAL_EPSILON = 1.0
DEFAULT_MINIMUM_EPSILON = 0.05
DEFAULT_EPSILON_DECAY = 0.995
DEFAULT_TAU = 0.005
DEFAULT_REPLAY_CAPACITY = 10_000


def set_random_seeds(seed: int) -> None:
    """
    Set all random seeds used by the experiment.

    This makes traffic generation, NumPy operations and neural-network
    initialization reproducible.
    """
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def run_rl_sequence_experiment(
    rho: float = DEFAULT_LOAD_FACTOR,
    base_traffic_seed: int = DEFAULT_TRAFFIC_SEED,
    simulation_duration_s: float = DEFAULT_SIMULATION_DURATION_S,
    enable_purification: bool = DEFAULT_ENABLE_PURIFICATION,
    verbose: bool = False,
):
    """
    Run one SeQUeNCe experiment with online Double DQN swapping control.

    All routers use independent DQNSwapController instances while sharing
    one common OnlineDQNAgent and replay buffer.
    """
    if rho <= 0:
        raise ValueError("rho must be strictly positive.")

    if simulation_duration_s <= 0:
        raise ValueError("simulation_duration_s must be positive.")

    if base_traffic_seed < 0:
        raise ValueError("base_traffic_seed must be non-negative.")

    # 0. Reset experiment state and random seeds
    reset_link_metrics()
    reset_protocol_metrics()
    set_random_seeds(base_traffic_seed)

    # 1. Scale the Poisson traffic matrix
    simulation_traffic = scale_poisson_traffic(traffic_matrix=TRAFFIC_MATRIX, rho=rho)
    initialize_flow_protocol_metrics(simulation_traffic)
    configured_offered_traffic = compute_configured_offered_traffic(simulation_traffic)

    print("\n================ EXPERIMENT CONFIGURATION ================\n")
    print(f"Load factor rho         : {rho:.4f}")
    print(f"Base traffic seed       : {base_traffic_seed}")
    print(f"Simulation duration     : {simulation_duration_s:.3f} s")
    print(f"Configured offered load : {configured_offered_traffic:.6f} pairs/s")
    print(f"Purification enabled    : {enable_purification}")

    # 2. Load topology and timeline
    network_topology = RouterNetTopo(str(NETWORK_CONFIG))
    timeline = network_topology.get_timeline()
    timeline.stop_time = int(simulation_duration_s * 1e12)

    routers = {
        node.name: node
        for node in network_topology.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER)
    }

    print(f"\n[DEBUG] Config file: {NETWORK_CONFIG}")
    print(
        f"[DEBUG] Timeline stop time: {timeline.stop_time} ps "
        f"({timeline.stop_time * 1e-12:.3f} s)"
    )
    print(f"[DEBUG] Number of routers: {len(routers)}")

    for router in routers.values():
        router.network_manager.enable_purification = enable_purification

    # 3. Apply hardware and physical-link parameters
    set_parameters(topology=network_topology, use_random_coherence=True)

    print("\n================ MEMORY CONFIGURATION ================\n")

    for node in routers.values():
        memory_arrays = node.get_components_by_type(MemoryArray)

        if not memory_arrays:
            print(f"{node.name}: no MemoryArray found")
            continue

        memory_array = memory_arrays[0]

        if len(memory_array) == 0:
            print(f"{node.name}: {type(memory_array).__name__}, size=0")
            continue

        print(f"{node.name}: {type(memory_array[0]).__name__}, size={len(memory_array)}")

    # 4. Create the shared online DQN agent
    shared_agent = OnlineDQNAgent(
        obs_dim=DEFAULT_OBSERVATION_DIMENSION,
        action_dim=DEFAULT_ACTION_DIMENSION,
        gamma=DEFAULT_GAMMA,
        batch_size=DEFAULT_BATCH_SIZE,
        lr=DEFAULT_LEARNING_RATE,
        epsilon=DEFAULT_INITIAL_EPSILON,
        epsilon_min=DEFAULT_MINIMUM_EPSILON,
        epsilon_decay=DEFAULT_EPSILON_DECAY,
        tau=DEFAULT_TAU,
        replay_capacity=DEFAULT_REPLAY_CAPACITY,
    )

    print("\n================ RL CONFIGURATION ================\n")
    print(f"Observation dimension  : {DEFAULT_OBSERVATION_DIMENSION}")
    print(f"Action dimension       : {DEFAULT_ACTION_DIMENSION}")
    print(f"Target fidelity        : {DEFAULT_TARGET_FIDELITY:.3f}")
    print(f"Gamma                  : {DEFAULT_GAMMA:.3f}")
    print(f"Batch size             : {DEFAULT_BATCH_SIZE}")
    print(f"Learning rate          : {DEFAULT_LEARNING_RATE}")
    print(f"Initial epsilon        : {DEFAULT_INITIAL_EPSILON:.3f}")
    print(f"Minimum epsilon        : {DEFAULT_MINIMUM_EPSILON:.3f}")
    print(f"Epsilon decay          : {DEFAULT_EPSILON_DECAY:.6f}")
    print(f"Replay capacity        : {DEFAULT_REPLAY_CAPACITY}")
    print(f"Device                 : {shared_agent.device}")

    # 5. Attach one RL controller to each router
    controllers: dict[str, DQNSwapController] = {}

    for router_name, router in routers.items():
        controller = DQNSwapController(
            agent=shared_agent,
            default_target_fidelity=DEFAULT_TARGET_FIDELITY,
        )

        router.rl_swap_controller = controller
        controllers[router_name] = controller
        print(f"[RL] Controller attached to {router_name}")

    # 6. Install instrumentation
    instrument_tracking(network_topology)

    # 7. Create one Poisson application per source node
    applications: list[NodeTrafficApp] = []

    for app_index, source_name in enumerate(simulation_traffic):
        if source_name not in routers:
            raise KeyError(f"Traffic source '{source_name}' is not present in the topology.")

        application_seed = base_traffic_seed + app_index

        application = NodeTrafficApp(
            node=routers[source_name],
            traffic_demands=simulation_traffic[source_name],
            start_offset_s=0.5 * app_index,
            max_parallel_sessions_per_flow=10,
            reservation_duration_s=5.0,
            reservation_setup_margin_s=1.0,
            random_seed=application_seed,
            enable_purification=enable_purification,
            verbose=verbose,
        )

        application.schedule_initial_events()
        applications.append(application)
        print(f"[TRAFFIC] source={source_name}, seed={application_seed}")

    # 8. Initialize and run the simulation
    timeline.init()

    print("\n================ RUNNING RL SEQUENCE ================\n")
    timeline.run()
    print("\n================ SIMULATION COMPLETED ================\n")

    # 9. Close all still-pending RL transitions
    for controller in controllers.values():
        controller.on_simulation_end(nodes_by_name=routers)

    # 10. Print simulation statistics
    for application in applications:
        application.print_traffic_statistics()

    compute_node_flow_statistics(applications)
    compute_link_physics_statistics()

    # 11. Print optional detailed histories
    if verbose:
        for application in applications:
            print(f"\n=== FINAL HISTORY FOR NODE {application.node.name} ===")

            for item in application.history:
                print(item)

    # 12. Save the trained model
    results_directory = Path(__file__).resolve().parent / "results" / "rl_sequence"
    results_directory.mkdir(parents=True, exist_ok=True)

    model_path = (
        results_directory
        / f"online_dqn_rho_{rho:.4f}_seed_{base_traffic_seed}.pt"
    )

    shared_agent.save(model_path)

    # 13. Aggregate RL statistics
    total_completed_waits = sum(
        getattr(controller, "completed_waits", 0)
        for controller in controllers.values()
    )
    total_completed_swaps = sum(
        getattr(controller, "completed_swaps", 0)
        for controller in controllers.values()
    )
    pending_waits = sum(
        len(getattr(controller, "pending_wait", {}))
        for controller in controllers.values()
    )
    pending_swaps = sum(
        len(getattr(controller, "pending_swap", {}))
        for controller in controllers.values()
    )

    replay_buffer = getattr(shared_agent, "replay_buffer", None)
    replay_size = len(replay_buffer) if replay_buffer is not None else 0
    training_updates = getattr(shared_agent, "train_step_counter", 0)
    final_epsilon = getattr(shared_agent, "epsilon", None)
    last_loss = getattr(shared_agent, "last_loss", None)

    print("\n================ ONLINE DQN SUMMARY ================\n")
    print(f"Device                     : {shared_agent.device}")
    print(f"Replay size                : {replay_size}")
    print(f"Training updates           : {training_updates}")
    print(f"Completed WAIT transitions : {total_completed_waits}")
    print(f"Completed SWAP transitions : {total_completed_swaps}")
    print(f"Pending WAIT transitions   : {pending_waits}")
    print(f"Pending SWAP transitions   : {pending_swaps}")

    if final_epsilon is not None:
        print(f"Final epsilon              : {final_epsilon:.6f}")

    print(f"Last loss                  : {last_loss}")
    print(f"Model saved to             : {model_path.resolve()}")

    return {
        "rho": rho,
        "base_traffic_seed": base_traffic_seed,
        "simulation_duration_s": simulation_duration_s,
        "configured_offered_traffic_pairs_per_second": configured_offered_traffic,
        "applications": applications,
        "network_topology": network_topology,
        "shared_agent": shared_agent,
        "controllers": controllers,
        "model_path": model_path,
        "rl_statistics": {
            "replay_size": replay_size,
            "training_updates": training_updates,
            "completed_waits": total_completed_waits,
            "completed_swaps": total_completed_swaps,
            "pending_waits": pending_waits,
            "pending_swaps": pending_swaps,
            "final_epsilon": final_epsilon,
            "last_loss": last_loss,
        },
    }


def main() -> None:
    run_rl_sequence_experiment(
        rho=3.20,
        base_traffic_seed=1000,
        simulation_duration_s=10.0,
        enable_purification=False,
        verbose=False,
    )


if __name__ == "__main__":
    main()