#!/usr/bin/env python3
"""Company check: public records about the employers on the Daily Splat.

Runs once a day after the splat (infra/company-check.service). For each
employer in company_names.json, a table reviewed by a person that maps the
names job boards use to the employer's legal names and federal contractor IDs,
it gathers:

  contracts   USAspending: prime federal obligations by fiscal year, and how
              much came from DoD, DHS, ICE and CBP.
  labor       NLRB: unfair-labor-practice charges naming the employer, with
              how each closed (a charge is an allegation, not a finding).
  courts      CourtListener: federal employment civil-rights cases (nature of
              suit 442 and 445) with the employer as a party.
  pac         FEC bulk files: the PAC connected to the employer, and what it
              gave federal candidates by party. Employee-funded: a company
              cannot give to federal candidates itself.
  violations  Violation Tracker (Good Jobs First): a link to the parent page or
              a name search. Its terms allow linking, not copying the data.

Every fact carries a link to its source record. An employer the table does not
cover gets search links only, never numbers: a match made on a loose name pins
someone else's lawsuit or contract on an innocent employer.

Each source refreshes on its own cadence and budget (CourtListener's free tier
allows about 125 searches a day) and is cached in $STATE_DIRECTORY; a source
that fails keeps its last facts. Writes companies.json beside jobs.json.

    python3 company_check.py                 # full run
    python3 company_check.py --only labor    # one source (still writes)
    python3 company_check.py --dry-run --dump /tmp/c.json
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

HERE = Path(__file__).resolve().parent
TABLE = HERE / "company_names.json"
_SPLAT_OUT = os.environ.get("SPLAT_OUT")
OUT = Path(os.environ.get("COMPANY_OUT") or (Path(_SPLAT_OUT).parent / "companies.json" if _SPLAT_OUT
                                              else HERE / "companies.json"))
STATE_DIR = Path(os.environ.get("STATE_DIRECTORY") or HERE / ".state")
CACHE = STATE_DIR / "company-check.json"
DATA = STATE_DIR / "company-data"
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()
UA = "daily-splat/1.0 (company check; https://iambraun.com/cv/job-sources/daily-splat/)"

REFRESH_DAYS = 7          # each source, per employer
SINCE_YEARS = 5           # labor charges and court cases filed in the last five years
COURT_BUDGET = 100        # CourtListener searches a day, across runs (free tier: 125 a day)
COURT_PAGES = 2           # 20 dockets a page
FY_COUNT = 3              # fiscal years of federal obligations shown
RUN_MINUTES = 50          # then every stage stops and the run writes what it has (the unit allows 60)
BREAKER = 3               # failures in a row that stop a source for the day: the service is down
SINCE = (TODAY - timedelta(days=365 * SINCE_YEARS)).isoformat()
START = time.monotonic()


# ── Names ────────────────────────────────────────────────────────────────────

SUFFIX = re.compile(r"\b(?:incorporated|inc|llc|l l c|ltd|limited|corp|corporation|co|company|lp|llp|plc|pllc|"
                    r"n a|na|the)\b")


def norm_entity(s: str | None) -> str:
    """A legal name reduced for exact comparison: case, punctuation and the
    corporate suffix fall away ("Amazon.com Services, LLC" -> "amazon com
    services"). Matching is whole-name equality on this, never a substring."""
    s = unescape(s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(SUFFIX.sub(" ", s).split())


def page_key(s: str | None) -> str:
    """The key the page looks a card's company up by; index.html builds the
    same one (lowercase, & as "and", anything else not a letter or digit as one space)."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower().replace("&", " and ")).split())


CORE_SUFFIX = re.compile(r"[\s,]+(?:incorporated|inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|lp|llp|plc|"
                         r"pllc|n\.a)\.?$", re.I)


def core_name(s: str) -> str:
    """A legal name without its corporate suffix, punctuation kept, for a
    search engine: "The Boeing Company" -> "Boeing", "Amazon.com Services,
    LLC" -> "Amazon.com Services". A phrase search for the core finds every
    suffix the name is filed under; norm_entity() then decides each match."""
    prev = None
    s = (s or "").strip()
    while s != prev:
        prev, s = s, CORE_SUFFIX.sub("", s).strip()
    return re.sub(r"^the\s+", "", s, flags=re.I)


def matched_names(e: dict) -> list[str]:
    """The legal names the name-matched sources use, one per normalized form."""
    keys, out = legal_set(e), {}
    for x in e.get("legal") or []:
        if norm_entity(x) in keys:
            out.setdefault(norm_entity(x), x)
    return list(out.values())


def legal_set(e: dict) -> set[str]:
    avoid = {norm_entity(a) for a in e.get("avoid") or []}
    return {n for n in (norm_entity(x) for x in e.get("legal") or []) if n and n not in avoid}


# What a name-matched source reports when every legal name of the employer is
# also another employer's once the suffix is dropped ("Absolute Business
# Solutions, Inc." and "... LLC"): records cannot be told apart by name, and
# "none found" would be a claim the check cannot make.
AMBIGUOUS = {"ambiguous": True}


# ── HTTP ─────────────────────────────────────────────────────────────────────

def fetch(url: str, data: dict | None = None, headers: dict | None = None, timeout: int = 60,
          tries: int = 2, to_file: Path | None = None):
    body = json.dumps(data).encode() if data is not None else None
    h = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    if body is not None:
        h["Content-Type"] = "application/json"
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if to_file:
                    tmp = to_file.with_suffix(to_file.suffix + ".part")
                    got = 0
                    with open(tmp, "wb") as f:
                        while chunk := r.read(1 << 20):
                            f.write(chunk)
                            got += len(chunk)
                    # A connection that closes early ends read() quietly, and a
                    # cut-off file would then be trusted for a week.
                    want = r.headers.get("Content-Length")
                    if (want and want.isdigit() and int(want) != got) or \
                            (to_file.suffix == ".zip" and not zipfile.is_zipfile(tmp)):
                        tmp.unlink(missing_ok=True)
                        raise OSError(f"incomplete download: {got} of {want or '?'} bytes")
                    os.replace(tmp, to_file)
                    return to_file
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 401, 403, 404, 410, 422, 429):
                break
        except Exception as e:  # noqa: BLE001 - network trouble of every shape
            last = e
        if attempt + 1 < tries:
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{type(last).__name__}: {str(last)[:160]}")


def fetch_json(url: str, data: dict | None = None, headers: dict | None = None, timeout: int = 60):
    return json.loads(fetch(url, data, headers, timeout))


def fresh(path: Path) -> bool:
    """A downloaded file younger than the refresh window, less six hours, so a
    file fetched at 07:05 is due again by 07:00 a week later (the facts that
    use it are due by calendar day)."""
    return path.exists() and time.time() - path.stat().st_mtime < REFRESH_DAYS * 86400 - 6 * 3600


# ── Federal contracts: USAspending ───────────────────────────────────────────

USA = "https://api.usaspending.gov/api/v2"
# Contracts only (definitive contracts, purchase orders, delivery orders, BPA calls): a CHIPS Act grant
# or a loan is federal money too, but not what "who does this employer build for" asks.
CONTRACT_TYPES = ["A", "B", "C", "D"]


def fiscal_years(today: date = TODAY) -> list[int]:
    fy = today.year + (1 if today.month >= 10 else 0)
    return [fy - i for i in range(FY_COUNT)]


# The agencies the panel breaks out, by the agency that paid (funding), as
# USAspending's agencies filter names them. The awarding agency would file
# DoD work bought through GSA's assisted-acquisition shops under GSA: Peraton
# FY2025, DoD-awarded $1,224M, DoD-funded $2,053M (review, 2026-11-09).
BUCKETS = (
    ("dod", {"type": "funding", "tier": "toptier", "name": "Department of Defense"}),
    ("dhs", {"type": "funding", "tier": "toptier", "name": "Department of Homeland Security"}),
    ("ice", {"type": "funding", "tier": "subtier", "name": "U.S. Immigration and Customs Enforcement",
             "toptier_name": "Department of Homeland Security"}),
    ("cbp", {"type": "funding", "tier": "subtier", "name": "U.S. Customs and Border Protection",
             "toptier_name": "Department of Homeland Security"}),
)


def usa_search(category: str, ueis: list[str], fy: int, agency: dict | None = None, page: int = 1) -> dict:
    """One spending_by_category search: contract obligations in fiscal year fy
    on transactions whose recipient, or whose recipient's parent, is one of
    ueis. One search over the whole list counts each transaction once, however
    the employer's IDs nest (tested 2026-11-09: Palantir's two UEIs and its
    parent family both give $584.7M for FY2024)."""
    filters = {"recipient_search_text": ueis, "award_type_codes": CONTRACT_TYPES,
               "time_period": [{"start_date": f"{fy - 1}-10-01", "end_date": f"{fy}-09-30"}]}
    if agency:
        filters["agencies"] = [agency]
    d = fetch_json(f"{USA}/search/spending_by_category/{category}/",
                   {"filters": filters, "category": category, "limit": 100, "page": page})
    if any("recipient" in str(m).lower() and "not" in str(m).lower() for m in d.get("messages") or []):
        raise RuntimeError("USAspending ignored the recipient filter")  # never report someone else's money
    return d


def subagency_totals(ueis: list[str], fy: int) -> dict[str, float]:
    """The year's total and the DoD, DHS, ICE and CBP parts of it, by funding agency."""
    tot = {"total": 0.0, "dod": 0.0, "dhs": 0.0, "ice": 0.0, "cbp": 0.0}
    for row in usa_search("funding_subagency", ueis, fy).get("results") or []:
        amt = float(row.get("amount") or 0)
        ab, code = (row.get("agency_abbreviation") or "").upper(), (row.get("code") or "").upper()
        tot["total"] += amt
        tot["dod"] += amt if ab == "DOD" else 0
        tot["dhs"] += amt if ab == "DHS" else 0
        tot["ice"] += amt if code == "ICE" else 0
        tot["cbp"] += amt if code == "CBP" else 0
    return tot


def recipient_totals(ueis: list[str], fy: int, agency: dict | None = None) -> dict[str, float]:
    """The same search, by the UEI that received the money."""
    out: dict[str, float] = {}
    for page in range(1, 21):
        d = usa_search("recipient", ueis, fy, agency, page)
        for row in d.get("results") or []:
            if row.get("uei"):
                out[row["uei"]] = out.get(row["uei"], 0.0) + float(row.get("amount") or 0)
        if not (d.get("page_metadata") or {}).get("hasNext"):
            break
    return out


def contracts(e: dict, others: frozenset[str] = frozenset()) -> dict | None:
    """Prime contract obligations per fiscal year for the employer's reviewed
    UEIs, less what UEIs that are not the employer added to that search: its
    exclude_ueis (joint ventures, units it sold) and others, the UEIs the table
    gives other employers (HPE's federal arm sits under HP Inc.'s parent record).

    Only the part the search counted comes off, split by agency the same way.
    Subtracting an excluded UEI's own total would also take off money it got
    under no parent of the employer (a joint venture's self-parented years;
    found 2026-11-09 for Boeing Sikorsky Aircraft Support, $446M alone and
    $350M under Boeing)."""
    ueis = [u["uei"] for u in e.get("ueis") or []]
    if not ueis:
        return None
    exclude = (set(e.get("exclude_ueis") or []) | set(others)) - set(ueis)
    main = (e.get("ueis") or [{}])[0]
    rows = fetch_json(f"{USA}/recipient/", {"keyword": main["uei"], "award_type": "all", "limit": 10}).get("results") or []
    hit = next((r for r in rows if r.get("uei") == main["uei"] and r.get("recipient_level") == main.get("level")), None) \
        or next((r for r in rows if r.get("uei") == main["uei"]), None)
    years, taken = [], set()
    for fy in fiscal_years():
        tot = subagency_totals(ueis, fy)
        out = {u: a for u, a in recipient_totals(ueis, fy).items() if u in exclude and a}
        less = sum(out.values())
        if out:
            taken |= set(out)
            tot["total"] -= less
            for key, agency in BUCKETS:
                part = recipient_totals(ueis, fy, agency)
                tot[key] -= sum(part.get(u, 0.0) for u in out)
        years.append({"fy": fy, **{k: round(max(0.0, v)) for k, v in tot.items()}, "less": round(less)})
    return {"fy": years, "ueis": ueis, "excluded": sorted(taken),
            "link": f"https://www.usaspending.gov/recipient/{hit['id']}/latest" if hit else None,
            "name": hit.get("name") if hit else main.get("name")}


def usaspending_as_of() -> str | None:
    """USAspending's last load. It also probes the service: when it does not
    answer, the contract stage is skipped for the day instead of waiting out a
    timeout for every employer."""
    return fetch_json(f"{USA}/awards/last_updated/", timeout=30).get("last_updated")


# ── Labor board: NLRB (the labordata project's nightly SQLite) ───────────────

NLRB_ZIP = "https://github.com/labordata/nlrb-data/releases/download/nightly/nlrb.db.zip"


def nlrb_db(refresh: bool) -> Path:
    """The NLRB case database, downloaded at most weekly (186 MB zipped, 1 GB)."""
    DATA.mkdir(parents=True, exist_ok=True)
    db = DATA / "nlrb.db"
    if not refresh and fresh(db):
        return db
    z = fetch(NLRB_ZIP, to_file=DATA / "nlrb.db.zip", timeout=600)
    with zipfile.ZipFile(z) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".db"))
        with zf.open(name) as src, open(DATA / "nlrb.db.part", "wb") as dst:
            while chunk := src.read(1 << 20):
                dst.write(chunk)
    os.replace(DATA / "nlrb.db.part", db)
    z.unlink(missing_ok=True)
    return db


def labor_index(db_path: Path) -> dict[str, list[tuple]]:
    """Every unfair-labor-practice charge against an employer since SINCE, by
    the respondent's normalized name. One pass, so each employer is a lookup."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = db.execute(
        "select p.participant, f.case_number, f.status, f.reason_closed, f.date_filed, f.date_closed, f.url "
        "from participant p join filing f using (case_number) "
        "where p.type = 'Charged Party / Respondent' and p.subtype = 'Employer' and f.case_type = 'CA' "
        "and f.date_filed >= ?", (SINCE,))
    idx: dict[str, list[tuple]] = {}
    for r in rows:
        idx.setdefault(norm_entity(r[0]), []).append(r)
    db.close()
    return idx


def labor(e: dict, idx: dict[str, list[tuple]]) -> dict:
    if not legal_set(e):
        return AMBIGUOUS
    cases: dict[str, tuple] = {}
    for n in legal_set(e):
        for r in idx.get(n, []):
            cases.setdefault(r[1], r)
    rows = sorted(cases.values(), key=lambda r: r[4] or "", reverse=True)
    closed: dict[str, int] = {}
    for r in rows:
        if (r[2] or "").lower().startswith("closed"):
            closed[r[3] or "Closed"] = closed.get(r[3] or "Closed", 0) + 1
    return {"since": SINCE, "charges": len(rows),
            "open": sum(1 for r in rows if not (r[2] or "").lower().startswith("closed")),
            "closed": dict(sorted(closed.items(), key=lambda kv: -kv[1])),
            "names": sorted({r[0] for r in rows})[:6],
            "recent": [{"case": r[1], "filed": r[4], "status": r[2], "reason": r[3],
                        "link": r[6] or f"https://www.nlrb.gov/case/{r[1]}"} for r in rows[:5]]}


# ── Courts: CourtListener ────────────────────────────────────────────────────

CL = "https://www.courtlistener.com"


def courts(e: dict, token: str, budget: list[int]) -> dict | None:
    """Federal employment civil-rights dockets (nature of suit 442, 445) since
    SINCE with one of the employer's legal names as a party. Each legal name is
    searched once by its core (suffix dropped, so "Starbucks Corp." turns up
    too), and a docket counts only when a party's name equals a legal name
    after norm_entity(); the search engine stems ("Regence" found "Regency")."""
    names = legal_set(e)
    if not token:
        return None
    if not names:
        return AMBIGUOUS
    seen: dict[int, dict] = {}
    more = False
    for legal in matched_names(e):
        q = f'party:("{core_name(legal).replace(chr(34), "")}") AND suitNature:(442 OR 445)'
        url = f"{CL}/api/rest/v4/search/?" + urllib.parse.urlencode({"type": "d", "q": q, "filed_after": SINCE})
        for _ in range(COURT_PAGES):
            if budget[0] <= 0:
                raise RuntimeError("budget")
            budget[0] -= 1
            d = fetch_json(url, headers={"Authorization": f"Token {token}"})
            for r in d.get("results") or []:
                if any(norm_entity(p) in names for p in r.get("party") or []):
                    seen[r.get("docket_id")] = r
            url = d.get("next")
            if not url:
                break
        else:
            more = True
    rows = sorted(seen.values(), key=lambda r: r.get("dateFiled") or "", reverse=True)
    # "unclosed", not "open": many dockets are captured once at filing and their
    # closing date is never filled in.
    return {"since": SINCE, "cases": len(rows), "more": more,
            "unclosed": sum(1 for r in rows if not r.get("dateTerminated")),
            "recent": [{"name": r.get("caseName"), "court": r.get("court_citation_string") or r.get("court_id"),
                        "filed": r.get("dateFiled"), "closed": r.get("dateTerminated"),
                        "link": CL + (r.get("docket_absolute_url") or "")} for r in rows[:4]]}


# ── PAC giving: FEC bulk files ───────────────────────────────────────────────

FEC_BULK = "https://www.fec.gov/files/bulk-downloads"
CM_COLS = ["CMTE_ID", "CMTE_NM", "TRES_NM", "CMTE_ST1", "CMTE_ST2", "CMTE_CITY", "CMTE_ST", "CMTE_ZIP", "CMTE_DSGN",
           "CMTE_TP", "CMTE_PTY_AFFILIATION", "CMTE_FILING_FREQ", "ORG_TP", "CONNECTED_ORG_NM", "CAND_ID"]
CN_PARTY = 2       # CAND_PTY_AFFILIATION in cn
# TRANSACTION_TP, TRANSACTION_AMT, OTHER_ID, CAND_ID, MEMO_CD and CMTE_ID in pas2
PAS_TP, PAS_AMT, PAS_OTHER, PAS_CAND, PAS_MEMO, PAS_CMTE = 5, 14, 15, 16, 19, 0
# Party codes by side. Minnesota's Democrats file as DFL, North Dakota's as DNL.
DEM, REP = {"DEM", "DFL", "DNL"}, {"REP"}
# Corporate PACs: ORG_TP C (corporation) and W (corporation without capital
# stock, such as USAA's, a reciprocal exchange).
CORP_ORG = {"C", "W"}


def cycles(today: date = TODAY) -> list[int]:
    c = today.year + (today.year % 2)
    return [c, c - 2]


def fec_file(kind: str, cycle: int, refresh: bool) -> Path:
    """One FEC bulk file (cm, cn, pas2), downloaded at most weekly."""
    DATA.mkdir(parents=True, exist_ok=True)
    yy = str(cycle)[2:]
    path = DATA / f"{kind}{yy}.zip"
    if refresh or not fresh(path):
        fetch(f"{FEC_BULK}/{cycle}/{kind}{yy}.zip", to_file=path, timeout=600)
    return path


def fec_rows(kind: str, cycle: int, refresh: bool):
    """Rows of one FEC bulk file, pipe-delimited."""
    path = fec_file(kind, cycle, refresh)
    try:
        with zipfile.ZipFile(path) as zf:
            with zf.open(zf.namelist()[0]) as f:
                for line in io.TextIOWrapper(f, encoding="latin-1"):
                    yield line.rstrip("\n").split("|")
    except zipfile.BadZipFile:
        path.unlink(missing_ok=True)   # fetched again next run
        raise


def pac_index(refresh: bool) -> dict:
    """Corporate PACs by the connected organization's normalized name, and their
    contributions to federal candidates (transaction type 24K) by party. A
    cycle whose files are not published yet (the new cycle, early in an odd
    year) is left out rather than failing the source."""
    by_org: dict[str, list[dict]] = {}
    gifts: dict[tuple[str, int], dict[str, float]] = {}
    party: dict[str, str] = {}
    cmte_cand: dict[str, str] = {}
    files: dict[int, tuple] = {}
    for i, cycle in enumerate(cycles()):
        try:
            cn = [r for r in fec_rows("cn", cycle, refresh) if r]
            cm = [r for r in fec_rows("cm", cycle, refresh) if len(r) >= len(CM_COLS)]
            pas = fec_file("pas2", cycle, refresh)
        except RuntimeError as err:
            if i == 0 and "404" in str(err):
                print(f"pac: FEC files for {cycle} not published yet", file=sys.stderr)
                continue
            raise
        files[cycle] = pas
        for r in cn:
            party.setdefault(r[0], (r[CN_PARTY] or "").upper())
        for r in cm:
            row = dict(zip(CM_COLS, r))
            if row["CAND_ID"]:
                cmte_cand.setdefault(row["CMTE_ID"], row["CAND_ID"])
            if row["ORG_TP"] in CORP_ORG and row["CMTE_TP"] in ("Q", "N") and row["CONNECTED_ORG_NM"]:
                k = norm_entity(row["CONNECTED_ORG_NM"])
                if not any(c["id"] == row["CMTE_ID"] for c in by_org.setdefault(k, [])):
                    by_org[k].append({"id": row["CMTE_ID"], "name": row["CMTE_NM"], "org": row["CONNECTED_ORG_NM"]})
    for cycle in files:
        for r in fec_rows("pas2", cycle, False):
            # Memo lines (MEMO_CD X) repeat money already counted; the FEC says not to add them.
            if len(r) <= PAS_MEMO or r[PAS_TP] != "24K" or r[PAS_MEMO] == "X":
                continue
            try:
                amt = float(r[PAS_AMT] or 0)
            except ValueError:
                continue
            cand = r[PAS_CAND] or cmte_cand.get(r[PAS_OTHER], "")
            p = party.get(cand, "")
            side = "dem" if p in DEM else "rep" if p in REP else "other"
            g = gifts.setdefault((r[PAS_CMTE], cycle), {"dem": 0.0, "rep": 0.0, "other": 0.0, "n": 0})
            g[side] += amt
            g["n"] += 1
    return {"by_org": by_org, "gifts": gifts, "cycles": sorted(files, reverse=True),
            "as_of": min((datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).date().isoformat()
                          for f in files.values()), default=None)}


def pac(e: dict, idx: dict) -> dict:
    if not legal_set(e):
        return AMBIGUOUS
    committees = []
    for n in legal_set(e):
        for c in idx["by_org"].get(n, []):
            if c not in committees:
                committees.append(c)
    out = {"committees": [{"id": c["id"], "name": c["name"], "org": c["org"],
                           "link": f"https://www.fec.gov/data/committee/{c['id']}/"} for c in committees], "cycles": []}
    for cycle in cycles():
        tot = {"cycle": cycle, "dem": 0, "rep": 0, "other": 0, "gifts": 0}
        if cycle not in idx.get("cycles", [cycle]):
            out["cycles"].append({**tot, "unpublished": True})
            continue
        for c in committees:
            g = idx["gifts"].get((c["id"], cycle))
            if g:
                tot["dem"] += round(g["dem"])
                tot["rep"] += round(g["rep"])
                tot["other"] += round(g["other"])
                tot["gifts"] += g["n"]
        out["cycles"].append(tot)
    return out


# ── Violation Tracker: links only ────────────────────────────────────────────

VT = "https://violationtracker.goodjobsfirst.org"


def vt_parents(refresh: bool) -> dict[str, str]:
    """Violation Tracker's parent list (normalized name -> slug), from the
    <select name="parent"> on its home page, one request a week."""
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "vt-parents.json"
    if not refresh and fresh(path):
        return json.loads(path.read_text(encoding="utf-8"))
    html = fetch(VT + "/", headers={"Accept": "text/html"}).decode("utf-8", "replace")
    block = re.search(r'<select name="parent".*?</select>', html, re.S)
    found = {norm_entity(unescape(m[1])): m[0] for m in re.findall(r'<option[^>]*value="([^"]+)"[^>]*>([^<]*)',
                                                                    block.group(0) if block else "") if m[0]}
    if len(found) < 1000:
        raise RuntimeError(f"Violation Tracker parent list looks wrong ({len(found)} parents)")
    path.write_text(json.dumps(found), encoding="utf-8")
    return found


def violations(e: dict, parents: dict[str, str]) -> dict:
    slug = e.get("vt")
    if slug is None:
        # The employer's own names only: a subsidiary's legal name finds the
        # subsidiary's page (Synopsys's "ANSYS, Inc." found Ansys).
        for n in [norm_entity(x) for x in (e.get("names") or []) + (e.get("legal") or [])[:1]]:
            if n in parents:
                slug = parents[n]
                break
    if slug:
        return {"link": f"{VT}/parent/{slug}", "kind": "parent"}
    name = (e.get("legal") or e.get("names") or [""])[0]
    return {"link": f"{VT}/?" + urllib.parse.urlencode({"company_op": "starts", "company": re.sub(r"[,.]", "", name)}),
            "kind": "search"}


# ── The run ──────────────────────────────────────────────────────────────────

SOURCES = ("contracts", "labor", "courts", "pac", "violations")


def entry_key(e: dict) -> str:
    """A fingerprint of an employer's table entry. Facts cached under an older
    entry are due again, so a reviewed fix (an added avoid name, a joint
    venture excluded) shows on the next run instead of a week later."""
    return hashlib.sha256(json.dumps(e, sort_keys=True).encode()).hexdigest()[:16]


def due(cache: dict, e: dict, src: str) -> bool:
    got = (cache.get(e["id"]) or {}).get(src) or {}
    return (not got.get("at") or got.get("key") != entry_key(e)
            or (TODAY - date.fromisoformat(got["at"])).days >= REFRESH_DAYS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dump")
    ap.add_argument("--only", help="comma list of sources: " + ",".join(SOURCES))
    ap.add_argument("--refresh", action="store_true", help="ignore cadences and download every file again")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else set(SOURCES)
    if args.dry_run and not args.only:
        # A dry run keeps nothing, so its CourtListener searches would be spent
        # twice that day. Name courts in --only to include it.
        only.discard("courts")

    table = json.loads(TABLE.read_text(encoding="utf-8"))
    employers = table["employers"]
    cache: dict = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except ValueError:
            cache = {}
    stats = {s: {"updated": 0, "kept": 0, "failed": 0, "error": None} for s in SOURCES}
    as_of: dict = dict((cache.get("_as_of") or {}))

    def put(e: dict, src: str, facts):
        cache.setdefault(e["id"], {})[src] = {"at": TODAY.isoformat(), "key": entry_key(e), "facts": facts}
        stats[src]["updated"] += 1

    def fail(src: str, err: Exception):
        stats[src]["failed"] += 1
        stats[src]["error"] = stats[src]["error"] or str(err)[:160]

    # Every UEI in the table and its employer. A UEI given to one employer is
    # taken out of every other employer's contract totals.
    owner = {u["uei"]: e["id"] for e in employers for u in e.get("ueis") or []}
    others = {e["id"]: frozenset(u for u, o in owner.items() if o != e["id"]) for e in employers}
    keyed = {"contracts": lambda e: {**e, "_others": sorted(others[e["id"]])}}

    def stage(src: str, setup, run):
        """One source over every due employer. A setup failure skips the source
        for today and keeps its cached facts; so does running out of time or
        budget, or BREAKER failures in a row. Saves when done, so a run the
        unit kills later keeps every stage that finished."""
        if src not in only:
            return
        todo = [e for e in employers if args.refresh or due(cache, keyed.get(src, dict)(e), src)]
        stats[src]["kept"] = len(employers) - len(todo)
        if not todo:
            return
        try:
            ctx = setup()
        except Exception as err:  # noqa: BLE001
            fail(src, err)
            stats[src]["kept"] = len(employers)
            print(f"{src}: skipped today ({err})", file=sys.stderr)
            return
        streak = 0
        for i, e in enumerate(todo):
            if time.monotonic() - START > RUN_MINUTES * 60:
                stats[src]["kept"] += len(todo) - i
                stats[src]["error"] = stats[src]["error"] or "out of time; the rest wait for tomorrow"
                break
            try:
                put(keyed.get(src, dict)(e), src, run(e, ctx))
                streak = 0
            except Exception as err:  # noqa: BLE001 - one employer never fails the source
                if str(err) == "budget":
                    stats[src]["kept"] += len(todo) - i
                    break
                fail(src, err)
                streak += 1
                if streak >= BREAKER:
                    stats[src]["kept"] += len(todo) - i - 1
                    stats[src]["error"] = f"stopped after {BREAKER} failures in a row: {err}"[:160]
                    break
        print(f"{src}: {stats[src]}", file=sys.stderr)
        save()

    def build() -> dict:
        companies, aliases = {}, {}
        for e in employers:
            rec = {"id": e["id"], "legal": e.get("legal") or [], "matched": matched_names(e),
                   "search_name": core_name((e.get("legal") or e.get("names") or [""])[0]),
                   "confidence": e.get("confidence"), "names": e.get("names") or []}
            for src in SOURCES:
                got = (cache.get(e["id"]) or {}).get(src)
                if got:
                    rec[src] = got["facts"]
                    rec.setdefault("checked", {})[src] = got["at"]
            companies[e["id"]] = rec
            for n in e.get("names") or []:
                aliases[page_key(n)] = e["id"]
        return {"generated_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "as_of": as_of, "stats": stats,
                "aliases": aliases, "companies": companies}

    def save():
        cache["_as_of"] = as_of
        cache["_court_used"] = {"date": TODAY.isoformat(), "n": COURT_BUDGET - budget[0] + used_before}
        if args.dry_run:
            return
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        for path, obj in ((CACHE, cache), (OUT, build())):
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)

    # CourtListener's limit is per day, so the budget counts this day's earlier runs.
    used = cache.get("_court_used") or {}
    used_before = used.get("n", 0) if used.get("date") == TODAY.isoformat() else 0
    budget = [max(0, COURT_BUDGET - used_before)]

    def labor_setup():
        idx = labor_index(nlrb_db(args.refresh))
        as_of["labor"] = datetime.fromtimestamp((DATA / "nlrb.db").stat().st_mtime, timezone.utc).date().isoformat()
        return idx

    def pac_setup():
        idx = pac_index(args.refresh)
        as_of["pac"] = idx.get("as_of") or as_of.get("pac")
        return idx

    stage("contracts", lambda: as_of.__setitem__("contracts", usaspending_as_of()),
          lambda e, _: contracts(e, others[e["id"]]))
    stage("labor", labor_setup, labor)
    token = os.environ.get("COURTLISTENER_TOKEN", "").strip()
    stage("courts", lambda: token or (_ for _ in ()).throw(RuntimeError("no COURTLISTENER_TOKEN")),
          lambda e, tok: courts(e, tok, budget))
    stage("pac", pac_setup, pac)
    stage("violations", lambda: vt_parents(args.refresh), violations)

    page = build()
    print(json.dumps(stats, indent=1), file=sys.stderr)
    if args.dump:
        Path(args.dump).write_text(json.dumps(page, indent=1), encoding="utf-8")
    save()
    if not args.dry_run:
        print(f"wrote {OUT} ({len(page['companies'])} employers)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
