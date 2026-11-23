"""Turn a saved benchmark run into numbers. No API calls.

    python3 bench/report.py                 # latest run
    python3 bench/report.py bench/results/<timestamp>
"""

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
PRIORITY_MAP = {"very_low": "low", "low": "low", "medium": "medium", "high": "high", "critical": "highest"}


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}% ({a}/{b})" if b else "n/a"


def print_report(run: Path) -> dict:
    meta = json.loads((run / "meta.json").read_text())
    rows = [json.loads(line) for line in (run / "rows.jsonl").read_text().splitlines()]
    n = len(rows)
    summary = {"run": run.name, **meta}
    print(f"\n=== {run.name}: {n} tickets, split={meta['split']} ===")

    if meta.get("teams") == "real":
        # The dataset's labels use other categories, so accuracy comes from your labels (score_labels.py).
        for key in ("jev", "claude"):
            ok = [r[key] for r in rows if key in r and "error" not in r[key]]
            if ok:
                cost = sum(x["cost"] for x in ok)
                ms = statistics.median(x["ms"] for x in ok)
                print(f"{key:<7} {len(ok)} answered, ${cost:.4f} (${1000 * cost / len(ok):.3f} per 1,000), median {ms:.0f} ms")
                print(f"        picks: {Counter(x['choice'] for x in ok).most_common()}")
        jev = [r for r in rows if "error" not in r.get("jev", {"error": 1})]
        if jev:
            routed = sum(r["jev"]["route"]["inbox"] != "triage" for r in jev)
            print(f"Jev + rules routed automatically: {pct(routed, len(jev))}")
        print("Accuracy: run bench/score_labels.py once you have labelled the tickets.")
        return summary

    jev = [r for r in rows if "jev" in r and "error" not in r["jev"]]
    if jev:
        top1 = sum(r["jev"]["choice"] == r["label"] for r in jev)
        routed = [r for r in jev if r["jev"]["route"]["inbox"] != "triage"]
        routed_ok = sum(r["jev"]["route"]["inbox"] == r["label"] for r in routed)
        cost = sum(r["jev"]["cost"] for r in jev)
        ms = statistics.median(r["jev"]["ms"] for r in jev)
        print(f"\nJev ({meta['jev_model']})")
        print(f"  top-1 department (Choice only)   {pct(top1, len(jev))}")
        print(f"  routed automatically             {pct(len(routed), len(jev))}  (rest go to triage)")
        print(f"  correct when routed              {pct(routed_ok, len(routed))}")
        print(f"  wrong inbox overall              {pct(len(routed) - routed_ok, len(jev))}")
        print(f"  cost                             ${cost:.4f} total, ${1000 * cost / len(jev):.4f} per 1,000")
        print(f"  median latency                   {ms:.0f} ms")

        print("  calibration (Choice top probability vs actual accuracy):")
        for lo, hi in [(0, .4), (.4, .6), (.6, .8), (.8, .95), (.95, 1.01)]:
            b = [r for r in jev if lo <= r["jev"]["choice_p"] < hi]
            if b:
                acc = sum(r["jev"]["choice"] == r["label"] for r in b) / len(b)
                avg = sum(r["jev"]["choice_p"] for r in b) / len(b)
                print(f"    p {lo:.2f}-{min(hi, 1):.2f}: n={len(b):>3}  says {avg:.2f}, right {acc:.2f}")

        pr = [r for r in jev if r.get("priority") in PRIORITY_MAP]
        pr_ok = sum(PRIORITY_MAP[r["priority"]] == r["jev"]["route"]["priority"] for r in pr)
        print(f"  priority matches dataset label   {pct(pr_ok, len(pr))}  (synthetic labels, rough signal)")
        summary["jev"] = {"top1": top1, "n": len(jev), "routed": len(routed), "routed_ok": routed_ok,
                          "cost": cost, "median_ms": ms}

    cl = [r for r in rows if "claude" in r and "error" not in r["claude"]]
    if cl:
        ok = sum(r["claude"]["choice"] == r["label"] for r in cl)
        cost = sum(r["claude"]["cost"] for r in cl)
        ms = statistics.median(r["claude"]["ms"] for r in cl)
        print(f"\nClaude ({meta['claude_model']}), one prompt, same department list")
        print(f"  top-1 department                 {pct(ok, len(cl))}")
        print(f"  cost                             ${cost:.4f} total, ${1000 * cost / len(cl):.2f} per 1,000")
        print(f"  median latency                   {ms:.0f} ms")
        summary["claude"] = {"top1": ok, "n": len(cl), "cost": cost, "median_ms": ms}

    both = [r for r in rows if r.get("jev", {}).get("choice") and r.get("claude", {}).get("choice")]
    if both:
        agree = sum(r["jev"]["choice"] == r["claude"]["choice"] for r in both)
        print(f"\nJev and Claude agree               {pct(agree, len(both))}")

    errors = Counter(f"{s}: {r[s]['error'][:60]}" for r in rows for s in ("jev", "claude") if "error" in r.get(s, {}))
    if errors:
        print("\nErrors:", dict(errors))

    if jev:
        confusions = Counter((r["label"], r["jev"]["choice"]) for r in jev if r["jev"]["choice"] != r["label"])
        print("\nJev's most common mix-ups (true -> chosen):")
        for (a, b), c in confusions.most_common(6):
            print(f"  {c:>3}  {a} -> {b}")
    (run / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    runs = sorted((HERE / "results").glob("*"))
    print_report(Path(sys.argv[1]) if len(sys.argv) > 1 else runs[-1])
