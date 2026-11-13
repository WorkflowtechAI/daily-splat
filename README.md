# The Daily Splat

`https://worksearchcollective.com/splat/` is a tiny job aggregator for
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
  With a track chosen, a Kind row narrows it (`sub=secops,grc`;
  `#track=se&sub=sesec` is security sales engineering).
- **State:** `/var/lib/daily-splat/state.json` (systemd `StateDirectory`):
  yesterday's postings, for "new today" and the failed-source grace (each with
  its source's id, `sid`, which the page leaves out), and the
  Jev answer cache, so each day only pays for its new postings. Beside it,
  `boards.json`: the Company check's census of the employers' own Ashby,
  Greenhouse and Lever boards (see "Company check"), which `splat.py` reads,
  never writes, for the employer's own posting dates and for the boards it
  reads directly ("Found boards" below). A missing or corrupt one is an empty
  ledger for both scripts, and so no found boards.
- **Keys:** `/etc/daily-splat.env`, root:root 0600, holding
  `FOORILLA_API_KEY`, `TYPESAFE_API_KEY`, and `USAJOBS_API_KEY` with `USAJOBS_EMAIL` (the address the
  USAJOBS key was registered under; OPM wants it as the User-Agent). Kept on the server only. None is required; a missing one skips that
  source or pass and the run says so in `jobs.json` under `sources` / `jev`.

## Where the postings come from

| kind | sources | how removal works |
|---|---|---|
| Company boards | Greenhouse, Lever, Ashby, Workday tenants, Eightfold (Autodesk, Lam Research, Tektronix, Fortive), Amazon and Providence (Oracle HCM), listed in `splat.py`. The Portland-area governments on NEOGOV (City of Portland, TriMet, Port of Portland, Beaverton, Metro) were dropped 2026-11-11: NEOGOV's terms (effective 2025-09-02) forbid republishing content from the career pages it hosts, public or not, and its robots.txt disallows all but search engines. The page links to their job pages instead, and the reader never fetches governmentjobs.com (`NO_FETCH`) | the board stops returning the job |
| Found boards | An employer's own Ashby, Greenhouse or Lever board that the aggregators' cards link to, read directly (added 2026-11-12, `auto_boards`; see "Found boards" below) | the board stops returning the job; the board stops being read a week after the census last read it |
| Shared by members | The page's "Share a job" form (added 2026-11-11). `infra/intake.py` checks the tip with `splat.py`'s own rules before taking it, and says why when it refuses: an https link on a host name (IDNA-normalized, so a look-alike spelling is not a way around the rules), not a short link, not a middleman site (`MIDDLEMAN`) or one the Splat may not read (`NO_FETCH`), an employer that is not a staffing firm, a place in WA/OR/ID or remote US, a title in the tracks. It stores the tip in `/var/lib/daily-splat/tips.json` (`TIPS_FILE`; 40 new a day and 300 held at most) and emails David links signed with `TIP_SECRET` (one per action) to remove it and, for a link that needs it, to approve it; with no `TIP_SECRET` it takes no tips. The member's note goes only to that email. `src_tips` reads the file each morning; tips pass the same rules and Jev on the title, employer and place the member typed. A LinkedIn or Indeed link is never fetched (`NO_FETCH`; their terms forbid scraping, and one member pointing at one job is not copying their listings): the Splat looks for the employer's own posting (`find_source`: a Greenhouse board whose own name is the employer's, or a Lever or Ashby board named exactly for it, with the same title in the same place; 40 lookups a run) and links there, else the card says "LinkedIn only" (`gate`). Any other link shows only when it is a posting on a tracking system the reader reads through the vendor's API (`ats_link`) and was read, or when David approved it (`not_verified`, `reader.tip_unverified`): a page that merely carries JobPosting data could sit beside a fake sign-in form. A tip of a job already on the page is dropped (`drop_tip_twins`, `counts.tip_twins`) | 30 days after it was shared (14 for LinkedIn/Indeed), or when David removes it |
| Federal government | USAJOBS Search API (added 2026-11-11, `src_usajobs`): jobs open to the public in the IT series (2210, 0854, 1550, 0391) in OR, WA and ID, plus remote (none on 2026-11-11). Free with a key; OPM's terms allow republishing ("Prior OPM approval is not required"). Each row links to the official usajobs.gov posting, and the text, pay, grade (as a level hint) and clearance come with the list | the posting closes or is pulled |
| Open aggregators | RemoteOK, Jobicy, Himalayas, Working Nomads, Workable search. Remotive was dropped 2026-11-04: its API returns the same ~11 jobs whatever is asked, and it asks that its jobs not be republished on third-party sites. The Muse was dropped 2026-11-10: since 2026-11-09 its public jobs API answers every script with a Cloudflare browser challenge (403/404) | the feed stops returning the job |
| Foorilla (keyed) | PNW location queries over 30 days; remote-US over 14 days from its InfoSec & Privacy (102) and Cybersecurity (26) categories plus security and infra title queries | `expired` flag, or it ages out of the window |

**Found boards** (2026-11-12). An employer that reaches the Splat only through
an aggregator, but whose cards link to its own Ashby, Greenhouse or Lever
board, gets that board read directly, the way Vultr's is by hand in `ASHBY`:
most of its rows then come from the employer (the card says "Employer's own
board", with the board's own date), and its in-track jobs the aggregator missed
show up too. A posting whose board row fails a rule its aggregator copy passed
stays the copy ("Listed on Foorilla", dated by the board): about 1 in 5 rows on
found boards in a 2026-11-12 sample, mostly a Foorilla copy that passed on
Foorilla's infosec category (board rows carry none) or a remote Lever role whose
board gives only a city. The boards come from the Company check's census
(`boards.json`), which stores for each board the card name that most often
links to it and how many cards do, leaving out members' tips: a member types
the employer's name (for an Ashby link the member must), and a tip on a board
the reader reads is on the page without David looking at it, so a tip never
names a board or puts one on this list. A board is read when:

- the census read it OK in the last 7 days (`AUTO_BOARD_DAYS`). The census
  reads the boards today's cards link to, and a found board's own rows link to
  it, so a board in use is read every day. A week rides out a vendor or the
  Company check being down for a few days without the employer's rows flipping
  back to the aggregator's copies (each flip asks Jev and reads again where a
  copy is keyed differently), and still drops within a week a board no card
  links to any more, or one that is gone;
- it is not hand-kept (`GREENHOUSE`, `LEVER`, `ASHBY`, tokens without case;
  Lever's EU boards are their own key and are read on Lever's EU API host);
- it has a stored name that is not a staffing firm's (`AGENCY`, checked on the
  board's token too, since a recruiter's board carries other employers' jobs;
  the middlemen's own boards, such as jobgether, ladders and haystack, are in
  `AGENCY`). The only hosts read are the three vendors' APIs, none of them a
  `MIDDLEMAN` or `NO_FETCH` host;
- it is among the 300 busiest by cards (`AUTO_BOARD_MAX`): twice the 147 the
  2026-11-12 page had (98 Greenhouse, 34 Ashby, 14 Lever, 1 Lever EU), and under
  the census's 400 a run, so every one is read daily.

Each is read by the vendor's own reader under the stored name, so a row's key
(company, title, place) is the aggregator copy's. Its read key (the key and the
URL) is the copy's too when the board links the same URL (45 of 46 in a
2026-11-12 sample), so that posting is not read again; a board whose links go
to the employer's own site is read once more. Jev's key also holds the place
as written, which the board words differently about 1 in 4 times ("Remote" for
Foorilla's "Remote · United States"), so the row that replaces a copy takes the
copy's Jev answer (`carry_jev`) and Jev is not asked again: without it Jev was
asked again the day a board took over, and a pm or se row Jev could not
confirm that day left the page. Where the board titles or places a posting
differently than the aggregator did, the copy is dropped (`drop_board_copies`,
counted in `counts.board_copies`; a remote copy marks the board's local row as
also remote). A local copy of the board's remote row is the other way round,
as in the remote fold: the local copy stays, marked also remote, and the
board's row goes, since a remote row can still drop when its posting says
hybrid or onsite and a local one cannot. They are **not tech
sources** (`tech_source`): the hand-kept boards are technology companies by
review, these are whatever employers an aggregator carries (SpaceX, Axon,
Flexport, a health-care firm), and their rows came through Foorilla, which is
not one either; so the titles that pass are the ones that passed before, and
only where a row comes from changes. Source ids are `auto:<board>`
(`auto:ashby:horizon3ai`), the same every day while the board is in the
ledger, and the failed-source grace goes by that id (`source_answered`; the
state keeps each row's source id, the page does not), not by the label: a
found board's label is its stored name, which can change and can be another
source's (Palantir's Lever EU board and its hand-kept Lever board). Three at a time per API host, none
started after 10 minutes into the run (`AUTO_FETCH_MINUTES`), and three
failures in a row on a host stop its boards for the run (`AUTO_BREAKER`; a 404
is that board's, not the vendor's; a read that answers starts the count
again). One list call per board: about 150 more calls a run today, and the
first run asks Jev about and reads the found boards' rows the page did not
have, plus a read for the few whose board links elsewhere (about 1.7 new rows
per board in a 2026-11-12 sample
of 18 of the busiest; the Lever ones kept none, their remote roles giving only
a city, which `where` does not take as remote). They are not counted toward failing closed, and the page
folds them into one line under the source list.

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
   **Go to the source** (2026-11-11): a row whose link goes through a job
   site that stands between the job seeker and the employer (an email gate,
   a sign-up, a hop to another site: Dice, Haystack, LinkedIn, Indeed,
   ZipRecruiter, Lensa... in `MIDDLEMAN`) is dropped, except a member's LinkedIn or Indeed tip, which shows labelled as such (see "Shared by members"). A remote row whose own
   posting says hybrid or onsite (the reader's `mode`, from the employer's
   text or ATS field) is dropped too (`not_remote`, counted in
   `reader.not_remote`): that job is in an office outside the PNW. On
   2026-11-11 97% of links already went to the employer, and 53 of 984
   remote rows said otherwise in their own posting, 46 of them Foorilla's
   remote flag.
   **The employer's own date** (2026-11-12): an aggregator's date can move
   while the employer's posting keeps its own (Foorilla had Vultr's
   "Technical Support Specialist" posted 2026-11-06; Ashby's `publishedAt`
   for the same posting was 2026-03-31), so a months-old role passed the age
   rule and read "posted 6 days ago". When the Company check's census
   (`boards.json`) has the board an aggregator row links to, the card takes
   the board's date for that posting if it is older (never a later one), and
   the age rule runs again on it (`board_redate` in `rules_pass`). That
   happens on each fetched row, before rows are picked one per role: an old
   copy that won the pick, a tip's twin check or the remote fold and then
   dropped would take a fresh copy, the employer's own row or the tip with it.
   It is before Jev and the reader too, so a dropped row costs neither. A row
   then too old drops and is counted in `counts.older_by_board`. The
   employer's own board rows already carry the board's date, and members' tips
   keep their own 30-day life; both are left alone. The census is from the day
   before (the check runs after the splat); that is fine because a card only
   ever takes an earlier date, and a board new today dates its cards from
   tomorrow.
   **Workday's "30+"** (2026-11-12): a Workday list gives an age, not a
   date ("Posted Today", "Posted 29 Days Ago", "Posted 30 Days Ago", then
   "Posted 30+ Days Ago"). The Splat read "30+" as exactly 30 days, so every
   older local Workday job stayed on the page for good, "posted 30 days ago"
   (55 rows on 2026-11-12, 31 to 261 days old by Workday's own `startDate`).
   Checked against each posting's `startDate` that day, "30 Days Ago" is 30
   days and "30+" is 31 or more, so "30+" is past the 30-day window by
   Workday's own word: such a row gets no date and drops in `rules_pass`
   (`_wd_older`), and so does an aggregator's copy of the same posting (the
   same tenant and requisition in the link, `wd_posting`), whatever date the
   aggregator gives, since with the Workday row gone a copy keyed the same
   would take its place. Both are counted in `counts.workday_30_plus`, after
   the title rules, so the count is rows that would otherwise show. No extra
   fetch: "30+" never covers a posting inside the window.
   The PM track (added 2026-11-03 at the group's request): project, program
   and technical program managers, project coordinators, and product managers
   and owners, when the work is technology, IT or security (`pm_track`). The
   title has to name the work (`PM_TECH`: IT, software, cloud, data, platform,
   SOC, IAM, GRC, Epic...), unless the posting is on a technology employer's
   own board (`TECH_SOURCES` and `TECH_WORKDAY`: the hand-kept Greenhouse, Lever
   and Ashby boards, not the found ones, and the tech Workday tenants), where "Senior Product Manager,
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
   The SE track (added 2026-11-10, for a laid-off infosec sales engineer in
   the group): sales engineers and the other technical pre-sales roles, when
   what they sell is technology (`se_track`). A sales or solutions engineer,
   solutions consultant or pre-sales title is proposed whatever the employer;
   a solutions architect only on a vendor's own board (`tech_source`, plus
   Amazon, whose solutions architects are AWS's sales engineers, "SA" in its
   titles too), under Foorilla's infosec topics, or when the title names the
   sale ("Partner Solutions Architect"); elsewhere a solutions architect
   designs the employer's own systems and reads as before. Google's
   "Customer Engineer" counts when the title or board says technology.
   Recruiting, legal, construction, HVAC and physical-security titles are
   always out (`SE_HARD_NOT`); the rest of the excluded words give way to a
   technology word, as for PMs. In an SE title "Architect" is the job, not
   the grade: "Solutions Architect" is mid. **Jev confirms every se row**
   (held back as `jev.se_unconfirmed` until it does): "Sales Engineer" is as
   often valves, trucks or HVAC as software, and the title rarely says
   which. Jev may also move a vendor's other names for a sales engineer
   ("Systems Engineer" at Palo Alto, "Field Engineer" at Databricks) into
   se, but no other title. **Then the posting itself decides**: Jev's track
   answer sees a title and, from most boards, no text, and an audit of the
   first dry run (2026-11-10) found 43 of 210 se rows were not sales
   engineering (professional services and deployment after the sale,
   support, customer success, an employer's own systems). So the reader
   asks one more question of an se posting's own sentences
   (`PRESALES_Q`), and a "no" sends the row back to the track its title had
   before the SE track, cyber or eng, or off the page (`not_presales`).
   Against the audit: 30 of 39 caught, 5 of 150 real sales engineers lost,
   93% of the track right (was 80%). The Kind is what the engineer sells:
   security, cloud and infra, or software and data. Sources search for the titles:
   Foorilla (remote), Workable, the Workday search tenants, Amazon; every
   whole-board source needed nothing.
2. **Jev** (TypeSafe's typed classifier).
   Three typed questions per new posting: track (cyber / eng / pm / se / neither), level
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
   BambooHR and Workable job APIs, and otherwise the page's schema.org
   `JobPosting` data. Hosts that bar automated access are never fetched,
   a redirect included (`NO_FETCH`: governmentjobs.com). ADP, Dayforce, UKG and SAP pages
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
     posting Jev still owes answers to is never cached. A posting Jev has
     since moved to another track with a Kind (cyber, eng, se) is read
     again, since a Kind belongs to the track it was read under
     (`read_due`). `jobs.json` reports
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

## Patch notes

`changes.html` holds the patch notes, newest first, one `<section id="vX.Y">`
per version. The masthead links to it with the current version
(`<a id="version" ...>vX.Y</a>`), and CI fails if the two differ. The unit
installs `changes.html` beside `index.html`.

A PR that changes what people see (`index.html`, `splat.py`,
`company_check.py`, `infra/intake.py`) adds its entry and bumps the version in
the same PR; CI fails it otherwise. A fix nobody would notice gets the
`no-notes` label instead. A new entry is a minor bump (v2.1) for fixes and
small additions, a major one (v3.0) for a new feature. The notes are written
to the group, plainly, in David's voice; `company_names.json` updates need
none.

## Company check

"Company check" on each card opens public records about the employer, from
`companies.json`, which `company_check.py` writes beside `jobs.json` once a day
(`infra/company-check.service`, 07:00 Pacific, after the splat):

| Source | What it shows | How it matches |
|---|---|---|
| [USAspending](https://api.usaspending.gov) | Prime contract obligations per federal fiscal year, and how much DoD, DHS, ICE and CBP paid for (funding agency; DoD data runs about 90 days late) | The employer's reviewed UEIs; whatever joint ventures, sold units (`exclude_ueis`) and other listed employers' UEIs received inside that search comes off |
| NLRB, via [labordata/nlrb-data](https://github.com/labordata/nlrb-data) | Unfair-labor-practice charges against it since five years ago, and how they closed | Respondent name equals a reviewed legal name |
| [CourtListener](https://www.courtlistener.com/help/api/) | Federal employment civil-rights cases (nature of suit 442 and 445) | A docket party equals a reviewed legal name |
| [FEC bulk files](https://www.fec.gov/data/browse-data/?tab=bulk-data) | What the employer's connected PAC gave federal candidates, by party | The PAC's connected organization equals a reviewed legal name |
| [Violation Tracker](https://violationtracker.goodjobsfirst.org/) | A link only: its terms allow linking, not copying | Its parent list, else a name search (the first legal name its `avoid` list leaves) |
| The employer's own job board (`hiring`): the public, keyless board APIs of [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api), [Greenhouse](https://docs.greenhouse.io/job-board.html) and [Lever](https://github.com/lever/postings-api) | Open listings today, across all teams and places; their ages by the board's own date, as tags (under 30 days, 30 to 89, 90 or more; `companies.json` also holds the oldest date and the median, which the panel does not show); after 90 days of daily reading, roles taken down and listed again under a new listing ("Relisted roles" on the panel); Greenhouse prospect posts counted apart | By URL, not by name: the board the employer's Splat cards link to, with `splat.py`'s posting patterns. Cards on two or more boards give no numbers; cards on none (Workday, LinkedIn, an employer's own site) say so |

The hiring source, added 2026-11-12 after the group argued whether Vultr's
~62 open roles on its Ashby board were hiring:

- **The census.** Every run (daily, not weekly), each board found on today's
  cards is read once (400 a run, busiest first, 15 minutes at most; three
  failures in a row stop that vendor for the day, but a board the vendor says
  is not there, a 404 or 410, does not count toward them: the boards go in the
  same order every day, so three dead ones would stop the rest for good). An
  employer whose one board has not been read yet, or whose cards moved to a
  board not read yet, says so (`"state": "unread"`) instead of showing another
  board's numbers or promising a day; one whose board failed today keeps its
  last numbers, and the panel gives the day they were read
  (`checked.hiring`). Dates: Ashby's `publishedAt` (Ashby documents
  it as when the job was last published), Greenhouse's `first_published`
  (never `updated_at`, the last edit), Lever's `createdAt`; a listing without
  one counts as undated. Ashby's unlisted jobs are left out. A Greenhouse
  "prospect post" (a general-interest post with `internal_job_id` null, per
  Greenhouse's docs) is counted apart, not as a listing. Greenhouse's EU
  boards answer on its one API host; Lever's EU boards are their own.
- **The ledger**, `boards.json` in the state directory (not `company-data`,
  which holds downloads that can be fetched again): per board, the first and
  last day it was read and every day it was read (`reads`, as runs of days in
  a row), the card company name that most often links to it (`name`, a tie to
  the first in sort order) and how many cards do (`cards`), both leaving out
  members' tips and both as of its last read (a read whose cards give no name
  keeps the last one; an entry written before 2026-11-12 has neither until its
  next read, and `splat.py` reads no found board without a name), and per listing its title, place, board date, and the first and
  last day the census saw it. No descriptions. A listing not seen for 180
  days, a board not read for 180 days, and read days as old are pruned. A
  board that fails leaves its ledger as it was and its employers keep their
  last facts; a dry run writes no ledger.
- **Taken down and listed again** shows only after 90 days of daily reading.
  The census reads only the boards today's cards link to, so a board goes
  unread while its employer has no card on the page; a gap of more than three
  days starts the watch again (`READ_GAP`, `watch_start`), and the panel's
  "reading this board daily since" is that start. A role counts when a new
  listing has the same title and place as a different listing on the same
  board that the read days show was gone before the new one was up: on the
  last day the census read the board before the new listing's first day, the
  old one was already gone, or that read was the day before and the old one
  was still there (a handover overnight). That read must fall in the last 90
  days, and so must the new listing's own board date when it has one (and not
  before the old one's last day seen). Two listings up at once are several
  openings, not this, and so are two that may have been up together while
  nobody read the board; a listing that leaves and comes back under the same
  id is the same listing; two boards never match. Counted once per title and
  place, up to three examples with their place.
- **Wording**, for the page and anything written about it: counts and the
  board's own dates, each number linked to the board, and no verdict. Never
  "ghost", "fake", "stale", "suspicious", "red flag", "real", "placeholder",
  scores, colors or ranks, and never the bare word "repost" (the page uses
  "reposts" for recruiter copies); say "listing", "listed", "taken down and
  listed again", and never imply why. A long-listed role can be hard to fill,
  one listing for several hires, or republished by the board, and the panel
  says the numbers don't tell which. `infra/test_company_check.py` checks
  the page for those words.

Two kinds of employer:

- **Reviewed**, in `company_names.json`: job-board names mapped to legal names
  and UEIs, with an `avoid` list of look-alikes, checked against live
  USAspending data. Names are compared whole, after dropping case, punctuation
  and the corporate suffix; never as a substring.
- **Matched automatically**: every other employer on today's page (`jobs.json`).
  The card's name is the legal name, and USAspending is searched for it: one
  UEI under exactly that name is taken as the employer's; two or more mean
  several registrations share the name, and the panel shows search links
  instead of numbers. The panel says the match is automatic and shows what it
  matched. Court cases are not searched for these (CourtListener allows few
  searches a day). A looked-up name is cached for a week. The hiring source
  is matched by the cards' links, not the name, so these get it the same way
  as reviewed employers.

A charge or a lawsuit is an allegation, and the page says so.

### The weekly review

Once a week a scheduled Claude routine runs `python3 infra/company_gaps.py`,
which lists the busiest employers on the live page that the table does not
cover, maps about 15 of them into `company_names.json` by the rules below, has
a second pass check each one, runs the tests and opens a PR that merges when CI
is green. Nobody edits the table by hand; anyone can.

An entry:

```json
{"id": "palantir-technologies", "names": ["Palantir Technologies"],
 "kind": "public company", "legal": ["Palantir Technologies Inc.", "Palantir USG, Inc."],
 "avoid": ["Palantir.net, Inc."], "avoid_notes": ["Palantir.net, Inc. (Evanston IL web agency, unrelated)"],
 "ueis": [{"uei": "FSY4LVSBGWB7", "name": "PALANTIR TECHNOLOGIES INC.", "level": "P"},
          {"uei": "HNN4F9JZWDY8", "name": "PALANTIR USG INC", "level": "C"}],
 "exclude_ueis": [], "vt": null, "confidence": "high", "notes": "..."}
```

- `names`: the employer exactly as cards show it (every spelling in use).
- `legal`: US legal entity names a labor charge, court caption or FEC filing
  would use. Leave out one that equals a different company once the suffix is
  dropped, and put that company in `avoid`.
- `avoid`: bare entity names only; notes go in `avoid_notes`. An avoid name
  equal to a legal name blocks that legal name (the panel then says the
  records cannot be told apart).
- `ueis`: confirmed by USAspending's `/api/v2/recipient/` lookup; parents (P)
  plus self-parented records that hold contracts. No UEI already in the table.
- `exclude_ueis`: joint ventures, sold units and other companies' units that a
  search over `ueis` would count (find them with `spending_by_category/recipient`
  over FY window). Only what they received inside the search comes off.
- `unclear` (top level): job-board names reviewed and left out because the
  employer could not be identified; `company_gaps.py` skips them.

Then run `python3 infra/test_company_check.py`.

On the server it needs `COURTLISTENER_TOKEN` in `/etc/daily-splat.env` (a free
CourtListener account); without it the court source is skipped. Each source
refreshes weekly per employer, except hiring, which refreshes daily (`REFRESH`
in `company_check.py`), CourtListener within about 100 searches a day, and a
source that fails keeps its last facts.

- By hand: `sudo systemctl start company-check.service`
- Last run: `journalctl -u company-check.service -n 40`
- Test without writing: `sudo systemd-run --pipe --wait -p User=foundry -p EnvironmentFile=/etc/daily-splat.env -p StateDirectory=daily-splat /usr/bin/python3 /home/foundry/daily-splat/company_check.py --dry-run --dump /tmp/c.json`

## It fails closed

If fewer than half the sources answer, the script assumes the box lost network,
writes nothing and exits non-zero. Found boards are not counted: they are many
small sources on three vendors' APIs, so one vendor down would otherwise read
as the box offline. Same if zero postings survive. Yesterday's
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
- One source only: add `--only greenhouse` (or `workday`, `foorilla`, `ashby:openai`, `auto` for every found board, `auto:ashby:horizon3ai`, ...). Partial runs never write.

## Contributing

1. Fork, branch, change. The usual changes: an employer's board token in the
   dicts at the top of `splat.py`, or a rule in `where`, `rule_track` or
   `rule_level` with a test case for it in `infra/test_splat.py`.
2. Run the tests (`python3 infra/test_splat.py`) and, for a new source, one
   source by hand (`python3 splat.py --dry-run --only greenhouse --dump /tmp/s.json`).
3. Open a PR into `main`. If it changes what people see, add a patch notes
   entry (see "Patch notes"). It needs a maintainer's review; once merged it
   runs on the next morning's build.

Stdlib Python only: no pip dependencies. Never commit keys or `jobs.json`.
