import json
import os

import sqlalchemy

from db import get_engine

CHANGELOG_TABLE = "_dvc_changelog"
STATE_FILE = os.path.join("checkpoints", "poller_state.json")
CHANGELOG_FILE = os.path.join("checkpoints", "changelog.jsonl")


def _dialect(engine) -> str:
    return engine.url.get_backend_name()


def changelog_table_ddl(dialect: str) -> str:
    if dialect == "mysql":
        return f"""
        CREATE TABLE IF NOT EXISTS {CHANGELOG_TABLE} (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            table_name VARCHAR(255) NOT NULL,
            op_type VARCHAR(20) NOT NULL,
            pk_json JSON,
            row_json JSON,
            created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6)
        );
        """
    elif dialect in ("postgresql", "postgres"):
        return f"""
        CREATE TABLE IF NOT EXISTS {CHANGELOG_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            table_name VARCHAR(255) NOT NULL,
            op_type VARCHAR(20) NOT NULL,
            pk_json JSONB,
            row_json JSONB,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
    raise ValueError(f"Unsupported dialect: {dialect}")


def trigger_ddl(dialect: str, table_name: str, columns: list, pk_columns: list) -> list:
    """One AFTER INSERT/UPDATE/DELETE trigger per table, writing to the changelog table.

    Standard SQL feature (triggers), NOT CDC/binlog -- syntax differs per dialect,
    the mechanism is portable across any engine that supports triggers.
    """
    pk_cols = pk_columns or columns[:1]

    if dialect == "mysql":
        def pk_object(alias):
            fields = ", ".join(f"'{c}', {alias}.`{c}`" for c in pk_cols)
            return f"JSON_OBJECT({fields})"

        def row_object(alias):
            fields = ", ".join(f"'{c}', {alias}.`{c}`" for c in columns)
            return f"JSON_OBJECT({fields})"

        stmts = []
        stmts.append(f"""
        CREATE TRIGGER trg_{table_name}_dvc_ai AFTER INSERT ON `{table_name}` FOR EACH ROW
        INSERT INTO {CHANGELOG_TABLE} (table_name, op_type, pk_json, row_json)
        VALUES ('{table_name}', 'INSERT', {pk_object('NEW')}, {row_object('NEW')});
        """)
        stmts.append(f"""
        CREATE TRIGGER trg_{table_name}_dvc_au AFTER UPDATE ON `{table_name}` FOR EACH ROW
        INSERT INTO {CHANGELOG_TABLE} (table_name, op_type, pk_json, row_json)
        VALUES ('{table_name}', 'UPDATE', {pk_object('NEW')}, {row_object('NEW')});
        """)
        stmts.append(f"""
        CREATE TRIGGER trg_{table_name}_dvc_ad AFTER DELETE ON `{table_name}` FOR EACH ROW
        INSERT INTO {CHANGELOG_TABLE} (table_name, op_type, pk_json, row_json)
        VALUES ('{table_name}', 'DELETE', {pk_object('OLD')}, {row_object('OLD')});
        """)
        return stmts

    elif dialect in ("postgresql", "postgres"):
        func_name = f"dvc_log_{table_name}"
        pk_fields = ", ".join(f"'{c}', row_data.\"{c}\"" for c in pk_cols)
        row_fields = ", ".join(f"'{c}', row_data.\"{c}\"" for c in columns)
        stmts = [f"""
        CREATE OR REPLACE FUNCTION {func_name}() RETURNS TRIGGER AS $$
        DECLARE
            row_data RECORD;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                row_data := OLD;
            ELSE
                row_data := NEW;
            END IF;
            INSERT INTO {CHANGELOG_TABLE} (table_name, op_type, pk_json, row_json)
            VALUES ('{table_name}', TG_OP, jsonb_build_object({pk_fields}), jsonb_build_object({row_fields}));
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """,
        f"""
        CREATE TRIGGER trg_{table_name}_dvc
        AFTER INSERT OR UPDATE OR DELETE ON "{table_name}"
        FOR EACH ROW EXECUTE FUNCTION {func_name}();
        """]
        return stmts

    raise ValueError(f"Unsupported dialect: {dialect}")


def get_tracked_tables(engine) -> list:
    """Tables that currently have DVC changelog triggers installed.

    Used to re-install triggers after a schema rebuild drops+recreates a table
    (MySQL/Postgres both drop triggers along with their target table).
    """
    dialect = _dialect(engine)
    try:
        with engine.connect() as conn:
            if dialect == "mysql":
                rows = conn.execute(
                    sqlalchemy.text(
                        "SELECT DISTINCT EVENT_OBJECT_TABLE FROM INFORMATION_SCHEMA.TRIGGERS "
                        "WHERE TRIGGER_NAME LIKE 'trg_%_dvc_%'"
                    )
                ).fetchall()
            else:
                rows = conn.execute(
                    sqlalchemy.text(
                        "SELECT DISTINCT event_object_table FROM information_schema.triggers "
                        "WHERE trigger_name LIKE 'trg_%_dvc'"
                    )
                ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def _drop_triggers_for_table(conn, dialect: str, table_name: str):
    """Drop this table's DVC triggers if present -- makes (re)install idempotent.

    Needed because a trigger's body hardcodes the column list at install time;
    an ALTER TABLE (rename/add/drop column) leaves the old trigger pointing at
    columns that may no longer exist, so any structural change requires a
    drop+recreate, not just a plain CREATE TRIGGER.
    """
    if dialect == "mysql":
        for suffix in ("ai", "au", "ad"):
            conn.execute(sqlalchemy.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_dvc_{suffix}"))
    else:
        conn.execute(sqlalchemy.text(f'DROP TRIGGER IF EXISTS trg_{table_name}_dvc ON "{table_name}"'))


def install_changelog(tables: dict, table_names: list = None):
    """Create the changelog table + triggers for the given tables (opt-in subset or all).

    `tables` is the schema dict shape from snapshot.py: {table_name: {columns: [...], constraints: [...]}}.
    Safe to call repeatedly (e.g. after every schema change) -- drops and
    recreates each table's triggers so their column references stay in sync.
    """
    engine = get_engine()
    dialect = _dialect(engine)
    target_tables = table_names or list(tables.keys())

    with engine.connect() as conn:
        conn.execute(sqlalchemy.text(changelog_table_ddl(dialect)))
        conn.commit()

        for table_name in target_tables:
            table_schema = tables[table_name]
            columns = [c["name"] for c in table_schema.get("columns", [])]
            pk_columns = [
                c["name"] for c in table_schema.get("columns", []) if c.get("primary_key")
            ]
            _drop_triggers_for_table(conn, dialect, table_name)
            for stmt in trigger_ddl(dialect, table_name, columns, pk_columns):
                conn.execute(sqlalchemy.text(stmt))
            conn.commit()

    print(f"Changelog + triggers installed for: {', '.join(target_tables)}")


def insert_bulk_change_marker(engine, table_name: str, old_count: int, new_count: int):
    """Insert a synthetic marker for changes triggers can't see (e.g. TRUNCATE).

    MySQL/Postgres both skip row-level AFTER DELETE triggers on TRUNCATE, so a
    sudden row-count drop with no matching DELETE changelog entries is the only
    signal available. This marker tells revert not to trust changelog
    continuity across this point for this table.
    """
    with engine.connect() as conn:
        conn.execute(
            sqlalchemy.text(
                f"INSERT INTO {CHANGELOG_TABLE} (table_name, op_type, pk_json, row_json) "
                f"VALUES (:table_name, 'BULK_CHANGE_DETECTED', NULL, :row_json)"
            ),
            {
                "table_name": table_name,
                "row_json": json.dumps({"old_count": old_count, "new_count": new_count}),
            },
        )
        conn.commit()
    print(f"BULK_CHANGE_DETECTED marker inserted for {table_name}: {old_count} -> {new_count}")


def poll_once(max_rows: int = 5000) -> int:
    """Read new changelog rows since last_seen, append to the git-tracked changelog file.

    Cheap by design: a single indexed `id > :last_seen` query, never a full table scan.
    """
    os.makedirs("checkpoints", exist_ok=True)
    last_seen = 0
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            last_seen = json.load(f).get("last_seen", 0)

    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text(
                f"SELECT id, table_name, op_type, pk_json, row_json, created_at "
                f"FROM {CHANGELOG_TABLE} WHERE id > :last_seen ORDER BY id LIMIT :max_rows"
            ),
            {"last_seen": last_seen, "max_rows": max_rows},
        )
        rows = result.fetchall()

    if not rows:
        return 0

    with open(CHANGELOG_FILE, "a") as f:
        for row in rows:
            entry = {
                "id": row[0],
                "table_name": row[1],
                "op_type": row[2],
                "pk": row[3] if isinstance(row[3], dict) else json.loads(row[3] or "{}"),
                "row": row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}"),
                "created_at": str(row[5]),
            }
            f.write(json.dumps(entry) + "\n")

    new_last_seen = rows[-1][0]
    with open(STATE_FILE, "w") as f:
        json.dump({"last_seen": new_last_seen}, f)

    print(f"Appended {len(rows)} changelog entries (last_seen={new_last_seen})")
    return len(rows)


def load_entries_up_to(up_to_timestamp: str = None, after_id: int = 0) -> list:
    """Read the git-tracked changelog file, bounded by (after_id, up_to_timestamp].

    `after_id` must be the changelog id the baseline dump was taken at -- otherwise
    replay re-applies changes the baseline already contains (duplicate key errors).
    """
    if not os.path.exists(CHANGELOG_FILE):
        return []
    entries = []
    with open(CHANGELOG_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry["id"] <= after_id:
                continue
            if up_to_timestamp and entry["created_at"] > up_to_timestamp:
                break
            entries.append(entry)
    return entries


def batch_replay(engine, entries: list, batch_size: int = 500):
    """Replay changelog entries in batches, one transaction, no per-row execute loop.

    Entries for the same (table, op_type) are grouped into parameterized batches --
    SQLAlchemy's executemany form sends each batch as a single driver round trip
    instead of one round trip per row, which is what makes 100k-row replay safe to
    run against a live DB.
    """
    if not entries:
        return

    with engine.connect() as conn:
        i = 0
        while i < len(entries):
            chunk = entries[i : i + batch_size]
            i += batch_size

            groups = {}
            for entry in chunk:
                key = (entry["table_name"], entry["op_type"])
                groups.setdefault(key, []).append(entry)

            for (table_name, op_type), group_entries in groups.items():
                if op_type == "DELETE":
                    pk_cols = list(group_entries[0]["pk"].keys())
                    where_clause = " AND ".join(f"`{c}` = :{c}" for c in pk_cols)
                    stmt = sqlalchemy.text(f"DELETE FROM `{table_name}` WHERE {where_clause}")
                    conn.execute(stmt, [e["pk"] for e in group_entries])

                elif op_type == "INSERT":
                    cols = list(group_entries[0]["row"].keys())
                    col_list = ", ".join(f"`{c}`" for c in cols)
                    placeholders = ", ".join(f":{c}" for c in cols)
                    stmt = sqlalchemy.text(
                        f"INSERT INTO `{table_name}` ({col_list}) VALUES ({placeholders})"
                    )
                    conn.execute(stmt, [e["row"] for e in group_entries])

                elif op_type == "UPDATE":
                    cols = list(group_entries[0]["row"].keys())
                    pk_cols = list(group_entries[0]["pk"].keys())
                    set_clause = ", ".join(f"`{c}` = :{c}" for c in cols if c not in pk_cols)
                    where_clause = " AND ".join(f"`{c}` = :pk_{c}" for c in pk_cols)
                    stmt = sqlalchemy.text(
                        f"UPDATE `{table_name}` SET {set_clause} WHERE {where_clause}"
                    )
                    params = []
                    for e in group_entries:
                        p = dict(e["row"])
                        for c in pk_cols:
                            p[f"pk_{c}"] = e["pk"][c]
                        params.append(p)
                    conn.execute(stmt, params)

        conn.commit()
