import random
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim

from simulator.first_RL.RL.replay_buffer import ReplayBuffer


class QNetwork(nn.Module):
    """Feed-forward network used to approximate Q(s, a)."""

    def __init__(self, obs_dim: int, action_dim: int):
        super().__init__()

        self.net = nn.Sequential(
            nn.Linear(obs_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 64),
            nn.ReLU(),
            nn.Linear(64, action_dim),
        )

    def forward(self, x):
        return self.net(x)


class OnlineDQNAgent:
    """
    Online Double DQN agent with experience replay and
    Polyak target-network updates.
    """

    def __init__(
        self,
        obs_dim: int = 9,
        action_dim: int = 2,
        gamma: float = 0.95,
        batch_size: int = 32,
        lr: float = 1e-3,
        epsilon: float = 1.0,
        epsilon_min: float = 0.05,
        epsilon_decay: float = 0.995,
        tau: float = 0.005,
        replay_capacity: int = 10_000,
    ):
        self.obs_dim = obs_dim
        self.action_dim = action_dim
        self.gamma = gamma
        self.batch_size = batch_size
        self.tau = tau

        self.epsilon = epsilon
        self.epsilon_min = epsilon_min
        self.epsilon_decay = epsilon_decay

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        self.q_net = QNetwork(obs_dim, action_dim).to(self.device)
        self.target_net = QNetwork(obs_dim, action_dim).to(self.device)

        self.target_net.load_state_dict(self.q_net.state_dict())
        self.target_net.eval()

        self.optimizer = optim.Adam(self.q_net.parameters(), lr=lr)
        self.replay_buffer = ReplayBuffer(capacity=replay_capacity)

        self.train_step_counter = 0
        self.last_loss = None

        self.statistics = {
            "random_actions": 0,
            "greedy_actions": 0,
            "training_updates": 0,
        }

    def act(self, observation):
        """Select an action using an epsilon-greedy policy."""

        if random.random() < self.epsilon:
            self.statistics["random_actions"] += 1
            return random.randrange(self.action_dim)

        observation = torch.as_tensor(
            observation,
            dtype=torch.float32,
            device=self.device,
        ).unsqueeze(0)

        with torch.no_grad():
            q_values = self.q_net(observation)

        self.statistics["greedy_actions"] += 1
        return int(torch.argmax(q_values, dim=1).item())

    def store_transition(self, state, action, reward, next_state, done):
        self.replay_buffer.push(state, action, reward, next_state, done)

    def train_step(self):
        """Perform one Double-DQN update."""

        if not self.replay_buffer.is_ready(self.batch_size):
            return None

        states, actions, rewards, next_states, dones = self.replay_buffer.sample(
            self.batch_size
        )

        states = torch.as_tensor(
            states,
            dtype=torch.float32,
            device=self.device,
        )
        actions = torch.as_tensor(
            actions,
            dtype=torch.long,
            device=self.device,
        )
        rewards = torch.as_tensor(
            rewards,
            dtype=torch.float32,
            device=self.device,
        )
        next_states = torch.as_tensor(
            next_states,
            dtype=torch.float32,
            device=self.device,
        )
        dones = torch.as_tensor(
            dones,
            dtype=torch.float32,
            device=self.device,
        )

        current_q = self.q_net(states).gather(
            1,
            actions.unsqueeze(1),
        ).squeeze(1)

        # Double DQN:
        # the online network selects the best next action,
        # while the target network evaluates it.
        with torch.no_grad():
            best_actions = self.q_net(next_states).argmax(dim=1, keepdim=True)
            next_q = self.target_net(next_states).gather(
                1,
                best_actions,
            ).squeeze(1)

            target_q = rewards + (1.0 - dones) * self.gamma * next_q

        loss = nn.functional.smooth_l1_loss(current_q, target_q)

        self.optimizer.zero_grad()
        loss.backward()

        torch.nn.utils.clip_grad_norm_(
            self.q_net.parameters(),
            max_norm=1.0,
        )

        self.optimizer.step()

        # Polyak averaging for the target-network parameters.
        with torch.no_grad():
            for target_param, online_param in zip(
                self.target_net.parameters(),
                self.q_net.parameters(),
            ):
                target_param.data.mul_(1.0 - self.tau)
                target_param.data.add_(self.tau * online_param.data)

        self.train_step_counter += 1
        self.statistics["training_updates"] += 1
        self.last_loss = float(loss.item())

        self.epsilon = max(
            self.epsilon_min,
            self.epsilon * self.epsilon_decay,
        )

        return self.last_loss

    def save(self, path: str | Path):
        torch.save(
            {
                "q_network": self.q_net.state_dict(),
                "target_network": self.target_net.state_dict(),
                "optimizer": self.optimizer.state_dict(),
                "epsilon": self.epsilon,
                "train_steps": self.train_step_counter,
            },
            path,
        )

    def load(self, path: str | Path):
        checkpoint = torch.load(path, map_location=self.device)

        self.q_net.load_state_dict(checkpoint["q_network"])
        self.target_net.load_state_dict(checkpoint["target_network"])
        self.optimizer.load_state_dict(checkpoint["optimizer"])

        self.epsilon = checkpoint["epsilon"]
        self.train_step_counter = checkpoint["train_steps"]