"""Rules tests for splat.py. Stdlib only.

    python3 infra/test_splat.py

Every case below is a real location or title string a source returned on
2026-10-02, most of them a bug the first sample run shipped.
"""

import importlib.util
import sys
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
]

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
    ("Principal Solutions Engineer", "cyber"),
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
    # No answer from Jev yet: a pm row waits, cyber and eng rows publish on the rules.
    ("Food Systems Program Manager", "pm", "mid", None, None, None),
    ("SOC Analyst II", "cyber", "mid", None, None, ("cyber", "mid")),
]

# Only technology employers' own boards are tech sources (not Amazon, the
# aggregators or the telecoms).
TECH_SOURCE = [
    ("greenhouse:stripe", True), ("workday:nvidia", True), ("workday:nike", False), ("workday:tmobile", False),
    ("eightfold:autodesk", True), ("eightfold:lamresearch", True), ("eightfold:fortive", False),
    ("providence", False), ("neogov:portlandor", False), ("amazon", False), ("himalayas", False),
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


# A governmentjobs.com fragment as the City of Portland served it on 2026-10-21,
# cut to two postings and their markup.
NEOGOV_FRAGMENT = """
<ul class="unstyled search-results-listing-container job-listing-container ">
  <li class="list-item" data-job-id="5468381">
    <h3 class="job-item-link-container">
      <a aria-label="x" class="item-details-link" data-department-name="BTS" href="/careers/portlandor/jobs/5468381/it-security-analyst" rel="">Information Technology Security Analyst</a>
    </h3>
    <ul class="list-meta">
      <li>Commissioners&#39; Office, OR</li>
      <li>Full-Time</li>
    </ul>
    <div class="list-published">
      <span class="list-entry-starts"><span>Posted 3 days ago</span></span>
    </div>
  </li>
  <li class="list-item" data-job-id="5430025">
    <h3 class="job-item-link-container">
      <a aria-label="x" class="item-details-link" href="/careers/portlandor/jobs/5430025/water-operator" rel="">Water Operator &amp; Mechanic</a>
    </h3>
    <ul class="list-meta">
      <li>Portland, OR</li>
    </ul>
    <div class="list-published">
      <span class="list-entry-starts"><span>Posted more than 30 days ago</span></span>
    </div>
  </li>
</ul>
<div class="pager-container"></div>
"""


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


def check_neogov() -> int:
    rows = splat.neogov_rows(NEOGOV_FRAGMENT, "City of Portland")
    got = [(r["title"], r["url"], splat.where(r["location"]), splat.age_days(r["posted"])) for r in rows]
    want = [("Information Technology Security Analyst",
             "https://www.governmentjobs.com/careers/portlandor/jobs/5468381/it-security-analyst", "oregon", 3),
            ("Water Operator & Mechanic",
             "https://www.governmentjobs.com/careers/portlandor/jobs/5430025/water-operator", "oregon", 30)]
    if got != want:
        print(f"neogov_rows = {got!r}, want {want!r}")
        return 1
    return 0


def main() -> int:
    bad = check_neogov() + check_eightfold()
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
             + len(TECH_SOURCE) + len(APPLY_JEV) + 2)
    print(f"{total - bad}/{total} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
