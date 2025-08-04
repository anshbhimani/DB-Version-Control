import json
import os
import sys
from datetime import datetime
import re

def load_schema(file_path):
    with open(file_path, "r") as f:
        return json.load(f)

def compare_schemas(old_schema, new_schema):
    up_statements = []
    down_statements = []

    old_tables = set(old_schema.keys())
    new_tables = set(new_schema.keys())

    added_tables = new_tables - old_tables
    removed_tables = old_tables - new_tables
    common_tables = old_tables & new_tables

    # Handle added tables
    for table in added_tables:
        cols = new_schema[table]["columns"]
        col_defs = []
        for col in cols:
            nullability = "NOT NULL" if not col["nullable"] else ""
            col_defs.append(f"{col['name']} {col['type']} {nullability}".strip())
        up_statements.append(f"CREATE TABLE {table} ({', '.join(col_defs)});")
        down_statements.append(f"DROP TABLE {table};")

    # Handle removed tables
    for table in removed_tables:
        cols = old_schema[table]["columns"]
        col_defs = []
        for col in cols:
            nullability = "NOT NULL" if not col["nullable"] else ""
            col_defs.append(f"{col['name']} {col['type']} {nullability}".strip())
        down_statements.append(f"CREATE TABLE {table} ({', '.join(col_defs)});")
        up_statements.append(f"DROP TABLE {table};")

    # Handle table modifications
    for table in common_tables:
        old_cols = {col["name"]: col for col in old_schema[table]["columns"]}
        new_cols = {col["name"]: col for col in new_schema[table]["columns"]}

        added_cols = new_cols.keys() - old_cols.keys()
        removed_cols = old_cols.keys() - new_cols.keys()
        common_cols = new_cols.keys() & old_cols.keys()

        for col in added_cols:
            col_def = new_cols[col]
            nullability = "NOT NULL" if not col_def["nullable"] else ""
            up_statements.append(f"ALTER TABLE {table} ADD COLUMN {col_def['name']} {col_def['type']} {nullability};")
            down_statements.append(f"ALTER TABLE {table} DROP COLUMN {col_def['name']};")

        for col in removed_cols:
            col_def = old_cols[col]
            nullability = "NOT NULL" if not col_def["nullable"] else ""
            down_statements.append(f"ALTER TABLE {table} ADD COLUMN {col_def['name']} {col_def['type']} {nullability};")
            up_statements.append(f"ALTER TABLE {table} DROP COLUMN {col_def['name']};")

        for col in common_cols:
            old_col = old_cols[col]
            new_col = new_cols[col]

            if old_col["type"] != new_col["type"]:
                up_statements.append(f"ALTER TABLE {table} MODIFY COLUMN {col} {new_col['type']};")
                down_statements.append(f"ALTER TABLE {table} MODIFY COLUMN {col} {old_col['type']};")

            if old_col["nullable"] != new_col["nullable"]:
                if new_col["nullable"]:
                    up_statements.append(f"ALTER TABLE {table} MODIFY COLUMN {col} {new_col['type']} NULL;")
                    down_statements.append(f"ALTER TABLE {table} MODIFY COLUMN {col} {old_col['type']} NOT NULL;")
                else:
                    up_statements.append(f"ALTER TABLE {table} MODIFY COLUMN {col} {new_col['type']} NOT NULL;")
                    down_statements.append(f"ALTER TABLE {table} MODIFY COLUMN {col} {old_col['type']} NULL;")

        # Handle primary keys
        old_pk = [col["name"] for col in old_cols.values() if col["primary_key"]]
        new_pk = [col["name"] for col in new_cols.values() if col["primary_key"]]
        if set(old_pk) != set(new_pk):
            if old_pk:
                down_statements.append(f"ALTER TABLE {table} ADD PRIMARY KEY ({', '.join(old_pk)});")
                up_statements.append(f"ALTER TABLE {table} DROP PRIMARY KEY;")
            if new_pk:
                up_statements.append(f"ALTER TABLE {table} ADD PRIMARY KEY ({', '.join(new_pk)});")
                down_statements.append(f"ALTER TABLE {table} DROP PRIMARY KEY;")

        # Handle foreign key constraints
        old_constraints = set(old_schema[table].get("constraints", []))
        new_constraints = set(new_schema[table].get("constraints", []))

        added_constraints = new_constraints - old_constraints
        removed_constraints = old_constraints - new_constraints

        for constraint in added_constraints:
            if "ForeignKeyConstraint" in constraint:
                fk_name, stmt = parse_foreign_key_constraint(constraint, table)
                up_statements.append(stmt)
                down_statements.append(f"ALTER TABLE {table} DROP CONSTRAINT {fk_name};")
            else:
                up_statements.append(f"-- ADD CONSTRAINT {constraint}")
                down_statements.append(f"-- DROP CONSTRAINT {constraint}")

        for constraint in removed_constraints:
            if "ForeignKeyConstraint" in constraint:
                fk_name, stmt = parse_foreign_key_constraint(constraint, table)
                up_statements.append(f"ALTER TABLE {table} DROP CONSTRAINT {fk_name};")
                down_statements.append(stmt)
            else:
                up_statements.append(f"-- DROP CONSTRAINT {constraint}")
                down_statements.append(f"-- ADD CONSTRAINT {constraint}")

    return up_statements, down_statements


def parse_foreign_key_constraint(constraint_str, table):
    """
    Parses a SQLAlchemy-style foreign key constraint and returns:
    - constraint name (deterministic)
    - SQL string to add the constraint
    """
    col_match = re.search(r"\[(.*?)\]", constraint_str)
    ref_match = re.search(r"\[(.*?\..*?)\]", constraint_str.split(",", 1)[-1])

    if not col_match or not ref_match:
        return "unknown_fk", f"-- Malformed FK constraint: {constraint_str}"

    local_cols = [x.strip().strip("'\"") for x in col_match.group(1).split(',')]
    ref = ref_match.group(1).strip().strip("'\"")
    ref_table, ref_col = ref.split(".")

    constraint_name = f"fk_{table}_{'_'.join(local_cols)}_{ref_table}_{ref_col}"
    fk_sql = f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} FOREIGN KEY ({', '.join(local_cols)}) REFERENCES {ref_table}({ref_col});"

    return constraint_name, fk_sql

def save_migration(up_sql, down_sql):
    os.makedirs("migrations", exist_ok=True)
    version = datetime.now().strftime("%Y%m%d_%H%M%S")
    up_file = f"migrations/{version}.up.sql"
    down_file = f"migrations/{version}.down.sql"

    with open(up_file, "w") as f:
        f.write("\n".join(up_sql))
    with open(down_file, "w") as f:
        f.write("\n".join(down_sql))

    print(f"✅ Migration files generated:\n  - {up_file}\n  - {down_file}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python cli/schema_diff.py <old_snapshot.json> <new_snapshot.json>")
        sys.exit(1)

    old_path = sys.argv[1]
    new_path = sys.argv[2]

    old_schema = load_schema(old_path)
    new_schema = load_schema(new_path)

    up_sql, down_sql = compare_schemas(old_schema, new_schema)
    save_migration(up_sql, down_sql)