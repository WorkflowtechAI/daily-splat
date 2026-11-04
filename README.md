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
  like `#track=cyber&where=pnw` can be pasted into the group chat.
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

`infra/test_splat.py` holds the rule cases, most of them bugs the
first sample run shipped. Run it after touching a regex:

```bash
python3 infra/test_splat.py
```

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
sudo systemctl daemon-reload
sudo systemctl enable --now daily-splat.timer
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
