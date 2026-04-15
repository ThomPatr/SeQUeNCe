from pathlib import Path

NETWORK_CONFIG = Path(
    r"C:\Users\thoma\OneDrive\Desktop\Double degree\Internship\Internsheep\Simulator\SeQUeNCe\simulator\sixth_simulation\topology\ideal_topo_star.json"
)

PERIPHERAL_NODES = ["valrose", "sophia", "antibes", "grasse"]

TRAFFIC_MATRIX = {
    "valrose": {
        "sophia":  {"interval_s": 1.0, "memory_size": 1, "target_fidelity": 0.75},
        "antibes": {"interval_s": 1.4, "memory_size": 1, "target_fidelity": 0.75},
        "grasse":  {"interval_s": 1.8, "memory_size": 1, "target_fidelity": 0.75},
    },
    "sophia": {
        "valrose": {"interval_s": 1.1, "memory_size": 1, "target_fidelity": 0.75},
        "antibes": {"interval_s": 1.2, "memory_size": 1, "target_fidelity": 0.75},
        "grasse":  {"interval_s": 1.6, "memory_size": 1, "target_fidelity": 0.75},
    },
    "antibes": {
        "valrose": {"interval_s": 1.5, "memory_size": 1, "target_fidelity": 0.75},
        "sophia":  {"interval_s": 1.0, "memory_size": 1, "target_fidelity": 0.75},
        "grasse":  {"interval_s": 1.3, "memory_size": 1, "target_fidelity": 0.75},
    },
    "grasse": {
        "valrose": {"interval_s": 1.0, "memory_size": 1, "target_fidelity": 0.75},
        "sophia":  {"interval_s": 1.7, "memory_size": 1, "target_fidelity": 0.75},
        "antibes": {"interval_s": 1.4, "memory_size": 1, "target_fidelity": 0.75},
    }
}

NODE_HW = {
    "valrose": {"memo_freq": 8e3, "memo_expire": 0.05,  "memo_eff": 0.45, "base_fidelity": 0.90, "memo_stdev": 0.010},
    "ecov":    {"memo_freq": 8e3, "memo_expire": 0.06,  "memo_eff": 0.47, "base_fidelity": 0.91, "memo_stdev": 0.012},
    "sophia":  {"memo_freq": 8e3, "memo_expire": 0.05,  "memo_eff": 0.46, "base_fidelity": 0.90, "memo_stdev": 0.010},
    "antibes": {"memo_freq": 8e3, "memo_expire": 0.045, "memo_eff": 0.44, "base_fidelity": 0.89, "memo_stdev": 0.009},
    "grasse":  {"memo_freq": 8e3, "memo_expire": 0.04,  "memo_eff": 0.42, "base_fidelity": 0.88, "memo_stdev": 0.008},
}

BSM_HW = {
    "detector_efficiency": 0.55,
    "detector_count_rate": 2e7,
    "detector_resolution": 50,  # ps
}

LINK_PHYSICS = {
    tuple(sorted(("valrose", "sophia"))): {
        "base_alpha_db_per_km": 0.18,
        "extra_loss_db": 0.10,
    },
    tuple(sorted(("sophia", "ecov"))): {
        "base_alpha_db_per_km": 0.22,
        "extra_loss_db": 0.10,
    },
    tuple(sorted(("sophia", "antibes"))): {
        "base_alpha_db_per_km": 0.20,
        "extra_loss_db": 0.03,
    },
    tuple(sorted(("sophia", "grasse"))): {
        "base_alpha_db_per_km": 0.28,
        "extra_loss_db": 0.08,
    },
}

PHYSICAL_LINKS = set(LINK_PHYSICS.keys())
MEMORY_MODEL= "deterministic"  # "deterministic" or "random"
QC_FREQ = 5e6