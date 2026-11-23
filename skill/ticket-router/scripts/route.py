"""Route a ticket to department inboxes: Jev answers the questions, this code decides.

Usage:
    python3 route.py                      # dry run: print the request for every probe ticket, send nothing
    python3 route.py --live               # send probe tickets to TypeSafe (needs the key in .env)
    python3 route.py --live --only t03    # one ticket

Every live request and response is saved in runs/ so any result can be rerun and audited.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import questions as Q

HERE = Path(__file__).parent
API_URL = "https://api.typesafe.ai/v1/systemone"
TRIAGE = "triage"  # the human inbox for anything the rules can't settle
PRIORITIES = ["low", "medium", "high", "highest"]


class JevError(Exception):
    """The API call failed. Never treat this as an answer."""

    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status


def load_env() -> None:
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config() -> dict:
    return json.loads((HERE / "departments.json").read_text())


def build_request(company: dict, departments: dict, ticket: dict) -> dict:
    fields = ("title", "description", "reporter_role")
    return {
        "model": Q.MODEL,
        "state": {"company": company, "ticket": {k: ticket[k] for k in fields if ticket.get(k)}},
        "questions": Q.build_questions(departments),
    }


def call_jev(payload: dict) -> dict:
    key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY")
    if not key:
        raise JevError(0, "TYPESAFE_API_KEY (or JEV_API_KEY) is not set. Put it in .env (see .env.example).")
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise JevError(e.code, e.read().decode()[:300]) from None
    except urllib.error.URLError as e:
        raise JevError(0, f"could not reach TypeSafe: {e.reason}") from None


def reading(p: float) -> str:
    return "yes" if p > Q.YES else "no" if p < Q.NO else "unsettled"


def decide(answers: dict, departments: dict) -> dict:
    """The decision table. Each 'trace' line names the row in DESIGN.md that fired."""
    noul = lambda name: answers[name]["noul"]
    ranked = sorted(answers["main_department"]["probabilities"].items(), key=lambda kv: kv[1], reverse=True)
    top, top_p = ranked[0]
    reasons, trace = [], []

    # Rows 1-4: who owns it.
    fits_p = noul("fits_a_department")
    fits = reading(fits_p)
    suggest = f"suggest {ranked[0][0]} ({ranked[0][1]:.2f}) or {ranked[1][0]} ({ranked[1][1]:.2f})"
    if fits == "no":
        owner = TRIAGE
        reasons.append("no department fits")
        trace.append(f"Row 1: fits_a_department is no ({fits_p:.2f}), so triage")
    elif fits == "unsettled" or top_p < Q.MAIN_INBOX_MIN:
        owner = TRIAGE
        why = "unclear if any department fits" if fits == "unsettled" else "no clear main department"
        reasons.append(f"{why}: {suggest}")
        trace.append(f"Row 2: fits_a_department {fits_p:.2f}, top department {top} {top_p:.2f}, so triage")
    elif reading(noul(f"needs_{top}")) == "no":
        owner = TRIAGE
        reasons.append(f"answers disagree: main choice is {top}, but needs_{top} is no")
        trace.append(f"Row 3: needs_{top} is no ({noul(f'needs_{top}'):.2f}), so triage")
    else:
        owner = top
        trace.append(f"Row 4: main inbox {top} ({top_p:.2f})")

    # Row 5: copies to every other team that has work in it.
    copies = [d for d in departments if d != owner and reading(noul(f"needs_{d}")) == "yes"]
    if copies:
        trace.append("Row 5: copy to " + ", ".join(f"{d} ({noul(f'needs_{d}'):.2f})" for d in copies))
    unsure = [d for d in departments if d != owner and reading(noul(f"needs_{d}")) == "unsettled"]
    if unsure:
        trace.append("Row 5: not copied, unsure: " + ", ".join(f"{d} ({noul(f'needs_{d}'):.2f})" for d in unsure))

    # Row 7: priority from the urgency levels. Look at the top level separately so a
    # two-peaked answer (e.g. 'can wait' vs 'breach') is not averaged into 'medium'.
    urgency = answers["urgency"]
    level = min(round(urgency["score"]), len(PRIORITIES) - 1)
    trace.append(f"Row 7: urgency score {urgency['score']:.2f}, so {PRIORITIES[level]}")
    top_level_p = urgency["probabilities"].get(str(len(PRIORITIES) - 1), 0)
    if top_level_p >= 0.3 and level < len(PRIORITIES) - 1:
        level = len(PRIORITIES) - 1
        reasons.append("possible critical impact")
        trace.append(f"Row 7: 'many people blocked' at {top_level_p:.2f} (≥ 0.30), so raised to highest")

    # Row 7b: harm that grows every hour is highest; unsure is at least high.
    loss_p = noul("ongoing_loss")
    if reading(loss_p) == "yes":
        level = 3
        reasons.append("ongoing loss")
        trace.append(f"Row 7b: ongoing_loss is yes ({loss_p:.2f}), so highest")
    elif reading(loss_p) == "unsettled" and level < 2:
        level = 2
        trace.append(f"Row 7b: ongoing_loss is unsure ({loss_p:.2f}), so at least high")

    # Row 6: security work raises priority and always copies security.
    if "security" in departments and reading(noul("needs_security")) == "yes":
        if level < 2:
            level = 2
            trace.append("Row 6: security involved, so priority at least high")
        if owner != "security" and "security" not in copies:
            copies.append("security")
            trace.append("Row 6: copy to security")

    # Row 8: confidential tickets go to the owner only. Unsettled is treated as confidential.
    conf_p = noul("is_confidential_personal_matter")
    confidential = reading(conf_p) != "no"
    if confidential:
        trace.append(f"Row 8: confidential is {reading(conf_p)} ({conf_p:.2f}), so restricted")
        if copies:
            reasons.append(f"confidential: copies to {', '.join(copies)} withheld")
            trace.append("Row 8: copies withheld")
            copies = []

    # Row 9: ask the reporter for detail before a team picks it up.
    labels = []
    detail_p = noul("has_enough_detail")
    if reading(detail_p) == "no":
        labels.append("needs-info")
        trace.append(f"Row 9: has_enough_detail is no ({detail_p:.2f}), so needs-info")
    if confidential:
        labels.append("restricted")

    # Ticket fields: set only when Jev's top answer is clear enough; otherwise left empty for a person.
    fields = {}
    for name in ("request_type", "requester"):
        if name in answers:
            probs = answers[name]["probabilities"]
            best = max(probs, key=probs.get)
            fields[name] = best if probs[best] >= Q.MAIN_INBOX_MIN else None
    if fields:
        trace.append("Fields: " + ", ".join(f"{k} = {v or 'unsure'}" for k, v in fields.items()))

    return {
        "inbox": owner,
        "copies": copies,
        "priority": PRIORITIES[level],
        "labels": labels,
        "fields": fields,
        "reasons": reasons,
        "trace": trace,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="send requests to TypeSafe")
    ap.add_argument("--only", help="ticket id to run")
    args = ap.parse_args()

    load_env()
    config = load_config()
    tickets = json.loads((HERE / "probe_tickets.json").read_text())["tickets"]
    if args.only:
        tickets = [t for t in tickets if t["id"] == args.only]

    runs = HERE / "runs" / time.strftime("%Y%m%d-%H%M%S")
    for t in tickets:
        payload = build_request(config["company"], config["departments"], t)
        if not args.live:
            print(f"{t['id']}: {len(payload['questions'])} questions, {len(json.dumps(payload))} bytes  | {t['title']}")
            continue
        try:
            response = call_jev(payload)
        except JevError as e:
            if e.status in (0, 401, 403):
                sys.exit(f"Stopping: {e}")
            # A failed call is an error, never a 'no': the ticket goes to a person.
            print(f"{t['id']}: API error {e} -> {TRIAGE}")
            continue
        runs.mkdir(parents=True, exist_ok=True)
        (runs / f"{t['id']}.json").write_text(json.dumps({"request": payload, "response": response}, indent=2))
        route = decide(response["answers"], config["departments"])
        route.pop("trace")
        print(f"{t['id']}: {t['title']}\n   -> {json.dumps(route)}  (expected: {t.get('expected', '?')})")
    if not args.live:
        print("\nDry run: nothing sent. Add --live to call TypeSafe.")


if __name__ == "__main__":
    main()
