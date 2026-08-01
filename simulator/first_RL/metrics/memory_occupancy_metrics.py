from __future__ import annotations

from collections import defaultdict
from typing import Any


# Active occupation indexed by:
# (node_name, memory_name)
_ACTIVE_MEMORY_OCCUPATIONS: dict[
    tuple[str, str],
    dict[str, Any],
] = {}

# Completed occupation records.
_MEMORY_OCCUPATION_RECORDS: list[
    dict[str, Any]
] = []


def reset_memory_occupancy_metrics() -> None:
    _ACTIVE_MEMORY_OCCUPATIONS.clear()
    _MEMORY_OCCUPATION_RECORDS.clear()


def _memory_name(memory: Any) -> str:
    return str(
        getattr(
            memory,
            "name",
            id(memory),
        )
    )


def register_memory_occupation_start(
    node_name: str,
    memory: Any,
    start_time_ps: int,
    source: str | None = None,
    destination: str | None = None,
    reservation_identity: str | int | None = None,
    state: str | None = None,
) -> None:
    key = (
        str(node_name),
        _memory_name(memory),
    )

    # Do not overwrite an already active occupation.
    if key in _ACTIVE_MEMORY_OCCUPATIONS:
        return

    _ACTIVE_MEMORY_OCCUPATIONS[key] = {
        "node": str(node_name),
        "memory": _memory_name(memory),
        "start_time_ps": int(start_time_ps),
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
        "reservation_identity":
            reservation_identity,
        "initial_state": state,
    }


def register_memory_occupation_end(
    node_name: str,
    memory: Any,
    end_time_ps: int,
    reason: str,
    final_state: str = "RAW",
) -> None:
    key = (
        str(node_name),
        _memory_name(memory),
    )

    active_record = (
        _ACTIVE_MEMORY_OCCUPATIONS.pop(
            key,
            None,
        )
    )

    if active_record is None:
        return

    start_time_ps = int(
        active_record["start_time_ps"]
    )

    end_time_ps = int(end_time_ps)

    duration_ps = max(
        0,
        end_time_ps - start_time_ps,
    )

    completed_record = {
        **active_record,
        "end_time_ps":
            end_time_ps,
        "duration_ps":
            duration_ps,
        "duration_s":
            duration_ps * 1e-12,
        "reason":
            str(reason),
        "final_state":
            str(final_state),
    }

    _MEMORY_OCCUPATION_RECORDS.append(
        completed_record
    )


def close_open_memory_occupations(
    end_time_ps: int,
    reason: str = "simulation_end",
) -> None:
    active_keys = list(
        _ACTIVE_MEMORY_OCCUPATIONS.keys()
    )

    for node_name, memory_name in active_keys:
        active_record = (
            _ACTIVE_MEMORY_OCCUPATIONS.get(
                (
                    node_name,
                    memory_name,
                )
            )
        )

        if active_record is None:
            continue

        class MemoryReference:
            name = memory_name

        register_memory_occupation_end(
            node_name=node_name,
            memory=MemoryReference(),
            end_time_ps=end_time_ps,
            reason=reason,
        )


def get_memory_occupancy_records() -> list[dict]:
    return [
        record.copy()
        for record in _MEMORY_OCCUPATION_RECORDS
    ]

def get_memory_occupancy_debug_state() -> dict:
    return {
        "active_occupations":
            len(_ACTIVE_MEMORY_OCCUPATIONS),
        "completed_records":
            len(_MEMORY_OCCUPATION_RECORDS),
        "active_keys": [
            {
                "node": node_name,
                "memory": memory_name,
            }
            for node_name, memory_name
            in _ACTIVE_MEMORY_OCCUPATIONS
        ],
    }