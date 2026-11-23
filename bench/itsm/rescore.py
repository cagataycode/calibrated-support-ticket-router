"""Re-apply the routing rules to saved Jev answers with a given policy. No API calls.

    python3 bench/itsm/rescore.py bench/results/<run>                 # policy from bench/itsm/queues.json
    python3 bench/itsm/rescore.py bench/results/<run> --default       # Northwind's default policy

Shows queue accuracy after the rules (triage counts as not routed) and priority agreement.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent))
import route  # noqa: E402

MAP = {"low": "low", "medium": "medium", "high": "high", "critical": "highest"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--default", action="store_true")
    ap.add_argument("--set", action="append", default=[], help="override a policy key, e.g. first_line=null")
    args = ap.parse_args()

    config = json.loads((HERE / "queues.json").read_text())
    policy = route.DEFAULT_POLICY if args.default else route.policy_from(config)
    for kv in args.set:
        k, v = kv.split("=", 1)
        policy = {**policy, k: json.loads(v)}
    rows = [json.loads(l) for l in (Path(args.run) / "rows.jsonl").read_text().splitlines()]
    rows = [r for r in rows if "jev" in r and "error" not in r["jev"]]
    n = len(rows)
    routes = [route.decide(r["jev"]["answers"], config["departments"], policy) for r in rows]
    routed = [(r, d) for r, d in zip(rows, routes) if d["inbox"] != route.TRIAGE]
    inbox_ok = sum(d["inbox"] == r["label"] for r, d in zip(rows, routes))
    pr_ok = sum(MAP[r["priority"]] == d["priority"] for r, d in zip(rows, routes))
    l1 = [(r, d) for r, d in zip(rows, routes) if r["label"] == policy.get("first_line")]
    print(f"{n} tickets  policy={json.dumps({k: v for k, v in policy.items()})}")
    print(f"  inbox right (triage = wrong)   {inbox_ok}/{n} = {inbox_ok / n:.1%}")
    print(f"  routed automatically           {len(routed)}/{n} = {len(routed) / n:.1%}, correct when routed "
          f"{sum(d['inbox'] == r['label'] for r, d in routed) / max(len(routed), 1):.1%}")
    if l1:
        print(f"  first-line tickets right       {sum(d['inbox'] == r['label'] for r, d in l1)}/{len(l1)}")
        sent = [(r, d) for r, d in zip(rows, routes) if d["inbox"] == policy["first_line"]]
        print(f"  sent to first line             {len(sent)}, of which truly first line {sum(r['label'] == policy['first_line'] for r, d in sent)}")
    print(f"  priority matches               {pr_ok}/{n} = {pr_ok / n:.1%}")
    print("  priority (dataset -> ours)     " + ", ".join(f"{a}->{b}:{c}" for (a, b), c in
          sorted(Counter((r["priority"], d["priority"]) for r, d in zip(rows, routes)).items())))


if __name__ == "__main__":
    main()
