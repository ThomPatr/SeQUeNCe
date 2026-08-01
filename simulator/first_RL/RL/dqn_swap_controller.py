from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Hashable

import numpy as np

from simulator.first_RL.RL.online_dqn_agent import OnlineDQNAgent
from simulator.first_RL.metrics.link_metrics import LINK_METRICS
from simulator.first_RL.physics.link_utils import normalize_link


WAIT = 0
SWAP = 1


@dataclass
class PendingDecision:
    state: np.ndarray
    action: int
    decision_time_ps: int
    node_name: str
    left_memory_name: str
    right_memory_name: str
    target_fidelity: float
    reservation_identity: Hashable | None
    source: str | None
    destination: str | None


class DQNSwapController:
    """
    Local DQN controller for WAIT/SWAP decisions.

    The policy does not observe the fidelity of the entangled pairs.
    Fidelity and simulator-internal information may be used only to compute
    the training reward after the real outcome of an action is known.

    A controller can receive:
      - one shared agent used by all nodes, or
      - one independent agent for each node.
    """

    def __init__(
        self,
        agent: OnlineDQNAgent,
        default_target_fidelity: float = 0.60,
        wait_penalty_per_second: float = 0.05,
        swap_failure_penalty: float = -1.0,
        swap_success_reward: float = 0.4,
        end_to_end_reward: float = 2.0,
        fidelity_reward: float = 1.0,
        fidelity_margin_weight: float = 0.5,
        expiration_penalty: float = -1.0,
        discard_penalty: float = -0.6,
        reservation_end_penalty: float = -0.8,
    ):
        self.agent = agent

        self.default_target_fidelity = float(
            default_target_fidelity
        )

        self.wait_penalty_per_second = float(
            wait_penalty_per_second
        )
        self.swap_failure_penalty = float(
            swap_failure_penalty
        )
        self.swap_success_reward = float(
            swap_success_reward
        )
        self.end_to_end_reward = float(
            end_to_end_reward
        )
        self.fidelity_reward = float(
            fidelity_reward
        )
        self.fidelity_margin_weight = float(
            fidelity_margin_weight
        )

        self.expiration_penalty = float(
            expiration_penalty
        )
        self.discard_penalty = float(
            discard_penalty
        )
        self.reservation_end_penalty = float(
            reservation_end_penalty
        )

        self.pending_wait: dict[
            Hashable,
            PendingDecision,
        ] = {}

        self.pending_swap: dict[
            Hashable,
            PendingDecision,
        ] = {}

        self.completed_waits = 0
        self.completed_swaps = 0

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _remote_node(memory) -> str | None:
        entangled_memory = getattr(
            memory,
            "entangled_memory",
            {},
        )

        if isinstance(
            entangled_memory,
            dict,
        ):
            remote = entangled_memory.get(
                "node_id"
            )

            if remote is not None:
                return str(remote)

        remote = getattr(
            memory,
            "remote_node",
            None,
        )

        if remote is not None:
            return str(remote)

        return None

    @staticmethod
    def _memory_name(memory) -> str:
        return str(
            getattr(
                memory,
                "name",
                id(memory),
            )
        )

    @staticmethod
    def _memory_entangle_time(memory) -> int | None:
        entangle_time = getattr(
            memory,
            "entangle_time",
            None,
        )

        if (
            entangle_time is not None
            and entangle_time >= 0
        ):
            return int(entangle_time)

        entangled_memory = getattr(
            memory,
            "entangled_memory",
            {},
        )

        if isinstance(
            entangled_memory,
            dict,
        ):
            entangle_time = (
                entangled_memory.get(
                    "entangle_time"
                )
            )

        if (
            entangle_time is not None
            and entangle_time >= 0
        ):
            return int(entangle_time)

        return None

    @staticmethod
    def _extract_reservation(protocol=None, reservation=None):
        if reservation is not None:
            return reservation

        if protocol is None:
            return None

        tracking_reservation = getattr(
            protocol,
            "tracking_reservation",
            None,
        )

        if tracking_reservation is not None:
            return tracking_reservation

        return getattr(
            protocol,
            "reservation",
            None,
        )

    def _reservation_metadata(
        self,
        protocol=None,
        reservation=None,
    ) -> dict[str, Any]:
        reservation = self._extract_reservation(
            protocol=protocol,
            reservation=reservation,
        )

        if reservation is None:
            return {
                "reservation": None,
                "identity": None,
                "source": None,
                "destination": None,
                "target_fidelity":
                    self.default_target_fidelity,
                "end_time_ps": None,
            }

        identity = getattr(
            reservation,
            "identity",
            None,
        )

        if identity is None:
            identity = id(reservation)

        source = getattr(
            reservation,
            "initiator",
            None,
        )
        destination = getattr(
            reservation,
            "responder",
            None,
        )

        target_fidelity = getattr(
            reservation,
            "fidelity",
            self.default_target_fidelity,
        )

        end_time_ps = getattr(
            reservation,
            "end_time",
            None,
        )

        return {
            "reservation": reservation,
            "identity": identity,
            "source": (
                str(source)
                if source is not None
                else None
            ),
            "destination": (
                str(destination)
                if destination is not None
                else None
            ),
            "target_fidelity":
                float(target_fidelity),
            "end_time_ps": end_time_ps,
        }

    # ------------------------------------------------------------------
    # Decision identity
    # ------------------------------------------------------------------

    def _pair_key(
        self,
        node,
        left_memory,
        right_memory,
        reservation_identity=None,
    ) -> tuple:
        """
        Identify the actual entanglement instances.

        Memory names alone are not sufficient because the same slots may be
        reused by different pairs and reservations.
        """
        left_record = (
            self._memory_name(left_memory),
            self._remote_node(left_memory),
            self._memory_entangle_time(
                left_memory
            ),
        )

        right_record = (
            self._memory_name(right_memory),
            self._remote_node(right_memory),
            self._memory_entangle_time(
                right_memory
            ),
        )

        pair_records = tuple(
            sorted(
                (
                    left_record,
                    right_record,
                ),
                key=str,
            )
        )

        return (
            str(node.name),
            reservation_identity,
            pair_records,
        )

    # ------------------------------------------------------------------
    # Physical observation
    # ------------------------------------------------------------------

    def residual_lifetime(
        self,
        memory,
        now_ps: int,
    ) -> float:
        coherence_time = getattr(
            memory,
            "coherence_time",
            0.0,
        )

        if (
            coherence_time is None
            or coherence_time <= 0
        ):
            return 1.0

        coherence_ps = (
            float(coherence_time)
            * 1e12
        )

        entangle_time = (
            self._memory_entangle_time(
                memory
            )
        )

        if entangle_time is None:
            return 1.0

        age_ps = max(
            0.0,
            float(now_ps - entangle_time),
        )

        remaining_ps = max(
            0.0,
            coherence_ps - age_ps,
        )

        return float(
            np.clip(
                remaining_ps
                / coherence_ps,
                0.0,
                1.0,
            )
        )

    @staticmethod
    def _get_memory_array(node):
        for component in node.components.values():
            component_name = str(
                getattr(
                    component,
                    "name",
                    "",
                )
            )

            if "MemoryArray" in component_name:
                return component

        for component in node.components.values():
            if not (
                hasattr(component, "__iter__")
                and hasattr(component, "__len__")
            ):
                continue

            try:
                if (
                    len(component) > 0
                    and hasattr(
                        component[0],
                        "entangled_memory",
                    )
                ):
                    return component
            except Exception:
                continue

        return None

    @staticmethod
    def _memory_is_available(
        node,
        memory,
    ) -> bool:
        """
        Prefer ResourceManager MemoryInfo when available.

        Fallback: a memory is considered free only when it is not entangled.
        """
        resource_manager = getattr(
            node,
            "resource_manager",
            None,
        )

        memory_manager = getattr(
            resource_manager,
            "memory_manager",
            None,
        )

        if memory_manager is not None:
            try:
                memory_info = (
                    memory_manager.get_info_by_memory(
                        memory
                    )
                )

                state = str(
                    getattr(
                        memory_info,
                        "state",
                        "",
                    )
                ).upper()

                reservation = getattr(
                    memory_info,
                    "reservation",
                    None,
                )

                return (
                    state == "RAW"
                    and reservation is None
                )
            except Exception:
                pass

        entangled_memory = getattr(
            memory,
            "entangled_memory",
            {},
        )

        if isinstance(
            entangled_memory,
            dict,
        ):
            return (
                entangled_memory.get(
                    "node_id"
                )
                is None
            )

        return (
            getattr(
                memory,
                "remote_node",
                None,
            )
            is None
        )

    def _free_memory_ratio(
        self,
        node,
    ) -> float:
        memory_array = self._get_memory_array(
            node
        )

        if memory_array is None:
            return 0.0

        total = len(memory_array)

        if total <= 0:
            return 0.0

        free = sum(
            1
            for memory in memory_array
            if self._memory_is_available(
                node,
                memory,
            )
        )

        return float(
            free / total
        )

    @staticmethod
    def _is_physical_link(
        local_node: str,
        remote_node: str | None,
    ) -> bool:
        if remote_node is None:
            return False

        key = normalize_link(
            local_node,
            remote_node,
        )

        return key in LINK_METRICS

    def _empirical_link_success_rate(
        self,
        local_node: str,
        remote_node: str | None,
    ) -> float:
        """
        Return a smoothed empirical probability only when LINK_METRICS
        contains an elementary physical-link record.

        A long-distance entangled pair created through swapping must not be
        treated as a direct physical link.
        """
        if remote_node is None:
            return 0.5

        key = normalize_link(
            local_node,
            remote_node,
        )

        statistics = LINK_METRICS.get(
            key
        )

        if statistics is None:
            return 0.5

        attempts = float(
            statistics.get(
                "eg_attempts",
                0,
            )
        )
        successes = float(
            statistics.get(
                "eg_successes",
                0,
            )
        )

        prior_successes = 1.0
        prior_attempts = 2.0

        return float(
            np.clip(
                (
                    successes
                    + prior_successes
                )
                / (
                    attempts
                    + prior_attempts
                ),
                0.0,
                1.0,
            )
        )

    @staticmethod
    def _normalized_reservation_time_left(
        node,
        reservation_end_time_ps,
    ) -> float:
        if reservation_end_time_ps is None:
            return 1.0

        now_ps = int(
            node.timeline.now()
        )

        remaining_ps = max(
            0,
            int(reservation_end_time_ps)
            - now_ps,
        )

        # A bounded monotonic transform avoids requiring a fixed global
        # reservation duration in the observation.
        remaining_s = (
            remaining_ps
            * 1e-12
        )

        return float(
            remaining_s
            / (
                1.0
                + remaining_s
            )
        )

    @staticmethod
    def _destination_flags(
        left_remote: str | None,
        right_remote: str | None,
        destination: str | None,
    ) -> tuple[float, float]:
        if destination is None:
            return 0.0, 0.0

        return (
            float(left_remote == destination),
            float(right_remote == destination),
        )

    def build_observation(
        self,
        node,
        left_memory,
        right_memory,
        protocol=None,
        reservation=None,
    ) -> np.ndarray:
        metadata = self._reservation_metadata(
            protocol=protocol,
            reservation=reservation,
        )

        now_ps = int(
            node.timeline.now()
        )

        ttl_left = self.residual_lifetime(
            left_memory,
            now_ps,
        )
        ttl_right = self.residual_lifetime(
            right_memory,
            now_ps,
        )

        ttl_imbalance = abs(
            ttl_left - ttl_right
        )

        free_memory_ratio = (
            self._free_memory_ratio(
                node
            )
        )

        left_remote = self._remote_node(
            left_memory
        )
        right_remote = self._remote_node(
            right_memory
        )

        eg_success_left = (
            self._empirical_link_success_rate(
                node.name,
                left_remote,
            )
        )
        eg_success_right = (
            self._empirical_link_success_rate(
                node.name,
                right_remote,
            )
        )

        reservation_time_left = (
            self._normalized_reservation_time_left(
                node,
                metadata["end_time_ps"],
            )
        )

        destination_left, destination_right = (
            self._destination_flags(
                left_remote,
                right_remote,
                metadata["destination"],
            )
        )

        # Fidelity is intentionally not included.
        observation = np.array(
            [
                ttl_left,
                ttl_right,
                ttl_imbalance,
                free_memory_ratio,
                eg_success_left,
                eg_success_right,
                reservation_time_left,
                destination_left,
                destination_right,
            ],
            dtype=np.float32,
        )

        return observation

    # ------------------------------------------------------------------
    # Transition storage
    # ------------------------------------------------------------------

    def _store_transition(
        self,
        state: np.ndarray,
        action: int,
        reward: float,
        next_state: np.ndarray,
        done: bool,
    ) -> None:
        self.agent.store_transition(
            state,
            int(action),
            float(reward),
            next_state,
            bool(done),
        )

        self.agent.train_step()

    @staticmethod
    def _terminal_state_like(
        state: np.ndarray,
    ) -> np.ndarray:
        return np.zeros_like(
            state,
            dtype=np.float32,
        )

    # ------------------------------------------------------------------
    # WAIT handling
    # ------------------------------------------------------------------

    def _close_previous_wait_on_new_decision(
        self,
        pair_key,
        current_observation: np.ndarray,
        now_ps: int,
    ) -> None:
        pending = self.pending_wait.pop(
            pair_key,
            None,
        )

        if pending is None:
            return

        elapsed_s = max(
            0.0,
            (
                now_ps
                - pending.decision_time_ps
            )
            * 1e-12,
        )

        reward = (
            -self.wait_penalty_per_second
            * elapsed_s
        )

        self._store_transition(
            state=pending.state,
            action=WAIT,
            reward=reward,
            next_state=current_observation,
            done=False,
        )

        self.completed_waits += 1

    def close_wait_as_terminal(
        self,
        pair_key,
        reason: str,
        end_time_ps: int,
    ) -> bool:
        pending = self.pending_wait.pop(
            pair_key,
            None,
        )

        if pending is None:
            return False

        elapsed_s = max(
            0.0,
            (
                int(end_time_ps)
                - pending.decision_time_ps
            )
            * 1e-12,
        )

        reason_penalty = {
            "memory_expired":
                self.expiration_penalty,
            "pair_discarded":
                self.discard_penalty,
            "reservation_ended":
                self.reservation_end_penalty,
            "simulation_end":
                0.0,
        }.get(
            reason,
            self.discard_penalty,
        )

        reward = (
            reason_penalty
            - self.wait_penalty_per_second
            * elapsed_s
        )

        self._store_transition(
            state=pending.state,
            action=WAIT,
            reward=reward,
            next_state=self._terminal_state_like(
                pending.state
            ),
            done=True,
        )

        self.completed_waits += 1

        return True

    # ------------------------------------------------------------------
    # SWAP handling
    # ------------------------------------------------------------------

    def _build_pending_decision(
        self,
        state: np.ndarray,
        action: int,
        node,
        left_memory,
        right_memory,
        metadata: dict[str, Any],
    ) -> PendingDecision:
        return PendingDecision(
            state=state.copy(),
            action=int(action),
            decision_time_ps=int(
                node.timeline.now()
            ),
            node_name=str(node.name),
            left_memory_name=(
                self._memory_name(
                    left_memory
                )
            ),
            right_memory_name=(
                self._memory_name(
                    right_memory
                )
            ),
            target_fidelity=float(
                metadata[
                    "target_fidelity"
                ]
            ),
            reservation_identity=(
                metadata["identity"]
            ),
            source=metadata["source"],
            destination=metadata[
                "destination"
            ],
        )

    def _swap_reward(
        self,
        pending: PendingDecision,
        success: bool,
        output_fidelity: float | None,
        end_to_end: bool,
        result_time_ps: int,
    ) -> float:
        elapsed_s = max(
            0.0,
            (
                int(result_time_ps)
                - pending.decision_time_ps
            )
            * 1e-12,
        )

        if not success:
            return (
                self.swap_failure_penalty
                - self.wait_penalty_per_second
                * elapsed_s
            )

        reward = (
            self.swap_success_reward
            - self.wait_penalty_per_second
            * elapsed_s
        )

        if end_to_end:
            reward += (
                self.end_to_end_reward
            )

        if output_fidelity is not None:
            fidelity_margin = (
                float(output_fidelity)
                - pending.target_fidelity
            )

            reward += (
                self.fidelity_margin_weight
                * float(
                    np.clip(
                        fidelity_margin,
                        -1.0,
                        1.0,
                    )
                )
            )

            if (
                output_fidelity
                >= pending.target_fidelity
            ):
                reward += (
                    self.fidelity_reward
                )

        return float(reward)

    def on_swap_result(
        self,
        decision_key,
        node,
        success: bool,
        output_fidelity: float | None = None,
        end_to_end: bool = False,
        next_left_memory=None,
        next_right_memory=None,
        protocol=None,
        reservation=None,
        terminal: bool | None = None,
    ) -> bool:
        """
        Close a pending SWAP decision after the real protocol result.

        This method must be called by the swapping protocol or by the
        instrumentation callback that observes its completion.
        """
        pending = self.pending_swap.pop(
            decision_key,
            None,
        )

        if pending is None:
            return False

        now_ps = int(
            node.timeline.now()
        )

        reward = self._swap_reward(
            pending=pending,
            success=bool(success),
            output_fidelity=(
                output_fidelity
            ),
            end_to_end=bool(
                end_to_end
            ),
            result_time_ps=now_ps,
        )

        if terminal is None:
            terminal = (
                not success
                or end_to_end
                or next_left_memory is None
                or next_right_memory is None
            )

        if terminal:
            next_state = (
                self._terminal_state_like(
                    pending.state
                )
            )
        else:
            next_state = self.build_observation(
                node=node,
                left_memory=next_left_memory,
                right_memory=next_right_memory,
                protocol=protocol,
                reservation=reservation,
            )

        self._store_transition(
            state=pending.state,
            action=SWAP,
            reward=reward,
            next_state=next_state,
            done=bool(terminal),
        )

        self.completed_swaps += 1

        print(
            f"[ONLINE-DQN][SWAP-CLOSED]"
            f"[{node.name}] "
            f"success={success}, "
            f"end_to_end={end_to_end}, "
            f"output_fidelity="
            f"{output_fidelity}, "
            f"reward={reward:.4f}, "
            f"done={terminal}, "
            f"epsilon="
            f"{self.agent.epsilon:.4f}"
        )

        return True

    def close_swap_as_terminal(
        self,
        decision_key,
        node,
        reason: str,
    ) -> bool:
        pending = self.pending_swap.pop(
            decision_key,
            None,
        )

        if pending is None:
            return False

        reason_penalty = {
            "memory_expired":
                self.expiration_penalty,
            "pair_discarded":
                self.discard_penalty,
            "reservation_ended":
                self.reservation_end_penalty,
            "simulation_end":
                0.0,
        }.get(
            reason,
            self.swap_failure_penalty,
        )

        now_ps = int(
            node.timeline.now()
        )

        elapsed_s = max(
            0.0,
            (
                now_ps
                - pending.decision_time_ps
            )
            * 1e-12,
        )

        reward = (
            reason_penalty
            - self.wait_penalty_per_second
            * elapsed_s
        )

        self._store_transition(
            state=pending.state,
            action=SWAP,
            reward=reward,
            next_state=self._terminal_state_like(
                pending.state
            ),
            done=True,
        )

        self.completed_swaps += 1

        return True

    # ------------------------------------------------------------------
    # Main decision
    # ------------------------------------------------------------------

    def decide(
        self,
        node,
        left_memory,
        right_memory,
        protocol=None,
        reservation=None,
    ) -> tuple[bool, Hashable | None]:
        """
        Select WAIT or SWAP.

        Returns:
            (execute_swap, decision_key)

        decision_key is non-None only when SWAP is selected. It must be
        attached to the resulting swapping protocol so that on_swap_result()
        can close the correct transition.
        """
        metadata = self._reservation_metadata(
            protocol=protocol,
            reservation=reservation,
        )

        observation = self.build_observation(
            node=node,
            left_memory=left_memory,
            right_memory=right_memory,
            protocol=protocol,
            reservation=reservation,
        )

        pair_key = self._pair_key(
            node=node,
            left_memory=left_memory,
            right_memory=right_memory,
            reservation_identity=(
                metadata["identity"]
            ),
        )

        now_ps = int(
            node.timeline.now()
        )

        self._close_previous_wait_on_new_decision(
            pair_key=pair_key,
            current_observation=observation,
            now_ps=now_ps,
        )

        left_valid = (
            self._remote_node(
                left_memory
            )
            is not None
        )
        right_valid = (
            self._remote_node(
                right_memory
            )
            is not None
        )

        swap_is_valid = (
            left_valid
            and right_valid
            and left_memory is not right_memory
        )

        action = int(
            self.agent.act(
                observation
            )
        )

        # Invalid SWAP is converted to WAIT. An action mask inside the agent
        # would be preferable if OnlineDQNAgent supports it.
        

        if action == WAIT:
            self.pending_wait[
                pair_key
            ] = self._build_pending_decision(
                state=observation,
                action=WAIT,
                node=node,
                left_memory=left_memory,
                right_memory=right_memory,
                metadata=metadata,
            )

            print(
                f"[ONLINE-DQN][{node.name}] "
                f"time="
                f"{now_ps * 1e-12:.6f}s, "
                f"action=WAIT, "
                f"epsilon="
                f"{self.agent.epsilon:.4f}"
            )

            return False, None

        decision_key = (
            "swap",
            pair_key,
            now_ps,
        )

        self.pending_swap[
            decision_key
        ] = self._build_pending_decision(
            state=observation,
            action=SWAP,
            node=node,
            left_memory=left_memory,
            right_memory=right_memory,
            metadata=metadata,
        )

        print(
            f"[ONLINE-DQN][{node.name}] "
            f"time="
            f"{now_ps * 1e-12:.6f}s, "
            f"action=SWAP, "
            f"decision_key="
            f"{decision_key}, "
            f"epsilon="
            f"{self.agent.epsilon:.4f}"
        )

        return True, decision_key

    # ------------------------------------------------------------------
    # External lifecycle callbacks
    # ------------------------------------------------------------------

    def on_memory_invalidated(
        self,
        node,
        memory,
        reason: str,
    ) -> None:
        """
        Close all decisions involving a memory that expired or was reset.
        """
        memory_name = self._memory_name(
            memory
        )
        now_ps = int(
            node.timeline.now()
        )

        wait_keys = [
            key
            for key, pending
            in self.pending_wait.items()
            if (
                pending.node_name
                == node.name
                and memory_name
                in {
                    pending.left_memory_name,
                    pending.right_memory_name,
                }
            )
        ]

        for key in wait_keys:
            self.close_wait_as_terminal(
                pair_key=key,
                reason=reason,
                end_time_ps=now_ps,
            )

        swap_keys = [
            key
            for key, pending
            in self.pending_swap.items()
            if (
                pending.node_name
                == node.name
                and memory_name
                in {
                    pending.left_memory_name,
                    pending.right_memory_name,
                }
            )
        ]

        for key in swap_keys:
            self.close_swap_as_terminal(
                decision_key=key,
                node=node,
                reason=reason,
            )

    def on_reservation_end(
        self,
        node,
        reservation_identity,
    ) -> None:
        now_ps = int(
            node.timeline.now()
        )

        wait_keys = [
            key
            for key, pending
            in self.pending_wait.items()
            if (
                pending.node_name
                == node.name
                and pending.reservation_identity
                == reservation_identity
            )
        ]

        for key in wait_keys:
            self.close_wait_as_terminal(
                pair_key=key,
                reason="reservation_ended",
                end_time_ps=now_ps,
            )

        swap_keys = [
            key
            for key, pending
            in self.pending_swap.items()
            if (
                pending.node_name
                == node.name
                and pending.reservation_identity
                == reservation_identity
            )
        ]

        for key in swap_keys:
            self.close_swap_as_terminal(
                decision_key=key,
                node=node,
                reason="reservation_ended",
            )

    def on_simulation_end(
        self,
        nodes_by_name: dict[str, Any],
    ) -> None:
        for key, pending in list(
            self.pending_wait.items()
        ):
            node = nodes_by_name.get(
                pending.node_name
            )

            if node is None:
                continue

            self.close_wait_as_terminal(
                pair_key=key,
                reason="simulation_end",
                end_time_ps=int(
                    node.timeline.now()
                ),
            )

        for key, pending in list(
            self.pending_swap.items()
        ):
            node = nodes_by_name.get(
                pending.node_name
            )

            if node is None:
                continue

            self.close_swap_as_terminal(
                decision_key=key,
                node=node,
                reason="simulation_end",
            )