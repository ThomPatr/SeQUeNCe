from sequence.components.memory import Memory, MemoryWithRandomCoherenceTime
from sequence.components.memory import MemoryArray as BaseMemoryArray

from simulator.first_RL.config import MEMORY_MODEL, NODE_HW


class CustomMemoryArray(BaseMemoryArray):
    """
    Custom memory array with node-specific hardware parameters.

    Each quantum router receives memories whose fidelity, frequency,
    efficiency and coherence time are taken from NODE_HW.
    """

    def __init__(
        self,
        name,
        timeline,
        num_memories=10,
        fidelity=0.85,
        frequency=80e6,
        efficiency=1,
        coherence_time=-1,
        wavelength=500,
        decoherence_errors=None,
        cutoff_ratio=1,
        cutoff_flag=True,
    ):
        super().__init__(name, timeline, num_memories=0)

        self.memories = []
        self.memory_name_to_index = {}

        node_name = name.split(".")[0]

        if node_name not in NODE_HW:
            raise KeyError(f"No hardware configuration found for node '{node_name}'")

        hw = NODE_HW[node_name]

        memo_fidelity = hw["base_fidelity"]
        memo_frequency = hw["memo_freq"]
        memo_efficiency = hw["memo_eff"]
        memo_coherence_time = hw["memo_expire"]
        memo_stdev = hw.get("memo_stdev", 0.0)

        for i in range(num_memories):
            memory_name = f"{self.name}[{i}]"
            self.memory_name_to_index[memory_name] = i

            if MEMORY_MODEL == "random":
                memory = MemoryWithRandomCoherenceTime(
                    memory_name,
                    timeline,
                    memo_fidelity,
                    memo_frequency,
                    memo_efficiency,
                    memo_coherence_time,
                    memo_stdev,
                    wavelength,
                )

            elif MEMORY_MODEL == "deterministic":
                memory = Memory(
                    memory_name,
                    timeline,
                    memo_fidelity,
                    memo_frequency,
                    memo_efficiency,
                    memo_coherence_time,
                    wavelength,
                    decoherence_errors,
                    cutoff_ratio,
                    cutoff_flag,
                )

            else:
                raise ValueError(
                    f"Unknown MEMORY_MODEL='{MEMORY_MODEL}'. "
                    "Expected 'random' or 'deterministic'."
                )

            memory.attach(self)
            memory.set_memory_array(self)
            self.memories.append(memory)