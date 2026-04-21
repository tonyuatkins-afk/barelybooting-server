# barelybooting-server

Companion server for [CERBERUS](https://github.com/tonyuatkins-afk/CERBERUS),
the DOS-era hardware diagnostic tool. Accepts uploaded `CERBERUS.INI`
files from real-hardware runs, parses them, and exposes a browse /
compare / archival UI at `barelybooting.com/cerberus/`.

## Architecture

Small Python/Flask web app with a flat SQLite schema. Intentionally
boring: MVP-friendly, deployable to any host that runs Python 3, and
migratable to Postgres later if submission volume demands.

- **Flask + Jinja** — server-rendered pages, no JS framework
- **SQLite** — single-file DB, denormalized `submissions` table with
  every extracted field as a column (filterable without joins)
- **No external services** — no redis, no celery, no s3. The raw INI
  bodies live in the DB alongside the extracted fields.

## API contract

See the upstream CERBERUS repo's `docs/ini-upload-contract.md` for
the full spec. Quick summary:

- `POST /api/v1/submit` — accepts raw CERBERUS.INI in body.
  Returns HTTP 200 with a two-line body: 8-char submission ID,
  then public view URL.
- Error response: any non-200 is treated by the client as "failed."

## Running locally

```
python -m venv .venv
source .venv/bin/activate        # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
python -m barelybooting init-db
python -m barelybooting run       # listens on http://127.0.0.1:5000
```

## Seeding real-hardware INIs

For testing + to give the browse UI something to display on day one:

```
python -m barelybooting seed path/to/ini-archive/
```

Walks the directory, POSTs every `.INI` to the running server. Uses
the same submit endpoint the DOS client will use.

## Routes

- `GET  /cerberus/`                    — browse (newest first, paginated)
- `GET  /cerberus/cpu/<class>`         — filter by CPU class
- `GET  /cerberus/machine/<hw_sig>`    — all runs from one machine
- `GET  /cerberus/unknown`             — unidentified hardware
- `GET  /cerberus/run/<id>`            — single submission detail
- `POST /api/v1/submit`                — accept INI upload
- `GET  /api/v1/health`                — `{"status":"ok"}`
- `GET  /cerberus/export/all.csv`      — stubbed, v0.7.1

## Deployment target

Production:  `barelybooting.com/cerberus/`
Staging:     any port on a VPS or home server
Dev:         `127.0.0.1:5000`

## License

MIT, same as CERBERUS.
