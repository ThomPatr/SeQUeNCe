from metrics.link_metrics import LINK_METRICS
from config import PHYSICAL_LINKS

def compute_node_flow_statistics(apps):
    print("\n================ NODE FLOW STATISTICS ================\n")

    for app in apps:
        if not app.history:
            print(f"Node: {app.node.name}")
            print("  no completed requests")
            print("-----------------------------------------------------\n")
            continue

        print(f"Node: {app.node.name}")
        destinations = sorted(set(x["dst"] for x in app.history))

        for dst in destinations:
            flow_hist = [x for x in app.history if x["dst"] == dst]

            total_requests = len(flow_hist)
            approved_requests = sum(1 for x in flow_hist if x["approved"])
            completed_requests = sum(
                1 for x in flow_hist
                if x["approved"] and x["delivered_pairs"] == x["requested_pairs"]
            )

            total_delivered = sum(x["delivered_pairs"] for x in flow_hist)
            total_requested = sum(x["requested_pairs"] for x in flow_hist)

            approval_rate = approved_requests / total_requests if total_requests > 0 else 0.0
            completion_rate = completed_requests / total_requests if total_requests > 0 else 0.0
            avg_delivery_ratio = total_delivered / total_requested if total_requested > 0 else 0.0

            successful_requests = [x for x in flow_hist if x["delivered_pairs"] > 0]
            if successful_requests:
                avg_fidelity_success_only = (
                    sum(x["avg_fidelity"] for x in successful_requests) / len(successful_requests)
                )
            else:
                avg_fidelity_success_only = None

            valid_latencies = [
                x["avg_latency_s"] for x in successful_requests
                if x["avg_latency_s"] is not None
            ]
            avg_latency_success_only = (
                sum(valid_latencies) / len(valid_latencies)
                if valid_latencies else None
            )

            print(f"  Flow {app.node.name} -> {dst}")
            print(f"    total requests                 : {total_requests}")
            print(f"    approval rate                  : {approval_rate:.2f}")
            print(f"    completion rate                : {completion_rate:.2f}")
            print(f"    avg delivery ratio             : {avg_delivery_ratio:.2f}")
            print(f"    avg fidelity (successful only) : {avg_fidelity_success_only:.4f}" if avg_fidelity_success_only is not None else "    avg fidelity (successful only) : None")
            print(f"    avg latency (successful only)  : {avg_latency_success_only:.6f} s" if avg_latency_success_only is not None else "    avg latency (successful only)  : None")
            print()

        print("-----------------------------------------------------\n")


def compute_link_physics_statistics():
    print("\n================ LINK PHYSICS STATISTICS ================\n")

    if not LINK_METRICS:
        print("No link-level physical metrics were recorded.\n")
        return

    for link in sorted(LINK_METRICS.keys()):
        stats = LINK_METRICS[link]

        attempts = stats["eg_attempts"]
        successes = stats["eg_successes"]
        observed_creations = stats.get("observed_creations", 0)
        records = stats["pair_records"]

        p_gen = (successes / attempts) if attempts > 0 else None

        lifetimes = [r["observed_lifetime_ps"] for r in records if r["observed_lifetime_ps"] is not None]
        avg_lifetime_s = (sum(lifetimes) / len(lifetimes) * 1e-12) if lifetimes else None

        f_create = [r["fidelity_at_creation"] for r in records if r["fidelity_at_creation"] is not None]
        f_discard = [r["fidelity_at_discard"] for r in records if r["fidelity_at_discard"] is not None]

        avg_f_create = (sum(f_create) / len(f_create)) if f_create else None
        avg_f_discard = (sum(f_discard) / len(f_discard)) if f_discard else None

        fid_drops = [
            r["fidelity_at_creation"] - r["fidelity_at_discard"]
            for r in records
            if r["fidelity_at_creation"] is not None and r["fidelity_at_discard"] is not None
        ]
        avg_fid_drop = (sum(fid_drops) / len(fid_drops)) if fid_drops else None
        is_physical = link in PHYSICAL_LINKS
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
        print(f"  avg lifetime            : {avg_lifetime_s:.6f} s" if avg_lifetime_s is not None else "  avg lifetime            : None")
        print(f"  avg fidelity creation   : {avg_f_create:.6f}" if avg_f_create is not None else "  avg fidelity creation   : None")
        print(f"  avg fidelity discard    : {avg_f_discard:.6f}" if avg_f_discard is not None else "  avg fidelity discard    : None")
        print(f"  avg fidelity drop       : {avg_fid_drop:.6f}" if avg_fid_drop is not None else "  avg fidelity drop       : None")
        print("---------------------------------------------------------")