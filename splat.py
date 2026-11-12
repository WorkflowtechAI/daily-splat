#!/usr/bin/env python3
"""The daily splat: cybersecurity, the infra and IT around it, the project,
program and product managers of technology work, and the sales engineers who sell
it, entry to senior, in the Pacific Northwest or remote from it. Built for the
job-hunt cabal.

Runs once a day on the Foundry VPS (infra/daily-splat). Each run:

  1. READS every source fresh. Company boards (Greenhouse, Lever, Ashby,
     Workday, Eightfold, Amazon) and the open aggregators (RemoteOK, Jobicy,
     Himalayas, Working Nomads) return what is open today; Foorilla
     (keyed) returns the last FOORILLA_DAYS days with an `expired` flag. An
     employer's own Ashby, Greenhouse or Lever board that the aggregators'
     cards link to is read directly too (auto_boards, from boards.json).
  2. FILTERS with rules: a title that reads as security, infra engineering, a
     technology project, program or product manager, or a sales engineer, a level
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
     ago drops regardless; an aggregator's copy of an Ashby, Greenhouse or
     Lever posting is dated by the employer's own board where company_check.py's
     census has it (boards.json).

It fails closed: if fewer than half the sources answer, it assumes the box lost
network, writes nothing, and exits non-zero. Yesterday's page stands.

Keys come from the environment (EnvironmentFile in the unit), never from this
file: FOORILLA_API_KEY, TYPESAFE_API_KEY, USAJOBS_API_KEY with USAJOBS_EMAIL (the
address the key was registered under). Any may be absent; that source or pass is
skipped and named in the run stats.

    python3 splat.py                # full run, writes jobs.json beside itself (or to $SPLAT_OUT)
    python3 splat.py --dry-run      # everything except the write
    python3 splat.py --only greenhouse,amazon --no-jev
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html import unescape
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The page's data: beside the script, or where the server says
# (SPLAT_OUT, set in the unit to the docroot; the repo is a clone elsewhere).
OUT = Path(os.environ.get("SPLAT_OUT") or HERE / "jobs.json")
STATE_DIR = Path(os.environ.get("STATE_DIRECTORY") or HERE / ".state")
STATE = STATE_DIR / "state.json"
# company_check.py's daily census of the Ashby, Greenhouse and Lever boards the
# cards link to (both units share StateDirectory=daily-splat). Read only, for
# the employer's own posting dates; see board_redate and rules_pass.
BOARDS = STATE_DIR / "boards.json"

NOW = datetime.now(timezone.utc)
_T0 = time.monotonic()  # the run's start, for RUN_BUDGET
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
    "opensesame": "OpenSesame", "airship": "Airship",
    # From the job source directory (iambraun.com/job-sources): MSP-ecosystem vendors.
    "connectwise": "ConnectWise", "kaseya": "Kaseya",
}
LEVER = {"palantir": "Palantir", "spotify": "Spotify", "outreach": "Outreach", "shieldai": "Shield AI",
         "smarsh": "Smarsh", "perforce": "Perforce", "instrument": "Instrument"}
ASHBY = {
    "snowflake": "Snowflake", "ramp": "Ramp", "openai": "OpenAI", "notion": "Notion",
    "supabase": "Supabase", "linear": "Linear", "semgrep": "Semgrep", "socket": "Socket",
    "vanta": "Vanta", "drata": "Drata", "cohere": "Cohere", "replit": "Replit",
    "1password": "1Password", "sentry": "Sentry", "zip": "Zip", "decagon": "Decagon",
    "sierra": "Sierra", "harvey": "Harvey", "persona": "Persona",
    # 2026-11-12: Vultr's roles reached the Splat only through Foorilla, some with a
    # later date than its own board's (a March posting shown as Nov 6).
    "vultr": "Vultr",
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
    # From the job source directory: MSP-ecosystem vendors.
    ("pax8inc", "wd12", "pax8careers", "Pax8"),
    ("acronis", "wd502", "acronis_careers", "Acronis"),
]
# Oregon employers, each answered with an Oregon location facet on 2026-10-21.
# Read in 'local' mode: every job at their PNW locations, no keyword passes.
# A tenant's site names are listed in https://{tenant}.{wdN}.myworkdayjobs.com/robots.txt.
WORKDAY_LOCAL = [
    ("columbiasportswearcompany", "wd5", "CSC_Careers", "Columbia Sportswear"),
    ("dtna", "wd5", "DTNA_external", "Daimler Truck North America"),
    ("standard", "wd1", "Search", "The Standard"),
    ("cambiahealth", "wd504", "External", "Cambia Health Solutions"),
    ("portlandgeneral", "wd5", "pgn", "Portland General Electric"),
    ("columbiabank", "wd108", "Columbia", "Columbia Bank"),
    ("latticesemi", "wd5", "latticesemiconductorscareers", "Lattice Semiconductor"),
    ("flir", "wd1", "flircareers", "Teledyne FLIR"),
    ("oregonstate", "wd501", "OSU_Careers_Site", "Oregon State University"),
    ("sou", "wd1", "Southern_Oregon_University", "Southern Oregon University"),
    ("portlandcc", "wd12", "External", "Portland Community College"),
    ("stcharles", "wd1", "External", "St. Charles Health System"),
    ("pacificsource", "wd5", "PacificSource", "PacificSource Health Plans"),
    ("careoregon", "wd12", "CO", "CareOregon"),
    ("lithia", "wd504", "LithiaCareers", "Lithia Motors"),
    ("iberdrola", "wd3", "Iberdrola", "Avangrid"),
    ("astound", "wd108", "Astound_Careers", "Astound Broadband"),
    ("hp", "wd5", "ExternalCareerSite", "HP"),
    ("nvidia", "wd5", "NVIDIAExternalCareerSite", "NVIDIA"),
    ("analogdevices", "wd1", "External", "Analog Devices"),
    ("cisco", "wd5", "Cisco_Careers", "Cisco"),
]
# Tenants whose every job is in Oregon and whose location facets name agencies,
# not places: searched without a location facet, every row placed in Oregon.
WORKDAY_ALL_OREGON = [
    ("oregon", "wd5", "SOR_External_Career_Site", "State of Oregon"),
    ("multco", "wd1", "Multco_Jobs", "Multnomah County"),
]

# ── Classification rules ─────────────────────────────────────────────────────

CYBER = re.compile(
    r"secur|cyber|infosec|appsec|devsecops|secops|\bsoc\b|threat|incident response|detection|"
    r"privacy|\bisso\b|\bissm\b|"
    r"vulnerab|penetration|pen ?test|red team|blue team|purple team|malware|forensic|\bgrc\b|"
    r"governance,? risk|identity (and|&) access|\biam\b|zero trust|cryptograph|privacy engineer|"
    r"\bsiem\b|offensive|defensive|trust (and|&) safety engineer|information (risk|assurance)", re.I)
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
    r"system administrator|systems administrator|field it|end.user (support|computing)|"
    # Public-sector class titles (State of Oregon, 2026-10-21): "Information Systems
    # Specialist 6", "Information Technology Manager 2", "Senior IT Oversight Analyst".
    r"information (systems?|technology)|\bdatabase\b|\bdba\b|backup|\bit oversight", re.I)
# "engine+r": Columbia Bank posted a "Senior Network Engineeer" on 2026-10-21.
ENG_ROLE = re.compile(r"engine+r|architect|administrator|\badmin\b|\bsre\b|devops|specialist|operations|"
                      r"support|technician|\bdesk\b|analyst|associate|\bmanager\b", re.I)
NOT_ROLE = re.compile(  # wrong job family for this page, whatever the keywords
    r"\bsales\b(?! engineer)|account executive|account manager|business development|marketing|"
    r"recruit|talent|counsel|attorney|paralegal|legal|customer success manager|\bsdr\b|\bbdr\b|"
    r"security guard|(?<!information )(?<!systems )(?<!system )security officer(?!.*\bisso\b)|physical security|loss prevention|securities|social security|"
    # Project, program and product managers are their own track since 2026-11-03 (PM_ROLE below).
    r"sourcing|procurement|designer|"
    r"\bux\b|writer|scrum|data analyst|business analyst|"
    r"mechanical|electrical|civil|structural|chemical|manufacturing|process engineer|field service|"
    r"maintenance|facilities|hvac|construction|industrial|environmental|geotechnical|biomedical|"
    r"packaging|physical design|\basic\b|\brtl\b|analog|mixed.signal|\bpcb\b|\brf engineer|"
    # "technician" alone is not here: IT and data-center technicians are entry roles.
    r"(lab|automotive|surgical|veterinary|dental|wind|solar|fiber|cable|install\w*) technician|electrician|"
    r"customer (support|service|care|experience)(?!.*engineer)|call center|"
    r"nurse|clinical|pharmac|driver|warehouse|\brn\b|dispatcher|"
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
# Federal and defense contractors. Many of their roles need a clearance the title
# never mentions (2026-11-07: 0 of 14 Peraton rows flagged, its "SOC Analyst"
# wants an active Secret), so their rows say "Check clearance".
FEDERAL = re.compile(
    r"peraton|leidos|\bsaic\b|science applications|booz allen|\bcaci\b|mantech|general dynamics|\bgdit\b|"
    r"northrop|lockheed|raytheon|\brtx\b|\bbae systems\b|l3harris|parsons corp|\bkbr\b|amentum|\bv2x\b|\bmitre\b|"
    r"accenture federal|^ecs\b|asm research|empower ai|decisionpoint|computer world services|xcelerate solutions|"
    r"anduril|shield ai|huntington ingalls|sierra nevada corp|general atomics|noblis|battelle", re.I)

# Oregon is the page's home (2026-10-21: 18 of 252 PNW rows were Oregon, all
# Amazon, Intel or Nike), so it gets its own bucket. Vancouver, Camas and
# Washougal are Portland metro and count with it. Whole words only: on
# 2026-11-07 "Jerusalem" read as Salem and "Trenton, NJ" as Renton.
OR_CITY = re.compile(
    r"\b(?:portland|beaverton|hillsboro|tigard|tualatin|lake oswego|wilsonville|gresham|happy valley|"
    r"milwaukie|clackamas|oregon city|west linn|sherwood|forest grove|troutdale|vancouver,?\s*(wa|washington)|"
    r"camas|washougal|salem|eugene|springfield,?\s*(or|oregon)|corvallis|albany,?\s*(or|oregon)|bend|"
    r"redmond,?\s*(or|oregon)|medford|ashland,?\s*(or|oregon)|klamath falls|roseburg|grants pass|"
    r"the dalles|hood river|prineville|hermiston|umatilla|boardman|pendleton|astoria|newport,?\s*(or|oregon))\b", re.I)
PNW_CITY = re.compile(
    r"\b(?:seattle|bellevue|redmond|kirkland|bothell|everett|tacoma|olympia|spokane|renton|issaquah|"
    r"lynnwood|tukwila|sammamish|woodinville|mukilteo|bellingham|richland|kennewick|pullman|"
    r"boise|meridian|nampa|coeur d.alene|idaho falls|puget sound)\b", re.I)
PNW_STATE = re.compile(r"\bwashington\b|\boregon\b|\bidaho\b", re.I)
OR_STATE = re.compile(r"\boregon\b", re.I)
# Only the shapes a state code actually takes in a location: ", WA", "US-WA",
# "WA 98101". A bare " OR " is the English word ("NY OR US Remote" was read as
# Oregon on 2026-10-03). Case-sensitive on purpose.
PNW_ABBR = re.compile(r"(?:,\s*|\bUSA?\s*-\s*|\bUSA?-)(WA|OR|ID)\b|\b(WA|OR|ID)\s+\d{5}\b")
OR_ABBR = re.compile(r"(?:,\s*|\bUSA?\s*-\s*|\bUSA?-)OR\b|\bOR\s+\d{5}\b")
# A segment that names DC, or Washington as a street, is not Washington State.
NOT_WA_STATE = re.compile(r"\bd\.?\s?c\.?\b|district of columbia|washington\s+(st|street|ave|avenue|blvd|rd|road)\b", re.I)
# Same-named cities elsewhere.
NOT_PNW_CITY = re.compile(r"portland,?\s*(me\b|maine)|meridian,?\s*(ms\b|mississippi)|everett,?\s*(ma\b|mass)|"
                          r"bellevue,?\s*(ne\b|nebraska)|richland,?\s*(ms\b|mississippi)|"
                          r"salem,?\s*(ma\b|mass|nh\b|new hampshire|va\b|virginia)|medford,?\s*(ma\b|mass|nj\b|ny\b)|"
                          r"oregon,?\s*(wi\b|wisconsin|il\b|illinois|oh\b|ohio|mo\b|missouri)|"
                          r"camp pendleton|richland county", re.I)
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
    """'oregon', 'pnw' (WA/ID), 'remote', or None. Local wins: a PNW hybrid that
    also allows remote is local, and a role listed in Seattle and Portland is Oregon's."""
    loc = location or ""
    # One segment at a time: "Seattle, WA | Washington, DC" is PNW by its first
    # segment, and the DC in the second must not veto it.
    pnw = False
    for seg in re.split(r"[;|\n]", loc):
        if NOT_PNW_CITY.search(seg):
            continue
        # A city name in a segment that names another country is a namesake
        # abroad. A state name or code still counts.
        city = not NOT_US.search(seg)
        if (city and OR_CITY.search(seg)) or OR_ABBR.search(seg) or OR_STATE.search(seg):
            return "oregon"
        if (city and PNW_CITY.search(seg)) or PNW_ABBR.search(seg):
            pnw = True
        elif PNW_STATE.search(seg) and not NOT_WA_STATE.search(seg):
            pnw = True
    if pnw:
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


# Providence tags its IT department's roles "IS" (Information Services):
# "IS Customer Support Technician - On Site" is help desk, not customer service.
# Case-sensitive, so the English word "is" never counts.
IS_DEPT = re.compile(r"(?:^|\s)IS(?=\s|$|,)")

# The PM track (2026-11-03, asked for by the group): project, program and
# technical program managers, project coordinators and PMO roles, and product
# managers and owners, when the work is technology, IT or security. Scrum masters
# and agile coaches were not asked for and stay out (NOT_ROLE has "scrum"). Delivery
# managers are not part of it; an infrastructure delivery manager still reads as
# Infra & IT, as it did before.
# The rules only propose a PM row; Jev confirms it (main() publishes no pm row
# Jev has not answered). Three review rounds on 2026-11-03 showed no word list
# tells a technology PM from a public-works, utility or care PM every time:
# "engineering", "systems", "infrastructure" and "security" all mean both. Jev's
# question names the PMs that are out. The rules still matter: a title wrongly
# read as a PM is taken from cyber or eng, and every candidate costs a Jev call.
PM_ROLE = re.compile(r"\b(project|program|programme|product)\s+(manager|mgr|lead|owner|coordinator)s?\b", re.I)
PRODUCT = re.compile(r"\bproduct\s+(manager|mgr|lead|owner|management)s?\b", re.I)
# A team name or an abbreviation is the job only when no other job noun sits
# beside it. For a team name that means another discipline ("Software Engineer,
# Project Management Tools"); PMO analysts and administrators are PMO roles. For
# "PM"/"TPM" any other job noun means it is not the job ("TPM Security Consultant").
# Whole words: "Engineering" and "Developer Platform" are teams a TPM works in.
PM_TEAM = re.compile(r"\b(project|program|programme|product)\s+management\b|\bpmo\b", re.I)
PM_ABBR = re.compile(r"\bT?PMs?\b")  # case-sensitive; strips the abbreviation for level_text
# The abbreviation is the job only where a job title starts: "Sr. TPM, Cloud",
# "PM, Identity", "Security TPM", Amazon's "PM-T". Elsewhere it is a shift ("SOC
# Supervisor - PM") or a module ("SAP PM/EAM Functional Lead").
PM_ABBR_LEAD = re.compile(r"^\W*((?i:sr\.?|senior|lead|principal|staff|group|associate|technical|it|security|cloud|"
                          r"data|platform|infrastructure|identity|network|ai|ml|software|hardware|systems|enterprise|"
                          r"product|program)\s+)*T?PMs?\b")
NOT_PM_TEAM = re.compile(r"\b(engineers?|developers?|architects?|researchers?|scientists?|technicians?|designers?|"
                         r"systems? administrators?|sysadmins?|writers?|assistants?|business partners?)\b"
                         r"(?!\s+(platform|infrastructure|tools|experience|productivity))", re.I)
NOT_PM_ABBR = re.compile(NOT_PM_TEAM.pattern + r"|\b(analysts?|administrators?|specialists?|associates?|agents?|"
                         r"operators?|consultants?|coordinators?|support|tier\s*\d|desk|officers?|supervisors?|"
                         r"guards?|facilitators?)\b", re.I)
# "TPM" the security chip, or Total Productive Maintenance.
TPM_CHIP = re.compile(r"pluton|firmware|secure boot|attestation|\bhsm\b|trusted platform|total productive|"
                      r"maintenance|\bchip|silicon", re.I)
# Shift hours are not project managers: "SOC Analyst (3 PM - 11 PM)", "2:30PM",
# "AM/PM", "AM & PM", "PM Shift", "(PM)", "Days/PM", "1800-0600 PM" (the 2026-11-03
# reviews). Stripped before any PM test.
CLOCK = re.compile(r"\b\d{1,4}(:\d{2})?\s*[AP]\.?M\b\.?|\bAM\s*[/&,-]\s*PM\b|\bPM\s*[/&,-]\s*AM\b|"
                   r"\b(swing|days?|nights?|evenings?|weekends?)\s*/\s*PM\b|"
                   r"\bPM\s*/\s*(swing|noc|days?|nights?|evenings?|weekends?)\b|\bPM\s+shifts?\b|\(\s*PM\s*\)", re.I)

# Three tiers decide a PM title (after the reviews of 2026-11-03):
# 1. PM_HARD_NOT: never technology PMs, whatever else the title says.
PM_HARD_NOT = re.compile(r"construction|facilit(y|ies)|marketing|\bsales\b|\blegal\b|counsel|attorney|paralegal|"
                         r"recruit|talent acquisition|scrum|agile coach|physical security|(?<!data )loss prevention|"
                         r"security guard|security officer|executive protection|fire alarm|\bcctv\b|"
                         r"(electronic|aviation|airport|transit|port|campus|corporate|event|hospital) security|"
                         r"\bcivil\b|structural|hvac|architectural|landscap", re.I)
# 2. PM_CARE: patient-care PMs, out unless a health-IT word (PM_STRONG) says the
#    job is the systems: "Clinical Applications Project Manager", "Nursing
#    Informatics Project Manager", "Epic Project Manager (RN preferred)".
PM_CARE = re.compile(r"clinical|nurs(e|es|ing)|pharmac|\brn\b|patient care|surgical|dental|behavioral health|"
                     r"mental health", re.I)
PM_STRONG = re.compile(r"information (technology|systems|services|security)|clinical (applications?|apps|systems?|informatics)|"
                       r"technical (program|project|product)|"  # a TPM is technical by definition
                       r"\b(ehr|emr|epic|cerner|meditech|informatics|eclinicalworks|allscripts|athenahealth|"
                       r"software|cyber\w*|cloud|sap|servicenow|erp)\b", re.I)
IT_ABBR = re.compile(r"\bIT\b|\bPM-T\b|\bTPMs?\b")
# 3. The rest of NOT_ROLE (manufacturing, warehouse, field service, electrical...)
#    gives way to any technology word: "Industrial Cybersecurity Program Manager",
#    "IT Project Manager / Business Analyst", "Cloud Data Warehouse Program Manager".
# Technology words. Specific phrases, not CYBER's and INFRA's stems, which in a PM
# title catch "Food Insecurity", "Early Detection", "Knowledge Management" and
# "Windows & Doors" (the second review).
PM_TECH = re.compile(
    r"\b(technical(?!\s+assistance)|technology|technologies|information (technology|systems?|security|services)|"
    r"software|cyber\w*|infosec|appsec|devsecops|secops|soc|siem|"
    r"soar|iam|pam|grc|mfa|sso|dlp|edr|xdr|mdr|zero[- ]?trust|vulnerabilit\w*|vuln|penetration|pen ?test\w*|"
    r"red team|incident response|threat (intel\w*|detection|hunting|modeling)|insider threat|"
    r"detection (and|&) response|data loss prevention|identity (and|&) access|"
    r"identity (management|governance|platform|security|services|engineering)|privacy|isso|issm|fedramp|cmmc|"
    r"nist|okta|sailpoint|cloud|(?<!green )(?<!water )(?<!charging )(?<!civil )infrastructure|platforms?|data|"
    r"(?<!provider )network(s|ing)?(?!\s+(development|adequacy|contracting|relations))|systems?|digital|"
    # Engineering only as a software discipline or an EPM, not "Electrical
    # Engineering Project Manager" or a public-works "Engineering Project Manager".
    r"(software|platform|cloud|security|data|infrastructure|systems|network|reliability|production|developer|"
    r"machine learning|ml|ai) engineering|engineering program (manag\w*|lead)|"
    r"devops|sre|site reliability|saas|apis?|ai|ml|erp|crm|sap|servicenow|salesforce|workday|"
    r"(enterprise|business|software|web|mobile|cloud|clinical) (applications?|apps)|apps|noc|data cent(er|re)s?|"
    r"kubernetes|linux|active directory|endpoint|servers?|(?<!energy )(?<!battery )storage|virtualization|vmware|"
    r"observability|help ?desk|service desk|desktop|end.user|telecom\w*|voip|firewalls?|disaster recovery|ehr|emr|"
    r"epic|cerner|meditech|informatics|eclinicalworks|allscripts|athenahealth|oracle health|web)\b|"
    r"(?<!food )(?<!water )(?<!income )(?<!social )(?<!housing )(?<!economic )(?<!physical )(?<!homeland )"
    r"(?<!financial )(?<!energy )(?<!retirement )(?<!nutrition )(?<!employment )\bsecurity\b|"
    r"[-,]\s*tech\b", re.I)  # Amazon's "Sr. Product Manager - Tech"
# A program or project manager in operations is not technology work, even at a
# technology employer (Intel's supply chain, Amazon's grocery logistics). Product
# managers are exempt: Zillow's "Product Manager, Real Estate Transactions" builds software.
PM_NOT_TECH = re.compile(r"supply chain|logistics|grocery|retail|merchandis|apparel|footwear|real estate|workplace|"
                         r"health (and|&) safety|workforce management|labor and scheduling|perishable|fulfillment|"
                         r"warehouse|transportation", re.I)


def is_pm(title: str) -> bool:
    t = CLOCK.sub(" ", title)
    if PM_ROLE.search(t):
        return True
    if PM_TEAM.search(t) and not NOT_PM_TEAM.search(t):
        return True
    return bool(PM_ABBR_LEAD.search(t) and not NOT_PM_ABBR.search(t) and not TPM_CHIP.search(t))


def pm_track(title: str, tech_employer: bool = False, sec_topic: bool = False) -> bool:
    """A project, program or product manager of technology work. sec_topic: filed
    under Foorilla's infosec topics, so a security PM whatever the title says."""
    if not is_pm(title):
        return False
    t = CLOCK.sub(" ", title)
    if PM_HARD_NOT.search(t):
        return False
    strong = bool(PM_STRONG.search(t) or IT_ABBR.search(t) or IS_DEPT.search(t))
    if PM_CARE.search(t) and not strong:
        return False
    tech = strong or bool(PM_TECH.search(t))
    if NOT_ROLE.search(PM_CARE.sub(" ", t)) and not tech:
        return False
    if not PRODUCT.search(t) and PM_NOT_TECH.search(t) and not strong:
        return False
    return tech or sec_topic or tech_employer


# The SE track (2026-11-10, David, for a laid-off infosec sales engineer in the
# group): sales engineers and the other technical pre-sales roles, when what they
# sell is technology. Before it, "Sales Engineer" matched no rule and no source
# searched for it; the few solutions engineers and architects that got in were
# filed under Cyber or Infra & IT.
# As with the PM track, the rules only propose an se row and Jev confirms it:
# "Sales Engineer" is as often valves, HVAC or trucks as software, and the title
# rarely says which.
SE_ROLE = re.compile(r"\bsales\s+engineer(s|ing)?\b|\bpre-?\s?sales\b|\bsolutions?\s+(engineer(s|ing)?|consultants?)\b|"
                     r"\btechnical\s+sales\s+(engineer|specialist|consultant|architect|lead)s?\b|"
                     r"\b(value|demo)\s+engineers?\b", re.I)
# A solutions architect sells at a technology vendor (AWS, NVIDIA, Microsoft), and
# designs the employer's own systems elsewhere ("Enterprise Solutions Architect"
# at a bank), so it is an se row only on a vendor's own board, under Foorilla's
# infosec topics, or when the title names the sale. AWS writes "SA" too
# ("Senior Networking SA"); case-sensitive.
SE_ARCH = re.compile(r"\bsolutions?\s+architect", re.I)
SA_ABBR = re.compile(r"\bSAs?\b")
SE_SALES = re.compile(r"\bpre-?\s?sales|\bsales\b|\bpartners?\b|\bchannel\b|\bfield\b|\bcustomers?\b|\baccounts?\b|"
                      r"\bclients?\b|\bspecialist\b", re.I)
# Google's title for its sales engineers. Elsewhere it is often field service, so
# the title or the board has to say technology.
CUSTOMER_ENG = re.compile(r"\bcustomer\s+engineers?\b", re.I)
# Titles Jev may move into se, from eng or cyber: the above, and the vendors'
# own names for a sales engineer (Palo Alto and Fortinet hire "Systems Engineers"
# and Palo Alto's specialists are "Domain Consultants", Databricks "Field Engineers").
SE_LOOSE = re.compile(SE_ROLE.pattern + r"|\bsolutions?\s+architect|\b(systems|field|channel|partner)\s+engineers?\b|"
                      r"\bdomain\s+consultants?\b|" + CUSTOMER_ENG.pattern, re.I)
# Never a technology sales engineer, whatever else the title says.
SE_HARD_NOT = re.compile(r"recruit|talent acquisition|\bsourcer\b|counsel|attorney|paralegal|\blegal\b|construction|"
                         r"facilit(y|ies)|\bhvac\b|plumbing|roofing|elevator|physical security|security guard|"
                         r"fire (alarm|protection)|\bcctv\b", re.I)


def se_title(title: str) -> bool:
    """A title Jev may file as a sales engineer (see SE_LOOSE)."""
    return bool(SE_LOOSE.search(title) or SA_ABBR.search(title))


def se_track(title: str, sa_employer: bool = False, sec_topic: bool = False) -> bool:
    """A sales engineer, solutions engineer or consultant, pre-sales role, or a
    vendor's solutions architect or customer engineer, of technology. sa_employer:
    the posting is on a board whose solutions architects sell (a technology
    employer's own, or Amazon's: AWS). Jev has the final say on every se row."""
    role, arch = SE_ROLE.search(title), SE_ARCH.search(title) or SA_ABBR.search(title)
    cust = CUSTOMER_ENG.search(title)
    if not (role or arch or cust) or SE_HARD_NOT.search(title):
        return False
    # "Technical" in "Technical Sales Engineer" is the job's name, not what it sells
    # ("Technical Sales Engineer - Industrial Valves", the branch review).
    tech = bool(PM_TECH.search(re.sub(r"\btechnical\s+sales\b", " ", title, flags=re.I)))
    # The rest of NOT_ROLE (mechanical, industrial, manufacturing...) gives way to a
    # technology word, as for a PM: "Industrial Cybersecurity Sales Engineer" counts.
    # The sales words themselves are not the wrong job family here.
    if NOT_ROLE.search(SE_ROLE.sub(" ", title)) and not tech:
        return False
    if role:
        return True
    if arch:
        return sa_employer or sec_topic or bool(SE_SALES.search(SE_ARCH.sub(" ", title)))
    return tech or sa_employer or sec_topic


def level_text(title: str, track: str | None) -> str:
    """The title as the level rules should read it. In a PM title "Manager" is
    the job, not a grade: "Product Manager" is mid, "Senior Product Manager" senior.
    In an SE title "Architect" is the job too: AWS hires "Solutions Architects"
    below its "Senior Solutions Architects", and a "Pre-Sales Architect" is not
    senior for the word."""
    if track == "se":
        return SA_ABBR.sub(" ", re.sub(r"architect", " ", title, flags=re.I))
    if track != "pm":
        return title
    t = CLOCK.sub(" ", title)
    return PM_ABBR.sub(" ", PM_TEAM.sub(" ", PM_ROLE.sub(" ", t)))


# Rows the rules only propose and Jev must confirm before they are published.
CONFIRM = ("pm", "se")


def settle_level(title: str, rule_track_: str, rule_level_: str, jev_track: str, jev_level: str) -> str:
    """Jev's level, unless the title states its level. Whether "Manager" states a
    level depends on the job: if the rules or Jev call it a PM, "Manager" is the
    job, so a "Technical Program Manager" the rules filed pm/mid and Jev calls
    eng/senior is senior, and a "Product Manager" filed cyber/senior that Jev
    calls pm/mid is mid. "Solutions Architect" likewise, when either calls it se."""
    pair = (rule_track_, jev_track)
    lt = level_text(title, "pm" if "pm" in pair else "se" if "se" in pair else rule_track_)
    return rule_level_ if (STAFF.search(lt) or SENIOR.search(lt) or ENTRY.search(lt)) else jev_level


def apply_jev(p: dict, ans: dict | None) -> bool:
    """Fold Jev's answer (None: none yet) into a candidate. False: not for the page
    today, because Jev says so, or because it is a pm or se row Jev has not
    confirmed (see PM_ROLE and SE_ROLE); that row is asked again tomorrow."""
    p["jev"] = ans is not None
    if ans is None:
        return p["track"] not in CONFIRM
    if ans["track"] == "neither" or ans["level"] in ("intern", "executive"):
        return False
    # Jev may move a row into pm only when the title is a PM job: in the audit of
    # 2026-11-03 it filed "Cloud Architect, Product Management" and "Robotics
    # Systems Engineer III" there. Into se, likewise, only a sales-engineer title.
    # Otherwise the rules' track stands.
    track = ans["track"]
    if (track == "pm" and not is_pm(p["title"])) or (track == "se" and not se_title(p["title"])):
        track = p["track"]
    if track in CONFIRM and track != ans["track"]:
        return False  # the rules' pm or se, which Jev did not confirm (it said the other one)
    # A title that says its level ("Senior", "Staff", "Junior") keeps it; Jev settles the rest.
    p["level"] = settle_level(p["title"], p["track"], p["level"], track, ans["level"])
    p["track"] = track
    p["clearance"] = p["clearance"] or ans["clearance"]
    return True


def rule_track(title: str, sec_topic: bool = False, tech_employer: bool = False,
               sa_employer: bool | None = None, se: bool = True) -> str | None:
    """sec_topic: the source already filed it under infosec (Foorilla topics 102/26).
    Then a role-shaped title is enough ("Security Engineer III" at a security
    vendor), and Jev has the final say. tech_employer: the posting is on a
    technology company's own board, so a PM there is tech work (see tech_source).
    A PM title that is not technology work is out, as every PM title was before
    the PM track. sa_employer (default: tech_employer): its solutions architects
    sell (see se_track); a sales engineer comes before the wrong-family words,
    which name sales. se=False: the track the title would have without the SE
    track, for a row whose posting says it is not pre-sales (not_presales)."""
    if is_pm(title):
        return "pm" if pm_track(title, tech_employer, sec_topic) else None
    if se and se_track(title, tech_employer if sa_employer is None else sa_employer, sec_topic):
        return "se"
    if IS_DEPT.search(title) and ENG_ROLE.search(title) and not DATA_ML.search(title):
        return "cyber" if CYBER.search(title) else "eng"
    if NOT_ROLE.search(title):
        return None
    if CYBER.search(title) or (sec_topic and SEC_ROLE.search(title)):
        return "cyber"
    if INFRA.search(title) and ENG_ROLE.search(title) and not DATA_ML.search(title):
        return "eng"
    return None


def rule_level(title: str, hint: str | None = None, track: str | None = None) -> str | None:
    """'entry' | 'mid' | 'senior' | 'staff' | None (intern or executive: out)."""
    if INTERN.search(title) or TOO_SENIOR.search(title) or hint == "EX":
        return None
    title = level_text(title, track)
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
    return json.loads(http_text(url, data, headers, tries, timeout))


# A fetch with a deadline: every socket it opens (redirect hops too) is recorded
# for this thread, and a timer shuts them all down when the deadline passes. That
# bounds what a per-read timeout cannot: headers or chunk framing dripped a byte at
# a time, which keeps each read under the socket timeout forever (review, 2026-11-09).
_FETCH = threading.local()


def _kill(sock) -> None:
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)  # the base call, under any TLS layer
    except OSError:
        pass


@contextlib.contextmanager
def fetch_deadline(seconds: float):
    socks: list = []
    killed = [False]
    prev = getattr(_FETCH, "state", None)
    _FETCH.state = (socks, killed)

    def fire():
        killed[0] = True
        for s in list(socks):
            _kill(s)

    t = threading.Timer(seconds, fire)
    t.daemon = True
    t.start()
    try:
        yield killed
    finally:
        t.cancel()
        _FETCH.state = prev


def _track(sock) -> None:
    state = getattr(_FETCH, "state", None)
    if state:
        state[0].append(sock)
        if state[1][0]:
            _kill(sock)


class _TrackedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        _track(self.sock)


class _TrackedHTTP(http.client.HTTPConnection):  # host_get never uses http; the tests do
    def connect(self):
        super().connect()
        _track(self.sock)


class _TrackedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_TrackedHTTPS, req, context=self._context)


class _TrackedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_TrackedHTTP, req)


def http_text(url: str, data: dict | None = None, headers: dict | None = None, tries: int = 3, timeout: int = 40,
              limit: int | None = None, opener=None, deadline: float | None = None) -> str:
    """deadline: seconds for the whole request, from connecting to the last byte
    (see fetch_deadline); the body is read as it arrives (read1)."""
    body = json.dumps(data).encode() if data is not None else None
    h = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    if body is not None:
        h["Content-Type"] = "application/json"
    last: Exception | None = None
    for attempt in range(tries):
        with (fetch_deadline(deadline) if deadline else contextlib.nullcontext([False])) as killed:
            try:
                req = urllib.request.Request(url, data=body, headers=h)
                with (opener.open(req, timeout=timeout) if opener else urllib.request.urlopen(req, timeout=timeout)) as r:
                    if deadline is None:
                        return (r.read(limit) if limit else r.read()).decode("utf-8", "replace")
                    buf, size = [], 0
                    while not limit or size < limit:
                        chunk = r.read1(65536)
                        if not chunk:
                            break
                        buf.append(chunk)
                        size += len(chunk)
                    if killed[0]:
                        raise TimeoutError(f"no complete answer in {deadline:.0f}s")
                    return b"".join(buf)[:limit].decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (404, 410, 401, 403, 422):
                    break  # retrying will not change the answer
            except Exception as e:  # noqa: BLE001 - network flakiness of every shape
                last = TimeoutError(f"no complete answer in {deadline:.0f}s") if killed[0] else e
        if attempt + 1 < tries:
            time.sleep(2 ** (attempt + 1))  # never after the last try: nothing follows it
    raise RuntimeError(f"{type(last).__name__}: {str(last)[:120]}")


# Tag patterns are [^<>]* and every input is cut before any pattern runs: a hostile
# page of "<script " or "<" repeated made [^>]+ quadratic (hours at 3 MB, review 2026-11-09).
TAG = re.compile(r"<[^<>]*>")


def text(html: str | None, n: int = 1500) -> str:
    s = TAG.sub(" ", unescape((html or "")[:200_000]))
    return re.sub(r"\s+", " ", s).strip()[:n]


BODY_MAX = 12000
BLOCK = re.compile(r"<\s*(?:br|/?p|/?li|/?h[1-6]|/div|/tr|/?ul|/?ol|/dd|/dt)\b[^<>]*>", re.I)


def body_text(html: str | None, n: int = BODY_MAX) -> str:
    """A posting's HTML as plain text that keeps its line breaks, so sentences and
    bullets can be picked out one by one. Greenhouse escapes its HTML twice. All
    whitespace but the newline becomes one space (Unicode runs made the year and
    day patterns backtrack)."""
    s = unescape((html or "")[:300_000])
    if "&lt;" in s:
        s = unescape(s)
    s = unescape(TAG.sub(" ", BLOCK.sub("\n", s)))
    lines = (re.sub(r"[^\S\n]+", " ", x).strip() for x in s.split("\n"))
    return "\n".join(x for x in lines if x)[:n]


def first_int(v) -> int | None:
    m = re.search(r"\d+", str(v if v is not None else ""))
    return int(m.group()) if m else None


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
    # Sources put an hourly range in their yearly field ("$17-$20/yr" on 2026-11-06).
    if unit == "/yr" and max(lo, hi) < 1000:
        unit = "/hr"
    return (f"{k(lo)}–{k(hi)}" if lo and hi and lo != hi else k(lo or hi)) + unit


def ashby_pay(summary: str | None) -> str | None:
    """Ashby's tier summary is the range and then the perks: "$207K – $244K • Offers
    Equity • This role is also eligible for medical benefits..." (Vanta, 2026-11-08,
    126 characters on one card). The card shows the range only."""
    head = (summary or "").split("•")[0].strip()
    return head if re.search(r"\d", head) else None


def posting(**kw) -> dict:
    # body: the posting's full text when the list call carries it; ref: how to fetch
    # it when it does not (see fetch_posting); mode/yrs: structured facts a source gives.
    p = {"title": "", "company": "", "url": "", "location": "", "remote": False, "posted": None,
         "salary": None, "desc": "", "hint": None, "origin": "company", "sec_topic": False,
         "body": "", "ref": None, "mode": None, "yrs": None}
    p.update(kw)
    p["title"] = re.sub(r"\s+", " ", unescape(p["title"] or "")).strip()
    p["company"] = unescape(p["company"] or "").strip()
    return p


# Recruiters and staffing shops posting on behalf of an unnamed employer. The
# page lists employers; a recruiter's copy of a role is noise at best.
AGENCY = re.compile(r"recruit|staffing|talent|\bsearch\b|headhunt|mercor|remote office|consultants? inc|"
                    r"jobgether|ladders|hackajob|haystack|swooped|remotehunter|lato jobs|greenlever|"
                    # Staffing firms by name (2026-11-07: TEK Systems 24 rows, Bright Vision 18).
                    r"tek ?systems|bright vision tech|insight global|robert half|kforce|apex systems|randstad|"
                    r"aerotek|actalent|experis|adecco|manpower|cybercoders|collabera|judge group|akkodis|"
                    r"\bmodis\b|beacon hill|kelly services|\bvaco\b|jobot|mindlance|diverse lynx|infojini|"
                    r"\byoh\b|system one|ascendion|\bhays\b|harnham|genesis10|signature consultants|"
                    r"matlen silver|calance|pyramid consulting|cynet systems|intelliswift|net2source", re.I)
# Job sites that stand between a job seeker and the employer: an email gate, a
# sign-up, or a hop to another such site before the real posting (2026-11-11, a
# LinkedIn post David shared: Haystack's "remote" IT manager asked for an email,
# sent to Dice, which asked again, then to a CACI job that was not remote). A row
# whose link goes to one of these is dropped; the page links to the source. None
# on 2026-11-11 (1,406 of 1,755 links were the employer's ATS, 301 its own site).
MIDDLEMAN = re.compile(r"^https?://(?:[\w-]+\.)*(?:dice\.com|haystackapp\.io|haystack\.\w+|lensa\.com|jooble\.org|"
                       r"ziprecruiter\.com|linkedin\.com|indeed\.com|talent\.com|jobs2careers\.com|simplyhired\.com|"
                       r"glassdoor\.com|monster\.com|careerbuilder\.com|jobgether\.com|theladders\.com|getwork\.com|"
                       r"jobright\.ai|adzuna\.com|neuvoo\.\w+|joblist\.com|jobilize\.com|learn4good\.com)(?::\d+)?(?:[/?#]|$)",
                       re.I)


def fold_remote_twins(cands: dict) -> int:
    """A role listed locally and again as remote, the same company and title, is
    one job (2026-11-07: 8 such pairs, mostly an aggregator's remote copy of a
    company-board row). The local row stays and is marked also_remote, so the
    Remote filter still finds it. Keys are company|title|where. Returns how many folded."""
    local: dict[str, list[dict]] = {}
    for k, p in cands.items():
        if p["where"] != "remote":
            local.setdefault(k.rsplit("|", 1)[0], []).append(p)
    folded = 0
    for k in [k for k, p in cands.items() if p["where"] == "remote"]:
        twins = local.get(k.rsplit("|", 1)[0])
        if not twins:
            continue
        for t in twins:
            t["also_remote"] = True
            t["posted"] = t["posted"] or cands[k]["posted"]
        del cands[k]
        folded += 1
    return folded


# ── Sources. Each returns a list of posting() dicts or raises. ───────────────

def src_greenhouse(token: str, name: str) -> list[dict]:
    d = http_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    # The text is read later, one call per kept posting (ref), with its pay range.
    return [posting(title=j.get("title"), company=name, url=j.get("absolute_url"),
                    location=(j.get("location") or {}).get("name", ""),
                    posted=iso(j.get("first_published") or j.get("updated_at")), ref=("gh", token, j.get("id")))
            for j in d.get("jobs", [])]


LEVER_MODE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}


def lever_body(j: dict) -> str:
    """Lever splits a posting: an intro, titled lists (requirements, pay), a closing."""
    parts = [j.get("description") or j.get("descriptionPlain") or ""]
    parts += [f"<p>{x.get('text', '')}</p>{x.get('content', '')}" for x in j.get("lists") or []]
    parts.append(j.get("additional") or j.get("additionalPlain") or "")
    return body_text("<p>".join(parts))


def lever_pay(j: dict) -> str | None:
    r = j.get("salaryRange") or {}
    if (r.get("currency") or "USD") != "USD":
        return None
    return structured_pay(r.get("min"), r.get("max"), r.get("interval"))


def structured_pay(lo, hi, unit_word=None) -> str | None:
    """Pay a source states as numbers. Monthly, weekly, daily or one-off amounts are
    left out rather than shown as a year (review, 2026-11-09), and the size has to
    agree with the stated unit: an hourly figure is under $1,000, a salary over it."""
    w = str(unit_word or "").lower()
    if re.search(r"month|week|day|daily|one.?time|once", w):
        return None
    try:
        lo_f, hi_f = float(lo or 0), float(hi or 0)
    except (TypeError, ValueError):
        return None
    if not lo_f and not hi_f:
        return None
    unit = _pay_unit(lo_f or hi_f, hi_f or lo_f)
    if not unit or ("hour" in w and unit != "/hr") or (re.search(r"year|annual|salary", w) and unit != "/yr"):
        return None
    return money(lo_f, hi_f, unit)


def src_lever(token: str, name: str, eu: bool = False) -> list[dict]:
    # eu: a board on jobs.eu.lever.co answers only on Lever's EU API host (the
    # census reads it there too; one found board on 2026-11-12, Siteimprove).
    d = http_json(f"https://api{'.eu' if eu else ''}.lever.co/v0/postings/{token}?mode=json")
    out = []
    for j in d:
        c = j.get("categories") or {}
        locs = c.get("allLocations") or [c.get("location") or ""]
        out.append(posting(title=j.get("text"), company=name, url=j.get("hostedUrl"),
                           location="; ".join(x for x in locs if x), remote=j.get("workplaceType") == "remote",
                           posted=iso(j.get("createdAt")), desc=text(j.get("descriptionPlain") or j.get("description")),
                           body=lever_body(j), salary=lever_pay(j), mode=LEVER_MODE.get(j.get("workplaceType"))))
    return out


def src_ashby(token: str, name: str) -> list[dict]:
    # Quoted: a found board's name may hold a space ("PAR Technology"); the hand-kept tokens are unchanged by it.
    d = http_json(f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(token)}?includeCompensation=true")
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        comp = ashby_pay((j.get("compensation") or {}).get("compensationTierSummary"))
        out.append(posting(title=j.get("title"), company=name, url=j.get("jobUrl"),
                           location="; ".join(x for x in locs if x),
                           # isRemote is set on hybrid roles too; workplaceType is the real answer.
                           remote=(j.get("workplaceType") or ("Remote" if j.get("isRemote") else "")) == "Remote",
                           posted=iso(j.get("publishedAt")), salary=comp,
                           desc=text(j.get("descriptionPlain") or j.get("descriptionHtml")),
                           body=body_text(j.get("descriptionHtml") or j.get("descriptionPlain")),
                           mode=ASHBY_MODE.get((j.get("workplaceType") or "").lower())))
    return out


ASHBY_MODE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}


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


WD_QUERIES = ("security", "cyber", "cloud", "infrastructure", "network", "systems engineer", "devops", "reliability",
              "support", "administrator", "help desk",
              # The PM track (2026-11-03). 'local' and 'all_oregon' tenants read every job and need no keyword.
              "project manager", "program manager", "product manager",
              # The SE track (2026-11-10): CrowdStrike, F5, Acronis and Pax8 among these tenants.
              "sales engineer", "solutions engineer", "solutions architect")


def src_workday(tenant: str, host: str, site: str, name: str, mode: str = "search") -> list[dict]:
    """mode 'search': every PNW or remote location facet, one keyword search at a time.
    'local': the PNW facets only, every job under them, no keywords; an Oregon
    employer's local list is short, and eleven searches per facet would blow the
    unit's time budget across 25 tenants. 'all_oregon': no facet at all, every job."""
    base = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    if mode == "all_oregon":
        targets = [(None, None, "Oregon")]
    else:
        first = http_json(base, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
        keep = ("oregon", "pnw") if mode == "local" else ("oregon", "pnw", "remote")
        targets = [(p, i, d) for p, i, d in _wd_facets(first.get("facets") or [], [])
                   if p and "location" in p.lower() and where(d) in keep]
    all_oregon = mode == "all_oregon"
    queries = WD_QUERIES if mode == "search" else ("",)
    cap = 400 if mode == "search" else 1000
    out, seen = [], set()
    for param, fid, desc in targets:
        for q in queries:
            offset = 0
            while offset < cap:
                facets = {param: [fid]} if param else {}
                d = http_json(base, {"appliedFacets": facets, "limit": 20, "offset": offset, "searchText": q})
                rows = d.get("jobPostings") or []
                for j in rows:
                    path = j.get("externalPath") or ""
                    if path in seen:
                        continue
                    seen.add(path)
                    loc = j.get("locationsText") or ""
                    if all_oregon and not where(loc):
                        loc = f"{loc}, Oregon" if loc and not re.match(r"\d+ Locations", loc) else "Oregon"
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
                  "it support", "data center technician", "system administrator",
                  # The PM track. Not plain "program manager": Amazon's are mostly
                  # operations and logistics, thousands of them in Washington alone.
                  "technical program manager", "product manager technical", "security program manager",
                  "it project manager",
                  # The SE track: AWS's sales engineers are its solutions architects
                  # (about 80 in Washington on 2026-11-10; the search also returns
                  # consultants and TAMs, which the rules drop).
                  "solutions architect", "solutions engineer"):
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
                                       desc=text((j.get("description_short") or "") + " " + (j.get("basic_qualifications") or "")),
                                       body=body_text("<p>".join(j.get(k) or "" for k in (
                                           "description", "basic_qualifications", "preferred_qualifications")))))
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
              "data center technician",
              # The PM track (2026-11-03).
              "technical program manager", "security program manager", "it project manager", "project manager",
              "program manager", "product manager", "product owner",
              # The SE track (2026-11-10).
              "sales engineer", "solutions engineer", "solutions architect", "solutions consultant", "presales",
              "pre-sales", "customer engineer"):
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
                                   mode="remote" if wm == 2 else None, yrs=first_int(x.get("experience_years")),
                                   desc="Skills: " + ", ".join(t.get("name", "") for t in x.get("tags") or [])))
            pages = d.get("pages") or 1
            if page >= pages:
                break
            page += 1
            time.sleep(0.5)
        time.sleep(0.5)  # Foorilla allows 5 starts a second; stay well under
    return out


def src_workable() -> list[dict]:
    """Workable's cross-employer search (jobs.workable.com), no key. It is where the
    small Portland employers are (Energy Trust, Galois, local MSPs). Its matching is
    loose, so the title rules do the real filtering."""
    out, seen = [], set()
    for loc in ("Portland, Oregon", "Salem, Oregon", "Eugene, Oregon", "Bend, Oregon", "Seattle, Washington"):
        for q in ("security", "cyber", "systems administrator", "network engineer", "cloud engineer", "devops",
                  "help desk", "IT support", "infrastructure", "IT specialist",
                  "project manager", "program manager", "product manager",
                  "sales engineer", "solutions engineer"):
            token = None
            for _ in range(5):
                params = {"query": q, "location": loc, **({"pageToken": token} if token else {})}
                d = http_json("https://jobs.workable.com/api/v1/jobs?" + urllib.parse.urlencode(params))
                for j in d.get("jobs") or []:
                    if j.get("id") in seen:
                        continue
                    seen.add(j.get("id"))
                    locs = [x for x in j.get("locations") or [] if x.upper() != "TELECOMMUTE"]
                    out.append(posting(title=j.get("title"), company=(j.get("company") or {}).get("title"),
                                       url=j.get("url"), location="; ".join(locs),
                                       remote=j.get("workplace") == "remote", posted=iso(j.get("created")),
                                       desc=text(j.get("description")), origin="aggregator",
                                       body=body_text(j.get("description")),
                                       mode={"remote": "remote", "hybrid": "hybrid", "on_site": "onsite"}.get(j.get("workplace"))))
                token = d.get("nextPageToken")
                if not token or not d.get("jobs"):
                    break
                time.sleep(0.4)
    return out


# Employers on Eightfold's careers platform: (key, host, domain, name, locations).
# Its search answers without a login (/api/pcsx/search, ten a page). Autodesk
# left Workday for it about 2026-11-03 (its Workday board fell to 5 jobs, none
# here); Lam Research (Tualatin), Ralliant (Tektronix, Beaverton) and Fortive
# (Everett) were on it already. Each answered from the VPS on 2026-11-04.
EIGHTFOLD = [
    ("autodesk", "jobs.autodesk.com", "autodesk.com", "Autodesk", ("Portland, OR", "Seattle, WA")),
    ("lamresearch", "careers.lamresearch.com", "lamresearch.com", "Lam Research", ("Oregon",)),
    ("ralliant", "ralliant.eightfold.ai", "ralliant.com", "Tektronix (Ralliant)", ("Oregon",)),
    ("fortive", "fortive.eightfold.ai", "fortive.com", "Fortive", ("Washington", "Oregon")),
]


def eightfold_rows(positions: list, host: str, name: str, domain: str = "") -> list[dict]:
    return [posting(title=j.get("name"), company=name, url=f"https://{host}{j.get('positionUrl') or ''}",
                    location="; ".join(j.get("locations") or []),
                    remote=(j.get("workLocationOption") or "").lower() == "remote", posted=iso(j.get("postedTs")),
                    ref=("ef", host, domain, j.get("id")) if domain else None)
            for j in positions]


def src_eightfold(key: str, host: str, domain: str, name: str, locations: tuple) -> list[dict]:
    out, seen = [], set()
    for loc in locations:
        start = 0
        while start < 600:
            params = {"domain": domain, "query": "", "location": loc, "start": start}
            d = (http_json(f"https://{host}/api/pcsx/search?" + urllib.parse.urlencode(params)) or {}).get("data") or {}
            rows = d.get("positions") or []
            fresh = [j for j in rows if j.get("id") not in seen]
            seen.update(j.get("id") for j in fresh)
            out += eightfold_rows(fresh, host, name, domain)
            start += len(rows)
            if not rows or start >= (d.get("count") or 0):
                break
            time.sleep(0.3)
    return out


PROVIDENCE = "https://evac.fa.us2.oraclecloud.com"


def src_providence() -> list[dict]:
    """Providence Health, the region's largest health system, on Oracle Cloud HCM.
    About 2,000 openings; the keyword search is loose ("help desk" returned a food
    service job on 2026-10-21), so read them all, 200 a page, and let the rules sort."""
    out, offset = [], 0
    while offset < 5000:
        finder = f"findReqs;siteNumber=CX_1,limit=200,offset={offset}"
        d = http_json(f"{PROVIDENCE}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
                      f"&expand=requisitionList.secondaryLocations&finder={urllib.parse.quote(finder, safe=';=,')}")
        rows = ((d.get("items") or [{}])[0]).get("requisitionList") or []
        for r in rows:
            locs = [r.get("PrimaryLocation") or ""] + [s.get("Name") or "" for s in r.get("secondaryLocations") or []]
            out.append(posting(title=r.get("Title"), company="Providence",
                               url=f"{PROVIDENCE}/hcmUI/CandidateExperience/en/sites/CX_1/job/{r.get('Id')}",
                               location="; ".join(x for x in locs if x), remote=r.get("WorkplaceTypeCode") == "ORA_REMOTE",
                               posted=iso(r.get("PostedDate")), ref=("prov", r.get("Id")),
                               mode={"ORA_REMOTE": "remote", "ORA_HYBRID": "hybrid",
                                     "ORA_ON_SITE": "onsite"}.get(r.get("WorkplaceTypeCode"))))
        if len(rows) < 200:
            break
        offset += 200
        time.sleep(0.4)
    return out


# Federal jobs from USAJOBS (OPM's Search API, added 2026-11-11). Free with a key,
# and its terms allow republishing: "Prior OPM approval is not required"
# (developer.usajobs.gov/guides/terms-of-service). The key and the email it was
# registered under (sent as the User-Agent, as OPM requires) live in the server's
# environment, never here. Only jobs open to the public, in the IT series: 2210 IT
# management (the federal cyber, sysadmin, network and help desk jobs), 0854
# computer engineering, 1550 computer science, 0391 telecommunications.
USAJOBS_SERIES = "2210;0854;1550;0391"
USAJOBS_PLACES = ("Oregon", "Washington", "Idaho")
# GS grade to the rules' level hint: GS-7 and under hire at entry, GS-13 and up senior.
USAJOBS_PAY = {"PA": "/yr", "PER YEAR": "/yr", "PH": "/hr", "PER HOUR": "/hr"}


def usajobs_rows(items: list) -> list[dict]:
    """USAJOBS search results (SearchResultItems) to postings. The link is the
    official posting on usajobs.gov, and the text comes with the list, so the
    reader never fetches it."""
    out = []
    for it in items:
        d = it.get("MatchedObjectDescriptor") or {}
        det = (d.get("UserArea") or {}).get("Details") or {}
        locs = []
        for loc in d.get("PositionLocation") or []:
            city, state = loc.get("CityName") or "", loc.get("CountrySubDivisionCode") or ""
            locs.append(city if not state or state.lower() in city.lower() else f"{city}, {state}")
        remote = bool(det.get("RemoteIndicator"))
        location = "; ".join(x for x in locs if x) or d.get("PositionLocationDisplay") or ""
        if remote:
            location = f"{location} (remote) · United States"
        pay = None
        for r in d.get("PositionRemuneration") or []:
            unit = USAJOBS_PAY.get(str(r.get("RateIntervalCode") or r.get("Description") or "").upper())
            if unit:
                pay = money(r.get("MinimumRange"), r.get("MaximumRange"), unit)
                break
        grade = str(det.get("LowGrade") or "")
        plan = ((d.get("JobGrade") or [{}])[0].get("Code") or "").upper()
        hint = "EX" if plan == "ES" else (("EN" if int(grade) <= 7 else "SE" if int(grade) >= 13 else "MI")
                                          if grade.isdigit() else None)
        clr = str(det.get("SecurityClearance") or "")
        # "Not Required" must not reach CLEARANCE, which matches the word itself.
        # Its values on 2026-11-11: Not Required, Other, Confidential, Secret, Top Secret,
        # Sensitive Compartmented Information, L Access Authorization.
        real_clr = clr if re.search(r"secret|confidential|\bsci\b|\bq\b|\bl\b|sensitive", clr, re.I) else ""
        # The agency, unless the organization is an office inside it ("Deputy Assistant
        # Secretary for Information Technology" is the VA).
        org, dept = d.get("OrganizationName") or "", d.get("DepartmentName") or ""
        company = dept if not org or re.search(r"secretary|office of|deputy|headquarters", org, re.I) else org
        duties = det.get("MajorDuties") or []
        body = body_text("<p>".join([str(det.get("JobSummary") or ""), *[str(x) for x in duties],
                                     str(d.get("QualificationSummary") or ""), str(det.get("Requirements") or ""),
                                     str(det.get("Education") or ""), f"Security clearance: {clr}" if clr else ""]))
        url = re.sub(r"^(https://www\.usajobs\.gov):443", r"\1", d.get("PositionURI") or "")
        out.append(posting(title=d.get("PositionTitle"), company=company,
                           url=url, location=location, remote=remote, posted=iso(d.get("PublicationStartDate")),
                           salary=pay, hint=hint,
                           desc=(f"Clearance: {real_clr}. " if real_clr else "") + text(det.get("JobSummary")),
                           body=body, mode="remote" if remote else None))
    return out


# Jobs members share (added 2026-11-11): the "Share a job" form on the page posts to
# infra/intake.py, which checks the link, the place and the title against these
# same rules, appends the tip to TIPS beside the state file, and emails David signed
# links to remove it (and, where needed, to approve it). For jobs only on LinkedIn or
# Indeed, which the Splat never copies (their terms forbid it): a member pointing at
# one job is not copying their database. Every tip still goes through the rules and
# Jev, on the title, employer and place the member typed; the member's note goes
# only to David's email, never to the page or the classifier.
# - A LinkedIn or Indeed link (`gate`): the Splat looks for the employer's own
#   posting on its Greenhouse, Lever or Ashby board (find_source) and links there
#   when it is surely the same job; otherwise the row says "LinkedIn only" and stays
#   TIP_GATED_DAYS. Those pages are never fetched (NO_FETCH).
# - Any other link shows only when it is a posting on an applicant tracking system
#   the reader reads through its own API (ats_link) and the reader read it, or when
#   David approved it. A page that merely carries JobPosting data is not enough:
#   anyone can put that beside a fake sign-in form (the branch review, 2026-11-11).
# - A tip of a job already on the page is dropped (drop_tip_twins), so a member's
#   copy never stands in for the employer's own row.
TIPS = STATE_DIR / "tips.json"
TIP_GATED_DAYS = 14
GATE_HOST = re.compile(r"^https://(?:[\w-]+\.)*(linkedin|indeed)\.com/", re.I)
FIND_MAX = 40          # find_source lookups a run: a flood of tips must not stall the run
_FIND_LEFT = [FIND_MAX]


def tip_slugs(company: str) -> list[str]:
    """Board names a company might use: "Hilgers PLLC" -> hilgers, hilgerspllc."""
    words = re.sub(r"[^a-z0-9 ]+", "", re.sub(r"&", " and ", (company or "").lower())).split()
    core = [w for w in words if w not in ("inc", "llc", "pllc", "ltd", "corp", "corporation", "co", "company", "the")]
    out = []
    for ws in (core, words):
        for s in ("".join(ws), "-".join(ws)):
            if len(s) >= 3 and s not in out:
                out.append(s)
    return out[:4]


def same_place(loc: str, tip_where: str | None) -> bool:
    w = where(loc or "", bool(REMOTE.search(loc or "")))
    return bool(w) and (w == tip_where or (tip_where in ("oregon", "pnw") and w in ("oregon", "pnw")))


def find_source(company: str, title: str, tip_where: str | None) -> dict | None:
    """The employer's own posting of a job a member found on LinkedIn or Indeed, or
    None. Only when it is surely the same job: the same title in the same place, on
    a Greenhouse board whose own name is the employer's, or on a Lever or Ashby
    board named exactly for it (the review found a guessed board name alone can be
    another company's). At most FIND_MAX lookups a run. The Greenhouse match keeps
    its API reference, so the reader reads it even on a custom careers domain."""
    if _FIND_LEFT[0] <= 0:
        return None
    _FIND_LEFT[0] -= 1
    want, who = norm(title), norm(company)
    slugs = tip_slugs(company)
    for slug in slugs:
        try:
            board = host_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
            if norm(board.get("name") or "") != who:
                continue
            for j in host_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs").get("jobs") or []:
                if norm(j.get("title") or "") == want and same_place((j.get("location") or {}).get("name"), tip_where):
                    return {"url": j.get("absolute_url"), "ref": ("gh", slug, j.get("id"))}
        except Exception:  # noqa: BLE001 - no board under that name
            continue
    exact = slugs[:1]  # the whole name, joined: "hilgers" for "Hilgers PLLC"
    for slug in exact:
        for url, rows, link, place in (
                (f"https://api.lever.co/v0/postings/{slug}?mode=json", lambda d: d if isinstance(d, list) else [],
                 lambda j: j.get("hostedUrl"), lambda j: (j.get("categories") or {}).get("location")),
                (f"https://api.ashbyhq.com/posting-api/job-board/{slug}", lambda d: d.get("jobs") or [],
                 lambda j: j.get("jobUrl"), lambda j: j.get("location"))):
            try:
                d = host_get(url)
            except Exception:  # noqa: BLE001 - no board under that name
                continue
            for j in rows(d):
                if (norm(j.get("title") or j.get("text") or "") == want and same_place(place(j), tip_where)
                        and (u := link(j))):
                    return {"url": u, "ref": None}
    return None


def src_tips() -> list[dict]:
    if not TIPS.exists():
        return []
    tips = json.loads(TIPS.read_text(encoding="utf-8"))
    out = []
    _FIND_LEFT[0] = FIND_MAX
    for t in tips if isinstance(tips, list) else []:
        age = age_days(t.get("shared"))
        url = t.get("url") or ""
        gate = GATE_HOST.match(url)
        if t.get("status") == "removed" or age is None or age > (TIP_GATED_DAYS if gate else MAX_AGE_DAYS):
            continue
        loc = t.get("location") or ""
        # posted stays empty: the share date is not when the job was posted.
        p = posting(title=t.get("title"), company=t.get("company"), url=url, location=loc,
                    remote=bool(REMOTE.search(loc)), origin="member")
        p["approved"] = t.get("status") == "approved"
        if gate:
            src = find_source(p["company"], p["title"], where(loc, p["remote"]))
            if src:
                p.update(url=src["url"], ref=src["ref"], found=True)
            else:
                p["gate"] = "LinkedIn" if gate[1].lower() == "linkedin" else "Indeed"
        out.append(p)
    return out


def ats_link(url: str) -> bool:
    """A posting on an applicant tracking system whose own API the reader reads (see
    fetch_posting), so what a visitor reaches is the vendor's page, not one an
    anyone-can-type link could swap or cloak."""
    if any(r.search(url or "") for r in (GH_URL, LEVER_URL, ASHBY_URL, WD_URL, ORACLE_URL, RIPPLING_URL,
                                         SMARTR_URL, BAMBOO_URL, WORKABLE_URL)):
        return True
    m = EF_URL.search(url or "")
    return bool(m and EF_DOMAIN.get(m[1].lower()))


def not_verified(p: dict, f: dict | None) -> bool:
    """A member's tip that is not on LinkedIn or Indeed is held back unless David
    approved it, or the reader read it as a posting on a tracking system it reads
    through the vendor's API (ats_link, or a find_source match)."""
    if p.get("origin") != "member" or p.get("gate") or p.get("approved"):
        return False
    return not (f and f.get("read") and (p.get("found") or ats_link(p.get("url") or "")))


def drop_tip_twins(cands: dict) -> int:
    """A member's tip of a job already on the page (the same employer and title from
    any other source, in any place) is dropped: the employer's own row, not a member's
    copy, shows. The review found a Portland-typed LinkedIn tip could otherwise fold
    away the employer's own remote row. Returns how many were dropped."""
    seen = {(norm(p["company"]), norm(p["title"])) for p in cands.values() if p.get("origin") != "member"}
    twins = [k for k, p in cands.items()
             if p.get("origin") == "member" and (norm(p["company"]), norm(p["title"])) in seen]
    for k in twins:
        del cands[k]
    return len(twins)


def src_usajobs() -> list[dict]:
    key = os.environ.get("USAJOBS_API_KEY", "").strip()
    email = os.environ.get("USAJOBS_EMAIL", "").strip()
    if not key or not email:
        raise RuntimeError("no USAJOBS_API_KEY or USAJOBS_EMAIL")
    headers = {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key}
    # Remote federal IT jobs: 0 of 159 open to the public on 2026-11-11 (104 allowed
    # telework). The search is kept for when that changes.
    queries = [{"LocationName": p} for p in USAJOBS_PLACES] + [{"RemoteIndicator": "True"}]
    out, seen = [], set()
    for q in queries:
        page = 1
        while page <= 20:  # 500 a page; the IT series in three states and remote is far less
            params = {**q, "JobCategoryCode": USAJOBS_SERIES, "HiringPath": "public", "DatePosted": MAX_AGE_DAYS,
                      "ResultsPerPage": 500, "Page": page, "Fields": "Full"}
            d = http_json("https://data.usajobs.gov/api/search?" + urllib.parse.urlencode(params), headers=headers)
            res = d.get("SearchResult") or {}
            for p in usajobs_rows(res.get("SearchResultItems") or []):
                if p["url"] and p["url"] not in seen:
                    seen.add(p["url"])
                    out.append(p)
            if page >= int((res.get("UserArea") or {}).get("NumberOfPages") or 1):
                break
            page += 1
            time.sleep(0.5)
    return out


# The Portland-area governments on NEOGOV (governmentjobs.com: City of Portland,
# TriMet, Port of Portland, City of Beaverton, Oregon Metro) were dropped
# 2026-11-11. NEOGOV's terms of use (effective 2025-09-02) forbid anyone to
# "reproduce, distribute, ... publicly display, ... republish" content on the
# career pages it hosts, "regardless of whether such content is public or viewable
# without a login", and its robots.txt disallows every crawler but the search
# engines. The page links to those agencies' own job pages instead, and the reader
# fetches nothing from governmentjobs.com (NO_FETCH). The old reader is in git
# history (src_neogov, before this commit).


# Remotive was dropped 2026-11-04: its API returns the same ~11 newest jobs
# whatever is asked (it kept none), and its response asks that its jobs not be
# resubmitted to third-party job sites.


def src_remoteok() -> list[dict]:
    d = http_json("https://remoteok.com/api")
    return [posting(title=j.get("position"), company=j.get("company"), url=j.get("url"),
                    location="Remote · " + (j.get("location") or ""), remote=True, posted=iso(j.get("epoch")),
                    salary=money(j.get("salary_min"), j.get("salary_max"), "/yr"),
                    desc=text(j.get("description")), origin="aggregator", body=body_text(j.get("description")))
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
                               desc=text(j.get("jobExcerpt")), origin="aggregator",
                               body=body_text(j.get("jobDescription"))))
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
                               desc=text(j.get("excerpt") or j.get("description")), origin="aggregator",
                               body=body_text(j.get("description"))))
        if len(rows) < 20:
            break
        time.sleep(0.4)
    return out


def src_workingnomads() -> list[dict]:
    d = http_json("https://www.workingnomads.com/api/exposed_jobs/")
    return [posting(title=j.get("title"), company=j.get("company_name"), url=j.get("url"),
                    location="Remote · " + (j.get("location") or ""), remote=True, posted=iso(j.get("pub_date")),
                    desc=text(j.get("description")), origin="aggregator", body=body_text(j.get("description")))
            for j in d]


# The Muse was dropped 2026-11-10: from 2026-11-09 every call to its public jobs
# API (https://www.themuse.com/api/public/jobs) answers 404 from this server and
# 403 elsewhere, with Cloudflare's "Just a moment..." browser challenge, and its
# developer pages sit behind the same challenge. A script cannot pass that, and
# should not try. No notice of a replacement API was found. If it comes back,
# the old reader is in git history (src_muse, before this commit).


# Technology employers' own boards, for the PM track: a "Product Manager,
# Payments" on Stripe's board is technology work, the same title at Nike or the
# State of Oregon has to say so. Every Greenhouse, Lever and Ashby board above is
# a tech company's. Not Amazon (its program managers are mostly operations and
# logistics), not the aggregators (they carry any employer, and a copy there
# must not pass where the employer's own board would not), and not the telecoms
# (T-Mobile's and Astound's PMs are mostly retail and care).
TECH_SOURCES = {"greenhouse", "lever", "ashby"}
TECH_WORKDAY = {"crowdstrike", "zillow", "ffive", "intel", "micron", "salesforce", "pax8inc", "acronis",
                "latticesemi", "flir", "hp", "nvidia", "analogdevices", "cisco"}
# Fortive is an industrial holding company, so not here.
TECH_EIGHTFOLD = {"autodesk", "lamresearch", "ralliant"}


def tech_source(sid: str) -> bool:
    family, _, name = sid.partition(":")
    return (family in TECH_SOURCES or (family == "workday" and name in TECH_WORKDAY)
            or (family == "eightfold" and name in TECH_EIGHTFOLD))


# ── Employer boards found through job feeds ("found boards") ────────────────
#
# An employer that reaches the Splat only through an aggregator, but whose cards
# link to its own Ashby, Greenhouse or Lever board, gets that board read
# directly, as Vultr's is by hand: its rows then come from the employer (origin
# "company", the board's own dates), and its in-track jobs the aggregator missed
# show up too. The boards are the ones company_check.py's census read
# (boards.json), each with the card name that most often links to it (`name`)
# and how many cards do (`cards`). Each is read by its vendor's own reader under
# that name, so a row keys (company|title|where) and reads (read_key: the key
# and the URL) as the aggregator's copy did, and a posting read before is not
# read again. On 2026-11-12, 147 boards linked from aggregator rows were not
# hand-kept (98 Greenhouse, 34 Ashby, 14 Lever, 1 Lever EU).
#
# - Read OK within AUTO_BOARD_DAYS. The census reads only the boards today's
#   cards link to, and a found board's own rows link to it, so a board in use
#   is read every day. A week rides out a vendor, or the company check, down for
#   a few days without the employer's rows flipping back to the aggregator's
#   copies (each flip pays Jev and the reader again where a copy keys
#   differently), and still drops within a week a board nobody links to any
#   more or one that is gone (its reads fail).
# - Not hand-kept (GREENHOUSE, LEVER, ASHBY above; Lever EU is its own key).
# - A usable name that is not a staffing firm's (AGENCY, on the token too: a
#   recruiter's board carries other employers' jobs under its one name), on a
#   board whose URLs pass MIDDLEMAN and NO_FETCH.
# - At most AUTO_BOARD_MAX, the busiest (cards) first: twice the 2026-11-12
#   count, and under the census's BOARD_MAX (400) so each one is read daily.
#
# Not tech sources (tech_source): the hand-kept boards are technology companies
# by review; these are whatever employers an aggregator carries (SpaceX, Axon,
# Flexport, a health-care firm), and their rows came through Foorilla, which is
# not a tech source either. So the titles that pass are the ones that passed
# before; only where a row comes from changes. The family is "auto"
# ("auto:ashby:horizon3ai"), an id that stays the same while the board is in
# the ledger, so the failed-source grace (by label, the stored name) holds.
AUTO_BOARD_DAYS = 7
AUTO_BOARD_MAX = 300
AUTO_FETCH_MINUTES = 10   # no found board starts later in the run: the rest of RUN_BUDGET is Jev's and the reader's
AUTO_BREAKER = 3          # failures in a row on one API host stop its found boards for the run
AUTO_API = {"greenhouse": "boards-api.greenhouse.io", "lever": "api.lever.co", "lever-eu": "api.eu.lever.co",
            "ashby": "api.ashbyhq.com"}
# Tokens as board_key() makes them from a URL: Ashby's may hold a space ("par technology").
AUTO_TOKEN = {"greenhouse": re.compile(r"[\w-]{1,100}"), "lever": re.compile(r"[\w.-]{1,100}"),
              "lever-eu": re.compile(r"[\w.-]{1,100}"), "ashby": re.compile(r"[^/?#%\\\x00-\x1f\x7f]{1,100}")}
_AUTO_FAILS: dict[str, int] = {}


def hand_kept_boards() -> set[str]:
    """The boards listed at the top of this file, keyed as board_key() keys them."""
    return ({f"greenhouse:{t.lower()}" for t in GREENHOUSE} | {f"lever:{t.lower()}" for t in LEVER}
            | {f"ashby:{t.lower()}" for t in ASHBY})


def auto_boards(ledger: dict | None) -> list[tuple[str, str, str, str]]:
    """The found boards to read this run, busiest first: (source id, name, ats,
    token). The token is the ledger key's, checked, never the entry's own field."""
    hand = hand_kept_boards()
    picked = []
    for key, rec in (ledger or {}).items():
        if not isinstance(key, str) or not isinstance(rec, dict) or key in hand:
            continue
        ats, _, token = key.partition(":")
        if ats not in AUTO_API or not AUTO_TOKEN[ats].fullmatch(token) or not token.strip(". "):
            continue
        last = rec.get("last_ok")
        age = age_days(last) if isinstance(last, str) else None
        if age is None or age > AUTO_BOARD_DAYS:
            continue
        name = rec.get("name").strip() if isinstance(rec.get("name"), str) else ""
        if not name or len(name) > 200 or AGENCY.search(name) or AGENCY.search(token):
            continue
        link = rec.get("link") if isinstance(rec.get("link"), str) else ""
        if any(MIDDLEMAN.search(u) or NO_FETCH.search(u) for u in (f"https://{AUTO_API[ats]}/", link)):
            continue
        cards = rec.get("cards")
        cards = cards if isinstance(cards, int) and not isinstance(cards, bool) else 0
        picked.append((cards, key, name, ats, token))
    picked.sort(key=lambda x: (-x[0], x[1]))
    return [(f"auto:{key}", name, ats, token) for _, key, name, ats, token in picked[:AUTO_BOARD_MAX]]


def src_auto(ats: str, token: str, name: str) -> list[dict]:
    """A found board, through src_greenhouse, src_lever or src_ashby. Three at a
    time per API host (host_get's semaphores), none started after
    AUTO_FETCH_MINUTES, and AUTO_BREAKER failures in a row on a host stop the
    rest of its boards for the run; a board the vendor says is not there (404,
    410) is that board's trouble and does not count. A board not read is a
    failed source: its rows from the last GRACE_DAYS are carried."""
    host = AUTO_API[ats]
    with _HOSTS_LOCK:
        slot = _HOSTS.setdefault(host, threading.BoundedSemaphore(3))
    with slot:
        if time.monotonic() - _T0 > AUTO_FETCH_MINUTES * 60:
            raise RuntimeError("not read today: the run's time for found boards is up")
        with _HOSTS_LOCK:
            if _AUTO_FAILS.get(host, 0) >= AUTO_BREAKER:
                raise RuntimeError(f"not read today: {host} failed {AUTO_BREAKER} times in a row")
        try:
            if ats == "greenhouse":
                rows = src_greenhouse(token, name)
            elif ats == "ashby":
                rows = src_ashby(token, name)
            else:
                rows = src_lever(token, name, eu=ats == "lever-eu")
        except Exception as e:
            with _HOSTS_LOCK:
                if not re.search(r"HTTP Error (?:404|410)\b", str(e)):
                    _AUTO_FAILS[host] = _AUTO_FAILS.get(host, 0) + 1
            raise
        with _HOSTS_LOCK:
            _AUTO_FAILS[host] = 0
    return rows


def all_sources(only: set[str] | None, board_ledger: dict | None = None):
    s = [("foorilla", "Foorilla", src_foorilla, ()), ("amazon", "Amazon", src_amazon, ()),
         ("remoteok", "RemoteOK", src_remoteok, ()),
         ("jobicy", "Jobicy", src_jobicy, ()), ("himalayas", "Himalayas", src_himalayas, ()),
         ("workingnomads", "Working Nomads", src_workingnomads, ()),
         ("workable", "Workable", src_workable, ()), ("providence", "Providence", src_providence, ()),
         ("usajobs", "USAJOBS", src_usajobs, ()), ("tips", "Shared by members", src_tips, ())]
    s += [(f"eightfold:{e[0]}", e[3], src_eightfold, e) for e in EIGHTFOLD]
    s += [(f"greenhouse:{t}", n, src_greenhouse, (t, n)) for t, n in GREENHOUSE.items()]
    s += [(f"lever:{t}", n, src_lever, (t, n)) for t, n in LEVER.items()]
    s += [(f"ashby:{t}", n, src_ashby, (t, n)) for t, n in ASHBY.items()]
    s += [(f"workday:{w[0]}", w[3], src_workday, w) for w in WORKDAY]
    s += [(f"workday:{w[0]}", w[3], src_workday, (*w, "local")) for w in WORKDAY_LOCAL]
    s += [(f"workday:{w[0]}", w[3], src_workday, (*w, "all_oregon")) for w in WORKDAY_ALL_OREGON]
    # Last, so the pool starts every listed source before them.
    s += [(sid, name, src_auto, (ats, tok, name)) for sid, name, ats, tok in auto_boards(board_ledger)]
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
        "pm": "Project, program or product management of technology work: IT and software project managers, "
              "program and technical program managers (TPM), security program managers, project coordinators "
              "and PMO roles on technology projects, and product managers or product owners of software, "
              "platform, data or security products.",
        "se": "Sales engineering of technology: sales engineers, solutions engineers and consultants, pre-sales "
              "engineers and architects, and a vendor's solutions architects or customer engineers, who work "
              "with its sales team to win customers for software, cloud, security, networking, data or IT "
              "products (discovery, demos, proofs of concept, solution design). Not engineers who build or run "
              "the employer's own systems, support or professional-services delivery after the sale, or "
              "technical account managers.",
        "neither": "Anything else, including general application or product software development (frontend, "
                   "backend, full-stack, mobile, ML, data, games) whose main job is not infrastructure or security; "
                   "customer service or call-center support that is not technical; "
                   "sales roles without technical pre-sales work (account executives, sales representatives); "
                   "sales engineers of industrial, mechanical, electrical, HVAC, building, vehicle, medical-device "
                   "or semiconductor-equipment products; "
                   "marketing, recruiting, analysts who do not engineer, "
                   "physical security, hardware, mechanical, civil, manufacturing or chip-design engineering; "
                   "project, program or product managers whose work is not technology (construction, facilities, "
                   "data center construction or build-out, water or energy programs, clinical care, marketing, "
                   "merchandise, apparel, supply chain, customer experience or support operations, social impact "
                   "or philanthropy, a health plan's provider network, or other business operations); "
                   "scrum masters and agile coaches."}},
    "level": {"type": "choice", "instructions": "What seniority does this role hire at?", "criteria": {
        "intern": "Internship, co-op, or summer program.",
        "entry": "Entry level: new grad, junior, associate, apprentice, tier 1, level I, or about 0-2 years asked.",
        "mid": "Mid-level: a few years of experience, level II, no senior title.",
        "senior": "Senior, lead, level III, or a people manager of a team. A project, program or product "
                  "manager title alone is not senior, nor is a sales engineer's \"Solutions Architect\".",
        "staff": "Staff, principal, distinguished, or senior architect.",
        "executive": "Director, VP, head of, or C-level."}},
    "clearance": {"type": "noul", "instructions": "Does this role require a US government security clearance?"},
}


# Bump when JEV_QUESTIONS changes meaning, so cached answers to the old
# questions are not read as answers to the new ones. v2 (2026-10-03): "eng"
# narrowed from all engineering to infrastructure next to security. v3
# (2026-10-04): "eng" takes IT support and help desk; level has "intern". v4
# (2026-11-03): track has "pm"; "neither" no longer covers technology PMs. v5
# (2026-11-10): track has "se"; "neither" names the sales engineers who are out.
JEV_VERSION = "v5"


def jev_key(p: dict) -> str:
    raw = f"{JEV_VERSION}|{p['title']}|{p['company']}|{p['location']}".lower()
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# Jev down must not cost the run (review, 2026-11-09: retries held the slots and an
# outage ran past the unit's 45 minutes). Every call goes through jev_call: four
# at a time (six at ~150 ms each reached TypeSafe's 40 a second and drew 429s), two
# tries with the pause outside the slot, and a breaker that, after JEV_BREAK failed
# calls in a row, skips every later one without a request.
JEV_BREAK = 10
_JEV_FAILS = [0]
_JEV_LOCK = threading.Lock()
_JEV_SLOTS = threading.BoundedSemaphore(4)


def jev_open() -> bool:
    with _JEV_LOCK:
        return _JEV_FAILS[0] >= JEV_BREAK


def jev_call(state: dict, questions: dict, key: str, valid) -> dict | None:
    """Jev's whole answer, when valid(answers) holds, or None. The breaker is checked
    before and after waiting for a slot; once open, a late success does not close it."""
    for attempt in range(2):
        if jev_open():
            return None
        with _JEV_SLOTS:
            if jev_open():
                return None
            try:
                d = http_json("https://api.typesafe.ai/v1/systemone", {"state": state, "model": "jev-latest",
                              "questions": questions}, headers={"Authorization": f"Bearer {key}"}, tries=1, timeout=30)
            except (RuntimeError, ValueError):
                d = None
        if d is not None and valid(d.get("answers") or {}):
            with _JEV_LOCK:
                if _JEV_FAILS[0] < JEV_BREAK:
                    _JEV_FAILS[0] = 0
            return d
        if attempt == 0:
            time.sleep(1.5)
    with _JEV_LOCK:
        _JEV_FAILS[0] += 1
    return None


TRACKS = ("cyber", "eng", "pm", "se", "neither")
LEVELS = ("intern", "entry", "mid", "senior", "staff", "executive")


def ask_jev(p: dict, key: str) -> dict | None:
    state = {"title": p["title"], "company": p["company"], "location": p["location"], "excerpt": p["desc"][:1500]}
    d = jev_call(state, JEV_QUESTIONS, key, lambda a: (a.get("track") or {}).get("choice") in TRACKS
                 and (a.get("level") or {}).get("choice") in LEVELS)
    if d is None:
        return {"error": "Jev did not answer" + (" (breaker open)" if jev_open() else "")}
    a = d["answers"]
    clr = (a.get("clearance") or {}).get("noul")
    return {"track": a["track"]["choice"], "level": a["level"]["choice"],
            "clearance": isinstance(clr, (int, float)) and clr >= 0.6,
            "tokens": (d.get("usage") or {}).get("input_tokens", 0)}


# ── Reading each posting once ────────────────────────────────────────────────
# A title says little about pay, clearance, office days, degree or years. Those
# are in the posting's own text, so each kept posting is read once, the day it is
# first seen, and its facts cached under READ_VERSION. Code takes what code can
# (pay, years, days a week); Jev answers bounded choices over the few sentences
# that matter, never the whole posting: in the operator's own tests a long
# haystack moved its answers, a few hundred relevant characters fixed them.

READ_VERSION = "r1"   # bump when a question or a code rule below changes meaning
READ_MAX_PER_RUN = 2500
READ_MIN = 600        # characters of text before a fact it lacks counts as "not stated"
_HOSTS: dict[str, threading.BoundedSemaphore] = {}
_HOSTS_LOCK = threading.Lock()


def public_host(host: str) -> bool:
    """True when every address the name resolves to is on the public internet. The
    reader fetches URLs that third-party listings supply; one must not be able to
    point this server at itself, its network, or a cloud metadata address."""
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
    except (OSError, UnicodeError):
        return False
    return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlsplit(newurl)
        if u.scheme != "https" or u.port not in (None, 443) or not public_host(u.hostname or ""):
            raise urllib.error.HTTPError(newurl, 403, "redirect to a non-public address", headers, fp)
        if NO_FETCH.search(newurl):
            raise urllib.error.HTTPError(newurl, 403, "redirect to a host that bars automated access", headers, fp)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            fp.close()  # urllib would read the redirect's own body whole, with no cap (review, 2026-11-09)
        return new


PUBLIC = urllib.request.build_opener(_PublicRedirects, _TrackedHTTPSHandler, _TrackedHTTPHandler)


def host_get(url: str, as_json: bool = True, headers: dict | None = None):
    """A posting fetch: https on port 443 to a public host only, redirects
    included; one try with a 25-second budget for the whole answer (a read that
    fails is tried again the next day); three requests at a time per host at most
    (the boards are employers' sites). A page is cut at 500 KB, an API answer at
    20 MB (Ashby's boards run to about 5)."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.port not in (None, 443) or not public_host(u.hostname or ""):
        raise RuntimeError(f"not a public https URL: {u.hostname}")
    if NO_FETCH.search(url):
        raise RuntimeError(f"not a public https URL for automated access: {u.hostname}")  # PERMANENT: waits a week
    host = u.netloc.lower()
    with _HOSTS_LOCK:
        slot = _HOSTS.setdefault(host, threading.BoundedSemaphore(3))
    with slot:
        s = http_text(url, headers=headers, tries=1, timeout=20, limit=20_000_000 if as_json else 500_000,
                      opener=PUBLIC, deadline=25)
    return json.loads(s) if as_json else s


def _gh_read(token: str, jid) -> dict:
    d = host_get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}?pay_transparency=true")
    usd = [r for r in d.get("pay_input_ranges") or [] if (r.get("currency_type") or "USD") == "USD" and r.get("min_cents")]
    # Each range carries a title; an OTE or commission range is not base pay unless it is all there is.
    base = [r for r in usd if not OTE.search(EXCEPT.sub("", f"{r.get('title') or ''} {text(r.get('blurb'), 300)}"))] or usd
    pay = structured_pay(min(r["min_cents"] for r in base) / 100, max(r.get("max_cents") or 0 for r in base) / 100) if base else None
    return {"body": body_text(d.get("content")), "pay": pay}


def _lever_read(m) -> dict:
    j = host_get(f"https://api{'.eu' if m[1] else ''}.lever.co/v0/postings/{m[2]}/{m[3]}")
    return {"body": lever_body(j), "pay": lever_pay(j), "mode": LEVER_MODE.get(j.get("workplaceType"))}


@lru_cache(maxsize=128)
def _ashby_board(org: str) -> dict:
    # A board name may hold a space ("PAR Technology", 2026-11-09).
    d = host_get(f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(org)}?includeCompensation=true")
    return {j.get("id"): j for j in d.get("jobs") or []}


def _ashby_read(m) -> dict | None:
    j = _ashby_board(urllib.parse.unquote(m[1])).get(m[2])
    if not j:
        return None
    return {"body": body_text(j.get("descriptionHtml") or j.get("descriptionPlain")),
            "pay": ashby_pay((j.get("compensation") or {}).get("compensationTierSummary")),
            "mode": ASHBY_MODE.get((j.get("workplaceType") or "").lower())}


def _workday_read(m) -> dict:
    d = host_get(f"https://{m[1]}.{m[2]}.myworkdayjobs.com/wday/cxs/{m[1]}/{m[3]}/{m[4]}")
    info = d.get("jobPostingInfo") or {}
    rt = (info.get("remoteType") or "").lower()
    return {"body": body_text(info.get("jobDescription")),
            "mode": "remote" if "remote" in rt else "hybrid" if "hybrid" in rt else "onsite" if "site" in rt else None}


EF_DOMAIN = {e[1]: e[2] for e in EIGHTFOLD} | {"apply.careers.microsoft.com": "microsoft.com"}


def _eightfold_read(host: str, domain: str, pid) -> dict:
    d = (host_get(f"https://{host}/api/pcsx/position_details?position_id={pid}&domain={domain}&hl=en") or {}).get("data") or {}
    wl = (d.get("workLocationOption") or "").lower()
    # Not its "onsite": Microsoft and Lam send it for roles whose text says otherwise (audit, 2026-11-09).
    return {"body": body_text(d.get("jobDescription")),
            "mode": "remote" if "remote" in wl else "hybrid" if "hybrid" in wl else None}


def _oracle_read(base: str, site: str, rid) -> dict:
    """Oracle Cloud HCM (Providence and the other tenants Foorilla links to)."""
    finder = f'ById;Id="{rid}",siteNumber={site}'
    d = host_get(f"{base}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                 f"&finder={urllib.parse.quote(finder, safe=';=,')}")
    it = (d.get("items") or [{}])[0]
    return {"body": body_text("<p>".join(it.get(k) or "" for k in (
        "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")))}


def _rippling_read(m) -> dict:
    d = host_get(f"https://ats.rippling.com/api/v2/board/{m[1]}/jobs/{m[2]}")
    desc = d.get("description")
    html = "<p>".join(str(v) for v in desc.values()) if isinstance(desc, dict) else str(desc or "")
    return {"body": body_text(html)}


def _smartrecruiters_read(m) -> dict:
    d = host_get(f"https://api.smartrecruiters.com/v1/companies/{m[1]}/postings/{m[2]}")
    secs = (d.get("jobAd") or {}).get("sections") or {}
    html = "<p>".join((secs.get(k) or {}).get("text") or "" for k in (
        "jobDescription", "qualifications", "additionalInformation", "companyDescription"))
    return {"body": body_text(html), "mode": "remote" if (d.get("location") or {}).get("remote") else None}


def _bamboohr_read(m) -> dict:
    op = ((host_get(f"https://{m[1]}.bamboohr.com/careers/{m[2]}/detail") or {}).get("result") or {}).get("jobOpening") or {}
    return {"body": body_text(op.get("description")), "pay": pay_from_text(str(op.get("compensation") or "")),
            "mode": {"0": "onsite", "1": "remote", "2": "hybrid"}.get(str(op.get("locationType")))}


def _workable_read(code: str) -> dict | None:
    """apply.workable.com/j/CODE redirects to /ACCOUNT/j/CODE; the job API wants the account."""
    req = urllib.request.Request(f"https://apply.workable.com/j/{code}", headers={"User-Agent": UA})
    with fetch_deadline(20), PUBLIC.open(req, timeout=20) as r:  # only the redirect's address is wanted
        m = re.search(r"^https://apply\.workable\.com/([\w-]+)/j/", r.geturl())
    if not m:
        return None
    d = host_get(f"https://apply.workable.com/api/v2/accounts/{m[1]}/jobs/{code}")
    pay = (structured_pay(d.get("salary_from"), d.get("salary_to"), d.get("salary_frequency"))
           if (d.get("salary_currency_iso_code") or "USD") == "USD" else None)
    return {"body": body_text("<p>".join(d.get(k) or "" for k in ("description", "requirements", "benefits"))),
            "pay": pay, "mode": {"remote": "remote", "hybrid": "hybrid", "on_site": "onsite"}.get(d.get("workplace"))}


def _ld_nodes(x):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from _ld_nodes(v)
    elif isinstance(x, list):
        for v in x:
            yield from _ld_nodes(v)


def ld_pay(sal) -> str | None:
    if not isinstance(sal, dict) or (sal.get("currency") or "USD") != "USD":
        return None
    v = sal.get("value") if isinstance(sal.get("value"), dict) else sal
    return structured_pay(v.get("minValue") or v.get("value"), v.get("maxValue"), v.get("unitText") or sal.get("unitText"))


def ld_blobs(html: str):
    """The bodies of <script type="application/ld+json"> tags, found with str.find:
    a lazy regex rescanned the page from every unclosed "<script"."""
    low, i = html.lower(), 0
    while (i := low.find("<script", i)) >= 0:
        close = low.find(">", i)
        if close < 0:
            return
        end = low.find("</script", close)
        if end < 0:
            return
        if "application/ld+json" in low[i:close]:
            yield html[close + 1:end]
        i = end + 8


def jsonld_posting(html: str) -> dict | None:
    """The schema.org JobPosting most career sites embed for search engines."""
    for blob in ld_blobs(html):
        try:
            data = json.loads(blob.strip())
        except ValueError:
            continue
        for node in _ld_nodes(data):
            if "JobPosting" in str(node.get("@type")):
                return {"body": body_text(node.get("description")), "pay": ld_pay(node.get("baseSalary")),
                        "mode": "remote" if "TELECOMMUTE" in str(node.get("jobLocationType")).upper() else None}
    return None


# Each pattern is anchored at the start of the URL, so the ATS is the host, not a
# path segment: "https://evil.example/governmentjobs.com/careers/a/jobs/1" is no NEOGOV page.
GH_URL = re.compile(r"^https://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", re.I)
LEVER_URL = re.compile(r"^https://jobs\.(eu\.)?lever\.co/([\w.-]+)/([0-9a-f-]{36})", re.I)
ASHBY_URL = re.compile(r"^https://jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", re.I)
WD_URL = re.compile(r"^https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/(job/[^?#]+)", re.I)
# Eightfold: /careers/job/ID, or Microsoft's /careers/apply?pid=/careers/job/ID.
EF_URL = re.compile(r"^https://([\w.-]+)/careers/(?:job/(\d+)|[^#]{0,200}?\bpid=\D{0,40}(\d+))", re.I)
# Hosts the reader never fetches, a redirect included: their terms or robots.txt
# say no to automated access (governmentjobs.com, see the NEOGOV note above).
# LinkedIn and Indeed too: members may share their job links (see src_tips), and
# their terms forbid scraping, so the Splat links to them and never reads them.
NO_FETCH = re.compile(r"^https?://(?:[\w-]+\.)*(?:governmentjobs\.com|linkedin\.com|indeed\.com|lnkd\.in)(?::\d+)?(?:[/?#]|$)",
                      re.I)
ORACLE_URL = re.compile(r"^(https://[\w-]+\.fa\.[\w-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[\w-]+/sites/([\w-]+)/job/(\d+)", re.I)
RIPPLING_URL = re.compile(r"^https://ats\.rippling\.com/([\w-]+)/jobs/([0-9a-f-]{36})", re.I)
SMARTR_URL = re.compile(r"^https://jobs\.smartrecruiters\.com/([\w-]+)/(\d+)", re.I)
BAMBOO_URL = re.compile(r"^https://([\w-]+)\.bamboohr\.com/careers/(\d+)", re.I)
WORKABLE_URL = re.compile(r"^https://apply\.workable\.com/(?:[\w-]+/)?j/([0-9A-F]{6,12})", re.I)


def fetch_posting(p: dict) -> dict | None:
    """The posting's text (and any pay or work mode stated beside it), from the
    employer's own system where we know its API, else the page's JobPosting data."""
    ref, url = p.get("ref"), p.get("url") or ""
    if ref and ref[0] == "gh":
        return _gh_read(ref[1], ref[2])
    if ref and ref[0] == "ef":
        return _eightfold_read(ref[1], ref[2], ref[3])
    if ref and ref[0] == "prov":
        return _oracle_read(PROVIDENCE, "CX_1", ref[1])
    if m := GH_URL.search(url):
        return _gh_read(m[1], m[2])
    if m := LEVER_URL.search(url):
        return _lever_read(m)
    if m := ASHBY_URL.search(url):
        return _ashby_read(m)
    if m := WD_URL.search(url):
        return _workday_read(m)
    if (m := EF_URL.search(url)) and EF_DOMAIN.get(m[1].lower()):
        return _eightfold_read(m[1].lower(), EF_DOMAIN[m[1].lower()], m[2] or m[3])
    if m := ORACLE_URL.search(url):
        return _oracle_read(m[1], m[2], m[3])
    if m := RIPPLING_URL.search(url):
        return _rippling_read(m)
    if m := SMARTR_URL.search(url):
        return _smartrecruiters_read(m)
    if m := BAMBOO_URL.search(url):
        return _bamboohr_read(m)
    if m := WORKABLE_URL.search(url):
        return _workable_read(m[1])
    if NO_FETCH.search(url):
        return None
    if url.startswith("https://"):
        return jsonld_posting(host_get(url, as_json=False, headers={"Accept": "text/html"}))
    return None


# Pay: a range of two dollar amounts. Annual 25K-1.5M or hourly 10-400, the high
# end at most 4x the low. Several ranges (pay zones) give the lowest low and the
# highest high; a range named as OTE or commission gives way to a base range;
# one-off amounts (a bonus, a stipend) are not ranges and are ignored. With no
# range, one amount named as the salary will do. Audit fixes 2026-11-09: Amazon
# writes "USA, WA, Seattle - 115,600.00 - 160,000.00 USD annually" with no $, and
# "$121,000/year to $163,700/year" puts a unit inside the range.
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_NUMC = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?)"  # comma-grouped: safe to read without a $
_PER = r"(?:\s*/\s*(?:year|yr|annum|hour|hr))?"
_TO = r"\s*(?:-|–|—|to|and)\s*"
PAY_RANGES = [
    # [ \t]* before K, not \s*: a newline there let the tail read the next line ("Equity...").
    re.compile(r"(?<![\w$])(?:USD\s*)?\$\s?" + _NUM + r"[ \t]*([kK])?" + _PER + r"(?:\s*USD)?" + _TO
               + r"(?:USD\s*)?\$?\s?" + _NUM + r"[ \t]*([kK])?([^\n.;]{0,40})", re.I),
    re.compile(r"(?<![\w$.,])" + _NUMC + r"()" + _TO + _NUMC + r"()(\s*(?:USD|annually|per year|a year)[^\n.;]{0,30})", re.I),
    re.compile(r"\bUSD\s*" + _NUMC + r"()" + _TO + _NUMC + r"()([^\n.;]{0,40})", re.I),
]
# (?![\d,]|\.\d): the amount ends where its number ends, so "$120,000 - $150,000"
# never reads as "$120" when the range itself was set aside (review, 2026-11-09).
PAY_ONE = re.compile(r"\b(?:salary|base pay|pay rate|pay|compensation|wage)\b([^$\n]{0,40}?)(?:USD\s*)?\$\s?" + _NUM
                     + r"(?![\d,]|\.\d)[ \t]*([kK])?(?!\s*(?:-|–|—|to)\s*(?:USD\s*)?\$?\s?\d)([^\n.;]{0,30})", re.I)
OTE = re.compile(r"\bOTE\b|on-?target|target earnings|commission|total (?:target )?compensation", re.I)
# Money that is not the salary: a sign-on bonus, equity, relocation (review, 2026-11-09).
NOT_BASE = re.compile(r"sign-?on|\bsigning\b|\bbonus|\bequity\b|\bRSUs?\b|\bstock\b|relocation|stipend", re.I)
NOT_BASE_AFTER = re.compile(r"^\s*(?:USD\s*)?(?:in\s+)?(?:sign-?on|signing\b|bonus|equity\b|RSUs?\b|stock\b|relocation|stipend)",
                            re.I)
SALARY_WORD = re.compile(r"salary|\bbase\b|\bpay\b|compensation|\bwages?\b|\brange\b", re.I)
EXCEPT = re.compile(r"(?:not including|excluding|exclusive of|does not include|\bplus\b)[^$:]{0,40}", re.I)


def not_base(label: str, tail: str) -> bool:
    """Is a range labeled as a bonus, equity or the like rather than pay? The label,
    less its "plus ..." and "not including ..." asides, decides when its last such
    word comes after its last salary word ("Sign-on bonus:" yes; "Base salary, plus
    bonus and equity:" no); a word right after the range decides unless it opens a
    new label ("... $150,000 Bonus: 10%")."""
    label = EXCEPT.sub("", label)
    nb = list(NOT_BASE.finditer(label))
    sw = list(SALARY_WORD.finditer(label))
    if nb and (not sw or nb[-1].start() > sw[-1].start()):
        return True
    return bool(NOT_BASE_AFTER.match(tail)) and not re.match(r"\s*[A-Za-z][\w -]{0,20}:", tail)


def _pay_unit(lo: float, hi: float) -> str | None:
    """The size settles the unit: no hourly rate tops $1,000 and no salary is under it.
    (A nearby "40 hours / week" once made $121,000 read as hourly.)"""
    if hi < 1000:
        return "/hr" if 10 <= lo and hi <= 400 else None
    return "/yr" if 25_000 <= lo and hi <= 1_500_000 else None


def pay_from_text(body: str) -> str | None:
    body = body or ""
    found = []  # (lo, hi, unit, ote)
    for rx in PAY_RANGES:
        for m in rx.finditer(body):
            lo = float(m[1].replace(",", "")) * (1000 if m[2] else 1)
            hi = float(m[3].replace(",", "")) * (1000 if m[4] else 1)
            if not lo or hi < lo or hi > lo * 4:
                continue
            before = body[max(0, m.start() - 120):m.start()]
            # The label is the clause just before the range ("Sign-on bonus: $20,000 -
            # $30,000"), or, when the range opens its line, the line above (a heading).
            parts = re.split(r"\n|[.!?]\s", before)
            label = parts[-1] if parts[-1].strip() or len(parts) < 2 else parts[-2]
            if not_base(label, m[5]):
                continue
            unit = _pay_unit(lo, hi)
            if unit:
                found.append((lo, hi, unit, bool(OTE.search(EXCEPT.sub("", label) + m[5]))))
    if not found:
        for m in PAY_ONE.finditer(body):
            gap, tail = m[1], m[4]
            # "Salary: up to $95,000" is a cap, and "pay ... a $30,000 sign-on bonus" is not pay.
            if not_base(gap, tail) or re.search(r"\bup to\b|\bmax", gap, re.I):
                continue
            v = float(m[2].replace(",", "")) * (1000 if m[3] else 1)
            unit = _pay_unit(v, v)
            if unit:
                return money(v, None, unit)
        return None
    base = [f for f in found if not f[3]] or found
    unit = "/yr" if any(f[2] == "/yr" for f in base) else "/hr"
    base = [f for f in base if f[2] == unit]
    return money(min(f[0] for f in base), max(f[1] for f in base), unit)


NUM_WORD = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "twelve": 12, "fifteen": 15}
_YN = r"(\d{1,2}|" + "|".join(NUM_WORD) + r")\s*(?:\(\d{1,2}\)\s*)?\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b"
YEARS = [re.compile(r"(?<![\d.$])" + _YN + r"(?:(?!degree|college)[^.;\n]){0,60}?\bexperience", re.I),
         # a requirement bullet that opens with the number: "3+ years of one of the following..."
         re.compile(r"^\s*(?:[-•*·]\s*)?(?:minimum (?:of )?|at least )?" + _YN, re.I | re.M)]
PREFERRED = re.compile(r"^\s*(?:preferred|desired|nice[- ]to[- ]have|bonus|pluses|additional|it'?s a plus)", re.I | re.M)


def years_from_text(body: str) -> int | None:
    """The years of experience the first requirement asks for, read before any
    "preferred" list. The first, not the highest: Amazon lists "5+ years ..., 7+
    years ..., 3+ years ..." with the main one first, and Microsoft lists degree
    paths "2+ years ... OR 3+ ... OR 5+" (the audit, 2026-11-09, scored the highest
    at 47 of 60 and the first at 56). Over 15 is not a requirement."""
    body = body or ""
    cut = PREFERRED.search(body)
    for part in ((body[:cut.start()], body[cut.start():]) if cut else (body,)):
        hits = sorted((m.start(), m.end(), int(m[1]) if m[1].isdigit() else NUM_WORD[m[1].lower()])
                      for rx in YEARS for m in rx.finditer(part))
        for start, end, n in hits:
            # About-us lines ("With over 10 years of experience, we...") and degree
            # lengths ("a 2 year degree") are not the requirement (review, 2026-11-09).
            line = part[max(part.rfind("\n", 0, start), 0):part.find("\n", end) % (len(part) + 1)]
            if (WE.search(line) and not YOU.search(line)) or DEGREE_AFTER.match(part, end) or not 0 <= n <= 15:
                continue
            return n
    return None


# About-us text: not a bullet, and it speaks as the company. Not "us" (it matched
# the country, "US"), and requirement words always win (review, 2026-11-09).
WE = re.compile(r"^\s*(?![-•*·])[^\n]*?\b(?:we|our|founded|history|since (?:19|20)\d\d)\b|^\W*with (?:over|more than)\b|"
                r"\bis an? (?:leader|leading)\b|\bleading provider\b", re.I)
YOU = re.compile(r"\b(?:you|your|candidates?|someone|require[sd]?|requirements?|qualifications?|expect(?:ed|s)?|"
                 r"minimum|must|should|need|looking for|seeking|ideal)\b", re.I)
DEGREE_AFTER = re.compile(r"\s*(?:degree|college|apprenticeship)\b", re.I)


_DN = r"(one|two|three|four|five|[1-5])"
DAYS = re.compile(r"\b(?:" + _DN + r"\s*(?:-|–|to|or)\s*)?" + _DN + r"\s*(?:\(\d\)\s*)?(?:x\s*days?|days?|x)\s*"
                  r"(?:a|per|each|every|/)?\s*(?:work\s*)?week", re.I)
OFFICE = re.compile(r"office|on-?site|in[- ]person|hybrid|campus|onsite", re.I)


def office_days(body: str) -> int | None:
    """Days a week in the office, when a sentence about the office says so; for a
    range ("2-3 days") the low end."""
    for line in re.split(r"\n|(?<=[.!?])\s+", body or ""):
        if not OFFICE.search(line):
            continue
        for m in DAYS.finditer(line):
            # Judge the clause that holds the count: "remote up to 2 days a week" counts
            # days at home; "3 days in office, 2 days remote" keeps its 3 (review, 2026-11-09).
            starts = [c.end() for c in CLAUSE.finditer(line, 0, m.start())]
            e = CLAUSE.search(line, m.end())
            if AT_HOME.search(line[(starts[-1] if starts else 0):(e.start() if e else len(line))]):
                continue
            w = (m[1] or m[2]).lower()
            return NUM_WORD.get(w) or int(w)
    return None


AT_HOME = re.compile(r"remote|from home|\bwfh\b|telework|\bvirtual(?:ly)?\b", re.I)
CLAUSE = re.compile(r"[,;:()]|\b(?:and|with|but|while|plus)\b", re.I)


def snippet(body: str, pattern: re.Pattern, n: int = 900) -> str:
    """The posting's sentences that match, in order, up to n characters."""
    keep, size = [], 0
    for line in re.split(r"\n|(?<=[.!?])\s+", body or ""):
        if pattern.search(line) and line not in keep:
            keep.append(line[:400])
            size += len(keep[-1])
            if size >= n:
                break
    return "\n".join(keep)[:n]


CLR_WORDS = re.compile(r"clearance|\bts/sci\b|top secret|\bsecret\b|polygraph|public trust|suitability|"
                       r"security investigation|\bcleared\b|\bsci\b", re.I)
# Sentences about this job's own arrangement, not "remote partners" or "onsite
# events" (the audit, 2026-11-09: 14 of 60 work modes were read from such lines).
MODE_WORDS = re.compile(
    r"\b(?:this|the) (?:role|position|job|opportunity)\b[^.\n]{0,60}\b(?:remote|hybrid|on-?site|in[- ]office|in[- ]person)"
    r"|\b(?:fully|100%) remote|\bremote[- ](?:role|position|first|eligible|opportunity|work)\b|\bwork (?:remotely|from home)"
    r"|\bhybrid (?:role|position|work|schedule|model|arrangement)\b|\bon-?site (?:role|position|daily|required|five)"
    r"|\bwork(?:ing)? (?:from|in|at) (?:the|our|an?) (?:office|site|campus)|\bdays? (?:a|per|each) week|\bin-office"
    r"|\btelework|\b(?:location|workplace|work) (?:type|model|arrangement)\b", re.I)
JEV_SURE = 0.8  # the least confidence for a work-mode or remote-limit answer to be shown
LIMIT_WORDS = re.compile(r"\breside|located in|live in|based in|states?\b|time ?zone|\b[ECMP][SD]?T\b|eastern|central|"
                         r"mountain|pacific|business hours|cannot (?:hire|employ)|eligible to work", re.I)
EDU_WORDS = re.compile(r"degree|bachelor|master'?s|ph\.?d|diploma|\bGED\b|\bB\.?S\.?\b|\bB\.?A\.?\b|education|"
                       r"equivalent (?:work )?experience", re.I)

SUB_LABEL = {"secops": "SecOps", "appsec": "AppSec", "cloudsec": "Cloud & infra security", "iam": "IAM",
             "grc": "GRC", "offensive": "Offensive", "support": "Help desk & support", "systems": "Systems",
             "network": "Network", "cloud": "Cloud & DevOps", "datacenter": "Data center",
             "project": "Project & program", "product": "Product",
             # The SE track's kinds: what the sales engineer sells. Keys are unique
             # across tracks, since the page looks a Kind up by its key alone.
             "sesec": "Security", "seinfra": "Cloud & infra", "seapps": "Software & data"}
READ_Q = {
    "clearance": {"type": "choice", "instructions": "This is from a US job posting. What US government security "
                  "clearance must the person hired hold?", "criteria": {
        "none": "No government security clearance is required. Protecting data, 'security' as the job's subject, "
                "drug tests, background checks and CJIS or other criminal-justice checks are not a clearance.",
        "obtain": "The person must be able to obtain a clearance, be eligible for one, or a clearance is preferred, "
                  "but an active clearance is not needed to start.",
        "public_trust": "A Public Trust position, suitability determination or other federal background "
                        "investigation that is not a Secret or Top Secret clearance.",
        "secret": "An active Secret clearance is required (not Top Secret).",
        "top_secret": "An active Top Secret clearance is required, with or without SCI access or a polygraph."}},
    "mode": {"type": "choice", "instructions": "This is from a job posting. Where does the person hired work?",
             "criteria": {
        "remote": "Fully remote, from home; no regular days in an office.",
        "hybrid": "Some days each week or month in an office or on site, the rest remote.",
        "onsite": "In an office, on site, in a data center, lab or field location on every workday.",
        "not_stated": "The text does not say where the work is done."}},
    "limit": {"type": "choice", "instructions": "This is from a remote US job posting. Where may the person hired "
              "live and work from?", "criteria": {
        "any_us": "Anywhere in the United States, or the text only asks for US work authorization or US residence.",
        "some_states": "The text says the hire must live in or near only some named US states, cities or a region, "
                       "or names states it cannot hire in. A location label on the posting, office addresses, or "
                       "pay ranges that differ by location do not limit where a hire may live.",
        "time_zone": "Anywhere in the US, but working hours must match a named US time zone.",
        "not_stated": "The text does not say."}},
    "education": {"type": "choice", "instructions": "This is from a job posting. What education does it require?",
                  "criteria": {
        "none": "No degree is asked for, or a high school diploma or GED is enough.",
        "or_exp": "A degree is asked for or preferred, but equivalent experience, training or certifications can "
                  "stand in for it, or the degree is only preferred.",
        "bachelor": "A bachelor's degree is required, with no alternative named.",
        "advanced": "A master's degree or doctorate is required.",
        "not_stated": "The text does not say what education is needed."}},
}
SUB_Q = {
    "cyber": {"type": "choice", "instructions": "Which kind of security job is this?", "criteria": {
        "secops": "Security operations: SOC analyst or engineer, detection and response, incident response, "
                  "threat intelligence or hunting, security monitoring, vulnerability management.",
        "appsec": "Application or product security: securing the software the company builds, secure code "
                  "review, AppSec, product security, security in the development lifecycle.",
        "cloudsec": "Cloud, network, endpoint or infrastructure security engineering or architecture: building "
                    "and running defenses for cloud platforms, networks and corporate systems.",
        "iam": "Identity and access management: IAM, identity engineering, privileged access, single sign-on, "
               "directory services.",
        "grc": "Governance, risk and compliance: GRC, security compliance, audit, risk, privacy, security "
               "policy, third-party risk.",
        "offensive": "Offensive security: penetration testing, red team, adversary emulation, security "
                     "research, exploit development.",
        "other": "Security work that fits none of these, spans several equally, or is software development of a "
                 "security product (agents, kernel drivers, features)."}},
    "eng": {"type": "choice", "instructions": "Which kind of infrastructure or IT job is this?", "criteria": {
        "support": "IT or technical support: help desk, service desk, desktop or end-user support, IT support "
                   "specialist, technical support engineer who troubleshoots for users or customers.",
        "systems": "Systems administration or engineering: Windows or Linux servers, endpoints and device "
                   "management, Microsoft 365, Active Directory, virtualization, storage, backup.",
        "network": "Network engineering or operations: routing, switching, firewalls, wireless, NOC, telecom.",
        "cloud": "Cloud, platform, DevOps or site reliability engineering: cloud infrastructure, Kubernetes, "
                 "infrastructure as code, CI/CD, running production services. Not building product software.",
        "datacenter": "Data center operations: data center technicians, server hardware, racking and cabling.",
        "other": "Work that fits none of these, including software development, engineering management of "
                 "product software, and enterprise application or platform ownership (ServiceNow, ERP)."}},
    "se": {"type": "choice", "instructions": "What does this sales engineer mainly sell?", "criteria": {
        "sesec": "Security products: endpoint, network, cloud, email or application security, software "
                 "supply-chain and container security, identity and access, SIEM, SOC and threat-detection "
                 "tools (Splunk security too), vulnerability management, data security.",
        "seinfra": "Cloud, infrastructure or IT products: cloud platforms, networking, wireless and telecom "
                   "services, storage and backup systems, servers, data center, AI infrastructure, "
                   "observability, IT service management and IT operations tools.",
        "seapps": "Software and data products: data platforms, databases, analytics, AI and developer tools, "
                  "fintech and payments, business and industry applications (CRM, ERP, fleet, field "
                  "operations).",
        "other": "Something that fits none of these, or several equally."}},
}
MANAGER_Q = {"type": "noul", "instructions": "Does the person hired for this job manage other employees "
             "(have direct reports)?"}
# The SE track's second check, on the posting's own words. Jev's track answer sees
# a title, a company and, from most boards, no text; the audit of the dry run
# (2026-11-10) found 43 of 210 se rows were not sales engineering: professional
# services and deployment after the sale ("First Time Deployment" solutions
# architects, "Job Function: Professional Services"), support, customer success,
# or an employer's own systems ("Solutions Architect, Global Tax Data").
PRESALES_Q = {"type": "noul", "instructions": "Is this a pre-sales job: working with a sales team before the sale "
              "to win new customers or deals (discovery, demos, proofs of concept, solution design for "
              "prospects)? Answer no for professional services, implementation, deployment or migration "
              "delivery after the sale, customer success or adoption, technical support, and for building or "
              "running the employer's own internal systems."}
PRESALES_WORDS = re.compile(
    r"pre-?\s?sales|post-?\s?sales|\bsales\b|account (executive|team|manager)s?|\bdemo|proofs? of (concept|value)|"
    r"\bpo[cv]s?\b|technical win|win rate|\bdeals?\b|pipeline|quota|opportunit|prospect|"
    r"professional services|implementation|deploy|migration|go-live|onboard|customer success|adoption|"
    r"\bsupport\b|escalation|statement of work|\bsow\b|utilization|billable|internal|our own|job function|"
    r"department|reports? (in)?to", re.I)


def jev_ask(state: dict, questions: dict, key: str) -> dict | None:
    """Answers to every question asked, each one valid, or None (see jev_call)."""
    def valid(a):
        return all((a.get(n) or {}).get("choice") in q["criteria"] if q["type"] == "choice"
                   else isinstance((a.get(n) or {}).get("noul"), (int, float)) for n, q in questions.items())
    d = jev_call(state, questions, key, valid)
    return {"answers": d["answers"], "tokens": (d.get("usage") or {}).get("input_tokens", 0)} if d else None


READ_BUDGET = 15 * 60  # seconds for the read pass; what is left waits for tomorrow
RUN_BUDGET = 40 * 60   # the unit allows 45 minutes; the read pass ends in time to write
_READ_END = [float("inf")]
_STUCK = [False]  # a read worker never finished: exit without waiting for it


def read_facts(p: dict, key: str, keep_body: bool = False) -> dict:
    """Everything worth knowing beyond the title, from the posting's own text.
    Never raises: a fact it cannot get is left out, and "error" says why."""
    if time.monotonic() > _READ_END[0]:
        return {"read": False, "error": "deadline", "tokens": 0}
    out: dict = {}
    try:
        return _read_facts(p, key, keep_body, out)
    except Exception as e:  # noqa: BLE001 - a parsing surprise in one posting never fails the run
        return {"read": False, "error": f"reader: {str(e)[:100]}", "tokens": out.get("tokens", 0)}


def _read_facts(p: dict, key: str, keep_body: bool, out: dict) -> dict:
    out["track"] = p.get("track")
    body, got = p.get("body") or "", {}
    if len(body) < READ_MIN:
        try:
            got = fetch_posting(p) or {}
        except Exception as e:  # noqa: BLE001 - one posting never fails the run
            out["error"] = str(e)[:120]
        body = (got.get("body") or "") if len(got.get("body") or "") > len(body) else body
    read = len(body) >= READ_MIN
    out["read"] = read
    pay = got.get("pay") or (pay_from_text(body) if read else None)
    if pay:
        out["pay"] = pay
    # The employer's own words first; an aggregator's label is the fallback (Foorilla's
    # "remote" had hidden the "Posting says hybrid" check, review 2026-11-09).
    yrs = years_from_text(body) if read else None
    if yrs is None:
        yrs = p.get("yrs")
    if yrs is not None:
        out["yrs"] = yrs
    if days := office_days(body):
        out["days"] = days
    agg = p.get("origin") == "aggregator"
    mode = got.get("mode") or (None if agg else p.get("mode"))
    tokens = 0
    failed = False
    head = {"title": p["title"], "company": p["company"]}

    def ask(state_text: str, qs: dict) -> dict | None:
        nonlocal tokens, failed
        if failed or not key:  # after one failure, nothing more is asked about this posting
            return None
        r = jev_ask({**head, "text": state_text}, qs, key)
        if not r:
            failed = True
            out["jev_failed"] = True  # whatever else went wrong, Jev still owes this posting answers
            out["error"] = out.get("error") or "jev"
            return None
        tokens += r["tokens"]
        return r["answers"]

    def choice(name: str, words: re.Pattern, q: dict, n: int = 900, hay: str | None = None):
        s = snippet(body if hay is None else hay, words, n)
        if not s:
            return "none" if name == "clearance" and read else None
        a = (ask(s, {name: q}) or {}).get(name) or {}
        conf = a.get("confidence")
        if name in ("mode", "limit") and isinstance(conf, (int, float)) and conf < JEV_SURE:
            return None  # shown on the card as a fact, so only when Jev is sure
        return a.get("choice")

    if body:
        # The title counts too: "Systems Administrator - TS/SCI" with a silent body is not "none".
        clr = choice("clearance", CLR_WORDS, READ_Q["clearance"], hay=f"{p['title']}\n{body}")
        if clr in READ_Q["clearance"]["criteria"]:
            out["clr"] = clr
        if not mode:
            mode = choice("mode", MODE_WORDS, READ_Q["mode"])
        if p.get("where") == "remote" or p.get("also_remote"):
            lim = choice("limit", LIMIT_WORDS, READ_Q["limit"])
            if lim in ("some_states", "time_zone", "any_us"):
                out["limit"] = lim
        edu = choice("education", EDU_WORDS, READ_Q["education"])
        if edu in ("none", "or_exp", "bachelor", "advanced"):
            out["edu"] = edu
    mode = mode or (p.get("mode") if agg else None)
    if mode in ("remote", "hybrid", "onsite"):
        out["mode"] = mode

    # Kind of work and people management: the title and the posting's opening.
    track = p.get("track")
    if track == "pm":
        out["sub"] = pm_sub(p["title"])
    qs = {"manager": MANAGER_Q} | ({"sub": SUB_Q[track]} if track in SUB_Q else {})
    a = ask(body[:700] or p.get("desc", "")[:700], qs)
    if a:
        sub = (a.get("sub") or {}).get("choice")
        if sub in SUB_LABEL:
            out["sub"] = sub
        mgr = (a.get("manager") or {}).get("noul")
        if isinstance(mgr, (int, float)) and mgr >= 0.6:
            out["mgr"] = True
    if track == "se" and read:
        # The sentences that say where the job sits; the title too, since "Customer
        # Success Solutions Architect" says it there.
        s = snippet(f"{p['title']}\n{body}", PRESALES_WORDS, 1500)
        v = ((ask(s or body[:1500], {"presales": PRESALES_Q}) or {}).get("presales") or {}).get("noul")
        if isinstance(v, (int, float)):
            out["presales"] = v >= 0.5
    out["tokens"] = tokens
    if keep_body:
        out["_body"] = body
    return out


def pm_sub(title: str) -> str:
    return "product" if PRODUCT.search(title) else "project"


def not_presales(p: dict, f: dict | None, tech_employer: bool) -> str | None:
    """An se row whose posting says it is not pre-sales (PRESALES_Q, read under
    se): "drop", or the track its title has without the SE track when that is
    cyber or eng ("Senior Solutions Architect, First Time Deployment Networking"
    is NVIDIA infrastructure delivery). None: it stays se. Jev chose se, not pm,
    so a PM title is dropped rather than published unconfirmed."""
    if p.get("track") != "se" or not f or f.get("track") != "se" or f.get("presales") is not False:
        return None
    alt = rule_track(p["title"], p.get("sec_topic", False), tech_employer, se=False)
    return alt if alt in ("cyber", "eng") else "drop"


def not_remote(p: dict, f: dict | None) -> bool:
    """A row on the page only as remote whose own posting says it is not: the
    employer's text or ATS field says hybrid or onsite (the reader's `mode`; an
    aggregator's own remote label never sets hybrid or onsite). That job is in an
    office somewhere outside the PNW, so it is not for this page. On 2026-11-11, 53
    of 984 remote rows, 46 of them Foorilla's remote flag ("Analyst I, Falcon
    Complete GovCloud (Hybrid)" listed as "USA - Remote"). A PNW row stays: it is
    local whatever its mode."""
    return p.get("where") == "remote" and bool(f) and f.get("mode") in ("hybrid", "onsite")


def se_sub(title: str, sec_topic: bool = False) -> str | None:
    """The SE Kind from the title alone, for a row the reader has not answered:
    security when the title or Foorilla's infosec topic says so, else unknown."""
    return "sesec" if sec_topic or CYBER.search(title) else None


def read_key(k: str, url: str = "") -> str:
    """Per posting: the row key and its URL, so a new requisition under the same
    company, title and place is read afresh."""
    return hashlib.sha1(f"{READ_VERSION}|{k}|{url}".encode()).hexdigest()[:16]


# A fetch that will fail the same way tomorrow (403, 404, 410, a refused URL) is
# cached as unread and tried again a week later; anything else, the next day.
PERMANENT = re.compile(r"HTTP Error (?:40[0-4]|410|422)|not a public")  # not 408 or 429: those pass
RETRY_UNREAD_DAYS = 7


def read_due(f: dict | None, p: dict) -> bool:
    """Read the posting today? When it was never read; when it could not be read a
    week ago; or when Jev has since filed it under a track whose Kind the reader
    asks (the SE track took rows read as cyber or eng, 2026-11-10): a Kind belongs
    to the track it was read under. Only on Jev's word, so a day Jev is down and
    the rules decide does not set off a re-read."""
    if f is None:
        return True
    if not f.get("read") and (age_days(f.get("seen")) or 0) >= RETRY_UNREAD_DAYS:
        return True
    return bool(p.get("jev") and f.get("track") and f["track"] != p["track"] and p["track"] in SUB_Q)


def fold_read(reads: dict, rk: str, facts: dict, track: str, err: str | None, jev_failed: bool) -> dict | None:
    """One posting's read into the cache. Returns the facts to show today, or None
    to show the cached read.
    A first read is cached unless it should be tried again tomorrow: Jev owed it
    answers, the pass ran out of time, or the fetch failed in a way that may pass
    (a 403, 404 or 410 is cached as unread and waits RETRY_UNREAD_DAYS).
    A re-read for a new track (read_due) that fell short keeps the good read, on
    today's page and in the cache; apply_read drops its old Kind (branch review,
    2026-11-10: a failed re-read had shown a Secret job as "No clearance"). One
    that may pass is tried again tomorrow; a refused or closed page, or one read
    short, takes the new track and any Kind Jev gave onto the old read, so it is
    not fetched every day."""
    old = reads.get(rk) or {}
    passing = jev_failed or err == "deadline" or bool(err and not PERMANENT.search(err))
    if old.get("read") and (err or not facts.get("read")):
        if not passing:
            reads[rk] = {**{k: v for k, v in old.items() if k != "sub"},
                         **{k: facts[k] for k in ("sub", "mgr") if k in facts}, "track": track}
        return None
    if not (err and (jev_failed or err == "deadline" or (not facts.get("read") and not PERMANENT.search(err)))):
        reads[rk] = {**facts, "seen": TODAY}
    return facts


def apply_read(j: dict, f: dict) -> None:
    """A posting's read facts onto its page row. Where the text was read, its own
    words replace the guesses made from the title and the company's name."""
    # A range read from the posting also beats a source's single figure ("$172K/yr"
    # where the posting says "$150,000.00 to $172,000.00", audit 2026-11-09).
    if f.get("pay") and (not j.get("salary") or ("–" in f["pay"] and "–" not in j["salary"])):
        j["salary"] = f["pay"]
    same_track = not f.get("track") or f["track"] == j.get("track")  # a Kind belongs to the track it was read under
    for k in ("sub", "mode", "days", "limit", "edu", "yrs"):
        if f.get(k) is not None and (k != "sub" or same_track):
            j[k] = f[k]
    if f.get("mgr"):
        j["mgr"] = True
    clr = f.get("clr")
    if clr and clr != "none":
        j["clr"] = clr
    if clr in ("secret", "top_secret"):
        j["clearance"] = True  # "No clearance" hides it, read in full or not
    if f.get("read"):
        j["read"] = True
        # The posting's word that no active clearance is needed replaces the guesses,
        # unless the title itself names one.
        if clr and clr not in ("secret", "top_secret") and not CLEARANCE.search(j.get("title") or ""):
            j["clearance"] = False
            j.pop("clearance_check", None)


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


# ── The employer's own date on an aggregator's copy ─────────────────────────
#
# An aggregator's date can move while the employer's posting keeps its own
# (2026-11-12: Foorilla had Vultr's "Technical Support Specialist" posted
# 2026-11-06, Ashby's publishedAt was 2026-03-31, the same posting id), so a
# months-old role passed the age rule and showed "posted 6 days ago". 134 live
# rows had a posted date later than the day the Splat first saw them.

def board_key(url: str | None) -> tuple[str, str] | None:
    """(board key, posting id) for a posting on an Ashby, Greenhouse or Lever
    board, keyed as company_check.py's board_of() keys its census ledger:
    "ashby:vultr", "greenhouse:okta", "lever-eu:siteimprove". Tokens without case;
    Greenhouse's EU boards share its one API, so they key as greenhouse."""
    url = url or ""
    if m := GH_URL.search(url):
        return f"greenhouse:{m[1].lower()}", m[2]
    if m := LEVER_URL.search(url):
        return f"{'lever-eu' if m[1] else 'lever'}:{m[2].lower()}", m[3].lower()
    if m := ASHBY_URL.search(url):
        return f"ashby:{urllib.parse.unquote(m[1]).lower()}", m[2].lower()
    return None


def load_board_ledger(path: Path | None = None) -> dict:
    """The census ledger's boards, read only. Missing or unreadable is an empty
    ledger, never fatal: the cards keep the dates their sources gave."""
    try:
        d = json.loads((path or BOARDS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    boards = d.get("boards") if isinstance(d, dict) else None
    return boards if isinstance(boards, dict) else {}


def board_date(url: str | None, ledger: dict) -> str | None:
    """The date the employer's own board gives the posting a link points to, or
    None. A date that is not a real day ("2026-02-30", a hand edit) is none: it
    would stop the run where the rules read it."""
    k = board_key(url)
    rec = ledger.get(k[0]) if k else None
    posts = rec.get("postings") if isinstance(rec, dict) else None
    p = posts.get(k[1]) if isinstance(posts, dict) else None
    d = p.get("d") if isinstance(p, dict) else None
    if not (isinstance(d, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)):
        return None
    try:
        datetime.fromisoformat(d)
    except ValueError:
        return None
    return d


def board_redate(p: dict, ledger: dict) -> bool:
    """An aggregator's copy of a posting on an employer's Ashby, Greenhouse or
    Lever board takes the board's date when it is older than the aggregator's
    (or the aggregator gave none); a date never moves forward. Returns whether
    it moved; rules_pass() then runs the age rule again on it.

    The employer's own board rows are left alone: their date already is the
    board's. So are members' tips: a tip lives 30 days from the day it was shared
    (src_tips), whatever the posting's age."""
    if p.get("origin") != "aggregator":
        return False
    d = board_date(p.get("url"), ledger)
    if not d or (p.get("posted") and p["posted"] <= d):
        return False
    p["posted"] = d
    return True


def drop_board_copies(cands: dict) -> int:
    """An aggregator's copy of a posting whose own board row is on the page under
    another title or place: the same posting id on the same board, so one job,
    and the board's row stays. rules_pass already keeps one row per
    company|title|where; this is the rest. Sampled 2026-11-12 on found boards, 5
    of 68 aggregator rows were the board's own row keyed differently: Pure
    Storage's "... Cyber Resilience - SLED West" was "... - West" on Foorilla,
    Temporal's US-remote role a Seattle copy, Fluidstack's Seattle-and-remote
    roles remote copies. A remote copy of a local row marks it also_remote, as
    the remote fold would. Returns how many went."""
    own: dict[tuple, dict] = {}
    for p in cands.values():
        if p.get("origin") == "company" and (bk := board_key(p.get("url"))):
            own.setdefault(bk, p)
    gone = 0
    for k in list(cands):
        p = cands[k]
        mine = own.get(board_key(p.get("url"))) if p.get("origin") == "aggregator" else None
        if mine is None:
            continue
        if p.get("where") == "remote" and mine.get("where") != "remote":
            mine["also_remote"] = True
        del cands[k]
        gone += 1
    return gone


def rules_pass(raw: list[tuple[str, dict]], board_ledger: dict) -> tuple[dict, int]:
    """The rules over every fetched row, one row per company|title|where: (the
    rows kept, how many aggregator rows the employer's own board date made too
    old). Cheap, and they decide everything Jev does not."""
    cands: dict[str, dict] = {}
    older_by_board = 0
    for sid, p in raw:
        if (not p["title"] or not p["url"] or not p["company"] or AGENCY.search(p["company"])
                or (MIDDLEMAN.search(p["url"]) and not p.get("gate"))):
            continue
        w = where(p["location"], p["remote"])
        if not w:
            continue
        max_age = REMOTE_MAX_AGE_DAYS if w == "remote" else MAX_AGE_DAYS
        a = age_days(p["posted"])
        if a is not None and a > max_age:
            continue
        track = rule_track(p["title"], p["sec_topic"], tech_source(sid), tech_source(sid) or sid == "amazon")
        level = rule_level(p["title"], p["hint"], track)
        if not track or not level:
            continue
        # The employer's own date, here and not once the rows are picked: an old
        # aggregator copy that won the pick below (or a tip's twin check, or the
        # remote fold) and dropped after took the row it beat with it, a fresh
        # copy or the employer's own row (review, 2026-11-12). Counted only when
        # the board's date is what made the row too old.
        if board_redate(p, board_ledger) and (age_days(p["posted"]) or 0) > max_age:
            older_by_board += 1
            continue
        p.update(where=w, track=track, level=level, source=sid,
                 clearance=bool(CLEARANCE.search(p["title"] + " " + p["desc"][:600])),
                 clearance_check=bool(FEDERAL.search(p["company"])))
        k = f"{norm(p['company'])}|{norm(p['title'])}|{w}"
        old = cands.get(k)
        # One row per role: the company's own board beats an aggregator's copy.
        if not old or (old["origin"] != "company" and p["origin"] == "company"):
            if old and not p["posted"]:
                p["posted"] = old["posted"]
            cands[k] = p
    return cands, older_by_board


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-jev", action="store_true")
    ap.add_argument("--no-read", action="store_true", help="skip reading postings (pay, clearance, office days...)")
    ap.add_argument("--dump-bodies", help="testing: write each read posting's text and facts to this path")
    ap.add_argument("--dump", help="also write the page JSON to this path (testing; works with --dry-run)")
    ap.add_argument("--only", help="comma list of source ids or families (greenhouse, workday, foorilla, ...)")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None
    # Yesterday's board census (company_check.py runs after the splat): a
    # posting's date is fixed, so a day old is fine. A board new today is read
    # later this morning and dates its cards from tomorrow, and is read as a
    # found board (auto_boards) from tomorrow too.
    board_ledger = load_board_ledger()

    prev = {}
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prev = {}
    prev_jobs: dict = prev.get("jobs", {})
    jev_cache: dict = prev.get("jev", {})

    sources = all_sources(only, board_ledger)  # the found boards from the census too (auto_boards)
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

    # Foorilla and Amazon pace themselves; the boards are separate hosts. 16, not
    # 8, since 2026-10-21 added 25 Workday tenants and the unit allows 45 minutes.
    with ThreadPoolExecutor(16) as ex:
        for sid, label, rows, err, secs in ex.map(run, sources):
            stats[sid] = {"label": label, "ok": err is None, "fetched": len(rows), "kept": 0,
                          "error": err, "secs": round(secs, 1)}
            raw += [(sid, r) for r in rows]
            print(f"{sid:32} {'OK ' if err is None else 'ERR'} {len(rows):6} {err or ''}", file=sys.stderr)

    # The found boards are not counted: they are many small sources on three
    # vendors' APIs, so one vendor down would read as the box offline, and a
    # lost network shows as plainly in the listed sources.
    core = [s for sid, s in stats.items() if not sid.startswith("auto:")]
    ok = sum(1 for s in core if s["ok"])
    if not only and ok < len(core) / 2:
        print(f"FAIL CLOSED: only {ok}/{len(core)} sources answered; leaving the page as it was", file=sys.stderr)
        return 2

    # Rules first: cheap, and they decide everything Jev does not. The employer's
    # own board dates go in there too, before Jev and the reader, so a row they
    # drop costs neither.
    cands, older_by_board = rules_pass(raw, board_ledger)
    board_copies = drop_board_copies(cands)
    tip_twins = drop_tip_twins(cands)
    folded = fold_remote_twins(cands)

    # Jev refines track and level on postings it has not seen.
    jev_stats = {"asked": 0, "failed": 0, "cached": 0, "dropped": 0, "pm_unconfirmed": 0, "se_unconfirmed": 0,
                 "tokens": 0, "error": None}
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
        if not apply_jev(p, ans):
            del cands[k]
            jev_stats["dropped" if ans else f"{p['track']}_unconfirmed"] += 1

    # Read each posting once: pay, clearance, office days, degree, years, sub-track.
    reads: dict = prev.get("read", {})
    run_facts: dict = {}  # today's answers, shown today even when not cached
    read_stats = {"asked": 0, "read": 0, "unread": 0, "failed": 0, "cached": 0, "abandoned": 0, "tokens": 0,
                  "not_presales": 0, "not_remote": 0, "tip_unverified": 0, "error": None}
    if args.no_jev or args.no_read or not key:
        read_stats["error"] = "skipped" if key else "no TYPESAFE_API_KEY"
    else:
        if jev_stats["asked"] >= 10 and jev_stats["failed"] > jev_stats["asked"] / 2:
            with _JEV_LOCK:
                _JEV_FAILS[0] = JEV_BREAK  # Jev is down today: read without it, cache nothing it owed

        todo = [(k, p) for k, p in cands.items() if read_due(reads.get(read_key(k, p["url"])), p)][:READ_MAX_PER_RUN]
        read_stats["cached"] = len(cands) - len(todo)
        bodies: dict = {}
        # The pass gets READ_BUDGET. A worker still stuck after it (a host that never
        # finishes, say) is left behind, not waited for: the run writes the page anyway.
        budget = max(60.0, min(READ_BUDGET, RUN_BUDGET - (time.monotonic() - _T0) - 180))
        _READ_END[0] = time.monotonic() + budget
        ex = ThreadPoolExecutor(16)
        futs = {ex.submit(read_facts, p, key, bool(args.dump_bodies)): (k, p) for k, p in todo}
        done, pending = wait(futs, timeout=budget + 120)
        ex.shutdown(wait=False, cancel_futures=True)
        read_stats["abandoned"] = len(pending)
        _STUCK[0] = any(f.running() for f in pending)
        for fut in done:
            k, p = futs[fut]
            facts = fut.result()
            rk = read_key(k, p["url"])
            read_stats["asked"] += 1
            read_stats["tokens"] += facts.pop("tokens", 0)
            if "_body" in facts:
                bodies[hashlib.sha1(k.encode()).hexdigest()[:12]] = {
                    "title": p["title"], "company": p["company"], "url": p["url"], "where": p["where"],
                    "track": p["track"], "body": facts.pop("_body"), "facts": dict(facts)}
            read_stats["read" if facts["read"] else "unread"] += 1
            err = facts.pop("error", None)
            jev_failed = facts.pop("jev_failed", False)
            if err:
                read_stats["failed"] += 1
                read_stats["error"] = read_stats["error"] or err
            shown = fold_read(reads, rk, facts, p["track"], err, jev_failed)
            if shown is not None:
                run_facts[rk] = shown
        if args.dump_bodies:
            Path(args.dump_bodies).write_text(json.dumps(bodies, ensure_ascii=False), encoding="utf-8")

    # Assemble: today's postings, plus a failed source's postings within grace.
    jobs: dict[str, dict] = {}
    keep_jev: set[str] = set()
    keep_read: set[str] = set()
    for k, p in cands.items():
        jid = hashlib.sha1(k.encode()).hexdigest()[:12]
        old = prev_jobs.get(jid, {})
        rk = read_key(k, p["url"])
        f = run_facts.get(rk) or reads.get(rk)  # today's read, when there was one, over an older cached one
        if rk in reads:
            keep_read.add(rk)
        keep_jev.add(jev_key(p))
        if alt := not_presales(p, f, tech_source(p["source"])):
            read_stats["not_presales"] += 1
            if alt == "drop":
                continue
            p["track"] = alt
        if not_remote(p, f):
            read_stats["not_remote"] += 1
            continue
        if not_verified(p, f):
            read_stats["tip_unverified"] += 1
            continue
        jobs[jid] = {
            "id": jid, "title": p["title"], "company": p["company"], "url": p["url"],
            "location": p["location"][:160], "where": p["where"], "track": p["track"], "level": p["level"],
            "posted": p["posted"], "first_seen": old.get("first_seen", TODAY), "last_seen": TODAY,
            "salary": p["salary"], "source": stats[p["source"]]["label"],
            "via": p["origin"], "clearance": p["clearance"], "checked": p["jev"],
        }
        # Only when true, so a day-old page and this one read the same.
        if p.get("gate"):
            jobs[jid]["gate"] = p["gate"]  # a member's tip only on LinkedIn or Indeed
        for flag in ("clearance_check", "also_remote"):
            if p.get(flag):
                jobs[jid][flag] = True
        if f:
            apply_read(jobs[jid], f)
        if p["track"] == "pm" and not jobs[jid].get("sub"):
            jobs[jid]["sub"] = pm_sub(p["title"])  # from the title, whatever the reader managed
        if p["track"] == "se" and not jobs[jid].get("sub") and (s := se_sub(p["title"], p["sec_topic"])):
            jobs[jid]["sub"] = s
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
                   "remote_twins_folded": folded, "tip_twins": tip_twins,
                   # aggregator copies too old by the employer's own board date (rules_pass)
                   "older_by_board": older_by_board,
                   # aggregator copies of a posting its own board row is on the page with (drop_board_copies)
                   "board_copies": board_copies,
                   "cyber": sum(1 for j in rows if j["track"] == "cyber"),
                   "entry": sum(1 for j in rows if j["level"] == "entry"),
                   "eng": sum(1 for j in rows if j["track"] == "eng"),
                   "pm": sum(1 for j in rows if j["track"] == "pm"),
                   "se": sum(1 for j in rows if j["track"] == "se"),
                   # "pnw" stays all three states so old counts and links mean what they did.
                   "pnw": sum(1 for j in rows if j["where"] in ("oregon", "pnw")),
                   "oregon": sum(1 for j in rows if j["where"] == "oregon"),
                   "remote": sum(1 for j in rows if j["where"] == "remote" or j.get("also_remote")),
                   "read": sum(1 for j in rows if j.get("read")),
                   "pay": sum(1 for j in rows if j.get("salary"))},
        "sources": [{"id": sid, **{k: v for k, v in s.items() if k != "secs"}} for sid, s in sorted(stats.items())],
        "jev": jev_stats,
        "reader": read_stats,
        "jobs": rows,
    }
    print(json.dumps({k: page[k] for k in ("counts", "jev", "reader")}, indent=1), file=sys.stderr)
    if args.dump:
        Path(args.dump).write_text(json.dumps(page, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.dry_run or only:
        print("dry run / partial run: nothing written", file=sys.stderr)
        return 0

    # Prune the Jev cache to what is still useful, then write both atomically.
    # Answers for dropped postings are kept a week, so a "neither" is not re-asked daily.
    jev_cache = {k: v for k, v in jev_cache.items() if k in keep_jev or (age_days(v.get("seen")) or 0) <= 7}
    reads = {k: v for k, v in reads.items() if k in keep_read or (age_days(v.get("seen")) or 0) <= 7}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for path, obj in ((STATE, {"jobs": jobs, "jev": jev_cache, "read": reads}), (OUT, page)):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    print(f"wrote {OUT} ({len(rows)} jobs)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    except BaseException:  # noqa: BLE001 - reported, then the exit below still runs
        import traceback
        traceback.print_exc()
    finally:
        if _STUCK[0]:
            # A read worker is still blocked in a fetch. Exiting normally (or on an
            # exception) would wait for that thread, so leave without waiting.
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(rc)
    sys.exit(rc)
