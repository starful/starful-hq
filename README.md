# Starful HQ

Local back office for managing the Starful sites. A virtual office where AI staff handle writing, SEO, images, PRs and deploys in one flow.

- URL: http://127.0.0.1:8091
- Run: `./start.sh` (or `./start.sh 7777`)
- Sites: the `hq:` blocks in `/opt/work/sites.yaml`

## Layout

```text
starful-hq/
  start.sh          ← run
  app/              ← code
    routes.py       ← pages and API (Flask)
    sites.py        ← sites.yaml loader
    office/         ← floors, staff, meetings
    work/           ← tasks, run queue, agents, deploys
    content/        ← posts and images (per site type)
    money/          ← affiliate, GCP costs, HQ click usage
    snapshot.py     ← cached numbers
    templates/ static/
  docs/SPEC.md      ← development spec
  data/             ← runtime state (not source)
  tests/
```

Code and data are kept apart. `data/` is local state and is not committed.

## Documents

- Business requirements: the Starful HQ plan (HTML) in Google Drive `AI/Starful HQ/`
- Screen design: Claude Design canvas "Starful HQ 화면 설계"
- Development spec: [`docs/SPEC.md`](docs/SPEC.md)

## Repository rules

- File and folder names are English only: letters, digits, `-`, `_`, `.` (no Korean, no spaces). Checked by `tests/test_filenames.py`.
- README is written in English by default.
- Commit instead of copying to `.bak/`. `data/`, `.bak/`, `__pycache__/` and `.env` are ignored.
