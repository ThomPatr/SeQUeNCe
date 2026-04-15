from sequence.topology.router_net_topo import RouterNetTopo
from sequence.components.memory import MemoryArray
from config import NODE_HW, BSM_HW, LINK_PHYSICS, QC_FREQ
from physics.link_utils import endpoint_name, logical_link_from_qchannel, effective_attenuation_db_per_km




def set_parameters(topology: RouterNetTopo, use_random_coherence: bool = True):

    for node in topology.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER):
        hw = NODE_HW[node.name]
        memory_array = node.get_components_by_type(MemoryArray)[0]

        memory_array.update_memory_params("frequency", hw["memo_freq"])
        memory_array.update_memory_params("coherence_time", hw["memo_expire"])
        memory_array.update_memory_params("efficiency", hw["memo_eff"])
        memory_array.update_memory_params("raw_fidelity", hw["base_fidelity"])
        for memory in memory_array:
            if hasattr(memory, "coherence_time_stedv"):
                memory.coherence_time_stdev = hw["memo_stdev"]
        print(
            f"[SETUP] Node {node.name}: "
            f"freq={hw['memo_freq']}, "
            f"coherence={hw['memo_expire']}, "
            f"eff={hw['memo_eff']}, "
            f"raw_fidelity={hw['base_fidelity']:.4f}"
        )

    for node in topology.get_nodes_by_type(RouterNetTopo.BSM_NODE):
        bsm = node.get_components_by_type("SingleAtomBSM")[0]
        bsm.update_detectors_params("efficiency", BSM_HW["detector_efficiency"])
        bsm.update_detectors_params("count_rate", BSM_HW["detector_count_rate"])
        bsm.update_detectors_params("time_resolution", BSM_HW["detector_resolution"])

    for qc in topology.get_qchannels():
        logical_key = logical_link_from_qchannel(qc)

        if logical_key in LINK_PHYSICS:
            params = LINK_PHYSICS[logical_key]
            distance_m = qc.distance

            alpha_eff_db_per_km = effective_attenuation_db_per_km(
                params["base_alpha_db_per_km"],
                params["extra_loss_db"],
                distance_m
            )

            qc.attenuation = alpha_eff_db_per_km / 1000.0
            qc.frequency = QC_FREQ

            print(
                f"[SETUP] QLink {endpoint_name(qc.sender)} <-> {endpoint_name(qc.receiver)} "
                f"(logical {logical_key}): "
                f"distance={distance_m} m, "
                f"alpha_eff={alpha_eff_db_per_km:.4f} dB/km, "
                f"attenuation={qc.attenuation:.8f} dB/m, "
                f"freq={qc.frequency}"
            )
        else:
            qc.frequency = QC_FREQ
            print(
                f"[SETUP] QLink {endpoint_name(qc.sender)} <-> {endpoint_name(qc.receiver)} "
                f"(logical {logical_key}): using existing attenuation={qc.attenuation}"
            )