"""Rules tests for splat.py. Stdlib only.

    python3 infra/test_splat.py

Every case below is a real location or title string a source returned on
2026-10-02, most of them a bug the first sample run shipped.
"""

import importlib.util
import sys
import time
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
    ("Portland, Oregon, USA", False, "oregon"),
    ("Boise, ID", False, "pnw"),
    # Listed in both states: Oregon's (2026-10-21, the page leans Portland).
    ("United States, Washington, Redmond; United States, Oregon, Hillsboro", False, "oregon"),
    ("Umatilla, Oregon, USA", False, "oregon"),
    ("US, Oregon, Hillsboro", False, "oregon"),
    ("Vancouver, WA", False, "oregon"),       # Portland metro
    ("Redmond, OR", False, "oregon"),
    ("Redmond, WA", False, "pnw"),
    ("US-OR-Remote", True, "oregon"),
    ("Salem, MA", False, None),
    ("Medford, MA", False, None),
    ("Oregon, WI", False, None),             # a Workday facet on 2026-10-21
    ("Vancouver, BC, Canada", False, None),
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
    ("Hillsboro, OR 97124", False, "oregon"),
    ("Remote - Seattle", True, "pnw"),
    # A Foorilla US-only query row with a bare city, after the source tags it.
    ("San Francisco (remote) · United States", True, "remote"),
    # City names inside other words, or namesakes, shown as Oregon or PNW on 2026-11-07:
    ("Jerusalem, Israel", False, None),
    ("USA, CA, Camp Pendleton (Bldg 110203), United States", False, None),
    ("US - Trenton, NJ (TDY)", False, None),
    ("Richland County", False, None),
    ("Tel Aviv, Israel; Portland, OR", False, "oregon"),
    # And the real ones still are:
    ("Salem | DOJ | Justice Building", False, "oregon"),
    ("Pendleton, OR", False, "oregon"),
    ("Bend", False, "oregon"),
    ("Renton, WA", False, "pnw"),
    ("Richland, WA", False, "pnw"),
]

MONEY = [
    ((120000, 160000, "/yr"), "$120K–$160K/yr"),
    ((17, 20, "/yr"), "$17–$20/hr"),   # an hourly range in a yearly field
    ((None, None, "/yr"), None),
]

ASHBY_PAY = [
    ("$207K – $244K • Offers Equity • This role is also eligible for medical benefits, 401(k) plan, and other company perk programs.",
     "$207K – $244K"),
    ("$175K – $325K • Offers Equity • Multiple Ranges", "$175K – $325K"),
    ("$120K – $150K", "$120K – $150K"),
    ("Offers Equity", None),
    (None, None),
]

AGENCY = [
    ("TEK Systems", True), ("TEKsystems", True), ("Bright Vision Technologies", True),
    ("Robert Half", True), ("Insight Global", True),
    ("Cambia Health Solutions", False), ("Tektronix", False), ("Modis Inc", True), ("Systematic Inc", False),
]

FEDERAL = [
    ("Peraton", True), ("General Dynamics Information Technology", True), ("CACI International Inc", True),
    ("ECS", True), ("Leidos", True),
    ("Huntington National Bank", False), ("Specsavers", False), ("Microsoft", False), ("Deloitte", False),
]


def check_fold_remote_twins() -> int:
    def p(where, posted=None):
        return {"where": where, "posted": posted}
    cands = {"cisco|senior security engineer|pnw": p("pnw"),
             "cisco|senior security engineer|remote": p("remote", "2026-11-05"),
             "acme|sre|remote": p("remote"),
             "acme|sre ii|oregon": p("oregon")}
    folded = splat.fold_remote_twins(cands)
    want = {"cisco|senior security engineer|pnw": {"where": "pnw", "posted": "2026-11-05", "also_remote": True},
            "acme|sre|remote": {"where": "remote", "posted": None},
            "acme|sre ii|oregon": {"where": "oregon", "posted": None}}
    if folded != 1 or cands != want:
        print(f"fold_remote_twins -> {folded}, {cands!r}")
        return 1
    return 0

TRACK = [
    # Oregon public-sector and local titles the rules dropped on 2026-10-21.
    ("Information Systems Specialist 5", "eng"),
    ("DDS IT User Support Analyst (Information System Support Specialist 4)", "eng"),
    ("Information Technology Manager 2", "eng"),
    ("Senior IT Oversight Analyst", "eng"),
    ("Senior Database Administrator", "eng"),
    ("Senior Network Engineeer", "eng"),
    ("Senior Information Risk Officer", "cyber"),
    ("Part Time Faculty Interest Pool - Computer Information Systems", None),
    ("Software Developer", None),
    # Providence's "IS" (Information Services) department tag, 2026-10-21.
    ("IS Customer Support Technician - On Site", "eng"),
    ("IS Support Administrator - On Site", "eng"),
    ("Senior Security Engineer IS - Identity and Access Management, Remote", "cyber"),
    ("Data Engineer I IS - Remote", None),
    ("Customer Support Technician", None),
    ("Advanced Specialty Staff RN - Medical Forensic", None),
    ("Dispatcher - Security", None),
    ("Engineer, Senior, Site and Database Reliability", "eng"),   # TriMet
    ("Database Marketing Manager", None),
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
    # Since 2026-11-03 these are the PM track (see TRACK_PM), not out and not cyber.
    ("Senior Product Manager, Platform", "pm"),
    ("Technical Program Manager - Security", "pm"),
    ("Product Security Manager", "cyber"),
    ("Scrum Master", None),
    # Shift hours and the TPM chip are not project managers (2026-11-03 review):
    # these read as they did before the PM track.
    ("SOC Analyst - Swing Shift 3 PM - 11 PM", "cyber"),
    ("NOC Technician (Nights 7 PM - 7 AM)", "eng"),
    ("Help Desk Analyst - AM/PM Shifts", "eng"),
    ("IT Support Technician - 2nd Shift (2:30 PM - 11 PM)", "eng"),
    ("Data Center Technician, Night Shift (6 PM - 6 AM)", "eng"),
    ("Service Desk Analyst - 2nd Shift (2:00PM - 10:30PM)", "eng"),
    ("Firmware Security Engineer - TPM", "cyber"),
    ("TPM Engineer", None),
    ("Delivery Manager, Cloud", "eng"),                 # not a PM; Infra & IT as before
    # "PM" or a PM team name that is not the job (the third review).
    ("SOC Supervisor - PM", "cyber"),
    ("Network Operations Center Supervisor - PM/Nights", "eng"),
    ("Data Center Operations Manager - PM", "eng"),
    ("PM Security Officer", None),
    ("SAP PM/EAM Functional Lead", None),
    ("TPM Facilitator", None),
    ("Systems Administrator, Project Management Tools", "eng"),
    ("Technical Writer, Product Management", None),
    ("Help Desk Lead (2 PM - 10 PM)", "eng"),            # only CLOCK keeps this one out of pm
    ("Semiconductor Equipment Engineer", None),
    ("Sr. Design Validation Product Engineer, DEG", None),
]

# Titles Foorilla filed under its infosec topics, where the title alone is not
# enough. The rule lets a role-shaped title through; Jev makes the real call.
TRACK_SEC_TOPIC = [
    # A security vendor's sales engineers are the SE track since 2026-11-10.
    ("Principal Solutions Engineer", "se"),
    ("Sr Solution Architect- Sec", "se"),                 # CDW, cyber until 2026-11-10
    ("Solutions Architect", "se"),
    ("Security Engineer III", "cyber"),
    ("Mid-Level Data Engineer", "cyber"),
    ("Account Executive", None),
    # A PM under the infosec topics is a security PM (2026-11-03), not cyber.
    ("Product Manager", "pm"),
    ("Program Lead", "pm"),
    ("Marketing Program Manager", None),
]

# The PM track (2026-11-03): (title, tech employer, expected). A PM title counts
# when it names technology work, or when the source is a tech employer's board.
TRACK_PM = [
    ("IT Project Manager", False, "pm"),
    ("Technical Program Manager", False, "pm"),
    ("Sr. TPM, Cloud Infrastructure", False, "pm"),
    ("Security Program Manager", False, "pm"),
    ("Product Owner - Identity Platform", False, "pm"),
    ("Associate Product Manager, Data", False, "pm"),
    ("Project Coordinator - Information Technology", False, "pm"),
    ("Clinical Applications Project Manager - Epic", False, "pm"),   # healthcare IT, despite "clinical"
    ("Senior Product Manager, Payments", True, "pm"),                # at Stripe
    ("Senior Product Manager, Payments", False, None),               # at a bank, the title has to say so
    ("Program Manager", True, "pm"),
    ("Program Manager", False, None),
    ("Product Manager, Footwear", False, None),                      # Nike
    ("Construction Project Manager", True, None),
    ("Infrastructure Construction Project Manager", False, None),
    ("Physical Security Program Manager", False, None),
    ("Marketing Program Manager", True, None),
    ("Clinical Program Manager", False, None),
    ("Facilities Project Manager - Data Center", False, None),
    ("Product Marketing Manager, Security", True, None),
    ("Scrum Master", True, None),
    ("Agile Coach", True, None),
    ("Director, Program Management", True, "pm"),                     # track yes; rule_level drops it
    # The 2026-11-03 review. Security words count as technology at any employer
    # (these were cyber, or dropped, before the fix).
    ("IAM Product Owner", False, "pm"),
    ("Product Owner, SIEM", False, "pm"),
    ("Program Lead, Vulnerability Management", False, "pm"),
    ("SOC Program Manager", False, "pm"),
    ("GRC Program Manager", False, "pm"),
    # Nothing outranks construction, marketing, sales or scrum.
    ("Construction Project Manager - Cloud Data Centers", True, None),
    ("Cloud Marketing Program Manager", True, None),
    ("Software Sales Program Manager", True, None),
    ("IT Project Manager / Scrum Master", False, None),
    # Healthcare IT without the word Epic.
    ("Clinical Informatics Project Manager", False, "pm"),
    ("Clinical Applications Project Manager", False, "pm"),
    ("Cerner Project Manager", False, "pm"),
    ("Nursing Informatics Project Manager", False, "pm"),
    # Amazon is not a tech source; its technical PMs say so.
    ("Sr. Program Manager, Perishable Distribution Strategy, Worldwide Grocery Stores (WWGS) Logistics", False, None),
    ("Sr. Product Manager - Tech, Alexa Shopping", False, "pm"),
    ("PM-T, Ads Measurement", False, "pm"),
    # A tech employer's non-technology PM.
    ("Supply Chain Program Manager, Memory & Storage", True, None),
    # Team names are a PM job only with no other job noun.
    ("Senior Manager, Product Management", True, "pm"),
    ("Software Engineer, Project Management Tools", True, None),
    # The second review (2026-11-03).
    # Other NOT_ROLE words give way to a technology word; construction and the like do not.
    ("Senior Product Manager, Manufacturing Cloud", True, "pm"),
    ("Cloud Data Warehouse Program Manager", False, "pm"),
    ("IT Project Manager - Manufacturing Systems", False, "pm"),
    ("IT Program Manager, ServiceNow Field Service Management", False, "pm"),
    ("Industrial Cybersecurity Program Manager", False, "pm"),
    ("Cybersecurity Program Manager - Electrical Grid", False, "pm"),
    ("IT Project Manager / Business Analyst", False, "pm"),
    ("Software Program Manager - Linux Drivers", False, "pm"),
    ("Data Loss Prevention Program Manager", False, "pm"),
    ("Epic Project Manager (RN preferred)", False, "pm"),
    ("Clinical Application Project Manager", False, "pm"),
    ("Clinical Program Manager, Digital Health", False, None),           # care program; "digital" is not health IT
    ("Behavioral Health Program Manager", False, None),
    # A tech employer's product managers build software, whatever the domain.
    ("Senior Product Manager, Real Estate Transactions", True, "pm"),     # Zillow
    ("Senior Product Manager, Workforce Management", True, "pm"),         # Salesforce
    ("Product Manager, Shipping & Logistics", True, "pm"),                # OfferUp
    # Words that only look like technology in a PM title.
    ("Knowledge Management Program Manager", False, None),
    ("Food Insecurity Program Coordinator", False, None),
    ("Food Security Program Manager", False, None),
    ("Breast and Cervical Cancer Early Detection Program Manager", False, None),
    ("Leak Detection and Repair Program Manager", False, None),
    ("Forensic Evaluation Program Manager", False, None),
    ("Vulnerable Populations Program Manager", False, None),
    ("Behavioral Threat Assessment Program Manager", False, None),
    ("Product Manager - Windows & Doors", False, None),
    ("Surgical Tech Program Manager", False, None),
    ("Mobile Crisis Program Manager", False, None),
    ("Provider Network Program Manager", False, None),
    ("Behavioral Health Integration Program Manager", False, None),
    # TPMs working in Engineering or on a Developer Platform are still TPMs.
    ("Sr. TPM - Data Engineering", False, "pm"),
    ("TPM, Developer Infrastructure", False, "pm"),
    ("Senior PM, Developer Platform", False, "pm"),
    ("Sr. TPM, Platform Engineering", False, "pm"),
    ("Senior TPM, Cloud Architecture", False, "pm"),
    ("Engineering Program Management Lead", False, "pm"),
    ("TPM - Security Engineering", False, "pm"),
    # PMO roles are PM roles.
    ("IT PMO Analyst", False, "pm"),
    ("Project Management Analyst, IT", False, "pm"),
    ("Program Management Analyst, Cybersecurity", False, "pm"),
    # The TPM chip and maintenance, beside any job noun.
    ("Hardware Security Specialist - TPM", False, "cyber"),          # the chip; cyber as before
    ("TPM Specialist", True, None),
    ("TPM Coordinator", True, None),
    # The third review: engineering disciplines, and words with a non-IT sense.
    ("Engineering Project Manager - Public Works", False, None),
    ("Electrical Engineering Project Manager", False, None),
    ("Engineering Program Manager", False, "pm"),
    ("Aviation Security Program Manager", False, None),
    ("Security Officer - Swing/PM", False, None),
    ("Financial Security Program Manager", False, None),
    ("Energy Storage Program Manager", False, None),
    ("Green Infrastructure Program Manager", False, None),
    ("Technical Assistance Program Manager", False, None),
    ("Records and Information Management Program Manager", False, None),
    ("Network Development Program Manager", False, None),
    ("Executive Business Partner, Product Management", True, None),
    ("Product Managers - Cloud Security", False, "pm"),
    ("Security Program Managers", False, "pm"),
    ("Security TPM", False, "pm"),
    # A TPM outranks an operations word; a plain program manager does not (Amazon, live 2026-11-03).
    ("Principal Technical Program Manager, Customer Logistics Security (CLS)", False, "pm"),
    ("Senior Program Manager, Business Operations, Customer Logistics Security (CLS)", False, None),
]

# The SE track (2026-11-10): (title, sa employer, expected). sa employer: a
# technology employer's own board or Amazon's, where a solutions architect sells.
# Jev confirms every se row; these are what the rules propose.
TRACK_SE = [
    ("Sales Engineer", False, "se"),                      # whatever it sells; Jev decides
    ("Senior Sales Engineer - Enterprise", False, "se"),
    ("Channel Sales Engineer", False, "se"),
    ("Sales Engineer I", False, "se"),
    ("Pre-Sales Solutions Architect", False, "se"),
    ("Presales Solutions Engineer - SecOps/SIEM", False, "se"),
    ("Solutions Engineer", False, "se"),
    ("Associate Solutions Engineer", False, "se"),
    ("Solution Consultant - Marketing Cloud", False, "se"),   # "marketing" gives way to "cloud"
    ("Sales Engineering Manager, West", False, "se"),
    ("Industrial Cybersecurity Sales Engineer", False, "se"),
    ("Customer Engineer, Security, Google Cloud", False, "se"),
    # Solutions architects sell on a vendor's board or when the title names the sale.
    ("Solutions Architect", True, "se"),
    ("Partner Solutions Architect, Global Partner Development", True, "se"),
    ("Senior Networking SA, AWS Security & Networking - North America", True, "se"),
    ("Sr. Manager, Solutions Architecture - Communication Developer Services", True, "se"),
    ("Senior Solutions Architect, AI Infrastructure", True, "se"),           # NVIDIA, Infra & IT until 2026-11-10
    ("Partner Solutions Architect", False, "se"),
    ("Solutions Architect", False, None),                 # a bank's or an agency's own systems: as before
    ("Enterprise Solutions Architect", False, None),
    ("Cloud Solutions Architect", False, "eng"),          # as before
    ("Principal Infrastructure Solutions Architect", False, "eng"),
    # Not technology sales engineers, or not sales engineers.
    ("Sales Engineer, HVAC Systems", False, None),
    ("Inside Sales Engineer - Industrial Automation", False, None),
    ("Mechanical Sales Engineer", False, None),
    # "Technical" in "Technical Sales" is the job's name, not a technology (branch review).
    ("Technical Sales Engineer - Industrial Valves", False, None),
    ("Technical Sales Specialist - Industrial Coatings", False, None),
    ("Technical Sales Engineering Manager - Industrial", False, None),
    ("Technical Sales Engineer - Industrial Cybersecurity", False, "se"),
    ("Technical Sales Engineer", False, "se"),
    ("Sales Engineer - Physical Security", False, None),
    ("Recruiter, Sales Engineering", False, None),
    ("Technical Recruiter - Sales Engineering", True, None),
    ("Account Executive, Security", False, None),
    ("Customer Engineer", False, None),
    ("Customer Engineer - Field Service", False, None),
    ("Delivery Consultant - Application Modernization, AWS Professional Services", True, None),
    ("Sr. Technical Account Manager, Sr. TAM", True, None),
    ("SAS Programmer", True, None),
    ("Solutions Engineering Program Manager", False, "pm"),   # a PM first
    # The vendors' other names for a sales engineer read as before; Jev may move them (APPLY_JEV).
    ("Systems Engineer - Enterprise", True, "eng"),
    ("Principal Security Field Engineer", True, "cyber"),
]

# In an SE title "Architect" is the job, not the grade.
LEVEL_SE = [
    ("Solutions Architect", "mid"),
    ("Senior Solutions Architect", "senior"),
    ("Principal Solutions Architect, Strategic Accounts", "staff"),
    ("Senior Account SA, AGS Tech", "senior"),
    ("Sales Engineer", "mid"),
    ("Sales Engineer I", "entry"),
    ("Sales Engineering Manager", "senior"),
    ("Director, Sales Engineering", None),
    ("Pre-Sales Architect", "mid"),                       # any Architect in an SE title is the job
    ("Technical Sales Architect II", "mid"),
    ("Senior Pre-Sales Architect", "senior"),
]

# fold_read: a posting's read into the cache. (cached, today's facts, error, Jev
# failed) -> (shown today: "new" or "cached", what the cache holds after).
GOOD = {"read": True, "track": "cyber", "sub": "secops", "clr": "secret", "pay": "$150K–$180K/yr", "seen": "2026-11-09"}
FOLD_READ = [
    # A first read is cached and shown.
    (None, {"read": True, "track": "se", "sub": "sesec"}, None, False,
     ("new", {"read": True, "track": "se", "sub": "sesec", "seen": "TODAY"})),
    # A first read Jev still owes answers: shown today, not cached.
    (None, {"read": True, "track": "se"}, "jev", True, ("new", None)),
    # A good re-read replaces the old read.
    (GOOD, {"read": True, "track": "se", "sub": "sesec"}, None, False,
     ("new", {"read": True, "track": "se", "sub": "sesec", "seen": "TODAY"})),
    # A re-read that fails in a way that may pass: the good read stands, tried again tomorrow.
    (GOOD, {"read": False, "track": "se"}, "HTTP Error 503: Service Unavailable", False, ("cached", GOOD)),
    (GOOD, {"read": True, "track": "se"}, "jev", True, ("cached", GOOD)),
    (GOOD, {"read": False, "track": "se"}, "deadline", False, ("cached", GOOD)),
    # A refused or closed page: the good read takes the new track, without its old Kind.
    (GOOD, {"read": False, "track": "se", "mgr": True}, "HTTP Error 404: Not Found", False,
     ("cached", {"read": True, "track": "se", "clr": "secret", "pay": "$150K–$180K/yr", "seen": "2026-11-09",
                 "mgr": True})),
]

# not_presales: an se row whose posting says it is not pre-sales goes back to the
# track its title had before the SE track, or off the page. (title, track now,
# cached facts, tech employer) -> expected.
NO_PRE = {"track": "se", "presales": False, "read": True}
NOT_PRESALES = [
    ("Senior Solutions Architect, First Time Deployment Networking InfiniBand - NVIS", "se", NO_PRE, True, "eng"),
    ("Global Solutions Architect - AI Security", "se", NO_PRE, True, "cyber"),
    ("Solution Architect/Senior Solution Architect - Data 360", "se", NO_PRE, True, "drop"),
    ("Sales Engineer", "se", NO_PRE, True, "drop"),
    ("Sales Engineer", "se", {**NO_PRE, "presales": True}, True, None),
    ("Sales Engineer", "se", {"track": "se", "read": True}, True, None),          # not asked (Jev down): stays
    ("Sales Engineer", "se", None, True, None),                                    # not read yet
    ("Sales Engineer", "se", {**NO_PRE, "track": "cyber"}, True, None),            # read under another track
    ("Security Engineer", "cyber", NO_PRE, True, None),
]

# not_remote: a remote row whose own posting says hybrid or onsite leaves the page
# (2026-11-11). (where, cached facts) -> dropped?
NOT_REMOTE = [
    ("remote", {"read": True, "mode": "hybrid"}, True),
    ("remote", {"read": True, "mode": "onsite"}, True),
    ("remote", {"read": True, "mode": "remote"}, False),
    ("remote", {"read": False}, False),                 # not read: the listing's word stands
    ("remote", None, False),
    ("oregon", {"read": True, "mode": "hybrid"}, False),  # local whatever its mode
    ("pnw", {"read": True, "mode": "onsite"}, False),
]
# MIDDLEMAN: links through email-gated job sites are dropped; the employer's own are not.
MIDDLEMAN_URLS = [
    ("https://www.dice.com/job-detail/1234", True),
    ("https://haystackapp.io/jobs/it-manager", True),
    ("https://www.linkedin.com/jobs/view/4012345678", True),
    ("https://www.indeed.com/viewjob?jk=abc", True),
    ("https://www.ziprecruiter.com/c/CACI/Job/IT-Manager", True),
    ("https://careers.caci.com/global/en/job/123", False),
    ("https://job-boards.greenhouse.io/okta/jobs/7103874", False),
    ("https://jobicy.com/jobs/152929-security-engineer-cloud", False),   # labelled "Listed on Jobicy", no email gate
    ("https://example.com/dice.com/x", False),
    ("https://notdice.com/x", False),
]

# apply_jev guard for a non-PM se row (branch review): (title, rules track, Jev track) -> kept track or None.
CROSS_CONFIRM = [
    ("Solutions Consultant", "se", "pm", None),       # Jev said pm, not se: not published as se
    ("Technical Program Manager", "pm", "se", None),
    ("Domain Consultant - Cortex", "cyber", "se", "se"),  # Palo Alto's specialist sales engineers
    ("Cloud Architect, Product Management", "eng", "pm", "eng"),
]

# read_due: is a posting read today? (cached facts, row) -> expected.
READ_DUE = [
    (None, {"track": "se", "jev": True}, True),                                          # never read
    ({"read": True, "track": "se", "seen": "2026-11-09"}, {"track": "se", "jev": True}, False),
    # Jev moved it into se since it was read as cyber: its Kind is asked again.
    ({"read": True, "track": "cyber", "seen": "2026-11-09"}, {"track": "se", "jev": True}, True),
    # Not on the rules' word alone (Jev down that day).
    ({"read": True, "track": "cyber", "seen": "2026-11-09"}, {"track": "eng", "jev": False}, False),
    # The PM Kind comes from the title, so a move into pm needs no read.
    ({"read": True, "track": "eng", "seen": "2026-11-09"}, {"track": "pm", "jev": True}, False),
]

# apply_jev: what main() does with Jev's answer. (rules' track, rules' level,
# Jev's track, Jev's level, title) -> (final track, final level), or None when
# Jev drops the row. A title that states its level keeps it; whether "Manager"
# states one depends on whether either side calls the job a PM.
APPLY_JEV = [
    ("Technical Program Manager", "pm", "mid", "eng", "senior", ("eng", "senior")),
    ("IT Project Manager", "pm", "mid", "eng", "senior", ("eng", "senior")),
    ("Product Manager", "cyber", "senior", "pm", "mid", ("pm", "mid")),   # a Foorilla infosec PM
    ("Senior Product Manager", "pm", "senior", "pm", "mid", ("pm", "senior")),
    ("Senior Security Engineer", "cyber", "senior", "cyber", "mid", ("cyber", "senior")),
    ("Network Manager", "eng", "senior", "eng", "mid", ("eng", "senior")),
    ("DevOps Engineer", "eng", "mid", "eng", "entry", ("eng", "entry")),
    ("Program Manager, Store Operations", "pm", "mid", "neither", "mid", None),
    # Jev cannot make a non-PM title a PM (the 2026-11-03 audit); the rules' track stands.
    ("Cloud Architect, Product Management", "eng", "senior", "pm", "senior", ("eng", "senior")),
    ("RAI Industry Specialist, Amazon Privacy", "cyber", "mid", "pm", "senior", ("cyber", "senior")),
    # No answer from Jev yet: a pm or se row waits, cyber and eng rows publish on the rules.
    ("Food Systems Program Manager", "pm", "mid", None, None, None),
    ("Sales Engineer", "se", "mid", None, None, None),
    ("SOC Analyst II", "cyber", "mid", None, None, ("cyber", "mid")),
    # The SE track (2026-11-10). Jev confirms, moves out, or drops a non-technology one.
    ("Sales Engineer", "se", "mid", "se", "senior", ("se", "senior")),
    ("Sales Engineer", "se", "mid", "neither", "mid", None),                  # valves, say
    ("Technical Solutions Engineer", "se", "mid", "eng", "mid", ("eng", "mid")),  # support after the sale
    ("Information Security Solutions Engineer II", "se", "mid", "cyber", "mid", ("cyber", "mid")),
    # Into se only from a sales-engineer title: Palo Alto's "Systems Engineer" yes,
    # a security engineer no.
    ("Systems Engineer - Enterprise", "eng", "mid", "se", "mid", ("se", "mid")),
    ("Principal Security Field Engineer", "cyber", "staff", "se", "senior", ("se", "staff")),
    ("Senior Security Engineer", "cyber", "senior", "se", "senior", ("cyber", "senior")),
    # "Solutions Architect" is the job once either side calls it se: Jev's level stands.
    ("Solutions Architect", "se", "mid", "se", "senior", ("se", "senior")),
    ("Cloud Solutions Architect", "eng", "senior", "se", "mid", ("se", "mid")),
    ("Senior Solutions Architect", "se", "senior", "se", "mid", ("se", "senior")),
]

# Only technology employers' own boards are tech sources (not Amazon, the
# aggregators or the telecoms).
TECH_SOURCE = [
    ("greenhouse:stripe", True), ("workday:nvidia", True), ("workday:nike", False), ("workday:tmobile", False),
    ("eightfold:autodesk", True), ("eightfold:lamresearch", True), ("eightfold:fortive", False),
    ("providence", False), ("amazon", False), ("himalayas", False),
    ("foorilla", False), ("workable", False),
]

# PM titles carry "Manager" as the job, not the grade.
LEVEL_PM = [
    ("Product Manager", "mid"),
    ("Senior Product Manager", "senior"),
    ("Associate Product Manager", "entry"),
    ("Principal Product Manager", "staff"),
    ("Technical Program Manager III", "senior"),
    ("Project Coordinator", "mid"),
    ("Director, Program Management", None),
    ("Senior Manager, Product Management", "senior"),
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


# Two positions as Eightfold's /api/pcsx/search returned them for Autodesk on
# 2026-11-04, cut to the fields eightfold_rows reads.
EIGHTFOLD_POSITIONS = [
    {"id": 1443152973589, "name": "Software Development Manager, Horizontal Services",
     "locations": ["AMER - United States - Oregon - Remote", "Oregon, USA - Remote", "Montreal, QC, Canada"],
     "postedTs": 1793145600, "workLocationOption": "remote", "positionUrl": "/careers/job/1443152973589"},
    {"id": 1443154213410, "name": "Principal Visual Experience Designer",
     "locations": ["Portland, OR, USA", "San Francisco, CA, USA"],
     "postedTs": 1793491200, "workLocationOption": "onsite", "positionUrl": "/careers/job/1443154213410"},
]


def check_eightfold() -> int:
    rows = splat.eightfold_rows(EIGHTFOLD_POSITIONS, "jobs.autodesk.com", "Autodesk")
    got = [(r["title"], r["url"], splat.where(r["location"], r["remote"]), r["remote"], r["posted"]) for r in rows]
    want = [("Software Development Manager, Horizontal Services", "https://jobs.autodesk.com/careers/job/1443152973589",
             "oregon", True, "2026-10-28"),
            ("Principal Visual Experience Designer", "https://jobs.autodesk.com/careers/job/1443154213410",
             "oregon", False, "2026-11-01")]
    if got != want:
        print(f"eightfold_rows = {got!r}, want {want!r}")
        return 1
    return 0


# The posting reader (2026-11-09): what code takes from a posting's own text.
PAY_TEXT = [
    ("The base pay range for this position is $120,000 - $150,000 per year.", "$120K–$150K/yr"),
    ("Salary range: $85K–$110K", "$85K–$110K/yr"),
    ("Pay: $25.00 - $32.50 per hour", "$25–$32/hr"),
    ("$43.36 - $58.10 Hourly", "$43–$58/hr"),
    ("Zone A: $150,000 to $200,000. Zone B: $135,000 to $180,000.", "$135K–$200K/yr"),  # pay zones: widest span
    ("between $90,000 and $110,000 annually", "$90K–$110K/yr"),
    ("$120,000.00 - $150,000.00 USD", "$120K–$150K/yr"),
    ("A $5,000 - $10,000 sign-on bonus and a 401(k) match.", None),  # a bonus is not pay
    ("Revenue grew from $5M to $50M last year.", None),
    ("No pay stated here.", None),
    # From the reader audit, 2026-11-09.
    ("USA, WA, Seattle - 115,600.00 - 160,000.00 USD annually", "$116K–$160K/yr"),  # Amazon: no $
    ("USA, CA, Sunnyvale - 122,600.00 - 185,000.00 USD annually\nUSA, WA, Redmond - 99,100.00 - 160,000.00 USD annually",
     "$99K–$185K/yr"),
    ("40 hours / week, Salary Range $121,000/year to $163,700/year.", "$121K–$164K/yr"),
    ("United States of America: Annual Salary USD 123,500 - 285,000 in Tennessee", "$124K–$285K/yr"),
    ("Salary: $122,000", "$122K/yr"),
    ("The salary range for this role is USD $ 195K. This range reflects the base salary only", "$195K/yr"),
    ("The base salary range for this position is $96,000 - $184,000. The estimated on-target earnings, or OTE, "
     "is $120,000 - $230,000.", "$96K–$184K/yr"),  # base beats OTE
]
YEARS_TEXT = [
    ("5+ years of experience in security operations", 5),
    ("Minimum of three (3) years of relevant experience", 3),
    ("2-4 years experience with Linux", 2),
    ("Bachelor's degree and 7 years of professional experience", 7),
    ("We have been in business for 25 years.", None),
    # From the reader audit, 2026-11-09: the first requirement, a bullet that opens with it, never a preferred one.
    ("- 5+ years of technical product or program management experience\n- 7+ years of working directly with "
     "engineering teams experience\n- 3+ years of software development experience", 5),
    ("Bachelor's Degree AND 2+ years technical experience\nOR Master's Degree AND 1+ year(s) technical experience\n"
     "OR 5+ years technical experience", 2),
    ("2 years of experience with software development or 1 year of experience with an advanced degree", 2),
    ("- 3+ years of one of the following related domains: SRE, systems engineering", 3),
    ("5+ years of experience in networking\nPreferred Qualifications\n10+ years of experience at scale", 5),
]
DAYS_TEXT = [
    ("This is a hybrid role, in the office three days a week.", 3),
    ("Work from our Seattle office 4 days per week", 4),
    ("Our team ships 5 days a week.", None),  # no office in the sentence
    ("This role is hybrid, requiring 2-3 days per week on-site at one of the posted locations.", 2),
    ("This role is hybrid, requiring 2x days per week on-site at one of the posted locations.", 2),
]
READER_URLS = [
    (splat.GH_URL, "https://job-boards.greenhouse.io/anthropic/jobs/4567", ("anthropic", "4567")),
    (splat.LEVER_URL, "https://jobs.lever.co/palantir/0a1b2c3d-0000-1111-2222-333344445555/apply",
     (None, "palantir", "0a1b2c3d-0000-1111-2222-333344445555")),
    (splat.WD_URL, "https://gdit.wd5.myworkdayjobs.com/en-US/External_Career_Site/job/Any-Location--Remote/Lead-Cloud-Architect_RQ231565-1",
     ("gdit", "wd5", "External_Career_Site", "job/Any-Location--Remote/Lead-Cloud-Architect_RQ231565-1")),
    (splat.EF_URL, "https://apply.careers.microsoft.com/careers/apply?pid=/careers/job/1970393557053979",
     ("apply.careers.microsoft.com", None, "1970393557053979")),
    (splat.EF_URL, "https://jobs.autodesk.com/careers/job/1443152973589", ("jobs.autodesk.com", "1443152973589", None)),
    (splat.ORACLE_URL, "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/job/349868",
     ("https://eeho.fa.us2.oraclecloud.com", "jobsearch", "349868")),
    (splat.WORKABLE_URL, "https://apply.workable.com/j/6D2ED2A2FC/apply", ("6D2ED2A2FC",)),
    (splat.SMARTR_URL, "https://jobs.smartrecruiters.com/Lakeshore/744000160292019-senior-it-security", ("Lakeshore", "744000160292019")),
]
JSONLD_PAGE = ('<html><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",'
               '"description":"&lt;p&gt;Run the SOC.&lt;/p&gt;","jobLocationType":"TELECOMMUTE","baseSalary":'
               '{"@type":"MonetaryAmount","currency":"USD","value":{"@type":"QuantitativeValue","minValue":100000,'
               '"maxValue":130000,"unitText":"YEAR"}}}</script></html>')


def check_reader() -> tuple[int, int]:
    bad = total = 0

    def eq(label, got, want):
        nonlocal bad, total
        total += 1
        if got != want:
            bad += 1
            print(f"{label} = {got!r}, want {want!r}")

    for s, want in PAY_TEXT:
        eq(f"pay_from_text({s!r})", splat.pay_from_text(s), want)
    for s, want in YEARS_TEXT:
        eq(f"years_from_text({s!r})", splat.years_from_text(s), want)
    for s, want in DAYS_TEXT:
        eq(f"office_days({s!r})", splat.office_days(s), want)
    for rx, url, want in READER_URLS:
        m = rx.search(url)
        eq(f"{rx.pattern[:24]}… on {url}", m.groups() if m else None, want)
    eq("body_text(double-escaped)", splat.body_text("&lt;p&gt;Line one&lt;/p&gt;&lt;ul&gt;&lt;li&gt;A &amp;amp; B&lt;/li&gt;&lt;/ul&gt;"),
       "Line one\nA & B")
    eq("jsonld_posting", splat.jsonld_posting(JSONLD_PAGE), {"body": "Run the SOC.", "pay": "$100K–$130K/yr", "mode": "remote"})
    eq("snippet(clearance)", splat.snippet("About us.\nMust hold an active TS/SCI clearance.\nGreat perks.", splat.CLR_WORDS),
       "Must hold an active TS/SCI clearance.")
    # The posting's own words replace the company-name clearance guess, but only once it was read.
    j = {"clearance": False, "clearance_check": True}
    splat.apply_read(j, {"read": True, "clr": "none", "pay": "$90K–$110K/yr", "sub": "secops"})
    eq("apply_read(read, no clearance)", j, {"clearance": False, "salary": "$90K–$110K/yr", "sub": "secops", "read": True})
    j = {"clearance": False, "clearance_check": True, "salary": "$1–$2/hr"}
    splat.apply_read(j, {"read": False, "pay": "$90K–$110K/yr", "sub": "grc"})
    eq("apply_read(unread)", j, {"clearance": False, "clearance_check": True, "salary": "$1–$2/hr", "sub": "grc"})
    j = {"clearance": False}
    splat.apply_read(j, {"read": True, "clr": "top_secret"})
    eq("apply_read(top secret)", j, {"clearance": True, "clr": "top_secret", "read": True})
    # Listings supply the URLs the reader fetches: never this box, its network, or metadata.
    for host in ("127.0.0.1", "10.1.2.3", "192.168.1.5", "169.254.169.254", "::1", "localhost"):
        eq(f"public_host({host!r})", splat.public_host(host), False)
    for url in ("http://example.com/job", "https://127.0.0.1:8791/api/splat-intake", "file:///etc/passwd",
                "https://example.com:8791/job"):
        try:
            splat.host_get(url)
            got = "fetched"
        except RuntimeError:
            got = "refused"
        eq(f"host_get({url!r})", got, "refused")

    # From the branch review, 2026-11-09.
    for s, want in REVIEW_PAY:
        eq(f"pay_from_text({s!r})", splat.pay_from_text(s), want)
    for args, want in STRUCTURED_PAY:
        eq(f"structured_pay{args!r}", splat.structured_pay(*args), want)
    for s, want in REVIEW_YEARS:
        eq(f"years_from_text({s!r})", splat.years_from_text(s), want)
    for s, want in REVIEW_DAYS:
        eq(f"office_days({s!r})", splat.office_days(s), want)
    # governmentjobs.com bars automated access (NEOGOV, 2026-11-11): never fetched, a subdomain
    # included; the name in another host's path is not that host.
    eq("NO_FETCH governmentjobs.com", bool(splat.NO_FETCH.search("https://www.governmentjobs.com/careers/portlandor/jobs/1")), True)
    eq("NO_FETCH bare host", bool(splat.NO_FETCH.search("https://governmentjobs.com")), True)
    eq("NO_FETCH on another host", bool(splat.NO_FETCH.search("https://evil.example/governmentjobs.com/careers/a/jobs/1")),
       False)
    eq("NO_FETCH lookalike host", bool(splat.NO_FETCH.search("https://notgovernmentjobs.com/x")), False)
    eq("fetch_posting skips governmentjobs.com", splat.fetch_posting({"url": "https://www.governmentjobs.com/careers/x/jobs/1"}),
       None)
    j = {"clearance": False}
    splat.apply_read(j, {"read": False, "clr": "top_secret"})
    eq("apply_read(unread, top secret)", j, {"clearance": True, "clr": "top_secret"})
    j = {"title": "Systems Administrator - TS/SCI w/ Poly", "clearance": True}
    splat.apply_read(j, {"read": True, "clr": "none"})
    eq("apply_read(title names a clearance)", j["clearance"], True)
    j = {"track": "eng"}
    splat.apply_read(j, {"read": True, "track": "cyber", "sub": "secops"})
    eq("apply_read(Kind from another track)", "sub" in j, False)
    # Hostile pages stay linear: 300 KB of unclosed "<script " and of "<" parse in well under a second each.
    for page in ("<script " * 37_500, "<" * 300_000):
        t0 = time.monotonic()
        splat.jsonld_posting(page)
        splat.body_text(page)
        eq(f"hostile page {page[:9]!r}… parsed fast", time.monotonic() - t0 < 2, True)
    # Jev down: two tries per question, and after JEV_BREAK failed questions no more calls.
    calls = []

    def down(*a, **k):
        calls.append(1)
        raise RuntimeError("HTTPError: HTTP Error 503")

    real, real_sleep = splat.http_json, splat.time.sleep
    splat.http_json, splat.time.sleep = down, (lambda s: None)
    try:
        for _ in range(splat.JEV_BREAK + 5):
            splat.jev_ask({"title": "x"}, {"manager": splat.MANAGER_Q}, "key")
        eq("jev breaker: calls made while Jev is down", len(calls), 2 * splat.JEV_BREAK)
        eq("jev breaker: open", splat.jev_open(), True)
    finally:
        splat.http_json, splat.time.sleep = real, real_sleep
        splat._JEV_FAILS[0] = 0
    # A server that drips its headers a byte at a time is cut off at the fetch deadline.
    eq("fetch deadline on dripped headers", fetch_against_drip(), "TimeoutError")
    return bad, total


def fetch_against_drip() -> str:
    """http_text with a 2 s deadline against a local server that sends one header
    byte every 0.5 s forever (each read stays under the socket timeout)."""
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    stop = threading.Event()

    def serve():
        conn, _ = srv.accept()
        conn.recv(4096)
        try:
            conn.sendall(b"HTTP/1.1 200 OK\r\n")
            while not stop.is_set():
                conn.sendall(b"X")
                time.sleep(0.5)
        except OSError:
            pass
        finally:
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    t0 = time.monotonic()
    try:
        splat.http_text(f"http://127.0.0.1:{srv.getsockname()[1]}/", tries=1, timeout=5, limit=1000,
                        opener=splat.PUBLIC, deadline=2)
        got = "answered"
    except RuntimeError as e:
        got = str(e).split(":")[0] if time.monotonic() - t0 < 4 else "too slow"
    finally:
        stop.set()
        srv.close()
    return got


REVIEW_PAY = [
    ("Sign-on bonus: $20,000 - $30,000.\nBase salary: $120,000 - $150,000", "$120K–$150K/yr"),
    ("This role includes a $30,000 - $50,000 sign-on bonus.", None),
    ("Base pay (not including bonus or equity): $100,000 - $130,000", "$100K–$130K/yr"),
    ("In addition to base salary, this role includes a $30,000 sign-on bonus.", None),
    ("Salary: up to $95,000 DOE", None),
    # The recheck, 2026-11-09: a set-aside range never turns into "$120/hr", and real base ranges stay.
    ("Sign-on bonus: $20,000 - $30,000", None),
    ("Salary Range: $120,000 - $150,000 plus bonus and equity", "$120K–$150K/yr"),
    ("Base salary, plus bonus and equity: $150,000 - $200,000", "$150K–$200K/yr"),
    ("Base salary $120,000 - $150,000\nBonus: 10% target", "$120K–$150K/yr"),
    ("Our designing team pays $120,000 - $150,000", "$120K–$150K/yr"),
    ("Sign-on bonus\n$30,000 - $50,000\nBase salary\n$120,000 - $150,000", "$120K–$150K/yr"),  # labels as headings
    ("The base salary range is $96,000 - $184,000 (not including commission).", "$96K–$184K/yr"),
]
STRUCTURED_PAY = [
    ((5000, 7000, "MONTH"), None),  # monthly is not shown as a year
    ((100000, 130000, "YEAR"), "$100K–$130K/yr"),
    ((45, 60, "per-hour-wage"), "$45–$60/hr"),
    ((45, 60, "per-year-salary"), None),  # the size disagrees with the label
    ((90000, 110000, None), "$90K–$110K/yr"),
]
REVIEW_YEARS = [
    ("With over 10 years of experience in the industry, Acme is a leader.\nRequirements\n- 3+ years of experience "
     "in IT support", 3),
    ("Founded 12 years ago, we bring experience to every client.\n- 2+ years in a help desk role", 2),
    ("A 2 year degree and 3 years of experience in networking", 3),
    ("We are looking for someone with 5+ years of experience in cloud security", 5),
    ("Requirements\n- 5+ years of experience supporting users across the US", 5),  # "US" is not "us"
    ("We require 4+ years of experience with Windows Server", 4),
    ("Qualifications\n- 5+ years program management in a technology organization\n- PMP preferred", 5),
]
REVIEW_DAYS = [
    ("This position is hybrid with the flexibility to work remotely up to 2 days per week.", None),
    ("Hybrid: work from home 2 days a week.", None),
    ("Hybrid schedule: in the office 3 days a week.", 3),
    ("Hybrid (3 days/week in office, 2 days remote)", 3),
    ("This is a hybrid role: in the office 3 days a week and remote 2 days a week.", 3),
    ("This role is not remote; on-site 5 days a week in Hillsboro.", 5),
    ("Hybrid: on-site 3 days a week supporting VMware virtualization and storage", 3),
]


def main() -> int:
    reader_bad, reader_total = check_reader()
    bad = check_eightfold() + check_fold_remote_twins() + reader_bad
    for args, want in MONEY:
        if splat.money(*args) != want:
            bad += 1
            print(f"money{args!r} = {splat.money(*args)!r}, want {want!r}")
    for summary, want in ASHBY_PAY:
        if splat.ashby_pay(summary) != want:
            bad += 1
            print(f"ashby_pay({summary!r}) = {splat.ashby_pay(summary)!r}, want {want!r}")
    for name, pattern, cases in (("AGENCY", splat.AGENCY, AGENCY), ("FEDERAL", splat.FEDERAL, FEDERAL)):
        for company, want in cases:
            if bool(pattern.search(company)) != want:
                bad += 1
                print(f"{name}.search({company!r}) = {not want}, want {want}")
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
    for title, tech, want in TRACK_PM:
        got = splat.rule_track(title, tech_employer=tech)
        if got != want:
            bad += 1
            print(f"rule_track({title!r}, tech_employer={tech}) = {got!r}, want {want!r}")
    for title, want in LEVEL_PM:
        got = splat.rule_level(title, None, "pm")
        if got != want:
            bad += 1
            print(f"rule_level({title!r}, track='pm') = {got!r}, want {want!r}")
    for title, sa, want in TRACK_SE:
        got = splat.rule_track(title, tech_employer=sa, sa_employer=sa)
        if got != want:
            bad += 1
            print(f"rule_track({title!r}, sa_employer={sa}) = {got!r}, want {want!r}")
    for title, want in LEVEL_SE:
        got = splat.rule_level(title, None, "se")
        if got != want:
            bad += 1
            print(f"rule_level({title!r}, track='se') = {got!r}, want {want!r}")
    for f, p, want in READ_DUE:
        got = splat.read_due(f, p)
        if got != want:
            bad += 1
            print(f"read_due({f!r}, {p!r}) = {got!r}, want {want!r}")
    for old, facts, err, jf, want in FOLD_READ:
        reads = {"k": dict(old)} if old else {}
        shown = splat.fold_read(reads, "k", dict(facts), facts["track"], err, jf)
        after = reads.get("k")
        if after and after.get("seen") == splat.TODAY:
            after = {**after, "seen": "TODAY"}
        got = ("new" if shown is not None else "cached", after)
        if got != want:
            bad += 1
            print(f"fold_read(old={old!r}, {facts!r}, err={err!r}) = {got!r}, want {want!r}")
    for title, track, f, tech, want in NOT_PRESALES:
        got = splat.not_presales({"title": title, "track": track, "sec_topic": False}, f, tech)
        if got != want:
            bad += 1
            print(f"not_presales({title!r}, {track}, {f!r}) = {got!r}, want {want!r}")
    for where_, f, want in NOT_REMOTE:
        got = splat.not_remote({"where": where_}, f)
        if got != want:
            bad += 1
            print(f"not_remote({where_}, {f!r}) = {got!r}, want {want!r}")
    for url, want in MIDDLEMAN_URLS:
        got = bool(splat.MIDDLEMAN.search(url))
        if got != want:
            bad += 1
            print(f"MIDDLEMAN.search({url!r}) = {got!r}, want {want!r}")
    for title, rt, jt, want in CROSS_CONFIRM:
        p = {"title": title, "track": rt, "level": "mid", "clearance": False}
        kept = splat.apply_jev(p, {"track": jt, "level": "mid", "clearance": False})
        got = p["track"] if kept else None
        if got != want:
            bad += 1
            print(f"apply_jev({title!r}, rules {rt}, jev {jt}) = {got!r}, want {want!r}")
    se_subs = [("Sales Engineer", True, "sesec"), ("Security Solutions Engineer", False, "sesec"),
               ("Solutions Architect", False, None)]
    for title, topic, want in se_subs:
        if splat.se_sub(title, topic) != want:
            bad += 1
            print(f"se_sub({title!r}, {topic}) = {splat.se_sub(title, topic)!r}, want {want!r}")
    # Every Kind key is unique across tracks: the page looks a Kind up by its key alone.
    keys = [k for q in splat.SUB_Q.values() for k in q["criteria"] if k != "other"]
    if len(keys) != len(set(keys)) or not set(keys) <= set(splat.SUB_LABEL):
        bad += 1
        print(f"SUB_Q keys not unique or not all labelled: {keys}")
    if not set(splat.CONFIRM) < set(splat.TRACKS) or set(splat.JEV_QUESTIONS["track"]["criteria"]) != set(splat.TRACKS):
        bad += 1
        print("CONFIRM, TRACKS and Jev's track choices disagree")
    for sid, want in TECH_SOURCE:
        if splat.tech_source(sid) != want:
            bad += 1
            print(f"tech_source({sid!r}) = {splat.tech_source(sid)!r}, want {want!r}")
    for title, rt, rl, jt, jl, want in APPLY_JEV:
        p = {"title": title, "track": rt, "level": rl, "clearance": False}
        kept = splat.apply_jev(p, {"track": jt, "level": jl, "clearance": False} if jt else None)
        got = (p["track"], p["level"]) if kept else None
        if got != want:
            bad += 1
            print(f"apply_jev({title!r}, rules {rt}/{rl}, jev {jt}/{jl}) = {got!r}, want {want!r}")
    for a, b in NORM:
        if splat.norm(a) != splat.norm(b):
            bad += 1
            print(f"norm({a!r}) = {splat.norm(a)!r} != norm({b!r}) = {splat.norm(b)!r}")
    total = (len(WHERE) + len(TRACK) + len(TRACK_SEC_TOPIC) + len(TRACK_PM) + len(LEVEL_PM) + len(LEVEL) + len(NORM)
             + len(TECH_SOURCE) + len(APPLY_JEV) + len(MONEY) + len(ASHBY_PAY) + len(AGENCY) + len(FEDERAL) + 2 + reader_total
             + len(TRACK_SE) + len(LEVEL_SE) + len(READ_DUE) + len(se_subs) + 2 + len(FOLD_READ) + len(CROSS_CONFIRM) + len(NOT_PRESALES) + len(NOT_REMOTE) + len(MIDDLEMAN_URLS))
    print(f"{total - bad}/{total} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
