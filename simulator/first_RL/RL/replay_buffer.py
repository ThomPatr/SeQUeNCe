from collections import deque
import random

import numpy as np


class ReplayBuffer:
    """
    Uniform experience replay buffer.

    Stores transitions of the form:
        state, action, reward, next_state, done
    """

    def __init__(self, capacity: int = 10_000):
        if capacity <= 0:
            raise ValueError("Replay-buffer capacity must be positive.")

        self.capacity = int(capacity)
        self.buffer = deque(maxlen=self.capacity)

    def push(self, state, action, reward, next_state, done) -> None:
        self.buffer.append(
            (
                np.array(state, dtype=np.float32, copy=True),
                int(action),
                float(reward),
                np.array(next_state, dtype=np.float32, copy=True),
                bool(done),
            )
        )

    def sample(self, batch_size: int):
        if batch_size <= 0:
            raise ValueError("Batch size must be positive.")

        if not self.is_ready(batch_size):
            raise ValueError(
                f"Not enough transitions: requested {batch_size}, "
                f"available {len(self.buffer)}."
            )

        batch = random.sample(self.buffer, batch_size)
        states, actions, rewards, next_states, dones = zip(*batch)

        return (
            np.stack(states).astype(np.float32, copy=False),
            np.asarray(actions, dtype=np.int64),
            np.asarray(rewards, dtype=np.float32),
            np.stack(next_states).astype(np.float32, copy=False),
            np.asarray(dones, dtype=np.float32),
        )

    def is_ready(self, batch_size: int) -> bool:
        return batch_size > 0 and len(self.buffer) >= batch_size

    def clear(self) -> None:
        self.buffer.clear()

    def __len__(self) -> int:
        return len(self.buffer)