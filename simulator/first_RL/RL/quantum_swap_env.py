import gymnasium as gym
from gymnasium import spaces
import numpy as np


WAIT = 0
SWAP = 1


class QuantumSwapEnv(gym.Env):
    """
    Minimal Gymnasium environment for local WAIT/SWAP decisions.

    The agent does NOT observe fidelity.

    Observation:
        [
            min_residual_lifetime,
            ttl_imbalance,
            free_memory_ratio,
            eg_success_rate_left,
            eg_success_rate_right,
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
        wait_penalty=0.02,
        timeout_penalty=1.0,
        bad_swap_penalty=0.5,
        ttl_decay_per_step=0.05,
        link_update_probability=0.35,
        hidden_quality_drift=0.04,
    ):
        super().__init__()

        self.target_fidelity = target_fidelity
        self.max_steps = max_steps
        self.wait_penalty = wait_penalty
        self.timeout_penalty = timeout_penalty
        self.bad_swap_penalty = bad_swap_penalty
        self.ttl_decay_per_step = ttl_decay_per_step
        self.link_update_probability = link_update_probability
        self.hidden_quality_drift = hidden_quality_drift

        self.action_space = spaces.Discrete(2)

        self.observation_space = spaces.Box(
            low=np.zeros(5, dtype=np.float32),
            high=np.ones(5, dtype=np.float32),
            dtype=np.float32,
        )

        self.state = None
        self.steps = 0

        # Hidden simulator variables.
        # They are NOT exposed to the agent.
        self.hidden_fidelity_left = None
        self.hidden_fidelity_right = None

    def _build_obs(
        self,
        ttl_left,
        ttl_right,
        free_memory_ratio,
        eg_success_left,
        eg_success_right,
    ):
        min_ttl = min(ttl_left, ttl_right)
        ttl_imbalance = abs(ttl_left - ttl_right)

        return np.array(
            [
                min_ttl,
                ttl_imbalance,
                free_memory_ratio,
                eg_success_left,
                eg_success_right,
            ],
            dtype=np.float32,
        )

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)

        # Hidden physical quality, used only for training reward.
        self.hidden_fidelity_left = self.np_random.uniform(0.70, 0.95)
        self.hidden_fidelity_right = self.np_random.uniform(0.70, 0.95)

        ttl_left = self.np_random.uniform(0.30, 1.00)
        ttl_right = self.np_random.uniform(0.30, 1.00)

        free_memory_ratio = self.np_random.uniform(0.20, 1.00)

        eg_success_left = self.np_random.uniform(0.20, 0.90)
        eg_success_right = self.np_random.uniform(0.20, 0.90)

        self.state = self._build_obs(
            ttl_left,
            ttl_right,
            free_memory_ratio,
            eg_success_left,
            eg_success_right,
        )

        self._ttl_left = ttl_left
        self._ttl_right = ttl_right
        self._free_memory_ratio = free_memory_ratio
        self._eg_success_left = eg_success_left
        self._eg_success_right = eg_success_right

        self.steps = 0

        return self.state, {}

    def step(self, action):
        self.steps += 1

        terminated = False
        truncated = False

        hidden_swap_quality = min(
            self.hidden_fidelity_left,
            self.hidden_fidelity_right,
        )

        min_ttl = min(self._ttl_left, self._ttl_right)
        ttl_imbalance = abs(self._ttl_left - self._ttl_right)

        if action == WAIT:
            reward = -self.wait_penalty

            self._ttl_left = max(0.0, self._ttl_left - self.ttl_decay_per_step)
            self._ttl_right = max(0.0, self._ttl_right - self.ttl_decay_per_step)

            # Abstract effect of waiting:
            # new attempts/statistics can improve empirical link estimates.
            if self.np_random.random() < self.link_update_probability:
                self._eg_success_left = float(
                    np.clip(
                        self._eg_success_left + self.np_random.uniform(-0.05, 0.08),
                        0.0,
                        1.0,
                    )
                )
                self._eg_success_right = float(
                    np.clip(
                        self._eg_success_right + self.np_random.uniform(-0.05, 0.08),
                        0.0,
                        1.0,
                    )
                )

            # Hidden fidelity may improve slightly if a better candidate appears,
            # or degrade due to decoherence. This remains hidden.
            self.hidden_fidelity_left = float(
                np.clip(
                    self.hidden_fidelity_left + self.np_random.uniform(-0.03, self.hidden_quality_drift),
                    0.0,
                    1.0,
                )
            )
            self.hidden_fidelity_right = float(
                np.clip(
                    self.hidden_fidelity_right + self.np_random.uniform(-0.03, self.hidden_quality_drift),
                    0.0,
                    1.0,
                )
            )

            if self._ttl_left <= 0.0 or self._ttl_right <= 0.0:
                reward = -self.timeout_penalty
                terminated = True

        elif action == SWAP:
            if hidden_swap_quality >= self.target_fidelity:
                reward = 1.0 + min_ttl - 0.3 * ttl_imbalance
            else:
                reward = -self.bad_swap_penalty

            terminated = True

        if self.steps >= self.max_steps:
            reward = -self.timeout_penalty
            truncated = True

        self.state = self._build_obs(
            self._ttl_left,
            self._ttl_right,
            self._free_memory_ratio,
            self._eg_success_left,
            self._eg_success_right,
        )

        info = {
            "hidden_swap_quality": float(hidden_swap_quality),
            "min_ttl": float(min_ttl),
            "ttl_imbalance": float(ttl_imbalance),
            "target_fidelity": self.target_fidelity,
            "action": int(action),
        }

        return self.state, reward, terminated, truncated, info