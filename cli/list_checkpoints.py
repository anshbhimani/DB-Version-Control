import json
import os
import subprocess

from manifest import list_all as load_manifest


def _read_migration_file(timestamp: str) -> str:
    """The migration SQL watch.py generated WHEN this checkpoint was captured --
    i.e. what changed to create it, as opposed to the revert plan (which diffs
    current live state against this checkpoint's target)."""
    path = os.path.join("migrations", f"migration_{timestamp}.sql")
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return None


def _git_info(commit_hash: str) -> dict:
    if not commit_hash:
        return {"message": None, "date": None}
    result = subprocess.run(
        ["git", "show", "-s", "--format=%s|%ad", "--date=iso", commit_hash],
        check=False, capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {"message": None, "date": None}
    message, _, date = result.stdout.strip().partition("|")
    return {"message": message, "date": date}


def list_checkpoints() -> list:
    """Checkpoints enriched with their git commit info, newest first -- what the
    UI renders as the clickable commit/checkpoint list."""
    entries = load_manifest()
    enriched = []
    for entry in entries:
        git_info = _git_info(entry.get("git_commit"))
        migration_sql = _read_migration_file(entry["timestamp"])
        enriched.append({**entry, **git_info, "migration_sql": migration_sql})
    enriched.sort(key=lambda e: e["timestamp"], reverse=True)
    return enriched


def main():
    print(json.dumps(list_checkpoints()))


if __name__ == "__main__":
    main()
