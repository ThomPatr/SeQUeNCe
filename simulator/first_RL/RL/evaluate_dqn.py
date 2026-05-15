from stable_baselines3 import DQN

from simulator.first_RL.RL.quantum_swap_env import QuantumSwapEnv, WAIT, SWAP


ACTION_NAMES = {
    WAIT: "WAIT",
    SWAP: "SWAP",
}


def main():
    model = DQN.load("simulator/first_RL/RL/dqn_wait_swap")

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

            f_left = obs[0]
            f_right = obs[1]
            ttl_left = obs[2]
            ttl_right = obs[3]
            free_mem = obs[4]
            predicted_f = info["predicted_fidelity"]
            residual_ttl = info["residual_ttl"]

            print(
                f"step={step:02d} | "
                f"F_left={f_left:.3f}, F_right={f_right:.3f}, "
                f"TTL_left={ttl_left:.3f}, TTL_right={ttl_right:.3f}, "
                f"free_mem={free_mem:.3f} | "
                f"pred_F={predicted_f:.3f}, res_TTL={residual_ttl:.3f} | "
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