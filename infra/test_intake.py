"""Tests for infra/intake.py's "Share a job" path. Stdlib only, no network.

    python3 infra/test_intake.py
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp())
os.environ["TIPS_FILE"] = str(tmp / "tips.json")
os.environ["TIP_SECRET"] = "test-secret"
spec = importlib.util.spec_from_file_location("intake", ROOT / "infra" / "intake.py")
intake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(intake)

bad = total = 0


def eq(label, got, want):
    global bad, total
    total += 1
    if got != want:
        bad += 1
        print(f"{label} = {got!r}, want {want!r}")


def err(label, fn):
    """The call raises TipError (a reason shown to the member)."""
    global bad, total
    total += 1
    try:
        fn()
    except intake.TipError:
        return
    bad += 1
    print(f"{label}: no TipError")


# Links: LinkedIn and Indeed to their plain job address; others kept, less the fragment.
TIDY = [
    ("https://www.linkedin.com/jobs/view/4012345678/?refId=abc&trackingId=x", "https://www.linkedin.com/jobs/view/4012345678/"),
    ("https://www.linkedin.com/jobs/view/it-systems-security-administrator-at-hilgers-pllc-4012345678",
     "https://www.linkedin.com/jobs/view/4012345678/"),
    ("https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4012345678",
     "https://www.linkedin.com/jobs/view/4012345678/"),
    ("https://www.indeed.com/viewjob?jk=0a1b2c3d4e5f6789&from=serp", "https://www.indeed.com/viewjob?jk=0a1b2c3d4e5f6789"),
    ("https://www.indeed.com/jobs?q=sysadmin&vjk=0a1b2c3d4e5f6789", "https://www.indeed.com/viewjob?jk=0a1b2c3d4e5f6789"),
    ("https://job-boards.greenhouse.io/okta/jobs/7103874?gh_src=x#apply", "https://job-boards.greenhouse.io/okta/jobs/7103874?gh_src=x"),
    ("  https://Careers.Example.com/jobs/42  ", "https://careers.example.com/jobs/42"),
]
for raw, want in TIDY:
    eq(f"tidy_url({raw!r})", intake.tidy_url(raw), want)
for raw in ("http://example.com/job", "ftp://example.com/x", "https://10.0.0.5/job", "https://[::1]/job",
            "https://localhost/job", "https://printer.local/job", "https://user:pw@example.com/job",
            "https://www.dice.com/job-detail/123", "https://www.ziprecruiter.com/c/x/Job/y",
            "https://www.linkedin.com/in/someone", "https://www.indeed.com/cmp/acme", "not a link", ""):
    err(f"tidy_url({raw!r})", lambda raw=raw: intake.tidy_url(raw))

# A tip needs a title, an employer and a place; the rest is trimmed to size.
good = {"url": "https://www.linkedin.com/jobs/view/4012345678/", "title": "IT Systems  Security Administrator",
        "company": "Hilgers PLLC", "location": "Remote US", "note": "x" * 900, "website": "", "kind": "tip"}
t = intake.clean_tip(good)
eq("clean_tip title", t["title"], "IT Systems Security Administrator")
eq("clean_tip note length", len(t["note"]), 500)
eq("clean_tip keys", sorted(t), ["company", "location", "note", "title", "url"])
for k in ("title", "company", "location"):
    err(f"clean_tip without {k}", lambda k=k: intake.clean_tip({**good, k: " "}))

# Store, dedupe, remove with the signed link only.
tip, new = intake.add_tip(t)
eq("add_tip new", new, True)
eq("add_tip status", tip["status"], "new")
again, new2 = intake.add_tip(dict(t))
eq("add_tip same link again", (new2, again["id"]), (False, tip["id"]))
stored = json.loads(Path(os.environ["TIPS_FILE"]).read_text(encoding="utf-8"))
eq("tips file holds one tip", len(stored), 1)
eq("tips file keeps nothing about the member", sorted(stored[0]),
   ["company", "id", "location", "note", "shared", "status", "title", "url"])
eq("remove with a wrong signature", intake.remove_tip(tip["id"], "0" * 32), False)
eq("remove with the signature", intake.remove_tip(tip["id"], intake.tip_sig(tip["id"])), True)
eq("removed in the file", json.loads(Path(os.environ["TIPS_FILE"]).read_text(encoding="utf-8"))[0]["status"], "removed")
_, new3 = intake.add_tip(dict(t))
eq("a removed link can be shared again", new3, True)
eq("the email carries the remove link", f"#tip-review={tip['id']}.{intake.tip_sig(tip['id'])}" in intake.tip_mail_text(tip), True)
os.environ["TIP_SECRET"] = ""
eq("no secret: no remove link works", intake.remove_tip(tip["id"], intake.tip_sig(tip["id"])), False)

print(f"{total - bad}/{total} passed")
sys.exit(1 if bad else 0)
