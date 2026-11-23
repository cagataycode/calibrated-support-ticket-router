"""Convert lvogel/itsm-tickets-synthesized into the benchmark's ticket format.

Source: https://huggingface.co/datasets/lvogel/itsm-tickets-synthesized (no licence stated, so the raw and
converted text stay out of git; only scores are published).

    curl -L -o bench/itsm/raw/tickets.jsonl \\
      https://huggingface.co/datasets/lvogel/itsm-tickets-synthesized/resolve/main/tickets.jsonl
    python3 bench/itsm/prepare.py        # -> bench/itsm/tickets.jsonl

The dataset's `summary` becomes the title and `body` the description. The queue label is the answer key.
Splits: 'test' = the 200 tickets of the first run (random seed 2026 over the file order; never tune on these),
'dev' = 300 other random tickets (allowed for studying and tuning rules), 'rest' = everything else.
"""

import json
import random
from pathlib import Path

HERE = Path(__file__).parent


def main() -> None:
    queues = json.loads((HERE / "queues.json").read_text())["labels"]
    key_for = {name: key for key, name in queues.items()}
    out = []
    for line in (HERE / "raw" / "tickets.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        out.append({
            "id": r["custom_id"],
            "split": "rest",
            "title": r["summary"],
            "description": r["body"],
            "label": key_for[r["queue"]],
            "priority": r["priority"],
            "type": r["type"],
            "impact": r["impact"],
        })
    test = {t["id"] for t in random.Random(2026).sample(out, 200)}
    dev = {t["id"] for t in random.Random(11).sample([t for t in out if t["id"] not in test], 300)}
    for t in out:
        t["split"] = "test" if t["id"] in test else "dev" if t["id"] in dev else "rest"
    (HERE / "tickets.jsonl").write_text("".join(json.dumps(t) + "\n" for t in out))
    print(f"Wrote {len(out)} tickets to {HERE / 'tickets.jsonl'}")


if __name__ == "__main__":
    main()
