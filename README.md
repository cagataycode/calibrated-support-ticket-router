# Calibrated support ticket router

Routes Jira-style helpdesk tickets to the right team in **~200 ms for about $0.11 per 1,000 tickets**. It is at least as
accurate as Claude Opus 5.5, and its confidence is calibrated, so unsure tickets go to a person instead of the wrong inbox.

In Jira, Zendesk and similar tools, the person filing a ticket has to pick the project, component or team. They often
pick wrong or leave it empty, and tickets bounce between teams. Here the reporter only writes the ticket.

## How it works

1. **Jev answers 18 small typed questions about the ticket in one call.** [Jev](https://typesafe.ai) is TypeSafe's
   System One model. It doesn't write text; it answers typed questions with calibrated probabilities. All wording
   lives in [questions.py](questions.py):
   - `main_department`: which of the 10 teams in [departments.json](departments.json) owns the ticket
   - `fits_a_department`: does any team fit at all? (a choice always picks something, even when nothing fits)
   - `needs_<team>`: one yes/no per team, because a ticket can need more than one team
   - `urgency`: how many people are blocked, on four levels each written as a situation
   - `ongoing_loss`: is money or data being lost right now, so that waiting makes it worse?
   - `has_enough_detail`, `is_confidential_personal_matter`, `request_type`, `requester`
2. **Code decides the route** ([route.py](route.py), `decide()`). Jev is never asked "where should this go?". It
   answers the judgments, and a visible, testable decision table makes the decision.
3. **Unsure means a person.** Low or contradictory confidence sends the ticket to a **triage** inbox.

### Decision table

A yes/no answer reads as **yes** above 0.7, **no** below 0.3, and **unsettled** in between.

| # | Rule | Result |
|---|---|---|
| 1 | `fits_a_department` is no | Triage: no team fits |
| 2 | `fits_a_department` unsettled, or the top team is under 0.6 | Triage, with the top two suggestions |
| 3 | `needs_<top team>` is no | Triage: the answers disagree |
| 4 | Otherwise | Main inbox = top team |
| 5 | Every other team whose `needs_` is yes | Copy to that team |
| 6 | `needs_security` is yes | Copy Security, priority at least high |
| 7 | Urgency score, rounded; raised to highest if "many blocked" ≥ 0.3 | Priority |
| 7b | `ongoing_loss` yes → highest; unsettled → at least high | Priority |
| 8 | Confidential is yes or unsettled | Label `restricted`; copies withheld |
| 9 | `has_enough_detail` is no | Label `needs-info` |
| — | The API call fails | An error, never a "no"; the ticket goes to triage |

## Results

100 tickets from the synthetic [Customer Support Tickets](https://huggingface.co/datasets/Tobi-Bueck/customer-support-tickets)
dataset were hand-labelled with the correct team (4 were marked unclear, leaving 96). Claude Opus 5.5 got one prompt with
the same team list and had to pick exactly one team.

| 96 clear tickets | Jev + rules | Claude Opus 5.5 |
|---|---|---|
| Right team | **85.4%** | 81.2% |
| Cost per 1,000 tickets | **$0.11** | $5.40 |
| Median latency | **185 ms** | 1.9 s |
| Calibrated probability per answer | yes | no |
| Triage band for unsure tickets | yes | no |

- Jev + rules routes **89.6%** of tickets without a person, and **89.5%** of those reach the right team.
- **Calibration:** when Jev says 0.98 it is right 97% of the time; 0.88 → 85%; 0.69 → 71%.
- Most misses sit on team boundaries: how-to questions about security, operator vs. end-user problems, and billing vs. sales.

An earlier run against the dataset's own 10 queue labels scored only ~30% for both systems. Those labels are noisy:
when Jev and Claude agreed, the label matched only 34% of the time. The queues also mix three questions
(which team, what kind of request, who is writing). Replacing them with real-world teams plus separate `request_type` and
`requester` questions raised agreement between the two models from 58% to 86%.

A visual summary is in [report/ticket-router.html](report/ticket-router.html).

### Limits

- **The answer key leans toward Claude.** Labels were reviewed starting from Claude's suggestions (94 of 100 accepted), so the
  key could favour the Claude baseline. On the 6 tickets where the labeller changed Claude's suggestion, Jev got 4 right and Claude 2.
- **Small, synthetic sample.** A 4-point gap is 4 tickets, so read it as "at least as accurate", not "more accurate".
- **The team descriptions were written after reading these tickets.** That may flatter both systems; a fresh held-out set is the next test.
- **Vague tickets slip through.** All 4 "unclear" tickets were routed automatically instead of going to triage.
- **Known gaps:** a double charge gets low priority, because urgency measures how many people are blocked, not money owed;
  and Customer Support gets copied on many customer tickets.

## Run it

Python 3.9+. The router and demo use only the standard library.

```bash
cp .env.example .env    # then add your TYPESAFE_API_KEY (and ANTHROPIC_API_KEY for the Claude baseline)
python3 server.py       # demo at http://localhost:8018, labelling page at /label
python3 route.py        # dry run on the 16 example tickets: prints the request size, sends nothing
python3 route.py --live # route them live; requests and responses are saved in runs/
```

Benchmark (needs `pip install anthropic` for the Claude side):

```bash
python3 bench/fetch_data.py      # downloads the dataset sample into bench/data/
python3 bench/bench.py --help
python3 bench/score_labels.py    # scores saved answers against the hand labels; no API calls
```

## Claude skill

[skill/ticket-router/](skill/ticket-router/SKILL.md) is a Claude skill that routes tickets live. Claude writes its own
routing first as a baseline, then runs the router, explains which rule decided each result, and compares the two.
`skill/build.sh` copies the router files into the skill and zips it. Put `TYPESAFE_API_KEY` in a `.env` inside the skill folder.

## Data and privacy

- Ticket text is sent to TypeSafe's API. Don't route real tickets that contain personal data without a data-processing
  agreement and redaction.
- `bench/data/tickets.jsonl` and the saved results in `bench/results/` contain text from the
  [Customer Support Tickets](https://huggingface.co/datasets/Tobi-Bueck/customer-support-tickets) dataset by Tobi Bueck,
  licensed [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). That licence applies to this data.
- `probe_tickets.json` contains invented tickets. The company "Northwind" is fictional.
- API keys belong in `.env`, which is git-ignored. Tickets typed into the demo are saved in `runs/web/`, which is also
  git-ignored.
