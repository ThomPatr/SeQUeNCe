import random
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

from simulator.first_RL.RL.replay_buffer import ReplayBuffer


class QNetwork(nn.Module):

    def __init__(self, obs_dim, action_dim):
        super().__init__()

        self.net = nn.Sequential(
            nn.Linear(obs_dim, 64),
            nn.ReLU(),

            nn.Linear(64, 64),
            nn.ReLU(),

            nn.Linear(64, action_dim)
        )

    def forward(self, x):
        return self.net(x)


class OnlineDQNAgent:

    def __init__(self):

        self.obs_dim = 5
        self.action_dim = 2

        self.gamma = 0.99
        self.batch_size = 64

        self.epsilon = 1.0
        self.epsilon_min = 0.05
        self.epsilon_decay = 0.995

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        self.q_net = QNetwork(self.obs_dim, self.action_dim).to(self.device)

        self.target_net = QNetwork(self.obs_dim, self.action_dim).to(self.device)

        self.target_net.load_state_dict(self.q_net.state_dict())

        self.optimizer = optim.Adam(self.q_net.parameters(), lr=1e-3)

        self.replay_buffer = ReplayBuffer()

        self.train_step_counter = 0

    def act(self, obs):

        if random.random() < self.epsilon:
            return random.randint(0, 1)

        obs_tensor = torch.FloatTensor(obs).unsqueeze(0).to(self.device)

        with torch.no_grad():
            q_values = self.q_net(obs_tensor)

        return int(torch.argmax(q_values).item())

    def store_transition(self, s, a, r, ns, done):

        self.replay_buffer.push(s, a, r, ns, done)

    def train_step(self):

        if len(self.replay_buffer) < self.batch_size:
            return

        states, actions, rewards, next_states, dones = \
            self.replay_buffer.sample(self.batch_size)

        states = torch.FloatTensor(states).to(self.device)
        actions = torch.LongTensor(actions).to(self.device)
        rewards = torch.FloatTensor(rewards).to(self.device)
        next_states = torch.FloatTensor(next_states).to(self.device)
        dones = torch.FloatTensor(dones).to(self.device)

        q_values = self.q_net(states)

        current_q = q_values.gather(1, actions.unsqueeze(1)).squeeze(1)

        with torch.no_grad():

            next_q = self.target_net(next_states).max(1)[0]

            target_q = rewards + (1 - dones) * self.gamma * next_q

        loss = nn.functional.mse_loss(current_q, target_q)

        self.optimizer.zero_grad()

        loss.backward()

        self.optimizer.step()

        self.train_step_counter += 1

        if self.train_step_counter % 100 == 0:
            self.target_net.load_state_dict(self.q_net.state_dict())

        self.epsilon = max(
            self.epsilon_min,
            self.epsilon * self.epsilon_decay
        )