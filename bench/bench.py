"""Benchmark: Jev + routing rules vs Claude on the same labelled tickets.

    .venv/bin/python bench/bench.py --split test           # both systems
    .venv/bin/python bench/bench.py --split dev --limit 20 # quick check
    .venv/bin/python bench/bench.py --only jev

Every request and answer is saved under bench/results/<timestamp>/ so the report can be rebuilt
without calling any API again (bench/report.py).

Both systems get the same input: the ticket subject and body, plus the same department
descriptions (copied from the dataset card).
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import route  # noqa: E402

import anthropic  # noqa: E402

CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_PRICE = {"input": 4.00 / 1e6, "output": 20.00 / 1e6}  # USD per token, Opus 5.5
JEV_PRICE = 0.042 / 1e6  # USD per input token; output is free


def claude_system(company: dict, departments: dict) -> str:
    lines = "\n".join(f"- {key}: {desc}" for key, desc in departments.items())
    return (
        f"You route support tickets for {company['name']}: {company['description']}\n"
        f"Pick the one department that should own the ticket.\n\nDepartments:\n{lines}"
    )


def run_jev(config: dict, t: dict) -> dict:
    policy = route.policy_from(config)
    payload = route.build_request(config["company"], config["departments"], t, policy)
    start = time.perf_counter()
    try:
        resp = route.call_jev(payload)
    except route.JevError as e:
        return {"error": str(e)}
    ms = (time.perf_counter() - start) * 1000
    decision = route.decide(resp["answers"], config["departments"], policy)
    main = resp["answers"]["main_department"]
    tokens = resp.get("usage", {}).get("input_tokens", 0)
    return {
        "choice": main["choice"],
        "choice_p": max(main["probabilities"].values()),
        "route": decision,
        "answers": resp["answers"],
        "ms": ms,
        "input_tokens": tokens,
        "cost": tokens * JEV_PRICE,
    }


def run_claude(client: anthropic.Anthropic, system: str, departments: dict, t: dict) -> dict:
    user = f"Subject: {t['title']}\n\n{t['description']}"
    start = time.perf_counter()
    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {"department": {"type": "string", "enum": list(departments)}},
                        "required": ["department"],
                        "additionalProperties": False,
                    },
                }
            },
        )
    except anthropic.APIStatusError as e:
        return {"error": f"{e.status_code}: {e.message}"}
    except anthropic.APIConnectionError as e:
        return {"error": f"connection: {e}"}
    ms = (time.perf_counter() - start) * 1000
    if resp.stop_reason == "refusal":
        return {"error": "refusal", "ms": ms}
    text = next(b.text for b in resp.content if b.type == "text")
    u = resp.usage
    return {
        "choice": json.loads(text)["department"],
        "ms": ms,
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cost": u.input_tokens * CLAUDE_PRICE["input"] + u.output_tokens * CLAUDE_PRICE["output"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "all"], default="test")
    ap.add_argument("--limit", type=int, help="first N tickets only")
    ap.add_argument("--only", choices=["jev", "claude"])
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--teams", choices=["real", "dataset"], default="real",
                    help="real = ../departments.json (scored with score_labels.py); dataset = the dataset's own queues (Benchmark 1)")
    ap.add_argument("--labelled", action="store_true", help="only the 100 tickets on the labelling page")
    ap.add_argument("--dataset", choices=["support", "itsm"], default="support",
                    help="support = Tobi-Bueck tickets (bench/data); itsm = lvogel ITSM tickets scored on their own queues (bench/itsm)")
    ap.add_argument("--sample", type=int, help="random N tickets (fixed seed), for a cheap first run")
    args = ap.parse_args()

    route.load_env()
    if args.dataset == "itsm":
        args.teams = "dataset"
        config = json.loads((HERE / "itsm" / "queues.json").read_text())
        tickets_file = HERE / "itsm" / "tickets.jsonl"
    else:
        config = route.load_config() if args.teams == "real" else json.loads((HERE / "dataset_queues.json").read_text())
        tickets_file = HERE / "data" / "tickets.jsonl"
    tickets = [json.loads(line) for line in tickets_file.read_text().splitlines()]
    if args.labelled:
        from labels import sample
        keep = {t["id"] for t in sample()}
        tickets = [t for t in tickets if t["id"] in keep]
    elif args.split != "all":
        tickets = [t for t in tickets if t["split"] == args.split]
    if args.sample:
        import random
        tickets = random.Random(2026).sample(tickets, min(args.sample, len(tickets)))
    if args.limit:
        tickets = tickets[: args.limit]

    out = HERE / "results" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    (out / "meta.json").write_text(json.dumps({
        "dataset": args.dataset, "split": "labelled" if args.labelled else args.split, "teams": args.teams,
        "n": len(tickets), "claude_model": CLAUDE_MODEL, "jev_model": route.Q.MODEL,
        "thresholds": {"yes": route.Q.YES, "no": route.Q.NO, "main_min": route.Q.MAIN_INBOX_MIN},
    }, indent=2))

    client = anthropic.Anthropic() if args.only != "jev" else None
    system = claude_system(config["company"], config["departments"])

    def one(t: dict) -> dict:
        row = {"id": t["id"], "label": t["label"], "priority": t.get("priority"), "type": t.get("type"), "impact": t.get("impact")}
        if args.only != "claude":
            row["jev"] = run_jev(config, t)
        if args.only != "jev":
            row["claude"] = run_claude(client, system, config["departments"], t)
        return row

    rows = []
    with ThreadPoolExecutor(args.workers) as pool:
        for i, row in enumerate(pool.map(one, tickets), 1):
            rows.append(row)
            if i % 20 == 0 or i == len(tickets):
                print(f"{i}/{len(tickets)} done", flush=True)
            for system_name in ("jev", "claude"):
                err = row.get(system_name, {}).get("error", "")
                if err.startswith(("0:", "401", "403")):
                    sys.exit(f"Stopping: {system_name} {err}")
    (out / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"\nSaved {out}")

    import report
    report.print_report(out)


if __name__ == "__main__":
    main()
