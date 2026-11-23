"""Local demo server for the ticket router. The API key stays here, never in the browser.

    python3 server.py          # then open http://localhost:8018

Endpoints:
    GET  /               the inbox page
    GET  /api/config     departments and example tickets
    GET  /api/tickets    tickets routed since the server started
    POST /api/route      {title, description, reporter_role} -> route, Jev's answers, trace
    GET  /label          blind labelling page for the benchmark (bench/labels.py)
    GET  /api/label/setup  departments, the 100 tickets, and your saved labels
    POST /api/label      {id, label, note} -> saved to bench/data/my_labels.json
"""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import route
from bench import labels

HERE = Path(__file__).parent
PORT = 8018
MAX_BODY = 20_000  # bytes; a ticket is text, not a file
RUNS = HERE / "runs" / "web"

route.load_env()
CONFIG = route.load_config()
EXAMPLES = [
    {k: t[k] for k in ("title", "description", "reporter_role")}
    for t in json.loads((HERE / "probe_tickets.json").read_text())["tickets"]
]
TICKETS: list[dict] = []  # in memory: restarting the server empties the inboxes


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, (HERE / "static" / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/config":
            self._json(200, {
                "company": CONFIG["company"],
                "departments": CONFIG["departments"],
                "triage": route.TRIAGE,
                "examples": EXAMPLES,
            })
        elif self.path == "/api/tickets":
            self._json(200, TICKETS)
        elif self.path == "/label":
            self._send(200, (HERE / "static" / "label.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/label/setup":
            self._json(200, {
                "departments": {k: {"name": labels.team_name(k), "desc": d} for k, d in labels.teams().items()},
                "tickets": labels.sample(),
                "answers": labels.load(),
                "suggestions": labels.suggestions(),
            })
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/api/label":
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                return self._json(413, {"error": "too long"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                saved = labels.save(str(body.get("id", "")), str(body.get("label", "")), str(body.get("note", "")))
            except (json.JSONDecodeError, ValueError) as e:
                return self._json(400, {"error": str(e)})
            return self._json(200, {"done": len(saved)})
        if self.path != "/api/route":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY:
            return self._json(413, {"error": "ticket too long"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid JSON"})
        ticket = {k: str(body.get(k, "")).strip() for k in ("title", "description", "reporter_role")}
        if not ticket["title"] and not ticket["description"]:
            return self._json(400, {"error": "write a title or a description"})

        payload = route.build_request(CONFIG["company"], CONFIG["departments"], ticket)
        started = time.perf_counter()
        try:
            response = route.call_jev(payload)
        except route.JevError as e:
            # A failed call is shown as an error, never routed as if Jev had answered.
            return self._json(502, {"error": str(e)})
        ms = round((time.perf_counter() - started) * 1000)

        decision = route.decide(response["answers"], CONFIG["departments"])
        entry = {
            "id": len(TICKETS) + 1,
            "ticket": ticket,
            "route": decision,
            "answers": response["answers"],
            "model": response.get("model"),
            "usage": response.get("usage"),
            "ms": ms,
        }
        TICKETS.append(entry)
        RUNS.mkdir(parents=True, exist_ok=True)
        (RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{entry['id']}.json").write_text(
            json.dumps({"request": payload, "response": response, "route": decision}, indent=2)
        )
        self._json(200, entry)

    def log_message(self, fmt, *args) -> None:
        print(f"{self.address_string()} {fmt % args}")


if __name__ == "__main__":
    print(f"Ticket router demo on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
