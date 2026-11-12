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
TIPS = Path(os.environ["TIPS_FILE"])

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
    # A req number in the slug is not the job: the last number is (the review).
    ("https://www.linkedin.com/jobs/view/req-1234567-security-engineer-4012345678/",
     "https://www.linkedin.com/jobs/view/4012345678/"),
    ("https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4012345678",
     "https://www.linkedin.com/jobs/view/4012345678/"),
    ("https://www.indeed.com/viewjob?jk=0a1b2c3d4e5f6789&from=serp", "https://www.indeed.com/viewjob?jk=0a1b2c3d4e5f6789"),
    ("https://www.indeed.com/jobs?q=sysadmin&vjk=0a1b2c3d4e5f6789", "https://www.indeed.com/viewjob?jk=0a1b2c3d4e5f6789"),
    ("https://ca.indeed.com/viewjob?jk=0a1b2c3d4e5f6789", "https://ca.indeed.com/viewjob?jk=0a1b2c3d4e5f6789"),
    # Full-width letters are the same host after IDNA, not a way around the rules.
    ("https://ｗｗｗ.ｌｉｎｋｅｄｉｎ.com/jobs/view/4012345678/", "https://www.linkedin.com/jobs/view/4012345678/"),
    ("https://job-boards.greenhouse.io/okta/jobs/7103874?gh_src=x#apply", "https://job-boards.greenhouse.io/okta/jobs/7103874?gh_src=x"),
    ("  https://Careers.Example.com/jobs/42  ", "https://careers.example.com/jobs/42"),
]
for raw, want in TIDY:
    eq(f"tidy_url({raw!r})", intake.tidy_url(raw), want)
for raw in ("http://example.com/job", "ftp://example.com/x", "https://10.0.0.5/job", "https://[::1]/job",
            "https://localhost/job", "https://printer.local/job", "https://user:pw@example.com/job",
            "https://www.dice.com/job-detail/123", "https://www.ziprecruiter.com/c/x/Job/y", "https://haystack.co/j/1",
            "https://ｗｗｗ.ｄｉｃｅ.com/job-detail/1", "https://www.governmentjobs.com/careers/portlandor/jobs/1",
            "https://lnkd.in/gAbC123", "https://to.indeed.com/aaBbCc", "https://www.linkedin.com/in/someone",
            "https://www.indeed.com/cmp/acme", "not a link", ""):
    err(f"tidy_url({raw!r})", lambda raw=raw: intake.tidy_url(raw))

# A tip needs a title, an employer and a place the Splat lists; the rest is trimmed.
good = {"url": "https://www.linkedin.com/jobs/view/4012345678/", "title": "IT Systems  Security Administrator",
        "company": "Hilgers PLLC", "location": "Remote US", "note": "x" * 900, "website": "", "kind": "tip"}
t = intake.clean_tip(good)
eq("clean_tip title", t["title"], "IT Systems Security Administrator")
eq("clean_tip note length", len(t["note"]), 500)
eq("clean_tip keys", sorted(t), ["company", "location", "note", "title", "url"])
for k in ("title", "company", "location"):
    err(f"clean_tip without {k}", lambda k=k: intake.clean_tip({**good, k: " "}))
err("clean_tip outside the PNW", lambda: intake.clean_tip({**good, "location": "Austin, TX"}))
err("clean_tip a place nobody can read", lambda: intake.clean_tip({**good, "location": "PNW"}))
err("clean_tip outside the tracks", lambda: intake.clean_tip({**good, "title": "Barista"}))
err("clean_tip an internship", lambda: intake.clean_tip({**good, "title": "Security Engineering Intern"}))
err("clean_tip a staffing firm", lambda: intake.clean_tip({**good, "company": "Robert Half"}))
eq("clean_tip Portland", intake.clean_tip({**good, "location": "Portland, OR"})["location"], "Portland, OR")

# Store, dedupe, remove and approve with the matching signed link only.
tip, new = intake.add_tip(t)
eq("add_tip new", (new, tip["status"]), (True, "new"))
again, new2 = intake.add_tip(dict(t))
eq("add_tip same link again", (new2, again["id"]), (False, tip["id"]))
stored = json.loads(TIPS.read_text(encoding="utf-8"))
eq("tips file holds one tip", len(stored), 1)
eq("tips file keeps nothing about the member", sorted(stored[0]),
   ["company", "id", "location", "note", "shared", "status", "title", "url"])
eq("remove with a wrong signature", intake.review_tip(tip["id"], "remove", "0" * 32), False)
eq("approve with the remove signature", intake.review_tip(tip["id"], "approve", intake.tip_sig(tip["id"], "remove")), False)
eq("remove with the signature", intake.review_tip(tip["id"], "remove", intake.tip_sig(tip["id"], "remove")), True)
eq("removed in the file", json.loads(TIPS.read_text(encoding="utf-8"))[0]["status"], "removed")
_, new3 = intake.add_tip(dict(t))
eq("a removed link can be shared again", new3, True)

# An employer-site link needs David's approval; the email says so and carries both links.
site = intake.clean_tip({**good, "url": "https://careers.example.com/jobs/9"})
st, _ = intake.add_tip(site)
eq("needs_approval employer site", intake.needs_approval(st), True)
eq("needs_approval linkedin", intake.needs_approval(tip), False)
eq("needs_approval greenhouse", intake.needs_approval({"url": "https://boards.greenhouse.io/a/jobs/1"}), False)
mail = intake.tip_mail_text(st)
eq("the email carries the approve link", f"#tip-approve={st['id']}.{intake.tip_sig(st['id'], 'approve')}" in mail, True)
eq("the email carries the remove link", f"#tip-review={st['id']}.{intake.tip_sig(st['id'], 'remove')}" in mail, True)
eq("approve with the signature", intake.review_tip(st["id"], "approve", intake.tip_sig(st["id"], "approve")), True)
eq("approved in the file", [x["status"] for x in json.loads(TIPS.read_text(encoding="utf-8")) if x["id"] == st["id"]], ["approved"])

# A LinkedIn link past its two weeks can be shared again; a fresh one cannot.
old = json.loads(TIPS.read_text(encoding="utf-8"))
for x in old:
    if x["url"] == t["url"] and x["status"] != "removed":
        x["shared"] = "2000-01-01"
TIPS.write_text(json.dumps(old), encoding="utf-8")
_, new4 = intake.add_tip(dict(t))
eq("a LinkedIn tip past its window can be shared again", new4, True)

# Caps: a day's tips, all visitors together.
intake.TIPS_PER_DAY = len([x for x in json.loads(TIPS.read_text(encoding="utf-8")) if intake.tip_age(x, intake.datetime.now(intake.timezone.utc).date()) == 0])
err("add_tip past today's cap", lambda: intake.add_tip({**t, "url": "https://boards.greenhouse.io/a/jobs/2"}))
intake.TIPS_PER_DAY = 40

os.environ["TIP_SECRET"] = ""
eq("no secret: no link works", intake.review_tip(tip["id"], "remove", intake.tip_sig(tip["id"], "remove")), False)

print(f"{total - bad}/{total} passed")
sys.exit(1 if bad else 0)
