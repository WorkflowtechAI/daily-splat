"""Tests for company_check.py and company_names.json. Stdlib only, no network.

    python3 infra/test_company_check.py

The rule under test throughout: a record counts for an employer only when a
name on it equals one of the employer's reviewed legal names after
normalizing, never on a partial or a near match.
"""

import contextlib
import importlib.util
import io
import json
import re
import sqlite3
import sys
import tempfile
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("company_check", ROOT / "company_check.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)
cc.COURT_GAP = 0   # no pacing in tests
# splat.py, for the board patterns and keys the two files must share.
spec_splat = importlib.util.spec_from_file_location("splat", ROOT / "splat.py")
splat = importlib.util.module_from_spec(spec_splat)
spec_splat.loader.exec_module(splat)

bad = 0
total = 0


def check(what, got, want):
    global bad, total
    total += 1
    if got != want:
        bad += 1
        print(f"{what}: got {got!r}, want {want!r}")


# ── Names ────────────────────────────────────────────────────────────────────

SAME = [
    ("Amazon.com Services, LLC", "AMAZON.COM SERVICES LLC"),
    ("Lockheed Martin Corporation", "LOCKHEED MARTIN CORP"),
    ("The Boeing Company", "Boeing Co."),
    ("AT&T Inc.", "AT and T"),
    ("Johnson &amp; Johnson", "Johnson & Johnson"),
    ("Palantir Technologies Inc.", "PALANTIR TECHNOLOGIES, INC."),
    ("Bank of America, N.A.", "Bank of America"),
]
DIFFERENT = [
    ("Regence BlueShield", "Regency BlueShield"),     # CourtListener's search stems these together
    ("Amazon.com Services LLC", "Amazon.com Inc."),   # a subsidiary is its own legal name
    ("Axon Enterprise, Inc.", "Axon Enterprises, Inc."),
    ("Intel Corporation", "Intelligent Corporation"),
]
for a, b in SAME:
    check(f"norm_entity({a!r}) == norm_entity({b!r})", cc.norm_entity(a), cc.norm_entity(b))
for a, b in DIFFERENT:
    check(f"norm_entity({a!r}) != norm_entity({b!r})", cc.norm_entity(a) == cc.norm_entity(b), False)

# page_key must match the page's pageKey(): lowercase, & as "and", other runs of
# non-alphanumerics as one space, trimmed.
for name, want in [("AT&T", "at and t"), ("Amazon.com", "amazon com"), ("  Palantir  Technologies ", "palantir technologies"),
                   ("Booz Allen Hamilton", "booz allen hamilton"), ("T-Mobile", "t mobile")]:
    check(f"page_key({name!r})", cc.page_key(name), want)
page = (ROOT / "index.html").read_text(encoding="utf-8")
check("index.html pageKey() is the same recipe as page_key()",
      'const pageKey = (s) => (s || "").toLowerCase().replace(/&/g, " and ").replace(/[^a-z0-9]+/g, " ").trim();' in page, True)

E = {"id": "acme", "names": ["Acme"], "legal": ["Acme Corporation", "Acme Federal LLC", "Acme Holdings Inc"],
     "avoid": ["Acme Holdings, Inc."], "ueis": [{"uei": "AAAAAAAAAAAA", "name": "ACME CORP", "level": "P"},
                                               {"uei": "BBBBBBBBBBBB", "name": "ACME FEDERAL", "level": "C"}],
     "exclude_ueis": ["JVJVJVJVJVJV"], "vt": None}
check("legal_set drops a name the table says to avoid", cc.legal_set(E), {"acme", "acme federal"})
for name, want in [("The Boeing Company", "Boeing"), ("Amazon.com Services, LLC", "Amazon.com Services"),
                   ("CACI, Inc. - Federal", "CACI, Inc. - Federal"), ("Bank of America, N.A.", "Bank of America"),
                   ("Acme Corp., Inc.", "Acme"), ("Starbucks Corporation", "Starbucks")]:
    check(f"core_name({name!r})", cc.core_name(name), want)
check("matched_names: one legal name per normalized form, avoided ones left out",
      cc.matched_names({**E, "legal": E["legal"] + ["ACME CORP."]}), ["Acme Corporation", "Acme Federal LLC"])
# Every legal name is also someone else's once the suffix goes: the name-matched
# sources say they cannot tell, rather than "none found".
TWIN = {**E, "legal": ["Acme Corporation"], "avoid": ["Acme LLC"]}
check("labor: a name shared with another employer is not checked", cc.labor(TWIN, {"acme": [("Acme LLC",) * 7]}), cc.AMBIGUOUS)
check("pac: a name shared with another employer is not checked", cc.pac(TWIN, {"by_org": {}, "gifts": {}}), cc.AMBIGUOUS)
check("courts: a name shared with another employer is not searched", cc.courts(TWIN, "t0k", [10]), cc.AMBIGUOUS)

# ── Dates ────────────────────────────────────────────────────────────────────

check("fiscal_years in November", cc.fiscal_years(date(2026, 11, 9)), [2027, 2026, 2025])
check("fiscal_years in September", cc.fiscal_years(date(2026, 9, 30)), [2026, 2025, 2024])
check("cycles in an election year", cc.cycles(date(2026, 3, 1)), [2026, 2024])
check("cycles in an odd year", cc.cycles(date(2025, 3, 1)), [2026, 2024])

# ── Contracts ────────────────────────────────────────────────────────────────

# A small USAspending: transactions (recipient UEI, its parent UEI, agency, subtier
# code, amount). A search counts a transaction when its recipient or its
# recipient's parent is in the list, as recipient_search_text with UEIs does.
WORLD = [
    ("AAAAAAAAAAAA", "AAAAAAAAAAAA", "DOD", "ARMY", 100),
    ("AAAAAAAAAAAA", "AAAAAAAAAAAA", "DHS", "ICE", 20),
    ("AAAAAAAAAAAA", "AAAAAAAAAAAA", "DHS", "CBP", 5),
    ("AAAAAAAAAAAA", "AAAAAAAAAAAA", "GSA", "FAS", 7),
    ("JVJVJVJVJVJV", "AAAAAAAAAAAA", "DOD", "ARMY", 30),   # the joint venture, under the employer
    ("JVJVJVJVJVJV", "JVJVJVJVJVJV", "DOD", "ARMY", 50),   # the joint venture on its own: never counted here
    ("OOOOOOOOOOOO", "AAAAAAAAAAAA", "DHS", "ICE", 10),    # a unit the table gives another employer
]
TOP = {"Department of Defense": "DOD", "Department of Homeland Security": "DHS"}
SUBTIER = {"U.S. Immigration and Customs Enforcement": "ICE", "U.S. Customs and Border Protection": "CBP"}
calls = []


def fake_usa(url, data=None, headers=None, timeout=60):
    calls.append((url, data))
    if url.endswith("/api/v2/recipient/"):
        return {"results": [{"id": "abc-C", "uei": "AAAAAAAAAAAA", "recipient_level": "C", "name": "ACME CORP (child)"},
                            {"id": "abc-P", "uei": "AAAAAAAAAAAA", "recipient_level": "P", "name": "ACME CORP"}]}
    f = data["filters"]
    rows = [t for t in WORLD if t[0] in f["recipient_search_text"] or t[1] in f["recipient_search_text"]]
    for ag in f.get("agencies") or []:
        rows = [t for t in rows if (t[2] == TOP.get(ag["name"]) if ag["tier"] == "toptier" else t[3] == SUBTIER.get(ag["name"]))]
    if data["category"].endswith("_subagency"):
        by = {}
        for t in rows:
            by[(t[2], t[3])] = by.get((t[2], t[3]), 0) + t[4]
        return {"results": [{"amount": a, "agency_abbreviation": ab, "code": code} for (ab, code), a in by.items()]}
    by = {}
    for t in rows:
        by[t[0]] = by.get(t[0], 0) + t[4]
    res = [{"amount": a, "uei": u, "name": u} for u, a in sorted(by.items())]
    page = data["page"]
    return {"results": res[(page - 1) * 2:page * 2], "page_metadata": {"hasNext": len(res) > page * 2}}  # two to a page


cc.fetch_json = fake_usa
got = cc.contracts(E, frozenset({"OOOOOOOOOOOO", "AAAAAAAAAAAA"}))
check("contracts: less only what the joint venture and the other employer's unit added, by agency",
      {k: got["fy"][0][k] for k in ("total", "dod", "dhs", "ice", "cbp", "less")},
      {"total": 132, "dod": 100, "dhs": 25, "ice": 20, "cbp": 5, "less": 40})
check("contracts: names what came off", got["excluded"], ["JVJVJVJVJVJV", "OOOOOOOOOOOO"])
check("contracts: one search over every UEI at once, never one of the excluded",
      {tuple(d["filters"]["recipient_search_text"]) for u, d in calls if "spending_by_category" in u},
      {("AAAAAAAAAAAA", "BBBBBBBBBBBB")})
check("contracts: shares by the agency that paid",
      ({d["category"] for u, d in calls if d and d.get("category", "").endswith("_subagency")},
       {a["type"] for u, d in calls if d and "filters" in d for a in d["filters"].get("agencies") or []}), ({"funding_subagency"}, {"funding"}))
check("contracts: contracts only, no grants or loans",
      {tuple(d["filters"]["award_type_codes"]) for u, d in calls if "spending_by_category" in u}, {("A", "B", "C", "D")})
check("contracts: reads every page of recipients",
      max(d["page"] for u, d in calls if d and d.get("category") == "recipient"), 2)
check("contracts: links the record at the table's level", got["link"], "https://www.usaspending.gov/recipient/abc-P/latest")
calls.clear()
got = cc.contracts({**E, "exclude_ueis": []})
check("contracts: nothing to take off, no agency searches", (got["fy"][0]["total"], got["fy"][0]["less"],
      any(d.get("filters", {}).get("agencies") for u, d in calls if d)), (172, 0, False))
check("contracts: no UEIs, no facts", cc.contracts({**E, "ueis": []}), None)
cc.fetch_json = lambda url, data=None, headers=None, timeout=60: (
    {"results": []} if url.endswith("/api/v2/recipient/") else
    {"results": [{"amount": 1e9}], "messages": ["The recipient_search_text filter is not supported here"]})
try:
    cc.contracts(E)
    check("contracts: refuses totals when USAspending ignored the recipient filter", "no error", "error")
except RuntimeError:
    check("contracts: refuses totals when USAspending ignored the recipient filter", "error", "error")

# ── Labor board ──────────────────────────────────────────────────────────────

tmp = Path(tempfile.mkdtemp(prefix="cc-test-"))
recent = (date.today() - timedelta(days=200)).isoformat()
old = (date.today() - timedelta(days=365 * 6)).isoformat()
db_path = tmp / "nlrb.db"
db = sqlite3.connect(db_path)
db.executescript("""
create table participant (case_number text, participant text, type text, subtype text);
create table filing (case_number text, name text, case_type text, url text, status text, date_filed text,
                     date_closed text, reason_closed text);
""")
participants = [
    ("19-CA-000001", "Acme Corporation", "Charged Party / Respondent", "Employer"),
    ("19-CA-000001", "ACME CORP.", "Charged Party / Respondent", "Employer"),       # same case, two spellings: one charge
    ("19-CA-000002", "Acme Federal, LLC", "Charged Party / Respondent", "Employer"),
    ("19-CA-000003", "Acme Corporation", "Charged Party / Respondent", "Employer"),
    ("19-CA-000004", "Acme Holdings, Inc.", "Charged Party / Respondent", "Employer"),  # on the avoid list
    ("19-CA-000005", "Acme Corporation", "Charging Party", "Employer"),               # the employer filed it
    ("19-CB-000006", "Acme Corporation", "Charged Party / Respondent", "Employer"),   # not a CA case
    ("19-CA-000007", "Acme Corporation", "Charged Party / Respondent", "Employer"),   # too old
    ("19-CA-000008", "Acme Corp of Texas", "Charged Party / Respondent", "Employer"),  # a different name
]
filings = [
    ("19-CA-000001", "Acme", "CA", "https://www.nlrb.gov/case/19-CA-000001", "Closed", recent, recent, "Withdrawal Non-adjusted"),
    ("19-CA-000002", "Acme", "CA", None, "Open", recent, None, None),
    ("19-CA-000003", "Acme", "CA", "https://www.nlrb.gov/case/19-CA-000003", "Closed", recent, recent, "Settlement"),
    ("19-CA-000004", "Acme", "CA", None, "Open", recent, None, None),
    ("19-CA-000005", "Acme", "CA", None, "Open", recent, None, None),
    ("19-CB-000006", "Acme", "CB", None, "Open", recent, None, None),
    ("19-CA-000007", "Acme", "CA", None, "Closed", old, old, "Dismissal"),
    ("19-CA-000008", "Acme", "CA", None, "Open", recent, None, None),
]
db.executemany("insert into participant values (?,?,?,?)", participants)
db.executemany("insert into filing values (?,?,?,?,?,?,?,?)", filings)
db.commit()
db.close()
got = cc.labor(E, cc.labor_index(db_path))
check("labor: charges against the employer's own names, each case once", got["charges"], 3)
check("labor: open", got["open"], 1)
check("labor: how the closed ones closed", got["closed"], {"Withdrawal Non-adjusted": 1, "Settlement": 1})
check("labor: a case without a link gets the NLRB case page",
      [r["link"] for r in got["recent"] if r["case"] == "19-CA-000002"], ["https://www.nlrb.gov/case/19-CA-000002"])

# ── Courts ───────────────────────────────────────────────────────────────────

pages = {
    "first": {"results": [
        {"docket_id": 1, "party": ["Jane Roe", "Acme Corporation"], "caseName": "Roe v. Acme Corporation", "dateFiled": "2025-01-02",
         "dateTerminated": None, "docket_absolute_url": "/docket/1/roe-v-acme/", "court_citation_string": "D. Or."},
        {"docket_id": 2, "party": ["John Doe", "Acmeco Corporation"], "caseName": "Doe v. Acmeco", "dateFiled": "2025-02-02"},
        {"docket_id": 3, "party": ["Ann Poe", "ACME FEDERAL LLC"], "caseName": "Poe v. Acme Federal", "dateFiled": "2024-03-03",
         "dateTerminated": "2025-01-01", "docket_absolute_url": "/docket/3/poe/"},
    ], "next": None},
}
seen_q = []


def fake_cl(url, data=None, headers=None, timeout=60):
    seen_q.append(url)
    check("courts: sends the token", headers, {"Authorization": "Token t0k"})
    return pages["first"]


cc.fetch_json = fake_cl
budget = [10]
got = cc.courts(E, "t0k", budget)
check("courts: dockets with an exact party name only", got["cases"], 2)
check("courts: no closing date on record", got["unclosed"], 1)
check("courts: one search per legal name not avoided, by its core", (len(seen_q), budget[0]), (2, 8))
check("courts: the core of each name is the phrase searched",
      [urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["q"][0] for u in seen_q],
      ['party:("Acme") AND suitNature:(442 OR 445)', 'party:("Acme Federal") AND suitNature:(442 OR 445)'])
seen_q.clear()
cc.courts({**E, "legal": ["Acme Corporation", "Acme Federal LLC", "Acme Labs Inc", "Acme Space LLC", "Acme Air Co"]}, "t0k", [20])
check("courts: every legal name is searched, not only the first three", len(seen_q), 5)
check("courts: newest first, with the docket link", (got["recent"][0]["name"], got["recent"][0]["link"]),
      ("Roe v. Acme Corporation", "https://www.courtlistener.com/docket/1/roe-v-acme/"))
check("courts: no token, no search", cc.courts(E, "", [10]), None)
try:
    cc.courts(E, "t0k", [1])
    check("courts: stops when the day's searches run out", "no error", "budget")
except RuntimeError as err:
    check("courts: stops when the day's searches run out", str(err), "budget")
cc.fetch_json = lambda url, data=None, headers=None, timeout=60: {"results": [], "next": url + "&page=2"}
check("courts: says so when there are more pages than it reads", cc.courts({**E, "legal": ["Acme Corporation"]}, "t0k", [10])["more"], True)

# ── PAC giving ───────────────────────────────────────────────────────────────


def fake_fec(kind, cycle, refresh):
    if kind == "cn":
        return iter([["H0OR01001", "SMITH, A", "DEM"], ["S0OR02002", "JONES, B", "REP"], ["H0OR03003", "LEE, C", "LIB"],
                     ["S6MN00267", "KLOBUCHAR, A", "DFL"]])
    if kind == "cm":
        row = lambda cid, name, org_tp, tp, org, cand="": [cid, name, "", "", "", "", "", "", "U", tp, "", "Q", org_tp, org, cand]
        return iter([row("C001", "ACME CORP PAC", "C", "Q", "ACME CORPORATION"),
                     row("C002", "ACME FEDERAL PAC", "C", "N", "Acme Federal, LLC"),
                     row("C003", "ACME WORKERS UNION PAC", "L", "Q", "ACME CORPORATION"),   # a union's, not the company's
                     row("C004", "ACMECO PAC", "C", "Q", "ACMECO CORPORATION"),
                     row("C005", "ACME FEDERAL MUTUAL PAC", "W", "Q", "Acme Federal LLC"),  # corporation without capital stock
                     row("C900", "KLOBUCHAR FOR MINNESOTA", "", "S", "", "S6MN00267")])
    rows = []
    for cmte, tp, amt, other, cand, memo in [
            ("C001", "24K", "1000", "", "H0OR01001", ""), ("C001", "24K", "2500", "", "S0OR02002", ""),
            ("C001", "24E", "9999", "", "H0OR01001", ""),     # an independent expenditure, not a gift
            ("C001", "24K", "5000", "", "H0OR01001", "X"),    # a memo line: already counted elsewhere
            ("C001", "24K", "300", "", "S6MN00267", ""),      # Minnesota's Democrats file as DFL
            ("C001", "24K", "200", "C900", "", ""),           # no CAND_ID: found through the receiving committee
            ("C002", "24K", "500", "", "H0OR03003", ""), ("C005", "24K", "400", "", "S0OR02002", ""),
            ("C004", "24K", "7777", "", "H0OR01001", "")]:
        r = [""] * 22
        r[0], r[5], r[14], r[15], r[16], r[19] = cmte, tp, amt, other, cand, memo
        rows.append(r)
    return iter(rows) if cycle == cc.cycles()[0] else iter([])


fec_dir = tmp / "fec"
fec_dir.mkdir()


def fake_fec_file(kind, cycle, refresh):
    f = fec_dir / f"{kind}{cycle}.zip"
    f.touch()
    return f


cc.fec_rows, cc.fec_file = fake_fec, fake_fec_file
idx = cc.pac_index(False)
check("pac_index: corporate PACs by connected organization", sorted(idx["by_org"]), ["acme", "acme federal", "acmeco"])
got = cc.pac(E, idx)
check("pac: the employer's committees, a W one too", sorted(c["id"] for c in got["committees"]), ["C001", "C002", "C005"])
check("pac: this cycle's gifts by party, DFL as Democratic, memo lines left out",
      {k: got["cycles"][0][k] for k in ("dem", "rep", "other", "gifts")}, {"dem": 1500, "rep": 2900, "other": 500, "gifts": 6})
check("pac: a cycle with no gifts", got["cycles"][1]["gifts"], 0)
check("pac_index: when the files date from", idx["as_of"], date.today().isoformat())


def fec_404(newest):
    def rows(kind, cycle, refresh):
        if cycle == cc.cycles()[0 if newest else 1]:
            raise RuntimeError("HTTPError: HTTP Error 404: Not Found")
        return fake_fec(kind, cc.cycles()[0], refresh)
    return rows


cc.fec_rows = fec_404(True)
idx = cc.pac_index(False)
got = cc.pac(E, idx)
check("pac_index: a new cycle not published yet is left out, not fatal", idx["cycles"], [cc.cycles()[1]])
check("pac: and says so", (got["cycles"][0].get("unpublished"), got["cycles"][1]["gifts"]), (True, 6))
cc.fec_rows = fec_404(False)
try:
    cc.pac_index(False)
    check("pac_index: an older cycle missing is an error", "no error", "error")
except RuntimeError:
    check("pac_index: an older cycle missing is an error", "error", "error")
cc.fec_rows = fake_fec

# ── Downloads ────────────────────────────────────────────────────────────────


class Short:
    """A response that ends before its Content-Length."""
    headers = {"Content-Length": "100"}

    def __init__(self):
        self.left = [b"x" * 50]

    def read(self, n=-1):
        return self.left.pop() if self.left else b""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


real_urlopen = cc.urllib.request.urlopen
cc.urllib.request.urlopen = lambda req, timeout=60: Short()
target = tmp / "cut.json"
try:
    cc.fetch("https://example.test/f", to_file=target, tries=1)
    check("fetch: a cut-off download is refused", "kept", "refused")
except RuntimeError as err:
    check("fetch: a cut-off download is refused", ("incomplete" in str(err), target.exists(), target.with_suffix(".json.part").exists()),
          (True, False, False))
cc.urllib.request.urlopen = real_urlopen

# ── Violation Tracker ────────────────────────────────────────────────────────

parents = {"acme": "acme-corporation", "acmeco": "acmeco"}
check("violations: the table's slug wins", cc.violations({**E, "vt": "acme-inc"}, parents)["link"],
      "https://violationtracker.goodjobsfirst.org/parent/acme-inc")
check("violations: a parent matched on a reviewed name", cc.violations(E, parents),
      {"link": "https://violationtracker.goodjobsfirst.org/parent/acme-corporation", "kind": "parent"})
check("violations: never through a subsidiary's legal name",
      cc.violations({**E, "names": ["Synopsys"], "legal": ["Synopsys, Inc.", "ANSYS, Inc."]}, {"ansys": "ansys"})["kind"], "search")
check("violations: otherwise a name search", cc.violations({**E, "names": ["Zed"], "legal": ["Zed Holdings, Inc."]}, parents),
      {"link": "https://violationtracker.goodjobsfirst.org/?company_op=starts&company=Zed+Holdings+Inc", "kind": "search"})
# Vultr's first legal name is also an unrelated company's and sits on its own avoid
# list: neither the search nor a parent match may use it (2026-11-12).
VULTR = {"id": "vultr", "names": ["Vultr"], "legal": ["The Constant Company, LLC", "Choopa, LLC"],
         "avoid": ["The Constant Company, LLC of Lawrenceville GA", "Constant Contact, Inc.", "Vultron LLC",
                   "The Constant Company, LLC"], "ueis": [], "vt": None}
check("violations: the search uses the first legal name not avoided", cc.violations(VULTR, {}),
      {"link": "https://violationtracker.goodjobsfirst.org/?company_op=starts&company=Choopa+LLC", "kind": "search"})
check("violations: no parent matched through an avoided legal name", cc.violations(VULTR, {"constant": "constant-co"})["kind"],
      "search")
check("violations: every legal name avoided searches the board name",
      cc.violations({**VULTR, "avoid": VULTR["legal"]}, {})["link"], "https://violationtracker.goodjobsfirst.org/?company_op=starts&company=Vultr")
check("search_name: the first legal name not avoided", cc.search_name(VULTR), "Choopa")
check("search_name: every legal name avoided, the board name", cc.search_name({**VULTR, "avoid": VULTR["legal"]}), "Vultr")
check("search_name: no legal names, the board name", cc.search_name({"names": ["Tailscale"]}), "Tailscale")

# ── Open roles on its own board: discovery ───────────────────────────────────

for name in ("GH_URL", "LEVER_URL", "ASHBY_URL"):
    check(f"{name} is splat.py's pattern", (getattr(cc, name).pattern, getattr(cc, name).flags),
          (getattr(splat, name).pattern, getattr(splat, name).flags))
U1, U2 = "9120a88b-06ce-403a-b7c9-0fba02756386", "215bd046-ace9-406b-baf7-5eb6c035bd8c"
BOARD_URLS = [
    (f"https://jobs.ashbyhq.com/Vultr/{U1}", "ashby:vultr", U1),
    (f"https://jobs.ashbyhq.com/vultr/{U2.upper()}?src=x", "ashby:vultr", U2),       # Vultr and vultr: one board
    (f"https://jobs.ashbyhq.com/PAR%20Technology/{U1}", "ashby:par technology", U1),
    ("https://boards.greenhouse.io/okta/jobs/8093704", "greenhouse:okta", "8093704"),
    ("https://job-boards.greenhouse.io/Okta/jobs/8093704?gh_src=li", "greenhouse:okta", "8093704"),
    ("https://job-boards.eu.greenhouse.io/veeamsoftware/jobs/5001960101", "greenhouse:veeamsoftware", "5001960101"),
    (f"https://jobs.lever.co/palantir/{U1}", "lever:palantir", U1),
    (f"https://jobs.eu.lever.co/siteimprove/{U2}", "lever-eu:siteimprove", U2),
    ("https://acme.wd5.myworkdayjobs.com/External/job/Portland/SRE_R1", None, None),
    ("https://www.linkedin.com/jobs/view/4012345678/", None, None),
    ("https://www.governmentjobs.com/careers/portlandor/jobs/1", None, None),
    ("https://careers.example.com/jobs/42", None, None),
    (f"https://evil.example/jobs.ashbyhq.com/vultr/{U1}", None, None),                  # the host decides, not the path
    ("https://www.okta.com/company/careers/opportunity/8093704?gh_jid=8093704", None, None),  # never guessed
]
for url, key, pid in BOARD_URLS:
    b = cc.board_of(url)
    check(f"board_of({url!r})", (b["key"], b["id"]) if b else None, (key, pid) if key else None)
    check(f"board_of and splat.board_key agree on {url!r}", splat.board_key(url), (b["key"], b["id"]) if b else None)
check("board_of: Ashby's board link and API", {k: cc.board_of(BOARD_URLS[2][0])[k] for k in ("link", "api")},
      {"link": "https://jobs.ashbyhq.com/par%20technology", "api": "https://api.ashbyhq.com/posting-api/job-board/par%20technology"})
check("board_of: Greenhouse's board link and API, EU too", {k: cc.board_of(BOARD_URLS[5][0])[k] for k in ("link", "api")},
      {"link": "https://job-boards.greenhouse.io/veeamsoftware", "api": "https://boards-api.greenhouse.io/v1/boards/veeamsoftware/jobs"})
check("board_of: Lever EU's board link and API", {k: cc.board_of(BOARD_URLS[7][0])[k] for k in ("link", "api", "ats")},
      {"link": "https://jobs.eu.lever.co/siteimprove", "api": "https://api.eu.lever.co/v0/postings/siteimprove?mode=json",
       "ats": "lever-eu"})
PAGE_ROWS = [
    {"company": "Vultr", "url": f"https://jobs.ashbyhq.com/Vultr/{U1}"},
    {"company": "Vultr", "url": f"https://jobs.ashbyhq.com/vultr/{U2}"},
    {"company": "Acme", "url": "https://boards.greenhouse.io/acme/jobs/101"},
    {"company": "ACME", "url": f"https://jobs.lever.co/acme/{U1}"},             # the same employer by its page key
    {"company": "Zed & Co", "url": "https://acme.wd5.myworkdayjobs.com/External/job/Portland/SRE_R1"},
    {"company": "Nobody", "url": f"https://jobs.lever.co/nobody/{U2}"},          # no employer record: a board, no facts
]
by_emp, found_boards, card_n = cc.page_boards(PAGE_ROWS, {"vultr": "vultr", "acme": "acme", "zed and co": "zed"})
check("page_boards: each employer's boards through the aliases; a Workday link adds none",
      by_emp, {"vultr": {"ashby:vultr"}, "acme": {"greenhouse:acme", "lever:acme"}})
check("page_boards: every board found, with its cards", (sorted(found_boards), card_n["ashby:vultr"]),
      (["ashby:vultr", "greenhouse:acme", "lever:acme", "lever:nobody"], 2))

# ── Open roles on its own board: reading a board ─────────────────────────────

LEVER_MS = int(datetime(2026, 9, 15, 12, tzinfo=timezone.utc).timestamp() * 1000)
BOARD_JSON = {
    "https://api.ashbyhq.com/posting-api/job-board/vultr": {"apiVersion": "1", "jobs": [
        {"id": U1.upper(), "title": "RMA Systems Engineer", "location": "Remote - US", "isListed": True,
         "publishedAt": "2026-04-30T13:16:29.503+00:00", "descriptionHtml": "<p>...</p>"},
        {"id": U2, "title": "Technical Support Specialist", "location": "Remote - US", "isListed": True},
        {"id": "33333333-2222-3333-4444-555555555555", "title": "Hidden", "location": "Remote", "isListed": False,
         "publishedAt": "2026-01-01T00:00:00+00:00"}]},
    "https://boards-api.greenhouse.io/v1/boards/acme/jobs": {"meta": {"total": 3}, "jobs": [
        {"id": 101, "internal_job_id": 9001, "title": "SRE", "location": {"name": "Portland, OR"},
         "first_published": "2026-10-01T07:27:07-04:00", "updated_at": "2026-11-11T12:43:00-04:00"},
        {"id": 102, "internal_job_id": 9002, "title": "SOC Analyst", "location": {"name": "Remote"},
         "updated_at": "2026-11-11T12:43:00-04:00"},                                   # no first_published: undated
        {"id": 103, "internal_job_id": None, "title": "Join our talent community", "location": {"name": "Anywhere"},
         "first_published": "2025-01-01T00:00:00-04:00"}]},                            # a prospect post
    "https://api.lever.co/v0/postings/acme?mode=json": [
        {"id": U1, "text": "Cloud Engineer", "categories": {"location": "Seattle, WA"}, "createdAt": LEVER_MS}],
    "https://api.eu.lever.co/v0/postings/siteimprove?mode=json": {"ok": False, "error": "Document not found"},
}
board_calls = []


def fake_boards(url, data=None, headers=None, timeout=60):
    board_calls.append(url)
    if url.endswith("/api/v2/recipient/") or "spending_by_category" in url:
        return fake_auto(url, data, headers, timeout)
    if url not in BOARD_JSON:
        raise RuntimeError("HTTPError: HTTP Error 404: Not Found")
    return json.loads(json.dumps(BOARD_JSON[url]))


cc.fetch_json = fake_boards
got = cc.read_board(cc.board_of(f"https://jobs.ashbyhq.com/Vultr/{U1}"))
check("read_board ashby: listed jobs only, ids without case, publishedAt",
      got, {"prospect": 0, "postings": {U1: {"t": "RMA Systems Engineer", "l": "Remote - US", "d": "2026-04-30"},
                                        U2: {"t": "Technical Support Specialist", "l": "Remote - US", "d": None}}})
got = cc.read_board(cc.board_of("https://boards.greenhouse.io/acme/jobs/101"))
check("read_board greenhouse: first_published, never updated_at; a prospect post counted apart",
      got, {"prospect": 1, "postings": {"101": {"t": "SRE", "l": "Portland, OR", "d": "2026-10-01"},
                                        "102": {"t": "SOC Analyst", "l": "Remote", "d": None}}})
got = cc.read_board(cc.board_of(f"https://jobs.lever.co/acme/{U1}"))
check("read_board lever: createdAt in milliseconds, categories.location",
      got, {"prospect": 0, "postings": {U1: {"t": "Cloud Engineer", "l": "Seattle, WA", "d": "2026-09-15"}}})
for label, url in (("an answer of the wrong shape", f"https://jobs.eu.lever.co/siteimprove/{U2}"),
                   ("a board that is not there", "https://boards.greenhouse.io/nosuchboard/jobs/1")):
    try:
        cc.read_board(cc.board_of(url))
        check(f"read_board: {label} raises", "no error", "error")
    except (RuntimeError, ValueError):
        check(f"read_board: {label} raises", "error", "error")
check("read_board: through fetch_json, one call a board", board_calls,
      ["https://api.ashbyhq.com/posting-api/job-board/vultr", "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
       "https://api.lever.co/v0/postings/acme?mode=json", "https://api.eu.lever.co/v0/postings/siteimprove?mode=json",
       "https://boards-api.greenhouse.io/v1/boards/nosuchboard/jobs"])

# ── Open roles on its own board: the ledger across days ──────────────────────

D0 = date(2026, 1, 1)
day = lambda n: D0 + timedelta(days=n)   # noqa: E731
ds = lambda n: day(n).isoformat()        # noqa: E731
VB = cc.board_of(f"https://jobs.ashbyhq.com/vultr/{U1}")


def census(ledger, n, *rows, prospect=0):
    cc.ledger_update(ledger, VB, {"postings": {pid: {"t": t, "l": l, "d": d} for pid, t, l, d in rows},
                                  "prospect": prospect}, day(n))


def watch(ledger, days, listings, prospect=0):
    """The census on each of days; listings are (id, title, place, board date,
    first day up, last day up), and each day's read shows the ones up that day."""
    for n in days:
        census(ledger, n, *[(pid, t, l, d) for pid, t, l, d, a, b in listings if a <= n <= b], prospect=prospect)


def relist_on(listings, today, days=None):
    """relisted() on day today, the board read every day from 0 to today unless days says otherwise."""
    led = {"version": 1, "boards": {}}
    watch(led, range(today + 1) if days is None else days, listings)
    return cc.relisted(led["boards"]["ashby:vultr"], day(today))


L = {"version": 1, "boards": {}}
# Read every day from day 0. a (SRE, Portland) is up to day 29; d, a new listing
# with its title and place, from day 40. e opens on day 40 beside c, the same
# role while c is still up (two openings). b leaves on day 40 and comes back
# under its own id on day 60; f opens with b's title somewhere else.
LISTINGS = [("a", "SRE", "Portland, OR", "2025-12-01", 0, 29), ("b", "SOC Analyst", "Remote", None, 0, 39),
            ("c", "Help Desk", "Boise, ID", "2025-12-20", 0, 101), ("d", "SRE", "Portland, OR", "2026-02-10", 40, 100),
            ("e", "Help Desk", "Boise, ID", "2026-02-10", 40, 100), ("f", "SOC Analyst", "Seattle, WA", "2026-03-02", 60, 100)]
watch(L, [0], LISTINGS)
rec = L["boards"]["ashby:vultr"]
check("ledger: the first run sets f and s to today",
      (rec["first_ok"], rec["last_ok"], rec["link"], rec["reads"], rec["postings"]["a"]),
      ("2026-01-01", "2026-01-01", "https://jobs.ashbyhq.com/vultr", [["2026-01-01", "2026-01-01"]],
       {"t": "SRE", "l": "Portland, OR", "d": "2025-12-01", "f": "2026-01-01", "s": "2026-01-01"}))
watch(L, range(1, 41), LISTINGS)
check("ledger: an absent listing keeps its last day seen", (rec["postings"]["a"]["s"], rec["postings"]["d"]["f"]),
      (ds(29), ds(40)))
check("ledger: the days read, as one run", rec["reads"], [[ds(0), ds(40)]])
watch(L, range(41, 61), LISTINGS + [("b", "SOC Analyst", "Remote", None, 60, 101)])
check("ledger: the same id back keeps its first day seen", (rec["postings"]["b"]["f"], rec["postings"]["b"]["s"]),
      (ds(0), ds(60)))
check("relisted: none before 90 days of watching", cc.hiring_facts(rec, day(60))["relisted"], None)
# A listing present again with a new title and date (an edit, a republish): t, l
# and d follow the board, f stays.
census(L, 61, *[(pid, t, l, d) for pid, t, l, d, a, b in LISTINGS if a <= 61 <= b],
       ("b", "SOC Analyst II", "Remote - US", "2026-03-02"))
check("ledger: a listing seen again takes the board's title, place and date, keeps its first day",
      rec["postings"]["b"], {"t": "SOC Analyst II", "l": "Remote - US", "d": "2026-03-02", "f": ds(0), "s": ds(61)})
LISTINGS_B = LISTINGS + [("b", "SOC Analyst II", "Remote - US", "2026-03-02", 61, 101)]
watch(L, range(62, 101), LISTINGS_B, prospect=2)
facts = cc.hiring_facts(rec, day(100))
check("relisted: only the new listing after the old one's last day, not two up at once or an id come back",
      facts["relisted"], {"roles": 1, "window_days": 90,
                          "examples": [{"title": "SRE", "place": "Portland, OR", "link": "https://jobs.ashbyhq.com/vultr/d"}]})
check("hiring_facts: the board's numbers today", {k: facts[k] for k in ("state", "ats", "open", "dated", "watching_since", "prospect")},
      {"state": "ok", "ats": "ashby", "open": 5, "dated": 5, "watching_since": "2026-01-01", "prospect": 2})
census(L, 101, ("b", "SOC Analyst II", "Remote - US", "2026-03-02"), ("c", "Help Desk", "Boise, ID", "2025-12-20"))
check("relisted: an example no longer listed links to the board",
      cc.hiring_facts(rec, day(101))["relisted"]["examples"],
      [{"title": "SRE", "place": "Portland, OR", "link": "https://jobs.ashbyhq.com/vultr"}])

HD = ("Help Desk", "Boise, ID")
# The handover day: x and y are both up on day 95 (two openings at once), x gone
# on day 96. A day later, y first seen the day after x's last: taken down and
# listed again overnight.
check("relisted: the old listing's last day is the new one's first: two up at once, none",
      relist_on([("x", *HD, None, 0, 95), ("y", *HD, None, 95, 100)], 100)["roles"], 0)
check("relisted: the new listing first seen the day after the old one's last: one",
      relist_on([("x", *HD, None, 0, 95), ("y", *HD, None, 96, 100)], 100)["roles"], 1)
# One role taken down and listed again twice in the window is one role.
check("relisted: one role listed again twice counts once, the newest as the example",
      relist_on([("a", *HD, None, 0, 39), ("d", *HD, None, 40, 60), ("g", *HD, None, 61, 120)], 120),
      {"roles": 1, "window_days": 90, "examples": [{"title": "Help Desk", "place": "Boise, ID",
                                                   "link": "https://jobs.ashbyhq.com/vultr/g"}]})
check("relisted: titles and places match without case or punctuation",
      relist_on([("a", "Sr. SRE", "Portland, OR", None, 0, 49), ("b", "Sr SRE", "portland or", None, 50, 120)], 120)["roles"], 1)
check("relisted: a different place is another role",
      relist_on([("a", "SRE", "Portland, OR", None, 0, 49), ("b", "SRE", "Seattle, WA", None, 50, 120)], 120)["roles"], 0)
four = relist_on([x for i, t in enumerate(("NOC", "SOC", "SRE", "IT")) for x in
                  ((f"o{i}", t, "Remote", None, 0, 40 + i), (f"n{i}", t, "Remote", None, 41 + i, 120))], 120)
check("relisted: four roles, three examples, newest first",
      (four["roles"], [x["title"] for x in four["examples"]]), (4, ["IT", "SRE", "SOC"]))
# The window is the last 90 days (day 30 to day 120): the census's last read
# before the new listing came up must fall in it.
check("relisted: the last read before the new listing on the window's first day counts",
      relist_on([("q", *HD, None, 0, 30), ("p", *HD, None, 31, 120)], 120)["roles"], 1)
check("relisted: the day before the window does not",
      relist_on([("q", *HD, None, 0, 29), ("p", *HD, None, 30, 120)], 120)["roles"], 0)
check("relisted: exactly 90 days of watching shows the numbers", relist_on([("q", *HD, None, 0, 90)], 90),
      {"roles": 0, "window_days": 90, "examples": []})
check("relisted: 89 days does not", relist_on([("q", *HD, None, 0, 89)], 89), None)
# A new listing whose own board date is before the window, or before the old
# one's last day, was up (perhaps unlisted) before the census saw it.
check("relisted: the new listing's own date before the window does not count",
      relist_on([("q", *HD, None, 0, 5), ("p", *HD, ds(8), 50, 120)], 120)["roles"], 0)
check("relisted: nor its own date before the old listing's last day",
      relist_on([("q", *HD, None, 0, 49), ("p", *HD, ds(45), 50, 120)], 120)["roles"], 0)
check("relisted: its own date on the old listing's last day counts (taken down and listed that day)",
      relist_on([("q", *HD, None, 0, 49), ("p", *HD, ds(49), 50, 120)], 120)["roles"], 1)
# Gaps in the reads (2026-11-12 review): the census reads only the boards today's
# cards link to, so a board can go weeks unread. Read days 0-54 and 71 on: q is
# up to day 64 and p from day 58, both up together while nobody read the board.
GAP = [n for n in range(162) if not 55 <= n <= 70]
check("relisted: a long gap starts the watch again", relist_on([("q", *HD, None, 0, 64), ("p", *HD, None, 58, 161)], 100, GAP), None)
check("relisted: two listings up together in a gap are not counted after it",
      relist_on([("q", *HD, None, 0, 64), ("p", *HD, None, 58, 161)], 161, GAP)["roles"], 0)
gap_led = {"version": 1, "boards": {}}
watch(gap_led, GAP[:120], [("q", *HD, None, 0, 64)])
check("hiring_facts: watching since the read after the gap", cc.hiring_facts(gap_led["boards"]["ashby:vultr"], day(GAP[119]))["watching_since"],
      ds(71))
# A short gap (days 55 and 56 unread) stays one watch: a handover inside it is not
# known, one gone before it is.
SHORT = [n for n in range(121) if n not in (55, 56)]
check("relisted: a short gap keeps the watch; a handover inside it is not counted",
      relist_on([("q", *HD, None, 0, 55), ("p", *HD, None, 56, 120)], 120, SHORT)["roles"], 0)
check("relisted: one gone before the short gap is",
      relist_on([("q", *HD, None, 0, 40), ("p", *HD, None, 56, 120)], 120, SHORT)["roles"], 1)
# Read days 0-30, then nothing until day 200 (the employer's cards left the page):
# a listing first seen on day 200, dated day 40 by its board, is not "listed
# again in the last 90 days".
check("relisted: a board unread for months starts over",
      relist_on([("a", *HD, None, 0, 30), ("b", *HD, ds(40), 40, 300)], 289, [*range(31), *range(200, 290)]), None)
check("relisted: and after 90 more days, the change before the gap is not in the window",
      relist_on([("a", *HD, None, 0, 30), ("b", *HD, ds(40), 40, 300)], 290, [*range(31), *range(200, 291)])["roles"], 0)
check("read_runs: an entry without read days knows only its last day", cc.read_runs({"first_ok": ds(0), "last_ok": ds(9)}),
      [[ds(9), ds(9)]])
check("read_runs: read days that are not dates are dropped the same way",
      cc.read_runs({"first_ok": ds(0), "last_ok": ds(9), "reads": [["2026-02-30", ds(9)]]}), [[ds(9), ds(9)]])
# Pruning: listings not seen for 180 days, boards not read for 180 days, and read days as old.
L["boards"]["lever:gone"] = {"ats": "lever", "token": "gone", "link": "https://jobs.lever.co/gone", "first_ok": "2026-01-01",
                             "last_ok": "2026-01-01", "postings": {}}
rec["reads"] = [[ds(0), ds(30)], [ds(40), ds(101)]]
census(L, 250, ("d", "SRE", "Portland, OR", "2026-02-10"))
check("ledger_prune: what is gone 180 days leaves",
      (cc.ledger_prune(L, day(250)), sorted(rec["postings"]), sorted(L["boards"]), rec["reads"]),
      (2, ["b", "c", "d", "e", "f"], ["ashby:vultr"], [[ds(40), ds(101)], [ds(250), ds(250)]]))
bad_file, no_file, odd_file = tmp / "boards-bad.json", tmp / "boards-none.json", tmp / "boards-odd.json"
bad_file.write_text('{"version": 1, "boards": ', encoding="utf-8")
check("load_ledger: a corrupt file starts empty", cc.load_ledger(bad_file), {"version": 1, "boards": {}})
check("load_ledger: no file starts empty", cc.load_ledger(no_file), {"version": 1, "boards": {}})
odd_file.write_text(json.dumps({"version": 1, "boards": {
    "ashby:ok": {"first_ok": ds(0), "last_ok": ds(1), "postings": {}},
    "ashby:nofirst": {"last_ok": ds(1), "postings": {}},
    "ashby:badlast": {"first_ok": ds(0), "last_ok": "2026-02-30", "postings": {}},
    "ashby:nopostings": {"first_ok": ds(0), "last_ok": ds(1)},
    "ashby:list": ["not", "a", "board"]}}), encoding="utf-8")
check("load_ledger: a board without its days or listings is dropped", sorted(cc.load_ledger(odd_file)["boards"]), ["ashby:ok"])

# Age bands and the median, by the board's own dates, over what is listed today.
T = date(2026, 11, 12)
AGES = {"p0": 0, "p29": 29, "p30": 30, "p89": 89, "p90": 90, "p200": 200}
BANDS = {"ats": "greenhouse", "link": "https://job-boards.greenhouse.io/acme", "first_ok": "2026-10-01", "last_ok": "2026-11-12",
         "prospect": 0, "postings": {**{pid: {"t": pid, "l": "", "d": (T - timedelta(days=a)).isoformat(), "f": "2026-10-01",
                                              "s": "2026-11-12"} for pid, a in AGES.items()},
                                     "undated": {"t": "u", "l": "", "d": None, "f": "2026-10-01", "s": "2026-11-12"},
                                     "gone": {"t": "g", "l": "", "d": "2020-01-01", "f": "2026-10-01", "s": "2026-11-01"}}}
facts = cc.hiring_facts(BANDS, T)
check("hiring_facts: age bands, the oldest and the median on fixed dates",
      {k: facts[k] for k in ("open", "dated", "under_30", "d30_89", "d90_plus", "oldest", "median_days", "relisted")},
      {"open": 7, "dated": 6, "under_30": 2, "d30_89": 2, "d90_plus": 2, "oldest": "2026-04-26", "median_days": 30,
       "relisted": None})

# ── A whole run ──────────────────────────────────────────────────────────────

state = tmp / "state"
cc.JOBS = tmp / "no-page.json"   # no page: only the table's employers, until the auto-match tests below
cc.TABLE = tmp / "company_names.json"
ZED = {"id": "zed", "names": ["Zed & Co"], "legal": ["Zed Holdings, Inc."],
       "ueis": [{"uei": "OOOOOOOOOOOO", "name": "ZED UNIT", "level": "C"}]}
cc.TABLE.write_text(json.dumps({"employers": [E, ZED]}), encoding="utf-8")
cc.STATE_DIR, cc.DATA, cc.CACHE, cc.OUT = state, state / "company-data", state / "companies.json", tmp / "out" / "companies.json"
cc.BOARDS = state / "boards.json"
cc.OUT.parent.mkdir()
cc.DATA.mkdir(parents=True)
(cc.DATA / "nlrb.db").write_bytes(db_path.read_bytes())
cc.nlrb_db = lambda refresh: cc.DATA / "nlrb.db"
cc.vt_parents = lambda refresh: parents
cc.fetch_json = fake_usa
cc.usaspending_as_of = lambda: "2026-11-08T00:00:00"
sys.argv = ["company_check.py", "--only", "contracts,labor,pac,violations"]
check("run: exits 0", cc.main(), 0)
out = json.loads(cc.OUT.read_text(encoding="utf-8"))
check("run: the page looks a card's company up by its key", out["aliases"], {"acme": "acme", "zed and co": "zed"})
check("run: every source it ran is in the record", sorted(k for k in out["companies"]["acme"] if k in cc.SOURCES),
      ["contracts", "labor", "pac", "violations"])
check("run: a UEI the table gives another employer counts only there",
      (out["companies"]["acme"]["contracts"]["fy"][0]["total"], out["companies"]["zed"]["contracts"]["fy"][0]["total"]), (132, 10))
check("run: as_of for the sources that ran", sorted(out["as_of"]), ["contracts", "labor", "pac"])

# A source that fails the next day keeps its facts; a fresh one is not asked again.
cc.pac_index = lambda refresh: (_ for _ in ()).throw(RuntimeError("FEC down"))
sys.argv = ["company_check.py", "--refresh", "--only", "pac"]
cc.main()
out2 = json.loads(cc.OUT.read_text(encoding="utf-8"))
check("run: a failed source keeps yesterday's facts", out2["companies"]["acme"]["pac"], out["companies"]["acme"]["pac"])
check("run: and says it failed", (out2["stats"]["pac"]["failed"], out2["stats"]["pac"]["error"]), (1, "FEC down"))
asked = []
cc.fetch_json = lambda *a, **k: asked.append(a) or fake_usa(*a, **k)
sys.argv = ["company_check.py", "--only", "contracts"]
cc.main()
check("run: facts checked today are not fetched again", asked, [])
table_now = json.loads(cc.TABLE.read_text(encoding="utf-8"))
table_now["employers"][0]["exclude_ueis"] = ["JVJVJVJVJVJV", "CCCCCCCCCCCC"]
cc.TABLE.write_text(json.dumps(table_now), encoding="utf-8")
cc.main()
check("run: an edited table entry is checked again the next run",
      sorted({tuple(d["filters"]["recipient_search_text"]) for _, d in asked if d and "filters" in d}),
      [("AAAAAAAAAAAA", "BBBBBBBBBBBB")])
asked.clear()
table_now["employers"][1]["ueis"].append({"uei": "PPPPPPPPPPPP", "name": "ZED TWO", "level": "C"})
cc.TABLE.write_text(json.dumps(table_now), encoding="utf-8")
cc.main()
check("run: another employer's new UEI re-checks every employer's contracts",
      sorted({tuple(d["filters"]["recipient_search_text"]) for _, d in asked if d and "filters" in d}),
      [("AAAAAAAAAAAA", "BBBBBBBBBBBB"), ("OOOOOOOOOOOO", "PPPPPPPPPPPP")])

# BREAKER failures in a row stop a source for the day; the rest keep their facts.
FIVE = [{"id": f"e{i}", "names": [f"E{i}"], "legal": [f"E{i} Inc"], "ueis": [{"uei": f"{i}" * 12, "name": "X", "level": "P"}]}
        for i in range(5)]
cc.TABLE.write_text(json.dumps({"employers": FIVE}), encoding="utf-8")
cc.fetch_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("TimeoutError: timed out"))
sys.argv = ["company_check.py", "--refresh", "--only", "contracts"]
cc.main()
st = json.loads(cc.OUT.read_text(encoding="utf-8"))["stats"]["contracts"]
check("run: a source that keeps failing stops for the day", (st["failed"], st["kept"], st["error"][:22]), (3, 2, "stopped after 3 failur"))
cc.START = time.monotonic() - 3600
cc.main()
st = json.loads(cc.OUT.read_text(encoding="utf-8"))["stats"]["contracts"]
check("run: past its time, a stage stops and the run still writes", (st["failed"], st["kept"], st["error"][:11]), (0, 5, "out of time"))
cc.START = time.monotonic()

# CourtListener's limit is a day's, so a second run that day spends only what is left.
cc.COURT_BUDGET = 3
cc.os.environ["COURTLISTENER_TOKEN"] = "t0k"
TWO = [{"id": "a", "names": ["A"], "legal": ["Acme Corporation", "Acme Federal LLC"]},
       {"id": "b", "names": ["B"], "legal": ["Bolt Corporation", "Bolt Federal LLC"]}]
cc.TABLE.write_text(json.dumps({"employers": TWO}), encoding="utf-8")
cl_calls = []
cc.fetch_json = lambda url, data=None, headers=None, timeout=60: cl_calls.append(url) or {"results": [], "next": None}
sys.argv = ["company_check.py", "--only", "courts"]
cc.main()
st = json.loads(cc.OUT.read_text(encoding="utf-8"))["stats"]["courts"]
check("run: courts stops at the day's budget", (len(cl_calls), st["updated"], st["kept"]), (3, 1, 1))
cc.main()
check("run: a second run that day searches no more", len(cl_calls), 3)
cc.COURT_BUDGET, cc.COURT_RUN = 100, 2
cc.CACHE.unlink()
cl_calls.clear()
sys.argv = ["company_check.py", "--only", "courts"]
cc.main()
check("run: one run spends at most COURT_RUN searches (the hourly limit)", len(cl_calls), 2)
cc.COURT_RUN = 45
# Oldest first: an employer never checked goes before one checked last week,
# whatever their order in the table.
cache_now = json.loads(cc.CACHE.read_text(encoding="utf-8"))
week_ago = (date.today() - timedelta(days=8)).isoformat()
cache_now["a"] = {"courts": {"at": week_ago, "key": "", "facts": None}}
cache_now.pop("b", None)
cache_now.pop("_court_used", None)
cc.CACHE.write_text(json.dumps(cache_now), encoding="utf-8")
cc.COURT_RUN = 2
cl_calls.clear()
cc.main()
check("run: the employer never checked goes first", sorted({"Bolt" in u for u in cl_calls}), [True])
cc.COURT_RUN = 45
sys.argv = ["company_check.py", "--dry-run"]
cc.CACHE.unlink()
cl_calls.clear()
cc.main()
check("run: a dry run leaves courts alone unless asked", len([u for u in cl_calls if "courtlistener" in u]), 0)
cc.COURT_BUDGET = 100

# ── Employers the table does not cover: matched automatically ────────────────

check("auto_name drops a note in parentheses", cc.auto_name("Syms Strategic Group, LLC (SSG)"), "Syms Strategic Group, LLC")
# An ambiguous automatic employer avoids its one legal name; its searches use the
# card's name without the note, as before the avoid rule (review, 2026-11-12).
SSG = cc.auto_entry("Syms Strategic Group, LLC (SSG)", {"ambiguous": True})
check("search_name: an ambiguous automatic employer, the card name without its note", cc.search_name(SSG), "Syms Strategic Group")
check("violations: its Violation Tracker search the same", cc.violations(SSG, {"syms strategic group": "ssg"}),
      {"link": "https://violationtracker.goodjobsfirst.org/?company_op=starts&company=Syms+Strategic+Group+LLC", "kind": "search"})
RECIPIENTS = {
    "AttainX": [{"uei": "ATTAINX00001", "name": "ATTAINX, INC.", "recipient_level": "P", "id": "ax-P"},
                {"uei": "ATTAINX00001", "name": "ATTAINX, INC.", "recipient_level": "C", "id": "ax-C"},
                {"uei": "ATTAINXSOL01", "name": "ATTAINX SOLUTIONS LLC", "recipient_level": "R", "id": "axs-R"},
                {"uei": None, "name": "ATTAINX", "recipient_level": "R", "id": "old-R"}],
    "Knock": [{"uei": "KNOCKAAAAAA1", "name": "KNOCK, INC.", "recipient_level": "R", "id": "k1-R"},
              {"uei": "KNOCKBBBBBB2", "name": "KNOCK LLC", "recipient_level": "R", "id": "k2-R"}],
}
auto_calls = []


def fake_auto(url, data=None, headers=None, timeout=60):
    auto_calls.append((url, data))
    if url.endswith("/api/v2/recipient/"):
        kw = data["keyword"]
        return {"results": RECIPIENTS.get(kw) or [r for rs in RECIPIENTS.values() for r in rs if r["uei"] == kw]}
    return fake_usa(url, data, headers, timeout)


cc.fetch_json = fake_auto
found = cc.discover("AttainX")
check("discover: the one UEI whose name equals the card's, at its parent level",
      (found["ueis"], found["ambiguous"]), ([{"uei": "ATTAINX00001", "name": "ATTAINX, INC.", "level": "P"}], False))
check("discover: two registrations under one name are ambiguous", cc.discover("Knock")["ambiguous"], True)

cc.TABLE.write_text(json.dumps({"employers": [E, ZED]}), encoding="utf-8")
cc.JOBS = tmp / "jobs.json"
cc.JOBS.write_text(json.dumps({"jobs": [{"company": c} for c in ["Acme", "AttainX", "AttainX", "Knock", "Zed & Co", "ACME"]]}),
                   encoding="utf-8")
cc.CACHE.unlink(missing_ok=True)
cc.pac_index = lambda refresh: {"by_org": {}, "gifts": {}, "cycles": cc.cycles(), "as_of": None}   # FEC up again
cl_calls.clear()
sys.argv = ["company_check.py"]
cc.main()
out = json.loads(cc.OUT.read_text(encoding="utf-8"))
ax, kn = out["companies"].get("auto-attainx") or {}, out["companies"].get("auto-knock") or {}
check("auto: every employer on the page gets a record, the table's names keep theirs",
      {k: v for k, v in out["aliases"].items()}, {"acme": "acme", "zed and co": "zed", "attainx": "auto-attainx", "knock": "auto-knock"})
check("auto: says it was matched automatically", (ax.get("auto"), kn.get("auto")),
      ({"ambiguous": False, "ueis": 1}, {"ambiguous": True, "ueis": 2}))
check("auto: contracts by the one UEI found", ax.get("contracts", {}).get("ueis"), ["ATTAINX00001"])
check("auto: a shared name gets no numbers", (kn.get("contracts"), kn.get("labor"), kn.get("pac"), kn["violations"]["kind"]),
      (None, cc.AMBIGUOUS, cc.AMBIGUOUS, "search"))
check("auto: matched on the card's name", (ax["matched"], ax["labor"]["charges"]), (["AttainX"], 0))
check("auto: CourtListener's few searches stay with reviewed employers",
      ("courts" in ax, "courts" in kn, len([u for u in cl_calls if "AttainX" in u or "Knock" in u])), (False, False, 0))
auto_calls.clear()
cc.main()
check("auto: a name looked up this week is not looked up again",
      [d["keyword"] for u, d in auto_calls if u.endswith("/api/v2/recipient/") and d["keyword"] in ("AttainX", "Knock")], [])
cc.JOBS = tmp / "no-page.json"

# ── Open roles on its own board: a whole run ─────────────────────────────────

U3 = "44444444-2222-3333-4444-555555555555"
BOARD_JSON["https://api.ashbyhq.com/posting-api/job-board/attainx"] = {"jobs": [
    {"id": U3, "title": "Cloud Engineer", "location": "Remote", "isListed": True, "publishedAt": "2026-11-01T00:00:00+00:00"}]}
cc.TABLE.write_text(json.dumps({"employers": [E, ZED, VULTR]}), encoding="utf-8")
cc.JOBS = tmp / "jobs-boards.json"
cc.JOBS.write_text(json.dumps({"jobs": PAGE_ROWS + [{"company": "AttainX", "url": f"https://jobs.ashbyhq.com/AttainX/{U3}"}]}),
                   encoding="utf-8")
cc.CACHE.unlink(missing_ok=True)
cc.BOARDS.unlink(missing_ok=True)
cc.fetch_json = fake_boards
board_calls.clear()
sys.argv = ["company_check.py", "--only", "hiring"]
cc.main()
out = json.loads(cc.OUT.read_text(encoding="utf-8"))
co = out["companies"]
today_s = cc.TODAY.isoformat()
vh = co["vultr"].get("hiring") or {}
check("hiring run: a card's Ashby link gives its employer the board's numbers",
      {k: vh.get(k) for k in ("state", "ats", "link", "open", "dated", "oldest", "watching_since", "relisted", "prospect")},
      {"state": "ok", "ats": "ashby", "link": "https://jobs.ashbyhq.com/vultr", "open": 2, "dated": 1, "oldest": "2026-04-30",
       "watching_since": today_s, "relisted": None, "prospect": 0})
check("hiring run: checked today, like the other sources", co["vultr"]["checked"].get("hiring"), today_s)
check("hiring run: Vultr's search name is not the name it avoids", co["vultr"]["search_name"], "Choopa")
check("hiring run: cards on two boards give no numbers", co["acme"].get("hiring"),
      {"state": "several", "boards": ["https://job-boards.greenhouse.io/acme", "https://jobs.lever.co/acme"]})
check("hiring run: cards on no board read here", co["zed"].get("hiring"), {"state": "none"})
check("hiring run: an automatic employer gets it by its links too",
      ((co.get("auto-attainx") or {}).get("hiring") or {}).get("open"), 1)
check("hiring run: a new board that fails says it is not read yet, with no promise of when",
      (co.get("auto-nobody") or {}).get("hiring"), {"state": "unread", "link": "https://jobs.lever.co/nobody"})
check("hiring run: the census in the stats", (out["stats"]["hiring"]["boards"], out["stats"]["hiring"]["failed"]),
      ({"found": 5, "read": 4, "failed": 1, "skipped": 0}, 1))
ledger_now = json.loads(cc.BOARDS.read_text(encoding="utf-8"))
check("hiring run: boards.json holds every board read, several's too",
      (ledger_now["version"], sorted(ledger_now["boards"])), (1, ["ashby:attainx", "ashby:vultr", "greenhouse:acme", "lever:acme"]))
check("hiring run: the ledger keeps the listing, not its text",
      ledger_now["boards"]["ashby:vultr"]["postings"][U1], {"t": "RMA Systems Engineer", "l": "Remote - US", "d": "2026-04-30",
                                                           "f": today_s, "s": today_s})
board_calls.clear()
cc.main()
check("hiring run: a board read today is not read again; one that failed is",
      board_calls, ["https://api.lever.co/v0/postings/nobody?mode=json"])
# A dry run reads the boards and writes no ledger.
kept_ledger = cc.BOARDS.read_text(encoding="utf-8")
cc.BOARDS.unlink()
sys.argv = ["company_check.py", "--dry-run", "--refresh", "--only", "hiring"]
board_calls.clear()
cc.main()
check("hiring run: a dry run writes no boards.json", (len(board_calls) > 0, cc.BOARDS.exists()), (True, False))
# The next day the Vultr board fails: its ledger stays as it was, its facts stay.
# (The run is today, so the ledger is set back a day: Vultr's board last read yesterday.)
yesterday = (cc.TODAY - timedelta(days=1)).isoformat()
ledger_before = json.loads(kept_ledger)
vrec = ledger_before["boards"]["ashby:vultr"]
vrec["first_ok"] = vrec["last_ok"] = yesterday
vrec["reads"] = [[yesterday, yesterday]]
for p in vrec["postings"].values():
    p["f"] = p["s"] = yesterday
cc.BOARDS.write_text(json.dumps(ledger_before), encoding="utf-8")
cache_v = json.loads(cc.CACHE.read_text(encoding="utf-8"))
cache_v["vultr"]["hiring"]["at"] = yesterday
cc.CACHE.write_text(json.dumps(cache_v), encoding="utf-8")
vultr_api = "https://api.ashbyhq.com/posting-api/job-board/vultr"
vultr_json = BOARD_JSON.pop(vultr_api)
sys.argv = ["company_check.py", "--refresh", "--only", "hiring"]
cc.main()
out2 = json.loads(cc.OUT.read_text(encoding="utf-8"))
ledger2 = json.loads(cc.BOARDS.read_text(encoding="utf-8"))
check("hiring run: a failed board keeps its ledger", ledger2["boards"]["ashby:vultr"], vrec)
check("hiring run: and its employer's last facts, dated the day they were read (the page says so)",
      (out2["companies"]["vultr"]["hiring"], out2["companies"]["vultr"]["checked"]["hiring"]), (vh, yesterday))
check("hiring run: and says it failed", (out2["stats"]["hiring"]["failed"], "ashby:vultr" in (out2["stats"]["hiring"]["error"] or "")),
      (2, True))
BOARD_JSON[vultr_api] = vultr_json

# Cadence: hiring is due every day, every other source once a week (REFRESH).
k_v = cc.entry_key(VULTR)
week_ago = (cc.TODAY - timedelta(days=7)).isoformat()
due_cache = {"vultr": {"hiring": {"at": yesterday, "key": k_v}, "labor": {"at": yesterday, "key": k_v},
                       "pac": {"at": week_ago, "key": k_v}}}
check("due: hiring a day old is due; labor a day old waits its week; pac a week old is due",
      (cc.due(due_cache, VULTR, "hiring"), cc.due(due_cache, VULTR, "labor"), cc.due(due_cache, VULTR, "pac")), (True, False, True))
check("due: hiring checked today is not due", cc.due({"vultr": {"hiring": {"at": today_s, "key": k_v}}}, VULTR, "hiring"), False)
# The next day, without --refresh, every board is read again.
board_apis = lambda: sorted(u for u in board_calls if "/recipient/" not in u and "spending" not in u)   # noqa: E731
ALL_APIS = sorted([vultr_api, "https://boards-api.greenhouse.io/v1/boards/acme/jobs", "https://api.lever.co/v0/postings/acme?mode=json",
                   "https://api.lever.co/v0/postings/nobody?mode=json", "https://api.ashbyhq.com/posting-api/job-board/attainx"])
cc.CACHE.unlink(missing_ok=True)
cc.BOARDS.unlink(missing_ok=True)
sys.argv = ["company_check.py", "--only", "hiring"]
cc.main()
real_today = cc.TODAY
tomorrow = (real_today + timedelta(days=1)).isoformat()
cc.TODAY = real_today + timedelta(days=1)
board_calls.clear()
cc.main()
check("hiring run: the next day, without --refresh, every board is read again", board_apis(), ALL_APIS)
check("hiring run: and the days read run on", json.loads(cc.BOARDS.read_text(encoding="utf-8"))["boards"]["ashby:vultr"]["reads"],
      [[today_s, tomorrow]])
# Vultr's cards move to another board, which does not answer: no numbers from the
# board it left, and no promise of when.
cc.JOBS = tmp / "jobs-moved.json"
cc.JOBS.write_text(json.dumps({"jobs": [{"company": "Vultr", "url": f"https://jobs.lever.co/vultr/{U1}"}]}), encoding="utf-8")
sys.argv = ["company_check.py", "--refresh", "--only", "hiring"]
cc.main()
check("hiring run: cards on a new board that is not read yet drop the old board's numbers",
      json.loads(cc.OUT.read_text(encoding="utf-8"))["companies"]["vultr"]["hiring"], {"state": "unread", "link": "https://jobs.lever.co/vultr"})
cc.TODAY = real_today
cc.JOBS = tmp / "jobs-boards.json"

# The census's limits: BOARD_MAX boards a run, the clock, and a vendor's breaker.
cc.CACHE.unlink(missing_ok=True)
cc.BOARDS.unlink(missing_ok=True)
cc.BOARD_MAX = 1
err_out = io.StringIO()
with contextlib.redirect_stderr(err_out):
    cc.main()
cc.BOARD_MAX = 400
out3 = json.loads(cc.OUT.read_text(encoding="utf-8"))
check("hiring run: BOARD_MAX boards a run, the busiest first; the rest wait",
      (out3["stats"]["hiring"]["boards"], sorted(json.loads(cc.BOARDS.read_text(encoding="utf-8"))["boards"]),
       "hiring: 4 boards not read today" in err_out.getvalue()),
      ({"found": 5, "read": 1, "failed": 0, "skipped": 4}, ["ashby:vultr"], True))
check("hiring run: an employer whose board waits is not read yet", out3["companies"]["auto-attainx"]["hiring"],
      {"state": "unread", "link": "https://jobs.ashbyhq.com/attainx"})
ledger_text = cc.BOARDS.read_text(encoding="utf-8")
cc.START = time.monotonic() - 3600
board_calls.clear()
cc.main()
cc.START = time.monotonic()
check("hiring run: past the run's time, no board is read and the ledger stays",
      (json.loads(cc.OUT.read_text(encoding="utf-8"))["stats"]["hiring"]["boards"], board_apis(), cc.BOARDS.read_text(encoding="utf-8")),
      ({"found": 5, "read": 0, "failed": 0, "skipped": 5}, [], ledger_text))
# Three Lever boards that fail, then one that answers (boards go busiest first,
# then by key). A timeout three times is the vendor down: the fourth waits. A 404
# three times is three dead boards: the vendor answered, so the fourth is read.
BOARD_JSON["https://api.lever.co/v0/postings/good?mode=json"] = [
    {"id": U1, "text": "Cloud Engineer", "categories": {"location": "Seattle, WA"}, "createdAt": LEVER_MS}]
cc.JOBS = tmp / "jobs-lever.json"
cc.JOBS.write_text(json.dumps({"jobs": [{"company": f"L{t}", "url": f"https://jobs.lever.co/{t}/{U1}"}
                                         for t in ("dead1", "dead2", "dead3", "good")]}), encoding="utf-8")
LEVER_APIS = [f"https://api.lever.co/v0/postings/{t}?mode=json" for t in ("dead1", "dead2", "dead3", "good")]


def lever_timeouts(url, data=None, headers=None, timeout=60):
    if "/dead" in url:
        board_calls.append(url)
        raise RuntimeError("TimeoutError: timed out")
    return fake_boards(url, data, headers, timeout)


for label, fetcher, calls, tally in (
        ("three 404s are three dead boards, not Lever down", fake_boards, LEVER_APIS,
         {"found": 4, "read": 1, "failed": 3, "skipped": 0}),
        ("three timeouts in a row stop Lever for the day", lever_timeouts, LEVER_APIS[:3],
         {"found": 4, "read": 0, "failed": 3, "skipped": 1})):
    cc.CACHE.unlink(missing_ok=True)
    cc.BOARDS.unlink(missing_ok=True)
    cc.fetch_json = fetcher
    board_calls.clear()
    cc.main()
    check(f"hiring run: {label}", (sorted(set(board_apis())), json.loads(cc.OUT.read_text(encoding="utf-8"))["stats"]["hiring"]["boards"]),
          (calls, tally))
cc.fetch_json = fake_boards
cc.JOBS = tmp / "no-page.json"

# ── The page's hiring section ────────────────────────────────────────────────

html = (ROOT / "index.html").read_text(encoding="utf-8")
start, end = html.find('head("Open roles on its own board")'), html.find('head("Federal contracts")')
check("page: the hiring section comes first, under its heading", 0 < start < end, True)
for word in ("ghost", "fake listing", "suspicious", "red flag"):
    check(f"page: never {word!r}", word in html.lower(), False)
block = html[start:end]
check("page: the hiring section never says repost", "repost" in block.lower(), False)
for word in ("ghost", "fake", "stale", "suspicious", "red flag", "real", "placeholder", "verdict", "score"):
    check(f"page: the hiring section never says {word!r}", bool(re.search(rf"\b{word}\b", block, re.I)), False)

# ── The reviewed table ───────────────────────────────────────────────────────

table = json.loads((ROOT / "company_names.json").read_text(encoding="utf-8"))
ids, keys, uei_owner = set(), {}, {}
UEI = re.compile(r"^[A-Z0-9]{12}$")
for e in table["employers"]:
    eid = e.get("id")
    check(f"table: {eid} has a unique id", eid not in ids, True)
    ids.add(eid)
    check(f"table: {eid} has names and legal names", bool(e.get("names")) and bool(e.get("legal")), True)
    # A note left in a name ("... (unrelated, Union City NJ)") never equals a
    # record's name, so an avoid entry with one blocks nothing and a legal name
    # with one matches nothing. Notes go in avoid_notes or notes.
    for x in (e.get("legal") or []) + (e.get("avoid") or []):
        check(f"table: {eid} name {x!r} is a bare name", "(" not in x and ";" not in x, True)
    for u in e.get("ueis") or []:
        check(f"table: {eid} UEI {u.get('uei')} is well formed", bool(UEI.match(u.get("uei") or "")), True)
        check(f"table: UEI {u.get('uei')} belongs to one employer", uei_owner.setdefault(u.get("uei"), eid), eid)
    for x in e.get("exclude_ueis") or []:
        check(f"table: {eid} excludes {x}, a well-formed UEI not also counted", bool(UEI.match(x)) and
              x not in {u["uei"] for u in e.get("ueis") or []}, True)
    for n in e["names"]:
        k = cc.page_key(n)
        check(f"table: job-board name {n!r} belongs to one employer", keys.get(k, eid), eid)
        keys[k] = eid

print(f"{total - bad}/{total} passed")
sys.exit(1 if bad else 0)
