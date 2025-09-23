import json
import os
from datetime import datetime
from db import reflect_schema
import sqlalchemy

def extract_check_constraints(engine, database_name: str) -> dict:
    """
    Extract check constraints from INFORMATION_SCHEMA for MySQL.
    Returns dict: {table_name: {constraint_name: check_clause}}
    """
    check_map = {}

    query = f"""
    SELECT
        tc.TABLE_NAME,
        cc.CONSTRAINT_NAME,
        cc.CHECK_CLAUSE
    FROM
        INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
    JOIN
        INFORMATION_SCHEMA.CHECK_CONSTRAINTS cc
        ON tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
    WHERE
        tc.CONSTRAINT_TYPE = 'CHECK'
        AND tc.TABLE_SCHEMA = '{database_name}';
    """

    with engine.connect() as conn:
        result = conn.execute(sqlalchemy.text(query))
        for row in result:
            table = row[0]
            name = row[1]
            clause = row[2]
            check_map.setdefault(table, {})[name] = clause

    return check_map


def serialize_column(col):
    return {
        "name": col.name,
        "type": str(col.type),
        "nullable": col.nullable,
        "primary_key": col.primary_key,
        "default": str(col.default)
    }


def serialize_table(table, external_checks=None):
    """
    Serialize table including SQLAlchemy constraints.
    Optionally merge external_checks (from INFORMATION_SCHEMA).
    """
    constraints = []

    for c in table.constraints:
        c_dict = {
            "name": getattr(c, "name", None),
            "type": c.__class__.__name__,
            "columns": [col.name for col in getattr(c, "columns", [])]
        }
        if isinstance(c, sqlalchemy.ForeignKeyConstraint):
            c_dict["referenced_table"] = c.elements[0].column.table.name
            c_dict["referenced_columns"] = [c.elements[0].column.name]
        if isinstance(c, sqlalchemy.CheckConstraint):
            c_dict["clause"] = str(c.sqltext)
        constraints.append(c_dict)

    # Merge external check constraints
    if external_checks:
        for name, clause in external_checks.items():
            # Avoid duplicates
            if not any(c.get("name") == name for c in constraints):
                constraints.append({
                    "name": name,
                    "type": "CheckConstraint",
                    "columns": [],
                    "clause": clause
                })

    # Sort constraints by name then type
    constraints.sort(key=lambda x: (x['name'] or '', x['type']))

    return {
        "columns": [serialize_column(col) for col in table.columns],
        "indexes": sorted([i.name for i in table.indexes]),
        "constraints": constraints
    }


def snapshot_schema(stream_file=True):
    metadata, engine = reflect_schema()
    database_name = engine.url.database
    check_constraints = extract_check_constraints(engine, database_name)

    os.makedirs("snapshots", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"snapshots/schema_{timestamp}.json"

    if stream_file:
        # Stream JSON directly to file
        with open(filename, "w") as f:
            f.write("{\n")
            tables = sorted(metadata.tables.keys())
            for i, table_name in enumerate(tables):
                table = metadata.tables[table_name]
                serialized = serialize_table(table, external_checks=check_constraints.get(table_name))
                json.dump(table_name, f)
                f.write(": ")
                json.dump(serialized, f, indent=2)
                if i < len(tables) - 1:
                    f.write(",\n")
            f.write("\n}\n")
    else:
        # Regular full JSON dump
        schema_dict = {}
        for table_name, table in metadata.tables.items():
            schema_dict[table_name] = serialize_table(table, external_checks=check_constraints.get(table_name))
        with open(filename, "w") as f:
            json.dump(schema_dict, f, indent=2, sort_keys=True)

    print(f"✅ Schema snapshot saved to {filename}")


if __name__ == "__main__":
    snapshot_schema()
