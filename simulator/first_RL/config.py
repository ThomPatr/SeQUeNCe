from pathlib import Path

NETWORK_CONFIG = Path(
    r"C:\Users\thoma\OneDrive\Desktop\Double degree\Internship\Internsheep\Simulator\SeQUeNCe\simulator\first_RL\topology\ideal_topo_star.json"
)

PERIPHERAL_NODES = [
    "valrose",
    "sophia",
    "antibes",
    "grasse",
    "cannes",
    "cagnes",
    "nice",
    "monaco",
    "menton"
]

TRAFFIC_MATRIX = {

    "valrose": {
        "menton": {
            "interval_s": 2.0,
            "memory_size": 2,
            "target_fidelity": 0.85
        },
        "monaco": {
            "interval_s": 3.0,
            "memory_size": 2,
            "target_fidelity": 0.85
        }
    },

    "cannes": {
        "menton": {
            "interval_s": 2.5,
            "memory_size": 2,
            "target_fidelity": 0.85
        }
    },

    "grasse": {
        "nice": {
            "interval_s": 1.8,
            "memory_size": 2,
            "target_fidelity": 0.85
        }
    },

    "ecov": {
    "valrose": {
        "interval_s": 2.4,
        "memory_size": 2,
        "target_fidelity": 0.85
    },
    "menton": {
        "interval_s": 2.8,
        "memory_size": 2,
        "target_fidelity": 0.85
    }
    },

    "antibes": {
        "monaco": {
            "interval_s": 2.2,
            "memory_size": 2,
            "target_fidelity": 0.85
        }
    }
}

NODE_HW = {
    "valrose": {"memo_freq": 8e3, "memo_expire": 0.050, "memo_eff": 0.45, "base_fidelity": 0.90, "memo_stdev": 0.010},
    "nice":    {"memo_freq": 8e3, "memo_expire": 0.055, "memo_eff": 0.46, "base_fidelity": 0.90, "memo_stdev": 0.011},
    "cagnes":  {"memo_freq": 8e3, "memo_expire": 0.048, "memo_eff": 0.44, "base_fidelity": 0.89, "memo_stdev": 0.010},
    "antibes": {"memo_freq": 8e3, "memo_expire": 0.045, "memo_eff": 0.44, "base_fidelity": 0.89, "memo_stdev": 0.009},
    "sophia":  {"memo_freq": 8e3, "memo_expire": 0.060, "memo_eff": 0.47, "base_fidelity": 0.91, "memo_stdev": 0.012},
    "grasse":  {"memo_freq": 8e3, "memo_expire": 0.040, "memo_eff": 0.42, "base_fidelity": 0.88, "memo_stdev": 0.008},
    "cannes":  {"memo_freq": 8e3, "memo_expire": 0.043, "memo_eff": 0.43, "base_fidelity": 0.88, "memo_stdev": 0.009},
    "monaco":  {"memo_freq": 8e3, "memo_expire": 0.052, "memo_eff": 0.45, "base_fidelity": 0.90, "memo_stdev": 0.010},
    "menton":  {"memo_freq": 8e3, "memo_expire": 0.038, "memo_eff": 0.41, "base_fidelity": 0.87, "memo_stdev": 0.008},
    "ecov":     {"memo_freq": 8e3,"memo_expire": 0.060,"memo_eff": 0.47,"base_fidelity": 0.91,"memo_stdev": 0.012},
}
 
BSM_HW = { 
    "detector_efficiency": 0.55, 
    "detector_count_rate": 2e7, 
    "detector_resolution": 50,  # ps 
} 
 
LINK_PHYSICS = { 
    tuple(sorted(("valrose", "nice"))): { 
        "base_alpha_db_per_km": 0.18, 
        "extra_loss_db": 0.04, 
    }, 
    tuple(sorted(("nice", "cagnes"))): { 
        "base_alpha_db_per_km": 0.20, 
        "extra_loss_db": 0.05,
    },
    tuple(sorted(("cagnes", "antibes"))): {
        "base_alpha_db_per_km": 0.20,
        "extra_loss_db": 0.04,
    },
    tuple(sorted(("antibes", "sophia"))): {
        "base_alpha_db_per_km": 0.20,
        "extra_loss_db": 0.03,
    },
    tuple(sorted(("sophia", "grasse"))): {
        "base_alpha_db_per_km": 0.28,
        "extra_loss_db": 0.08,
    },

    tuple(sorted(("cagnes", "cannes"))): {
        "base_alpha_db_per_km": 0.22,
        "extra_loss_db": 0.07,
    },
    tuple(sorted(("cannes", "antibes"))): {
        "base_alpha_db_per_km": 0.21,
        "extra_loss_db": 0.06,
    },

    tuple(sorted(("nice", "monaco"))): {
        "base_alpha_db_per_km": 0.21,
        "extra_loss_db": 0.08,
    },
    tuple(sorted(("monaco", "menton"))): {
        "base_alpha_db_per_km": 0.22,
        "extra_loss_db": 0.06,
    },
    tuple(sorted(("menton", "sophia"))): {
        "base_alpha_db_per_km": 0.24,
        "extra_loss_db": 0.12,
    },

    tuple(sorted(("nice", "sophia"))): {
        "base_alpha_db_per_km": 0.19,
        "extra_loss_db": 0.06,
    },
    tuple(sorted(("sophia", "ecov"))): {
    "base_alpha_db_per_km": 0.22,
    "extra_loss_db": 0.10,
},

tuple(sorted(("ecov", "cannes"))): {
    "base_alpha_db_per_km": 0.23,
    "extra_loss_db": 0.08,
},
}

PHYSICAL_LINKS = set(LINK_PHYSICS.keys())
MEMORY_MODEL= "random"  # "deterministic" or "random"
QC_FREQ = 5e6