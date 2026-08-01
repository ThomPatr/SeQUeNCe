from stable_baselines3 import DQN
from stable_baselines3.common.monitor import Monitor
from simulator.first_RL.RL.quantum_swap_env import QuantumSwapEnv


def main():

    env = Monitor(
        QuantumSwapEnv(
            target_fidelity=0.75,
            max_steps=20,
            wait_penalty=0.02,
            timeout_penalty=1.0,
            bad_swap_penalty=0.5,
            ttl_decay_per_step=0.05,
            link_update_probability=0.35,
            hidden_quality_drift=0.04,
        )
    )

    model = DQN(
        policy="MlpPolicy",
        env=env,

        learning_rate=1e-3,

        gamma=0.95,

        buffer_size=10000,

        learning_starts=500,

        batch_size=64,

        target_update_interval=100,

        exploration_fraction=0.30,

        exploration_initial_eps=1.0,

        exploration_final_eps=0.05,

        verbose=1,
    )

    model.learn(total_timesteps=20000)

    model.save("dqn_wait_swap")

    print("Pre-trained DQN saved.")


if __name__ == "__main__":
    main()