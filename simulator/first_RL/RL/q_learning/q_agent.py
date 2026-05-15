import random
import numpy as np

from simulator.first_RL.RL.actions import WAIT, SWAP


class LocalSwapQAgent:
    """
    Simple online Q-learning agent.

    First implementation:
    - discrete state obtained by binning fidelity and TTL
    - actions: WAIT / SWAP
    """

    def __init__(
        self,
        alpha=0.1,
        gamma=0.95,
        epsilon=1.0,
        epsilon_min=0.05,
        epsilon_decay=0.999,
    ):
        self.q_table = {}

        self.alpha = alpha
        self.gamma = gamma

        self.epsilon = epsilon
        self.epsilon_min = epsilon_min
        self.epsilon_decay = epsilon_decay

    def discretize(self, obs):
        f_left, f_right, ttl_left, ttl_right, min_ttl, f_swap, f_target = obs

        return (
            int(f_left * 10),
            int(f_right * 10),
            int(ttl_left * 10),
            int(ttl_right * 10),
            int(min_ttl * 10),
            int(f_swap * 10),
        )

    def get_q_values(self, state):
        if state not in self.q_table:
            self.q_table[state] = {
                WAIT: 0.0,
                SWAP: 0.0,
            }
        return self.q_table[state]

    def act(self, obs):
        state = self.discretize(obs)

        if random.random() < self.epsilon:
            return random.choice([WAIT, SWAP])

        q_values = self.get_q_values(state)
        return max(q_values, key=q_values.get)

    def update(self, obs, action, reward, next_obs, done=False):
        state = self.discretize(obs)
        next_state = self.discretize(next_obs)

        q_values = self.get_q_values(state)
        next_q_values = self.get_q_values(next_state)

        current_q = q_values[action]

        if done:
            target = reward
        else:
            target = reward + self.gamma * max(next_q_values.values())

        q_values[action] = current_q + self.alpha * (target - current_q)

        self.epsilon = max(
            self.epsilon_min,
            self.epsilon * self.epsilon_decay
        )