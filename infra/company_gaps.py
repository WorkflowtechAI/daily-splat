#!/usr/bin/env python3
"""Employers on the Daily Splat that company_names.json does not cover, busiest
first: the weekly review's work list. Federal contractors (cards flagged for a
clearance) come first among equals, since contracts are what most needs review.

    python3 infra/company_gaps.py                      # the live page
    python3 infra/company_gaps.py jobs.json --top 15 --json
"""

import argparse
import collections
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = "https://worksearchcollective.com/splat/jobs.json"
spec = importlib.util.spec_from_file_location("company_check", ROOT / "company_check.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", nargs="?", default=LIVE, help="a jobs.json path or URL (default: the live page)")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", action="store_true", help="print the list as JSON, with sample titles and places")
    args = ap.parse_args()
    if args.jobs.startswith("https://"):
        req = urllib.request.Request(args.jobs, headers={"User-Agent": cc.UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            jobs = json.load(r)["jobs"]
    else:
        jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))["jobs"]
    table = json.loads(cc.TABLE.read_text(encoding="utf-8"))
    covered = {cc.page_key(n) for e in table["employers"] for n in e.get("names") or []}
    covered |= {cc.page_key(u["name"]) for u in table.get("unclear") or []}   # reviewed and left out
    by = collections.defaultdict(list)
    for j in jobs:
        if j.get("company") and cc.page_key(j["company"]) not in covered:
            by[j["company"]].append(j)
    fed = lambda rows: any(j.get("clearance") or j.get("clearance_check") or j.get("clr") for j in rows)  # noqa: E731
    order = sorted(by, key=lambda c: (-len(by[c]), not fed(by[c]), c))[:args.top]
    rows = [{"company": c, "cards": len(by[c]), "federal": fed(by[c]), "titles": [j["title"] for j in by[c]][:3],
             "locations": sorted({j.get("location") or "" for j in by[c]})[:3]} for c in order]
    if args.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    else:
        print(f"{len(by)} employers on the page are not in company_names.json; the top {len(rows)}:")
        for r in rows:
            print(f"{r['cards']:3}  {'federal  ' if r['federal'] else '         '}{r['company']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
