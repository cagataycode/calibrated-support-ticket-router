---
name: ticket-router
description: Route helpdesk tickets (Jira / Zendesk style) to the right team inbox with priority and labels, live, using TypeSafe's Jev for the judgments and visible rules for the decision. Use when the user asks to route, triage, categorise or assign tickets, wants to know which team a ticket belongs to, or wants to compare Claude's routing with Jev's.
---

# Ticket router

In Jira and similar tools the reporter has to pick the right project, component or team, and often gets it
wrong, so tickets bounce between teams. This skill lets the reporter just write the ticket. Routing works in
three layers, and each one stays visible:

1. **You (Claude)** read the ticket and give your own routing first, as an independent LLM baseline.
2. **Jev**, TypeSafe's System One model, answers 18 small typed questions about the ticket in one live call:
   which team owns it, whether any team fits at all, whether each team needs a copy, how many people are
   blocked, whether money or data is being lost right now, whether there's enough detail, and whether it's
   confidential. Every answer comes back as a calibrated probability.
3. **Code** (`scripts/route.py`, `decide()`) turns those probabilities into the route with a fixed decision
   table. Jev never answers "where should this go?". When Jev is unsure, the ticket goes to the **triage**
   inbox for a person.

The fictional company is Northwind, a B2B cloud-software firm with 10 teams. The teams are listed in
`scripts/departments.json`; read that file before routing so your own baseline uses the same teams.

## Before the first call

1. Run `python3 scripts/route_tickets.py --check` (from this skill's folder). If `api_key_set` is false, tell
   the user to put `TYPESAFE_API_KEY=...` in a `.env` file in this skill's folder, or to export it in their
   shell. Never ask them to paste the key into the chat. Keys come from https://typesafe.ai.
2. Tell the user once that the ticket text is sent to TypeSafe's API (api.typesafe.ai). If the tickets look
   real and contain names, emails, pay, health or other personal details, ask before sending them, and offer
   to redact names first. The shipped example tickets are invented and safe to send.

Python 3.9+ standard library only; nothing to install.

## Routing tickets

For each request:

1. **Collect the tickets.** Pasted text: use the first line as the title and the rest as the description.
   Several tickets: write them to a `.txt` file separated by lines of `---`, or a JSON file. To show what the
   router does without the user's own tickets, use `--examples` (16 invented tickets, each with the author's
   expected inbox, written before Jev was run).
2. **Write your own baseline first**, before running the script: for each ticket, the one team you'd pick
   from `departments.json` and a priority (low / medium / high / highest), one line each. Don't change it
   after seeing Jev's answer; the point is an honest comparison.
3. **Run the router live:**
   ```
   python3 scripts/route_tickets.py --title "<title>" --text "<body>" [--reporter customer|employee|...]
   python3 scripts/route_tickets.py --file <path>
   python3 scripts/route_tickets.py --examples [--only t03]
   ```
   `--dry-run` prints the exact request without sending it, if the user wants to see what leaves the machine.
4. **Report** in a compact table per ticket: main inbox, copies, priority, labels, and request type and
   requester when they're set. Then one or two sentences on *why*, from the `route.trace` lines (they name the
   rule that fired) and the key probabilities in `jev`. Then your baseline next to it: agree or disagree, and
   if you disagree, say which side you'd back and why. If `author_expected` is present, show it too.
5. Mention latency and cost from the output (typically ~200 ms and a fraction of a cent per ticket), and the
   `saved_to` folder holding every request and response for audit.

### Reading the result

- `inbox: triage` is a feature, not a failure: rows 1–3 of the decision table send a ticket to a person when
  no team fits, Jev's top team is under 0.6, or the answers contradict each other. Say which.
- Yes/no answers read as **yes** above 0.7, **no** below 0.3, **unsettled** in between.
- `restricted` label: the ticket may be confidential (pay, health, contract, a complaint about a colleague);
  copies to other teams are withheld. Unsettled counts as confidential, on purpose.
- `needs-info` label: Jev thinks the owning team couldn't start without asking the reporter something.
- An `error` row means the API call failed. That ticket goes to triage; never present it as an answer.

## The decision table (from `route.py`)

| # | Rule | Result |
|---|---|---|
| 1 | `fits_a_department` is no | Triage: no team fits |
| 2 | `fits_a_department` unsettled, or top team under 0.6 | Triage, with the top two suggestions |
| 3 | `needs_<top team>` is no | Triage: the answers disagree |
| 4 | Otherwise | Main inbox = top team |
| 5 | Every other team whose `needs_` is yes | Copy |
| 6 | `needs_security` is yes | Copy Security, priority at least high |
| 7 | Urgency score, rounded; raised to highest if "many blocked" ≥ 0.3 | Priority |
| 7b | `ongoing_loss` yes → highest; unsettled → at least high | Priority |
| 8 | Confidential yes or unsettled | `restricted`, copies withheld |
| 9 | `has_enough_detail` is no | `needs-info` |

## Evidence, if the user asks how good it is

On 96 hand-labelled tickets from a public synthetic support dataset (Nov 2026): Jev picked the right team on
85.4% vs 81.2% for Claude Opus 5.5 with a one-shot prompt over the same team list, at about $0.11 vs $5.40 per
1,000 tickets and 185 ms vs 1.9 s median latency. Jev's probabilities were well calibrated (0.98 → 97% right,
0.69 → 71%), which is what makes the triage band trustworthy. Limits: 96 tickets is small (say "at least as
accurate", not "more accurate"); the answer key started from Claude's suggestions; the tickets are synthetic,
and the team descriptions were written after reading them.

## Don'ts

- Don't edit `scripts/route.py`, `questions.py` or `departments.json` to make one ticket come out "right".
  If a route looks wrong, say so and say which answer or rule caused it.
- Don't override the router's output in your report. Your view goes in the baseline column.
