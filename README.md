# The Daily Splat

`https://iambraun.com/cv/job-sources/daily-splat/` is a tiny job aggregator for
the job-hunt cabal: cybersecurity openings plus the infrastructure engineering
next to them (cloud, network, systems, DevOps, sysadmin, IT support), and the
project, program and product managers of technology work, entry to senior
level, in Washington, Oregon and Idaho or remote within the US. It is rebuilt
every morning, and a posting its source stopped listing is gone that day.

The server pulls `main` before each daily run (05:30 Pacific), so a merged PR is
live the next morning. Contributions are welcome: see **Contributing** below.

- **Generator:** `splat.py`. Stdlib Python, no pip installs. Writes `jobs.json`
  beside itself, or where `SPLAT_OUT` says (the server sets the docroot).
- **Client:** `index.html` reads `jobs.json`
  and does all filtering in the browser. Filters live in the URL hash, so a view
  like `#track=cyber&where=oregon,remote` can be pasted into the group chat, and
  the last view is remembered on each device (localStorage). Where takes more
  than one place; an old `where=pnw` link still means Oregon plus WA/ID.
  Only takes `new`, `week` (posted in the last 7 days), `pay` and `noclr`.
  "New" is new since this morning, or since the device's last visit when that
  was before yesterday ("New since Thu"); the visit date stays in the browser
  (localStorage `splat-visit`). Cards posted over 21 days ago say "Open 3+ weeks".
  With a track chosen, a Kind row narrows it (`sub=secops,grc`).
- **State:** `/var/lib/daily-splat/state.json` (systemd `StateDirectory`):
  yesterday's postings, for "new today" and the failed-source grace, and the
  Jev answer cache, so each day only pays for its new postings.
- **Keys:** `/etc/daily-splat.env`, root:root 0600, holding
  `FOORILLA_API_KEY` and `TYPESAFE_API_KEY`. Kept on the server only. Neither is required; a missing one skips that
  source or pass and the run says so in `jobs.json` under `sources` / `jev`.

## Where the postings come from

| kind | sources | how removal works |
|---|---|---|
| Company boards | Greenhouse, Lever, Ashby, Workday tenants, Eightfold (Autodesk, Lam Research, Tektronix, Fortive), Amazon, Providence (Oracle HCM) and the Portland-area governments on NEOGOV (City of Portland, TriMet, Port of Portland, Beaverton, Metro), listed in `splat.py` | the board stops returning the job |
| Open aggregators | RemoteOK, Jobicy, Himalayas, Working Nomads, The Muse, Workable search. Remotive was dropped 2026-11-04: its API returns the same ~11 jobs whatever is asked, and it asks that its jobs not be republished on third-party sites | the feed stops returning the job |
| Foorilla (keyed) | PNW location queries over 30 days; remote-US over 14 days from its InfoSec & Privacy (102) and Cybersecurity (26) categories plus security and infra title queries | `expired` flag, or it ages out of the window |

Microsoft's own careers API refuses anonymous calls, but its roles arrive
through Foorilla. Not covered, and why: LinkedIn (no API, and scraping it is against its terms), We Work Remotely
(paywalled for job-seekers), USAJOBS (needs its own key; worth adding if the
group wants federal roles).

Every board token in `splat.py` answered from this box on 2026-10-02. To add
an employer, add its token to the right dict and run the script once by hand;
a wrong token shows up as a failed source, it does not break the run.

## The filters

1. **Rules** (`where`, `rule_track`, `rule_level` in `splat.py`). Location in
   WA/OR/ID (cities, state names, `, WA`-style codes; `Washington, DC` is not
   Washington), or remote with a US mention. Title reads as security, or as
   infrastructure next to it: an infra word (cloud, network, systems,
   platform, DevOps/SRE, identity, endpoint...) AND an engineering role word.
   General product software development is out: the first live run took every
   engineer title and the group's verdict was "mostly SWE roles, not infosec".
   Sales, recruiting, hardware, mechanical, civil and the like are out. Level: entry (junior, associate, `Engineer I`, tier 1, new grad, apprentice) through staff/principal; internships and director/VP/head-of/C-level are out. The entry lane and the IT-support titles were added 2026-10-04 for a member looking for entry sysadmin, cyber and support-engineer roles.
   Recruiter and staffing reposts are dropped: the page lists employers.
   The PM track (added 2026-11-03 at the group's request): project, program
   and technical program managers, project coordinators, and product managers
   and owners, when the work is technology, IT or security (`pm_track`). The
   title has to name the work (`PM_TECH`: IT, software, cloud, data, platform,
   SOC, IAM, GRC, Epic...), unless the posting is on a technology employer's
   own board (`TECH_SOURCES` and `TECH_WORKDAY`: the Greenhouse, Lever and Ashby
   boards and the tech Workday tenants), where "Senior Product Manager,
   Payments" is tech work as written, or Foorilla filed it under infosec.
   Amazon, the aggregators and the telecoms are not tech sources: their PMs
   are mostly operations, retail or any employer's. Three tiers of exclusion:
   construction, facilities, marketing, sales, legal, recruiting, scrum and
   physical-security PMs are always out (`PM_HARD_NOT`); patient-care PMs are
   out unless a health-IT word says the job is the systems ("Clinical
   Applications Project Manager", "Nursing Informatics", Epic, Cerner); the
   rest of the excluded words (manufacturing, warehouse, field service...) give
   way to any technology word ("Industrial Cybersecurity Program Manager"). An
   operations program manager (supply chain, logistics, retail) is out even at
   a tech employer unless the title is a TPM or names IT; a product manager
   there is not. A PM title that is not technology work is out, as every PM
   title was before. Scrum masters and agile coaches are out; delivery
   managers are not part of the PM track (one with an infra word still reads
   as Infra & IT, as before). "PM" in shift hours ("3 PM - 11 PM", "PM
   Shift", "AM & PM") and "TPM" the chip are not project managers. In a PM
   title "Manager" is the job, not the grade: "Product Manager" is mid.
   **The rules only propose PM rows; Jev confirms them.** A pm row Jev has
   not answered is held back (`jev.pm_unconfirmed` in `jobs.json`) and asked
   again the next day, so with no Jev key the PM track is empty. Three review
   rounds showed no word list separates a technology PM from a public-works,
   utility or care PM ("engineering", "systems", "infrastructure" and
   "security" mean both); Jev's track question names the PMs that are out.
2. **Jev** (TypeSafe's typed classifier).
   Three typed questions per new posting: track (cyber / eng / pm / neither), level
   (intern / entry / mid / senior / staff / executive), clearance required. "Neither",
   "intern" and "executive" drop the posting. A title that states its level
   keeps the rule's level. About 800 input tokens per posting at $0.042 per
   million (the first run: 5,687 postings, 4.7M tokens). Answers are cached
   under `JEV_VERSION`; bump it whenever the questions change meaning.

3. **The reader** (added 2026-11-09). Each kept posting is read once, the
   day it is first seen, and its facts cached under `READ_VERSION` in the
   state file. The text comes from the list call where it carries one
   (Lever, Ashby, Amazon, Workable, the aggregators), else from the
   employer's own system by URL: the Greenhouse, Lever, Ashby, Workday,
   Eightfold (Microsoft too), Oracle HCM, Rippling, SmartRecruiters,
   BambooHR and Workable job APIs, the NEOGOV page, and otherwise the
   page's schema.org `JobPosting` data. ADP, Dayforce, UKG and SAP pages
   render in the browser and stay unread.
   - Code takes what code can: the pay range (`pay_from_text`: two dollar
     amounts, annual 25K-1.5M or hourly 10-400, pay zones give the widest
     span), the years of experience asked, and days a week in the office.
   - Jev answers choices over only the sentences that say it (`snippet`):
     clearance (none / must be clearable / Public Trust / Secret / Top
     Secret), work mode, where a remote hire may live, degree, and, from
     the title and opening, the kind of work (`sub`) and whether the job
     manages people. A posting with no clearance sentence is "none"
     without asking.
   - A read posting's own words replace the guesses: its clearance answer
     replaces the federal-contractor `clearance_check`, and pay found in
     the text fills a row the source left without pay (or with one figure
     where the posting gives a range).
   - Accuracy, from a 60-posting audit on 2026-11-09 against each posting's
     own text: clearance 60/60, people manager 60/60, office days 60/60,
     pay 57/60, degree 57/60, years 54/60 (58 counting the years a source
     states), kind of work 54/60 with the rest left blank, not mislabeled.
     Work mode and remote limits are shown only when Jev is at least 0.8
     sure; wrong ones fell from 14 to 2 in 60.
   - A posting that could not be fetched is not cached and is tried again
     the next day; a 403, 404 or 410, or a refused URL, waits a week. A
     posting Jev still owes answers to is never cached. `jobs.json` reports
     the pass under `reader` and `counts.read`.
   - The run still lands when a source or Jev misbehaves. Posting URLs come
     from listings, so only public https hosts on port 443 are fetched, each
     fetch has 25 seconds in all (a timer shuts its sockets), pages are cut
     at 500 KB and parsed with linear patterns. Jev gets four calls at a
     time and two tries; after 10 failed questions in a row a breaker skips
     the rest of the day's questions. The read pass has 15 minutes and ends
     in time for the unit's 45; a fetch still stuck then is left behind.

`infra/test_splat.py` holds the rule cases, most of them bugs the
first sample run shipped. Run it after touching a regex:

```bash
python3 infra/test_splat.py
```

## Company check

"Company check" on each card opens public records about the employer, from
`companies.json`, which `company_check.py` writes beside `jobs.json` once a day
(`infra/company-check.service`, 07:00 Pacific, after the splat):

| Source | What it shows | How it matches |
|---|---|---|
| [USAspending](https://api.usaspending.gov) | Prime contract obligations per federal fiscal year, and how much came from DoD, DHS, ICE and CBP | The employer's reviewed UEIs, less its joint ventures (`exclude_ueis`) |
| NLRB, via [labordata/nlrb-data](https://github.com/labordata/nlrb-data) | Unfair-labor-practice charges against it since five years ago, and how they closed | Respondent name equals a reviewed legal name |
| [CourtListener](https://www.courtlistener.com/help/api/) | Federal employment civil-rights cases (nature of suit 442 and 445) | A docket party equals a reviewed legal name |
| [FEC bulk files](https://www.fec.gov/data/browse-data/?tab=bulk-data) | What the employer's connected PAC gave federal candidates, by party | The PAC's connected organization equals a reviewed legal name |
| [Violation Tracker](https://violationtracker.goodjobsfirst.org/) | A link only: its terms allow linking, not copying | Its parent list, else a name search |

Numbers show only for employers in `company_names.json`, a table a person
reviewed that maps job-board names to legal names and UEIs, with an `avoid`
list of look-alikes. Names are compared whole, after dropping case, punctuation
and the corporate suffix; never as a substring. Any employer not in the table
gets search links instead, because a loose match pins someone else's lawsuit or
contract on an innocent employer. A charge or a lawsuit is an allegation, and
the page says so.

To add an employer, add an entry to `company_names.json` (its job-board name
exactly as the card shows it, legal names, UEIs from USAspending's recipient
search) and run `python3 infra/test_company_check.py`.

On the server it needs `COURTLISTENER_TOKEN` in `/etc/daily-splat.env` (a free
CourtListener account); without it the court source is skipped. Each source
refreshes weekly per employer, CourtListener within about 100 searches a day,
and a source that fails keeps its last facts.

- By hand: `sudo systemctl start company-check.service`
- Last run: `journalctl -u company-check.service -n 40`
- Test without writing: `sudo systemd-run --pipe --wait -p User=foundry -p EnvironmentFile=/etc/daily-splat.env -p StateDirectory=daily-splat /usr/bin/python3 /home/foundry/daily-splat/company_check.py --dry-run --dump /tmp/c.json`

## It fails closed

If fewer than half the sources answer, the script assumes the box lost network,
writes nothing and exits non-zero. Same if zero postings survive. Yesterday's
`jobs.json` stays up. A single source failing is normal: its postings from the
last two days are carried and marked "Source down today" on the page.

## One-time setup on the server

```bash
git clone https://github.com/WorkflowtechAI/daily-splat.git /home/foundry/daily-splat
sudo cp /home/foundry/daily-splat/infra/daily-splat.service /etc/systemd/system/
sudo cp /home/foundry/daily-splat/infra/daily-splat.timer   /etc/systemd/system/
sudo cp /home/foundry/daily-splat/infra/company-check.service /home/foundry/daily-splat/infra/company-check.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now daily-splat.timer company-check.timer
sudo systemctl start daily-splat.service      # first run now, so the page has data
```

## Running it

- By hand: `sudo systemctl start daily-splat.service`
- Next run: `systemctl list-timers daily-splat.timer`
- Last run: `journalctl -u daily-splat.service -n 80`
- Test without writing (keys from the env file):
  `sudo systemd-run --pipe --wait -p User=foundry -p EnvironmentFile=/etc/daily-splat.env /usr/bin/python3 /home/foundry/daily-splat/splat.py --dry-run --dump /tmp/splat.json`
- One source only: add `--only greenhouse` (or `workday`, `foorilla`, `ashby:openai`, ...). Partial runs never write.

## Contributing

1. Fork, branch, change. The usual changes: an employer's board token in the
   dicts at the top of `splat.py`, or a rule in `where`, `rule_track` or
   `rule_level` with a test case for it in `infra/test_splat.py`.
2. Run the tests (`python3 infra/test_splat.py`) and, for a new source, one
   source by hand (`python3 splat.py --dry-run --only greenhouse --dump /tmp/s.json`).
3. Open a PR into `main`. It needs a maintainer's review; once merged it runs
   on the next morning's build.

Stdlib Python only: no pip dependencies. Never commit keys or `jobs.json`.
