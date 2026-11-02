## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for `lcp88-debug/gorges-gusts`, using the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Strava API

Read `docs/strava-api.md` before writing code that calls Strava, stores its data, or shows it to users.

Build and test with made-up data, and have the developer run anything that touches real Strava data: the API Policy restricts passing Strava data to AI tools (s.5.3).
