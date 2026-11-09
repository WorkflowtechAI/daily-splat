# daily-splat

Patch notes: a PR that changes what people see on the Splat (the page, the
listings, the company check, the intake form) ships with its entry in
`changes.html`. The mechanics and the version link are in README.md, "Patch notes".

- The entry is David talking to the job-hunt group: first person, plain, what
  broke and who reported it, caveats stated as caveats. Draft it with the
  personal-voice skill and show it to David before the merge; his wording wins.
- Every claim in the entry matches what the code and the live page do. Check
  each one before it ships.
- A fix nobody would notice carries the `no-notes` PR label instead. CI
  (`rules-tests`) fails a user-facing PR that has neither.
