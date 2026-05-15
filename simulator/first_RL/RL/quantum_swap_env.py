import gymnasium as gym
from gymnasium import spaces
import numpy as np


WAIT = 0
SWAP = 1


class QuantumSwapEnv(gym.Env):
    """
    Minimal Gymnasium environment for local WAIT/SWAP decisions.

    Observation:
        [
            fidelity_left,
            fidelity_right,
            ttl_left,
            ttl_right,
            free_memory_ratio
        ]

    Action:
        0 = WAIT
        1 = SWAP
    """

    metadata = {"render_modes": []}

    def __init__(
    self,
    target_fidelity=0.75,
    max_steps=20,
    wait_penalty=0.01,
    timeout_penalty=1.0,
    bad_swap_penalty=0.5,
    ttl_decay_per_step=0.05,
    improvement_probability=0.35,
    max_fidelity_improvement=0.08,
):
        super().__init__()

        self.target_fidelity = target_fidelity
        self.max_steps = max_steps
        self.wait_penalty = wait_penalty
        self.timeout_penalty = timeout_penalty
        self.bad_swap_penalty = bad_swap_penalty
        self.ttl_decay_per_step = ttl_decay_per_step

        self.action_space = spaces.Discrete(2)
        self.improvement_probability = improvement_probability
        self.max_fidelity_improvement = max_fidelity_improvement
        self.observation_space = spaces.Box(
            low=np.array([0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32),
            high=np.array([1.0, 1.0, 1.0, 1.0, 1.0], dtype=np.float32),
            dtype=np.float32,
        )

        self.state = None
        self.steps = 0

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)

        fidelity_left = self.np_random.uniform(0.70, 0.95)
        fidelity_right = self.np_random.uniform(0.70, 0.95)

        ttl_left = self.np_random.uniform(0.30, 1.00)
        ttl_right = self.np_random.uniform(0.30, 1.00)

        free_memory_ratio = self.np_random.uniform(0.20, 1.00)

        self.state = np.array(
            [
                fidelity_left,
                fidelity_right,
                ttl_left,
                ttl_right,
                free_memory_ratio,
            ],
            dtype=np.float32,
        )

        self.steps = 0

        return self.state, {}

    def step(self, action):
        self.steps += 1

        fidelity_left, fidelity_right, ttl_left, ttl_right, free_memory_ratio = self.state

        predicted_fidelity = fidelity_left * fidelity_right
        residual_ttl = min(ttl_left, ttl_right)

        terminated = False
        truncated = False

        if action == WAIT:
            reward = -self.wait_penalty

            ttl_left = max(0.0, ttl_left - self.ttl_decay_per_step)
            ttl_right = max(0.0, ttl_right - self.ttl_decay_per_step)

            # Abstract model of waiting:
            # with some probability, waiting gives time for a better pair
            # or for a purification-like improvement.
            if self.np_random.random() < self.improvement_probability:
                improvement_left = self.np_random.uniform(0.0, self.max_fidelity_improvement)
                improvement_right = self.np_random.uniform(0.0, self.max_fidelity_improvement)

                fidelity_left = min(1.0, fidelity_left + improvement_left)
                fidelity_right = min(1.0, fidelity_right + improvement_right)

            self.state = np.array(
                [
                    fidelity_left,
                    fidelity_right,
                    ttl_left,
                    ttl_right,
                    free_memory_ratio,
                ],
                dtype=np.float32,
            )

            if ttl_left <= 0.0 or ttl_right <= 0.0:
                reward = -self.timeout_penalty
                terminated = True

        elif action == SWAP:
            if predicted_fidelity >= self.target_fidelity:
                reward = 1.0 + residual_ttl
            else:
                reward = -self.bad_swap_penalty

            terminated = True

        if self.steps >= self.max_steps:
            reward = -self.timeout_penalty
            truncated = True

        info = {
            "predicted_fidelity": float(predicted_fidelity),
            "residual_ttl": float(residual_ttl),
            "target_fidelity": self.target_fidelity,
            "action": int(action),
        }

        return self.state, reward, terminated, truncated, info