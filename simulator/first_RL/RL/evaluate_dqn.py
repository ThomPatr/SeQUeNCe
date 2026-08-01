from stable_baselines3 import DQN

from simulator.first_RL.RL.quantum_swap_env import QuantumSwapEnv, WAIT, SWAP

ACTION_NAMES = {
    WAIT: "WAIT",
    SWAP: "SWAP",
}


def main():
    model = DQN.load("dqn_wait_swap")

    env = QuantumSwapEnv(
        target_fidelity=0.75,
        max_steps=20,
    )

    n_episodes = 20

    for ep in range(n_episodes):
        obs, info = env.reset()
        done = False
        total_reward = 0.0
        step = 0

        print(f"\n================ EPISODE {ep + 1} ================")

        while not done:
            action, _ = model.predict(obs, deterministic=True)

            next_obs, reward, terminated, truncated, info = env.step(int(action))

            min_ttl = obs[0]
            ttl_imbalance = obs[1]
            free_mem = obs[2]
            eg_left = obs[3]
            eg_right = obs[4]

            print(
                f"step={step:02d} | "
                f"min_TTL={min_ttl:.3f}, "
                f"ttl_imbalance={ttl_imbalance:.3f}, "
                f"free_mem={free_mem:.3f}, "
                f"eg_left={eg_left:.3f}, "
                f"eg_right={eg_right:.3f} | "
                f"hidden_quality={info['hidden_swap_quality']:.3f}, "
                f"target={info['target_fidelity']:.3f} | "
                f"action={ACTION_NAMES[int(action)]} | "
                f"reward={reward:.3f}"
            )

            obs = next_obs
            total_reward += reward
            done = terminated or truncated
            step += 1

        print(f"TOTAL REWARD: {total_reward:.3f}")


if __name__ == "__main__":
    main()