import numpy as np
from stable_baselines3 import DQN

WAIT = 0
SWAP = 1


class DQNSwapController:
    def __init__(self, model_path: str, target_fidelity: float = 0.75):
        self.model = DQN.load(model_path)
        self.target_fidelity = target_fidelity

    def residual_ttl(self, memory, now_ps: int) -> float:
        coherence_time = getattr(memory, "coherence_time", 0.0)

        if coherence_time <= 0:
            return 0.0

        entangle_time = getattr(memory, "entangle_time", None)

        if entangle_time is None or entangle_time < 0:
            return 0.0

        coherence_ps = coherence_time * 1e12
        age_ps = now_ps - entangle_time
        ttl_ps = max(0.0, coherence_ps - age_ps)

        return min(1.0, ttl_ps / coherence_ps)

    def build_observation(self, node, left_memory, right_memory):
        now_ps = node.timeline.now()

        f_left = getattr(left_memory, "fidelity", 0.0) or 0.0
        f_right = getattr(right_memory, "fidelity", 0.0) or 0.0

        ttl_left = self.residual_ttl(left_memory, now_ps)
        ttl_right = self.residual_ttl(right_memory, now_ps)

        memory_array = node.get_components_by_type("MemoryArray")[0]

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

        action, _ = self.model.predict(obs, deterministic=True)

        pred_f = obs[0] * obs[1]
        min_ttl = min(obs[2], obs[3])

        action = int(action)

        print(
            f"[DQN][{node.name}][{node.timeline.now() * 1e-12:.6f}s] "
            f"F_left={obs[0]:.4f}, F_right={obs[1]:.4f}, "
            f"pred_F={pred_f:.4f}, "
            f"TTL_left={obs[2]:.4f}, TTL_right={obs[3]:.4f}, "
            f"min_TTL={min_ttl:.4f}, "
            f"free_mem={obs[4]:.4f}, "
            f"action={'WAIT' if action == WAIT else 'SWAP'}"
        )

        return action == SWAP