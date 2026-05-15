from stable_baselines3 import DQN
from simulator.first_RL.RL.quantum_swap_env import QuantumSwapEnv


def main():
    env = QuantumSwapEnv(
    target_fidelity=0.75,
    max_steps=20,
    bad_swap_penalty=1.0,
    timeout_penalty=1.0,
    wait_penalty=0.01,
    improvement_probability=0.35,
    max_fidelity_improvement=0.08,
)

    model = DQN(
        "MlpPolicy",
        env,
        learning_rate=1e-3,
        buffer_size=10_000,
        learning_starts=500,
        batch_size=64,
        gamma=0.95,
        exploration_fraction=0.3,
        exploration_final_eps=0.05,
        verbose=1,
    )

    model.learn(total_timesteps=20_000)
    model.save("simulator/first_RL/rl/dqn_wait_swap")

    print("DQN model saved.")


if __name__ == "__main__":
    main()