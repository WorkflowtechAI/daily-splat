#!/usr/bin/env python3
"""Company check: public records about the employers on the Daily Splat.

Runs once a day after the splat (infra/company-check.service). For each
employer in company_names.json, a reviewed table (researched and checked against live USAspending data) that maps the
names job boards use to the employer's legal names and federal contractor IDs,
it gathers:

  contracts   USAspending: prime federal obligations by fiscal year, and how
              much came from DoD, DHS, ICE and CBP.
  labor       NLRB: unfair-labor-practice charges naming the employer, with
              how each closed (a charge is an allegation, not a finding).
  courts      CourtListener: federal employment civil-rights cases (nature of
              suit 442 and 445) with the employer as a party.
  pac         FEC bulk files: the PAC connected to the employer, and what it
              gave federal candidates by party. Its money comes from the
              company's executives, managers and shareholders who choose to
              give (other employees may be asked twice a year); the company
              runs it but cannot give to federal candidates itself.
  violations  Violation Tracker (Good Jobs First): a link to the parent page or
              a name search. Its terms allow linking, not copying the data.
  hiring      The employer's own Ashby, Greenhouse or Lever board, found from
              the links on its Splat cards (never from its name): how many
              listings are open today, how long they have been up by the
              board's own dates, and, after 90 days of daily census, roles taken
              down and listed again under a new listing. Read every day.

Every fact carries a link to its source record. An employer the table does not
cover gets search links only, never numbers: a match made on a loose name pins
someone else's lawsuit or contract on an innocent employer.

Each source refreshes on its own cadence and budget (CourtListener's free tier
allows about 125 searches a day) and is cached in $STATE_DIRECTORY; a source
that fails keeps its last facts. Writes companies.json beside jobs.json, and
the board census's ledger to $STATE_DIRECTORY/boards.json.

    python3 company_check.py                 # full run
    python3 company_check.py --only labor    # one source (still writes)
    python3 company_check.py --dry-run --dump /tmp/c.json
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import os
import re
import sqlite3
import statistics
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
JOBS = Path(os.environ.get("JOBS_JSON") or OUT.parent / "jobs.json")   # today's page: whose employers to check
CACHE = STATE_DIR / "company-check.json"
DATA = STATE_DIR / "company-data"
# The board census's ledger: which listings each board showed on which day. Not
# in DATA, which holds downloads that are fetched again; this is history that
# cannot be. splat.py reads it (same StateDirectory) for the employer's own dates.
BOARDS = STATE_DIR / "boards.json"
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()
UA = "daily-splat/1.0 (company check; https://worksearchcollective.com/splat/)"

REFRESH_DAYS = 7          # each source, per employer
REFRESH = {"hiring": 1}   # days, for a source that is not weekly: the board census is daily
SINCE_YEARS = 5          # labor charges and court cases filed in the last five years
COURT_BUDGET = 100        # CourtListener searches a day, across runs (free tier: 125 a day)
# Its free tier also limits a minute and an hour: the first live run (2026-11-09)
# got 429 after about 15 searches back to back. One search per COURT_GAP
# seconds keeps under 5 a minute, and COURT_RUN under 50 an hour.
COURT_GAP = 13
COURT_RUN = 45
COURT_PAGES = 2           # 20 dockets a page
FY_COUNT = 3              # fiscal years of federal obligations shown
AUTO_MAX = 800            # employers matched automatically, most postings first
RUN_MINUTES = 50          # then every stage stops and the run writes what it has (the unit allows 60)
BREAKER = 3               # failures in a row that stop a source for the day: the service is down
BOARD_MAX = 400           # boards the census reads a run (2026-11-06's page linked 156)
BOARD_MINUTES = 15        # the census's share of the run; the boards it did not reach wait a day
LEDGER_DAYS = 180         # a listing (or board) not seen for this long leaves the ledger
RELIST_DAYS = 90          # the window for roles taken down and listed again, and the watching it needs first
READ_GAP = 3              # days a board can go unread inside one watch (a vendor down); longer starts the watch again
SINCE =(TODAY - timedelta(days=365 * SINCE_YEARS)).isoformat()
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


def first_legal(e: dict) -> str | None:
    """The first legal name the avoid list leaves standing (legal_set's rule), or
    None. Not legal[0]: Vultr's first legal name, "The Constant Company, LLC", is
    also an unrelated Georgia company's and sits on its avoid list, and taking it
    anyway sent the Violation Tracker search, and the page's search links
    ("Constant"), to the name its own entry says to avoid (2026-11-12)."""
    keys = legal_set(e)
    return next((x for x in e.get("legal") or [] if norm_entity(x) in keys), None)


def board_name(e: dict) -> str:
    """The employer's first board name without a note in parentheses (auto_name):
    what a search uses when no legal name is left to use. An ambiguous automatic
    employer avoids its one legal name (auto_entry), and its card name's "(SSG)"
    matches no record system's name (review, 2026-11-12)."""
    return auto_name((e.get("names") or [""])[0])


def search_name(e: dict) -> str:
    """The name the page's search links use: the first legal name that survives
    the avoid list, else the board name, as for an entry with no legal names."""
    return core_name(first_legal(e) or board_name(e))


# What a name-matched source reports when every legal name of the employer is
# also another employer's once the suffix is dropped ("Absolute Business
# Solutions, Inc." and "... LLC"): records cannot be told apart by name, and
# "none found" would be a claim the check cannot make.
AMBIGUOUS = {"ambiguous": True}


# ── HTTP ─────────────────────────────────────────────────────────────────────

def fetch(url: str, data: dict | None = None, headers: dict | None = None, timeout: int = 60,
          tries: int = 3, to_file: Path | None = None):
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
            time.sleep(5 * (attempt + 1))   # USAspending drops connections in bursts
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


_court_last = [-1e9]   # when the last CourtListener search went out (monotonic seconds)


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
            wait = COURT_GAP - (time.monotonic() - _court_last[0])
            if wait > 0:
                time.sleep(wait)
            _court_last[0] = time.monotonic()
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
    vt = e.get("vt")
    first = first_legal(e)   # never a legal name the entry says to avoid
    if vt is None and not (e.get("auto") and not legal_set(e)):
        # The employer's own names only: a subsidiary's legal name finds the
        # subsidiary's page (Synopsys's "ANSYS, Inc." found Ansys).
        for n in [norm_entity(x) for x in (e.get("names") or []) + ([first] if first else [])]:
            if n in parents:
                vt = parents[n]
                break
    if vt:
        return {"link": f"{VT}/parent/{vt}", "kind": "parent"}
    name = first or board_name(e)
    return {"link": f"{VT}/?" + urllib.parse.urlencode({"company_op": "starts", "company": re.sub(r"[,.]", "", name)}),
            "kind": "search"}


# ── Open roles on its own board: Ashby, Greenhouse and Lever ─────────────────
#
# Added 2026-11-12: the job-hunt chat argued whether Vultr's ~62 open roles on
# its Ashby board were hiring, and the panel said nothing about hiring. A daily
# census reads the board each employer's Splat cards link to, counts what is
# listed by the board's own dates, and keeps a ledger of the days each listing
# was up, so a role taken down and listed again under a new listing can show
# once there are 90 days of history (state.json keeps only yesterday).
#
# The board comes from the cards' links only, never from the employer's name: a
# board named like an employer can be someone else's. Workday's dates stop at
# "30+ days", so Workday is not read here.

# splat.py's posting patterns (GH_URL, LEVER_URL, ASHBY_URL there), copied so this
# file stands alone; infra/test_company_check.py holds the copies to the same text.
GH_URL = re.compile(r"^https://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", re.I)
LEVER_URL = re.compile(r"^https://jobs\.(eu\.)?lever\.co/([\w.-]+)/([0-9a-f-]{36})", re.I)
ASHBY_URL = re.compile(r"^https://jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", re.I)


def board_of(url: str | None) -> dict | None:
    """The board a posting link is on, and the posting's id there, or None.

    The key is the one splat.py's board_key() builds, so the splat finds a card's
    board date in the ledger. Tokens are compared without case (the boards' APIs
    answer either way: Vultr's cards say "Vultr", its API takes "vultr"). Lever's
    EU boards are their own host and key ("lever-eu:"). Greenhouse's EU boards
    are not: on 2026-11-12 boards-api.eu.greenhouse.io did not resolve and
    boards-api.greenhouse.io served Veeam's EU board, so they are one API."""
    url = url or ""
    if m := GH_URL.search(url):
        tok = m[1].lower()
        return {"key": f"greenhouse:{tok}", "id": m[2], "ats": "greenhouse", "token": tok,
                "link": f"https://job-boards.greenhouse.io/{tok}",
                "api": f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs"}
    if m := LEVER_URL.search(url):
        tok, eu = m[2].lower(), ".eu" if m[1] else ""
        ats = "lever-eu" if m[1] else "lever"
        return {"key": f"{ats}:{tok}", "id": m[3].lower(), "ats": ats, "token": tok,
                "link": f"https://jobs{eu}.lever.co/{tok}", "api": f"https://api{eu}.lever.co/v0/postings/{tok}?mode=json"}
    if m := ASHBY_URL.search(url):
        tok = urllib.parse.unquote(m[1]).lower()
        q = urllib.parse.quote(tok)   # a board name may hold a space ("PAR Technology")
        return {"key": f"ashby:{tok}", "id": m[2].lower(), "ats": "ashby", "token": tok,
                "link": f"https://jobs.ashbyhq.com/{q}", "api": f"https://api.ashbyhq.com/posting-api/job-board/{q}"}
    return None


def page_rows(path: Path) -> list[dict]:
    """Today's page rows. Raises when the page cannot be read: the census then
    skips the day rather than calling every employer board-less."""
    jobs = json.loads(path.read_text(encoding="utf-8"))["jobs"]
    if not isinstance(jobs, list):
        raise ValueError(f"{path.name}: jobs is not a list")
    return [j for j in jobs if isinstance(j, dict)]


def page_boards(rows: list[dict], alias: dict[str, str]):
    """From the cards' links: each employer's boards ({employer id: {key}}), every
    board found ({key: board}), and how many cards link to each. A card whose link
    is on no board read here (Workday, a LinkedIn tip, an employer's own site)
    adds nothing.

    Each board also carries the card company name that most often links to it
    (`name`; a tie goes to the first in sort order) and its card count
    (`cards`). ledger_update keeps both, and splat.py reads the board directly
    under that name (its auto_boards), busiest first. Both leave out members'
    tips (via "member"): a member types the employer's name (for an Ashby link
    the member must), and a tip on a board the reader reads is on the page
    without David looking at it, so a tip must not name a board, win a tie for
    it, or put a board only it links to on the splat's daily list (review,
    2026-11-12). Such a board has no name and is not read as a found board;
    the census still reads it for its employer's facts (the third value counts
    every card)."""
    by_emp: dict[str, set[str]] = {}
    boards: dict[str, dict] = {}
    cards: collections.Counter = collections.Counter()
    feeds: collections.Counter = collections.Counter()
    names: dict[str, collections.Counter] = {}
    for j in rows:
        b = board_of(j.get("url"))
        if not b:
            continue
        boards.setdefault(b["key"], b)
        cards[b["key"]] += 1
        company = j.get("company")
        if j.get("via") != "member":
            feeds[b["key"]] += 1
            if isinstance(company, str) and company.strip():
                names.setdefault(b["key"], collections.Counter())[company.strip()[:200]] += 1
        eid = alias.get(page_key(company))
        if eid:
            by_emp.setdefault(eid, set()).add(b["key"])
    for key, b in boards.items():
        b["cards"] = feeds[key]
        if key in names:
            b["name"] = min(names[key].items(), key=lambda x: (-x[1], x[0]))[0]
    return by_emp, boards, cards


def board_day(v) -> str | None:
    """A board's date as YYYY-MM-DD: ISO text (Ashby, Greenhouse) or epoch
    milliseconds (Lever)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v / 1000, timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(v or ""))
    try:
        return date.fromisoformat(m[1]).isoformat() if m else None
    except ValueError:
        return None


def read_board(b: dict) -> dict:
    """What one board lists today: {"postings": {id: {"t", "l", "d"}}, "prospect": n}.
    Anything but the expected shape raises, so a bad answer never reads as a board
    that took every listing down.

    Dates: Ashby's publishedAt (Ashby documents it as when the job was last
    published), Greenhouse's first_published, Lever's createdAt. Never
    Greenhouse's updated_at: that is the last edit, and an old role edited
    yesterday would read as new. A listing with no date stays undated."""
    d = fetch_json(b["api"], timeout=30)
    ats = b["ats"]
    # Lever answers a list (an unknown site is {"ok": false, ...} with a 404);
    # Ashby and Greenhouse an object holding "jobs".
    jobs = d if ats.startswith("lever") else d.get("jobs") if isinstance(d, dict) else None
    if not isinstance(jobs, list):
        raise ValueError(f"{b['key']}: not a job list")
    postings: dict[str, dict] = {}
    prospect = 0
    for j in jobs:
        if not isinstance(j, dict) or j.get("id") in (None, ""):
            continue
        if ats == "ashby":
            if j.get("isListed") is False:   # unlisted: reachable by link only, not on the board
                continue
            row = {"t": j.get("title"), "l": j.get("location"), "d": board_day(j.get("publishedAt"))}
        elif ats == "greenhouse":
            # A prospect post is a general-interest post, not a job; Greenhouse's job
            # board API docs: "Prospect posts include a null value for internal_job_id".
            if "internal_job_id" in j and j["internal_job_id"] is None:
                prospect += 1
                continue
            loc = j.get("location")
            row = {"t": j.get("title"), "l": loc.get("name") if isinstance(loc, dict) else loc,
                   "d": board_day(j.get("first_published"))}
        else:
            cat = j.get("categories")
            row = {"t": j.get("text"), "l": cat.get("location") if isinstance(cat, dict) else None,
                   "d": board_day(j.get("createdAt"))}
        postings[str(j["id"]).lower()] = {"t": str(row["t"] or "")[:200], "l": str(row["l"] or "")[:200], "d": row["d"]}
    return {"postings": postings, "prospect": prospect}


def board_gone(err: Exception) -> bool:
    """A read that failed because the vendor answered that this one board is not
    there (404, 410; Lever's unknown site is a 404) or not a job list. Anything else
    (a timeout, a 5xx, a 429, a page that is not JSON) may be the vendor down."""
    if isinstance(err, ValueError) and not isinstance(err, json.JSONDecodeError):
        return True
    return bool(re.search(r"HTTP Error (?:404|410)\b", str(err)))


def load_ledger(path: Path) -> dict:
    """The census ledger; missing or unreadable starts empty, and says so."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("boards"), dict):
            boards = {k: v for k, v in d["boards"].items()
                      if isinstance(v, dict) and isinstance(v.get("postings"), dict) and board_day(v.get("first_ok"))
                      and board_day(v.get("last_ok"))}
            return {"version": 1, "boards": boards}
        print(f"hiring: {path.name} is not a ledger; starting empty", file=sys.stderr)
    except FileNotFoundError:
        print(f"hiring: no {path.name} yet; starting empty", file=sys.stderr)
    except (OSError, ValueError) as err:
        print(f"hiring: {path.name} unreadable ({str(err)[:80]}); starting empty", file=sys.stderr)
    return {"version": 1, "boards": {}}


def shift(d: str, days: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=days)).isoformat()


def read_runs(rec: dict) -> list[list[str]]:
    """The days the census read this board, as runs of days in a row: [[first,
    last], ...], oldest first. The census reads only the boards today's cards link
    to, so a board can go unread for weeks; what changed on it then is not known.
    A ledger entry without them (none was written so) knows only its last day."""
    runs = rec.get("reads")
    if isinstance(runs, list) and runs and all(
            isinstance(r, list) and len(r) == 2 and board_day(r[0]) == r[0] and board_day(r[1]) == r[1] and r[0] <= r[1]
            for r in runs):
        return sorted(runs)
    last = board_day(rec.get("last_ok"))
    return [[last, last]] if last else []


def ledger_update(ledger: dict, b: dict, got: dict, today: date) -> None:
    """A successful read onto the ledger. Every listing present gets today as its
    last day seen (s), and a new one today as its first (f). One absent is left
    as it was: its s falls behind, which is what taken down means here. Today
    goes on the board's read days, and today's card name and count (name,
    cards, from page_boards) on the board; an entry written before them has
    neither until its next read."""
    t = today.isoformat()
    rec = ledger["boards"].setdefault(b["key"], {"first_ok": t, "postings": {}})
    runs = read_runs(rec) if rec.get("last_ok") else []
    if runs and runs[-1][1] >= shift(t, -1):
        runs[-1][1] = max(runs[-1][1], t)
    else:
        runs.append([t, t])
    rec.update(ats=b["ats"], token=b["token"], link=b["link"], last_ok=t, prospect=got.get("prospect", 0), reads=runs)
    for k in ("name", "cards"):
        if b.get(k) is not None:
            rec[k] = b[k]
    for pid, x in got["postings"].items():
        p = rec["postings"].get(pid)
        if isinstance(p, dict):
            p.update(x, s=t)
        else:
            rec["postings"][pid] = {**x, "f": t, "s": t}


def ledger_prune(ledger: dict, today: date) -> int:
    """Listings not seen for LEDGER_DAYS go, and boards not read for as long.
    Returns how many listings and boards went."""
    cut = (today - timedelta(days=LEDGER_DAYS)).isoformat()
    gone = 0
    for key in list(ledger["boards"]):
        rec = ledger["boards"][key]
        if (rec.get("last_ok") or "") < cut:
            del ledger["boards"][key]
            gone += 1
            continue
        keep = {pid: p for pid, p in rec["postings"].items() if isinstance(p, dict) and (p.get("s") or "") >= cut}
        gone += len(rec["postings"]) - len(keep)
        rec["postings"] = keep
        rec["reads"] = [r for r in read_runs(rec) if r[1] >= cut]
    return gone


def posting_link(rec: dict, pid: str) -> str:
    return f"{rec['link']}/jobs/{pid}" if rec.get("ats") == "greenhouse" else f"{rec['link']}/{pid}"


def watch_start(rec: dict) -> str:
    """The first day of the board's current watch: back from its last read day
    through its read days, across gaps of at most READ_GAP days not read (a
    vendor down a day or two). A longer gap starts the watch again."""
    runs = read_runs(rec)
    if not runs:
        return rec.get("last_ok") or rec.get("first_ok") or ""
    start = runs[-1][0]
    for a, b in reversed(runs[:-1]):
        if (date.fromisoformat(start) - date.fromisoformat(b)).days - 1 > READ_GAP:
            break
        start = a
    return start


def read_before(runs: list[list[str]], f: str) -> str | None:
    """The last day the census read the board before day f, or None."""
    best = None
    for a, b in runs:
        if a < f:
            best = max(best or "", min(b, shift(f, -1)))
    return best


def relisted(rec: dict, today: date) -> dict | None:
    """Roles taken down and listed again under a new listing in the last
    RELIST_DAYS, or None until the board's current watch (watch_start) is that long.

    A role counts when a new listing has the same title and place as a different
    listing on this board that was gone before the new one was up, as the read
    days show: on r, the census's last read before the new listing's first day,
    the old one was already gone (its last day seen is before r), or r is the day
    before and the old one was there then (a handover overnight). r must fall in
    the window, so the change did. A gap in the reads hides what happened in it,
    so two listings up at once while nobody read the board never count, and the
    first read of a board (or the first after a gap) only sets what is there. One
    id that leaves and comes back is the same listing. A new listing whose own
    board date is before the window, or before the old one's last day seen, was up
    (perhaps unlisted) before and does not count. Counted once per title and
    place, with the newest new listing as the example (linked to the board when
    it is no longer listed)."""
    if (today - date.fromisoformat(watch_start(rec))).days < RELIST_DAYS:
        return None
    since = (today - timedelta(days=RELIST_DAYS)).isoformat()
    runs = read_runs(rec)
    by_role: dict[tuple, list] = {}
    for pid, p in rec["postings"].items():
        role = (page_key(p.get("t")), page_key(p.get("l")))
        if role[0]:
            by_role.setdefault(role, []).append((pid, p))
    found: dict[tuple, tuple] = {}
    for role, ps in by_role.items():
        for pid, p in ps:
            f = p.get("f") or ""
            r = read_before(runs, f) if board_day(f) else None
            d = board_day(p.get("d"))
            if not r or r < since or (d and d < since):
                continue
            overnight = r == shift(f, -1)
            olds = [s for s in (board_day(q.get("s")) for qid, q in ps if qid != pid) if s]
            if any((s < r or (s == r and overnight)) and not (d and d < s) for s in olds) and \
                    (role not in found or f > found[role][1]["f"]):
                found[role] = (pid, p)
    newest = sorted(found.values(), key=lambda x: (x[1]["f"], x[0]), reverse=True)[:3]
    return {"roles": len(found), "window_days": RELIST_DAYS,
            "examples": [{"title": p.get("t") or "", "place": p.get("l") or "",
                          "link": posting_link(rec, pid) if p.get("s") == rec["last_ok"] else rec["link"]}
                         for pid, p in newest]}


def hiring_facts(rec: dict, today: date) -> dict:
    """An employer's facts from its board's ledger entry, read today. Ages are
    from the board's own date to today, over what is listed now."""
    now = [p for p in rec["postings"].values() if p.get("s") == rec["last_ok"]]
    dates = sorted(d for d in (board_day(p.get("d")) for p in now) if d)
    ages = sorted((today - date.fromisoformat(d)).days for d in dates)
    return {"state": "ok", "ats": rec["ats"], "link": rec["link"], "open": len(now), "dated": len(ages),
            "under_30": sum(1 for a in ages if a < 30), "d30_89": sum(1 for a in ages if 30 <= a < 90),
            "d90_plus": sum(1 for a in ages if a >= 90), "oldest": dates[0] if dates else None,
            # median_low: an even count gives the younger middle listing's age, a whole day.
            "median_days": statistics.median_low(ages) if ages else None,
            "watching_since": watch_start(rec), "relisted": relisted(rec, today),
            "prospect": int(rec.get("prospect") or 0)}


# ── Employers the table does not cover: matched automatically ────────────────
#
# Every other employer on today's page is matched on the name its card shows,
# and the page says so. A name several federal contractors share (two UEIs that
# normalize to it) cannot be told apart, so it gets search links, as an
# ambiguous reviewed name does. The weekly review moves the busiest of these
# into company_names.json.

def page_employers(path: Path) -> list[str]:
    """Company names on today's page, most postings first."""
    try:
        jobs = json.loads(path.read_text(encoding="utf-8"))["jobs"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [n for n, _ in collections.Counter(j.get("company") for j in jobs if j.get("company")).most_common()]


def auto_name(card: str) -> str:
    """The card's name as a legal-name guess, a note in parentheses dropped
    ("Syms Strategic Group, LLC (SSG)")."""
    return re.sub(r"\s*\([^)]*\)", "", card).strip(" ,") or card


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def discover(name: str) -> dict:
    """USAspending recipients whose name equals the card's once normalized. One
    UEI is taken as the employer's; two or more mean several registrations
    share the name and the check cannot tell whose records are whose."""
    key = norm_entity(auto_name(name))
    rows = fetch_json(f"{USA}/recipient/", {"keyword": core_name(auto_name(name)), "award_type": "all", "limit": 50},
                      timeout=30).get("results") or []
    hits: dict[str, dict] = {}
    for r in rows:
        if r.get("uei") and key and norm_entity(r.get("name")) == key:
            h = hits.setdefault(r["uei"], {"uei": r["uei"], "name": r.get("name"), "level": r.get("recipient_level")})
            if r.get("recipient_level") == "P":
                h["level"] = "P"
    return {"key": key, "ueis": sorted(hits.values(), key=lambda h: h["uei"]), "ambiguous": len(hits) > 1}


def auto_entry(name: str, found: dict) -> dict:
    """A table-shaped entry for an employer matched automatically."""
    legal = auto_name(name)
    return {"id": "auto-" + slug(name), "names": [name], "legal": [legal],
            "avoid": [legal] if found.get("ambiguous") else [],
            "ueis": [] if found.get("ambiguous") else found.get("ueis") or [], "auto": True}


# ── The run ──────────────────────────────────────────────────────────────────

SOURCES = ("contracts", "labor", "courts", "pac", "violations", "hiring")
# courts' few daily searches go to reviewed employers. hiring is matched by the
# cards' links, not by name, so an automatic employer gets it the same way.
AUTO_SOURCES = ("contracts", "labor", "pac", "violations", "hiring")


def entry_key(e: dict) -> str:
    """A fingerprint of an employer's table entry. Facts cached under an older
    entry are due again, so a reviewed fix (an added avoid name, a joint
    venture excluded) shows on the next run instead of a week later."""
    return hashlib.sha256(json.dumps(e, sort_keys=True).encode()).hexdigest()[:16]


def due(cache: dict, e: dict, src: str) -> bool:
    got = (cache.get(e["id"]) or {}).get(src) or {}
    return (not got.get("at") or got.get("key") != entry_key(e)
            or (TODAY - date.fromisoformat(got["at"])).days >= REFRESH.get(src, REFRESH_DAYS))


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
    stats = {s: {"updated": 0, "kept": 0, "failed": 0, "error": None} for s in ("discover",) + SOURCES}
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
    reviewed = frozenset(owner)
    keyed = {"contracts": lambda e: {**e, "_others": sorted(others.get(e["id"], reviewed))}}

    # Employers on today's page that the table does not cover.
    covered = {page_key(n) for e in employers for n in e.get("names") or []}
    cards, seen_keys = [], set()
    for n in page_employers(JOBS):
        k = page_key(n)
        if k and k not in covered and k not in seen_keys:
            seen_keys.add(k)
            cards.append(n)
    cards = cards[:AUTO_MAX]
    probes = [{"id": "auto-" + slug(n), "names": [n]} for n in cards]
    autos: list[dict] = []

    def stage(src: str, setup, run, pool: list[dict] | None = None):
        """One source over every due employer. A setup failure skips the source
        for today and keeps its cached facts; so does running out of time or
        budget, or BREAKER failures in a row. Saves when done, so a run the
        unit kills later keeps every stage that finished."""
        if src not in only:
            return
        pool = employers if pool is None else pool
        todo = [e for e in pool if args.refresh or due(cache, keyed.get(src, dict)(e), src)]
        # Never checked first, then the longest unchecked. In table order, a
        # source that cannot finish in a day (courts: 45 searches a run, about
        # 390 for a full pass) never reached the employers at the end of the
        # table, the ones added last (found 2026-11-09).
        todo.sort(key=lambda e: ((cache.get(e["id"]) or {}).get(src) or {}).get("at") or "")
        stats[src]["kept"] = len(pool) - len(todo)
        if not todo:
            return
        try:
            ctx = setup()
        except Exception as err:  # noqa: BLE001
            fail(src, err)
            stats[src]["kept"] = len(pool)
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
        for e in autos + employers:   # a reviewed employer's name wins over an automatic one
            rec = {"id": e["id"], "legal": e.get("legal") or [], "matched": matched_names(e),
                   "search_name": search_name(e),
                   "confidence": e.get("confidence"), "names": e.get("names") or []}
            if e.get("auto"):
                found = ((cache.get(e["id"]) or {}).get("discover") or {}).get("facts") or {}
                rec["auto"] = {"ambiguous": bool(found.get("ambiguous")), "ueis": len(found.get("ueis") or [])}
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
        cache["_court_used"] = {"date": TODAY.isoformat(), "n": used_before + start_budget - budget[0]}
        if args.dry_run:
            return
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        files = [(CACHE, cache), (OUT, build())]
        if census["dirty"]:   # the ledger, once, after the census changed it
            files.append((BOARDS, census["ledger"]))
            census["dirty"] = False
        for path, obj in files:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)

    census: dict = {"ledger": None, "dirty": False}

    def hiring_stage():
        """The daily board census and each employer's hiring facts. Not stage():
        it reads boards, not employers, and one board that fails is not a streak
        of failed employers. A board that fails, or that the cap or the clock left
        for tomorrow, keeps its ledger as it was and its employers' last facts
        when they are that board's (the page dates them by checked.hiring); an
        employer with none of that board's facts gets {"state": "unread"}."""
        src = "hiring"
        if src not in only:
            return
        # An employer whose board has not been read yet is due every run, so a
        # second run the same day tries the board again.
        todo = [e for e in everyone if args.refresh or due(cache, e, src)
                or (((cache.get(e["id"]) or {}).get(src) or {}).get("facts") or {}).get("state") == "unread"]
        stats[src]["kept"] = len(everyone) - len(todo)
        if not todo:
            return
        try:
            rows = page_rows(JOBS)
        except Exception as err:  # noqa: BLE001 - no page today: keep yesterday's facts
            fail(src, err)
            stats[src]["kept"] = len(everyone)
            print(f"{src}: skipped today ({err})", file=sys.stderr)
            return
        # The page's company names to employer ids, as build() aliases them.
        alias = {page_key(n): e["id"] for e in autos + employers for n in e.get("names") or []}
        by_emp, boards, cards = page_boards(rows, alias)
        ledger = census["ledger"] = load_ledger(BOARDS)
        today = TODAY.isoformat()
        # Every board found, its employer's facts or not (splat.py dates the cards
        # from them), busiest first; one already read today is not read again.
        want = [k for k in sorted(boards, key=lambda k: (-cards[k], k))
                if args.refresh or (ledger["boards"].get(k) or {}).get("last_ok") != today]
        tally = {"found": len(boards), "read": 0, "failed": 0, "skipped": 0}
        errors: dict[str, str] = {}
        streak: dict[str, int] = {}
        t0 = time.monotonic()
        for i, key in enumerate(want):
            b = boards[key]
            if i >= BOARD_MAX or time.monotonic() - t0 > BOARD_MINUTES * 60 or time.monotonic() - START > RUN_MINUTES * 60:
                tally["skipped"] += len(want) - i
                break
            if streak.get(b["ats"], 0) >= BREAKER:   # that vendor's API is down today
                tally["skipped"] += 1
                errors[key] = f"{b['ats']}: stopped after {BREAKER} failures in a row"
                continue
            try:
                ledger_update(ledger, b, read_board(b), TODAY)
                census["dirty"] = True
                tally["read"] += 1
                streak[b["ats"]] = 0
            except Exception as err:  # noqa: BLE001 - one board never fails the census
                tally["failed"] += 1
                errors[key] = str(err)[:160]
                # A board the vendor says is not there means the vendor answered:
                # that board's trouble, not the vendor's. When it counted toward
                # the streak, three dead Lever sites in a row stopped every Lever
                # board after them, every day, since the boards go in the same
                # order (review, 2026-11-12).
                streak[b["ats"]] = 0 if board_gone(err) else streak.get(b["ats"], 0) + 1
        if tally["skipped"]:
            print(f"{src}: {tally['skipped']} boards not read today (the cap, the clock or a vendor down)", file=sys.stderr)
        if ledger_prune(ledger, TODAY):
            census["dirty"] = True
        for e in todo:
            keys = by_emp.get(e["id"]) or set()
            if not keys:
                put(e, src, {"state": "none"})
            elif len(keys) > 1:
                put(e, src, {"state": "several", "boards": sorted(boards[k]["link"] for k in keys)})
            elif (rec := ledger["boards"].get(next(iter(keys)))) and rec.get("last_ok") == today:
                put(e, src, hiring_facts(rec, TODAY))
            else:
                key = next(iter(keys))
                last = ((cache.get(e["id"]) or {}).get(src) or {}).get("facts") or {}
                if last.get("state") == "ok" and last.get("link") == boards[key]["link"]:
                    # Its board's last numbers stay; checked.hiring dates them on the page.
                    stats[src]["kept"] += 1
                else:
                    # Never read, or the numbers are another board's (its cards moved):
                    # say the board is not read yet, with no promise of when (a board
                    # that fails every day would make "tomorrow" false for good).
                    put(e, src, {"state": "unread", "link": boards[key]["link"]})
                if key in errors:
                    stats[src]["failed"] += 1
                    stats[src]["error"] = stats[src]["error"] or f"{key}: {errors[key]}"[:160]
        stats[src]["boards"] = tally
        print(f"{src}: {stats[src]}", file=sys.stderr)
        save()

    # CourtListener's limit is per day, so the budget counts this day's earlier runs.
    used = cache.get("_court_used") or {}
    used_before = used.get("n", 0) if used.get("date") == TODAY.isoformat() else 0
    budget = [max(0, min(COURT_RUN, COURT_BUDGET - used_before))]
    start_budget = budget[0]

    def labor_setup():
        idx = labor_index(nlrb_db(args.refresh))
        as_of["labor"] = datetime.fromtimestamp((DATA / "nlrb.db").stat().st_mtime, timezone.utc).date().isoformat()
        return idx

    def pac_setup():
        idx = pac_index(args.refresh)
        as_of["pac"] = idx.get("as_of") or as_of.get("pac")
        return idx

    # Cheap stages first, then the slow network ones, so a short day still
    # refreshes what it can: discovery, the local indexes, the board census (a
    # few minutes, at most BOARD_MINUTES), courts (paced, about ten minutes),
    # then contracts with whatever time is left.
    if only & set(AUTO_SOURCES):
        only.add("discover")
    stage("discover", lambda: None, lambda e, _: discover(e["names"][0]), probes)
    for n in cards:
        found = ((cache.get("auto-" + slug(n)) or {}).get("discover") or {}).get("facts")
        if found is not None:
            autos.append(auto_entry(n, found))
    everyone = employers + autos
    stage("labor", labor_setup, labor, everyone)
    stage("pac", pac_setup, pac, everyone)
    stage("violations", lambda: vt_parents(args.refresh), violations, everyone)
    hiring_stage()
    token =os.environ.get("COURTLISTENER_TOKEN", "").strip()
    stage("courts", lambda: token or (_ for _ in ()).throw(RuntimeError("no COURTLISTENER_TOKEN")),
          lambda e, tok: courts(e, tok, budget))
    stage("contracts", lambda: as_of.__setitem__("contracts", usaspending_as_of()),
          lambda e, _: contracts(e, others.get(e["id"], reviewed)), everyone)

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
