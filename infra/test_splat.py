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
    # Found boards keep the title rules their aggregator copies had (auto_boards).
    ("auto:greenhouse:purestorage", False), ("auto:ashby:horizon3ai", False), ("auto:lever-eu:siteimprove", False),
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


# Two USAJOBS search results, cut to the fields usajobs_rows reads, shaped as the
# Search API returned them on 2026-11-11 (Fields=Full).
USAJOBS_ITEMS = [
    {"MatchedObjectDescriptor": {
        "PositionTitle": "IT Cybersecurity Specialist", "PositionURI": "https://www.usajobs.gov:443/job/892244600",
        "OrganizationName": "Bonneville Power Administration", "DepartmentName": "Department of Energy",
        "PositionLocation": [{"CityName": "Portland, Oregon", "CountrySubDivisionCode": "Oregon"}],
        "PositionLocationDisplay": "Portland, Oregon",
        "PositionRemuneration": [{"MinimumRange": "96000.0", "MaximumRange": "125000.0", "RateIntervalCode": "PA"}],
        "JobGrade": [{"Code": "GS"}], "PublicationStartDate": "2026-11-05T00:00:00.0000",
        "QualificationSummary": "One year of specialized experience at the GS-11 level.",
        "UserArea": {"Details": {"JobSummary": "Protect BPA's grid control systems.", "LowGrade": "12",
                                 "MajorDuties": ["Monitors security events."], "SecurityClearance": "Not Required",
                                 "RemoteIndicator": False}}}},
    {"MatchedObjectDescriptor": {
        "PositionTitle": "Supervisory IT Specialist (OS)", "PositionURI": "https://www.usajobs.gov:443/job/891772600",
        "OrganizationName": "Deputy Assistant Secretary for Information Technology Operations",
        "DepartmentName": "Department of Veterans Affairs",
        "PositionLocation": [{"CityName": "Seattle, Washington", "CountrySubDivisionCode": "Washington"}],
        "PositionRemuneration": [{"MinimumRange": "107000", "MaximumRange": "140000", "RateIntervalCode": "Per Year"}],
        "JobGrade": [{"Code": "GS"}], "PublicationStartDate": "2026-11-02",
        "UserArea": {"Details": {"JobSummary": "Lead the server team.", "LowGrade": "13",
                                 "SecurityClearance": "Top Secret", "RemoteIndicator": False}}}},
]


def check_usajobs() -> int:
    rows = splat.usajobs_rows(USAJOBS_ITEMS)
    got = [(r["title"], r["company"], r["url"], splat.where(r["location"], r["remote"]), r["salary"], r["hint"],
            splat.rule_track(r["title"]), bool(splat.CLEARANCE.search(r["title"] + " " + r["desc"][:600])),
            "Security clearance: Not Required" in r["body"]) for r in rows]
    want = [("IT Cybersecurity Specialist", "Bonneville Power Administration", "https://www.usajobs.gov/job/892244600",
             "oregon", "$96K–$125K/yr", "MI", "cyber", False, True),
            ("Supervisory IT Specialist (OS)", "Department of Veterans Affairs", "https://www.usajobs.gov/job/891772600",
             "pnw", "$107K–$140K/yr", "SE", "eng", True, False)]
    if got != want:
        print(f"usajobs_rows = {got!r}, want {want!r}")
        return 1
    return 0


def check_tips() -> int:
    """Members' shared jobs (src_tips and its guards). No network: host_get and
    find_source are stubbed."""
    import json as _json
    import tempfile as _tempfile
    from datetime import date, timedelta
    from pathlib import Path as _Path
    today = date.today()
    d = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    tips = [
        {"id": "a" * 16, "shared": d(1), "url": "https://www.linkedin.com/jobs/view/4012345678/", "status": "new",
         "title": "IT Systems Security Administrator", "company": "Hilgers PLLC", "location": "Remote US",
         "note": "No clearance needed"},
        {"id": "b" * 16, "shared": d(1), "url": "https://www.indeed.com/viewjob?jk=0a1b2c3d4e5f6789", "status": "new",
         "title": "Help Desk Technician", "company": "Acme Health", "location": "Portland, OR", "note": ""},
        {"id": "c" * 16, "shared": d(20), "url": "https://www.linkedin.com/jobs/view/4000000001/", "status": "new",
         "title": "Network Engineer", "company": "Old Co", "location": "Seattle, WA", "note": ""},
        {"id": "d" * 16, "shared": d(2), "url": "https://careers.example.com/jobs/42", "status": "removed",
         "title": "SOC Analyst", "company": "Gone Inc", "location": "Remote US", "note": ""},
        {"id": "e" * 16, "shared": d(3), "url": "https://careers.example.com/jobs/43", "status": "approved",
         "title": "Cloud Engineer", "company": "Example", "location": "Bend, OR", "note": ""},
    ]
    f = _Path(_tempfile.mkdtemp()) / "tips.json"
    f.write_text(_json.dumps(tips), encoding="utf-8")
    old_tips, old_find = splat.TIPS, splat.find_source
    splat.TIPS = f
    splat.find_source = lambda company, title, w: ({"url": "https://boards.greenhouse.io/acmehealth/jobs/1",
                                                    "ref": ("gh", "acmehealth", 1)} if company == "Acme Health" else None)
    try:
        rows = splat.src_tips()
    finally:
        splat.TIPS, splat.find_source = old_tips, old_find
    got = [(r["title"], r["url"], r.get("gate"), bool(r.get("found")), r["approved"], r["posted"], r["desc"],
            splat.where(r["location"], r["remote"])) for r in rows]
    want = [("IT Systems Security Administrator", "https://www.linkedin.com/jobs/view/4012345678/", "LinkedIn", False,
             False, None, "", "remote"),
            ("Help Desk Technician", "https://boards.greenhouse.io/acmehealth/jobs/1", None, True, False, None, "", "oregon"),
            ("Cloud Engineer", "https://careers.example.com/jobs/43", None, False, True, None, "", "oregon")]
    bad = 0
    if got != want:
        print(f"src_tips = {got!r}, want {want!r}")
        bad += 1

    # find_source: a Greenhouse board counts only when its own name is the employer's
    # and the job is the same title in the same place; it keeps the API reference.
    boards = {
        "https://boards-api.greenhouse.io/v1/boards/acme": {"name": "Acme"},
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs": {"jobs": [
            {"id": 7, "title": "Help Desk Technician", "location": {"name": "Portland, Oregon"},
             "absolute_url": "https://acme.example/careers?gh_jid=7"},
            {"id": 8, "title": "Help Desk Technician", "location": {"name": "Austin, TX"},
             "absolute_url": "https://acme.example/careers?gh_jid=8"}]},
        "https://boards-api.greenhouse.io/v1/boards/other": {"name": "Other Acme Holdings"},
    }

    def fake_get(url, *a, **k):
        if url in boards:
            return boards[url]
        raise RuntimeError("HTTP Error 404")
    old_get = splat.host_get
    splat.host_get = fake_get
    try:
        splat._FIND_LEFT[0] = 5
        hit = splat.find_source("Acme", "Help Desk Technician", "oregon")
        miss_place = splat.find_source("Acme", "Help Desk Technician", "remote")
        miss_name = splat.find_source("Other", "Help Desk Technician", "oregon")
        splat._FIND_LEFT[0] = 0
        capped = splat.find_source("Acme", "Help Desk Technician", "oregon")
    finally:
        splat.host_get = old_get
        splat._FIND_LEFT[0] = splat.FIND_MAX
    checks = [
        ("find_source match", hit, {"url": "https://acme.example/careers?gh_jid=7", "ref": ("gh", "acme", 7)}),
        ("find_source other place", miss_place, None),
        ("find_source board of another name", miss_name, None),
        ("find_source past the cap", capped, None),
        ("tip_slugs", splat.tip_slugs("Hilgers PLLC"), ["hilgers", "hilgerspllc", "hilgers-pllc"]),
        ("tip_slugs &", splat.tip_slugs("Ben & Jerry's"), ["benandjerrys", "ben-and-jerrys"]),
        ("ats_link greenhouse", splat.ats_link("https://boards.greenhouse.io/acme/jobs/1"), True),
        ("ats_link lever", splat.ats_link("https://jobs.lever.co/acme/0e3c1b2a-1111-2222-3333-444455556666"), True),
        ("ats_link employer site", splat.ats_link("https://careers.example.com/jobs/42"), False),
        ("ats_link path spoof", splat.ats_link("https://evil.example/boards.greenhouse.io/x/jobs/1"), False),
        # A phishing page with JobPosting data reads fine; it still is not verified.
        ("not_verified read, not an ATS", splat.not_verified({"origin": "member", "url": "https://acme-careers.example/apply"},
                                                             {"read": True}), True),
        ("not_verified read ATS", splat.not_verified({"origin": "member", "url": "https://boards.greenhouse.io/a/jobs/1"},
                                                     {"read": True}), False),
        ("not_verified unread ATS", splat.not_verified({"origin": "member", "url": "https://boards.greenhouse.io/a/jobs/1"},
                                                       {"read": False}), True),
        ("not_verified found + read", splat.not_verified({"origin": "member", "url": "https://acme.example/c?gh_jid=7",
                                                          "found": True}, {"read": True}), False),
        ("not_verified approved", splat.not_verified({"origin": "member", "approved": True, "url": "https://x.example/"},
                                                     None), False),
        ("not_verified gated", splat.not_verified({"origin": "member", "gate": "LinkedIn"}, None), False),
        ("not_verified not a tip", splat.not_verified({"origin": "company"}, None), False),
        ("NO_FETCH linkedin", bool(splat.NO_FETCH.search("https://www.linkedin.com/jobs/view/1/")), True),
        ("NO_FETCH indeed", bool(splat.NO_FETCH.search("https://ca.indeed.com/viewjob?jk=ab")), True),
        ("NO_FETCH lnkd.in", bool(splat.NO_FETCH.search("https://lnkd.in/gAbC123")), True),
        ("fetch_posting skips linkedin", splat.fetch_posting({"url": "https://www.linkedin.com/jobs/view/1/"}), None),
    ]
    # drop_tip_twins: a tip of a job the page already has, in any place, goes.
    cands = {"acme|help desk technician|remote": {"company": "Acme", "title": "Help Desk Technician", "origin": "company"},
             "acme|help desk technician|oregon": {"company": "ACME", "title": "Help Desk Technician", "origin": "member"},
             "x|cloud engineer|oregon": {"company": "X", "title": "Cloud Engineer", "origin": "member"}}
    n = splat.drop_tip_twins(cands)
    checks.append(("drop_tip_twins", (n, sorted(cands)), (1, ["acme|help desk technician|remote", "x|cloud engineer|oregon"])))
    for label, g, w in checks:
        if g != w:
            print(f"{label} = {g!r}, want {w!r}")
            bad += 1
    return bad


def check_board_dates() -> tuple[int, int]:
    """The employer's own board date on an aggregator's copy (board_redate, inside
    rules_pass), from company_check.py's census ledger. The rules' age check counts
    from the run's day, so the dates in the rules_pass cases are days before it."""
    import inspect as _inspect
    import json as _json
    import tempfile as _tempfile
    from datetime import timedelta as _td
    from pathlib import Path as _Path
    vultr = "https://jobs.ashbyhq.com/Vultr/"   # the cards' case; the ledger keys "vultr"
    ledger = {
        "ashby:vultr": {"postings": {
            "6e0fea62-a9b8-4b06-a434-79968877f510": {"t": "RMA Systems Engineer", "d": "2026-04-30"},
            "11111111-2222-3333-4444-555555555555": {"t": "Technical Support Specialist", "d": "2026-03-31"},
            "22222222-2222-3333-4444-555555555555": {"t": "Senior Platform Engineer", "d": "2026-11-01"},
            "33333333-2222-3333-4444-555555555555": {"t": "Cloud Engineer", "d": "2026-11-09"},
            "66666666-2222-3333-4444-555555555555": {"t": "NOC Engineer", "d": "2026-10-20"},
            "77777777-2222-3333-4444-555555555555": {"t": "Help Desk", "d": None},
            "88888888-2222-3333-4444-555555555555": {"t": "Hand edit", "d": "2026-02-30"}}},
        "greenhouse:veeamsoftware": {"postings": {"5001960101": {"d": "2026-09-01"}}},
        "lever-eu:siteimprove": {"postings": {"adcd50b8-cf2e-426f-890d-44d037d84cd4": {"d": "2026-10-19"}}},
        "ashby:broken": ["not", "a", "board"],
    }

    def row(uid, posted, origin="aggregator", url=None):
        return {"url": url or vultr + uid, "posted": posted, "origin": origin}
    rows = {
        "older": row("22222222-2222-3333-4444-555555555555", "2026-11-06"),              # board's older date replaces
        "newer": row("33333333-2222-3333-4444-555555555555", "2026-11-03"),              # never moved forward
        "own": row("11111111-2222-3333-4444-555555555555", "2026-11-06", origin="company"),  # the employer's own board row
        "tip": row("11111111-2222-3333-4444-555555555555", None, origin="member"),       # a member's tip: its own rule
        "fill": row("66666666-2222-3333-4444-555555555555", None),                       # no date given: the board's fills it
        "undated": row("77777777-2222-3333-4444-555555555555", "2026-11-05"),            # the board has no date
        "unreal": row("88888888-2222-3333-4444-555555555555", "2026-11-05"),             # not a real day: none
        "unknown": row("99999999-2222-3333-4444-555555555555", "2026-11-05"),            # not in the ledger
        "workday": row("", "2026-11-05", url="https://acme.wd5.myworkdayjobs.com/External/job/Portland/SRE_R1"),
        "broken": row("", "2026-11-05", url="https://jobs.ashbyhq.com/broken/6e0fea62-a9b8-4b06-a434-79968877f510"),
    }
    moved = sorted(k for k, p in rows.items() if splat.board_redate(p, ledger))
    posted = {k: p["posted"] for k, p in rows.items()}
    same = [row("22222222-2222-3333-4444-555555555555", "2026-11-06"), row("66666666-2222-3333-4444-555555555555", None)]

    # rules_pass: the board's date goes on each fetched row before the rows are
    # picked, so an old copy never wins the pick, a tip's twin check or the remote
    # fold and takes a fresh row down with it (review, 2026-11-12).
    today = splat.NOW.date()
    ago = lambda n: (today - _td(days=n)).isoformat()   # noqa: E731
    OLD, NEW = "11111111-2222-3333-4444-555555555555", "22222222-2222-3333-4444-555555555555"
    led = {"ashby:vultr": {"postings": {OLD: {"d": ago(226)}, NEW: {"d": ago(7)}, **{
        f"{n:08d}-2222-3333-4444-555555555555": {"d": ago(n)} for n in (14, 15, 30, 31, 50)}}},
        "ashby:broken": {"postings": {OLD: {"d": "2026-02-30"}}}}

    def agg(uid, title, loc, posted, remote=False, board="Vultr", sid="foorilla", origin="aggregator", **kw):
        return sid, splat.posting(title=title, company="Vultr", url=f"https://jobs.ashbyhq.com/{board}/{uid}", location=loc,
                                  remote=remote, posted=posted, origin=origin, **kw)
    tss = "Technical Support Specialist"
    # Two copies of one remote role, the months-old one first: the fresh one shows.
    dedupe, dedupe_n, _ = splat.rules_pass([agg(OLD, tss, "Remote - US", ago(6), True), agg(NEW, tss, "Remote - US", ago(6), True)], led)
    # A Portland copy of the old listing, and the employer's own remote row: the
    # employer's row is not folded into a copy that then drops.
    fold, fold_n, _ = splat.rules_pass([agg(OLD, tss, "Portland, OR", ago(6)),
                                     agg(NEW, tss, "Remote - US", ago(7), True, sid="ashby:vultr", origin="company")], led)
    folded = splat.fold_remote_twins(fold)
    # A member's Portland tip of the same role: not dropped as the twin of a copy that drops.
    tips, tips_n, _ = splat.rules_pass([agg(OLD, tss, "Remote - US", ago(6), True),
                                     ("tips", splat.posting(title=tss, company="Vultr", url="https://www.linkedin.com/jobs/view/4012345678/",
                                                            location="Portland, OR", posted=ago(3), origin="member", gate="linkedin"))], led)
    tip_twins = splat.drop_tip_twins(tips)
    # The age rule at its edges, on the board's date: 30 days stays and 31 drops
    # locally, 14 stays and 15 drops remote, the same rule as the aggregator's date.
    uid = lambda n: f"{n:08d}-2222-3333-4444-555555555555"   # noqa: E731
    edges, edges_n, _ = splat.rules_pass([
        agg(uid(30), "Cloud Engineer", "Portland, OR", ago(2)), agg(uid(31), "Network Engineer", "Portland, OR", ago(2)),
        agg(uid(14), "Systems Engineer", "Remote - US", ago(2), True), agg(uid(15), "Site Reliability Engineer", "Remote - US", ago(2), True),
        agg(uid(50), "SOC Analyst", "Portland, OR", ago(40)),          # too old by its own date: not the board's doing
        agg(OLD, "Help Desk Technician", "Portland, OR", ago(2), board="broken"),   # an impossible ledger date
    ], led)
    edge_dates = {p["title"]: p["posted"] for p in edges.values()}
    main_src = _inspect.getsource(splat.main)
    at = lambda s: main_src.find(s)   # noqa: E731

    tmp = _Path(_tempfile.mkdtemp())
    (tmp / "bad.json").write_text("{not json", encoding="utf-8")
    (tmp / "good.json").write_text(_json.dumps({"version": 1, "boards": ledger}), encoding="utf-8")
    (tmp / "list.json").write_text(_json.dumps({"version": 1, "boards": ["ashby:vultr"]}), encoding="utf-8")
    checks = [
        ("board_redate: only an older board date moves posted", moved, ["fill", "older"]),
        ("board_redate: an older board date replaces the aggregator's", posted["older"], "2026-11-01"),
        ("board_redate: a newer board date never moves posted forward", posted["newer"], "2026-11-03"),
        ("board_redate: the employer's own board row is untouched", posted["own"], "2026-11-06"),
        ("board_redate: a member's tip is untouched", posted["tip"], None),
        ("board_redate: an empty posted takes the board's date", posted["fill"], "2026-10-20"),
        ("board_redate: no board date, no change",
         (posted["undated"], posted["unreal"], posted["unknown"], posted["workday"], posted["broken"]), ("2026-11-05",) * 5),
        ("board_redate: a missing ledger is a no-op",
         ([splat.board_redate(p, {}) for p in same], [p["posted"] for p in same]), ([False, False], ["2026-11-06", None])),
        ("rules_pass: of two copies, the old one does not win and drop the fresh one",
         ([p["url"].rsplit("/", 1)[1] for p in dedupe.values()], dedupe_n), ([NEW], 1)),
        ("rules_pass: the employer's own remote row is not folded into a copy that drops",
         ([(p["origin"], p["where"]) for p in fold.values()], fold_n, folded), ([("company", "remote")], 1, 0)),
        ("rules_pass: a member's tip is not dropped as the twin of a copy that drops",
         ([p["origin"] for p in tips.values()], tips_n, tip_twins), (["member"], 1, 0)),
        ("rules_pass: the age rule on the board's date, at its edges",
         (sorted(edge_dates), edges_n),
         (["Cloud Engineer", "Help Desk Technician", "Systems Engineer"], 2)),
        ("rules_pass: a kept row carries the board's date", (edge_dates.get("Cloud Engineer"), edge_dates.get("Systems Engineer")),
         (ago(30), ago(14))),
        ("rules_pass: an impossible ledger date is no date", edge_dates.get("Help Desk Technician"), ago(2)),
        ("main: board dates go on in the rules pass, before the twin steps and Jev, and are counted",
         (0 < at("rules_pass(raw, board_ledger)") < at("drop_tip_twins(") < at("fold_remote_twins(") < at("jev_stats ="),
          '"older_by_board": older_by_board' in main_src, at("board_ledger = load_board_ledger()") > 0), (True, True, True)),
        ("board_key: Ashby, the token without case", splat.board_key(vultr + "6E0FEA62-a9b8-4b06-a434-79968877f510"),
         ("ashby:vultr", "6e0fea62-a9b8-4b06-a434-79968877f510")),
        ("board_key: a board name with a space", splat.board_key("https://jobs.ashbyhq.com/PAR%20Technology/6e0fea62-a9b8-4b06-a434-79968877f510"),
         ("ashby:par technology", "6e0fea62-a9b8-4b06-a434-79968877f510")),
        ("board_date: Greenhouse's EU board is its one API", splat.board_date("https://job-boards.eu.greenhouse.io/veeamsoftware/jobs/5001960101", ledger),
         "2026-09-01"),
        ("board_date: Lever's EU board keeps its own key", splat.board_date("https://jobs.eu.lever.co/siteimprove/adcd50b8-cf2e-426f-890d-44d037d84cd4", ledger),
         "2026-10-19"),
        ("board_date: Lever's US key is another board", splat.board_date("https://jobs.lever.co/siteimprove/adcd50b8-cf2e-426f-890d-44d037d84cd4", ledger),
         None),
        ("board_date: not a board posting", splat.board_date("https://www.linkedin.com/jobs/view/1/", ledger), None),
        ("board_date: a date that is not a real day", splat.board_date(vultr + "88888888-2222-3333-4444-555555555555", ledger), None),
        ("board_date: a board that is not a board", splat.board_date("https://jobs.ashbyhq.com/broken/6e0fea62-a9b8-4b06-a434-79968877f510", ledger),
         None),
        ("load_board_ledger: missing file", splat.load_board_ledger(tmp / "none.json"), {}),
        ("load_board_ledger: corrupt file", splat.load_board_ledger(tmp / "bad.json"), {}),
        ("load_board_ledger: valid JSON, not a ledger", splat.load_board_ledger(tmp / "list.json"), {}),
        ("load_board_ledger: the boards", sorted(splat.load_board_ledger(tmp / "good.json")), sorted(ledger)),
    ]
    bad = 0
    for label, g, w in checks:
        if g != w:
            print(f"{label} = {g!r}, want {w!r}")
            bad += 1
    return bad, len(checks)


def check_auto_boards() -> tuple[int, int]:
    """Found boards (auto_boards, src_auto): the employer boards company_check.py's
    census read from aggregator links, read directly under the card name the
    ledger stores. Names and boards from the 2026-11-12 page."""
    import inspect as _inspect
    import json
    from datetime import timedelta as _td
    today = splat.NOW.date()
    ago = lambda n: (today - _td(days=n)).isoformat()   # noqa: E731

    def entry(name, cards, last, link=""):
        e = {"first_ok": ago(40), "last_ok": last, "postings": {}, "link": link}
        if name is not None:
            e["name"] = name
        if cards is not None:
            e["cards"] = cards
        return e
    led = {
        "ashby:horizon3ai": entry("Horizon3.ai", 11, ago(1)),
        "greenhouse:purestorage": entry("Pure Storage", 8, ago(0)),
        "greenhouse:axon": entry("Axon", 6, ago(splat.AUTO_BOARD_DAYS)),          # the cutoff's last day: in
        "greenhouse:flexport": entry("Flexport", 4, ago(splat.AUTO_BOARD_DAYS + 1)),  # a day past it: out
        "lever-eu:siteimprove": entry("Siteimprove", 1, ago(1)),
        "lever-eu:palantir": entry("Palantir", 1, ago(1)),       # Lever EU is its own key: not the hand-kept US board
        "lever:palantir": entry("Palantir", 9, ago(1)),          # hand-kept
        "ashby:vultr": entry("Vultr", 12, ago(1)),               # hand-kept ("Vultr" in ASHBY, keyed without case)
        "greenhouse:okta": entry("Okta", 20, ago(1)),            # hand-kept
        "ashby:par technology": entry("PAR Technology", 2, ago(2)),   # a board name with a space
        "greenhouse:insightglobal": entry("Insight Global", 5, ago(1)),   # a staffing firm by name
        "lever:jobgether": entry("Acme Cloud", 5, ago(1)),       # a recruiter's board under one client's name
        "greenhouse:diceboard": entry("Dice Co", 5, ago(1), "https://www.dice.com/jobs/x"),   # a middleman's link
        "ashby:noname": entry(None, 3, ago(1)),                   # written before the ledger kept names
        "ashby:blank": entry("  ", 3, ago(1)),
        "greenhouse:nocards": entry("Fleetio", None, ago(1)),    # no count: last
        "greenhouse:baddate": entry("Dragos", 2, "2026-02-30"),  # not a real day
        "greenhouse:nodate": entry("Impinj", 2, None),
        "greenhouse:a/b": entry("Slash", 2, ago(1)),             # not a token a board URL gives
        "lever:..": entry("Dots", 2, ago(1)),
        "workday:acme": entry("Acme", 2, ago(1)),                # not a board read here
        "ashby:list": ["not", "a", "board"],
    }
    want_ids = ["auto:ashby:horizon3ai", "auto:greenhouse:purestorage", "auto:greenhouse:axon",
                "auto:ashby:par technology", "auto:lever-eu:palantir", "auto:lever-eu:siteimprove",
                "auto:greenhouse:nocards"]
    picked = splat.auto_boards(led)
    cap = splat.AUTO_BOARD_MAX
    splat.AUTO_BOARD_MAX = 2
    try:
        capped = [x[0] for x in splat.auto_boards(led)]
    finally:
        splat.AUTO_BOARD_MAX = cap

    # Each found board read by its vendor's reader, under the stored name.
    T1, T2 = "Consulting Field Solutions Architect, Cyber Resilience - SLED West", "Program Manager"
    U = "6e0fea62-a9b8-4b06-a434-79968877f510"
    gh_url = "https://job-boards.greenhouse.io/purestorage/jobs/7000001"
    answers = {
        "https://boards-api.greenhouse.io/v1/boards/purestorage/jobs": {"jobs": [
            {"id": 7000001, "title": T1, "absolute_url": gh_url, "location": {"name": "Seattle, Washington"},
             "first_published": ago(20) + "T09:00:00-07:00", "updated_at": ago(1) + "T09:00:00-07:00"},
            {"id": 7000002, "title": T2, "absolute_url": "https://job-boards.greenhouse.io/purestorage/jobs/7000002",
             "location": {"name": "Portland, Oregon"}, "first_published": ago(3) + "T09:00:00-07:00"}]},
        "https://api.ashbyhq.com/posting-api/job-board/par%20technology?includeCompensation=true": {"jobs": [
            {"id": U, "title": "Cloud Security Engineer", "jobUrl": f"https://jobs.ashbyhq.com/PAR%20Technology/{U}",
             "location": "Remote - US", "workplaceType": "Remote", "publishedAt": ago(2) + "T00:00:00+00:00"}]},
        "https://api.eu.lever.co/v0/postings/siteimprove?mode=json": [
            {"id": U, "text": "Security Engineer", "hostedUrl": f"https://jobs.eu.lever.co/siteimprove/{U}",
             "categories": {"location": "Remote - US"}, "workplaceType": "remote"}],
    }
    calls: list[str] = []
    mode = {"fail": None}

    def fake(url, *a, **k):
        calls.append(url)
        if mode["fail"]:
            raise RuntimeError(f"HTTPError: HTTP Error {mode['fail']}")
        if url not in answers:
            raise RuntimeError("HTTPError: HTTP Error 404: Not Found")
        return json.loads(json.dumps(answers[url]))

    real, real_t0 = splat.http_json, splat._T0
    splat.http_json, splat._T0 = fake, time.monotonic()
    splat._AUTO_FAILS.clear()
    try:
        srcs = splat.all_sources(None, led)
        auto = [s for s in srcs if s[0].startswith("auto:")]
        tail = [s[0] for s in srcs[-len(want_ids):]]
        rows = {sid: fn(*a) for sid, label, fn, a in auto if sid in (
            "auto:greenhouse:purestorage", "auto:ashby:par technology", "auto:lever-eu:siteimprove")}
        read_calls = list(calls)
        only_auto = [s[0] for s in splat.all_sources({"auto"}, led)]
        only_gh = [s[0] for s in splat.all_sources({"greenhouse"}, led) if s[0].startswith("auto:")]
        no_ledger = [s[0] for s in splat.all_sources(None) if s[0].startswith("auto:")]
        # The breaker: AUTO_BREAKER failures in a row on one host stop its boards; a 404 is the board's own.
        calls.clear()
        mode["fail"] = "404: Not Found"
        errs404 = []
        for _ in range(splat.AUTO_BREAKER + 2):
            try:
                splat.src_auto("greenhouse", "gone", "Gone")
            except RuntimeError as e:
                errs404.append(str(e))
        n404 = len(calls)
        calls.clear()
        mode["fail"] = "503: Service Unavailable"
        errs503 = []
        for _ in range(splat.AUTO_BREAKER + 2):
            try:
                splat.src_auto("greenhouse", "purestorage", "Pure Storage")
            except RuntimeError as e:
                errs503.append(str(e))
        n503 = len(calls)
        mode["fail"] = None
        calls.clear()
        lever_ok = len(splat.src_auto("lever-eu", "siteimprove", "Siteimprove"))   # another host: not stopped
        splat._AUTO_FAILS.clear()
        # Late in the run: no found board starts.
        splat._T0 = time.monotonic() - splat.AUTO_FETCH_MINUTES * 60 - 1
        calls.clear()
        try:
            splat.src_auto("greenhouse", "purestorage", "Pure Storage")
            late = "read"
        except RuntimeError as e:
            late = str(e)
        late_calls = len(calls)
    finally:
        splat.http_json, splat._T0 = real, real_t0
        splat._AUTO_FAILS.clear()

    # The board's row replaces the aggregator's copy: the same row key and read key,
    # origin company, the board's own date.
    gh = rows.get("auto:greenhouse:purestorage") or []
    copy = splat.posting(title=T1, company="Pure Storage", url=gh_url, location="Seattle, Washington, United States",
                         posted=ago(2), origin="aggregator")
    board_row = next((p for p in gh if p["title"] == T1), None)
    merged, _, _ = splat.rules_pass([("foorilla", copy), ("auto:greenhouse:purestorage", dict(board_row or {}))], {})
    key = f"{splat.norm('Pure Storage')}|{splat.norm(T1)}|pnw"
    kept = merged.get(key) or {}

    # Not a tech source: "Program Manager" from a found board drops as Foorilla's
    # copy does, and passes on a hand-kept tech board.
    def pm(sid, origin):
        return sid, splat.posting(title=T2, company="Pure Storage", url="https://job-boards.greenhouse.io/purestorage/jobs/7000002",
                                  location="Portland, Oregon", posted=ago(3), origin=origin)
    pm_auto = splat.rules_pass([pm("auto:greenhouse:purestorage", "company")], {})[0]
    pm_agg = splat.rules_pass([pm("foorilla", "aggregator")], {})[0]
    pm_tech = splat.rules_pass([pm("greenhouse:okta", "company")], {})[0]
    sa = "Solutions Architect"
    sa_auto = splat.rule_track(sa, False, splat.tech_source("auto:ashby:horizon3ai"), splat.tech_source("auto:ashby:horizon3ai"))

    # drop_board_copies: one posting, two keys.
    tu = "https://jobs.ashbyhq.com/temporal/11111111-2222-3333-4444-555555555555"
    cands = {
        "pure storage|sled west|pnw": {"origin": "company", "url": gh_url, "where": "pnw"},
        "pure storage|west|remote": {"origin": "aggregator", "url": gh_url + "?gh_src=feed", "where": "remote"},
        "pure storage|other|pnw": {"origin": "aggregator", "url": "https://job-boards.greenhouse.io/purestorage/jobs/7000009", "where": "pnw"},
        "temporal|sse|pnw": {"origin": "aggregator", "url": tu, "where": "pnw"},
        "temporal|sse|remote": {"origin": "company", "url": tu.replace("temporal", "Temporal"), "where": "remote"},
        "pure storage|sled west|oregon": {"origin": "member", "url": gh_url, "where": "oregon"},   # a tip: its own step
        "workday|x|pnw": {"origin": "aggregator", "url": "https://acme.wd5.myworkdayjobs.com/External/job/Portland/SRE_R1", "where": "pnw"},
    }
    copies = splat.drop_board_copies(cands)
    main_src = _inspect.getsource(splat.main)
    at = lambda s: main_src.find(s)   # noqa: E731

    checks = [
        ("auto_boards: recent, not hand-kept, named, busiest first", [x[0] for x in picked], want_ids),
        ("auto_boards: the stored name is the source's name",
         {x[0]: x[1] for x in picked if x[0] in ("auto:ashby:horizon3ai", "auto:ashby:par technology")},
         {"auto:ashby:horizon3ai": "Horizon3.ai", "auto:ashby:par technology": "PAR Technology"}),
        ("auto_boards: the vendor and token from the ledger key", [x[2:] for x in picked][-3:],
         [("lever-eu", "palantir"), ("lever-eu", "siteimprove"), ("greenhouse", "nocards")]),
        ("auto_boards: the same ledger gives the same ids (the failed-source grace keys on them)",
         [x[0] for x in splat.auto_boards(json.loads(json.dumps(led)))], want_ids),
        ("auto_boards: the cap keeps the busiest", capped, want_ids[:2]),
        ("auto_boards: a week's cutoff, 300 boards, 10 minutes, 3 failures (as the README says)",
         (splat.AUTO_BOARD_DAYS, splat.AUTO_BOARD_MAX, splat.AUTO_FETCH_MINUTES, splat.AUTO_BREAKER), (7, 300, 10, 3)),
        ("auto_boards: no ledger, no boards", (splat.auto_boards({}), splat.auto_boards(None)), ([], [])),
        ("all_sources: found boards last, read by src_auto under the stored name",
         (tail, {s[2] for s in auto}, [s[3] for s in auto][:2]),
         (want_ids, {splat.src_auto}, [("ashby", "horizon3ai", "Horizon3.ai"), ("greenhouse", "purestorage", "Pure Storage")])),
        ("all_sources: --only auto, and not under the vendor's family", (only_auto, only_gh, no_ledger), (want_ids, [], [])),
        ("src_auto: one list call each; Ashby's space quoted; Lever EU on its own host", sorted(read_calls),
         sorted(answers)),
        ("src_auto: every row carries the stored name",
         {sid: sorted({p["company"] for p in r}) for sid, r in rows.items()},
         {"auto:greenhouse:purestorage": ["Pure Storage"], "auto:ashby:par technology": ["PAR Technology"],
          "auto:lever-eu:siteimprove": ["Siteimprove"]}),
        ("src_auto: Greenhouse's first_published, never updated_at", board_row and board_row["posted"], ago(20)),
        ("rules_pass: the board's row replaces the aggregator's copy, same key, the board's date",
         (list(merged), kept.get("origin"), kept.get("source"), kept.get("posted")),
         ([key], "company", "auto:greenhouse:purestorage", ago(20))),
        ("read_key: the same as the copy's, so the posting is not read again",
         splat.read_key(key, kept.get("url", "")), splat.read_key(key, copy["url"])),
        ("tech_source: a found board's PM passes only where the copy's did",
         (len(pm_auto), len(pm_agg), [p["track"] for p in pm_tech.values()]), (0, 0, ["pm"])),
        ("tech_source: a found board's solutions architect is not a sales engineer", sa_auto, None),
        ("src_auto: a board that is not there never stops the vendor",
         (n404, all("404" in e for e in errs404)), (splat.AUTO_BREAKER + 2, True)),
        ("src_auto: AUTO_BREAKER failures in a row stop that host's boards",
         (n503, errs503[-1].startswith("not read today")), (splat.AUTO_BREAKER, True)),
        ("src_auto: another host's boards go on", lever_ok, 1),
        ("src_auto: none starts after AUTO_FETCH_MINUTES", (late.startswith("not read today"), late_calls), (True, 0)),
        ("drop_board_copies: the board's row stays, its copies under other keys go, a tip and other postings stay",
         (copies, sorted(cands)),
         (2, ["pure storage|other|pnw", "pure storage|sled west|oregon", "pure storage|sled west|pnw",
              "temporal|sse|remote", "workday|x|pnw"])),
        ("drop_board_copies: a remote copy marks the local row also_remote",
         (cands["pure storage|sled west|pnw"].get("also_remote"), "also_remote" in cands["temporal|sse|remote"]), (True, False)),
        ("main: found boards from the ledger; not counted toward failing closed; copies dropped before the twin steps",
         ("all_sources(only, board_ledger)" in main_src, 'not sid.startswith("auto:")' in main_src,
          0 < at("rules_pass(raw, board_ledger)") < at("drop_board_copies(cands)") < at("drop_tip_twins("),
          '"board_copies": board_copies' in main_src),
         (True, True, True, True)),
    ]
    bad = 0
    for label, g, w in checks:
        if g != w:
            print(f"{label} = {g!r}, want {w!r}")
            bad += 1
    return bad, len(checks)


def check_workday_30_plus() -> tuple[int, int]:
    """Workday's "Posted 30+ Days Ago" is more than 30 days (probed 2026-11-12:
    "30 Days Ago" was startDate today-30, the youngest "30+" today-31). It gives
    no date and drops in rules_pass, with its aggregator copies; it used to be
    read as exactly 30 days old and stay on the page for good."""
    import inspect as _inspect
    from datetime import timedelta as _td
    today = splat.NOW.date()
    ago = lambda n: (today - _td(days=n)).isoformat()   # noqa: E731

    # src_workday on a stubbed tenant: one Oregon facet, then its jobs.
    listed = [("Cloud Engineer", "/job/Portland/Cloud-Engineer_R1", "Posted 30+ Days Ago"),
              ("Network Engineer", "/job/Portland/Network-Engineer_R2", "Posted 30 Days Ago"),
              ("Systems Engineer", "/job/Portland/Systems-Engineer_R3", "Posted 29 Days Ago"),
              ("Security Engineer", "/job/Portland/Security-Engineer_R4", "Posted Today")]

    def fake(url, body=None, *a, **k):
        if (body or {}).get("limit") == 1:
            return {"facets": [{"facetParameter": "locations", "values": [{"id": "pdx", "descriptor": "Portland, Oregon"}]}]}
        return {"jobPostings": [{"title": t, "externalPath": p, "locationsText": "Portland, Oregon", "postedOn": o}
                                for t, p, o in listed]}
    real = splat.http_json
    splat.http_json = fake
    try:
        wd = {p["title"]: p for p in splat.src_workday("acme", "wd5", "External", "Acme", "local")}
    finally:
        splat.http_json = real

    # rules_pass over the tenant's rows and aggregators' copies.
    base = "https://acme.wd5.myworkdayjobs.com/External/job/Portland/"
    agg_base = "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Portland/"

    def row(sid, title, path, posted=None, origin="company", company="Acme", loc="Portland, Oregon", remote=False, **kw):
        url = path if path.startswith("https://") else (agg_base if origin == "aggregator" else base) + path
        return sid, splat.posting(title=title, company=company, url=url, location=loc, remote=remote, posted=posted,
                                  origin=origin, **kw)
    raw = [
        # An aggregator's fresh-dated copy of the "30+" posting, listed before the
        # Workday row: still dropped, and does not take the row's place.
        row("foorilla", "Cloud Engineer", "Cloud-Engineer_R1", ago(3), "aggregator"),
        row("workday:acme", "Cloud Engineer", "Cloud-Engineer_R1", None, older_than=30),
        # The same posting under another employer name and place: dropped too.
        row("foorilla", "Cloud Engineer", "Cloud-Engineer_R1", ago(3), "aggregator", company="Acme Technology Inc",
            loc="Remote - US", remote=True),
        # Another posting with the same title and place: stays, with its own date.
        row("foorilla", "Cloud Engineer II", "Cloud-Engineer-II_R9", ago(3), "aggregator"),
        # A "30+" remote row: past the 14-day limit too.
        row("workday:acme", "Site Reliability Engineer", "SRE_R5", None, loc="Remote - US", remote=True, older_than=30),
        # "30 Days Ago" is a true date at the edge: it stays, dated.
        row("workday:acme", "Network Engineer", "Network-Engineer_R2", ago(30)),
        # Out of the tracks anyway: dropped by the title rules, not counted.
        row("workday:acme", "Registered Nurse", "Registered-Nurse_R6", None, older_than=30),
        # A member's tip of the "30+" posting keeps its own 30-day life.
        row("tips", "Cloud Engineer", "Cloud-Engineer_R1", ago(2), "member", loc="Seattle, WA"),
        # "More than 7 days" in a 30-day window: kept, and with no date rather than an invented one.
        row("workday:acme", "Help Desk Technician", "Help-Desk_R7", None, older_than=7),
    ]
    cands, older_by_board, n30 = splat.rules_pass(raw, {})
    kept = sorted((p["title"], p["origin"], p["posted"]) for p in cands.values())
    flipped, *_ = splat.rules_pass([raw[1], raw[0]], {})
    main_src = _inspect.getsource(splat.main)

    checks = [
        ("_wd_days: today, yesterday, N days", [splat._wd_days(s) for s in (
            "Posted Today", "Posted Yesterday", "Posted 2 Days Ago", "Posted 29 Days Ago", "Posted 30 Days Ago")],
         [ago(0), ago(1), ago(2), ago(29), ago(30)]),
        ("_wd_days: \"30+\" is no date", [splat._wd_days(s) for s in ("Posted 30+ Days Ago", "", None)], [None] * 3),
        ("_wd_older: only \"N+\" gives a bound", [splat._wd_older(s) for s in (
            "Posted 30+ Days Ago", "Posted 30 Days Ago", "Posted Today", None)], [30, None, None, None]),
        ("src_workday: \"30+\" carries no date and older_than 30; the rest their true dates",
         {t: (p["posted"], p["older_than"]) for t, p in wd.items()},
         {"Cloud Engineer": (None, 30), "Network Engineer": (ago(30), None), "Systems Engineer": (ago(29), None),
          "Security Engineer": (ago(0), None)}),
        ("wd_posting: the locale prefix and site do not matter, the posting does",
         (splat.wd_posting(base + "Cloud-Engineer_R1") == splat.wd_posting(agg_base + "Cloud-Engineer_R1"),
          splat.wd_posting(base + "Cloud-Engineer_R1") == splat.wd_posting(base + "Cloud-Engineer_R1-1"),
          splat.wd_posting("https://jobs.ashbyhq.com/acme/1")),
         (True, False, None)),
        ("rules_pass: \"30+\" rows and their aggregator copies drop; a true 30-day date, another posting and a tip stay",
         kept, sorted([("Cloud Engineer II", "aggregator", ago(3)), ("Network Engineer", "company", ago(30)),
                       ("Cloud Engineer", "member", ago(2)), ("Help Desk Technician", "company", None)])),
        ("rules_pass: counted in rows that would otherwise show, apart from the board dates",
         (n30, older_by_board), (4, 0)),
        ("rules_pass: the copy drops whichever comes first", sorted(p["origin"] for p in flipped.values()), []),
        ("main: workday_30_plus from the rules pass, published with the counts",
         ("cands, older_by_board, workday_30_plus = rules_pass(raw, board_ledger)" in main_src,
          '"workday_30_plus": workday_30_plus' in main_src), (True, True)),
    ]
    bad = 0
    for label, g, w in checks:
        if g != w:
            print(f"{label} = {g!r}, want {w!r}")
            bad += 1
    return bad, len(checks)


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
    board_bad, board_total = check_board_dates()
    auto_bad, auto_total = check_auto_boards()
    wd30_bad, wd30_total = check_workday_30_plus()
    bad = (check_eightfold() + check_usajobs() + check_tips() + check_fold_remote_twins() + reader_bad + board_bad
           + auto_bad + wd30_bad)
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
             + len(TECH_SOURCE) + len(APPLY_JEV) + len(MONEY) + len(ASHBY_PAY) + len(AGENCY) + len(FEDERAL) + 4 + reader_total
             + board_total + auto_total + wd30_total
             + len(TRACK_SE) + len(LEVEL_SE) + len(READ_DUE) + len(se_subs) + 2 + len(FOLD_READ) + len(CROSS_CONFIRM) + len(NOT_PRESALES) + len(NOT_REMOTE) + len(MIDDLEMAN_URLS))
    print(f"{total - bad}/{total} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
