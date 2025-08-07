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
            table = row[0]           # tc.TABLE_NAME
            name = row[1]            # cc.CONSTRAINT_NAME
            clause = row[2]          # cc.CHECK_CLAUSE
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

def serialize_table(table):
    return {
        "columns": [serialize_column(col) for col in table.columns],
        "indexes": [str(i) for i in table.indexes],
        "constraints": [str(c) for c in table.constraints]
    }

def snapshot_schema():
    metadata,engine = reflect_schema()
    database_name = engine.url.database

    check_constraints = extract_check_constraints(engine, database_name)

    schema_dict = {}
    for table_name, table in metadata.tables.items():
        serialized = serialize_table(table)

        # Attach parsed check constraints if found
        if table_name in check_constraints:
            for name, clause in check_constraints[table_name].items():
                serialized["constraints"].append(f"CheckConstraint('{clause}', name='{name}')")

        schema_dict[table_name] = serialized

    os.makedirs("snapshots", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"snapshots/schema_{timestamp}.json"

    with open(filename, "w") as f:
        json.dump(schema_dict, f, indent=2)

    print(f"✅ Schema snapshot saved to {filename}")

if __name__ == "__main__":
    snapshot_schema()