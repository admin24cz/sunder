# Sunder

Training log that syncs automatically with Garmin Connect, computes statistics,
route segments and personal records.

Open source, multi-user, designed to run entirely within free tiers.

---

## ⚠️ Disclaimer — Garmin Terms of Service

Garmin does not offer an official Developer API for a project of this kind.
Sunder logs into Garmin Connect **using your own credentials**, the same way the
website and the mobile app do (via [`garth`](https://github.com/matin/garth)).
This is **not** an OAuth integration.

**This is against the Garmin Terms of Service.** The realistic risk is that
Garmin rate-limits or blocks your Garmin account for automated access. You
accept that risk explicitly when you link your account, and you can unlink at
any time — unlinking hard-deletes the stored credentials.

Do not link an account you cannot afford to lose access to.

---

## Architecture

```
Garmin Connect (SSO, unofficial access)
        ▲
        │  1. log in + download new activities (rate-limited, with backoff)
        │
GitHub Actions (cron, Python: garth / python-garminconnect)
        │
        │  2. write activities, statistics and the sync log (service role key)
        ▼
Supabase (Postgres + Auth + Storage, protected by RLS)
        ▲
        │  3. read data (anon key + RLS, scoped to auth.uid())
        │
Frontend SPA (React + Vite, hosted on GitHub Pages)
```

| Layer | Technology |
|---|---|
| Frontend | React 18 + Vite + TypeScript (strict), Tailwind CSS |
| Maps / charts | MapLibre GL + OpenStreetMap, Recharts |
| Data / auth | Supabase (PostgreSQL + PostGIS + Auth + Storage) |
| Sync service | Python 3.12, `garth` / `python-garminconnect` |
| Automation | GitHub Actions (sync cron, CI, deploy, backup) |

## Repository layout

```
.github/workflows/   CI, deploy, sync cron, backup cron
apps/web/            React + Vite frontend
services/sync/       Python sync service (package: sunder_sync)
supabase/migrations/ Versioned SQL migrations (schema + RLS)
docs/                spec.md, setup.md, security.md, adr/
```

The frontend and the sync service share one data model, so they live in one
repository — one source of truth for the schema, atomic changes across layers.

## Quick start

```bash
# Frontend
cd apps/web
npm install
npm run dev

# Sync service
cd services/sync
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Full self-hosting instructions — Supabase project, secrets, first deploy — are
in [`docs/setup.md`](docs/setup.md).

## Security

Credentials are encrypted with AES-256-GCM before they ever reach the database,
and the encryption key lives only in GitHub Secrets — never in the database and
never in this repository. Row Level Security is enabled on every user table, and
`garmin_connections` is unreachable with the public anon key at all.

The full model, plus key rotation and incident response, is documented in
[`docs/security.md`](docs/security.md).

## Documentation

| Document | Contents |
|---|---|
| [`docs/spec.md`](docs/spec.md) | Project specification — the source of truth for decisions |
| [`docs/setup.md`](docs/setup.md) | Self-hosting guide |
| [`docs/security.md`](docs/security.md) | Security model, key rotation, incident response |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records |

## License

MIT
