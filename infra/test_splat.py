"""Rules tests for splat.py. Stdlib only.

    python3 infra/test_splat.py

Every case below is a real location or title string a source returned on
2026-10-02, most of them a bug the first sample run shipped.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("splat", ROOT / "splat.py")
splat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(splat)

WHERE = [
    # (location, remote flag, expected)
    ("Seattle, WA", False, "pnw"),
    ("US-CA-Menlo Park; US-WA-Bellevue", False, "pnw"),
    ("Bellevue, Washington; Chicago, Illinois", False, "pnw"),
    ("Portland, Oregon, USA", False, "pnw"),
    ("Boise, ID", False, "pnw"),
    ("United States, Washington, Redmond; United States, Oregon, Hillsboro", False, "pnw"),
    ("Washington, DC", False, None),
    ("Washington D.C.", False, None),
    ("San Francisco, CA | New York City, NY | Washington, DC", False, None),
    ("Remote - US", False, "remote"),
    ("Remote U.S.", False, "remote"),
    (" -REMOTE, USA-", False, "remote"),
    ("Remote - Illinois, USA; Remote - Indiana, USA", False, "remote"),
    ("Remote · Portugal,  USA", True, "remote"),
    ("Remote · ", True, "remote"),
    ("Flexible / Remote", False, "remote"),
    ("Remote · anywhere", True, "remote"),
    ("Remote · United Arab Emirates", True, None),
    ("Remote · Latin America", True, None),
    ("Remote, Canada", False, None),
    ("Anywhere in Europe", True, None),
    ("NL-Amsterdam", True, None),           # Ashby isRemote on an onsite role
    ("New York City", True, None),
    ("San Francisco", False, None),
    ("PL-Warsaw-Lixa C", True, None),
    # Read as PNW on 2026-10-03, none of them are:
    ("USA-DC-Washington (remote)", True, "remote"),
    ("USA, DC, Washington (935 Pennslyv Ave NW), United States (remote)", True, "remote"),
    ("Wash, 213 Washington St., Newark, NJ, United States", False, None),
    ("NY OR US Remote, US", True, "remote"),
    ("Portland, ME", False, None),
    # And these are:
    ("US-WA-Spokane-AD WA (WA019), United States", False, "pnw"),
    ("Seattle, WA | Washington, DC", False, "pnw"),
    ("Keyport, Washington, United States", False, "pnw"),
    ("Hillsboro, OR 97124", False, "pnw"),
    ("Remote - Seattle", True, "pnw"),
    # A Foorilla US-only query row with a bare city, after the source tags it.
    ("San Francisco (remote) · United States", True, "remote"),
]

TRACK = [
    ("Staff Security Engineer - Engineer", "cyber"),
    ("Manager, MDR & Threat Hunting", "cyber"),
    # Product SWE is out: "mostly SWE roles, not infosec" (the cabal, 2026-10-03).
    ("Senior Software Engineer, Backend", None),
    ("Member of Technical Staff", None),
    ("Frontend Software Engineer, Codex App", None),
    ("Senior Machine Learning Engineer", None),
    ("iOS Software Engineer, Consumer Engineering", None),
    ("Site Reliability Engineer", "eng"),
    ("Senior Cloud Infrastructure Engineer", "eng"),
    ("Network Engineer III", "eng"),
    ("Systems Administrator, Linux", "eng"),
    ("Sr. Security Engineer (Detection)", "cyber"),
    ("SOC Analyst II", "cyber"),
    # Entry-lane titles: support and sysadmin count as infra & IT.
    ("Help Desk Technician, Tier 1", "eng"),
    ("IT Support Specialist", "eng"),
    ("Technical Support Engineer", "eng"),
    ("Junior Systems Administrator", "eng"),
    ("Desktop Support Analyst", "eng"),
    ("Data Center Technician", "eng"),
    ("NOC Technician", "eng"),
    ("Customer Support Specialist", None),
    ("Customer Service Representative", None),
    ("Fiber Technician", None),
    ("Data Platform Engineer", None),
    ("ML Platform Engineer", None),
    ("Software Engineer - E-commerce Recommendation Infrastructure", None),
    ("Data Security Engineer", "cyber"),
    ("Information System Security Officer (ISSO)", "cyber"),
    ("Privacy Operations Lead", "cyber"),
    ("Strategic Sourcing Manager, Engineering Technology & Software", None),
    ("Sr. Manager, Electrical Engineer - Data Centers", None),
    ("Security Officer", None),
    ("Account Executive, Security", None),
    ("Senior Product Manager, Platform", None),
    ("Technical Program Manager - Security", "cyber"),
    ("Semiconductor Equipment Engineer", None),
    ("Sr. Design Validation Product Engineer, DEG", None),
]

# Titles Foorilla filed under its infosec topics, where the title alone is not
# enough. The rule lets a role-shaped title through; Jev makes the real call.
TRACK_SEC_TOPIC = [
    ("Principal Solutions Engineer", "cyber"),
    ("Mid-Level Data Engineer", "cyber"),
    ("Account Executive", None),
]

NORM = [
    ("Amazon.com", "Amazon"),
    ("Amazon Web Services, Inc.", "Amazon"),
]

LEVEL = [
    # The entry lane (2026-10-04): junior roles are kept and labelled; interns are not.
    ("Software Engineer I", None, "entry"),
    ("Software Engineer II", None, "mid"),
    ("Security Engineering Intern", None, None),
    ("IT Support Co-op", None, None),
    ("Junior Systems Administrator", None, "entry"),
    ("Associate Security Analyst", None, "entry"),
    ("Help Desk Technician, Tier 1", None, "entry"),
    ("SOC Analyst I", None, "entry"),
    ("Field Security Engineer - Sr. Security Engineer I", None, "senior"),
    ("New College Grad - IT Software Support Engineer", None, "entry"),
    ("Senior Associate, Cyber Risk", None, "senior"),
    ("Associate Director, Security", None, None),
    ("Director, Security Engineering", None, None),
    ("Sr. Security Engineer (Detection)", None, "senior"),
    ("Principal Site Reliability Engineer", None, "staff"),
    ("Staff+ Software Engineer, Account Creation", None, "staff"),
    ("DevOps Engineer", "SE", "senior"),
    ("DevOps Engineer", "EN", "entry"),
    ("DevOps Engineer", "EX", None),
    ("DevOps Engineer", None, "mid"),
]


def main() -> int:
    bad = 0
    for loc, flag, want in WHERE:
        got = splat.where(loc, flag)
        if got != want:
            bad += 1
            print(f"where({loc!r}, {flag}) = {got!r}, want {want!r}")
    for title, want in TRACK:
        got = splat.rule_track(title)
        if got != want:
            bad += 1
            print(f"rule_track({title!r}) = {got!r}, want {want!r}")
    for title, hint, want in LEVEL:
        got = splat.rule_level(title, hint)
        if got != want:
            bad += 1
            print(f"rule_level({title!r}, {hint!r}) = {got!r}, want {want!r}")
    for title, want in TRACK_SEC_TOPIC:
        got = splat.rule_track(title, True)
        if got != want:
            bad += 1
            print(f"rule_track({title!r}, sec_topic) = {got!r}, want {want!r}")
    for a, b in NORM:
        if splat.norm(a) != splat.norm(b):
            bad += 1
            print(f"norm({a!r}) = {splat.norm(a)!r} != norm({b!r}) = {splat.norm(b)!r}")
    total = len(WHERE) + len(TRACK) + len(TRACK_SEC_TOPIC) + len(LEVEL) + len(NORM)
    print(f"{total - bad}/{total} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
