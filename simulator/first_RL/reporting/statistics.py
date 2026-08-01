from simulator.first_RL.metrics.link_metrics import LINK_METRICS
from simulator.first_RL.config import PHYSICAL_LINKS


def _safe_mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def compute_node_flow_statistics(apps):
    """
    Print application-level statistics for each persistent traffic flow.

    Metrics include:
    - number of requests;
    - approval rate;
    - completion rate;
    - delivery ratio;
    - average fidelity;
    - average latency.
    """
    print("\n================ NODE FLOW STATISTICS ================\n")

    for app in apps:
        print(f"Node: {app.node.name}")

        if not app.history:
            print("  no closed requests")
            print("-----------------------------------------------------\n")
            continue

        destinations = sorted(set(record["dst"] for record in app.history))

        for dst in destinations:
            flow_hist = [record for record in app.history if record["dst"] == dst]

            total_requests = len(flow_hist)
            approved_requests = sum(1 for record in flow_hist if record["approved"] is True)
            completed_requests = sum(1 for record in flow_hist if record["completed"] is True)

            total_delivered = sum(record["delivered_pairs"] for record in flow_hist)
            total_requested = sum(record["requested_pairs"] for record in flow_hist)

            approval_rate = approved_requests / total_requests if total_requests else 0.0
            completion_rate = completed_requests / total_requests if total_requests else 0.0
            avg_delivery_ratio = total_delivered / total_requested if total_requested else 0.0

            successful_requests = [
                record for record in flow_hist
                if record["delivered_pairs"] > 0
            ]

            avg_fidelity_success_only = _safe_mean(
                record["avg_fidelity"] for record in successful_requests
            )

            avg_latency_success_only = _safe_mean(
                record["avg_latency_s"] for record in successful_requests
            )

            print(f"  Flow {app.node.name} -> {dst}")
            print(f"    total requests                 : {total_requests}")
            print(f"    approved requests              : {approved_requests}")
            print(f"    completed requests             : {completed_requests}")
            print(f"    approval rate                  : {approval_rate:.2f}")
            print(f"    completion rate                : {completion_rate:.2f}")
            print(f"    avg delivery ratio             : {avg_delivery_ratio:.2f}")

            if avg_fidelity_success_only is not None:
                print(f"    avg fidelity (successful only) : {avg_fidelity_success_only:.6f}")
            else:
                print("    avg fidelity (successful only) : None")

            if avg_latency_success_only is not None:
                print(f"    avg latency (successful only)  : {avg_latency_success_only:.6f} s")
            else:
                print("    avg latency (successful only)  : None")

            close_reasons = {}
            for record in flow_hist:
                reason = record.get("close_reason", "unknown")
                close_reasons[reason] = close_reasons.get(reason, 0) + 1

            print(f"    close reasons                  : {close_reasons}")
            print()

        print("-----------------------------------------------------\n")


def compute_link_physics_statistics():
    """
    Print physical/link-level statistics.

    Metrics include:
    - entanglement-generation attempts;
    - elementary-link successes;
    - observed pair creations;
    - pair lifetime in memory;
    - fidelity at creation/discard;
    - fidelity drop.
    """
    print("\n================ LINK PHYSICS STATISTICS ================\n")

    if not LINK_METRICS:
        print("No link-level physical metrics were recorded.\n")
        return

    for link in sorted(LINK_METRICS.keys()):
        stats = LINK_METRICS[link]

        attempts = stats.get("eg_attempts", 0)
        successes = stats.get("eg_successes", 0)
        observed_creations = stats.get("observed_creations", 0)
        records = stats.get("pair_records", [])

        is_physical = link in PHYSICAL_LINKS
        p_gen = successes / attempts if attempts > 0 else None

        lifetimes_ps = [
            record.get("observed_lifetime_ps")
            for record in records
            if record.get("observed_lifetime_ps") is not None
        ]
        avg_lifetime_s = (
            _safe_mean(lifetimes_ps) * 1e-12
            if lifetimes_ps else None
        )

        avg_f_create = _safe_mean(
            record.get("fidelity_at_creation") for record in records
        )

        avg_f_discard = _safe_mean(
            record.get("fidelity_at_discard") for record in records
        )

        fid_drops = [
            record["fidelity_at_creation"] - record["fidelity_at_discard"]
            for record in records
            if record.get("fidelity_at_creation") is not None
            and record.get("fidelity_at_discard") is not None
        ]

        avg_fid_drop = _safe_mean(fid_drops)

        discard_reasons = {}
        for record in records:
            reason = record.get("discard_reason", "unknown")
            discard_reasons[reason] = discard_reasons.get(reason, 0) + 1

        print(f"Link: {link[0]} <-> {link[1]}")
        print(f"  physical elementary link : {is_physical}")
        print(f"  eg_attempts             : {attempts}")
        print(f"  eg_successes            : {successes}")
        print(f"  observed_creations      : {observed_creations}")

        if is_physical and p_gen is not None:
            print(f"  p_gen                   : {p_gen:.6f}")
        else:
            print("  p_gen                   : N/A")

        print(f"  completed pair records  : {len(records)}")

        if avg_lifetime_s is not None:
            print(f"  avg lifetime            : {avg_lifetime_s:.6f} s")
        else:
            print("  avg lifetime            : None")

        if avg_f_create is not None:
            print(f"  avg fidelity creation   : {avg_f_create:.6f}")
        else:
            print("  avg fidelity creation   : None")

        if avg_f_discard is not None:
            print(f"  avg fidelity discard    : {avg_f_discard:.6f}")
        else:
            print("  avg fidelity discard    : None")

        if avg_fid_drop is not None:
            print(f"  avg fidelity drop       : {avg_fid_drop:.6f}")
        else:
            print("  avg fidelity drop       : None")

        print(f"  discard reasons         : {discard_reasons}")
        print("---------------------------------------------------------")