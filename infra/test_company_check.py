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
import time
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("company_check", ROOT / "company_check.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)
cc.COURT_GAP = 0   # no pacing in tests

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

# ── A whole run ──────────────────────────────────────────────────────────────

state = tmp / "state"
cc.TABLE = tmp / "company_names.json"
ZED = {"id": "zed", "names": ["Zed & Co"], "legal": ["Zed Holdings, Inc."],
       "ueis": [{"uei": "OOOOOOOOOOOO", "name": "ZED UNIT", "level": "C"}]}
cc.TABLE.write_text(json.dumps({"employers": [E, ZED]}), encoding="utf-8")
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
sys.argv = ["company_check.py", "--dry-run"]
cc.CACHE.unlink()
cl_calls.clear()
cc.main()
check("run: a dry run leaves courts alone unless asked", len([u for u in cl_calls if "courtlistener" in u]), 0)
cc.COURT_BUDGET = 100

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
