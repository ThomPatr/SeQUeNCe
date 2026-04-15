from sequence.components.memory import Memory, MemoryWithRandomCoherenceTime
from sequence.components.memory import MemoryArray as BaseMemoryArray
from config import MEMORY_MODEL, NODE_HW

class CustomMemoryArray(BaseMemoryArray):
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
        node_name= name.split(".")[0]
        hw = NODE_HW[node_name]
        for i in range(num_memories):
            memory_name = self.name + f"[{i}]"
            self.memory_name_to_index[memory_name] = i

            if MEMORY_MODEL == "random":
                memory = MemoryWithRandomCoherenceTime(
                    memory_name,
                    timeline,
                    fidelity,
                    frequency,
                    efficiency,
                    coherence_time,
                    hw["memo_stdev"],
                    wavelength,
                )
            else:
                memory = Memory(
                    memory_name,
                    timeline,
                    fidelity,
                    frequency,
                    efficiency,
                    coherence_time,
                    wavelength,
                    decoherence_errors,
                    cutoff_ratio,
                    cutoff_flag,
                )

            memory.attach(self)
            self.memories.append(memory)
            memory.set_memory_array(self)