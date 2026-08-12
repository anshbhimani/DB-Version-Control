# 🗃️ Database Version Control App ( On Going )

Experimenting

**Tested for MySQL only currently.** Postgres code paths exist (dump/restore, trigger DDL, DCL queries) but have not been run against a live Postgres instance.

Commands for running

for creating snap shot : python3 cli/snapshot.py

to create a sql script to generate a table from wrt another table version

python3 cli/schema_diff.py snapshots/schema_20250804_150411.json snapshots/schema_20250804_105759.json

## What's tracked

**Table level:**
- CREATE — full table capture (all columns + constraints)
- DROP — detected, dropped correctly (with FK cleanup both directions)
- RENAME — detected via column-signature match, emits `RENAME TABLE`, not drop+create (preserves data/identity)

**Column level:**
- Name — tracked, rename detected via type/nullable/default signature match
- Type — tracked, change triggers `MODIFY COLUMN`
- Nullable (NOT NULL) — tracked
- Default value — tracked
- Add/Drop — tracked

**Constraint level (add/drop/modify):**
- PRIMARY KEY — tracked, including modify-on-ALTER (composite PK changes)
- FOREIGN KEY — tracked: columns, referenced table, referenced columns, correct drop-before-table-drop / add-after-table-create ordering
- UNIQUE — tracked, named and unnamed
- CHECK — tracked: condition/clause
- Any other/unrecognized constraint type — NOT silently dropped; surfaces as a `-- WARNING: unhandled constraint type` comment in the diff, blocks revert until manually confirmed

**Index level:**
- Add/drop/change (columns or uniqueness) — tracked separately from UNIQUE constraints

**Column-level DDL flags:**
- AUTO_INCREMENT (MySQL) — tracked as a per-column property (is this column auto-incrementing or not), the same way `nullable`/`primary_key` are tracked. Gaining/losing it emits a `MODIFY COLUMN`. The counter *value* is deliberately NOT tracked here — that's data-plane, not schema, and already covered by baseline dump/changelog replay. Postgres IDENTITY/serial uses sequences (a different mechanism) and isn't covered yet.

**Explicitly NOT tracked (known gaps):**
- Postgres IDENTITY/serial sequence values, column collation/charset, generated/computed columns
- Table-level options (engine, charset, partitioning)
- Views, stored procedures/functions, sequences, other triggers as objects

**Separate tracks, not part of schema diff:**
- **DML** (row data: INSERT/UPDATE/DELETE) — via triggers → changelog, not schema_diff
- **DCL** (GRANT/REVOKE) — via `dcl.py`, separate snapshot/diff, not schema_diff
- **TRUNCATE** — separate row-count fallback marker, since triggers can't see it

## Revert

`revert.py` diffs the current live schema against a target snapshot and applies the change **in place**:
- Tables unchanged between now and target are left untouched.
- Tables with column/constraint/index changes get `ALTER`ed in place — no table drop, no data movement, same cost whether the table has 10 rows or 10 million.
- Tables that must actually stop or start existing (added/removed between the two states) are the only case needing DROP/CREATE + a scoped data restore (baseline dump + batched changelog replay).
- Any unhandled constraint type or ambiguous rename blocks the revert by default (`--force` to override) instead of applying silently.
- When reverting to a checkpoint (`--at <timestamp>`), this is a true hard reset: git history moves back to that checkpoint's commit (a safety tag is created first, so nothing is permanently lost), and every checkpoint after that point is pruned from `checkpoints/index.json`.

**Important**: hard-resetting git history affects the ENTIRE repository, not just the DB snapshot files. If this tool's own source code lives in the same git repo as the data it's tracking, a revert will also roll back any uncommitted code changes. Keep this tool's codebase in a separate repo from the DB you're versioning, or commit code changes before testing reverts.

## Continuous capture

`watch.py` runs continuously and covers both capture paths in one process:
- **DML** — already trigger-driven (instant); the loop flushes the live changelog table to a git-tracked file on a short interval (default 2s), since a trigger can't push to an external process directly.
- **DDL** — no native trigger exists for this in MySQL, so it's polled via a cheap catalog fingerprint on a longer interval (default ~30s); on Postgres, DDL event triggers could make this push-based too.
- Trigger bodies are automatically resynced whenever a tracked table's structure changes (a rename/add/drop column would otherwise leave the old trigger referencing a column that no longer exists).
- TRUNCATE (which skips row-level triggers on both MySQL and Postgres) is caught via a row-count fallback check in the same loop.

## UI

`ui/` is an Electron desktop app: a checkpoint timeline (from `checkpoints/index.json` + git log) with click-to-revert, a warning/confirmation flow before applying, and Start/Stop controls for `watch.py`. Run with `cd ui && npm install && npm start`.
