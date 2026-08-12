import json
import sys

import sqlalchemy

from db import get_engine


def _dialect(engine) -> str:
    return engine.url.get_backend_name()


def extract_privileges(engine, database_name: str) -> dict:
    """Snapshot GRANT-level privileges via information_schema (no row triggers apply to DCL).

    Returns {grantee: [{"table": table_or_*, "privilege": "SELECT"}, ...]}, sorted
    for stable diffing.
    """
    dialect = _dialect(engine)
    privileges = {}

    if dialect == "mysql":
        query = sqlalchemy.text(
            """
            SELECT GRANTEE, TABLE_SCHEMA, TABLE_NAME, PRIVILEGE_TYPE
            FROM INFORMATION_SCHEMA.TABLE_PRIVILEGES
            WHERE TABLE_SCHEMA = :db
            """
        )
    elif dialect in ("postgresql", "postgres"):
        query = sqlalchemy.text(
            """
            SELECT grantee, table_schema, table_name, privilege_type
            FROM information_schema.role_table_grants
            WHERE table_schema = :db
            """
        )
    else:
        raise ValueError(f"Unsupported dialect for privilege extraction: {dialect}")

    with engine.connect() as conn:
        rows = conn.execute(query, {"db": database_name}).fetchall()

    for grantee, schema, table, privilege in rows:
        privileges.setdefault(grantee, []).append({"table": table, "privilege": privilege})

    for grantee in privileges:
        privileges[grantee].sort(key=lambda p: (p["table"], p["privilege"]))

    return privileges


def snapshot_privileges(out_path: str = None) -> str:
    engine = get_engine()
    database_name = engine.url.database
    privileges = extract_privileges(engine, database_name)

    import os
    from datetime import datetime

    os.makedirs("snapshots", exist_ok=True)
    out_path = out_path or os.path.join(
        "snapshots", f"privileges_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(out_path, "w") as f:
        json.dump(privileges, f, indent=2, sort_keys=True)
    print(f"Privilege snapshot written to {out_path}")
    return out_path


def compare_privileges(old: dict, new: dict) -> list:
    """Diff two privilege snapshots into GRANT/REVOKE statements."""
    statements = []
    old_grantees = set(old.keys())
    new_grantees = set(new.keys())

    def _key(entry):
        return (entry["table"], entry["privilege"])

    for grantee in old_grantees | new_grantees:
        old_entries = {_key(e) for e in old.get(grantee, [])}
        new_entries = {_key(e) for e in new.get(grantee, [])}

        for table, privilege in old_entries - new_entries:
            statements.append(f"REVOKE {privilege} ON {table} FROM {grantee};")

        for table, privilege in new_entries - old_entries:
            statements.append(f"GRANT {privilege} ON {table} TO {grantee};")

    return statements


def main():
    if len(sys.argv) != 3:
        print("Usage: python dcl.py <old_privileges.json> <new_privileges.json>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        old = json.load(f)
    with open(sys.argv[2]) as f:
        new = json.load(f)

    statements = compare_privileges(old, new)

    import os

    os.makedirs("migrations", exist_ok=True)
    out_path = os.path.join("migrations", "privilege_migration.sql")
    with open(out_path, "w") as f:
        f.write("-- DCL migration script\n\n")
        for i, stmt in enumerate(statements, 1):
            f.write(f"-- Statement {i}\n{stmt}\n\n")
    print(f"Privilege migration SQL written to {out_path}")


if __name__ == "__main__":
    main()
