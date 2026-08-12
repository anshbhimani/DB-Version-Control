import json
import os

MANIFEST_PATH = os.path.join("checkpoints", "index.json")


def _load() -> list:
    if not os.path.exists(MANIFEST_PATH):
        return []
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def _save(entries: list):
    os.makedirs("checkpoints", exist_ok=True)
    with open(MANIFEST_PATH, "w") as f:
        json.dump(entries, f, indent=2)


def record_checkpoint(
    timestamp: str,
    schema_snapshot_path: str = None,
    baseline_dump_path: str = None,
    privilege_snapshot_path: str = None,
    changelog_last_id: int = None,
    git_commit: str = None,
):
    """Tie together whatever checkpoint artifacts exist for one point in time.

    Later fields (e.g. baseline_dump_path) are filled in as they're produced --
    a schema-only checkpoint is valid, so is one that also has a data baseline.
    """
    entries = _load()
    entry = {
        "timestamp": timestamp,
        "git_commit": git_commit,
        "schema_snapshot_path": schema_snapshot_path,
        "baseline_dump_path": baseline_dump_path,
        "privilege_snapshot_path": privilege_snapshot_path,
        "changelog_last_id": changelog_last_id,
    }
    entries.append(entry)
    entries.sort(key=lambda e: e["timestamp"])
    _save(entries)
    return entry


def find_nearest_checkpoint(target_timestamp: str, require_baseline: bool = False) -> dict:
    """Nearest checkpoint at or before target_timestamp (the revert anchor point)."""
    entries = [e for e in _load() if e["timestamp"] <= target_timestamp]
    if require_baseline:
        entries = [e for e in entries if e.get("baseline_dump_path")]
    if not entries:
        return None
    return max(entries, key=lambda e: e["timestamp"])


def latest_checkpoint() -> dict:
    entries = _load()
    return entries[-1] if entries else None


def list_all() -> list:
    return _load()


def prune_after(target_timestamp: str) -> list:
    """Remove checkpoint entries newer than target_timestamp (used after a
    hard-reset revert, so the checkpoint list matches the reset git history
    with no trace of the undone commits). Returns the removed entries."""
    entries = _load()
    kept = [e for e in entries if e["timestamp"] <= target_timestamp]
    removed = [e for e in entries if e["timestamp"] > target_timestamp]
    _save(kept)
    return removed
