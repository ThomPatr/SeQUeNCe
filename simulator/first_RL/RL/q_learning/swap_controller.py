from simulator.first_RL.rl.actions import WAIT, SWAP, ACTION_NAMES
from simulator.first_RL.rl.local_state import build_local_observation


class LocalSwapController:
    """
    Controller placed on a repeater.

    It does not choose the path.
    The path is still the standard shortest path / SeQUeNCe path.

    It only decides whether the repeater should:
    - WAIT
    - SWAP
    """

    def __init__(
        self,
        node,
        agent,
        target_fidelity=0.75,
        wait_penalty=-0.01,
        bad_swap_penalty=-0.2,
        success_bonus=1.0,
    ):
        self.node = node
        self.agent = agent

        self.target_fidelity = target_fidelity
        self.wait_penalty = wait_penalty
        self.bad_swap_penalty = bad_swap_penalty
        self.success_bonus = success_bonus

        self.last_obs = None
        self.last_action = None

    def decide(self, left_memory, right_memory):
        now_ps = self.node.timeline.now()

        obs = build_local_observation(
            left_memory=left_memory,
            right_memory=right_memory,
            now_ps=now_ps,
            target_fidelity=self.target_fidelity,
        )

        action = self.agent.act(obs)

        self.last_obs = obs
        self.last_action = action

        print(
            f"[RL][{self.node.name}][{now_ps * 1e-12:.6f}s] "
            f"action={ACTION_NAMES[action]}, "
            f"f_left={obs[0]:.4f}, f_right={obs[1]:.4f}, "
            f"ttl_left={obs[2]:.4f}, ttl_right={obs[3]:.4f}, "
            f"predicted_f_swap={obs[5]:.4f}"
        )

        return action == SWAP

    def reward_after_decision(self, left_memory, right_memory, swapped: bool):
        now_ps = self.node.timeline.now()

        next_obs = build_local_observation(
            left_memory=left_memory,
            right_memory=right_memory,
            now_ps=now_ps,
            target_fidelity=self.target_fidelity,
        )

        predicted_fidelity = next_obs[5]
        residual_ttl = next_obs[4]

        if not swapped:
            reward = self.wait_penalty
            done = False

        else:
            if predicted_fidelity >= self.target_fidelity:
                reward = self.success_bonus + residual_ttl
            else:
                reward = self.bad_swap_penalty

            done = True

        if self.last_obs is not None and self.last_action is not None:
            self.agent.update(
                obs=self.last_obs,
                action=self.last_action,
                reward=reward,
                next_obs=next_obs,
                done=done,
            )

        print(
            f"[RL][{self.node.name}] reward={reward:.4f}, "
            f"epsilon={self.agent.epsilon:.4f}"
        )

        return reward