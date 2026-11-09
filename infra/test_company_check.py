"""Tests for company_check.py and company_names.json. Stdlib only, no network.

    python3 infra/test_company_check.py

The rule under test throughout: a record counts for an employer only when a
name on it equals one of the employer's reviewed legal names after
normalizing, never on a partial or a near match.
"""

import importlib.util
import json
import re
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("company_check", ROOT / "company_check.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

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

calls = []


def fake_usa(url, data=None, headers=None, timeout=60):
    calls.append((url, data))
    if url.endswith("/recipient/"):
        return {"results": [{"id": "abc-C", "uei": "AAAAAAAAAAAA", "recipient_level": "C", "name": "ACME CORP (child)"},
                            {"id": "abc-P", "uei": "AAAAAAAAAAAA", "recipient_level": "P", "name": "ACME CORP"}]}
    ueis = data["filters"]["recipient_search_text"]
    if ueis == ["JVJVJVJVJVJV"]:
        return {"results": [{"amount": 30, "agency_abbreviation": "DOD", "code": "ARMY"}]}
    return {"results": [{"amount": 100, "agency_abbreviation": "DOD", "code": "ARMY"},
                        {"amount": 20, "agency_abbreviation": "DHS", "code": "ICE"},
                        {"amount": 5, "agency_abbreviation": "DHS", "code": "CBP"},
                        {"amount": 7, "agency_abbreviation": "GSA", "code": "FAS"}]}


cc.fetch_json = fake_usa
got = cc.contracts(E)
check("contracts: totals less the joint venture", {k: got["fy"][0][k] for k in ("total", "dod", "dhs", "ice", "cbp")},
      {"total": 102, "dod": 70, "dhs": 25, "ice": 20, "cbp": 5})
check("contracts: one search per fiscal year over every UEI at once",
      [d["filters"]["recipient_search_text"] for u, d in calls if "spending_by_category" in u][:2],
      [["AAAAAAAAAAAA", "BBBBBBBBBBBB"], ["JVJVJVJVJVJV"]])
check("contracts: contracts only, no grants or loans",
      {tuple(d["filters"]["award_type_codes"]) for u, d in calls if "spending_by_category" in u}, {("A", "B", "C", "D")})
check("contracts: links the record at the table's level", got["link"], "https://www.usaspending.gov/recipient/abc-P/latest")
check("contracts: no UEIs, no facts", cc.contracts({**E, "ueis": []}), None)
cc.fetch_json = lambda url, data=None, headers=None, timeout=60: (
    {"results": []} if url.endswith("/recipient/") else
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
check("courts: open", got["open"], 1)
check("courts: one search per legal name not avoided", (len(seen_q), budget[0]), (2, 8))
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
        return iter([["H0OR01001", "SMITH, A", "DEM"], ["S0OR02002", "JONES, B", "REP"], ["H0OR03003", "LEE, C", "LIB"]])
    if kind == "cm":
        row = lambda cid, name, org_tp, tp, org: [cid, name, "", "", "", "", "", "", "U", tp, "", "Q", org_tp, org, ""]
        return iter([row("C001", "ACME CORP PAC", "C", "Q", "ACME CORPORATION"),
                     row("C002", "ACME FEDERAL PAC", "C", "N", "Acme Federal, LLC"),
                     row("C003", "ACME WORKERS UNION PAC", "L", "Q", "ACME CORPORATION"),   # a union's, not the company's
                     row("C004", "ACMECO PAC", "C", "Q", "ACMECO CORPORATION")])
    rows = []
    for cmte, tp, amt, cand in [("C001", "24K", "1000", "H0OR01001"), ("C001", "24K", "2500", "S0OR02002"),
                                ("C001", "24E", "9999", "H0OR01001"),   # an independent expenditure, not a gift
                                ("C002", "24K", "500", "H0OR03003"), ("C004", "24K", "7777", "H0OR01001")]:
        r = [""] * 22
        r[0], r[5], r[14], r[16] = cmte, tp, amt, cand
        rows.append(r)
    return iter(rows) if cycle == cc.cycles()[0] else iter([])


cc.fec_rows = fake_fec
idx = cc.pac_index(False)
check("pac_index: corporate PACs by connected organization", sorted(idx["by_org"]), ["acme", "acme federal", "acmeco"])
got = cc.pac(E, idx)
check("pac: both of the employer's committees", sorted(c["id"] for c in got["committees"]), ["C001", "C002"])
check("pac: this cycle's gifts by party", {k: got["cycles"][0][k] for k in ("dem", "rep", "other", "gifts")},
      {"dem": 1000, "rep": 2500, "other": 500, "gifts": 3})
check("pac: a cycle with no gifts", got["cycles"][1]["gifts"], 0)

# ── Violation Tracker ────────────────────────────────────────────────────────

parents = {"acme": "acme-corporation", "acmeco": "acmeco"}
check("violations: the table's slug wins", cc.violations({**E, "vt": "acme-inc"}, parents)["link"],
      "https://violationtracker.goodjobsfirst.org/parent/acme-inc")
check("violations: a parent matched on a reviewed name", cc.violations(E, parents),
      {"link": "https://violationtracker.goodjobsfirst.org/parent/acme-corporation", "kind": "parent"})
check("violations: otherwise a name search", cc.violations({**E, "names": ["Zed"], "legal": ["Zed Holdings, Inc."]}, parents),
      {"link": "https://violationtracker.goodjobsfirst.org/?company_op=starts&company=Zed+Holdings+Inc", "kind": "search"})

# ── A whole run ──────────────────────────────────────────────────────────────

state = tmp / "state"
cc.TABLE = tmp / "company_names.json"
cc.TABLE.write_text(json.dumps({"employers": [E, {"id": "zed", "names": ["Zed & Co"], "legal": ["Zed Holdings, Inc."]}]}),
                    encoding="utf-8")
cc.STATE_DIR, cc.DATA, cc.CACHE, cc.OUT = state, state / "company-data", state / "companies.json", tmp / "out" / "companies.json"
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
check("run: an employer with no UEIs has no contract facts", out["companies"]["zed"]["contracts"], None)
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

# ── The reviewed table ───────────────────────────────────────────────────────

table = json.loads((ROOT / "company_names.json").read_text(encoding="utf-8"))
ids, keys = set(), {}
UEI = re.compile(r"^[A-Z0-9]{12}$")
for e in table["employers"]:
    eid = e.get("id")
    check(f"table: {eid} has a unique id", eid not in ids, True)
    ids.add(eid)
    check(f"table: {eid} has names and legal names", bool(e.get("names")) and bool(e.get("legal")), True)
    for u in e.get("ueis") or []:
        check(f"table: {eid} UEI {u.get('uei')} is well formed", bool(UEI.match(u.get("uei") or "")), True)
    for x in e.get("exclude_ueis") or []:
        check(f"table: {eid} excludes {x}, a well-formed UEI not also counted", bool(UEI.match(x)) and
              x not in {u["uei"] for u in e.get("ueis") or []}, True)
    for n in e["names"]:
        k = cc.page_key(n)
        check(f"table: job-board name {n!r} belongs to one employer", keys.get(k, eid), eid)
        keys[k] = eid

print(f"{total - bad}/{total} passed")
sys.exit(1 if bad else 0)
