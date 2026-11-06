#!/usr/bin/env python3
"""The Splat's "tell me what would help" form, delivered to David's inbox.

The page posts JSON to /api/splat-intake; nginx passes it here (127.0.0.1:8791).
This turns it into a plain-text email and sends it through Microsoft Graph as
wizard@workflowtech.ai, the app sender every workflowtech.ai service uses.
Nothing is stored: a submission exists only as that email.

Every field is optional by design. Spam guards, no CAPTCHA: a hidden field a
person never fills, a minimum time on the page, and a few posts per visitor an
hour (nginx limits too). Keys come from /etc/splat-intake.env (root, 0600):
GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_SENDER, INTAKE_TO.

    python3 infra/intake.py            # serve
    python3 infra/intake.py --dry-run  # serve, print the email instead of sending
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8791
MAX_BODY = 32_000
MIN_SECONDS = 4          # faster than a person can read the form
PER_HOUR = 5
DRY = "--dry-run" in sys.argv
# Field: (label, longest kept). Anything else posted is ignored.
FIELDS = {
    "situation": ("Where they are right now", 4000),
    "tracks": ("Tracks", 200),
    "other_tracks": ("Other tracks", 300),
    "region": ("Where", 300),
    "level": ("Level", 100),
    "would_use": ("Would use the tools", 50),
    "pieces": ("Pieces that matter", 400),
    "account": ("Account preference", 100),
    "donate": ("Optional donate link", 100),
    "else": ("Anything else", 4000),
    "name": ("Name", 120),
    "email": ("Email", 200),
}
_hits: dict[str, list[float]] = {}
_token: dict = {"value": "", "until": 0.0}


def clean(form: dict) -> dict:
    out = {}
    for key, (_, n) in FIELDS.items():
        v = form.get(key)
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        v = str(v or "").replace("\r", "").strip()[:n]
        if v:
            out[key] = v
    return out


def body_text(form: dict) -> str:
    lines = [f"{FIELDS[k][0]}:\n{v}\n" for k, v in form.items()]
    return "\n".join(lines) or "(every field was left empty)"


def graph_token() -> str:
    if _token["value"] and time.time() < _token["until"]:
        return _token["value"]
    tenant = os.environ["GRAPH_TENANT_ID"]
    data = urllib.parse.urlencode({
        "client_id": os.environ["GRAPH_CLIENT_ID"], "client_secret": os.environ["GRAPH_CLIENT_SECRET"],
        "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}).encode()
    with urllib.request.urlopen(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", data, timeout=20) as r:
        d = json.load(r)
    _token.update(value=d["access_token"], until=time.time() + int(d.get("expires_in", 3600)) - 120)
    return _token["value"]


def send(form: dict) -> None:
    subject = "Splat intake: " + (form.get("name") or "someone")
    msg = {"subject": subject, "body": {"contentType": "Text", "content": body_text(form)},
           "toRecipients": [{"emailAddress": {"address": os.environ.get("INTAKE_TO", "")}}]}
    if "@" in form.get("email", ""):
        msg["replyTo"] = [{"emailAddress": {"address": form["email"]}}]
    if DRY:
        print(json.dumps(msg, indent=1), file=sys.stderr)
        return
    sender = urllib.parse.quote(os.environ["GRAPH_SENDER"])
    req = urllib.request.Request(
        f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
        json.dumps({"message": msg, "saveToSentItems": False}).encode(),
        {"Authorization": f"Bearer {graph_token()}", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).close()


class Handler(BaseHTTPRequestHandler):
    def reply(self, code: int, obj: dict) -> None:
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):  # noqa: N802 - http.server's name
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self.reply(413, {"ok": False})
        try:
            form = json.loads(self.rfile.read(n))
        except ValueError:
            return self.reply(400, {"ok": False})
        # The trap and the timer: answer "ok" so a bot learns nothing.
        if form.get("website") or (time.time() - float(form.get("t") or 0) / 1000) < MIN_SECONDS:
            return self.reply(200, {"ok": True})
        ip = self.headers.get("X-Real-IP") or self.client_address[0]
        now = time.time()
        recent = [t for t in _hits.get(ip, []) if now - t < 3600]
        if len(recent) >= PER_HOUR:
            return self.reply(429, {"ok": False, "error": "Too many in an hour. Try again later."})
        _hits[ip] = recent + [now]
        try:
            send(clean(form))
        except Exception as e:  # noqa: BLE001 - say what failed in the log, not to the visitor
            print(f"send failed: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
            return self.reply(502, {"ok": False, "error": "It did not send. Please try again in a bit."})
        return self.reply(200, {"ok": True})

    def log_message(self, fmt, *args):  # the access log would hold visitors' addresses
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
