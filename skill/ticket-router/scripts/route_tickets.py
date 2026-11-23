"""Route helpdesk tickets live with Jev: Jev answers typed questions, code decides the inbox.

Usage:
    python3 route_tickets.py --title "Charged twice" --text "My card was charged twice for March."
    python3 route_tickets.py --file tickets.json          # {"tickets": [{"title", "description", ...}]} or a list
    python3 route_tickets.py --file tickets.txt           # plain text, tickets separated by a line with ---
    python3 route_tickets.py --examples                   # the 16 example tickets shipped with the skill
    python3 route_tickets.py --examples --only t03
    python3 route_tickets.py --check                      # is a key set? sends nothing
    add --dry-run to print the exact request instead of sending it

Needs TYPESAFE_API_KEY (or JEV_API_KEY) in the environment or in a .env file in the skill folder.
Prints one JSON object per run on stdout. Every request and response is saved in runs/ next to the skill.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
SKILL = HERE.parent
sys.path.insert(0, str(HERE))

import route  # noqa: E402  (bundled copy of the project's route.py)

JEV_PRICE = 0.042 / 1e6  # USD per input token; output is free


def load_env() -> None:
    for env in (SKILL / ".env", HERE / ".env", Path.cwd() / ".env"):
        if env.exists():
            for line in env.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_tickets(args) -> list[dict]:
    if args.examples:
        tickets = json.loads((SKILL / "examples" / "tickets.json").read_text())["tickets"]
    elif args.file:
        path = Path(args.file)
        raw = path.read_text()
        if path.suffix == ".json":
            data = json.loads(raw)
            tickets = data["tickets"] if isinstance(data, dict) else data
        else:
            tickets = []
            for block in raw.split("\n---"):
                lines = [l for l in block.strip().splitlines()]
                if lines:
                    tickets.append({"title": lines[0].strip(), "description": "\n".join(lines[1:]).strip()})
    elif args.text or args.title:
        tickets = [{"title": args.title or "", "description": args.text or "", "reporter_role": args.reporter}]
    else:
        sys.exit("Give a ticket: --title/--text, --file, or --examples.")
    for i, t in enumerate(tickets, 1):
        t.setdefault("id", f"ticket{i}")
        if args.reporter and not t.get("reporter_role"):
            t["reporter_role"] = args.reporter
    if args.only:
        tickets = [t for t in tickets if t["id"] == args.only]
    return tickets


def summary(answers: dict, departments: dict) -> dict:
    """The probabilities a reader needs to see why the router decided what it did."""
    noul = lambda name: round(answers[name]["noul"], 2)
    main = answers["main_department"]["probabilities"]
    ranked = sorted(main.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return {
        "main_department_top3": {k: round(v, 2) for k, v in ranked},
        "fits_a_department": noul("fits_a_department"),
        "needs": {d: noul(f"needs_{d}") for d in departments if answers[f"needs_{d}"]["noul"] >= route.Q.NO},
        "urgency_levels": {k: round(v, 2) for k, v in answers["urgency"]["probabilities"].items()},
        "ongoing_loss": noul("ongoing_loss"),
        "has_enough_detail": noul("has_enough_detail"),
        "is_confidential_personal_matter": noul("is_confidential_personal_matter"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title")
    ap.add_argument("--text", help="ticket body")
    ap.add_argument("--reporter", help="optional reporter role, e.g. customer, employee")
    ap.add_argument("--file")
    ap.add_argument("--examples", action="store_true")
    ap.add_argument("--only", help="ticket id")
    ap.add_argument("--dry-run", action="store_true", help="print the request, send nothing")
    ap.add_argument("--check", action="store_true", help="report whether an API key is set, send nothing")
    args = ap.parse_args()

    load_env()
    has_key = bool(os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY"))
    if args.check:
        print(json.dumps({"api_key_set": has_key, "endpoint": route.API_URL, "model": route.Q.MODEL}))
        return

    config = json.loads((HERE / "departments.json").read_text())
    company, departments = config["company"], config["departments"]
    tickets = read_tickets(args)
    run_dir = SKILL / "runs" / time.strftime("%Y%m%d-%H%M%S")
    results = []

    for t in tickets:
        payload = route.build_request(company, departments, t)
        if args.dry_run:
            results.append({"id": t["id"], "request": payload})
            continue
        start = time.perf_counter()
        try:
            response = route.call_jev(payload)
        except route.JevError as e:
            # A failed call is an error, never an answer: the ticket goes to a person.
            results.append({"id": t["id"], "title": t.get("title"), "error": str(e), "inbox": route.TRIAGE})
            if e.status in (0, 401, 403):
                break
            continue
        ms = (time.perf_counter() - start) * 1000
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / f"{t['id']}.json").write_text(json.dumps({"request": payload, "response": response}, indent=2))
        tokens = response.get("usage", {}).get("input_tokens", 0)
        row = {
            "id": t["id"],
            "title": t.get("title"),
            "route": route.decide(response["answers"], departments),
            "jev": summary(response["answers"], departments),
            "model": response.get("model"),
            "latency_ms": round(ms),
            "input_tokens": tokens,
            "cost_usd": round(tokens * JEV_PRICE, 6),
        }
        if "expected" in t:
            row["author_expected"] = t["expected"]
        results.append(row)

    out = {"tickets": results}
    if not args.dry_run and any("route" in r for r in results):
        out["saved_to"] = str(run_dir)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
