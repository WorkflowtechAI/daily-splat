#!/usr/bin/env python3
"""The Splat's "tell me what would help" form, delivered to David's inbox, and its
"Share a job" form.

The page posts JSON to /api/splat-intake; nginx passes it here (127.0.0.1:8791).
A help-form submission becomes a plain-text email sent through Microsoft Graph as
wizard@workflowtech.ai, the app sender every workflowtech.ai service uses, and
nothing of it is stored.

A shared job (`"kind": "tip"`, added 2026-11-11) is checked, its link tidied
(LinkedIn and Indeed links to their plain job address), and appended to TIPS_FILE,
which splat.py reads each morning (see src_tips there). David gets an email with
a signed link that removes it (`"kind": "tip_review"`). A tip holds only what the
member typed about the job; nothing about the member is kept.

Every help-form field is optional by design. Spam guards, no CAPTCHA: a hidden
field a person never fills, a minimum time on the page, and a few posts per
visitor an hour (nginx limits too). Keys come from /etc/splat-intake.env (root,
0600): GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_SENDER,
INTAKE_TO, and TIP_SECRET (signs the remove links).

    python3 infra/intake.py            # serve
    python3 infra/intake.py --dry-run  # serve, print the email instead of sending
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
    "extras": ("Other help they'd want", 600),
    "extras_other": ("Other help, in their words", 600),
    "else": ("Anything else", 4000),
    "name": ("Name", 120),
    "email": ("Email", 200),
}
_hits: dict[str, list[float]] = {}
_token: dict = {"value": "", "until": 0.0}

# ── Shared jobs ──────────────────────────────────────────────────────────────
TIPS_FILE = Path(os.environ.get("TIPS_FILE") or "/var/lib/daily-splat/tips.json")
TIP_KEEP_DAYS = 31        # splat.py shows a tip at most 30 days (14 for LinkedIn/Indeed)
PAGE = "https://iambraun.com/cv/job-sources/daily-splat/"
TIP_FIELDS = {"url": 500, "title": 160, "company": 120, "location": 120, "note": 500}
_tips_lock = threading.Lock()
# Job sites that stand between a job seeker and the employer (splat.py's MIDDLEMAN,
# less LinkedIn and Indeed, whose links are allowed and shown as such).
NOT_A_SOURCE = re.compile(r"(?:^|\.)(?:dice\.com|haystackapp\.io|lensa\.com|jooble\.org|ziprecruiter\.com|"
                          r"talent\.com|jobs2careers\.com|simplyhired\.com|glassdoor\.com|monster\.com|"
                          r"careerbuilder\.com|jobgether\.com|theladders\.com|getwork\.com|jobright\.ai|adzuna\.com)$", re.I)
LINKEDIN_ID = re.compile(r"(?:/jobs/view/(?:[^/?#]*?-)?|[?&]currentJobId=)(\d{6,})")
INDEED_JK = re.compile(r"[?&](?:jk|vjk)=([0-9a-f]{8,32})\b", re.I)


class TipError(ValueError):
    """A reason to show the member, in plain words."""


def tidy_url(raw: str) -> str:
    """The job's link as the Splat should show it, or TipError. https only, a host
    name (not an address), not a job site that stands in front of the employer.
    A LinkedIn or Indeed link becomes its plain job address, without tracking."""
    u = urllib.parse.urlsplit((raw or "").strip())
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or "." not in host or u.username or u.password:
        raise TipError("The job link has to start with https://.")
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip or host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        raise TipError("Use the job's web address.")
    if NOT_A_SOURCE.search(host):
        raise TipError("That site stands between you and the employer. Share the employer's own posting, "
                       "or the LinkedIn or Indeed link if that's the only place it is.")
    if re.search(r"(?:^|\.)linkedin\.com$", host):
        m = LINKEDIN_ID.search(u.path + "?" + u.query)
        if not m:
            raise TipError("That LinkedIn link isn't a job posting. Open the job and copy its link.")
        return f"https://www.linkedin.com/jobs/view/{m[1]}/"
    if re.search(r"(?:^|\.)indeed\.com$", host):
        m = INDEED_JK.search("?" + u.query)
        if not m:
            raise TipError("That Indeed link isn't a job posting. Open the job and copy its link.")
        return f"https://www.indeed.com/viewjob?jk={m[1]}"
    return urllib.parse.urlunsplit(("https", u.netloc.lower(), u.path or "/", u.query, ""))


def clean_tip(form: dict) -> dict:
    t = {k: re.sub(r"\s+", " ", str(form.get(k) or "")).strip()[:n] for k, n in TIP_FIELDS.items()}
    for k, what in (("title", "the job title"), ("company", "the employer"), ("location", "where the job is")):
        if len(t[k]) < 2:
            raise TipError(f"Please add {what}.")
    t["url"] = tidy_url(t["url"])
    return t


def tip_sig(tip_id: str) -> str:
    key = os.environ.get("TIP_SECRET", "")
    return hmac.new(key.encode(), tip_id.encode(), hashlib.sha256).hexdigest()[:32] if key else ""


def load_tips() -> list:
    try:
        d = json.loads(TIPS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def save_tips(tips: list) -> None:
    tmp = TIPS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(tips, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, TIPS_FILE)


def add_tip(tip: dict) -> tuple[dict, bool]:
    """Store a tip; (the stored tip, whether it is new). A link already shared and
    not removed is not stored twice. Tips past TIP_KEEP_DAYS are dropped."""
    today = datetime.now(timezone.utc).date()
    with _tips_lock:
        tips = [t for t in load_tips()
                if (today - datetime.fromisoformat(str(t.get("shared") or "2000-01-01")).date()).days <= TIP_KEEP_DAYS]
        for t in tips:
            if t.get("url") == tip["url"] and t.get("status") != "removed":
                return t, False
        new = {"id": secrets.token_hex(8), "shared": today.isoformat(), **tip, "status": "new"}
        tips.append(new)
        save_tips(tips)
    return new, True


def remove_tip(tip_id: str, sig: str) -> bool:
    good = tip_sig(str(tip_id))
    if not good or not hmac.compare_digest(good, str(sig or "")):
        return False
    with _tips_lock:
        tips = load_tips()
        hit = [t for t in tips if t.get("id") == tip_id]
        for t in hit:
            t["status"] = "removed"
        if hit:
            save_tips(tips)
    return bool(hit)


def tip_mail_text(t: dict) -> str:
    sig = tip_sig(t["id"])
    lines = [f"{t['title']}\n{t['company']} · {t['location']}\n{t['url']}\n"]
    if t.get("note"):
        lines.append(f"Their note (not shown on the page):\n{t['note']}\n")
    lines.append("It goes on the Splat at the next morning run if the rules and Jev keep it. "
                 "A LinkedIn or Indeed link shows as such unless the employer's own posting turns up.\n")
    lines.append(f"Remove it: {PAGE}#tip-review={t['id']}.{sig}" if sig else "(No TIP_SECRET set, so no remove link.)")
    return "\n".join(lines)


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
    send_mail("Splat intake: " + (form.get("name") or "someone"), body_text(form), form.get("email", ""))


def send_mail(subject: str, content: str, reply_to: str = "") -> None:
    msg = {"subject": subject, "body": {"contentType": "Text", "content": content},
           "toRecipients": [{"emailAddress": {"address": os.environ.get("INTAKE_TO", "")}}]}
    if "@" in reply_to:
        msg["replyTo"] = [{"emailAddress": {"address": reply_to}}]
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
        if form.get("kind") == "tip_review":
            ok = remove_tip(str(form.get("id") or ""), str(form.get("sig") or ""))
            return self.reply(200 if ok else 403, {"ok": ok, **({} if ok else {"error": "That remove link is not valid."})})
        if form.get("kind") == "tip":
            try:
                tip, new = add_tip(clean_tip(form))
            except TipError as e:
                return self.reply(400, {"ok": False, "error": str(e)})
            except OSError as e:  # the tips file: say what failed in the log
                print(f"tip not saved: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
                return self.reply(502, {"ok": False, "error": "It did not save. Please try again in a bit."})
            if new:
                try:
                    send_mail(f"Splat tip: {tip['title']} at {tip['company']}", tip_mail_text(tip))
                except Exception as e:  # noqa: BLE001 - the tip is saved; only the notice failed
                    print(f"tip mail failed: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
            return self.reply(200, {"ok": True, "new": new})
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
