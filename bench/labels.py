"""Your own labels for benchmark tickets: the sample to label, and where answers are stored.

Labels use the real-world teams in ../departments.json. The dataset's own queues are used only to
draw a balanced sample. The labelling page never sees the dataset's label or either model's answer.
Answers are written to bench/data/my_labels.json after every click.
"""

import json
import random
import threading
from pathlib import Path

HERE = Path(__file__).parent
TICKETS_FILE = HERE / "data" / "tickets.jsonl"
LABELS_FILE = HERE / "data" / "my_labels.json"
SUGGESTIONS_FILE = HERE / "data" / "suggested_labels.json"
PER_QUEUE = 10  # 10 per dataset queue -> 100 tickets
SEED = 2026
UNCLEAR = "unclear"  # two or more teams fit equally well
NONE_FITS = "none_fits"

_lock = threading.Lock()


def teams() -> dict:
    """Team key -> description, from the shared ../departments.json."""
    return json.loads((HERE.parent / "departments.json").read_text())["departments"]


def team_name(key: str) -> str:
    special = {"internal_it": "Internal IT", "hr": "HR", UNCLEAR: "Unclear", NONE_FITS: "None fits"}
    return special.get(key, key.replace("_", " ").title())


def sample() -> list[dict]:
    """100 test tickets, 10 per dataset queue, in a fixed shuffled order. Only what you need to judge."""
    queues = json.loads((HERE / "dataset_queues.json").read_text())["labels"]
    tickets = [json.loads(line) for line in TICKETS_FILE.read_text().splitlines()]
    test = [t for t in tickets if t["split"] == "test"]
    rng = random.Random(SEED)
    picked = []
    for queue in queues:
        rows = [t for t in test if t["label"] == queue]
        picked += rng.sample(rows, min(PER_QUEUE, len(rows)))
    rng.shuffle(picked)
    return [{"id": t["id"], "title": t["title"], "description": t["description"]} for t in picked]


def load() -> dict:
    return json.loads(LABELS_FILE.read_text()) if LABELS_FILE.exists() else {}


def suggestions() -> dict:
    """Claude's suggested labels (made from subject and body only). Shown for review, never used as truth."""
    return json.loads(SUGGESTIONS_FILE.read_text()) if SUGGESTIONS_FILE.exists() else {}


def save(ticket_id: str, label: str, note: str = "") -> dict:
    allowed = set(teams()) | {UNCLEAR, NONE_FITS}
    if label not in allowed:
        raise ValueError(f"unknown label {label!r}")
    if ticket_id not in {t["id"] for t in sample()}:
        raise ValueError(f"unknown ticket {ticket_id!r}")
    with _lock:
        labels = load()
        suggested = suggestions().get(ticket_id, {}).get("label")
        # 'accepted' = you kept the suggestion; 'changed' = you picked something else; 'own' = no suggestion.
        source = "own" if suggested is None else "accepted" if label == suggested else "changed"
        labels[ticket_id] = {"label": label, "note": note[:300], "source": source}
        tmp = LABELS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(labels, indent=1))
        tmp.replace(LABELS_FILE)
    return labels
