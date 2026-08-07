# Sunder — specifikace projektu

*Tréninkový deník: automatická synchronizace s Garmin Connect, statistiky, segmenty a osobní rekordy.*

**Verze:** 2.0 (produkční revize)
**Stav:** připraveno k implementaci
**Umístění v repu:** `docs/spec.md` — zdroj pravdy o architektuře a rozhodnutích

---

## 1. Cíl projektu

**Sunder** je webová aplikace pro sledování sportovních aktivit, která se automaticky synchronizuje s Garmin Connect, počítá statistiky, segmenty tratí a osobní rekordy. Open-source (veřejné repo), víceuživatelská, provozovaná v rámci free tierů.

**Provozní model:** start jako uzavřená instance jen pro majitele, postupné otevírání dalším uživatelům až po ověření bezpečnosti a stability (viz sekce 6.6).

---

## 2. Architektura

Garmin nemá dostupný oficiální Developer API přístup pro tento typ projektu. Použije se **neoficiální přístup přes stejné přihlášení jako Garmin Connect web/appka** (`garth` / `python-garminconnect`) — ne OAuth. Architektura proto stojí na periodické úloze, která se přihlásí jménem uživatele a stáhne data.

> **Disclaimer:** Tento přístup je proti Garmin Terms of Service. Riziko je omezení/zablokování Garmin účtu za automatizovaný přístup. Musí být uvedeno v README a odsouhlaseno uživatelem při propojování účtu.

### 2.1 Datový tok

```
Garmin Connect (SSO, neoficiální přístup)
        ▲
        │  1. přihlášení + stažení nových aktivit (rate-limited, s backoffem)
        │
GitHub Actions (cron, Python: garth / python-garminconnect)
        │
        │  2. zápis aktivit, statistik a sync logu (service role key)
        ▼
Supabase (Postgres + Auth + Storage, chráněno RLS)
        ▲
        │  3. čtení dat (anon key + RLS, scoped na auth.uid())
        │
Frontend SPA (React + Vite, hostováno na GitHub Pages)
```

### 2.2 Hosting a limity

| Komponenta | Služba | Limit zdarma | Pozn. |
|---|---|---|---|
| Frontend | GitHub Pages | prakticky bez limitu | statický build |
| Sync + CI | GitHub Actions | fair use (veřejné repo) | limit 2000 min/měsíc platí jen pro privátní repa |
| DB + Auth + Storage | Supabase free | 500 MB DB, 1 GB storage, 5 GB egress, 50k MAU | **žádné zálohy** → viz 8.3 |

**Pozastavení Supabase projektu:** free projekt se pozastaví po 7 dnech bez requestů. Hodinový sync cron ho udržuje aktivní automaticky — není potřeba samostatný keepalive.

**Frekvence synchronizace:** periodická (výchozí 1×/hod), ne real-time. Odpovídá požadavku "průběžné stahování".

---

## 3. Tech stack

| Vrstva | Volba | Zdůvodnění |
|---|---|---|
| Frontend | React 18 + Vite + TypeScript | TS povinné — u výpočtů segmentů/statistik chrání před tichými chybami |
| Routing | React Router | standard |
| Data fetching / cache | TanStack Query | cache, retry, invalidace — bez psaní vlastní vrstvy |
| State | TanStack Query + React Context | globální store (Redux/Zustand) není pro tento rozsah potřeba |
| Styling | Tailwind CSS | viz poznámka níže |
| Mapy | MapLibre GL + OpenStreetMap | zdarma, bez API klíče; lepší výkon pro heatmapy než Leaflet |
| Grafy | Recharts | dostačující pro elevační profil, tempo, agregace |
| Backend sync | Python 3.12 + `garth` / `python-garminconnect` | jediné udržované knihovny pro Garmin |
| DB | Supabase (PostgreSQL + PostGIS) | relační data, nativní RLS, PostGIS pro geometrii segmentů |
| Testy FE | Vitest + React Testing Library + Playwright | viz sekce 11 |
| Testy BE | pytest + pytest-mock | viz sekce 11 |
| Lint/format FE | ESLint + Prettier | |
| Lint/format BE | Ruff + mypy | |
| Pre-commit | pre-commit hooks | zabrání commitu neformátovaného kódu a secrets |

**Poznámka k Tailwindu:** V lednu 2026 Tailwind Labs propustila 75 % inženýrského týmu kvůli propadu tržeb z placených produktů (návštěvnost dokumentace klesla vlivem AI nástrojů). Framework samotný zůstává open-source pod MIT licencí a zdarma — zakladatel to explicitně potvrdil. Riziko pro projekt je pouze pomalejší vývoj nových funkcí, ne výpadek. Alternativa při obavách: CSS Modules / vanilla CSS.

---

## 4. Struktura repozitáře

```
/
├── .github/
│   └── workflows/
│       ├── ci.yml              # lint + testy při každém PR
│       ├── deploy.yml          # build + deploy frontendu na GH Pages
│       ├── sync.yml            # cron: stažení dat z Garminu
│       └── backup.yml          # cron: záloha DB (viz 8.3)
├── apps/
│   └── web/                    # React + Vite frontend
│       ├── src/
│       │   ├── components/     # znovupoužitelné UI komponenty
│       │   ├── features/       # activities/, segments/, stats/, auth/
│       │   ├── lib/            # supabase klient, formátování, utils
│       │   ├── hooks/
│       │   ├── pages/
│       │   └── types/          # sdílené TS typy (generované ze Supabase schématu)
│       └── tests/
├── services/
│   └── sync/                   # Python sync služba
│       ├── src/
│       │   ├── garmin/         # klient, retry, rate limiting
│       │   ├── crypto/         # šifrování/dešifrování credentials
│       │   ├── parsers/        # FIT/GPX → doménový model
│       │   ├── computations/   # statistiky, segmenty, rekordy
│       │   └── db/             # zápis do Supabase
│       └── tests/
│           └── fixtures/       # ukázková Garmin data (anonymizovaná)
├── supabase/
│   ├── migrations/             # verzované SQL migrace
│   └── seed.sql                # testovací data pro lokální vývoj
├── docs/
│   ├── spec.md                 # tento dokument
│   ├── setup.md                # self-hosting návod
│   ├── security.md             # bezpečnostní model podrobně
│   └── adr/                    # Architecture Decision Records
├── .env.example
├── .pre-commit-config.yaml
└── README.md
```

**Proč monorepo:** frontend a sync služba sdílí datový model; jedno repo znamená jednu pravdu o schématu a atomické změny napříč vrstvami.

---

## 5. Funkční požadavky

### 5.1 Autentizace a účty
- Registrace / přihlášení do appky (Supabase Auth, email+heslo, volitelně Google OAuth)
- Propojení s Garmin Connect: uživatel zadá své Garmin přihlašovací údaje, uloží se šifrovaně (sekce 6). **Není to OAuth flow.**
- Správa profilu, odpojení Garmin účtu (tvrdé smazání credentials)
- Export vlastních dat a smazání účtu (GDPR, viz sekce 9)

### 5.2 Synchronizace dat
- Periodické stahování nových aktivit přes GitHub Actions cron
- Backfill historických aktivit (dávkově, s rate limitingem — viz 7.2)
- Typy aktivit: běh, kolo, plavání (rozšiřitelné)
- Ukládané metriky: GPS trasa, tepová frekvence, tempo, kadence, převýšení, čas
- **Idempotence:** opakovaný běh syncu nesmí vytvořit duplicity (dedup podle Garmin `activity_id`)

### 5.3 Statistiky
- Metriky na aktivitu: vzdálenost, čas, tempo/rychlost, převýšení, průměrná/max TF
- Agregace: týden / měsíc / rok — vzdálenost, čas, počet aktivit, převýšení
- Grafy vývoje výkonnosti v čase
- Osobní rekordy: nejrychlejší 1 km, 5 km, 10 km, půlmaraton, maraton; nejdelší trasa; největší převýšení

### 5.4 Segmenty
- **MVP:** uživatel definuje segment ručně výběrem úseku na mapě z existující aktivity
- Detekce průjezdů segmentem napříč vlastními aktivitami (map-matching s tolerancí)
- Žebříček vlastních pokusů na segmentu (čas, datum, zlepšení)
- **Mimo MVP:** automatická detekce opakovaných úseků, cross-user leaderboard

### 5.5 Vizualizace
- Mapa aktivity s trasou
- Heatmapa všech tras uživatele
- Detail aktivity: graf tempa a TF v čase, elevační profil
- Zobrazení segmentu na mapě s vyznačením pokusů

---

## 6. Bezpečnost

> Nejcitlivější část systému. Implementuje se **před** tím, než se uloží první reálný záznam.

### 6.1 Šifrování Garmin credentials
- Heslo se **nikdy** neukládá v plain textu
- Aplikační šifrování AES-256-GCM (nebo `pgsodium` v Supabase) před zápisem do DB
- Šifrovací klíč **není v DB ani v repu** — pouze GitHub Secret, dostupný jen sync workflow
- Cíl: i při úniku databáze zůstanou hesla nečitelná

### 6.2 Row Level Security
- RLS zapnuté na **všech** tabulkách s uživatelskými daty od prvního dne
- Politiky scoped na `auth.uid()` — uživatel vidí výhradně vlastní data
- `garmin_connections`: frontend (anon key) nemá **žádný** přístup — ani read, ani write
- Přístup ke credentials pouze přes service role key (jen v GitHub Secrets)
- **Ověřeno testem** (sekce 11.3), ne jen předpokladem

### 6.3 Klíče a secrets
| Klíč | Kde smí být | Kde nesmí být |
|---|---|---|
| Supabase anon key | frontend build, veřejně | — (je veřejný by design, chrání ho RLS) |
| Supabase service role key | GitHub Secrets | frontend, repo, logy |
| `ENCRYPTION_KEY` | GitHub Secrets | frontend, repo, logy, DB |

### 6.4 Minimalizace expozice
- Žádné logování hesel, tokenů ani celých request/response těl obsahujících credentials
- Explicitní `::add-mask::` pro secrets v GitHub Actions
- Sync skript po použití hesla proměnnou okamžitě uvolní
- `pre-commit` hook s detekcí secrets (gitleaks) — brání náhodnému commitu klíče

### 6.5 Proces propojení Garmin účtu
- Formulář odesílá data přímo do Supabase přes Edge Function s omezenými právy — ne přes analytics, error tracking ani jiný mezikrok
- Explicitní souhlas s riziky (checkbox + srozumitelný text) při propojování
- Kdykoliv možné odpojení → tvrdé smazání credentials

### 6.6 Uzavřený start (jen pro majitele)
Veřejné repo ≠ veřejný přístup. Kód je vidět, přístup řídí Supabase:
1. Vypnout registraci: Supabase → Authentication → Settings → "Allow new users to sign up" = **off**
2. Vlastní účet založit ručně přes dashboard nebo Admin API
3. RLS zajistí, že i případně autentizovaný cizí účet nevidí nic
4. Otevření veřejnosti později = zapnutí signups zpět + email verifikace + rate limiting; **žádný refaktoring**

### 6.7 Do budoucna
- Rotace šifrovacího klíče (procedura popsaná v `docs/security.md`)
- Audit log přístupů ke `garmin_connections`
- 2FA na účet appky (Supabase Auth podporuje)

---

## 7. Odolnost a provoz synchronizace

Toto je nejkřehčí část systému — závisí na neoficiálním přístupu ke třetí straně.

### 7.1 Izolace chyb
- Sync běží **per-user v izolaci**: selhání jednoho uživatele nesmí shodit celý běh
- Každý uživatel má vlastní try/except blok, chyba se zaloguje a pokračuje se dalším

### 7.2 Rate limiting a backoff
- Umělá prodleva mezi requesty na Garmin (min. 1–2 s) — chrání před detekcí a blokací
- Exponenciální backoff při chybách (2 s → 4 s → 8 s, max 3 pokusy)
- Backfill historických dat rozdělený do dávek napříč více běhy, ne jeden masivní import
- Při HTTP 429 / detekci blokace: okamžité ukončení pro daného uživatele, označení stavu, žádné další pokusy v tomto běhu

### 7.3 Stavy propojení
Tabulka `garmin_connections.status`:
- `active` — funguje normálně
- `auth_failed` — neplatné heslo (uživatel si změnil heslo) → notifikace v UI, sync přeskočen
- `rate_limited` — dočasné omezení, další pokus později
- `disabled` — uživatel odpojil nebo admin zakázal

Sync přeskakuje vše kromě `active` a `rate_limited`.

### 7.4 Sledování běhů
Tabulka `sync_runs` (viz datový model): každý běh zaznamená start, konec, počet zpracovaných uživatelů, počet nových aktivit, chyby. Umožňuje diagnostiku bez procházení GitHub Actions logů.

### 7.5 Selhání knihovny
Riziko: Garmin změní přihlašovací flow → `garth` přestane fungovat.
- Sync workflow při opakovaném selhání (např. 3 běhy v řadě) vytvoří GitHub Issue automaticky
- V UI se uživateli zobrazí, kdy proběhla poslední úspěšná synchronizace

---

## 8. Data

### 8.1 Datový model

```sql
-- users: spravuje Supabase Auth (auth.users)

profiles (
  id uuid PK REFERENCES auth.users,
  display_name text,
  created_at timestamptz
)

garmin_connections (
  user_id uuid PK REFERENCES auth.users,
  garmin_email text NOT NULL,
  garmin_password_encrypted bytea NOT NULL,
  status text NOT NULL,              -- active | auth_failed | rate_limited | disabled
  last_sync_at timestamptz,
  last_error text,
  created_at timestamptz
)

activities (
  id uuid PK,
  user_id uuid REFERENCES auth.users,
  garmin_activity_id bigint NOT NULL, -- dedup klíč
  type text NOT NULL,                 -- running | cycling | swimming | ...
  started_at timestamptz NOT NULL,
  duration_seconds int,
  distance_meters numeric,
  elevation_gain_meters numeric,
  avg_heart_rate int,
  max_heart_rate int,
  avg_pace_seconds_per_km numeric,
  track geography(LineString, 4326),  -- PostGIS, zjednodušená trasa
  stream_path text,                   -- odkaz do Supabase Storage na detailní stream
  created_at timestamptz,
  UNIQUE (user_id, garmin_activity_id)
)

segments (
  id uuid PK,
  user_id uuid REFERENCES auth.users,
  name text NOT NULL,
  geometry geography(LineString, 4326) NOT NULL,
  distance_meters numeric,
  created_at timestamptz
)

segment_efforts (
  id uuid PK,
  segment_id uuid REFERENCES segments,
  activity_id uuid REFERENCES activities,
  user_id uuid REFERENCES auth.users,
  elapsed_seconds int NOT NULL,
  started_at timestamptz,
  UNIQUE (segment_id, activity_id)
)

personal_records (
  id uuid PK,
  user_id uuid REFERENCES auth.users,
  category text NOT NULL,             -- 1k | 5k | 10k | half_marathon | longest | ...
  value numeric NOT NULL,
  activity_id uuid REFERENCES activities,
  achieved_at timestamptz,
  UNIQUE (user_id, category)
)

sync_runs (
  id uuid PK,
  started_at timestamptz,
  finished_at timestamptz,
  users_processed int,
  activities_imported int,
  errors jsonb
)
```

**Indexy (nutné, ne volitelné):**
- `activities (user_id, started_at DESC)` — hlavní výpis
- `activities (user_id, type, started_at)` — filtrované agregace
- GiST index na `activities.track` a `segments.geometry` — prostorové dotazy pro segmenty

### 8.2 Objem dat a strategie ukládání

Free tier má **500 MB DB**. Surová GPS data jednu aktivitu snadno vyženou na stovky kB.

**Řešení:**
- V DB pouze **zjednodušená trasa** (Douglas-Peucker, tolerance ~10 m) — pro mapu a segmenty stačí
- Detailní stream (TF, kadence, tempo po sekundách) → **Supabase Storage** jako komprimovaný JSON/Parquet, v DB jen odkaz (`stream_path`)
- Načítá se lazy až při otevření detailu aktivity
- Odhad: ~5–15 kB/aktivitu v DB → 500 MB vydrží desítky tisíc aktivit

### 8.3 Zálohy

**Supabase free tier nemá žádné automatické zálohy.** To je největší provozní riziko projektu.

**Řešení:** samostatný GitHub Actions workflow (`backup.yml`), 1×denně:
- `pg_dump` databáze
- Šifrování dumpu
- Upload jako GitHub Actions artifact (retence 30 dní) nebo do externího úložiště

### 8.4 Migrace
- Verzované SQL migrace v `supabase/migrations/`, aplikované přes Supabase CLI
- Každá migrace má komentář **proč**, ne jen co
- Migrace se nikdy needitují zpětně — jen se přidává nová

---

## 9. Právní a compliance

Projekt provozovaný z ČR, ukládá osobní údaje a přihlašovací údaje třetích osob → **GDPR se vztahuje**, jakmile appku otevřeš dalším lidem.

**Pro uzavřený start (jen ty):** minimální dopad, zpracováváš vlastní data.

**Před otevřením dalším uživatelům je nutné:**
- Zásady zpracování osobních údajů (privacy policy) — jaké údaje, proč, jak dlouho, kdo má přístup
- Export vlastních dat na vyžádání (funkce v UI)
- Smazání účtu včetně všech dat (tvrdé, ne soft-delete)
- Srozumitelné upozornění, že jde o neoficiální přístup k Garminu v rozporu s jeho ToS a jaké to nese riziko pro jejich Garmin účet

**Poznámka k odpovědnosti:** držení hesel cizích lidí k jejich Garmin účtům je výrazně vyšší závazek než osobní nástroj. Doporučené pořadí: nejdřív jen pro sebe → pak úzký okruh lidí, kteří rozumí riziku → veřejné otevření až s vyřešenou privacy policy a ověřenou bezpečností. Nespěchat na poslední krok.

---

## 10. Nefunkční požadavky

- **Responzivita:** mobil i desktop (primárně mobil — kontrola po tréninku)
- **Přístupnost:** sémantické HTML, klávesová navigace, kontrast dle WCAG AA, popisky u grafů
- **Výkon:** první vykreslení < 2 s; seznam aktivit stránkovaný; mapy lazy-loaded
- **Chybové stavy:** každá async operace má loading, error i empty state; React Error Boundary na úrovni stránek
- **Jazyk:** UI česky, kód a dokumentace anglicky (standard pro open-source)
- **Provoz v rámci free tierů** se sledováním limitů

---

## 11. Testování

Testy jsou součástí Fáze 1, ne dodatek.

### 11.1 Frontend
- **Vitest + React Testing Library** — komponenty, hooky, formátovací utility
- **Playwright** — e2e: přihlášení, výpis aktivit, detail aktivity, vytvoření segmentu

### 11.2 Sync služba
- **pytest** — parsování FIT/GPX, výpočty statistik, detekce segmentů, výpočet rekordů
- **Garmin API se v testech nikdy nevolá naživo** — mockované odpovědi z `tests/fixtures/` (anonymizovaná data)
- Testy retry logiky a backoffu (simulované 429/500)
- Test idempotence: dvojí běh syncu nad stejnými daty nevytvoří duplicity

### 11.3 Bezpečnostní testy (prioritní)
- RLS blokuje přístup k datům jiného uživatele — dva testovací účty, křížový přístup musí selhat
- `garmin_connections` je nečitelná přes anon key
- Šifrování: uložená hodnota v DB není plain text a jde správně dešifrovat klíčem
- Statická kontrola, že v buildu frontendu není service role key ani `ENCRYPTION_KEY`

### 11.4 CI
- `ci.yml` spouští lint + typecheck + testy na každém PR
- Merge do `main` podmíněný zeleným CI (branch protection)
- Pro MVP netlačit na % pokrytí — soustředit se na kritické cesty: auth, RLS, šifrování, parsování, výpočty

---

## 12. Dokumentace

| Soubor | Obsah |
|---|---|
| `README.md` | přehled, architektura, quick start, **disclaimer o Garmin ToS** |
| `docs/spec.md` | tento dokument — zdroj pravdy o rozhodnutích |
| `docs/setup.md` | self-hosting návod (Supabase projekt, secrets, první deploy) |
| `docs/security.md` | bezpečnostní model, rotace klíčů, co dělat při incidentu |
| `docs/adr/` | Architecture Decision Records — proč Supabase, proč neoficiální Garmin přístup, proč monorepo |

Dále: docstringy u všeho, co se dotýká šifrování nebo Garmin přihlášení; komentáře u SQL migrací vysvětlující **proč**.

---

## 13. Fázování

### Fáze 1 — Základ a bezpečnost
**Definice hotovo:** Přihlásím se do appky, propojím Garmin účet, cron stáhne mé aktivity, vidím je v seznamu a na mapě. Bezpečnostní testy zelené.

- Monorepo scaffold, CI/CD, lint/format/pre-commit
- Supabase projekt, migrace se schématem a **RLS politikami dřív, než se uloží první data**
- Šifrovací vrstva pro credentials + testy
- Supabase Auth, uzavřená registrace (6.6)
- UI pro propojení Garmin účtu se souhlasem
- Sync workflow: přihlášení, stažení aktivit, dedup, rate limiting, per-user izolace, `sync_runs`
- Seznam a detail aktivity, mapa trasy
- Backup workflow

### Fáze 2 — Statistiky
**Definice hotovo:** Vidím týdenní/měsíční/roční souhrny, grafy vývoje a osobní rekordy.

- Agregační dotazy + indexy
- Grafy (tempo, TF, elevace, vývoj v čase)
- Výpočet a zobrazení osobních rekordů
- Backfill historických dat po dávkách

### Fáze 3 — Segmenty
**Definice hotovo:** Vytvořím segment na mapě, systém najde mé průjezdy napříč aktivitami a ukáže žebříček.

- Ruční definice segmentu na mapě
- Map-matching: detekce průjezdů s tolerancí (PostGIS)
- Žebříček pokusů, zobrazení na mapě

### Fáze 4 — Polish
**Definice hotovo:** Appka je použitelná na mobilu, má heatmapu, kompletní dokumentaci a je připravená otevřít dalším lidem.

- Heatmapa všech tras
- Přístupnost, responzivita, výkonnostní optimalizace
- Kompletní dokumentace + ADR
- Privacy policy, export a smazání dat (sekce 9)

---

## 14. Checklist — GitHub Secrets

| Secret | Odkud |
|---|---|
| `SUPABASE_URL` | Supabase → Project Settings → API |
| `SUPABASE_SERVICE_ROLE_KEY` | tamtéž (**tajné**, nikdy do frontendu) |
| `SUPABASE_ANON_KEY` | tamtéž (veřejný, jde do frontend buildu) |
| `ENCRYPTION_KEY` | vygenerovat: `openssl rand -hex 32` |
| `BACKUP_ENCRYPTION_KEY` | vygenerovat stejně, pro šifrování záloh |

Garmin credentials **nejsou** GitHub Secret — přicházejí od uživatelů přes UI a ukládají se šifrovaně do Supabase.

---

## 15. Checklist — první spuštění

1. Založit GitHub repo (public)
2. Založit Supabase projekt, počkat na provisioning
3. Zapnout PostGIS extension v Supabase
4. Zkopírovat API klíče do GitHub Secrets (sekce 14)
5. Spustit migrace (schéma + RLS politiky)
6. **Ověřit bezpečnostními testy, že RLS funguje** — před uložením reálných dat
7. Vypnout veřejnou registraci (6.6), založit si účet ručně
8. Nasadit frontend přes GitHub Pages, ověřit načtení
9. Propojit vlastní Garmin účet, ručně spustit sync workflow, ověřit data
10. Ověřit, že `backup.yml` proběhne a vytvoří artifact
11. Zapnout branch protection na `main` (vyžadovat zelené CI)

---

## 16. Handoff brief pro implementaci (Claude Code v IntelliJ)

Tato sekce je určená k předání implementačnímu agentovi. Zkopíruj ji jako první instrukci.

### Kontext
Projekt se jmenuje **Sunder**. Použij tento název konzistentně: název repozitáře, `package.json` (`name: "sunder"`), Python balíček (`sunder_sync`), `<title>` v HTML, nadpis v README.

Implementuješ projekt podle `docs/spec.md`. Přečti si celou specifikaci před psaním kódu. Klíčové sekce: **6 (bezpečnost)**, **7 (odolnost syncu)**, **8 (data)**.

### Pořadí prací (Fáze 1)
1. Scaffold monorepa dle sekce 4, včetně lint/format/pre-commit a `ci.yml`
2. SQL migrace: schéma ze sekce 8.1 **včetně indexů a RLS politik**
3. Bezpečnostní testy ze sekce 11.3 — **musí být zelené dřív, než se implementuje ukládání credentials**
4. Šifrovací modul (`services/sync/src/crypto/`) + testy
5. Garmin klient s rate limitingem, backoffem a per-user izolací (sekce 7)
6. Parsery a zápis do DB s dedup logikou
7. `sync.yml` workflow
8. Frontend: auth, propojení Garmin účtu, seznam a detail aktivity, mapa
9. `deploy.yml` a `backup.yml`

### Pravidla implementace
- **TypeScript strict mode**, žádné `any` bez zdůvodnění v komentáři
- **Python:** type hints všude, `ruff` + `mypy` čisté
- Každý modul dostane testy současně s implementací, ne později
- **Nikdy** nelogovat hesla, tokeny ani celá těla requestů s credentials
- Commity: Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`)
- Malé, revidovatelné commity po logických celcích — ne jeden obří commit na fázi
- Při každém rozhodnutí, které se odchyluje od specifikace, přidat ADR do `docs/adr/`

### Co si vyžádat od uživatele (nikdy negenerovat sám)
- Hodnoty všech secrets ze sekce 14
- Potvrzení Supabase project URL
- Souhlas před `git push` do vzdáleného repozitáře

### Definice hotovo pro Fázi 1
Viz sekce 13. Před ohlášením hotova ověř: CI zelené, bezpečnostní testy zelené, sync workflow proběhl a naimportoval reálná data, frontend je dostupný na GitHub Pages.
