---
kind: durable
---

# Daily Splat data API

The Daily Splat publishes its data as two JSON files that anyone can fetch, with
no key and no sign-up. Both are rebuilt every morning.

| File | What it holds | Lands by |
|---|---|---|
| `https://worksearchcollective.com/splat/jobs.json` | Today's job list, plus how the run went | about 06:35 Pacific |
| `https://worksearchcollective.com/splat/companies.json` | Public records for each employer on the list (the Company check) | about 08:10 Pacific |

The page also posts its two forms to `POST /api/splat-intake`. That endpoint is
for the page, not for scripts. It is described [at the end](#post-apisplat-intake)
so the page and the service can be maintained.

This reference was checked against the code and the live files on 2026-11-14.
Where it and the code disagree, the code wins: open an issue.

## Status: version 0

- Neither file has a version field yet. A merged change to either file's shape
  is live the next morning.
- Fields can be added without notice. Read the fields you know and ignore the rest.
- Enum values can be added too (the `se` track came in v2.1). Treat an unknown
  value as "other".
- Optional row keys appear only when they are true or known, so a missing key is
  not an error.
- `jev`, `reader` and `stats` are the operator's run diagnostics. They can change
  at any time.

## Reuse

Publishing these files is not a license to republish them. Most rows are job
listings that belong to the employers. The Splat reads them through job boards,
aggregators and hiring systems, each with its own terms, and some of those terms
do not allow passing listings on to others. There is no license for the files
yet. Open an issue on this repo and ask before you republish the data or build a
product on it.

Two rules apply whatever you do with the files:

- **Court records** (`companies.*.courts`) come from CourtListener. Its terms
  forbid using them, or anything derived from them, to decide a person's
  eligibility for credit, insurance or employment, or to build a consumer report.
  The case names name the people who sued.
- **PAC records** (`companies.*.pac`) are committee-level FEC data, with no
  individual donors. If you join the committee IDs to FEC individual-contribution
  records, federal law (52 U.S.C. 30111(a)(4)) bars using that information to ask
  for contributions or for commercial purposes.

## Fetching

- HTTPS only. `http://`, `www.` and `worksearchcollective.org` redirect (301) to
  `https://worksearchcollective.com`, keeping the path and query.
- The files are static: no auth, no paging, no query parameters, no rate limit.
  `Range` requests are ignored, so you always get the whole file.
- Every response has `Cache-Control: no-cache`, an `ETag` and `Last-Modified`.
  Send the ETag back in `If-None-Match` exactly as you got it (it arrives as
  `W/"..."`), and an unchanged file answers `304` with no body.
- Compression follows `Accept-Encoding`: gzip, br or zstd. On 2026-11-14
  `jobs.json` was 855 KB raw and 139 KB gzipped, and `companies.json` was 704 KB
  raw and 87 KB gzipped.
- A browser page on another site cannot read the files, because there is no
  `Access-Control-Allow-Origin` header. (`companies.json` sends one for
  `https://iambraun.com` only, the Splat's old address, until about 2026-12-13.)
  Fetch from a server.
- Fetch once a day: `jobs.json` after 06:45 Pacific, `companies.json` after 08:15
  Pacific. There is no feed or push.
- During a write, `jobs.tmp` or `companies.tmp` can exist for a moment. Fetch only
  the `.json` names; they are swapped in whole.

### Times

All times and dates in both files are UTC.

- `generated_at` (`YYYY-MM-DDTHH:MM:SSZ`) is when the run started, not when the
  file was written. The write comes a few minutes later.
- `date`, `first_seen`, `last_seen` and every `checked` date are `YYYY-MM-DD`, the
  UTC date when the run started. A scheduled run (05:30 Pacific plus up to 20
  minutes for jobs, 07:00 plus up to 10 for companies) falls on the same day in
  Pacific time. A run started by hand after 16:00 Pacific (17:00 in summer)
  carries the next day's date.
- `posted` is the source's own calendar date, with no time zone.

### Days that go wrong

- `jobs.json` is not rewritten when fewer than half of the listed sources answer,
  or when no rows survive. Yesterday's file stays up with yesterday's `date`, so
  compare `date` with today's UTC date before you treat a row as current.
- When one source fails, its rows from the last run are kept for up to two days
  and marked `carried` (see [Carried rows](#carried-rows)).
- `companies.json` is always written, and it is rewritten after each stage of its
  run. Between about 07:00 and 08:10 Pacific its content can change while
  `generated_at` stays the same, so use `ETag` or `Last-Modified` to see a change.
  A check that failed keeps its last facts and shows the failure in `stats`.
- From about 05:50 to 07:10 Pacific, `jobs.json` is today's and `companies.json`
  is yesterday's, so an employer new that day has no record yet.

### Text and links

Both files are UTF-8 JSON on one line, with non-ASCII characters written as they
are. Every text field and every URL comes from a third party.

- Escape all text before you show it.
- Check a URL's scheme before you link it. One live row uses `http://`.
- Normalize whitespace before you compare text. Some fields carry no-break
  spaces, doubled spaces or newlines, and one live title is mis-encoded at its
  source.

## jobs.json

```json
{
  "generated_at": "2026-11-14T13:49:53Z",
  "date": "2026-11-14",
  "rules": {"max_age_days": 30, "remote_max_age_days": 14, "grace_days": 2},
  "counts": {"total": 1594, "new_today": 155, "...": 0},
  "sources": [{"id": "foorilla", "label": "Foorilla", "ok": true, "fetched": 3813, "kept": 842, "error": null}],
  "jev": {"...": 0},
  "reader": {"...": 0},
  "jobs": [{"id": "302ccade3e17", "title": "...", "...": "..."}]
}
```

### rules

The age and grace limits the run used.

| Field | Type | Meaning |
|---|---|---|
| `max_age_days` | integer | A job in WA, OR or ID drops when its `posted` date is more than this many days before `date`. Now 30. |
| `remote_max_age_days` | integer | The same for remote jobs. Now 14. |
| `grace_days` | integer | How long a failed source's rows are kept (see [Carried rows](#carried-rows)). Now 2. |

### counts

All integers, always present. These describe the rows in `jobs`, carried rows
included, and can be recomputed from them:

| Field | Counts |
|---|---|
| `total` | Rows in `jobs`. Never 0: a run with no rows writes nothing. |
| `new_today` | Rows whose `first_seen` is `date`. |
| `carried` | Rows marked `carried`. |
| `cyber`, `eng`, `pm`, `se` | Rows on each `track`. They add up to `total`. |
| `entry` | Rows with `level` `entry`. Other levels have no count. |
| `oregon` | Rows with `where` `oregon`. |
| `pnw` | Rows anywhere in WA, OR or ID: `where` `oregon` or `pnw`. On a row, `pnw` means Washington or Idaho only; the count keeps its old meaning so old links still work. |
| `remote` | Rows with `where` `remote`, plus local rows marked `also_remote`. It overlaps `pnw`. |
| `read` | Rows marked `read`. |
| `pay` | Rows with a `salary`. |

These count what the run removed, and cannot be recomputed from the file:

| Field | Counts |
|---|---|
| `dropped_since_last_run` | Jobs in the last written run that are not in this one, for any reason. |
| `remote_twins_folded` | Remote listings merged into the same job's local row (which is then `also_remote`). |
| `tip_twins` | Member tips dropped because the same employer and title were already listed. |
| `older_by_board` | Aggregator copies dropped once the employer's own board showed the posting was past the age limit. |
| `workday_30_plus` | Workday postings, and copies of them, dropped because Workday showed them as "30+ days ago" past the age limit. |
| `board_copies` | Second rows of one board posting under another title or place. |

### sources

One entry for each source the run tried, sorted by `id`.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | The source's key, such as `foorilla`, `usajobs`, `tips`, `greenhouse:okta` or `workday:nvidia`, or `auto:<vendor>:<board>` for an employer board found through the aggregators. Found boards change from day to day, and a board name can contain a space. |
| `label` | string | Display name. Fresh rows carry it in `jobs[].source`, which is the only way to tie a row to its source. |
| `ok` | boolean | False when the source failed this run. Check this rather than `error`. |
| `fetched` | integer | Rows the source returned before any rule. 0 when `ok` is false. |
| `kept` | integer | Fresh rows on today's list credited to this source. Carried rows are not counted. |
| `error` | string or null | Why it failed, up to 200 characters, or null. It can be an empty string. |

### jev and reader

Diagnostics for the run's two AI passes, kept so the operator can see a bad day.
Jev (TypeSafe's classifier) confirms each job's track and level. The reader reads
each posting once and pulls out pay, clearance, office days, work mode, education
and years. Every key is always present: integer counts, and `error`, a string or
null. Don't build on these. They change with the pipeline.

### jobs

One row per role, newest first: by `posted` (or `first_seen` when there is no
`posted`), then by `first_seen`.

Every row has these keys:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | 12 hex characters: a hash of the employer, the title and `where`, not of the URL. Use it to follow a role from day to day (see [Identity](#identity)). |
| `title` | string | The title as the source gives it. |
| `company` | string | The employer's name as the source gives it. One employer can appear under several spellings ("T-Mobile", "T-Mobile USA, Inc."). For one record per employer, see [Joining jobs to companies](#joining-jobs-to-companies). |
| `url` | string | Where to read and apply: the employer's own posting for board rows, the aggregator's apply link for aggregator rows, the shared link for member tips. Not unique (see [Duplicates](#duplicates)). |
| `location` | string | The source's place text, for display. Cut to 160 characters with no marker. Filter on `where`. |
| `where` | string | `oregon`: Oregon, or the Portland area including Vancouver, Camas and Washougal WA. `pnw`: Washington or Idaho, never Oregon. `remote`: remote within the US. A local place wins over remote. Set from the full location before the cut, by rules that sometimes misfire (a "River Bend, LA" can land in `oregon`). |
| `track` | string | `cyber` (security), `eng` (infrastructure and IT: cloud, platform, DevOps and SRE, network, systems, support), `pm` (project, program and product management of technology work) or `se` (sales and solutions engineering). Title rules propose it and Jev confirms it. |
| `level` | string | `entry`, `mid`, `senior` or `staff` (staff, principal and up, shown as "Staff+"). Interns, and directors and up, are never listed. |
| `posted` | string or null | The posting date the source gives, `YYYY-MM-DD`. Workday dates are estimates from "Posted N days ago". An aggregator copy takes the employer board's date when that is earlier. null when the source gives none (member tips). It can be later than `first_seen` when a source re-dates a posting. |
| `first_seen` | string | The first run of the current unbroken stretch with this `id` on the list. |
| `last_seen` | string | The last run whose source returned this job. Equals `date`, except on carried rows. |
| `salary` | string or null | Base pay as display text, or null. See [Salary](#salary). |
| `source` | string | The source's display name, the same as a `sources[].label`. Carried rows keep the label from the day they were last fetched. |
| `via` | string | `company` (the employer's own board or hiring system, USAJOBS included), `aggregator` (a feed across employers) or `member` (a "Share a job" tip). |
| `clearance` | boolean | True when the job is believed to need an active US government security clearance. |
| `checked` | boolean | True when Jev has answered for this row. False means title rules alone set `track` and `level`. |

These keys appear only when they apply. A missing flag means false. A missing fact
means not stated or not read, not "none".

| Field | Type | Meaning |
|---|---|---|
| `read` | `true` | The reader got at least 600 characters of the posting's own text. The facts below are most reliable on these rows. |
| `sub` | string | The kind of work within the track. cyber: `secops`, `appsec`, `cloudsec`, `iam`, `grc`, `offensive`. eng: `support`, `systems`, `network`, `cloud`, `datacenter`. pm: `project`, `product`. se: `sesec`, `seinfra`, `seapps`. |
| `mode` | string | `remote`, `hybrid` or `onsite`, from the employer's system, the posting's words, or last of all the aggregator's label. A row with `where` `remote` never says `hybrid` or `onsite`: those rows are dropped. |
| `days` | integer | Days a week in the office, 1 to 5 (the low end of a range). |
| `limit` | string | Remote rows: where the hire may live. `any_us`, `some_states` (named states or a region) or `time_zone` (anywhere in the US, hours tied to one zone). |
| `edu` | string | Education required. `none` (no degree, or a diploma), `or_exp` (a degree or experience instead, or a degree only preferred), `bachelor` (required) or `advanced` (master's or doctorate required). |
| `yrs` | integer | Years of experience asked, as a minimum. 0 means none asked, which is not the same as missing. |
| `clr` | string | The clearance the hire must hold. `obtain` (able to get one; none needed to start), `public_trust`, `secret` or `top_secret`. |
| `mgr` | `true` | The job manages people. |
| `clearance_check` | `true` | The employer is a federal or defense contractor, so the job may need a clearance the title doesn't mention. |
| `also_remote` | `true` | A local row whose remote listing was merged into it. Treat it as remote too. |
| `carried` | `true` | The row's source failed this run, and this is the row as last seen (see [Carried rows](#carried-rows)). |
| `gate` | string | `LinkedIn` or `Indeed`: a member's tip found only there. The Splat never reads those sites, so these rows have no reader facts. |

#### Identity

`id` follows a role, not a posting: one employer, one title, one place. Several
openings with the same title in the same place share one row and one link. The id
stays the same when the employer's own board replaces an aggregator copy. It
changes when a source respells the employer or the title, or when the place rules
change; the row then counts as both dropped and new that day. A day when
`new_today` and `dropped_since_last_run` both jump usually means a rules change,
not a hiring wave.

#### Duplicates

The key uses the employer's name as written, so one posting can show twice:

- under two spellings of one employer, with the same `url` (4 pairs on 2026-11-14);
- on Workday, under two URLs that differ only by a locale segment such as `/en-US/`;
- on two hosts, such as an employer's Workday site and its own careers site.

For one row per posting, drop rows whose exact `url` you have already seen, and
key Workday links on the tenant and the last path segment.

#### Carried rows

When a source fails, its rows from the last written run come back unchanged,
marked `carried`, with their old `last_seen`. They stay while `last_seen` is no
more than `grace_days` before `date`: with one run a day, two failed days, and
gone on the third. Carried rows are not re-checked against the age limits or
merged with remote twins, so a carried row can be a day or two past the limit, or
sit beside a fresh copy of the same job. The page labels them "Source down today".

#### When rows come and go

- A job appears in the first run where a source returns it and it passes the
  rules: place, track, level, age, an employer rather than a staffing firm, and a
  link that does not go through a middleman site. `pm` and `se` jobs also wait for
  Jev, so they can appear a day late.
- A job leaves in the first run where its source answers without it. It also
  leaves when it ages past the limit, when Jev rules it out of the tracks, when its
  posting says a remote job is hybrid or onsite after all, or, for a tip, when it
  cannot be verified.
- Reader facts (`salary`, `mode`, `clr` and the rest) can arrive a day or more
  after the row, and can change when a posting is read again.

#### Salary

There are two shapes, so parse it as text:

- Most rows: `$<low>–$<high>/yr` or `$<n>/yr`, or the same with `/hr`, with an en
  dash (U+2013) and no spaces. Amounts of $1,000 or more are whole thousands with
  `K`.
- Ashby boards: Ashby's own summary, unchanged, such as `$207K – $244K`,
  `$83.4K – $166.8K` or `$19 – $21 per hour`. Most carry no unit.

Nearly all pay is in US dollars, but not every source is checked: one live row
reads `AED 540K – AED 715K`. Aggregator figures are not sanity-checked and can be
implausible.

## companies.json

```json
{
  "generated_at": "2026-11-14T15:01:59Z",
  "as_of": {"contracts": "11/14/2026", "labor": "2026-11-09", "pac": "2026-11-09"},
  "stats": {"contracts": {"updated": 58, "kept": 582, "failed": 2, "error": "..."}, "...": {}},
  "aliases": {"mongodb": "mongodb", "second front systems": "auto-second-front-systems"},
  "companies": {"mongodb": {"id": "mongodb", "names": ["MongoDB"], "...": "..."}}
}
```

The Company check covers every employer on the job list. 180 employers are
reviewed by hand: `company_names.json` in this repo maps their board names to
legal entities and federal contractor IDs. The rest are matched automatically by
name and marked as automatic.

### as_of

How current each bulk source is.

| Field | Meaning |
|---|---|
| `contracts` | USAspending's own last-update date, in its format, `MM/DD/YYYY`. Can be null. |
| `labor` | The date the Splat downloaded its copy of the NLRB data, `YYYY-MM-DD`. |
| `pac` | The date the Splat downloaded the FEC files, `YYYY-MM-DD`. |

### stats

Counters for this run only, one object per stage: `discover`, `contracts`,
`labor`, `courts`, `pac`, `violations` and `hiring`. Each has `updated` (employers
refreshed), `kept` (employers whose facts carried over), `failed` and `error`
(the first error, or null). `hiring` also has `boards`, with `found`, `read`,
`failed` and `skipped`. All zeros means the stage did not run. These are operator
diagnostics, like `jev`.

### Joining jobs to companies

```
key    = jobs[].company, lowercased, with "&" replaced by " and ",
         every run of characters outside a-z and 0-9 replaced by one space,
         and the ends trimmed
record = companies[aliases[key]]
```

All 1,594 rows joined on 2026-11-14. A miss means no record yet. Look up through
`aliases` on every fetch rather than storing ids, because an automatic id changes
when its employer is reviewed. One employer can have two records under two
spellings (`t-mobile` and `auto-t-mobile-usa-inc`), so don't add records together.

### The record

`companies.<id>` has:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Same as the key. Reviewed: the id in `company_names.json`. Automatic: `auto-` plus the name. |
| `names` | array | The employer's name as job cards show it. |
| `legal` | array | Legal entity names. Reviewed: the table's list. Automatic: the card name. |
| `matched` | array | The legal names the labor, court and PAC checks compare against. A match is the whole name after dropping case, punctuation and suffixes such as "Inc.", never part of a name. Empty when every name is shared with another entity. |
| `search_name` | string | The name to use in a search link. |
| `confidence` | string or null | The reviewer's confidence in the name mapping, `high` or `medium`. null for automatic matches. |
| `auto` | object | Only on automatic matches. `ueis`: how many USAspending registrations had exactly this name in the first 50 search results. `ambiguous`: true when two or more did, so their records cannot be told apart. |
| `checked` | object | For each check that has facts, the date they were fetched: `contracts`, `labor`, `courts`, `pac`, `violations`, `hiring`. A date older than today means the check failed or was not due, and the older facts stand. |
| `contracts`, `labor`, `courts`, `pac`, `violations`, `hiring` | object | One per check, below. |

#### Missing, null, empty, ambiguous

- A check's key missing means that check has never succeeded for this employer.
- `contracts: null` means checked, with no contractor ID to search.
- `{"ambiguous": true}` under `labor`, `courts` or `pac` means every legal name is
  shared with another entity, so records cannot be told apart by name. It does not
  mean none were found. Show search links, never a zero.
- A zero count means none under the matched names in the window.

#### contracts

Federal prime contract money from USAspending, searched by contractor ID (UEI),
not by name.

| Field | Meaning |
|---|---|
| `fy` | Three rows, newest first: the current federal fiscal year so far, then the two before. Fiscal year N runs from Oct 1 of N-1 to Sep 30 of N. |
| `fy[].fy` | The fiscal year. |
| `fy[].total` | Net prime contract obligations that year across all agencies, contracts only (no grants or loans), in whole dollars. Floored at 0. |
| `fy[].dod`, `dhs`, `ice`, `cbp` | The part paid for by the Department of Defense, Homeland Security, ICE and CBP, by funding agency. Each is floored at 0 on its own, so the parts can add up to more than `total`. Defense data runs about 90 days late. |
| `fy[].less` | What came off `total` for units that are not this employer, such as joint ventures and sold units. Can be negative. |
| `ueis` | The contractor IDs searched. |
| `excluded` | The IDs whose money came off. |
| `link` | The USAspending recipient page, or null. |
| `name` | USAspending's name for the first ID. |

#### labor

National Labor Relations Board unfair labor practice charges against the employer
(case type CA) filed in the last five years. A charge is an allegation, not a
finding.

| Field | Meaning |
|---|---|
| `since` | Start of the window, `YYYY-MM-DD`. |
| `charges` | Distinct charges in the window. |
| `open` | Charges not closed. |
| `closed` | Closed charges counted by the NLRB's reason, such as `Withdrawal Non-adjusted` or `Informal Settlement`. |
| `names` | Up to 6 respondent names as filed. |
| `recent` | Up to 5 newest charges, each with `case`, `filed`, `status` (`Open` or `Closed`), `reason` (null while open) and `link` (the nlrb.gov case page). |

#### courts

Federal employment discrimination and ADA employment cases (CourtListener, nature
of suit 442 and 445) naming the employer, filed in the last five years. Reviewed
employers only. A filed case is an allegation, not a finding. See [Reuse](#reuse)
for CourtListener's rule.

| Field | Meaning |
|---|---|
| `since` | Start of the window. |
| `cases` | Distinct cases. A lower bound when `more` is true. |
| `more` | True when there were more results than the check reads. |
| `unclosed` | Cases with no closing date on record. Many cases never get one, so this is not "open". |
| `recent` | Up to 4 newest, each with `name` (the case name, which names the person who sued), `court`, `filed`, `closed` (or null) and `link`. |

#### pac

The employer's corporate political action committee and its direct gifts to
federal candidates, from FEC bulk files. A PAC's money comes from executives,
managers and shareholders who choose to give. The company cannot give to federal
candidates itself.

| Field | Meaning |
|---|---|
| `committees` | The PACs connected to the employer, each with `id`, `name`, `org` (the connected organization as filed) and `link`. Often empty. |
| `cycles` | The current two-year cycle and the one before. Each has `cycle` (an even year); `dem`, `rep` and `other` in whole dollars, which can be negative after refunds; and `gifts`, a count of gifts, not dollars. `unpublished: true` marks a cycle the FEC has not published yet, with zero amounts. |

#### violations

A link to the employer on Violation Tracker (Good Jobs First). Its terms do not
allow copying its data, so the Splat only links.

| Field | Meaning |
|---|---|
| `link` | The Violation Tracker page. |
| `kind` | `parent` (the employer's parent-company page) or `search` (a name search). |

#### hiring

Today's read of the employer's own Ashby, Greenhouse or Lever job board. The board
is found from the links on the employer's Splat cards, never by name. Counts cover
the whole board, every team and place.

| Field | Meaning |
|---|---|
| `state` | `ok` (the numbers follow), `none` (no Ashby, Greenhouse or Lever link on today's cards), `several` (the cards link to two or more boards, so no numbers) or `unread` (the board has not been read successfully yet). |
| `ats` | `ashby`, `greenhouse`, `lever` or `lever-eu`. |
| `link` | The board. |
| `open` | Listings open at the last read. |
| `dated` | Open listings with a board date. |
| `under_30`, `d30_89`, `d90_plus` | Dated listings by age from the board's own date: under 30 days, 30 to 89, and 90 or more. They add up to `dated`. |
| `oldest` | The oldest board date among open listings, or null. |
| `median_days` | The median listing age in days, or null. |
| `watching_since` | The first day of the current run of daily reads. |
| `relisted` | Roles taken down and listed again in the last 90 days: `roles`, `window_days` and `examples` (up to 3, each with `title`, `place` and `link`). null until a board has been read daily for 90 days, so not before 2027-02-11. |
| `prospect` | Greenhouse general-interest posts. They are not jobs and are not in `open`. |
| `boards` | With `several` only: the boards the cards link to. |

Board dates are each vendor's own: Ashby's last-published date, Greenhouse's
first-published date, Lever's created date. Some boards reset them when a role is
listed again.

### Describing this data

If you show this data, describe it the way the page does:

- Show counts and the source's own dates, each linked to its record. No scores,
  ranks, colors or verdicts.
- Call charges and lawsuits allegations.
- A long-open listing can mean a role that is hard to fill, several hires, or a
  board that republishes. Don't call listings "ghost", "fake" or "stale".
- Label automatic matches as automatic and show what they matched.

## POST /api/splat-intake

The page's two forms post here. "Tell me what would help" emails David. "Share a
job" stores a tip for the next morning's run and emails David. Please don't post
to it from scripts: every accepted post sends an email, there is no CORS, and the
spam checks treat anything but the page as a bot.

- `POST` only, one JSON object, up to 32,000 bytes.
- `kind`: absent for the help form, `tip` for Share a job, `tip_review` for David's
  signed remove and approve links.
- The fields and their length limits are `FIELDS` and `TIP_FIELDS` in
  `infra/intake.py`.
- Limits: 5 posts an hour per visitor (the app), and 2 requests a minute per
  visitor after a burst of 3 (nginx). Tips: 40 new a day and 300 held at once,
  across everyone.

The app always answers JSON with `ok`:

| Status | Body | When |
|---|---|---|
| 200 | `{"ok": true}` | The help form was sent, or a review link was applied. |
| 200 | `{"ok": true, "new", "review", "summary"}` | A tip was stored. `new` is false when that link was already stored. `review` is true when David has to approve it before it shows. |
| 400 | `{"ok": false}` | The body is not a JSON object. |
| 400 | `{"ok": false, "error"}` | A tip was refused. `error` says why, in plain words. |
| 400 | `{"ok": false, "need": "details", "error"}` | The tip's link does not say what the job is. Send the title, employer and place too. |
| 403 | `{"ok": false, "error"}` | A bad review link. |
| 413 | `{"ok": false}` | The body is empty or too large. |
| 429 | `{"ok": false, "error"}` | Over the hourly limit. |
| 502 | `{"ok": false, "error"}` | The email or the tip file failed. |
| 503 | `{"ok": false, "error"}` | Sharing is switched off. |

nginx answers with an HTML page instead for any method but POST (403), a body over
32 KB (413), its own rate limit (429), the service being down (502), and a reply
slower than 20 seconds (504).

## Linking to a filtered view

The page keeps its filters in the URL fragment, so a link can open a filtered
list:

```
https://worksearchcollective.com/splat/#track=cyber&where=oregon,remote&level=entry,mid
```

| Parameter | Values |
|---|---|
| `track` | `cyber`, `eng`, `pm` or `se` |
| `sub` | Kinds within the chosen track, comma-separated (the `sub` values above) |
| `where` | `oregon`, `waid` (Washington or Idaho) and `remote`, comma-separated |
| `level` | `entry`, `mid`, `senior` and `staff`, comma-separated |
| `only` | `new`, `week` (posted in the last 7 days), `pay` (shows pay) and `noclr` (no clearance), comma-separated. A row must meet all of them. |
| `q` | Search words. Each must match the start of a word in the title, employer or place. |

Unknown values are dropped, and the page ignores query strings (`?track=...`).
`new` depends on the viewer's last visit, so it can differ from one device to the
next.
