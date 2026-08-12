import os
import subprocess
import sys
from datetime import datetime
from urllib.parse import urlparse

import sqlalchemy as sa

from db import get_engine


def _current_changelog_id(engine) -> int:
    """Max id in _dvc_changelog at this moment, so replay knows where to start from."""
    with engine.connect() as conn:
        try:
            result = conn.execute(sa.text("SELECT MAX(id) FROM _dvc_changelog"))
            return result.scalar() or 0
        except Exception:
            return 0  # changelog not installed yet -- nothing to exclude


def _dialect(engine) -> str:
    return engine.url.get_backend_name()  # "mysql" or "postgresql"


def _conn_parts(engine):
    url = engine.url
    return {
        "host": url.host or "localhost",
        "port": url.port,
        "user": url.username,
        "password": url.password,
        "database": url.database,
    }


def dump_baseline(timestamp: str = None, table: str = None, out_dir: str = "data_snapshots") -> str:
    """Dump the full DB (or a single table) via the engine-native dump tool.

    Native dump tools are bulk-optimized regardless of row count -- this is
    intentionally NOT a row-by-row export.
    """
    engine = get_engine()
    dialect = _dialect(engine)
    conn = _conn_parts(engine)

    os.makedirs(out_dir, exist_ok=True)
    timestamp = timestamp or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{table}" if table else ""
    out_file = os.path.join(out_dir, f"baseline_{timestamp}{suffix}.sql")

    env = os.environ.copy()

    if dialect == "mysql":
        # --no-create-info: data-only. Schema is rebuilt separately by revert.py's
        # own CREATE TABLE step -- restoring a schema-carrying dump here would
        # re-run DROP TABLE/CREATE TABLE on tables that already have triggers
        # attached, silently destroying them (MySQL drops triggers with their table).
        cmd = ["mysqldump", "--protocol=TCP", "--no-create-info", "-h", conn["host"], "-u", conn["user"]]
        if conn["port"]:
            cmd += ["-P", str(conn["port"])]
        if conn["password"]:
            env["MYSQL_PWD"] = conn["password"]  # avoid password showing up in `ps`
        cmd += [conn["database"]]
        if table:
            cmd += [table]
        else:
            # Never dump internal bookkeeping tables -- restoring a baseline must not
            # overwrite the live changelog with a stale snapshot of it.
            cmd += [f"--ignore-table={conn['database']}._dvc_changelog"]
    elif dialect in ("postgresql", "postgres"):
        cmd = ["pg_dump", "--data-only", "-h", conn["host"], "-U", conn["user"], "-d", conn["database"]]
        if conn["port"]:
            cmd += ["-p", str(conn["port"])]
        if table:
            cmd += ["-t", table]
        else:
            cmd += ["--exclude-table=_dvc_changelog"]
        if conn["password"]:
            env["PGPASSWORD"] = conn["password"]
    else:
        raise ValueError(f"Unsupported dialect for dump: {dialect}")

    baseline_changelog_id = _current_changelog_id(engine)

    with open(out_file, "w") as f:
        subprocess.run(cmd, stdout=f, check=True, env=env)

    marker_file = out_file + ".changelog_id"
    with open(marker_file, "w") as f:
        f.write(str(baseline_changelog_id))

    print(f"Baseline dump written to {out_file} (changelog cutoff id={baseline_changelog_id})")
    return out_file


def restore_baseline(dump_file: str):
    """Restore a dump via the native restore tool -- bulk load, not per-row INSERTs."""
    engine = get_engine()
    dialect = _dialect(engine)
    conn = _conn_parts(engine)
    env = os.environ.copy()

    if dialect == "mysql":
        cmd = ["mysql", "--protocol=TCP", "-h", conn["host"], "-u", conn["user"]]
        if conn["port"]:
            cmd += ["-P", str(conn["port"])]
        if conn["password"]:
            env["MYSQL_PWD"] = conn["password"]
        cmd += [conn["database"]]
        with open(dump_file, "r") as f:
            subprocess.run(cmd, stdin=f, check=True, env=env)
    elif dialect in ("postgresql", "postgres"):
        if conn["password"]:
            env["PGPASSWORD"] = conn["password"]
        cmd = ["psql", "-h", conn["host"], "-U", conn["user"], "-d", conn["database"]]
        if conn["port"]:
            cmd += ["-p", str(conn["port"])]
        cmd += ["-f", dump_file]
        subprocess.run(cmd, check=True, env=env)
    else:
        raise ValueError(f"Unsupported dialect for restore: {dialect}")

    print(f"Restored {dump_file}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python data_dump.py --dump [table] | --restore <dump_file>")
        sys.exit(1)

    if sys.argv[1] == "--dump":
        table = sys.argv[2] if len(sys.argv) > 2 else None
        dump_baseline(table=table)
    elif sys.argv[1] == "--restore":
        restore_baseline(sys.argv[2])
    else:
        print("Usage: python data_dump.py --dump [table] | --restore <dump_file>")
        sys.exit(1)


if __name__ == "__main__":
    main()
