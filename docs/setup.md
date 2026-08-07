# Setting up Sunder

Self-hosting guide: Supabase project, secrets, first deploy. Follow it in order
— step 6 is a gate, and the steps after it assume it passed.

The order here matches spec section 15. The one thing not to reorder is
verifying RLS before storing real data.

---

## 1. Prerequisites

| Tool | Version | Install |
|---|---|---|
| Node.js | LTS | `brew install node` |
| uv | latest | `brew install uv` — manages Python 3.12 for the sync service |
| Supabase CLI | latest | `brew install supabase/tap/supabase` |
| pre-commit | latest | `brew install pre-commit` |

```bash
git clone https://github.com/<you>/sunder.git
cd sunder
pre-commit install          # gitleaks + lint before every commit

cd apps/web && npm install && cd ../..
cd services/sync && uv sync --all-extras && cd ../..
```

---

## 2. Create the Supabase project

1. Create a project at [supabase.com](https://supabase.com). Pick a region close
   to you — every frontend read crosses it.
2. Wait for provisioning to finish.
3. Enable PostGIS: **Database → Extensions → postgis → Enable**. The first
   migration also creates it, but enabling it in the dashboard first avoids a
   permissions surprise on some project ages.

---

## 3. Collect the keys

**Project Settings → API**

| Value | Where it goes | Secret? |
|---|---|---|
| Project URL | `SUPABASE_URL`, `VITE_SUPABASE_URL` | no |
| `anon` `public` key | `SUPABASE_ANON_KEY`, `VITE_SUPABASE_ANON_KEY` | no — RLS protects the data |
| `service_role` key | `SUPABASE_SERVICE_ROLE_KEY` | **yes** — bypasses RLS entirely |

**Project Settings → Database → Connection string → URI**

| Value | Where it goes | Secret? |
|---|---|---|
| Direct connection URI | `SUPABASE_DB_URL` | **yes** — contains the database password |

> `SUPABASE_DB_URL` is used only by `backup.yml`. `pg_dump` needs a real
> Postgres session, so use the **direct** connection string, not the transaction
> pooler one.

Generate the two encryption keys yourself:

```bash
openssl rand -hex 32   # -> ENCRYPTION_KEY
openssl rand -hex 32   # -> BACKUP_ENCRYPTION_KEY
```

> **`ENCRYPTION_KEY` has no recovery path.** It is deliberately not stored in the
> database (spec 6.1), so if you lose it, every stored Garmin credential becomes
> permanently undecryptable and every user has to re-link their account. Keep a
> copy in a password manager before going further.

---

## 4. Add the GitHub Secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Used by |
|---|---|
| `SUPABASE_URL` | sync, deploy |
| `SUPABASE_ANON_KEY` | deploy |
| `SUPABASE_SERVICE_ROLE_KEY` | sync |
| `SUPABASE_DB_URL` | backup |
| `ENCRYPTION_KEY` | sync |
| `BACKUP_ENCRYPTION_KEY` | backup |

Garmin credentials are **not** secrets here — they arrive from users through the
UI and are stored encrypted in Supabase.

For local development, copy `.env.example` to `.env` and fill in the same
values. `.env` is gitignored and gitleaks will block it if that ever changes.

---

## 5. Run the migrations

```bash
supabase link --project-ref <your-project-ref>
supabase db push
```

This creates the schema, the indexes, the RLS policies and the private Storage
bucket for activity streams.

---

## 6. Verify RLS — do this before storing any real data

This is the gate. Spec section 11.3 exists because "RLS is enabled" and "RLS
works" are different claims.

```bash
cd services/sync
export SUPABASE_URL=https://<ref>.supabase.co
export SUPABASE_ANON_KEY=<anon key>
export SUPABASE_SERVICE_ROLE_KEY=<service role key>
uv run pytest tests/security -v
```

The tests create two throwaway accounts, verify that neither can see the other's
data, that `garmin_connections` is unreachable through the anon key even by its
own owner, and that a stored credential is genuinely ciphertext that the key
decrypts. They delete the accounts afterwards.

**Every test must pass before you continue.** Do not point these at a project
that already holds real data — they create and delete users.

---

## 7. Close registration

Spec 6.6: a public repository is not a public app.

1. **Authentication → Sign In / Providers → Allow new users to sign up = off**
2. Create your own account: **Authentication → Users → Add user**, with
   "Auto Confirm User" enabled.

RLS means that even an account created some other way sees nothing, but closing
signups removes the question entirely.

---

## 8. Deploy the frontend

1. **Settings → Pages → Build and deployment → Source = GitHub Actions**
2. Push to `main`. `ci.yml` runs, and `deploy.yml` publishes only if it passed.
3. Open the Pages URL and check that the app loads and you can sign in.

---

## 9. Link your Garmin account and run the first sync

1. Sign in, open the Garmin linking form, accept the Terms of Service warning.
2. **Actions → Sync → Run workflow** to trigger a run by hand rather than
   waiting for the hourly cron.
3. Check that activities appear, and that `sync_runs` recorded the run.

---

## 10. Verify the backup

**Actions → Backup database → Run workflow.** Confirm it produces a
`sunder-backup-*` artifact.

Then confirm you can actually restore it — an untested backup is a guess:

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
  -in sunder.dump.enc -out sunder.dump -pass env:BACKUP_ENCRYPTION_KEY
pg_restore --list sunder.dump | head
```

---

## 11. Protect main

**Settings → Branches → Add rule** for `main`: require status checks to pass,
selecting the `Frontend`, `Sync service`, `Security` and `Secret scan` checks.

---

## Local development

```bash
# Frontend
cd apps/web && npm run dev

# Sync service checks
cd services/sync
uv run ruff check . && uv run mypy && uv run pytest
```

A local Supabase stack, if you would rather not test against the real project:

```bash
supabase start          # prints local URL, anon key and service role key
supabase db reset       # applies every migration from scratch
```

## Troubleshooting

**The deployed page is blank.** Almost always the base path. A GitHub Pages
project site is served from `/<repo>/`, and `deploy.yml` sets `VITE_BASE_PATH`
from the repository name — check it matches if you renamed the repo.

**`Chybí proměnná prostředí VITE_SUPABASE_URL`.** The build ran without the
secrets. Check they are set at repository level, not environment level.

**`pg_dump: server version mismatch`.** Supabase upgraded past the client in
`backup.yml`; bump `postgresql-client-17` to the newer major.

**Sync says `auth_failed`.** The stored Garmin password no longer works —
usually because it was changed. Re-link the account through the UI.
