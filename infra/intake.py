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
    "pieces_other": ("Other pieces, in their words", 300),
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
# The tip's place, title and employer are checked with splat.py's own rules, so a
# member hears now, in plain words, why a job would never show (the review found the
# form accepted tips the morning run could never publish).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import splat  # noqa: E402

TIPS_FILE = Path(os.environ.get("TIPS_FILE") or "/var/lib/daily-splat/tips.json")
TIP_KEEP_DAYS = 31        # splat.py shows a tip at most 30 days (14 for LinkedIn/Indeed)
TIPS_PER_DAY = 40         # new tips a day, all visitors together: a flood waits for tomorrow
TIPS_ACTIVE_MAX = 300     # tips held at once
PAGE = "https://worksearchcollective.com/splat/"
TIP_FIELDS = {"url": 500, "title": 160, "company": 120, "location": 120, "note": 500}
_tips_lock = threading.Lock()
# The last number in a /jobs/view/ slug is the job (a title can hold a req number),
# or the currentJobId of a search page.
LINKEDIN_ID = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d{6,})(?=[/?#]|$)|[?&]currentJobId=(\d{6,})")
INDEED_JK = re.compile(r"[?&](?:jk|vjk)=([0-9a-f]{8,32})\b", re.I)
SHORT_LINK = re.compile(r"(?:^|\.)(?:lnkd\.in|to\.indeed\.com)$", re.I)


class TipError(ValueError):
    """A reason to show the member, in plain words."""


def tidy_url(raw: str) -> str:
    """The job's link as the Splat should show it, or TipError. https only, a host
    name in plain ASCII after IDNA (so a full-width or look-alike spelling of
    linkedin.com is not mistaken for, or hidden from, the real one), not an address,
    not a job site that stands in front of the employer, not a site the Splat may not
    read. A LinkedIn or Indeed link becomes its plain job address, without tracking."""
    u = urllib.parse.urlsplit((raw or "").strip())
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise TipError("The job link has to start with https://.")
    try:
        host = u.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        port = u.port
    except (UnicodeError, ValueError):
        raise TipError("That link's web address doesn't look right.") from None
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip or "." not in host or host.endswith((".local", ".internal", ".localhost")):
        raise TipError("Use the job's web address.")
    url = urllib.parse.urlunsplit(("https", host + (f":{port}" if port else ""), u.path or "/", u.query, ""))
    if SHORT_LINK.search(host):
        raise TipError("That's a short link. Open the job and copy the address from your browser's address bar.")
    if re.search(r"(?:^|\.)linkedin\.com$", host):
        m = LINKEDIN_ID.search(u.path + "?" + u.query)
        if not m:
            raise TipError("That LinkedIn link isn't a job posting. Open the job and copy its link.")
        return f"https://www.linkedin.com/jobs/view/{m[1] or m[2]}/"
    if re.search(r"(?:^|\.)indeed\.com$", host):
        m = INDEED_JK.search("?" + u.query)
        if not m:
            raise TipError("That Indeed link isn't a job posting. Open the job and copy its link.")
        return f"https://{'www.indeed.com' if host == 'indeed.com' else host}/viewjob?jk={m[1]}"
    if splat.MIDDLEMAN.search(url):
        raise TipError("That site stands between you and the employer. Share the employer's own posting, "
                       "or the LinkedIn or Indeed link if that's the only place it is.")
    if splat.NO_FETCH.search(url):
        raise TipError("That job site doesn't allow its listings to be copied, so the Splat can't show it. "
                       "Post it in the Cabal chat instead.")
    return url


class NeedDetails(TipError):
    """The link alone does not say what the job is: the form asks for the details."""


FIRST_URL = re.compile(r"https://[^\s<>\"']+", re.I)


def quick_get(url: str, as_json: bool = True):
    """A short, guarded fetch while the member waits (nginx gives the request 20
    seconds): https to a public host only, never a NO_FETCH site, 6 seconds in all,
    a few hundred KB at most."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or not splat.public_host(u.hostname or "") or splat.NO_FETCH.search(url):
        raise TipError("not fetched")
    s = splat.http_text(url, headers={"Accept": "application/json" if as_json else "text/html"}, tries=1, timeout=6,
                        limit=20_000_000 if as_json else 500_000, opener=splat.PUBLIC, deadline=6)
    return json.loads(s) if as_json else s


def _place(*parts) -> str:
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


def describe(url: str) -> dict:
    """What the posting at a link says it is: title, company, location, as far as a
    quick read can tell. From the tracking system's own API where the Splat knows it
    (Greenhouse, Lever, Ashby, Workday), else the page's schema.org JobPosting. Never
    LinkedIn or Indeed (the Splat does not read them). Empty when it cannot tell."""
    out: dict = {}
    try:
        if m := splat.GH_URL.search(url):
            j = quick_get(f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}")
            out = {"title": j.get("title"), "location": (j.get("location") or {}).get("name"),
                   "company": quick_get(f"https://boards-api.greenhouse.io/v1/boards/{m[1]}").get("name")}
        elif m := splat.LEVER_URL.search(url):
            j = quick_get(f"https://api{'.eu' if m[1] else ''}.lever.co/v0/postings/{m[2]}/{m[3]}")
            out = {"title": j.get("text"), "location": (j.get("categories") or {}).get("location"),
                   "company": splat.LEVER.get(m[2]) or m[2].replace("-", " ").title()}
        elif m := splat.ASHBY_URL.search(url):
            org = urllib.parse.unquote(m[1])
            jobs = quick_get(f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(org)}").get("jobs") or []
            j = next((x for x in jobs if x.get("id") == m[2]), {})
            loc = j.get("location") or ""
            if j.get("isRemote") and "remote" not in loc.lower():
                loc = f"{loc} (remote)" if loc else "Remote US"
            # Ashby's board names no employer; only a board the Splat already reads has one.
            out = {"title": j.get("title"), "location": loc, "company": splat.ASHBY.get(org.lower())}
        elif m := splat.WD_URL.search(url):
            info = quick_get(f"https://{m[1]}.{m[2]}.myworkdayjobs.com/wday/cxs/{m[1]}/{m[3]}/{m[4]}").get("jobPostingInfo") or {}
            names = {w[0]: w[3] for w in (*splat.WORKDAY, *splat.WORKDAY_LOCAL, *splat.WORKDAY_ALL_OREGON)}
            out = {"title": info.get("title"), "location": info.get("location"),
                   "company": names.get(m[1].lower()) or ((info.get("hiringOrganization") or {}).get("name") if isinstance(info.get("hiringOrganization"), dict) else None)}
        elif not splat.GATE_HOST.match(url) and not splat.NO_FETCH.search(url):
            for blob in splat.ld_blobs(quick_get(url, as_json=False)):
                try:
                    data = json.loads(blob.strip())
                except ValueError:
                    continue
                node = next((n for n in splat._ld_nodes(data) if "JobPosting" in str(n.get("@type"))), None)
                if not node:
                    continue
                org = node.get("hiringOrganization")
                locs = node.get("jobLocation")
                loc = (locs[0] if isinstance(locs, list) and locs else locs) or {}
                addr = loc.get("address") if isinstance(loc, dict) else {}
                addr = addr if isinstance(addr, dict) else {}
                place = _place(addr.get("addressLocality"), addr.get("addressRegion"))
                if "TELECOMMUTE" in str(node.get("jobLocationType")).upper():
                    place = f"{place} (remote)" if place else "Remote US"
                out = {"title": node.get("title"), "company": org.get("name") if isinstance(org, dict) else org,
                       "location": place}
                break
    except Exception:  # noqa: BLE001 - any failure means: ask the member
        return {}
    return {k: splat.text(str(v), TIP_FIELDS[k]) for k, v in out.items() if v and str(v).strip()}


def clean_tip(form: dict, look=None) -> dict:
    """The tip, or TipError. Only the link is required: the title, employer and place
    come from the posting itself when the Splat can read it (describe; `look` is for
    tests), from the member otherwise, the member's words winning. NeedDetails when
    neither says. Then they are checked with the Splat's own rules, so the member
    learns now why it would never show."""
    t = {k: re.sub(r"\s+", " ", str(form.get(k) or "")).strip()[:n] for k, n in TIP_FIELDS.items()}
    raw = str(form.get("url") or "")[:5000]
    m = FIRST_URL.search(raw)  # a pasted bit of text with the link in it works too
    if not m:
        raise TipError("Paste the link to the job (it starts with https://).")
    t["url"] = tidy_url(m[0].rstrip(".,;:)]}"))
    if any(len(t[k]) < 2 for k in ("title", "company", "location")):
        found = (look or describe)(t["url"])
        for k in ("title", "company", "location"):
            if len(t[k]) < 2 and found.get(k):
                t[k] = found[k]
    missing = [k for k in ("title", "company", "location") if len(t[k]) < 2]
    if missing:
        why = ("The Splat doesn't read LinkedIn or Indeed, so it can't see what this job is."
               if splat.GATE_HOST.match(t["url"]) else "The Splat couldn't read what this job is from the link.")
        names = {"title": "the job title", "company": "the employer", "location": "where it is"}
        ask = [names[k] for k in missing]
        what = ask[0] if len(ask) == 1 else ", ".join(ask[:-1]) + " and " + ask[-1]
        if not splat.GATE_HOST.match(t["url"]) and len(missing) < 3:
            why = "The Splat read the posting but couldn't tell " + what + "."
        raise NeedDetails(f"{why} Add {what} under \"Add details\" and send it again.")
    if splat.AGENCY.search(t["company"]):
        raise TipError("The Splat lists employers, not recruiters or staffing firms. "
                       "Share the employer's own posting if you can find it.")
    if not splat.where(t["location"], bool(splat.REMOTE.search(t["location"]))):
        raise TipError("The Splat lists jobs in Oregon, Washington and Idaho, or remote anywhere in the US. "
                       "Write the place like \"Portland, OR\", \"Seattle, WA\" or \"Remote US\".")
    if not splat.rule_track(t["title"]) or not splat.rule_level(t["title"]):
        raise TipError("That job isn't one the Splat lists: cyber, infra and IT, tech PM or sales engineering, "
                       "entry to principal (no internships or directors).")
    return t


def tip_sig(tip_id: str, action: str) -> str:
    """The signature on David's links: one per tip and action, so a remove link can
    never approve. Empty without TIP_SECRET (and then no tip is taken)."""
    key = os.environ.get("TIP_SECRET", "")
    return hmac.new(key.encode(), f"{action}:{tip_id}".encode(), hashlib.sha256).hexdigest()[:32] if key else ""


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


def tip_age(t: dict, today) -> int:
    try:
        return (today - datetime.fromisoformat(str(t.get("shared"))).date()).days
    except ValueError:
        return 10_000


def tip_showing(t: dict, today) -> bool:
    """Could the morning run still show it? Not removed, and inside its window."""
    days = splat.TIP_GATED_DAYS if splat.GATE_HOST.match(t.get("url") or "") else splat.MAX_AGE_DAYS
    return t.get("status") != "removed" and tip_age(t, today) <= days


def add_tip(tip: dict) -> tuple[dict, bool]:
    """Store a tip; (the stored tip, whether it is new). A link that could still be
    showing is not stored twice; one past its window can be shared again. TipError
    when today's share or the held total is full."""
    today = datetime.now(timezone.utc).date()
    with _tips_lock:
        tips = [t for t in load_tips() if tip_age(t, today) <= TIP_KEEP_DAYS]
        for t in tips:
            if t.get("url") == tip["url"] and tip_showing(t, today):
                return t, False
        if sum(1 for t in tips if tip_age(t, today) == 0) >= TIPS_PER_DAY:
            raise TipError("Lots of jobs were shared today. Please try again tomorrow.")
        if sum(1 for t in tips if tip_showing(t, today)) >= TIPS_ACTIVE_MAX:
            raise TipError("The shared list is full right now. Please try again in a few days.")
        new = {"id": secrets.token_hex(8), "shared": today.isoformat(), **tip, "status": "new"}
        tips.append(new)
        save_tips(tips)
    return new, True


def review_tip(tip_id: str, action: str, sig: str) -> bool:
    """David's link: "remove" takes a tip off at the next run; "approve" lets a link
    the reader cannot vouch for show. Only with the matching signature."""
    if action not in ("remove", "approve"):
        return False
    good = tip_sig(str(tip_id), action)
    if not good or not hmac.compare_digest(good, str(sig or "")):
        return False
    with _tips_lock:
        tips = load_tips()
        hit = [t for t in tips if t.get("id") == tip_id]
        for t in hit:
            t["status"] = "removed" if action == "remove" else ("approved" if t.get("status") != "removed" else "removed")
        if hit:
            save_tips(tips)
    return bool(hit)


def needs_approval(t: dict) -> bool:
    """A link that is neither LinkedIn/Indeed nor a tracking system the reader reads
    through its API shows only when David approves it."""
    return not splat.GATE_HOST.match(t["url"]) and not splat.ats_link(t["url"])


def tip_mail_text(t: dict) -> str:
    link = lambda action, kind: f"{PAGE}#tip-{kind}={t['id']}.{tip_sig(t['id'], action)}"  # noqa: E731
    lines = [f"{t['title']}\n{t['company']} · {t['location']}\n{t['url']}\n"]
    if t.get("note"):
        lines.append(f"Their note (only in this email; not on the page, not given to the classifier):\n{t['note']}\n")
    if needs_approval(t):
        lines.append("This link isn't on a job system the Splat can check, so it shows only if you approve it.\n"
                     f"Approve it: {link('approve', 'approve')}\n")
    else:
        lines.append("It shows after the next morning run if the rules and Jev keep it. A LinkedIn or Indeed link "
                     "shows as such unless the employer's own posting turns up.\n")
    lines.append(f"Remove it: {link('remove', 'review')}")
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
        if not isinstance(form, dict):
            return self.reply(400, {"ok": False})
        # David's signed links: checked by signature, not by the visitor limits below,
        # so he can clear a flood (the review).
        if form.get("kind") == "tip_review":
            ok = review_tip(str(form.get("id") or ""), str(form.get("action") or ""), str(form.get("sig") or ""))
            return self.reply(200 if ok else 403, {"ok": ok, **({} if ok else {"error": "That link is not valid."})})
        # The trap and the timer: answer "ok" so a bot learns nothing. The page always
        # sends the time it loaded; a post without it is not from the page.
        if form.get("website") or not form.get("t") or (time.time() - float(form.get("t") or 0) / 1000) < MIN_SECONDS:
            return self.reply(200, {"ok": True})
        ip = self.headers.get("X-Real-IP") or self.client_address[0]  # nginx sets it from the connection
        now = time.time()
        if len(_hits) > 5000:  # forget visitors with nothing in the last hour
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > 3600]:
                del _hits[k]
        recent = [t for t in _hits.get(ip, []) if now - t < 3600]
        if len(recent) >= PER_HOUR:
            return self.reply(429, {"ok": False, "error": "Too many in an hour. Try again later."})
        _hits[ip] = recent + [now]
        if form.get("kind") == "tip":
            if not os.environ.get("TIP_SECRET"):  # no way to remove a bad tip: take none
                print("tip refused: no TIP_SECRET", file=sys.stderr)
                return self.reply(503, {"ok": False, "error": "Sharing is switched off right now. Please try again later."})
            try:
                tip, new = add_tip(clean_tip(form))
            except NeedDetails as e:  # the form opens its optional fields and asks for these
                return self.reply(400, {"ok": False, "need": "details", "error": str(e)})
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
            return self.reply(200, {"ok": True, "new": new, "review": new and needs_approval(tip),
                                    "summary": f"{tip['title']} at {tip['company']}, {tip['location']}"})
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
