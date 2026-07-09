import numpy as np

from simulator.first_RL.RL.online_dqn_agent import OnlineDQNAgent

AGENT = OnlineDQNAgent()

WAIT = 0
SWAP = 1


class DQNSwapController:
    def __init__(self, target_fidelity: float = 0.75):
        self.target_fidelity = target_fidelity
        self.pending_wait = {}
    
    def _pair_key(self, node, left_memory, right_memory):
        return (
            node.name,
            tuple(sorted([left_memory.name, right_memory.name]))
        )

    def residual_ttl(self, memory, now_ps: int) -> float:
        if not hasattr(self, "_printed_memory_debug"):
            print(memory.__dict__)
            self._printed_memory_debug = True
        coherence_time = getattr(memory, "coherence_time", 0.0)

        if coherence_time <= 0:
            return 0.0

        coherence_ps = coherence_time * 1e12

        entangle_time = getattr(memory, "entangle_time", None)

        if entangle_time is None or entangle_time < 0:
            entangled_memory = getattr(memory, "entangled_memory", {})
            entangle_time = entangled_memory.get("entangle_time", None)

        if entangle_time is None or entangle_time < 0:
            return 1.0

        age_ps = now_ps - entangle_time
        ttl_ps = max(0.0, coherence_ps - age_ps)

        return max(0.0, min(1.0, ttl_ps / coherence_ps))

    def _get_memory_array(self, node):
        for component in node.components.values():
            if "MemoryArray" in component.name:
                return component

        for component in node.components.values():
            if hasattr(component, "__iter__") and hasattr(component, "__len__"):
                try:
                    if len(component) > 0 and hasattr(component[0], "fidelity"):
                        return component
                except Exception:
                    pass

        return None

    def build_observation(self, node, left_memory, right_memory):
        now_ps = node.timeline.now()

        f_left = getattr(left_memory, "fidelity", 0.0) or 0.0
        f_right = getattr(right_memory, "fidelity", 0.0) or 0.0

        ttl_left = self.residual_ttl(left_memory, now_ps)
        ttl_right = self.residual_ttl(right_memory, now_ps)

        memory_array = self._get_memory_array(node)

        if memory_array is None:
            free_memory_ratio = 0.0
        else:
            free = 0
            total = len(memory_array)

            for mem in memory_array:
                if getattr(mem, "entangled_memory", {}).get("node_id") is None:
                    free += 1

            free_memory_ratio = free / total if total > 0 else 0.0

        obs = np.array(
            [
                f_left,
                f_right,
                ttl_left,
                ttl_right,
                free_memory_ratio,
            ],
            dtype=np.float32,
        )

        return obs

    def decide(self, node, left_memory, right_memory):
        obs = self.build_observation(node, left_memory, right_memory)

        pair_key = self._pair_key(node, left_memory, right_memory)

        diagnostic_swap_f = obs[0] * obs[1]
        min_ttl = min(obs[2], obs[3])

        # Close previous pending WAIT transition, if any
        if pair_key in self.pending_wait:
            old_obs = self.pending_wait.pop(pair_key)

            old_quality = old_obs[0] * old_obs[1]
            new_quality = obs[0] * obs[1]

            old_ttl = min(old_obs[2], old_obs[3])
            new_ttl = min(obs[2], obs[3])

            wait_reward = (
                2.0 * (new_quality - old_quality)
                + 0.5 * (new_ttl - old_ttl)
                - 0.01
            )

            AGENT.store_transition(
                old_obs,
                WAIT,
                wait_reward,
                obs,
                False
            )
            AGENT.train_step()

            print(
                f"[ONLINE-DQN][WAIT-CLOSED][{node.name}][{node.timeline.now() * 1e-12:.6f}s] "
                f"old_quality={old_quality:.4f}, new_quality={new_quality:.4f}, "
                f"old_ttl={old_ttl:.4f}, new_ttl={new_ttl:.4f}, "
                f"wait_reward={wait_reward:.4f}, "
                f"buffer={len(AGENT.replay_buffer)}, "
                f"epsilon={AGENT.epsilon:.4f}"
            )

        action = AGENT.act(obs)

        if action == WAIT:
            self.pending_wait[pair_key] = obs

            print(
                f"[ONLINE-DQN][{node.name}][{node.timeline.now() * 1e-12:.6f}s] "
                f"F_left={obs[0]:.4f}, F_right={obs[1]:.4f}, "
                f"diagnostic_swap_F={diagnostic_swap_f:.4f}, "
                f"TTL_left={obs[2]:.4f}, TTL_right={obs[3]:.4f}, "
                f"min_TTL={min_ttl:.4f}, "
                f"free_mem={obs[4]:.4f}, "
                f"epsilon={AGENT.epsilon:.4f}, "
                f"action=WAIT_PENDING"
            )

            return False

        # SWAP reward: local threshold-centered reward
        quality = diagnostic_swap_f - self.target_fidelity

        reward = (
            6.0 * quality
            + 0.5 * min_ttl
            - 0.3
        )

        next_obs = self.build_observation(node, left_memory, right_memory)

        AGENT.store_transition(
            obs,
            SWAP,
            reward,
            next_obs,
            True
        )
        AGENT.train_step()

        print(
            f"[ONLINE-DQN][{node.name}][{node.timeline.now() * 1e-12:.6f}s] "
            f"F_left={obs[0]:.4f}, F_right={obs[1]:.4f}, "
            f"diagnostic_swap_F={diagnostic_swap_f:.4f}, "
            f"TTL_left={obs[2]:.4f}, TTL_right={obs[3]:.4f}, "
            f"min_TTL={min_ttl:.4f}, "
            f"free_mem={obs[4]:.4f}, "
            f"epsilon={AGENT.epsilon:.4f}, "
            f"reward={reward:.4f}, "
            f"buffer={len(AGENT.replay_buffer)}, "
            f"action=SWAP"
        )

        return True