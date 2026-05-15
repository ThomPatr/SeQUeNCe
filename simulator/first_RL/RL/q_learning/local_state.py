import numpy as np


def residual_ttl(memory, now_ps: int) -> float:
    """
    Returns normalized residual TTL in [0, 1].
    """
    coherence_time_s = getattr(memory, "coherence_time", 0.0)

    if coherence_time_s <= 0:
        return 0.0

    coherence_ps = coherence_time_s * 1e12

    entangle_time = getattr(memory, "entangle_time", None)
    if entangle_time is None or entangle_time < 0:
        return 0.0

    age_ps = now_ps - entangle_time
    ttl = max(0.0, coherence_ps - age_ps)

    return min(1.0, ttl / coherence_ps)


def build_local_observation(left_memory, right_memory, now_ps: int, target_fidelity: float):
    """
    Local observation used by the RL agent.

    obs = [
        fidelity_left,
        fidelity_right,
        ttl_left,
        ttl_right,
        min_ttl,
        predicted_swap_fidelity,
        target_fidelity
    ]
    """

    f_left = getattr(left_memory, "fidelity", 0.0) or 0.0
    f_right = getattr(right_memory, "fidelity", 0.0) or 0.0

    ttl_left = residual_ttl(left_memory, now_ps)
    ttl_right = residual_ttl(right_memory, now_ps)

    min_ttl = min(ttl_left, ttl_right)

    predicted_swap_fidelity = f_left * f_right

    return np.array([
        f_left,
        f_right,
        ttl_left,
        ttl_right,
        min_ttl,
        predicted_swap_fidelity,
        target_fidelity
    ], dtype=np.float32) 