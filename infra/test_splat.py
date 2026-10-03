"""Rules tests for site/cv/job-sources/daily-splat/splat.py. Stdlib only.

    python3 infra/daily-splat/test_splat.py

Every case below is a real location or title string a source returned on
2026-10-02, most of them a bug the first sample run shipped.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("splat", ROOT / "site/cv/job-sources/daily-splat/splat.py")
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
]

TRACK = [
    ("Staff Security Engineer - Engineer", "cyber"),
    ("Manager, MDR & Threat Hunting", "cyber"),
    ("Senior Software Engineer, Backend", "eng"),
    ("Member of Technical Staff", "eng"),
    ("Site Reliability Engineer", "eng"),
    ("Strategic Sourcing Manager, Engineering Technology & Software", None),
    ("Sr. Manager, Electrical Engineer - Data Centers", None),
    ("Security Officer", None),
    ("Account Executive, Security", None),
    ("Senior Product Manager, Platform", None),
    ("Technical Program Manager - Security", "cyber"),
    ("Semiconductor Equipment Engineer", None),
    ("Sr. Design Validation Product Engineer, DEG", None),
]

NORM = [
    ("Amazon.com", "Amazon"),
    ("Amazon Web Services, Inc.", "Amazon"),
]

LEVEL = [
    ("Software Engineer I", None, None),
    ("Software Engineer II", None, "mid"),
    ("Security Engineering Intern", None, None),
    ("Director, Security Engineering", None, None),
    ("Sr. Security Engineer (Detection)", None, "senior"),
    ("Principal Site Reliability Engineer", None, "staff"),
    ("Staff+ Software Engineer, Account Creation", None, "staff"),
    ("DevOps Engineer", "SE", "senior"),
    ("DevOps Engineer", "EN", None),
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
    for a, b in NORM:
        if splat.norm(a) != splat.norm(b):
            bad += 1
            print(f"norm({a!r}) = {splat.norm(a)!r} != norm({b!r}) = {splat.norm(b)!r}")
    total = len(WHERE) + len(TRACK) + len(LEVEL) + len(NORM)
    print(f"{total - bad}/{total} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
