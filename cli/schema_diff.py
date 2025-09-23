import json
import sys
from typing import Dict, List, Set
import os

class SchemaComparator:
    def __init__(self, dialect: str = "mysql"):
        """
        dialect: "mysql" or "postgresql"
        """
        self.dialect = dialect.lower()
        self.sql_statements: List[str] = []

    def parse_constraint(self, constraint: Dict) -> Dict:
        ctype = constraint.get("type", "").upper()
        if ctype.endswith("CONSTRAINT"):
            ctype = ctype.replace("CONSTRAINT", "") 
        return {
            "type": ctype,
            "columns": constraint.get("columns", []),
            "name": constraint.get("name"),
            "referenced_table": constraint.get("referenced_table"),
            "referenced_columns": constraint.get("referenced_columns", []),
            "condition": constraint.get("clause") or constraint.get("definition") or constraint.get("condition")
        }


    def get_column_definition(self, column: Dict) -> str:
        """Generate column definition for CREATE/ALTER statements"""
        col_def = f"`{column['name']}` {column['type']}"
        if not column.get("nullable", True):
            col_def += " NOT NULL"
        if column.get("default") and column["default"] != "None":
            col_def += f" DEFAULT {column['default']}"
        return col_def

    def _extract_not_null_columns(self, columns: List[Dict]) -> Set[str]:
        return {col["name"] for col in columns if not col.get("nullable", True)}
    
    def _create_table(self, table_name: str, table_schema: Dict):
        """Generate CREATE TABLE statement"""
        column_defs = [self.get_column_definition(col) for col in table_schema.get("columns", [])]
        constraint_defs = []

        for cons in table_schema.get("constraints", []):
                c = self.parse_constraint(cons)
                if c['type'] == "PRIMARYKEY" and c['columns']:
                    constraint_defs.append(f"PRIMARY KEY ({', '.join(c['columns'])})")
                elif c['type'] == "CHECK" and c.get('condition'):
                    cname = f"`{c['name']}`" if c['name'] else ""
                    constraint_defs.append(f"CONSTRAINT {cname} CHECK ({c['condition']})")
                elif c['type'] == "UNIQUE" and c.get('columns'):
                    cname = f"`{c['name']}`" if c['name'] else ""
                    constraint_defs.append(f"CONSTRAINT {cname} UNIQUE ({', '.join(c['columns'])})")
                elif c['type'] == "FOREIGNKEY" and c.get('columns') and c.get('referenced_table') and c.get('referenced_columns'):
                    cname = f"`{c['name']}`" if c['name'] else ""
                    constraint_defs.append(
                        f"CONSTRAINT {cname} FOREIGN KEY ({', '.join(c['columns'])}) REFERENCES {c['referenced_table']}({', '.join(c['referenced_columns'])})"
                    )

        all_defs = column_defs + constraint_defs
        create_stmt = f"CREATE TABLE `{table_name}` (\n    " + ",\n    ".join(all_defs) + "\n);"
        self.sql_statements.append(create_stmt)

    def _drop_table(self, table_name: str):
        self.sql_statements.append(f"DROP TABLE `{table_name}`;")

    def _modify_table(self, table_name: str, old_schema: Dict, new_schema: Dict):
        old_columns = {col['name']: col for col in old_schema.get("columns", [])}
        new_columns = {col['name']: col for col in new_schema.get("columns", [])}

        # --- Columns ---
        # Drop columns
        for col_name in set(old_columns) - set(new_columns):
            self.sql_statements.append(f"ALTER TABLE `{table_name}` DROP COLUMN `{col_name}`;")

        # Add columns
        for col_name in set(new_columns) - set(old_columns):
            col_def = self.get_column_definition(new_columns[col_name])
            self.sql_statements.append(f"ALTER TABLE `{table_name}` ADD COLUMN {col_def};")

        # Modify existing columns
        for col_name in set(old_columns) & set(new_columns):
            old_col = old_columns[col_name]
            new_col = new_columns[col_name]
            if old_col != new_col:
                col_def = self.get_column_definition(new_col)
                self.sql_statements.append(f"ALTER TABLE `{table_name}` MODIFY COLUMN {col_def};")

        # --- Constraints ---
        def _extract_constraint_key(c: Dict):
            """
            Returns a tuple that uniquely identifies a constraint for comparison purposes.
            This is used to detect added, removed, or modified constraints.
            """
            ctype = c['type'].upper()

            # PRIMARY KEY
            if ctype == "PRIMARYKEY":
                # PK is uniquely identified by its columns
                return (ctype, tuple(c.get('columns', [])))

            # UNIQUE
            elif ctype == "UNIQUE":
                return (
                    ctype,
                    tuple(c.get('columns', [])),   # columns are required
                    c.get('name')                  # include name if exists
                )

            # FOREIGN KEY
            elif ctype == "FOREIGNKEY":
                return (
                    ctype,
                    tuple(c.get('columns', [])),
                    c.get('referenced_table'),
                    tuple(c.get('referenced_columns', [])),
                    c.get('name')
                )

            # CHECK
            elif ctype == "CHECK":
                return (
                    ctype,
                    c.get('condition'),  # CHECK is uniquely identified by its condition
                    c.get('name')
                )

            # fallback: just use type and name
            return (ctype, c.get('name'))


        old_constraints = [self.parse_constraint(c) for c in old_schema.get("constraints", [])]
        new_constraints = [self.parse_constraint(c) for c in new_schema.get("constraints", [])]
        print("Old constraints:", old_constraints)
        print("New constraints:", new_constraints)

        old_cons_keys = {_extract_constraint_key(c): c for c in old_constraints}
        new_cons_keys = {_extract_constraint_key(c): c for c in new_constraints}

        print("Old keys:", old_cons_keys.keys())
        print("New keys:", new_cons_keys.keys())
        # Drop removed constraints
        for key, c in old_cons_keys.items():
            if key not in new_cons_keys:
                cname = f"`{c['name']}`" if c['name'] else "<UNNAMED>"
                if c['type'] == "CHECK":
                    self.sql_statements.append(f"ALTER TABLE {table_name} DROP CHECK {cname};")
                elif c['type'] == "UNIQUE":
                    self.sql_statements.append(f"ALTER TABLE {table_name} DROP CONSTRAINT {cname};")
                elif c['type'] == "FOREIGNKEY":
                    self.sql_statements.append(f"ALTER TABLE {table_name} DROP FOREIGN KEY {cname};")

        # Add new constraints
        for key, c in new_cons_keys.items():
            if key not in old_cons_keys:
                cname = f"`{c['name']}`" if c['name'] else ""
                if c['type'] == "CHECK":
                    self.sql_statements.append(f"ALTER TABLE {table_name} ADD CONSTRAINT {cname} CHECK ({c['condition']});")
                elif c['type'] == "UNIQUE":
                    self.sql_statements.append(f"ALTER TABLE {table_name} ADD CONSTRAINT {cname} UNIQUE ({', '.join(c['columns'])});")
                elif c['type'] == "FOREIGNKEY":
                    self.sql_statements.append(
                        f"ALTER TABLE {table_name} ADD CONSTRAINT {cname} FOREIGN KEY ({', '.join(c['columns'])}) REFERENCES {c['referenced_table']}({', '.join(c['referenced_columns'])});"
                    )

    def compare_schemas(self, old_schema: Dict, new_schema: Dict) -> List[str]:
        """Generate SQL statements to transform old_schema -> new_schema"""
        self.sql_statements = []

        old_tables = set(old_schema.keys())
        new_tables = set(new_schema.keys())

        # Drop tables
        for t in old_tables - new_tables:
            self._drop_table(t)

        # Create tables
        for t in new_tables - old_tables:
            self._create_table(t, new_schema[t])

        # Modify tables
        for t in old_tables & new_tables:
            self._modify_table(t, old_schema[t], new_schema[t])

        return self.sql_statements


def main():
    if len(sys.argv) != 3:
        print("Usage: python schema_diff.py <old_schema.json> <new_schema.json>")
        sys.exit(1)

    with open(sys.argv[1], "r") as f:
        old_schema = json.load(f)
    with open(sys.argv[2], "r") as f:
        new_schema = json.load(f)

    comparator = SchemaComparator(dialect="mysql")
    sql_statements = comparator.compare_schemas(old_schema, new_schema)

    output_file = "migration_output.sql"
    output_dir = "migrations"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, output_file)
    with open(output_file, "w") as f:
        f.write("-- SQL migration script\n\n")
        for i, stmt in enumerate(sql_statements, 1):
            f.write(f"-- Statement {i}\n{stmt}\n\n")
    print(f"✅ Migration SQL written to {output_file}")


if __name__ == "__main__":
    main()
