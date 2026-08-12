# 🗃️ Database Version Control App ( On Going )

Experimenting

**Tested for MySQL only currently.** Postgres code paths exist (dump/restore, trigger DDL, DCL queries) but have not been run against a live Postgres instance.

---

## Setup

### Prerequisites

- Python 3.8+
- MySQL server (tested) or PostgreSQL server (untested, code paths exist)
- Node.js 18+ (for the Electron desktop UI)
- `mysqldump` / `mysql` CLI tools (for data baseline dump/restore)

### Installation

```bash
# 1. Clone & enter
git clone <repo-url> && cd DB-Version-Control

# 2. Python dependencies
pip install -r requirements.txt
# → sqlalchemy, mysql-connector-python, psycopg2-binary, python-dotenv

# 3. Configure your database connection
cp .env.example .env
# Edit .env with your DB_URL, e.g.:
#   DB_URL=mysql+mysqlconnector://user:password@localhost:3306/yourdb

# 4. (Optional) Install git hooks for automatic snapshot on every commit
bash install_hooks.sh
```

### `.env` format

```env
DB_URL=mysql+mysqlconnector://user:password@localhost:3306/yourdb
# or for Postgres (untested):
# DB_URL=postgresql://user:password@localhost:5432/yourdb
```

> **Why `.env`?** Keeps credentials out of CLI args (which show up in `ps`) and out of version control (`.env` is gitignored).

---

## Quick Start — CLI Commands

### Take a schema snapshot

```bash
python3 cli/snapshot.py
```

> **Why?** Captures the full live schema (tables, columns, constraints, indexes) as a timestamped JSON file in `snapshots/`. This is the baseline for all diffs and reverts.

### Generate a migration diff between two snapshots

```bash
python3 cli/schema_diff.py snapshots/schema_20250804_150411.json snapshots/schema_20250804_105759.json
```

> **Why?** Produces a SQL migration script (`migrations/migration_output.sql`) that transforms the first schema into the second — the exact ALTER/CREATE/DROP statements needed, in dependency-correct order.

### Start continuous capture (watch mode)

```bash
python3 cli/watch.py
# Options: python3 cli/watch.py [changelog_interval_sec] [schema_check_every_n_ticks]
# One-shot mode: python3 cli/watch.py --once
```

> **Why?** Runs a single continuous process that captures both DML changes (via trigger-based changelog) and DDL changes (via catalog fingerprint polling). Automatically commits snapshots and migration scripts to git on every detected change.

### Revert to a previous state

```bash
# Dry run (shows the plan, no changes applied):
python3 cli/revert.py snapshots/schema_20250804_105759.json

# Apply the revert:
python3 cli/revert.py snapshots/schema_20250804_105759.json --apply

# Revert to a checkpoint by timestamp (resolves schema + data paths automatically):
python3 cli/revert.py --at 20250804_105759 --apply

# With data restore (baseline dump + changelog replay):
python3 cli/revert.py snapshots/schema_20250804_105759.json --apply --restore-data data_snapshots/baseline_20250804_105759.sql --to 20250804_105759

# JSON plan output (for UI consumption):
python3 cli/revert.py --at 20250804_105759 --json
```

> **Why?** `revert.py` diffs the **live** schema against the target and applies the minimal in-place change. Tables that only need column/constraint adjustments are ALTERed — no DROP TABLE, no data movement, regardless of row count.

### Take a data baseline dump

```bash
# Full database:
python3 cli/data_dump.py --dump

# Single table:
python3 cli/data_dump.py --dump my_table

# Restore a dump:
python3 cli/data_dump.py --restore data_snapshots/baseline_20250804_105759.sql
```

> **Why?** Uses `mysqldump`/`pg_dump` (bulk-optimized native tools) for data capture, not row-by-row exports. The dump is data-only (`--no-create-info`) because schema is managed separately by revert.py — restoring a schema-carrying dump would silently destroy triggers.

### Snapshot DCL (privileges/grants)

```bash
# Take a privilege snapshot:
python3 cli/dcl.py snapshots/privileges_old.json snapshots/privileges_new.json
```

> **Why?** GRANT/REVOKE statements can't be captured by triggers — they operate outside the data/schema plane. `dcl.py` queries `information_schema.TABLE_PRIVILEGES` and produces a separate snapshot + GRANT/REVOKE migration script.

### List checkpoints

```bash
python3 cli/list_checkpoints.py
```

> **Why?** Outputs a JSON list of all checkpoints (schema snapshots, data baselines, git commits) — enriched with commit messages and dates. This is what the desktop UI renders as the clickable timeline.

### List available databases

```bash
echo '{"host":"localhost","port":3306,"user":"root","password":"pass"}' | python3 cli/list_databases.py
```

> **Why?** Used during onboarding in the desktop UI — connects without selecting a database and lists what's available, so the user can pick which DB(s) to track. Credentials go through stdin (not CLI args) so they don't show up in `ps`.

---

## What's tracked

**Table level:**
- CREATE — full table capture (all columns + constraints)
- DROP — detected, dropped correctly (with FK cleanup both directions)
- RENAME — detected via column-signature match, emits `RENAME TABLE`, not drop+create (preserves data/identity)

> **Why rename detection?** A naive diff sees a dropped table + an added table. By comparing column signatures (type, nullable, default), we detect renames confidently and emit `RENAME TABLE` — preserving data, indexes, and identity instead of forcing a destructive drop+create cycle.

**Column level:**
- Name — tracked, rename detected via type/nullable/default signature match
- Type — tracked, change triggers `MODIFY COLUMN`
- Nullable (NOT NULL) — tracked
- Default value — tracked
- Add/Drop — tracked

> **Why signature-based rename?** Same principle as table renames: if a column disappears and a new one appears with the same type/nullable/default, it's treated as a rename (only when the match is unambiguous — 1:1). Ambiguous matches are flagged with a `-- POSSIBLE RENAME` comment instead of guessing.

**Constraint level (add/drop/modify):**
- PRIMARY KEY — tracked, including modify-on-ALTER (composite PK changes)
- FOREIGN KEY — tracked: columns, referenced table, referenced columns, correct drop-before-table-drop / add-after-table-create ordering
- UNIQUE — tracked, named and unnamed
- CHECK — tracked: condition/clause
- Any other/unrecognized constraint type — NOT silently dropped; surfaces as a `-- WARNING: unhandled constraint type` comment in the diff, blocks revert until manually confirmed

> **Why staged FK ordering?** FK constraints create cross-table dependencies. Dropping a table that's referenced by another table's FK fails unless the FK is dropped first. Similarly, adding an FK to a table that doesn't exist yet fails. The diff engine stages FK drops before table drops and FK adds after table creates, ensuring the migration is valid regardless of dict/set iteration order.

**Index level:**
- Add/drop/change (columns or uniqueness) — tracked separately from UNIQUE constraints
- Indexes backing active FKs are never dropped standalone (MySQL error 1553) — they're only removed implicitly when their FK is dropped

> **Why separate from UNIQUE constraints?** An index and a UNIQUE constraint can have the same name but are different objects in MySQL. Tracking them independently prevents false positives in the diff.

**Column-level DDL flags:**
- AUTO_INCREMENT (MySQL) — tracked as a per-column property (is this column auto-incrementing or not), the same way `nullable`/`primary_key` are tracked. Gaining/losing it emits a `MODIFY COLUMN`. The counter *value* is deliberately NOT tracked here — that's data-plane, not schema, and already covered by baseline dump/changelog replay. Postgres IDENTITY/serial uses sequences (a different mechanism) and isn't covered yet.

> **Why track the flag but not the counter?** The counter value changes on every INSERT — tracking it would create noise in every snapshot. The flag (whether a column IS auto-incrementing) is a schema-level decision that changes rarely and affects CREATE/ALTER statements.

**Explicitly NOT tracked (known gaps):**
- Postgres IDENTITY/serial sequence values, column collation/charset, generated/computed columns
- Table-level options (engine, charset, partitioning)
- Views, stored procedures/functions, sequences, other triggers as objects

**Separate tracks, not part of schema diff:**
- **DML** (row data: INSERT/UPDATE/DELETE) — via triggers → changelog, not schema_diff
- **DCL** (GRANT/REVOKE) — via `dcl.py`, separate snapshot/diff, not schema_diff
- **TRUNCATE** — separate row-count fallback marker, since triggers can't see it

> **Why separate DML/DCL/DDL tracks?** Each change type has fundamentally different capture mechanisms. DDL changes are detected by polling the catalog. DML changes are captured instantly via database triggers. DCL changes are queried from `information_schema` on demand. Mixing them in one pipeline would either miss changes or add unnecessary latency.

---

## Architecture — How the pieces fit together

### File-by-file overview

| File | Purpose | Why it exists |
|------|---------|---------------|
| `cli/db.py` | DB connection factory (`get_engine()`, `reflect_schema()`) | Single source of truth for the SQLAlchemy engine — reads `DB_URL` from `.env`, avoids duplicating connection logic across every module. |
| `cli/snapshot.py` | Schema capture → JSON | Serializes the live DB schema (tables, columns, constraints, indexes, AUTO_INCREMENT flags) into a timestamped JSON file. Streams directly to disk for large schemas. |
| `cli/schema_diff.py` | Two-snapshot comparison → SQL migration | The core diff engine. Compares two JSON schema snapshots and produces dependency-ordered ALTER/CREATE/DROP statements. Handles renames, FK ordering, index-FK protection. |
| `cli/changelog.py` | DML capture via triggers + changelog polling | Installs AFTER INSERT/UPDATE/DELETE triggers on tracked tables that write to `_dvc_changelog`. A poller flushes that table to a git-tracked JSONL file. Also handles TRUNCATE markers. |
| `cli/dcl.py` | Privilege snapshot + GRANT/REVOKE diff | Captures GRANT-level privileges from `information_schema` and diffs two snapshots into GRANT/REVOKE migration statements. |
| `cli/data_dump.py` | Native dump/restore (`mysqldump`/`pg_dump`) | Bulk data capture using engine-native tools. Records the changelog cutoff ID so replay knows where to start. Data-only (`--no-create-info`) to avoid conflicting with schema management. |
| `cli/watch.py` | Continuous capture loop (DDL + DML) | Polls DDL changes via a catalog fingerprint (SHA-256 of `information_schema.COLUMNS`). Flushes DML changelog on a short interval. Auto-resyncs triggers on schema changes. Detects TRUNCATE via row-count fallback. |
| `cli/revert.py` | Revert to any checkpoint (schema + data) | Diffs live schema vs target, applies in-place ALTERs, restores data via baseline dump + batched changelog replay. Supports git hard-reset semantics with safety tags. |
| `cli/manifest.py` | Checkpoint index (`checkpoints/index.json`) | Ties together schema snapshots, data baselines, and git commits into a single timeline. Supports nearest-checkpoint lookup and post-revert pruning. |
| `cli/list_checkpoints.py` | Enriched checkpoint list (for UI) | Reads `manifest.py`'s index, enriches with git commit info and migration SQL, outputs JSON for the desktop UI timeline. |
| `cli/list_databases.py` | Database discovery (for UI onboarding) | Connects without selecting a database and lists available schemas — used by the Electron UI during first-time setup. Reads credentials via stdin, not CLI args. |
| `install_hooks.sh` | Git post-commit hook installer | Installs a `post-commit` hook that triggers an immediate snapshot check (`watch.py --once`) on every git commit, independent of the polling interval. |
| `.env.example` | Environment config template | Documents the `DB_URL` format without exposing real credentials. |
| `requirements.txt` | Python dependencies | `sqlalchemy`, `mysql-connector-python`, `psycopg2-binary`, `python-dotenv` — the minimum set needed for both MySQL and Postgres code paths. |

---

## Revert

`revert.py` diffs the current live schema against a target snapshot and applies the change **in place**:
- Tables unchanged between now and target are left untouched.
- Tables with column/constraint/index changes get `ALTER`ed in place — no table drop, no data movement, same cost whether the table has 10 rows or 10 million.
- Tables that must actually stop or start existing (added/removed between the two states) are the only case needing DROP/CREATE + a scoped data restore (baseline dump + batched changelog replay).
- Any unhandled constraint type or ambiguous rename blocks the revert by default (`--force` to override) instead of applying silently.
- When reverting to a checkpoint (`--at <timestamp>`), this is a true hard reset: git history moves back to that checkpoint's commit (a safety tag is created first, so nothing is permanently lost), and every checkpoint after that point is pruned from `checkpoints/index.json`.

> **Why in-place ALTER instead of drop+recreate?** Dropping a table with 10 million rows just to change a column type would require re-inserting all 10 million rows afterward. ALTER TABLE modifies only the metadata (or rebuilds the table internally in MySQL for type changes), which is orders of magnitude faster and doesn't require a full data restore.

> **Why hard-reset git semantics?** A revert should feel like "go back to this exact point in time." If the checkpoint list still showed entries from after the revert, it would be confusing — those states no longer match reality. The safety tag ensures nothing is permanently lost (recoverable via `git checkout <tag>` / reflog).

**Important**: hard-resetting git history affects the ENTIRE repository, not just the DB snapshot files. If this tool's own source code lives in the same git repo as the data it's tracking, a revert will also roll back any uncommitted code changes. Keep this tool's codebase in a separate repo from the DB you're versioning, or commit code changes before testing reverts. `revert.py` actively checks for uncommitted changes outside managed paths (`snapshots/`, `migrations/`, `checkpoints/`, `data_snapshots/`) and **refuses** to proceed if any are found.

---

## Continuous capture

`watch.py` runs continuously and covers both capture paths in one process:
- **DML** — already trigger-driven (instant); the loop flushes the live changelog table to a git-tracked file on a short interval (default 2s), since a trigger can't push to an external process directly.
- **DDL** — no native trigger exists for this in MySQL, so it's polled via a cheap catalog fingerprint on a longer interval (default ~30s); on Postgres, DDL event triggers could make this push-based too.
- Trigger bodies are automatically resynced whenever a tracked table's structure changes (a rename/add/drop column would otherwise leave the old trigger referencing a column that no longer exists).
- TRUNCATE (which skips row-level triggers on both MySQL and Postgres) is caught via a row-count fallback check in the same loop.

> **Why a single process?** Running separate DML and DDL watchers creates race conditions — a schema change mid-DML-flush could cause the trigger to reference columns that no longer exist. One loop with a shared state file eliminates this.

> **Why catalog fingerprint instead of full reflect?** A full SQLAlchemy `metadata.reflect()` is expensive on large schemas (dozens of tables with many indexes). The fingerprint hashes `information_schema.COLUMNS` rows — a single cheap query — and only does a full reflect when the hash changes.

---

## DML Changelog System

The changelog system captures row-level data changes (INSERT, UPDATE, DELETE) using native database triggers:

1. **Trigger installation** (`changelog.py:install_changelog`): Creates AFTER INSERT/UPDATE/DELETE triggers on each tracked table. Each trigger writes the operation type, primary key, and full row data (as JSON) to the `_dvc_changelog` table.

2. **Polling** (`changelog.py:poll_once`): Reads new rows from `_dvc_changelog` (indexed `id > :last_seen` — never a full scan) and appends them to a git-tracked JSONL file (`checkpoints/changelog.jsonl`).

3. **Replay** (`changelog.py:batch_replay`): Groups changelog entries by (table, op_type) and replays them in batched `executemany` calls — one driver round-trip per batch, not per row. This is what makes 100k-row replays practical.

4. **TRUNCATE detection** (`watch.py`): Since TRUNCATE bypasses row-level triggers on both MySQL and Postgres, `watch.py` tracks per-table row counts and inserts a `BULK_CHANGE_DETECTED` marker when a count drops unexpectedly.

> **Why triggers instead of binlog/CDC?** Triggers are standard SQL, supported by every database engine, and don't require special server configuration (like `binlog_format=ROW` or replication slots). The trade-off is a small write overhead per DML operation, but this is negligible for the use case.

---

## UI — Complete Usage Guide

### Running the UI

```bash
cd ui && npm install && npm start
```

> **Why Electron?** The tool is fundamentally local (connects to a local or remote DB, writes to the local filesystem, uses local git). A web app would require a backend server for no reason. Electron gives a native desktop experience with direct filesystem and subprocess access.

---

### Screen 1: Onboarding (First Launch / Adding a New Database)

**What you see:** A centered dark card titled **"Connect a database"** with four input fields and one button.

**Fields visible:**
| Field | Default Value | What to enter |
|-------|---------------|---------------|
| **Host** | `localhost` | Your MySQL server hostname or IP |
| **Port** | `3306` | MySQL port number |
| **User** | `root` | Database username |
| **Password** | *(empty)* | Database password |

**Step-by-step:**

1. **Fill in your MySQL connection details** in the four fields above.

2. **Click `Connect & list databases`** (the blue-ish button below the form).
   - The button text changes to **"Connecting…"** while it works.
   - Behind the scenes, this calls `list_databases.py` which connects to the server (without selecting any specific database) and runs `SHOW DATABASES`.
   - If the connection fails, a **red error message** appears below the button. Check your credentials and try again.

3. **You now see a list of checkboxes** — one for each database on the server (system databases like `mysql`, `information_schema`, `performance_schema`, `sys` are filtered out).
   - Two new buttons appear: **`Add selected`** and **`Back`**.

4. **Check the boxes next to the databases you want to track.** Each selected database becomes an independent tracking unit with its own snapshot history, migration scripts, and git timeline.

5. **Click `Add selected`**.
   - The button text changes to **"Adding…"** while it works.
   - For each selected database, the app:
     - Creates a dedicated data directory at `~/.dvc/repos/<database_name>/`
     - Initializes a fresh git repository inside it
     - Takes an initial data baseline dump (`mysqldump --no-create-info`)
     - Registers the repo in `~/.dvc/repos.json`
   - When done, you're taken to the **Main Dashboard** (Screen 2).
   - If any database fails, the error is shown in red but other databases still proceed.

6. **`Back` button** — takes you back to the connection form if you want to change credentials.

---

### Screen 2: Main Dashboard (Day-to-Day View)

**What you see:** A dark-themed dashboard with three regions:

```
┌──────────────────────────────────────────────────────────────────┐
│  [Repo Dropdown ▾] [+ Add Database]   DB Version Control — mydb │ Capture: Stopped  [Start Capture] [Show Log] │
├────────────────────┬─────────────────────────────────────────────┤
│  CHECKPOINTS       │                                             │
│  [Refresh]         │  Select a checkpoint to see its revert plan.│
│                    │                                             │
│  ┌──────────────┐  │                                             │
│  │ 12 Aug 16:31 │  │                                             │
│  │ Auto-snapshot │  │                                             │
│  │ [schema]     │  │                                             │
│  └──────────────┘  │                                             │
│  ┌──────────────┐  │                                             │
│  │ 12 Aug 15:54 │  │                                             │
│  │ Data baseline│  │                                             │
│  │ [schema][data]│  │                                             │
│  └──────────────┘  │                                             │
│        ...         │                                             │
└────────────────────┴─────────────────────────────────────────────┘
```

#### Header Bar (top)

| Element | What it does |
|---------|-------------|
| **Repo Dropdown** (top-left) | Lists all tracked databases. Select one to switch — the checkpoint list and capture state update for that database. |
| **`+ Add Database`** button | Opens the Onboarding screen (Screen 1) so you can add another database to track. |
| **Title** (center) | Shows `DB Version Control — <database_name>` for the currently active repo. |
| **`Capture: Stopped`** / **`Capture: Running`** pill (top-right) | Status indicator. **Red pill** = stopped, **green pill** = running. |
| **`Start Capture`** / **`Stop Capture`** button | Toggles `watch.py` on/off for the active database. When running, it continuously monitors for DDL and DML changes. |
| **`Show Log`** / **`Hide Log`** button | Toggles a dark monospace log panel below the header showing real-time `watch.py` output (what it detected, when it committed, etc.). |

#### Left Panel — Checkpoint List

- Shows all checkpoints for the active database, **newest first**.
- Each checkpoint card shows:
  - **Timestamp** — when the checkpoint was captured (e.g., `Aug 12, 2026, 4:31:45 PM`)
  - **Commit message** — what triggered it (e.g., `Auto-snapshot: schema drift detected`, `Data checkpoint: 5 change(s) captured`)
  - **Badges** — colored tags showing what's included:
    - `schema` — a schema snapshot exists for this checkpoint
    - `data baseline` — a `mysqldump` data baseline was taken at this point
    - `privileges` — a DCL/privilege snapshot was taken
- **Click any checkpoint** to select it (it highlights with a blue border) and load its revert plan in the right panel.
- **`Refresh` button** — manually reloads the checkpoint list. Also auto-refreshes every 15 seconds and whenever `watch.py` logs a schema change.

#### Right Panel — Revert Plan (Empty State)

- Before selecting a checkpoint, shows: **"Select a checkpoint to see its revert plan."**
- After clicking a checkpoint, shows: **"Computing revert plan…"** while it diffs the live DB against the target.

---

### Screen 3: Revert Plan Detail (After Clicking a Checkpoint)

**What you see:** The right panel fills with a detailed breakdown of what would happen if you reverted to this checkpoint.

#### Sections (top to bottom):

1. **"What changed in this checkpoint"** — Shows the migration SQL that was auto-generated WHEN this checkpoint was captured (i.e., what changed at that point in time, not the revert plan). If it's the very first snapshot, shows: *"No migration recorded for this checkpoint (likely the first-ever snapshot)."*

2. **Summary Banner** (dark card) — Plain-English summary:
   - `Revert plan: current live state → <timestamp>`
   - How many tables altered in place (no data touched)
   - How many tables recreated / dropped
   - Whether any data loss occurs
   - Reminder that this is a **hard reset** (checkpoints after this point will be removed)
   - If the live state already matches this checkpoint: **"This checkpoint matches the current live state -- reverting to it is a no-op."**

3. **⚠️ Warning Box** (only if the diff found issues) — Orange-bordered box explaining:
   - Unhandled constraint types the diff couldn't migrate automatically
   - Ambiguous renames that need manual confirmation
   - Text: *"Review manually before forcing this through."*

4. **Five SQL statement sections** — Each shows a header with count and a code block:
   - **DROP FOREIGN KEYS** — FKs that must be removed first (run in parallel)
   - **DROP TABLES** — Tables that exist now but not in the target (run sequentially)
   - **CREATE TABLES** — Tables that exist in the target but not now (run in parallel)
   - **ALTER TABLES IN PLACE** — Column/constraint/index changes on existing tables (sequential, no data movement)
   - **ADD FOREIGN KEYS** — FKs to add after all tables exist (run in parallel)
   - If a section has 0 statements, it shows **"none"** in grey.

5. **Action Button** (bottom):
   - If **no-op** (already at this state): Grey disabled button — **"Nothing to revert -- already at this state"**
   - If **normal revert**: Red button — **`Revert to this point`**
   - If **blocked** (has warnings): Red button — **`Force revert anyway (not recommended)`**

---

### Screen 4: Confirmation Flow (After Clicking Revert)

Clicking the revert button opens a **dark overlay modal** with a multi-step confirmation:

#### Step 1 — Acknowledge

**What you see:** A centered modal card titled **"Confirm revert"** with:
- A warning paragraph explaining:
  - For a **normal revert**: *"This is a hard reset, like git reset --hard: every checkpoint made after this point will be removed from the list, and git history moves back to here. A safety tag is created first so nothing is permanently lost, but this action should be treated as irreversible in normal use."*
  - For a **forced revert** (with warnings): *"This revert has unresolved warnings and may apply an incomplete or incorrect migration..."*
- A **checkbox**: *"I understand this cannot be undone and want to proceed."*
- Two buttons: **`Cancel`** and **`Revert now`** (red, disabled until checkbox is checked).

**What to do:**
1. Read the warning text.
2. **Check the checkbox** to enable the `Revert now` button.
3. **Click `Revert now`**.
   - If the revert is **non-destructive** (no tables being dropped), the revert executes immediately.
   - If the revert is **destructive** (tables will be dropped), you proceed to **Step 2**.

#### Step 2 — Type Confirmation (Only for Destructive Reverts)

**What you see:** The modal switches to show:
- A **yellow warning** listing exactly which tables will be dropped and lose their current data: *"This will DROP and lose current data in: `orders`, `users` (restored from the checkpoint's own baseline/changelog, not from what's live now)."*
- A prompt: *"Type **REVERT** to confirm:"*
- A **text input field** with placeholder "Type REVERT"
- Two buttons: **`Cancel`** and **`Yes, revert and lose that data`** (red, disabled until you type exactly `REVERT`).

**What to do:**
1. Read the list of tables that will be dropped.
2. **Type `REVERT`** (exact, uppercase) in the text field.
3. **Click `Yes, revert and lose that data`**.

#### After Revert Executes

- The detail panel shows either:
  - **✅ Revert applied.** + the full stdout from `revert.py` (showing which ALTERs ran, which triggers were reinstalled, git reset status)
  - **❌ Revert failed.** + the error output
- The **checkpoint list auto-refreshes** — checkpoints after the reverted-to point are now gone from the list (pruned by hard reset).

---

### Log Panel

Click **`Show Log`** in the header to toggle a dark monospace panel that streams real-time output from `watch.py`:

```
No schema change.
Appended 3 changelog entries (last_seen=47)
Data checkpoint committed: 3 change(s)
Schema change detected, snapshot written to snapshots/schema_20260812_163145.json
Changelog + triggers installed for: users, orders
```

- Scrolls automatically to the latest line.
- Only shows logs for the **currently active repo** (switching repos doesn't mix logs).
- When a line contains `"Schema change detected"`, the checkpoint list auto-refreshes immediately.
- Click **`Show Log`** again (now reads **`Hide Log`**) to collapse the panel.

---

### Multi-Database Workflow

1. **Add multiple databases** during onboarding (check several boxes) or click **`+ Add Database`** from the main screen to add more later.
2. **Switch between databases** using the dropdown in the top-left. Each database has:
   - Its own checkpoint history
   - Its own capture process (`watch.py` runs independently per database)
   - Its own git repository at `~/.dvc/repos/<database_name>/`
3. **Capture runs per-repo** — starting capture on `mydb1` and switching to `mydb2` doesn't stop `mydb1`'s capture. Each has an independent `watch.py` subprocess.

---

### Where Data Lives

| What | Path | Notes |
|------|------|-------|
| Repo registry | `~/.dvc/repos.json` | Lists all tracked databases, credentials (stored locally, never in git) |
| Per-repo data | `~/.dvc/repos/<database>/` | Independent git repo with snapshots, migrations, checkpoints, data baselines |
| Schema snapshots | `~/.dvc/repos/<database>/snapshots/` | JSON files, one per captured schema state |
| Migration SQL | `~/.dvc/repos/<database>/migrations/` | Auto-generated SQL scripts for each detected change |
| Data baselines | `~/.dvc/repos/<database>/data_snapshots/` | `mysqldump` output files |
| Checkpoint index | `~/.dvc/repos/<database>/checkpoints/index.json` | Ties snapshots, baselines, and git commits together |
| Changelog | `~/.dvc/repos/<database>/checkpoints/changelog.jsonl` | Git-tracked DML change log (JSONL format) |

> **Why `~/.dvc/` instead of inside this repo?** Each tracked DB gets its own independent git repo. This means a hard-reset revert on one database's checkpoints never touches this tool's source code or any other tracked database's data — a problem that happened once before when everything was in a single repo.

---

## Project Structure

```
DB-Version-Control/
├── .env.example              # DB_URL config template
├── .gitignore                # Ignores .env, __pycache__, node_modules
├── Readme.md                 # This file
├── requirements.txt          # Python dependencies
├── install_hooks.sh          # Git post-commit hook installer
├── cli/                      # Core Python modules
│   ├── db.py                 # DB connection factory
│   ├── snapshot.py           # Schema capture → JSON
│   ├── schema_diff.py        # Schema comparison → SQL migration
│   ├── changelog.py          # DML trigger install + changelog polling/replay
│   ├── dcl.py                # Privilege snapshot + GRANT/REVOKE diff
│   ├── data_dump.py          # Native dump/restore (mysqldump/pg_dump)
│   ├── watch.py              # Continuous DDL + DML capture loop
│   ├── revert.py             # Revert to any checkpoint (schema + data)
│   ├── manifest.py           # Checkpoint index management
│   ├── list_checkpoints.py   # Enriched checkpoint list (for UI)
│   └── list_databases.py     # Database discovery (for UI onboarding)
├── snapshots/                # Schema snapshot JSON files (auto-generated)
├── migrations/               # Migration SQL scripts (auto-generated)
├── checkpoints/              # Checkpoint index + watch state + changelog
│   ├── index.json            # Checkpoint manifest
│   ├── watch_state.json      # Catalog fingerprint + row counts
│   └── changelog.jsonl       # Git-tracked DML changelog
├── data_snapshots/           # Data baseline dumps (auto-generated)
└── ui/                       # Electron desktop app
    ├── main.js               # Electron main process + IPC handlers
    ├── preload.js            # Context bridge (secure API exposure)
    ├── repoStore.js          # Multi-repo persistence layer
    ├── package.json          # Node dependencies (electron)
    └── renderer/             # Frontend
        ├── index.html        # App shell
        ├── renderer.js       # UI logic (onboarding, timeline, revert)
        └── style.css         # Styling
```

---

## Design Decisions & Trade-offs

| Decision | Rationale |
|----------|-----------|
| **In-place ALTER over drop+recreate** | Avoids data movement for column/constraint changes. A 10M-row table gets a metadata-only ALTER instead of a full rebuild + restore. |
| **Trigger-based DML over binlog/CDC** | Portable (standard SQL triggers work on any engine), no special server config needed. Trade-off: small per-DML write overhead. |
| **Catalog fingerprint polling for DDL** | MySQL has no DDL event triggers. Hashing `information_schema.COLUMNS` is a single cheap query vs. a full `metadata.reflect()` on every tick. |
| **Separate DML/DCL/DDL tracks** | Each change type has a fundamentally different capture mechanism. Mixing them would add complexity without benefit. |
| **Native dump tools for data** | `mysqldump`/`pg_dump` are bulk-optimized by the DB vendor. Row-by-row export would be orders of magnitude slower for large tables. |
| **Data-only dumps (no schema)** | Schema is managed by `revert.py`. Including schema in dumps would conflict (e.g., DROP TABLE would destroy triggers). |
| **Git as the history backend** | Every snapshot, migration, and changelog is a git-tracked file. History, branching, and undo come for free. |
| **Safety tag before hard-reset** | A revert creates a `backup/pre-reset-<timestamp>` tag before `git reset --hard`, so discarded commits are always recoverable via reflog. |
| **Refuse revert with uncommitted non-DVC changes** | `git reset --hard` affects the entire working tree. Without this guard, a revert could silently wipe uncommitted source code changes. |
| **Signature-based rename detection** | Column/table renames are detected by matching type/nullable/default signatures. Only 1:1 matches are treated as confident renames; ambiguous cases are flagged. |
| **Staged FK ordering** | FK drops run before any DROP TABLE; FK adds run after all CREATE TABLEs. Prevents cross-table dependency failures. |
| **Unhandled constraint types block revert** | Rather than silently dropping unknown constraints, they're flagged with `-- WARNING` comments and block the revert until manually confirmed (`--force` to override). |
| **Credentials via stdin / env vars** | `list_databases.py` reads creds from stdin, not CLI args. `DB_URL` is in `.env`. Neither appears in `ps` output. |
