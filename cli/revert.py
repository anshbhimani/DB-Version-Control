import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import sqlalchemy

from db import get_engine, reflect_schema
from snapshot import build_schema_dict
from schema_diff import SchemaComparator
from data_dump import restore_baseline
from changelog import load_entries_up_to, batch_replay, get_tracked_tables, install_changelog
from manifest import find_nearest_checkpoint, prune_after

INTERNAL_TABLE_PREFIXES = ("_dvc_",)


def _is_internal_table(table_name: str) -> bool:
    return any(table_name.startswith(p) for p in INTERNAL_TABLE_PREFIXES)


def load_target_schema(path: str) -> dict:
    with open(path, "r") as f:
        schema = json.load(f)
    return {t: s for t, s in schema.items() if not _is_internal_table(t)}


def get_current_schema() -> dict:
    metadata, engine = reflect_schema()
    schema = build_schema_dict(metadata, engine)
    return {t: s for t, s in schema.items() if not _is_internal_table(t)}


def build_revert_plan(current_schema: dict, target_schema: dict):
    """In-place ALTER-diff plan: current_schema -> target_schema.

    Tables present in BOTH states are ALTERed in place -- no DROP TABLE, no data
    movement, regardless of row count (dropping a column that had a million rows
    is still just a metadata operation; a full rebuild would force restoring all
    million rows for no reason). DROP/CREATE only happens for tables that
    genuinely need to stop or start existing (added/removed between the two
    states) -- that's the only case where a data restore is actually required.

    Safety net: check_for_warnings() must be called on the result before
    applying -- an unhandled constraint type or ambiguous rename means this
    plan should NOT be applied blindly.
    """
    comparator = SchemaComparator()
    comparator.compare_schemas(current_schema, target_schema)
    return comparator


def check_for_warnings(comparator: SchemaComparator) -> list:
    """Flag anything the diff couldn't confidently handle -- unhandled constraint
    types or ambiguous renames -- so revert stops instead of silently guessing."""
    warnings = []
    for stmt in comparator.alter_statements:
        if stmt.startswith("-- WARNING") or stmt.startswith("-- POSSIBLE RENAME"):
            warnings.append(stmt)
    return warnings


def _run_parallel(engine, statements, max_workers=8):
    if not statements:
        return
    def _exec(stmt):
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text(stmt))
            conn.commit()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_exec, stmt): stmt for stmt in statements}
        for future in as_completed(futures):
            future.result()  # re-raise on failure


def _run_sequential(engine, statements):
    executable = [s for s in statements if not s.strip().startswith("--")]
    if not executable:
        return
    with engine.connect() as conn:
        for stmt in executable:
            conn.execute(sqlalchemy.text(stmt))
        conn.commit()


def apply_revert_plan(comparator: SchemaComparator, engine):
    """Execute the plan in dependency-correct stages.

    Within a stage, statements are independent of each other and run in parallel;
    stages themselves run in order: FK drops -> table drops -> table creates ->
    alters -> FK adds. Alters run sequentially -- a single table's alters must
    stay in order (e.g. a rename before a subsequent drop+add on the same
    table), and table count here is small enough that parallelizing wouldn't
    matter; the actual scale risk (row count) never touches this stage at all.
    """
    _run_parallel(engine, list(dict.fromkeys(comparator.fk_drop_statements)))
    _run_sequential(engine, comparator.drop_table_statements)
    _run_parallel(engine, comparator.create_table_statements)
    _run_sequential(engine, comparator.alter_statements)
    _run_parallel(engine, comparator.fk_add_statements)


def print_plan(comparator: SchemaComparator):
    stages = [
        ("Drop FKs (parallel)", list(dict.fromkeys(comparator.fk_drop_statements))),
        ("Drop tables (sequential -- only tables removed at target)", comparator.drop_table_statements),
        ("Create tables (parallel -- only tables added at target)", comparator.create_table_statements),
        ("Alter existing tables in place (sequential, no data touched)", comparator.alter_statements),
        ("Add FKs (parallel)", comparator.fk_add_statements),
    ]
    for title, stmts in stages:
        print(f"\n-- {title} ({len(stmts)} statement(s)) --")
        for stmt in stmts:
            print(stmt)


def apply_data_restore(baseline_dump_file: str, target_timestamp: str = None):
    """Restore baseline dump, then batch-replay changelog up to the target timestamp.

    This is the only data-restore path -- no row-by-row INSERT reconstruction
    anywhere, which is what keeps a 100k-row (or truncated-table) revert safe to
    run against a live DB instead of causing the overload it's meant to avoid.
    """
    restore_baseline(baseline_dump_file)

    marker_file = baseline_dump_file + ".changelog_id"
    after_id = 0
    if os.path.exists(marker_file):
        with open(marker_file) as f:
            after_id = int(f.read().strip())

    entries = load_entries_up_to(target_timestamp, after_id=after_id)
    if not entries:
        print("No changelog entries to replay.")
        return
    engine = get_engine()
    batch_replay(engine, entries)
    print(f"Replayed {len(entries)} changelog entries in batches (up to {target_timestamp or 'latest'}).")


def resolve_from_manifest(target_timestamp: str):
    """Look up the nearest checkpoint at/before target_timestamp for schema + data paths."""
    checkpoint = find_nearest_checkpoint(target_timestamp)
    if not checkpoint:
        raise ValueError(f"No checkpoint found at or before {target_timestamp}")
    return checkpoint


DVC_MANAGED_PATH_PREFIXES = ("snapshots/", "migrations/", "checkpoints/", "data_snapshots/")


def _uncommitted_changes_outside_managed_paths() -> list:
    """`git reset --hard` rewrites the ENTIRE working tree, not just the paths
    this tool writes to. If the tool's own source code (or anything else)
    lives in the same repo with uncommitted changes, a hard reset silently
    wipes them -- this happened once already. Refuse instead of repeating it.
    """
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    offending = []
    for line in result.stdout.splitlines():
        status, path = line[:2], line[3:]
        if status.strip() == "??":
            continue  # untracked files are never touched by reset --hard
        if not any(path.startswith(p) for p in DVC_MANAGED_PATH_PREFIXES):
            offending.append(path)
    return offending


def hard_reset_git_history(target_commit: str, target_timestamp: str):
    """git-reset --hard semantics: the branch moves back to target_commit and
    everything after it disappears from the visible history/checkpoint list --
    matching a git hard reset rather than a git revert (history preserved).

    A safety tag is created first so the discarded commits aren't truly gone
    (recoverable via `git checkout <tag>` / reflog), even though they no
    longer show up in normal `git log` or the UI's checkpoint list.
    """
    offending = _uncommitted_changes_outside_managed_paths()
    if offending:
        print(
            "⚠️  Refusing to hard-reset: uncommitted changes exist outside "
            f"{DVC_MANAGED_PATH_PREFIXES} and would be silently discarded by "
            f"`git reset --hard`:\n  " + "\n  ".join(offending) +
            "\nCommit or stash these first, or keep this tool's source code in a "
            "separate repo from the DB artifacts it tracks."
        )
        return False

    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    current_head = head.stdout.strip() if head.returncode == 0 else None

    if current_head and current_head != target_commit:
        tag_name = f"backup/pre-reset-{target_timestamp}"
        subprocess.run(["git", "tag", tag_name, current_head], check=False)
        print(f"Safety tag created: {tag_name} -> {current_head}")

    result = subprocess.run(["git", "reset", "--hard", target_commit], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"⚠️  git reset --hard failed: {result.stderr.strip()}")
        return False

    removed = prune_after(target_timestamp)
    print(f"✅ Git history reset to {target_commit[:8]} -- {len(removed)} checkpoint(s) after this point removed from the list.")
    return True


def plan_to_dict(comparator: SchemaComparator, warnings: list) -> dict:
    """Structured form of the plan for UI consumption (revert.py --json)."""
    return {
        "fk_drops": list(dict.fromkeys(comparator.fk_drop_statements)),
        "drop_tables": comparator.drop_table_statements,
        "create_tables": comparator.create_table_statements,
        "alters": comparator.alter_statements,
        "fk_adds": comparator.fk_add_statements,
        "warnings": warnings,
        "blocked": bool(warnings),
        "tables_dropped": len(comparator.drop_table_statements),
        "tables_created": len(comparator.create_table_statements),
        "tables_altered_in_place": len({s.split("`")[1] for s in comparator.alter_statements if s.startswith("ALTER TABLE `")}),
    }


def main():
    if len(sys.argv) < 2:
        print(
            "Usage:\n"
            "  python revert.py <target_snapshot.json> [--apply]\n"
            "  python revert.py <target_snapshot.json> --apply --restore-data <baseline_dump.sql> [--to <timestamp>]\n"
            "  python revert.py --at <timestamp> [--apply]   (resolves paths via checkpoints/index.json)\n"
            "  python revert.py --at <timestamp> --json      (structured dry-run plan for UI use)"
        )
        sys.exit(1)

    args = sys.argv[1:]
    json_mode = "--json" in args

    checkpoint = None
    if args[0] == "--at":
        target_timestamp = args[1]
        checkpoint = resolve_from_manifest(target_timestamp)
        target_path = checkpoint["schema_snapshot_path"]
        baseline_dump_file = checkpoint.get("baseline_dump_path")
        apply_changes = "--apply" in args[2:]
    else:
        target_path = args[0]
        rest = args[1:]
        apply_changes = "--apply" in rest
        baseline_dump_file = rest[rest.index("--restore-data") + 1] if "--restore-data" in rest else None
        target_timestamp = rest[rest.index("--to") + 1] if "--to" in rest else None

    target_schema = load_target_schema(target_path)
    current_schema = get_current_schema()

    comparator = build_revert_plan(current_schema, target_schema)
    warnings = check_for_warnings(comparator)

    if json_mode:
        print(json.dumps(plan_to_dict(comparator, warnings)))
        return

    print_plan(comparator)

    if warnings:
        print("\n⚠️  Revert BLOCKED -- confirm these manually before applying:")
        for w in warnings:
            print(w)
        if "--force" not in sys.argv:
            print("\nRe-run with --force to apply anyway (not recommended without manual review).")
            return

    if not apply_changes:
        print("\nDry run only. Re-run with --apply to execute against the live DB.")
        return

    engine = get_engine()
    tracked_before = get_tracked_tables(engine)

    apply_revert_plan(comparator, engine)
    print("\n✅ Schema revert applied.")

    # Dropping+recreating a table drops its triggers with it -- reinstall for any
    # table that was being change-tracked before this revert.
    tracked_still_present = [t for t in tracked_before if t in target_schema]
    if tracked_still_present:
        install_changelog(target_schema, table_names=tracked_still_present)
        print(f"✅ Changelog triggers reinstalled for: {', '.join(tracked_still_present)}")

    if baseline_dump_file:
        apply_data_restore(baseline_dump_file, target_timestamp)
        print("✅ Data restore applied.")

    # Hard-reset semantics: only meaningful in --at mode, where the target
    # checkpoint maps to a specific commit. Runs last, after the DB is already
    # confirmed in the target state.
    if checkpoint and checkpoint.get("git_commit"):
        hard_reset_git_history(checkpoint["git_commit"], target_timestamp)


if __name__ == "__main__":
    main()
