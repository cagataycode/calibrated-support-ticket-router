"""Score saved Jev and Claude answers against YOUR labels. No API calls.

    python3 bench/score_labels.py
    python3 bench/score_labels.py --jev bench/results/<run> --claude bench/results/<run>

By default it uses the latest run that has each system's answers. 'unclear' and 'none_fits'
labels are counted separately and left out of accuracy.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
SPECIAL = ("unclear", "none_fits")


def latest_run(system: str) -> Path | None:
    for run in sorted((HERE / "results").glob("*"), reverse=True):
        rows, meta = run / "rows.jsonl", run / "meta.json"
        if not (rows.exists() and meta.exists()):
            continue
        # Only runs that used the real-world teams can be scored against your labels.
        if json.loads(meta.read_text()).get("teams") == "real" and system in rows.read_text().split("\n", 1)[0]:
            return run
    return None


def load(run: Path | None) -> dict:
    if not run:
        return {}
    return {r["id"]: r for r in map(json.loads, (run / "rows.jsonl").read_text().splitlines())}


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:5.1f}% ({a}/{b})" if b else "  n/a"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jev", type=Path)
    ap.add_argument("--claude", type=Path)
    args = ap.parse_args()

    mine = json.loads((HERE / "data" / "my_labels.json").read_text())
    jev_run, claude_run = args.jev or latest_run("jev"), args.claude or latest_run("claude")
    J, C = load(jev_run), load(claude_run)

    special = Counter(v["label"] for v in mine.values() if v["label"] in SPECIAL)
    clear = {i: v["label"] for i, v in mine.items() if v["label"] not in SPECIAL}
    print(f"Your labels: {len(mine)} tickets ({len(clear)} clear, {special['unclear']} unclear, {special['none_fits']} none fits)")
    sources = Counter(v.get("source", "own") for v in mine.values())
    print(f"Reviewed from Claude's suggestions: {sources['accepted']} accepted, {sources['changed']} changed, "
          f"{sources['own']} without a suggestion")
    if sources["accepted"]:
        print("  Note: suggestions came from Claude Opus 5.5, the same model as the Claude baseline. Accepted\n"
              "  suggestions can favour Claude; compare scores on 'changed' tickets too, and report that bias.")
    print(f"Jev run: {jev_run.name if jev_run else '-'}   Claude run: {claude_run.name if claude_run else '-'}\n")


    changed = {i for i, v in mine.items() if v.get("source") == "changed"}
    for name, rows, key in (("Jev (Choice)", J, "jev"), ("Claude", C, "claude")):
        have = [i for i in clear if i in rows and "error" not in rows[i].get(key, {"error": 1})]
        ok = sum(rows[i][key]["choice"] == clear[i] for i in have)
        ch = [i for i in have if i in changed]
        ok_ch = sum(rows[i][key]["choice"] == clear[i] for i in ch)
        print(f"{name:<36} {pct(ok, len(have))}   on tickets you changed: {pct(ok_ch, len(ch))}")

    have = [i for i in clear if i in J and "error" not in J[i].get("jev", {"error": 1})]
    if have:
        routed = [i for i in have if J[i]["jev"]["route"]["inbox"] != "triage"]
        ok = sum(J[i]["jev"]["route"]["inbox"] == clear[i] for i in routed)
        print(f"\nJev + rules: routed automatically    {pct(len(routed), len(have))}")
        print(f"Jev + rules: correct when routed     {pct(ok, len(routed))}")
        triaged_unclear = [i for i, v in mine.items() if v["label"] in SPECIAL and i in J
                           and J[i]["jev"]["route"]["inbox"] == "triage"]
        n_special = sum(1 for i, v in mine.items() if v["label"] in SPECIAL and i in J)
        print(f"Your unclear/none tickets sent to triage {pct(len(triaged_unclear), n_special)}")

        print("\nCalibration against your labels (Jev's top probability):")
        for lo, hi in [(0, .6), (.6, .8), (.8, .95), (.95, 1.01)]:
            b = [i for i in have if lo <= J[i]["jev"]["choice_p"] < hi]
            if b:
                acc = sum(J[i]["jev"]["choice"] == clear[i] for i in b) / len(b)
                avg = sum(J[i]["jev"]["choice_p"] for i in b) / len(b)
                print(f"  p {lo:.2f}-{min(hi, 1):.2f}: n={len(b):>3}  says {avg:.2f}, right {acc:.2f}")


if __name__ == "__main__":
    main()
