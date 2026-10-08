#!/usr/bin/env python3
"""The daily splat: cybersecurity and the infra and IT around it, entry to senior,
in the Pacific Northwest or remote from it. Built for the job-hunt cabal.

Runs once a day on the Foundry VPS (infra/daily-splat). Each run:

  1. READS every source fresh. Company boards (Greenhouse, Lever, Ashby,
     Workday, Amazon) and the open aggregators (Remotive, RemoteOK, Jobicy,
     Himalayas, Working Nomads, The Muse) return what is open today; Foorilla
     (keyed) returns the last FOORILLA_DAYS days with an `expired` flag.
  2. FILTERS with rules: a title that reads as security or engineering, a level
     that is not intern/junior/director, and a location in WA/OR/ID or remote
     within the US.
  3. ASKS JEV (TypeSafe's typed classifier) what track and level each NEW
     posting is, so "Member of Technical Staff" lands and "Security Officer"
     at a warehouse does not. Answers are cached by posting, so a day costs
     only its new postings. If Jev is down, the rules alone decide and the run
     still lands.
  4. REPLACES the page's data. A posting a source stopped returning is gone
     from the page that day; that is how removed jobs drop. A source that
     FAILED this run keeps yesterday's postings for GRACE_DAYS, so one flaky
     board does not empty its section. Anything posted more than MAX_AGE_DAYS
     ago drops regardless.

It fails closed: if fewer than half the sources answer, it assumes the box lost
network, writes nothing, and exits non-zero. Yesterday's page stands.

Keys come from the environment (EnvironmentFile in the unit), never from this
file: FOORILLA_API_KEY, TYPESAFE_API_KEY. Either may be absent; that source or
pass is skipped and named in the run stats.

    python3 splat.py                # full run, writes jobs.json beside itself (or to $SPLAT_OUT)
    python3 splat.py --dry-run      # everything except the write
    python3 splat.py --only greenhouse,amazon --no-jev
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The page's data: beside the script, or where the server says
# (SPLAT_OUT, set in the unit to the docroot; the repo is a clone elsewhere).
OUT = Path(os.environ.get("SPLAT_OUT") or HERE / "jobs.json")
STATE_DIR = Path(os.environ.get("STATE_DIRECTORY") or HERE / ".state")
STATE = STATE_DIR / "state.json"

NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()
MAX_AGE_DAYS = 30        # older than this drops, whatever the source says
REMOTE_MAX_AGE_DAYS = 14  # remote-US is ~16k engineering postings a month; two weeks is plenty
GRACE_DAYS = 2           # a failed source keeps its postings this long
FOORILLA_DAYS = 30
JEV_MAX_PER_RUN = 8000   # first run is ~6k; past the cap the rest waits a day and rules decide meanwhile
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

# ── Company boards. Every token here answered on 2026-10-02 from the VPS. ──────
# Weighted to PNW employers and security vendors. Add a token, it just works;
# a dead token shows up as a failed source in jobs.json's run stats.
GREENHOUSE = {
    "okta": "Okta", "datadog": "Datadog", "cloudflare": "Cloudflare", "tanium": "Tanium",
    "smartsheet": "Smartsheet", "newrelic": "New Relic", "huntress": "Huntress",
    "keepersecurity": "Keeper Security", "chainguard": "Chainguard", "tailscale": "Tailscale",
    "abnormalsecurity": "Abnormal Security", "axonius": "Axonius", "dropbox": "Dropbox",
    "stripe": "Stripe", "gitlab": "GitLab", "mongodb": "MongoDB", "elastic": "Elastic",
    "twilio": "Twilio", "zscaler": "Zscaler", "netskope": "Netskope", "expel": "Expel",
    "tines": "Tines", "torq": "Torq", "anthropic": "Anthropic", "rubrik": "Rubrik",
    "offerup": "OfferUp", "textio": "Textio", "pokemoncareers": "The Pokémon Company International",
    "bungie": "Bungie", "nintendo": "Nintendo of America", "truveta": "Truveta",
    "bishopfox": "Bishop Fox", "praetorian": "Praetorian", "securityscorecard": "SecurityScorecard",
    "sumologic": "Sumo Logic", "pagerduty": "PagerDuty", "launchdarkly": "LaunchDarkly",
    "grafanalabs": "Grafana Labs", "cockroachlabs": "Cockroach Labs", "planetscale": "PlanetScale",
    "fastly": "Fastly", "bugcrowd": "Bugcrowd", "roblox": "Roblox", "discord": "Discord",
    "reddit": "Reddit", "figma": "Figma", "jamasoftware": "Jama Software",
}
LEVER = {"palantir": "Palantir", "spotify": "Spotify", "outreach": "Outreach", "shieldai": "Shield AI"}
ASHBY = {
    "snowflake": "Snowflake", "ramp": "Ramp", "openai": "OpenAI", "notion": "Notion",
    "supabase": "Supabase", "linear": "Linear", "semgrep": "Semgrep", "socket": "Socket",
    "vanta": "Vanta", "drata": "Drata", "cohere": "Cohere", "replit": "Replit",
    "1password": "1Password", "sentry": "Sentry", "zip": "Zip", "decagon": "Decagon",
    "sierra": "Sierra", "harvey": "Harvey", "persona": "Persona",
}
# (tenant, wd host, site, display name). Workday is where most big PNW employers live.
WORKDAY = [
    ("crowdstrike", "wd5", "crowdstrikecareers", "CrowdStrike"),
    ("zillow", "wd5", "Zillow_Group_External", "Zillow"),
    ("ffive", "wd5", "f5jobs", "F5"),
    ("tmobile", "wd1", "External", "T-Mobile"),
    ("nike", "wd1", "nke", "Nike"),
    ("intel", "wd1", "External", "Intel"),
    ("boeing", "wd1", "EXTERNAL_CAREERS", "Boeing"),
    ("nordstrom", "wd501", "nordstrom_careers", "Nordstrom"),
    ("micron", "wd1", "External", "Micron"),
    ("salesforce", "wd12", "External_Career_Site", "Salesforce"),
]

# ── Classification rules ─────────────────────────────────────────────────────

CYBER = re.compile(
    r"secur|cyber|infosec|appsec|devsecops|secops|\bsoc\b|threat|incident response|detection|"
    r"privacy|\bisso\b|\bissm\b|"
    r"vulnerab|penetration|pen ?test|red team|blue team|purple team|malware|forensic|\bgrc\b|"
    r"governance,? risk|identity (and|&) access|\biam\b|zero trust|cryptograph|privacy engineer|"
    r"\bsiem\b|offensive|defensive|trust (and|&) safety engineer", re.I)
# "Engineering" here means the infrastructure security people work in and next
# to, not general software development. The first live run (2026-10-03) took
# every engineer title and landed 4,539 engineering rows against 657 cyber,
# almost all of them product SWE. The group's verdict: "mostly SWE roles, not
# infosec". So a title needs an infra word AND an engineering role word.
INFRA = re.compile(
    r"cloud|infrastructure|platform|devops|dev ops|\bsre\b|site reliability|reliability engineer|network|"
    r"systems? (engineer|administrator|architect|admin)|sysadmin|\bit (engineer|architect|systems)|"
    r"identity|endpoint|linux|windows|active directory|\bentra\b|kubernetes|\bk8s\b|terraform|"
    r"observability|\bnoc\b|data ?center|virtualization|vmware|storage engineer|edge|"
    # The entry lane (2026-10-04, a cabal member: "entry positions within
    # sysadmin, cybersecurity or support engineer"): IT support and help desk
    # are the way in, so they count as infra.
    r"help ?desk|service desk|desktop support|technical support|tech support|support engineer|"
    r"\bit (support|specialist|technician|analyst|administrator|associate|operations)|"
    r"system administrator|systems administrator|field it|end.user (support|computing)", re.I)
ENG_ROLE = re.compile(r"engineer|architect|administrator|\badmin\b|\bsre\b|devops|specialist|operations|"
                      r"support|technician|\bdesk\b|analyst|associate", re.I)
NOT_ROLE = re.compile(  # wrong job family for this page, whatever the keywords
    r"\bsales\b(?! engineer)|account executive|account manager|business development|marketing|"
    r"recruit|talent|counsel|attorney|paralegal|legal|customer success manager|\bsdr\b|\bbdr\b|"
    r"security guard|(?<!information )(?<!systems )(?<!system )security officer(?!.*\bisso\b)|physical security|loss prevention|securities|social security|"
    r"sourcing|procurement|program manager(?!.*secur)|product manager|project manager|designer|"
    r"\bux\b|writer|scrum|data analyst|business analyst|"
    r"mechanical|electrical|civil|structural|chemical|manufacturing|process engineer|field service|"
    r"maintenance|facilities|hvac|construction|industrial|environmental|geotechnical|biomedical|"
    r"packaging|physical design|\basic\b|\brtl\b|analog|mixed.signal|\bpcb\b|\brf engineer|"
    # "technician" alone is not here: IT and data-center technicians are entry roles.
    r"(lab|automotive|surgical|veterinary|dental|wind|solar|fiber|cable|install\w*) technician|electrician|"
    r"customer (support|service|care|experience)(?!.*engineer)|call center|"
    r"nurse|clinical|pharmac|driver|warehouse|"
    r"semiconductor|equipment engineer|\byield\b|\bfab\b|wafer|lithograph|\betch\b|diffusion|metrology|"
    r"thin film|product engineer, deg|design validation|failure analysis|test engineer, (dram|nand)", re.I)
# Internships are not jobs to apply to from here; everything else junior is the
# entry lane.
INTERN = re.compile(r"\bintern\b|internship|\bco-?op\b|summer 20\d\d", re.I)
# Words that make a role entry level whatever else the title says.
ENTRY_STRONG = re.compile(r"\bjunior\b|\bjr\.?\b|entry.level|new (college )?grad|graduate|apprentice|trainee|"
                          r"early career", re.I)
# Grade markers. "Sr. Security Engineer I" is senior's first grade, not entry,
# so a senior word outranks these.
ENTRY_WEAK = re.compile(r"\bassociate\b|\b(tier|level)\s*(1|i)\b|"
                        r"\b(engineer|developer|analyst|scientist|administrator|technician|specialist)\s+(i|1)\b", re.I)
ENTRY = re.compile(ENTRY_STRONG.pattern + "|" + ENTRY_WEAK.pattern, re.I)
TOO_SENIOR = re.compile(
    r"\bdirector\b|\bvp\b|vice president|head of|\bchief\b|\bcto\b|\bciso\b|\bcio\b|\bsvp\b|\bevp\b", re.I)
STAFF = re.compile(r"\bstaff\b|principal|distinguished|\bfellow\b", re.I)
SENIOR = re.compile(r"senior|\bsr\.?\b|\blead\b|\biii\b|\biv\b|\b(engineer|developer)\s+(3|4)\b|manager|architect", re.I)
CLEARANCE = re.compile(r"clearance|\bts/sci\b|top secret|\bsecret\b|polygraph", re.I)

PNW_CITY = re.compile(
    r"seattle|bellevue|redmond|kirkland|bothell|everett|tacoma|olympia|spokane|renton|issaquah|"
    r"lynnwood|tukwila|sammamish|woodinville|mukilteo|bellingham|richland|kennewick|pullman|"
    r"portland|beaverton|hillsboro|eugene|corvallis|tigard|lake oswego|wilsonville|"
    r"\bbend\b|boise|meridian|nampa|coeur d.alene|idaho falls|puget sound", re.I)
PNW_STATE = re.compile(r"\bwashington\b|\boregon\b|\bidaho\b", re.I)
# Only the shapes a state code actually takes in a location: ", WA", "US-WA",
# "WA 98101". A bare " OR " is the English word ("NY OR US Remote" was read as
# Oregon on 2026-10-03). Case-sensitive on purpose.
PNW_ABBR = re.compile(r"(?:,\s*|\bUSA?\s*-\s*|\bUSA?-)(WA|OR|ID)\b|\b(WA|OR|ID)\s+\d{5}\b")
# A segment that names DC, or Washington as a street, is not Washington State.
NOT_WA_STATE = re.compile(r"\bd\.?\s?c\.?\b|district of columbia|washington\s+(st|street|ave|avenue|blvd|rd|road)\b", re.I)
# Same-named cities elsewhere.
NOT_PNW_CITY = re.compile(r"portland,?\s*(me\b|maine)|meridian,?\s*(ms\b|mississippi)|everett,?\s*(ma\b|mass)|"
                          r"bellevue,?\s*(ne\b|nebraska)|richland,?\s*(ms\b|mississippi)", re.I)
REMOTE = re.compile(r"remote|anywhere|work from home|\bwfh\b|distributed|virtual|telecommute", re.I)
US_COUNTRY = re.compile(r"\bU\.?S\.?A?\b|united states", re.I)
WORLD = re.compile(r"worldwide|global|anywhere|international", re.I)
US = re.compile(
    r"\bU\.?S\.?A?\b|united states|(?<!latin )\bamericas?\b|"
    r"alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|"
    r"illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|"
    r"mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|"
    r"north carolina|north dakota|ohio|oklahoma|pennsylvania|rhode island|south carolina|south dakota|"
    r"tennessee|texas|utah|vermont|virginia|wisconsin|wyoming", re.I)
# What is left of a location once the remote words are gone. Anything left is a
# place, and then the place has to be in the US.
REMOTE_NOISE = re.compile(r"remote|anywhere|work from home|\bwfh\b|distributed|virtual|telecommute|"
                          r"flexible|fully|eligible|office|first|only|friendly|\bor\b|\band\b|\bin\b|[^a-z]+", re.I)
NOT_US = re.compile(
    r"canada|mexico|brazil|argentina|colombia|chile|peru|latam|latin america|europe|\beu\b|emea|"
    r"united kingdom|\buk\b|england|ireland|germany|france|spain|portugal|netherlands|poland|"
    r"romania|ukraine|israel|india|pakistan|philippines|apac|asia|australia|new zealand|japan|"
    r"singapore|africa|nigeria|kenya|egypt|turkey|vietnam|indonesia|china|korea|taiwan|"
    r"emirates|\buae\b|dubai|saudi|qatar|bahrain|switzerland|austria|belgium|sweden|norway|denmark|"
    r"finland|italy|greece|czech|hungary|bulgaria|serbia|croatia|estonia|latvia|lithuania|malaysia|"
    r"thailand|bangladesh|sri lanka|costa rica|guatemala|uruguay|paraguay|venezuela|ecuador|bolivia", re.I)


def where(location: str, remote_flag: bool = False) -> str | None:
    """'pnw', 'remote', or None. PNW wins: a PNW hybrid that also allows remote is local."""
    loc = location or ""
    # One segment at a time: "Seattle, WA | Washington, DC" is PNW by its first
    # segment, and the DC in the second must not veto it.
    for seg in re.split(r"[;|\n]", loc):
        if NOT_PNW_CITY.search(seg):
            continue
        if PNW_CITY.search(seg) or PNW_ABBR.search(seg):
            return "pnw"
        if PNW_STATE.search(seg) and not NOT_WA_STATE.search(seg):
            return "pnw"
    said_remote = bool(REMOTE.search(loc))
    if not (said_remote or remote_flag):
        return None
    if not said_remote:
        # Flag only: the text is a place. Trust it when that place is the country.
        return "remote" if US_COUNTRY.search(loc) or not REMOTE_NOISE.sub("", loc) else None
    if US.search(loc) or (WORLD.search(loc) and not NOT_US.search(loc)):
        return "remote"
    # A remote flag on a posting whose location names a city (Ashby marks
    # "NL-Amsterdam" and "New York City" isRemote) is not remote from here.
    # Only a location that is nothing but remote words passes without a US mention.
    if not REMOTE_NOISE.sub("", loc) and not NOT_US.search(loc):
        return "remote"
    return None


# Data and ML platforms say "platform" and "infrastructure" too, and are not
# the infra this page means (Jev let "Data Platform Engineer" through on 2026-10-03).
DATA_ML = re.compile(r"\bdata\b(?! ?cent(er|re))|\bml\b|machine learning|\bai platform|recommendation|databricks|snowflake|"
                     r"analytics|e-?commerce|\bmlops\b", re.I)
SEC_ROLE = re.compile(r"engineer|architect|analyst|specialist|consultant|officer|manager|\blead\b|tester|auditor|"
                      r"administrator|researcher|hunter|responder", re.I)


def rule_track(title: str, sec_topic: bool = False) -> str | None:
    """sec_topic: the source already filed it under infosec (Foorilla topics 102/26).
    Then a role-shaped title is enough ("Principal Solutions Engineer" at a
    security vendor), and Jev has the final say."""
    if NOT_ROLE.search(title):
        return None
    if CYBER.search(title) or (sec_topic and SEC_ROLE.search(title)):
        return "cyber"
    if INFRA.search(title) and ENG_ROLE.search(title) and not DATA_ML.search(title):
        return "eng"
    return None


def rule_level(title: str, hint: str | None = None) -> str | None:
    """'entry' | 'mid' | 'senior' | 'staff' | None (intern or executive: out)."""
    if INTERN.search(title) or TOO_SENIOR.search(title) or hint == "EX":
        return None
    if ENTRY_STRONG.search(title):
        return "entry"
    if ENTRY_WEAK.search(title) and not (STAFF.search(title) or SENIOR.search(title)):
        return "entry"
    if hint == "EN" and not (STAFF.search(title) or SENIOR.search(title)):
        return "entry"
    if STAFF.search(title):
        return "staff"
    if SENIOR.search(title) or hint == "SE":
        return "senior"
    return "mid"


# ── HTTP ─────────────────────────────────────────────────────────────────────

def http_json(url: str, data: dict | None = None, headers: dict | None = None, tries: int = 3, timeout: int = 40):
    body = json.dumps(data).encode() if data is not None else None
    h = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    if body is not None:
        h["Content-Type"] = "application/json"
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (404, 410, 401, 403, 422):
                break  # retrying will not change the answer
            time.sleep(2 ** (attempt + 1))
        except Exception as e:  # noqa: BLE001 - network flakiness of every shape
            last = e
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"{type(last).__name__}: {str(last)[:120]}")


def text(html: str | None, n: int = 1500) -> str:
    s = re.sub(r"<[^>]+>", " ", unescape(html or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]


def iso(v) -> str | None:
    """Anything date-like to YYYY-MM-DD, or None."""
    if v in (None, ""):
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, timezone.utc).date().isoformat()
        s = str(v).strip()
        if re.fullmatch(r"\d{9,13}", s):
            return iso(int(s))
        if re.match(r"\d{4}-\d{2}-\d{2}", s):
            return s[:10]
        for fmt in ("%B %d, %Y", "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                pass
        return None
    except Exception:  # noqa: BLE001
        return None


def money(lo, hi, unit: str = "") -> str | None:
    try:
        lo, hi = int(float(lo or 0)), int(float(hi or 0))
    except (TypeError, ValueError):
        return None
    if not lo and not hi:
        return None
    k = lambda x: f"${round(x / 1000)}K" if x >= 1000 else f"${x}"  # noqa: E731
    return (f"{k(lo)}–{k(hi)}" if lo and hi and lo != hi else k(lo or hi)) + unit


def posting(**kw) -> dict:
    p = {"title": "", "company": "", "url": "", "location": "", "remote": False, "posted": None,
         "salary": None, "desc": "", "hint": None, "origin": "company", "sec_topic": False}
    p.update(kw)
    p["title"] = re.sub(r"\s+", " ", unescape(p["title"] or "")).strip()
    p["company"] = unescape(p["company"] or "").strip()
    return p


# Recruiters and staffing shops posting on behalf of an unnamed employer. The
# page lists employers; a recruiter's copy of a role is noise at best.
AGENCY = re.compile(r"recruit|staffing|talent|\bsearch\b|headhunt|mercor|remote office|consultants? inc|"
                    r"jobgether|ladders|hackajob|haystack|swooped|remotehunter|lato jobs|greenlever", re.I)


# ── Sources. Each returns a list of posting() dicts or raises. ───────────────

def src_greenhouse(token: str, name: str) -> list[dict]:
    d = http_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    return [posting(title=j.get("title"), company=name, url=j.get("absolute_url"),
                    location=(j.get("location") or {}).get("name", ""),
                    posted=iso(j.get("first_published") or j.get("updated_at")))
            for j in d.get("jobs", [])]


def src_lever(token: str, name: str) -> list[dict]:
    d = http_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    out = []
    for j in d:
        c = j.get("categories") or {}
        locs = c.get("allLocations") or [c.get("location") or ""]
        out.append(posting(title=j.get("text"), company=name, url=j.get("hostedUrl"),
                           location="; ".join(x for x in locs if x), remote=j.get("workplaceType") == "remote",
                           posted=iso(j.get("createdAt")), desc=text(j.get("descriptionPlain") or j.get("description"))))
    return out


def src_ashby(token: str, name: str) -> list[dict]:
    d = http_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        comp = (j.get("compensation") or {}).get("compensationTierSummary")
        out.append(posting(title=j.get("title"), company=name, url=j.get("jobUrl"),
                           location="; ".join(x for x in locs if x),
                           # isRemote is set on hybrid roles too; workplaceType is the real answer.
                           remote=(j.get("workplaceType") or ("Remote" if j.get("isRemote") else "")) == "Remote",
                           posted=iso(j.get("publishedAt")), salary=comp,
                           desc=text(j.get("descriptionPlain") or j.get("descriptionHtml"))))
    return out


def _wd_facets(node, out: list):
    """Walk Workday's facet tree for (facetParameter, id, descriptor) leaves."""
    if isinstance(node, dict):
        param = node.get("facetParameter")
        for v in node.get("values") or []:
            if isinstance(v, dict) and "id" in v and "descriptor" in v:
                out.append((param, v["id"], v["descriptor"]))
            _wd_facets(v, out)
    elif isinstance(node, list):
        for x in node:
            _wd_facets(x, out)
    return out


def _wd_days(s: str) -> str | None:
    s = (s or "").lower()
    if "today" in s:
        return TODAY
    if "yesterday" in s:
        return (NOW - timedelta(days=1)).date().isoformat()
    m = re.search(r"(\d+)\+?\s*days", s)
    return (NOW - timedelta(days=int(m.group(1)))).date().isoformat() if m else None


def src_workday(tenant: str, host: str, site: str, name: str) -> list[dict]:
    base = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    first = http_json(base, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    targets = [(p, i, d) for p, i, d in _wd_facets(first.get("facets") or [], [])
               if p and "location" in p.lower() and where(d)]
    out, seen = [], set()
    for param, fid, desc in targets:
        for q in ("security", "cyber", "cloud", "infrastructure", "network", "systems engineer", "devops", "reliability",
                  "support", "administrator", "help desk"):
            offset = 0
            while offset < 400:
                d = http_json(base, {"appliedFacets": {param: [fid]}, "limit": 20, "offset": offset, "searchText": q})
                rows = d.get("jobPostings") or []
                for j in rows:
                    path = j.get("externalPath") or ""
                    if path in seen:
                        continue
                    seen.add(path)
                    loc = j.get("locationsText") or ""
                    out.append(posting(title=j.get("title"), company=name,
                                       url=f"https://{tenant}.{host}.myworkdayjobs.com/{site}{path}",
                                       location=desc if re.match(r"\d+ Locations", loc) else loc,
                                       remote=bool(REMOTE.search(desc)), posted=_wd_days(j.get("postedOn"))))
                if len(rows) < 20:
                    break
                offset += 20
                time.sleep(0.3)
    if not targets:
        raise RuntimeError("no PNW or remote location facet found")
    return out


def src_amazon() -> list[dict]:
    out, seen = [], set()
    for state in ("Washington", "Oregon", "Idaho"):
        for q in ("security", "systems engineer", "systems development engineer", "devops",
                  "site reliability", "cloud engineer", "infrastructure", "network engineer", "cloud support engineer",
                  "it support", "data center technician", "system administrator"):
            offset = 0
            while offset < 1000:
                params = {"base_query": q, "normalized_country_code[]": "USA", "normalized_state_name[]": state,
                          "result_limit": 100, "offset": offset, "sort": "recent"}
                d = http_json("https://www.amazon.jobs/en/search.json?" + urllib.parse.urlencode(params))
                rows = d.get("jobs") or []
                for j in rows:
                    if j.get("id_icims") in seen:
                        continue
                    seen.add(j.get("id_icims"))
                    out.append(posting(title=j.get("title"), company=j.get("company_name") or "Amazon",
                                       url="https://www.amazon.jobs" + (j.get("job_path") or ""),
                                       location=j.get("normalized_location") or j.get("location") or "",
                                       posted=iso(j.get("posted_date")),
                                       desc=text((j.get("description_short") or "") + " " + (j.get("basic_qualifications") or ""))))
                if len(rows) < 100:
                    break
                offset += 100
                time.sleep(0.4)
    # Amazon's company_name is a legal entity ("Amazon.com Services LLC"); say Amazon,
    # which is also what norm() folds Foorilla's "Amazon.com" copy of a role into.
    for p in out:
        p["company"] = "Amazon"
    return out


def src_foorilla() -> list[dict]:
    key = os.environ.get("FOORILLA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("no FOORILLA_API_KEY")
    since = (NOW - timedelta(days=FOORILLA_DAYS)).strftime("%Y-%m-%dT00:00:00Z")
    remote_since = (NOW - timedelta(days=REMOTE_MAX_AGE_DAYS)).strftime("%Y-%m-%dT00:00:00Z")
    pnw = ("Washington", "Oregon", "Idaho", "Boise")
    queries = [{"location": x, "published_after": since} for x in pnw]
    # Foorilla's own infosec categories: 102 "InfoSec & Privacy" (where isecjobs
    # was folded in), 26 "Cybersecurity". Measured 2026-10-03: ~900 remote-US in
    # 14 days, ~530 in Washington in 30. No experience_level filter: many rows
    # carry none, and the rules and Jev settle level anyway.
    for topic in (102, 26):
        queries.append({"topic": topic, "country": 238, "has_remote": "true", "published_after": remote_since})
    for t in ("security", "cyber", "soc analyst", "threat", "incident response", "vulnerability",
              "penetration", "grc", "identity", "detection", "devops", "site reliability",
              "cloud engineer", "infrastructure", "network engineer", "systems engineer", "platform engineer",
              # The entry lane: where sysadmin and support careers start.
              "help desk", "service desk", "desktop support", "technical support", "it support",
              "support engineer", "system administrator", "systems administrator", "noc", "it specialist",
              "data center technician"):
        queries.append({"country": 238, "has_remote": "true", "title": t, "published_after": remote_since})
    out, seen = [], set()
    for q in queries:
        page = 1
        while True:
            params = {**q, "company_agency": "false", "page_size": 1000, "page": page}
            url = "https://foorilla.com/api/v1/hiring/job/?" + urllib.parse.urlencode(params)
            d = http_json(url, headers={"Api-Key": key})
            for x in d.get("results") or []:
                if x.get("id") in seen or x.get("expired"):
                    continue
                seen.add(x.get("id"))
                wm = x.get("work_mode")
                loc = x.get("location") or ""
                # has_remote alone also marks onsite jobs with a remote option; trust work_mode 2.
                remote = wm == 2 or (wm is None and bool(x.get("has_remote")))
                if "country" in q and not remote and not where(loc):
                    continue
                c = x.get("company") or {}
                tail = ""
                if remote and "remote" not in loc.lower():
                    tail += " (remote)"
                # The query was US-only, so the row is US even when its
                # location is a bare city ("San Francisco", "Ford Island, HI").
                if "country" in q and remote and not US_COUNTRY.search(loc):
                    tail += " · United States"
                topics = {t.get("id") for t in x.get("topics") or []}
                out.append(posting(title=x.get("title"), company=c.get("name"), url=x.get("apply_url"),
                                   location=loc + tail, sec_topic=bool(topics & {102, 26}),
                                   remote=remote, posted=iso(x.get("published")),
                                   salary=money(x.get("salary_min_usd"), x.get("salary_max_usd"), "/yr"),
                                   hint=x.get("experience_level"), origin="aggregator",
                                   desc="Skills: " + ", ".join(t.get("name", "") for t in x.get("tags") or [])))
            pages = d.get("pages") or 1
            if page >= pages:
                break
            page += 1
            time.sleep(0.5)
        time.sleep(0.5)  # Foorilla allows 5 starts a second; stay well under
    return out


def src_remotive() -> list[dict]:
    out = []
    for cat in ("software-dev", "devops", "data"):
        d = http_json(f"https://remotive.com/api/remote-jobs?category={cat}")
        for j in d.get("jobs", []):
            out.append(posting(title=j.get("title"), company=j.get("company_name"), url=j.get("url"),
                               location="Remote · " + (j.get("candidate_required_location") or ""), remote=True,
                               posted=iso(j.get("publication_date")), salary=j.get("salary") or None,
                               desc=text(j.get("description")), origin="aggregator"))
    return out


def src_remoteok() -> list[dict]:
    d = http_json("https://remoteok.com/api")
    return [posting(title=j.get("position"), company=j.get("company"), url=j.get("url"),
                    location="Remote · " + (j.get("location") or ""), remote=True, posted=iso(j.get("epoch")),
                    salary=money(j.get("salary_min"), j.get("salary_max"), "/yr"),
                    desc=text(j.get("description")), origin="aggregator")
            for j in d if isinstance(j, dict) and j.get("position")]


def src_jobicy() -> list[dict]:
    out = []
    for extra in ("", "&tag=security", "&industry=engineering"):
        d = http_json(f"https://jobicy.com/api/v2/remote-jobs?count=100&geo=usa{extra}")
        for j in d.get("jobs", []):
            out.append(posting(title=unescape(j.get("jobTitle") or ""), company=j.get("companyName"), url=j.get("url"),
                               location="Remote · " + (j.get("jobGeo") or "USA"), remote=True,
                               posted=iso(j.get("pubDate")),
                               salary=money(j.get("annualSalaryMin"), j.get("annualSalaryMax"), "/yr"),
                               hint={"Senior": "SE", "Midweight": "MI", "Junior": "EN"}.get(j.get("jobLevel")),
                               desc=text(j.get("jobExcerpt")), origin="aggregator"))
    return out


def src_himalayas() -> list[dict]:
    out = []
    for offset in range(0, 400, 20):
        d = http_json(f"https://himalayas.app/jobs/api?limit=20&offset={offset}")
        rows = d.get("jobs") or []
        for j in rows:
            restr = j.get("locationRestrictions") or []
            out.append(posting(title=j.get("title"), company=j.get("companyName"),
                               url=j.get("applicationLink") or j.get("guid"),
                               location="Remote · " + (", ".join(restr) if restr else "anywhere"), remote=True,
                               posted=iso(j.get("pubDate")), salary=money(j.get("minSalary"), j.get("maxSalary"), "/yr"),
                               hint={"Senior": "SE", "Mid-level": "MI", "Entry-level": "EN"}.get(j.get("seniority", [None])[0] if isinstance(j.get("seniority"), list) and j.get("seniority") else None),
                               desc=text(j.get("excerpt") or j.get("description")), origin="aggregator"))
        if len(rows) < 20:
            break
        time.sleep(0.4)
    return out


def src_workingnomads() -> list[dict]:
    d = http_json("https://www.workingnomads.com/api/exposed_jobs/")
    return [posting(title=j.get("title"), company=j.get("company_name"), url=j.get("url"),
                    location="Remote · " + (j.get("location") or ""), remote=True, posted=iso(j.get("pub_date")),
                    desc=text(j.get("description")), origin="aggregator")
            for j in d]


def src_muse() -> list[dict]:
    out = []
    for loc in ("Seattle, WA", "Bellevue, WA", "Redmond, WA", "Portland, OR", "Boise, ID", "Flexible / Remote"):
        for page in range(0, 5):
            params = [("page", page), ("location", loc), ("level", "Mid Level"), ("level", "Senior Level"),
                      ("category", "Software Engineering"), ("category", "Computer and IT"),
                      ("category", "Data and Analytics"), ("category", "IT")]
            d = http_json("https://www.themuse.com/api/public/jobs?" + urllib.parse.urlencode(params))
            for j in d.get("results", []):
                locs = "; ".join(x.get("name", "") for x in j.get("locations") or [])
                out.append(posting(title=j.get("name"), company=(j.get("company") or {}).get("name"),
                                   url=(j.get("refs") or {}).get("landing_page"), location=locs,
                                   remote="remote" in locs.lower(), posted=iso(j.get("publication_date")),
                                   desc=text(j.get("contents")), origin="aggregator"))
            if page + 1 >= (d.get("page_count") or 0):
                break
            time.sleep(0.4)
    return out


def all_sources(only: set[str] | None):
    s = [("foorilla", "Foorilla", src_foorilla, ()), ("amazon", "Amazon", src_amazon, ()),
         ("remotive", "Remotive", src_remotive, ()), ("remoteok", "RemoteOK", src_remoteok, ()),
         ("jobicy", "Jobicy", src_jobicy, ()), ("himalayas", "Himalayas", src_himalayas, ()),
         ("workingnomads", "Working Nomads", src_workingnomads, ()), ("muse", "The Muse", src_muse, ())]
    s += [(f"greenhouse:{t}", n, src_greenhouse, (t, n)) for t, n in GREENHOUSE.items()]
    s += [(f"lever:{t}", n, src_lever, (t, n)) for t, n in LEVER.items()]
    s += [(f"ashby:{t}", n, src_ashby, (t, n)) for t, n in ASHBY.items()]
    s += [(f"workday:{w[0]}", w[3], src_workday, w) for w in WORKDAY]
    if only:
        s = [x for x in s if x[0] in only or x[0].split(":")[0] in only]
    return s


# ── Jev ──────────────────────────────────────────────────────────────────────

JEV_QUESTIONS = {
    "track": {"type": "choice", "instructions": "Which job family is this role?", "criteria": {
        "cyber": "Cybersecurity: security engineering, security operations, detection and response, "
                 "application or cloud security, identity and access, GRC, penetration testing, security architecture.",
        "eng": "Infrastructure and IT that security teams work alongside: cloud, platform, DevOps/SRE, "
               "network, systems, identity, endpoint or IT infrastructure engineering or administration, "
               "including system administrators, IT support, help desk, desktop support, NOC, and "
               "technical support engineers who troubleshoot systems.",
        "neither": "Anything else, including general application or product software development (frontend, "
                   "backend, full-stack, mobile, ML, data, games) whose main job is not infrastructure or security; "
                   "customer service or call-center support that is not technical; "
                   "sales, marketing, recruiting, analysts who do not engineer, "
                   "physical security, hardware, mechanical, civil, manufacturing or chip-design engineering."}},
    "level": {"type": "choice", "instructions": "What seniority does this role hire at?", "criteria": {
        "intern": "Internship, co-op, or summer program.",
        "entry": "Entry level: new grad, junior, associate, apprentice, tier 1, level I, or about 0-2 years asked.",
        "mid": "Mid-level: a few years of experience, level II, no senior title.",
        "senior": "Senior, lead, level III, or a manager of a team.",
        "staff": "Staff, principal, distinguished, or senior architect.",
        "executive": "Director, VP, head of, or C-level."}},
    "clearance": {"type": "noul", "instructions": "Does this role require a US government security clearance?"},
}


# Bump when JEV_QUESTIONS changes meaning, so cached answers to the old
# questions are not read as answers to the new ones. v2 (2026-10-03): "eng"
# narrowed from all engineering to infrastructure next to security. v3
# (2026-10-04): "eng" takes IT support and help desk; level has "intern".
JEV_VERSION = "v3"


def jev_key(p: dict) -> str:
    raw = f"{JEV_VERSION}|{p['title']}|{p['company']}|{p['location']}".lower()
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def ask_jev(p: dict, key: str) -> dict | None:
    state = {"title": p["title"], "company": p["company"], "location": p["location"], "excerpt": p["desc"][:1500]}
    try:
        d = http_json("https://api.typesafe.ai/v1/systemone",
                      {"state": state, "model": "jev-latest", "questions": JEV_QUESTIONS},
                      headers={"Authorization": f"Bearer {key}"}, tries=2, timeout=60)
    except RuntimeError as e:
        return {"error": str(e).replace(key, "[key]")}
    a = d.get("answers") or {}
    track = (a.get("track") or {}).get("choice")
    level = (a.get("level") or {}).get("choice")
    clr = (a.get("clearance") or {}).get("noul")
    if track not in ("cyber", "eng", "neither") or level not in ("intern", "entry", "mid", "senior", "staff", "executive"):
        return {"error": "malformed answer"}
    return {"track": track, "level": level, "clearance": isinstance(clr, (int, float)) and clr >= 0.6,
            "tokens": (d.get("usage") or {}).get("input_tokens", 0)}


# ── The run ──────────────────────────────────────────────────────────────────

def norm(s: str) -> str:
    s = re.sub(r"\.(com|io|ai|co)\b|\bweb services\b|\(aws\)", "", (s or "").lower())
    s = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|group|services)\b\.?", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def age_days(d: str | None) -> int | None:
    if not d:
        return None
    try:
        return (NOW.date() - datetime.fromisoformat(d).date()).days
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-jev", action="store_true")
    ap.add_argument("--dump", help="also write the page JSON to this path (testing; works with --dry-run)")
    ap.add_argument("--only", help="comma list of source ids or families (greenhouse, workday, foorilla, ...)")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None

    prev = {}
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prev = {}
    prev_jobs: dict = prev.get("jobs", {})
    jev_cache: dict = prev.get("jev", {})

    sources = all_sources(only)
    stats: dict[str, dict] = {}
    raw: list[tuple[str, dict]] = []

    def run(src):
        sid, label, fn, a = src
        t0 = time.time()
        try:
            rows = fn(*a)
            return sid, label, rows, None, time.time() - t0
        except Exception as e:  # noqa: BLE001 - one source never fails the run
            return sid, label, [], str(e)[:200], time.time() - t0

    # Foorilla and Amazon pace themselves; the boards are separate hosts.
    with ThreadPoolExecutor(8) as ex:
        for sid, label, rows, err, secs in ex.map(run, sources):
            stats[sid] = {"label": label, "ok": err is None, "fetched": len(rows), "kept": 0,
                          "error": err, "secs": round(secs, 1)}
            raw += [(sid, r) for r in rows]
            print(f"{sid:32} {'OK ' if err is None else 'ERR'} {len(rows):6} {err or ''}", file=sys.stderr)

    ok = sum(1 for s in stats.values() if s["ok"])
    if not only and ok < len(stats) / 2:
        print(f"FAIL CLOSED: only {ok}/{len(stats)} sources answered; leaving the page as it was", file=sys.stderr)
        return 2

    # Rules first: cheap, and they decide everything Jev does not.
    cands: dict[str, dict] = {}
    for sid, p in raw:
        if not p["title"] or not p["url"] or not p["company"] or AGENCY.search(p["company"]):
            continue
        w = where(p["location"], p["remote"])
        if not w:
            continue
        a = age_days(p["posted"])
        if a is not None and a > (REMOTE_MAX_AGE_DAYS if w == "remote" else MAX_AGE_DAYS):
            continue
        track = rule_track(p["title"], p["sec_topic"])
        level = rule_level(p["title"], p["hint"])
        if not track or not level:
            continue
        p.update(where=w, track=track, level=level, source=sid,
                 clearance=bool(CLEARANCE.search(p["title"] + " " + p["desc"][:600])))
        k = f"{norm(p['company'])}|{norm(p['title'])}|{w}"
        old = cands.get(k)
        # One row per role: the company's own board beats an aggregator's copy.
        if not old or (old["origin"] != "company" and p["origin"] == "company"):
            if old and not p["posted"]:
                p["posted"] = old["posted"]
            cands[k] = p

    # Jev refines track and level on postings it has not seen.
    jev_stats = {"asked": 0, "failed": 0, "cached": 0, "dropped": 0, "tokens": 0, "error": None}
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if args.no_jev or not key:
        jev_stats["error"] = "skipped" if args.no_jev else "no TYPESAFE_API_KEY"
    else:
        todo = [p for p in cands.values() if jev_key(p) not in jev_cache][:JEV_MAX_PER_RUN]
        jev_stats["cached"] = len(cands) - len(todo)
        with ThreadPoolExecutor(16) as ex:
            for p, ans in zip(todo, ex.map(lambda p: ask_jev(p, key), todo)):
                jev_stats["asked"] += 1
                if not ans or "error" in ans:
                    jev_stats["failed"] += 1
                    jev_stats["error"] = jev_stats["error"] or (ans or {}).get("error")
                    continue
                jev_stats["tokens"] += ans.pop("tokens", 0)
                jev_cache[jev_key(p)] = {**ans, "seen": TODAY}
    for k in list(cands):
        p = cands[k]
        ans = jev_cache.get(jev_key(p))
        if not ans:
            p["jev"] = False
            continue
        p["jev"] = True
        if ans["track"] == "neither" or ans["level"] in ("intern", "executive"):
            del cands[k]
            jev_stats["dropped"] += 1
            continue
        p["track"] = ans["track"]
        # A title that says its level ("Senior", "Staff", "Junior") keeps it; Jev settles the rest.
        if not (STAFF.search(p["title"]) or SENIOR.search(p["title"]) or ENTRY.search(p["title"])):
            p["level"] = ans["level"]
        p["clearance"] = p["clearance"] or ans["clearance"]

    # Assemble: today's postings, plus a failed source's postings within grace.
    jobs: dict[str, dict] = {}
    keep_jev: set[str] = set()
    for k, p in cands.items():
        jid = hashlib.sha1(k.encode()).hexdigest()[:12]
        old = prev_jobs.get(jid, {})
        jobs[jid] = {
            "id": jid, "title": p["title"], "company": p["company"], "url": p["url"],
            "location": p["location"][:160], "where": p["where"], "track": p["track"], "level": p["level"],
            "posted": p["posted"], "first_seen": old.get("first_seen", TODAY), "last_seen": TODAY,
            "salary": p["salary"], "source": stats[p["source"]]["label"],
            "via": p["origin"], "clearance": p["clearance"], "checked": p["jev"],
        }
        keep_jev.add(jev_key(p))
        stats[p["source"]]["kept"] += 1
    carried = 0
    for jid, j in prev_jobs.items():
        if jid in jobs:
            continue
        src_ok = next((s["ok"] for s in stats.values() if s["label"] == j.get("source")), True)
        if src_ok:
            continue  # the source answered and this job was not in it: removed
        if age_days(j.get("last_seen")) is not None and age_days(j["last_seen"]) <= GRACE_DAYS:
            jobs[jid] = {**j, "carried": True}
            carried += 1

    # Day-one guard against a wiped page. New jobs are first_seen today; on the
    # very first run everything is.
    rows = sorted(jobs.values(), key=lambda j: (j.get("posted") or j["first_seen"], j["first_seen"]), reverse=True)
    if not rows and not only:
        print("FAIL CLOSED: zero postings survived; leaving the page as it was", file=sys.stderr)
        return 3

    page = {
        "generated_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date": TODAY,
        "rules": {"max_age_days": MAX_AGE_DAYS, "remote_max_age_days": REMOTE_MAX_AGE_DAYS,
                  "grace_days": GRACE_DAYS},
        "counts": {"total": len(rows), "new_today": sum(1 for j in rows if j["first_seen"] == TODAY),
                   "dropped_since_last_run": len(set(prev_jobs) - set(jobs)), "carried": carried,
                   "cyber": sum(1 for j in rows if j["track"] == "cyber"),
                   "entry": sum(1 for j in rows if j["level"] == "entry"),
                   "eng": sum(1 for j in rows if j["track"] == "eng"),
                   "pnw": sum(1 for j in rows if j["where"] == "pnw"),
                   "remote": sum(1 for j in rows if j["where"] == "remote")},
        "sources": [{"id": sid, **{k: v for k, v in s.items() if k != "secs"}} for sid, s in sorted(stats.items())],
        "jev": jev_stats,
        "jobs": rows,
    }
    print(json.dumps({k: page[k] for k in ("counts", "jev")}, indent=1), file=sys.stderr)
    if args.dump:
        Path(args.dump).write_text(json.dumps(page, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.dry_run or only:
        print("dry run / partial run: nothing written", file=sys.stderr)
        return 0

    # Prune the Jev cache to what is still useful, then write both atomically.
    # Answers for dropped postings are kept a week, so a "neither" is not re-asked daily.
    jev_cache = {k: v for k, v in jev_cache.items() if k in keep_jev or (age_days(v.get("seen")) or 0) <= 7}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for path, obj in ((STATE, {"jobs": jobs, "jev": jev_cache}), (OUT, page)):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    print(f"wrote {OUT} ({len(rows)} jobs)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
