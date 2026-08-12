import hashlib
import json
import os
import subprocess
import sys
import time

import sqlalchemy

from db import get_engine, reflect_schema
from snapshot import build_schema_dict
from schema_diff import SchemaComparator
from changelog import (
    insert_bulk_change_marker,
    poll_once as poll_changelog_once,
    get_tracked_tables,
    install_changelog,
    CHANGELOG_FILE,
    get_last_seen_id,
)
from manifest import record_checkpoint

STATE_FILE = os.path.join("checkpoints", "watch_state.json")


def _dialect(engine) -> str:
    return engine.url.get_backend_name()


def catalog_fingerprint(engine, database_name: str) -> str:
    """Cheap catalog-only fingerprint -- information_schema rows, not a full reflect."""
    query = sqlalchemy.text(
        """
        SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, EXTRA, COLUMN_DEFAULT
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = :db
        ORDER BY TABLE_NAME, ORDINAL_POSITION
        """
    ) if _dialect(engine) == "mysql" else sqlalchemy.text(
        """
        SELECT table_name, column_name, data_type, is_nullable, '', '', column_default
        FROM information_schema.columns
        WHERE table_schema = :db
        ORDER BY table_name, ordinal_position
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"db": database_name}).fetchall()
    raw = json.dumps([list(r) for r in rows], sort_keys=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def table_row_counts(engine, table_names) -> dict:
    """Cheap per-table COUNT(*) -- used only to catch TRUNCATE, which triggers miss."""
    counts = {}
    with engine.connect() as conn:
        for t in table_names:
            result = conn.execute(sqlalchemy.text(f"SELECT COUNT(*) FROM `{t}`"))
            counts[t] = result.scalar()
    return counts


def _load_state():
    if not os.path.exists(STATE_FILE):
        return {"fingerprint": None, "counts": {}}
    with open(STATE_FILE) as f:
        return json.load(f)


def _save_state(state):
    os.makedirs("checkpoints", exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def _git_commit(paths, message) -> str:
    subprocess.run(["git", "add", *paths], check=False)
    subprocess.run(["git", "commit", "-m", message], check=False)
    result = subprocess.run(["git", "rev-parse", "HEAD"], check=False, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def poll_data_once():
    """Flush the live changelog table to the git-tracked file, and -- unlike the
    plain flush -- commit it when there's something new. DML capture itself is
    already instant (trigger-fired); this is what turns that into an actual
    checkpoint in history, the data-side equivalent of a schema-drift commit.
    """
    new_count = poll_changelog_once()
    if not new_count:
        return

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    commit_hash = _git_commit([CHANGELOG_FILE], f"Data checkpoint: {new_count} change(s) captured ({timestamp})")
    record_checkpoint(timestamp=timestamp, changelog_last_id=get_last_seen_id(), git_commit=commit_hash)
    print(f"Data checkpoint committed: {new_count} change(s)")


def poll_schema_once():
    """DDL capture. MySQL has no native DDL trigger (unlike Postgres event triggers),
    so this is polled -- kept cheap via a catalog-only fingerprint, not a full reflect
    unless something actually changed."""
    engine = get_engine()
    database_name = engine.url.database
    state = _load_state()

    fingerprint = catalog_fingerprint(engine, database_name)
    metadata, _ = reflect_schema()
    current_schema = build_schema_dict(metadata, engine)
    current_schema = {t: s for t, s in current_schema.items() if not t.startswith("_dvc_")}
    counts = table_row_counts(engine, current_schema.keys())

    # TRUNCATE / bulk-delete detection: count dropped with no schema change to explain it.
    for table_name, new_count in counts.items():
        old_count = state["counts"].get(table_name)
        if old_count is not None and new_count < old_count:
            insert_bulk_change_marker(engine, table_name, old_count, new_count)

    if fingerprint != state["fingerprint"]:
        os.makedirs("snapshots", exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        snapshot_path = os.path.join("snapshots", f"schema_{timestamp}.json")
        with open(snapshot_path, "w") as f:
            json.dump(current_schema, f, indent=2, sort_keys=True)

        prior_snapshot = state.get("last_snapshot_path")
        if prior_snapshot and os.path.exists(prior_snapshot):
            with open(prior_snapshot) as f:
                old_schema = json.load(f)
            comparator = SchemaComparator(dialect=_dialect(engine))
            statements = comparator.compare_schemas(old_schema, current_schema)
            os.makedirs("migrations", exist_ok=True)
            migration_path = os.path.join("migrations", f"migration_{timestamp}.sql")
            with open(migration_path, "w") as f:
                f.write("-- Auto-generated migration (schema drift detected by watch.py)\n\n")
                for i, stmt in enumerate(statements, 1):
                    f.write(f"-- Statement {i}\n{stmt}\n\n")
            commit_hash = _git_commit([snapshot_path, migration_path], f"Auto-snapshot: schema drift detected ({timestamp})")
        else:
            commit_hash = _git_commit([snapshot_path], f"Auto-snapshot: initial schema capture ({timestamp})")

        state["last_snapshot_path"] = snapshot_path
        record_checkpoint(timestamp=timestamp, schema_snapshot_path=snapshot_path, git_commit=commit_hash)
        print(f"Schema change detected, snapshot written to {snapshot_path}")

        # A structural change (rename/add/drop column) leaves any existing trigger
        # on that table pointing at stale column names -- resync automatically.
        tracked = [t for t in get_tracked_tables(engine) if t in current_schema]
        if tracked:
            install_changelog(current_schema, table_names=tracked)
    else:
        print("No schema change.")

    state["fingerprint"] = fingerprint
    state["counts"] = counts
    _save_state(state)


def main():
    """One continuous process covers both capture paths:

    - DML: already trigger-driven (instant) -- this loop just flushes
      `_dvc_changelog` to the git-tracked file on a short interval, since
      triggers can't push to an external process directly.
    - DDL: no native MySQL hook exists, so this checks the cheap catalog
      fingerprint every `schema_every`-th tick (costlier than a changelog
      read, so checked less often).
    """
    once = "--once" in sys.argv
    positional = [a for a in sys.argv[1:] if a != "--once"]
    changelog_interval = int(positional[0]) if len(positional) > 0 else 1
    schema_every = int(positional[1]) if len(positional) > 1 else 1  # every tick at default interval

    tick = 0
    while True:
        poll_data_once()
        if tick % schema_every == 0:
            poll_schema_once()
        tick += 1

        if once:
            break
        time.sleep(changelog_interval)


if __name__ == "__main__":
    main()
