"""Fetch a labelled sample from the public Customer Support Tickets dataset.

Source: https://huggingface.co/datasets/Tobi-Bueck/customer-support-tickets (CC BY-NC 4.0,
synthetic tickets, no real personal data). Uses the Hugging Face datasets-server API, so only
the sampled rows are downloaded, not the 56 MB file.

    python3 bench/fetch_data.py            # 30 English tickets per queue -> bench/data/tickets.jsonl

Split: per queue, the first 10 rows go to 'dev' (allowed for tuning) and the rest to 'test'
(never look at these while changing questions or rules).
"""

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
DATASET = "Tobi-Bueck/customer-support-tickets"
PER_QUEUE = 30
DEV_PER_QUEUE = 10
SEED = 18


def fetch(queue: str, offset: int, length: int) -> tuple[list[dict], int]:
    where = f"\"language\"='en' AND \"queue\"='{queue}'"
    q = urllib.parse.urlencode({
        "dataset": DATASET, "config": "default", "split": "train",
        "where": where, "offset": offset, "length": length,
    })
    for attempt in range(8):
        try:
            with urllib.request.urlopen(f"https://datasets-server.huggingface.co/filter?{q}", timeout=60) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            # The server answers 500 "dataset index is loading" until its index is ready.
            if e.code != 500 or attempt == 7:
                raise
            print(f"  server not ready ({e.read().decode()[:80]}), retrying in 20 s")
            time.sleep(20)
    return [row["row"] for row in data["rows"]], data.get("num_rows_total", 0)


def main() -> None:
    labels = json.loads((HERE / "dataset_queues.json").read_text())["labels"]
    rng = random.Random(SEED)
    out = []
    for key, queue in labels.items():
        _, total = fetch(queue, 0, 1)
        # Take a random window rather than the first rows, so the sample isn't the dataset's first entries.
        offset = rng.randrange(0, max(1, total - PER_QUEUE * 2))
        rows, _ = fetch(queue, offset, PER_QUEUE * 2)
        rows = [r for r in rows if (r.get("body") or "").strip()][:PER_QUEUE]
        for i, r in enumerate(rows):
            out.append({
                "id": f"{key}-{offset + i}",
                "split": "dev" if i < DEV_PER_QUEUE else "test",
                "title": (r.get("subject") or "").strip(),
                "description": r["body"].strip(),
                "label": key,
                "priority": r.get("priority"),
                "type": r.get("type"),
            })
        print(f"{queue:<34} {len(rows):>3} rows (of {total} English)")
        time.sleep(0.5)
    path = HERE / "data" / "tickets.jsonl"
    path.parent.mkdir(exist_ok=True)
    path.write_text("".join(json.dumps(t) + "\n" for t in out))
    print(f"\n{len(out)} tickets -> {path} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
