# The Daily Splat

`https://iambraun.com/cv/job-sources/daily-splat/` is a tiny job aggregator for
the job-hunt cabal: cybersecurity openings plus the infrastructure engineering
next to them (cloud, network, systems, DevOps), mid to senior
level, in Washington, Oregon and Idaho or remote within the US. It is rebuilt
every morning, and a posting its source stopped listing is gone that day.

Same shape as `infra/job-sources` and `infra/stats`: generator in the docroot,
systemd timer, a generated JSON the deploy is told never to stomp.

- **Generator:** `site/cv/job-sources/daily-splat/splat.py`. Stdlib Python, no
  pip installs. Writes `jobs.json` beside itself.
- **Client:** `site/cv/job-sources/daily-splat/index.html` reads `jobs.json`
  and does all filtering in the browser. Filters live in the URL hash, so a view
  like `#track=cyber&where=pnw` can be pasted into the group chat.
- **State:** `/var/lib/daily-splat/state.json` (systemd `StateDirectory`):
  yesterday's postings, for "new today" and the failed-source grace, and the
  Jev answer cache, so each day only pays for its new postings.
- **Keys:** `/etc/daily-splat.env`, root:root 0600, holding
  `FOORILLA_API_KEY` and `TYPESAFE_API_KEY`. Kept on the server only.Neither is required; a missing one skips that
  source or pass and the run says so in `jobs.json` under `sources` / `jev`.

## Where the postings come from

| kind | sources | how removal works |
|---|---|---|
| Company boards | Greenhouse, Lever, Ashby, Workday tenants and Amazon, listed at the top of `splat.py` | the board stops returning the job |
| Open aggregators | Remotive, RemoteOK, Jobicy, Himalayas, Working Nomads, The Muse | the feed stops returning the job |
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
   Sales, recruiting, hardware, mechanical, civil and the like are out. Level: no intern/junior/`Engineer I`, no director/VP/head-of/C-level.
   Recruiter and staffing reposts are dropped: the page lists employers.
2. **Jev** (TypeSafe's typed classifier).
   Three typed questions per new posting: track (cyber / eng / neither), level
   (entry / mid / senior / staff / executive), clearance required. "Neither",
   "entry" and "executive" drop the posting. A title that states its level
   keeps the rule's level. About 800 input tokens per posting at $0.042 per
   million (the first run: 5,687 postings, 4.7M tokens). Answers are cached
   under `JEV_VERSION`; bump it whenever the questions change meaning.

`infra/daily-splat/test_splat.py` holds the rule cases, most of them bugs the
first sample run shipped. Run it after touching a regex:

```bash
python3 infra/daily-splat/test_splat.py
```

## It fails closed

If fewer than half the sources answer, the script assumes the box lost network,
writes nothing and exits non-zero. Same if zero postings survive. Yesterday's
`jobs.json` stays up. A single source failing is normal: its postings from the
last two days are carried and marked "Source down today" on the page.

## One-time setup on the Foundry VPS

```bash
sudo cp infra/daily-splat/daily-splat.service /etc/systemd/system/
sudo cp infra/daily-splat/daily-splat.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now daily-splat.timer
sudo systemctl start daily-splat.service      # first run now, so the page has data
```

## Running it

- By hand: `sudo systemctl start daily-splat.service`
- Next run: `systemctl list-timers daily-splat.timer`
- Last run: `journalctl -u daily-splat.service -n 80`
- Test without writing (keys from the env file):
  `sudo systemd-run --pipe --wait -p User=foundry -p EnvironmentFile=/etc/daily-splat.env /usr/bin/python3 /var/www/iambraun.com/cv/job-sources/daily-splat/splat.py --dry-run --dump /tmp/splat.json`
- One source only: add `--only greenhouse` (or `workday`, `foorilla`, `ashby:openai`, ...). Partial runs never write.
