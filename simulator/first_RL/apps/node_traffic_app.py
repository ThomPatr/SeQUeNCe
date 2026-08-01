from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

import numpy as np

from sequence.kernel.event import Event
from sequence.kernel.process import Process

if TYPE_CHECKING:
    from sequence.resource_management.memory_manager import MemoryInfo


class NodeTrafficApp:
    """
    End-to-end Poisson traffic generator.

    Each source-destination flow generates reservation requests according
    to an independent Poisson process:

        Delta T_d ~ Exp(lambda_d)

    where lambda_d is the request arrival rate in requests per second.

    Each request asks for m_d end-to-end entangled pairs. Therefore, the
    average traffic demand associated with flow d is:

        h_d = lambda_d * m_d

    expressed in requested entangled pairs per second.

    Arrivals are independent of session completion. When an arrival occurs:

    - the request is admitted if the maximum number of active sessions for
      the flow has not been reached;
    - otherwise, the request is blocked;
    - the next arrival is always scheduled independently.
    """

    def __init__( self, node, traffic_demands: dict, start_offset_s: float = 0.0, enable_purification=True ,max_parallel_sessions_per_flow: int = 1, reservation_duration_s: float = 5.0, reservation_setup_margin_s: float = 1.0, random_seed: int | None = None,verbose: bool = False,):
        self.node = node
        self.node.set_app(self)
        self.traffic_demands = traffic_demands
        self.start_offset_ps = int(start_offset_s * 1e12)
        self.max_parallel_sessions_per_flow = int( max_parallel_sessions_per_flow )

        if self.max_parallel_sessions_per_flow <= 0:
            raise ValueError( "max_parallel_sessions_per_flow must be positive." )
        self.reservation_duration_ps = int( reservation_duration_s * 1e12)
        self.reservation_setup_margin_ps = int(  reservation_setup_margin_s * 1e12)
        if self.reservation_duration_ps <= 0:
            raise ValueError("reservation_duration_s must be positive." )

        if self.reservation_setup_margin_ps < 0:
            raise ValueError( "reservation_setup_margin_s cannot be negative." )
        self.verbose = verbose
        self.rng = np.random.default_rng(random_seed)
        self.enable_purification = bool(
        enable_purification
    )
        self.global_session_id = 0
        self.flow_active_sessions = { destination: set() for destination in traffic_demands}
        self.active_sessions: dict[int, dict] = {}
        self.history: list[dict] = []
        # Used only if SeQUeNCe does not preserve the reservation identity.
        self.pending_reservation_ids_by_dst = defaultdict(deque)
        self.flow_statistics = {destination: { "arrival_events": 0, "admitted_requests": 0, "blocked_requests": 0,  "requested_pairs": 0,  "admitted_pairs": 0, } for destination in traffic_demands}
        self._validate_traffic_demands()

    # ============================================================
    # CONFIGURATION
    # ============================================================

    def _validate_traffic_demands(self) -> None:
        for destination, demand in self.traffic_demands.items():
            arrival_rate = self._get_arrival_rate(destination)

            if arrival_rate <= 0:
                raise ValueError(
                    f"Arrival rate must be positive for "
                    f"{self.node.name}->{destination}."
                )

            memory_size = int(demand["memory_size"])

            if memory_size <= 0:
                raise ValueError(
                    f"memory_size must be positive for "
                    f"{self.node.name}->{destination}."
                )

            target_fidelity = float(demand["target_fidelity"])

            if not 0.0 <= target_fidelity <= 1.0:
                raise ValueError(
                    f"target_fidelity must belong to [0, 1] for "
                    f"{self.node.name}->{destination}."
                )

    def _get_arrival_rate(self, destination: str) -> float:
        """
        Return the request arrival rate lambda in requests per second.

        Preferred configuration:
            arrival_rate_requests_per_s

        Supported compatibility configuration:
            mean_interarrival_s

        The old interval_s field is interpreted as a mean inter-arrival
        time only for backward compatibility.
        """
        demand = self.traffic_demands[destination]

        if "arrival_rate_requests_per_s" in demand:
            arrival_rate = float( demand["arrival_rate_requests_per_s"]  )

        elif "mean_interarrival_s" in demand:
            mean_interarrival_s = float(demand["mean_interarrival_s"])

            if mean_interarrival_s <= 0:
                raise ValueError(
                    f"mean_interarrival_s must be positive for "
                    f"{self.node.name}->{destination}."
                )

            arrival_rate = 1.0 / mean_interarrival_s

        elif "interval_s" in demand:
            mean_interarrival_s = float(demand["interval_s"] )

            if mean_interarrival_s <= 0:
                raise ValueError(
                    f"interval_s must be positive for "
                    f"{self.node.name}->{destination}."
                )

            arrival_rate = 1.0 / mean_interarrival_s

        else:
            raise KeyError(
                f"Missing arrival configuration for "
                f"{self.node.name}->{destination}. "
                f"Use arrival_rate_requests_per_s."
            )

        if arrival_rate <= 0:
            raise ValueError(
                f"Arrival rate must be positive for "
                f"{self.node.name}->{destination}."
            )

        return arrival_rate

    # ============================================================
    # POISSON ARRIVAL PROCESS
    # ============================================================

    def schedule_initial_events(self) -> None:
        """
        Schedule the first stochastic arrival for every flow.

        The first inter-arrival time is sampled from the same exponential
        distribution used for all subsequent arrivals.
        """
        for flow_index, destination in enumerate(self.traffic_demands):
            
            flow_offset_ps = int( flow_index * 0.5 * 1e12 )

            first_arrival_ps = ( self.start_offset_ps + flow_offset_ps + self._sample_interarrival_ps(destination) )

            process = Process( self, "_handle_arrival", [destination],)

            self.node.timeline.schedule( Event(first_arrival_ps, process) )

    def _sample_interarrival_ps( self, destination: str,) -> int:
        """
        Sample an exponentially distributed inter-arrival time.

        If lambda is expressed in requests/s:

            Delta T ~ Exp(lambda)
            E[Delta T] = 1 / lambda
        """
        arrival_rate = self._get_arrival_rate(  destination)

        interarrival_s = self.rng.exponential(  scale=1.0 / arrival_rate )

        return max( 1, int(interarrival_s * 1e12), )

    def _schedule_next_arrival( self, destination: str,) -> None:
        next_arrival_ps = ( self.node.timeline.now()  + self._sample_interarrival_ps(destination) )

        process = Process( self, "_handle_arrival", [destination], )

        self.node.timeline.schedule( Event(next_arrival_ps, process) )

    def _handle_arrival( self, destination: str,) -> None:
        """
        Process one external Poisson arrival.

        The next arrival is scheduled independently of admission,
        completion, rejection, or timeout of the current request.
        """
        now_ps = self.node.timeline.now()
        demand = self.traffic_demands[destination]
        requested_pairs = int(demand["memory_size"])
        statistics = self.flow_statistics[ destination]
        statistics["arrival_events"] += 1
        statistics["requested_pairs"] += requested_pairs

        # Schedule the next external arrival independently.
        self._schedule_next_arrival(destination)

        active_count = (self._count_active_sessions_for_flow(  destination ) )

        if ( active_count >= self.max_parallel_sessions_per_flow):
            statistics["blocked_requests"] += 1
            blocked_summary = {
                "session_id": None,
                "approved": False,
                "delivered_pairs": 0,
                "requested_pairs": requested_pairs,
                "delivery_ratio": 0.0,
                "completed": False,
                "avg_fidelity": 0.0,
                "avg_latency_s": None,
                "src": self.node.name,
                "dst": destination,
                "close_reason": "arrival_blocked",
                "arrival_time_s": now_ps * 1e-12,
            }

            self.history.append(blocked_summary)

            if self.verbose:
                print(
                    f"[{self.node.name}] ARRIVAL BLOCKED "
                    f"for {destination} at "
                    f"{now_ps * 1e-12:.6f}s "
                    f"(active={active_count}, "
                    f"limit="
                    f"{self.max_parallel_sessions_per_flow})"
                )

            return

        statistics["admitted_requests"] += 1
        statistics["admitted_pairs"] += requested_pairs

        self._start_new_session(destination)

    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================

    def _count_active_sessions_for_flow( self, destination: str, ) -> int:
        return sum( 1 for session_id in self.flow_active_sessions[destination] if session_id in self.active_sessions )

    def _start_new_session( self, destination: str,) -> None:

        now_ps = self.node.timeline.now()

        demand = self.traffic_demands[destination]

        
        self.global_session_id += 1
        
        session_id = self.global_session_id
        
        requested_pairs = int(  demand["memory_size"] )
        
        target_fidelity = float(   demand["target_fidelity"] )
        
        reservation_start_ps = (    now_ps   + self.reservation_setup_margin_ps )
        
        reservation_end_ps = (  reservation_start_ps  + self.reservation_duration_ps )

        self.active_sessions[session_id] = {
            "session_id": session_id,
            "src": self.node.name,
            "dst": destination,
            "created_at_ps": now_ps,
            "reservation_start_ps":
                reservation_start_ps,
            "reservation_end_ps":
                reservation_end_ps,
            "approved": None,
            "requested_pairs": requested_pairs,
            "delivered_pairs": 0,
            "fidelities": [],
            "delivery_times_ps": [],
            "counted_pair_states": set(),
            "first_delivery_ps": None,
            "last_delivery_ps": None,
            "closed": False,
            "close_reason": None,
        }

        self.flow_active_sessions[ destination ].add(session_id)

        self.pending_reservation_ids_by_dst[  destination ].append(session_id)

        if self.verbose:
            print(
                f"\n[{self.node.name}] SESSION "
                f"{session_id} to {destination} CREATED "
                f"at {now_ps * 1e-12:.6f}s "
                f"(requested_pairs={requested_pairs}, "
                f"target_fidelity={target_fidelity:.4f}, "
                f"reservation="
                f"{reservation_start_ps * 1e-12:.6f}s -> "
                f"{reservation_end_ps * 1e-12:.6f}s)"
            )

        self.node.network_manager.request( destination, start_time=reservation_start_ps, end_time=reservation_end_ps, memory_size=requested_pairs, target_fidelity=target_fidelity, identity=session_id,)

        timeout_process = Process( self, "check_session_progress", [session_id],)

        self.node.timeline.schedule( Event( reservation_end_ps, timeout_process,  ) )

    def check_session_progress( self, session_id: int, ) -> None:
        if session_id not in self.active_sessions:
            return

        session = self.active_sessions[session_id]

        if session["closed"]:
            return

        if session["approved"] is False:
            self._close_session( session_id, close_reason="reservation_rejected", )
            return

        if ( session["delivered_pairs"] >= session["requested_pairs"]):
            self._close_session(  session_id, close_reason="completed", )
            return

        self._close_session( session_id, close_reason="partial_or_timeout",)

    def _close_session(  self,  session_id: int,  close_reason: str, ) -> None:
        if session_id not in self.active_sessions:
            return
        data = self.active_sessions[session_id]

        if data["closed"]:
            return

        data["closed"] = True
        data["close_reason"] = close_reason

        delivered_pairs = int(data["delivered_pairs"])

        requested_pairs = int( data["requested_pairs"])

        average_fidelity = ( sum(data["fidelities"]) / len(data["fidelities"]) if data["fidelities"] else 0.0 )

        if data["delivery_times_ps"]:
            average_latency_ps = ( sum(  delivery_time  - data["created_at_ps"]  for delivery_time  in data["delivery_times_ps"]  ) / len(data["delivery_times_ps"])  )

            average_latency_s = ( average_latency_ps * 1e-12 )
        else:
            average_latency_s = None

        summary = {
            "session_id": session_id,
            "approved": data["approved"],
            "delivered_pairs": delivered_pairs,
            "requested_pairs": requested_pairs,
            "delivery_ratio": (
                delivered_pairs / requested_pairs
                if requested_pairs > 0
                else 0.0
            ),
            "completed":
                delivered_pairs >= requested_pairs,
            "avg_fidelity": average_fidelity,
            "avg_latency_s": average_latency_s,
            "src": data["src"],
            "dst": data["dst"],
            "close_reason": close_reason,
            "arrival_time_s":
                data["created_at_ps"] * 1e-12,
        }

        self.history.append(summary)

        if self.verbose:
            print(
                f"[{self.node.name}] SESSION SUMMARY "
                f"{session_id} -> {data['dst']}"
            )
            print(
                f"approved     : {data['approved']}"
            )
            print(
                f"delivered    : "
                f"{delivered_pairs}/{requested_pairs}"
            )
            print(
                f"avg fidelity : "
                f"{average_fidelity:.6f}"
            )
            print(
                f"avg latency  : "
                f"{average_latency_s}"
            )
            print(
                f"close reason : {close_reason}"
            )

        destination = data["dst"]

        self.flow_active_sessions[ destination ].discard(session_id)

        del self.active_sessions[session_id]

    # ============================================================
    # RESERVATION CALLBACKS
    # ============================================================

    def get_reservation_result( self,  reservation,  result: bool, ) -> None:
        now_s = self.node.timeline.now() * 1e-12
        reservation_identity = getattr( reservation, "identity", None, )

        matched_session_id = None
        matched_destination = None

        if ( reservation_identity is not None and reservation_identity in self.active_sessions ):
            matched_session_id = (  reservation_identity )
            matched_destination = ( self.active_sessions[     matched_session_id ]["dst"])

        if matched_session_id is None:
            reservation_destination = None

            for attribute in (
                "responder",
                "dst",
                "dest",
                "destination",
            ):
                if hasattr(reservation, attribute):
                    reservation_destination = getattr(  reservation,  attribute,)
                    break

            if (reservation_destination in self.pending_reservation_ids_by_dst ):
                destinations_to_check = [ reservation_destination]
            else:
                destinations_to_check = list( self.pending_reservation_ids_by_dst )

            for destination in destinations_to_check:
                queue = (  self.pending_reservation_ids_by_dst[  destination]  )
                while queue:
                    candidate_id = queue.popleft()

                    if candidate_id not in self.active_sessions:
                        continue

                    candidate_session = (self.active_sessions[  candidate_id ]  )

                    if candidate_session["closed"]:
                        continue

                    if ( candidate_session["approved"] is None):
                        matched_session_id = (  candidate_id )
                        matched_destination = ( destination )
                        break

                if matched_session_id is not None:
                    break

        if matched_session_id is None:
            if self.verbose:
                print(
                    f"[{self.node.name}]"
                    f"[{now_s:.6f}s] "
                    f"Reservation result received, "
                    f"but no pending session was found."
                )
            return

        self.active_sessions[ matched_session_id ]["approved"] = result

        if self.verbose:
            status = ("APPROVED"
                if result
                else "FAILED"
            )

            print(
                f"[{self.node.name}]"
                f"[{now_s:.6f}s] "
                f"Reservation {status} for session "
                f"{matched_session_id} -> "
                f"{matched_destination}"
            )

    def get_other_reservation(
        self,
        reservation,
    ) -> None:
        pass

    # ============================================================
    # MEMORY DELIVERY CALLBACK
    # ============================================================
    def get_memory(self, info: "MemoryInfo") -> None:
        now_ps = self.node.timeline.now()
        now_s = now_ps * 1e-12

        # The physical lifecycle of the memory is already tracked by
        # instrument_resource_managers(). The application must not modify it.
        if info.state == "RAW":
            return

        if info.fidelity is None or info.fidelity <= 0 or info.remote_node is None:
            return

        entanglement_time_ps = (
            info.entangle_time
            if info.entangle_time is not None and info.entangle_time >= 0
            else now_ps
        )

        candidate_session_ids = []

        for session_id, session in list(self.active_sessions.items()):
            if session["closed"]:
                continue

            if session["approved"] is not True:
                continue

            if session["dst"] != info.remote_node:
                continue

            if session["delivered_pairs"] >= session["requested_pairs"]:
                continue

            candidate_session_ids.append(session_id)

        if not candidate_session_ids:
            return

        # Assign the pair to the oldest compatible admitted request.
        session_id = min(
            candidate_session_ids,
            key=lambda identifier: self.active_sessions[identifier]["created_at_ps"],
        )

        session = self.active_sessions[session_id]

        pair_state_identifier = (
            info.memory.name,
            entanglement_time_ps,
            info.remote_node,
        )

        if pair_state_identifier in session["counted_pair_states"]:
            return

        session["counted_pair_states"].add(pair_state_identifier)
        session["delivered_pairs"] += 1
        session["fidelities"].append(float(info.fidelity))
        session["delivery_times_ps"].append(now_ps)

        if session["first_delivery_ps"] is None:
            session["first_delivery_ps"] = now_ps

        session["last_delivery_ps"] = now_ps

        if self.verbose:
            print(
                f"[{self.node.name}][{now_s:.6f}s] "
                f"Session {session_id} -> {session['dst']} delivered "
                f"{session['delivered_pairs']}/{session['requested_pairs']} "
                f"(memory={info.index}, fidelity={info.fidelity:.6f})"
            )

        # Do not call:
        #
        # register_pair_creation(...)
        # register_pair_discard(...)
        # resource_manager.update(..., "RAW")
        #
        # The physical pair lifecycle is tracked by the instrumented
        # Resource Manager, and SeQUeNCe may still have active protocol
        # references to this memory.

        if (
            session_id in self.active_sessions
            and session["delivered_pairs"] >= session["requested_pairs"]
        ):
            self._close_session(session_id, close_reason="completed")

    # ============================================================
    # TRAFFIC REPORTING
    # ============================================================

    def print_traffic_statistics(self) -> None:
        print(
            f"\n========== TRAFFIC STATISTICS: "
            f"{self.node.name} ==========\n"
        )

        elapsed_time_s = (
            self.node.timeline.now() * 1e-12
        )

        for destination, statistics in (
            self.flow_statistics.items()
        ):
            demand = self.traffic_demands[
                destination
            ]

            arrival_rate = self._get_arrival_rate(
                destination
            )

            memory_size = int(
                demand["memory_size"]
            )

            configured_offered_traffic = (
                arrival_rate * memory_size
            )

            mean_interarrival_s = (
                1.0 / arrival_rate
            )

            # These variables must be assigned before they are used.
            arrivals = int(
                statistics["arrival_events"]
            )

            admitted = int(
                statistics["admitted_requests"]
            )

            blocked = int(
                statistics["blocked_requests"]
            )

            requested_pairs = int(
                statistics["requested_pairs"]
            )

            admitted_pairs = int(
                statistics["admitted_pairs"]
            )

            observed_arrival_rate = (
                arrivals / elapsed_time_s
                if elapsed_time_s > 0
                else 0.0
            )

            observed_offered_traffic = (
                requested_pairs / elapsed_time_s
                if elapsed_time_s > 0
                else 0.0
            )

            observed_admitted_traffic = (
                admitted_pairs / elapsed_time_s
                if elapsed_time_s > 0
                else 0.0
            )

            admission_ratio = (
                admitted / arrivals
                if arrivals > 0
                else 0.0
            )

            blocking_probability = (
                blocked / arrivals
                if arrivals > 0
                else 0.0
            )

            print(
                f"Flow {self.node.name} "
                f"-> {destination}"
            )

            print(
                f"  configured arrival rate : "
                f"{arrival_rate:.6f} requests/s"
            )

            print(
                f"  mean inter-arrival      : "
                f"{mean_interarrival_s:.6f} s"
            )

            print(
                f"  configured traffic      : "
                f"{configured_offered_traffic:.6f} pairs/s"
            )

            print(
                f"  observed arrival rate   : "
                f"{observed_arrival_rate:.6f} requests/s"
            )

            print(
                f"  observed offered traffic: "
                f"{observed_offered_traffic:.6f} pairs/s"
            )

            print(
                f"  observed admitted traffic: "
                f"{observed_admitted_traffic:.6f} pairs/s"
            )

            print(
                f"  arrival events          : "
                f"{arrivals}"
            )

            print(
                f"  admitted requests       : "
                f"{admitted}"
            )

            print(
                f"  blocked requests        : "
                f"{blocked}"
            )

            print(
                f"  admission ratio         : "
                f"{admission_ratio:.6f}"
            )

            print(
                f"  blocking probability    : "
                f"{blocking_probability:.6f}"
            )

            print(
                f"  requested pairs         : "
                f"{requested_pairs}"
            )

            print(
                f"  admitted pairs          : "
                f"{admitted_pairs}"
            )

            print()