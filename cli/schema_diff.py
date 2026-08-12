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
        self.fk_drop_statements: List[str] = []
        self.fk_add_statements: List[str] = []
        self.drop_table_statements: List[str] = []
        self.create_table_statements: List[str] = []
        self.alter_statements: List[str] = []
        self._staged_fk_drops: Set[tuple] = set()

    def _add_fk_drop(self, table_name: str, constraint_name):
        key = (table_name, constraint_name)
        if key in self._staged_fk_drops:
            return
        self._staged_fk_drops.add(key)
        cname = f"`{constraint_name}`" if constraint_name else "<UNNAMED>"
        self.fk_drop_statements.append(f"ALTER TABLE `{table_name}` DROP FOREIGN KEY {cname};")

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
        if column.get("auto_increment"):
            col_def += " AUTO_INCREMENT"
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
                    constraint_clause = f"CONSTRAINT {cname} " if cname else ""
                    self.fk_add_statements.append(
                        f"ALTER TABLE `{table_name}` ADD {constraint_clause}FOREIGN KEY ({', '.join(c['columns'])}) REFERENCES {c['referenced_table']}({', '.join(c['referenced_columns'])});"
                    )

        all_defs = column_defs + constraint_defs
        create_stmt = f"CREATE TABLE `{table_name}` (\n    " + ",\n    ".join(all_defs) + "\n);"
        self.create_table_statements.append(create_stmt)

    def _drop_table(self, table_name: str):
        self.drop_table_statements.append(f"DROP TABLE `{table_name}`;")

    def _stage_fk_drops_for_dropped_table(self, table_name: str, old_schema: Dict):
        """Drop the table's own FKs, and any FK in a surviving table that references it."""
        for cons in old_schema.get(table_name, {}).get("constraints", []):
            c = self.parse_constraint(cons)
            if c['type'] == "FOREIGNKEY":
                self._add_fk_drop(table_name, c['name'])

        for other_table, other_schema in old_schema.items():
            if other_table == table_name:
                continue
            for cons in other_schema.get("constraints", []):
                c = self.parse_constraint(cons)
                if c['type'] == "FOREIGNKEY" and c.get('referenced_table') == table_name:
                    self._add_fk_drop(other_table, c['name'])

    @staticmethod
    def _column_signature(col: Dict):
        return (col.get("type"), col.get("nullable", True), str(col.get("default")))

    def _match_renamed_columns(self, old_columns: Dict, new_columns: Dict, dropped: set, added: set):
        """Match dropped/added column names by type/nullable/default signature.

        Returns (renames, ambiguous_comments, still_dropped, still_added).
        Only a UNIQUE signature match on both sides is treated as a confident
        rename -- anything else falls back to drop+add with a flagged comment
        instead of guessing.
        """
        renames = []
        ambiguous_comments = []
        still_dropped = set(dropped)
        still_added = set(added)

        by_signature = {}
        for name in dropped:
            sig = self._column_signature(old_columns[name])
            by_signature.setdefault(sig, {"old": [], "new": []})["old"].append(name)
        for name in added:
            sig = self._column_signature(new_columns[name])
            by_signature.setdefault(sig, {"old": [], "new": []})["new"].append(name)

        for sig, sides in by_signature.items():
            olds, news = sides["old"], sides["new"]
            if len(olds) == 1 and len(news) == 1:
                renames.append((olds[0], news[0]))
                still_dropped.discard(olds[0])
                still_added.discard(news[0])
            elif olds and news:
                ambiguous_comments.append(
                    f"-- POSSIBLE RENAME: confirm -- columns dropped {olds} vs added {news} "
                    f"share the same type/nullable/default; review before applying the drop+add below."
                )

        return renames, ambiguous_comments, still_dropped, still_added

    def _modify_table(self, table_name: str, old_schema: Dict, new_schema: Dict):
        old_columns = {col['name']: col for col in old_schema.get("columns", [])}
        new_columns = {col['name']: col for col in new_schema.get("columns", [])}

        # --- Columns ---
        dropped_cols = set(old_columns) - set(new_columns)
        added_cols = set(new_columns) - set(old_columns)

        renames, ambiguous, dropped_cols, added_cols = self._match_renamed_columns(
            old_columns, new_columns, dropped_cols, added_cols
        )
        for old_name, new_name in renames:
            self.alter_statements.append(
                f"ALTER TABLE `{table_name}` RENAME COLUMN `{old_name}` TO `{new_name}`;"
            )
        self.alter_statements.extend(ambiguous)

        # Drop columns
        for col_name in dropped_cols:
            self.alter_statements.append(f"ALTER TABLE `{table_name}` DROP COLUMN `{col_name}`;")

        # Add columns
        for col_name in added_cols:
            col_def = self.get_column_definition(new_columns[col_name])
            self.alter_statements.append(f"ALTER TABLE `{table_name}` ADD COLUMN {col_def};")

        # Modify existing columns
        for col_name in set(old_columns) & set(new_columns):
            old_col = old_columns[col_name]
            new_col = new_columns[col_name]
            if old_col != new_col:
                col_def = self.get_column_definition(new_col)
                self.alter_statements.append(f"ALTER TABLE `{table_name}` MODIFY COLUMN {col_def};")

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

        old_cons_keys = {_extract_constraint_key(c): c for c in old_constraints}
        new_cons_keys = {_extract_constraint_key(c): c for c in new_constraints}

        # Drop removed constraints (FK drops staged separately so they run before any DROP TABLE)
        for key, c in old_cons_keys.items():
            if key not in new_cons_keys:
                cname = f"`{c['name']}`" if c['name'] else "<UNNAMED>"
                if c['type'] == "CHECK":
                    self.alter_statements.append(f"ALTER TABLE {table_name} DROP CHECK {cname};")
                elif c['type'] == "UNIQUE":
                    self.alter_statements.append(f"ALTER TABLE {table_name} DROP CONSTRAINT {cname};")
                elif c['type'] == "FOREIGNKEY":
                    self._add_fk_drop(table_name, c['name'])
                elif c['type'] == "PRIMARYKEY":
                    self.alter_statements.append(f"ALTER TABLE {table_name} DROP PRIMARY KEY;")
                else:
                    self.alter_statements.append(
                        f"-- WARNING: unhandled constraint type '{c['type']}' ({cname}) removed on {table_name} "
                        f"-- not migrated automatically, verify manually."
                    )

        # Add new constraints (FK adds staged separately so they run after all tables exist)
        for key, c in new_cons_keys.items():
            if key not in old_cons_keys:
                cname = f"`{c['name']}`" if c['name'] else ""
                if c['type'] == "CHECK":
                    constraint_clause = f"CONSTRAINT {cname} " if cname else ""
                    self.alter_statements.append(f"ALTER TABLE {table_name} ADD {constraint_clause}CHECK ({c['condition']});")
                elif c['type'] == "UNIQUE":
                    constraint_clause = f"CONSTRAINT {cname} " if cname else ""
                    self.alter_statements.append(f"ALTER TABLE {table_name} ADD {constraint_clause}UNIQUE ({', '.join(c['columns'])});")
                elif c['type'] == "FOREIGNKEY":
                    constraint_clause = f"CONSTRAINT {cname} " if cname else ""
                    self.fk_add_statements.append(
                        f"ALTER TABLE {table_name} ADD {constraint_clause}FOREIGN KEY ({', '.join(c['columns'])}) REFERENCES {c['referenced_table']}({', '.join(c['referenced_columns'])});"
                    )
                elif c['type'] == "PRIMARYKEY":
                    self.alter_statements.append(f"ALTER TABLE {table_name} ADD PRIMARY KEY ({', '.join(c['columns'])});")
                else:
                    self.alter_statements.append(
                        f"-- WARNING: unhandled constraint type '{c['type']}' ({cname}) added on {table_name} "
                        f"-- not migrated automatically, verify manually."
                    )

        # --- Indexes (non-unique + unique, separate from UNIQUE constraints above) ---
        old_indexes = {idx['name']: idx for idx in old_schema.get("indexes", []) if isinstance(idx, dict)}
        new_indexes = {idx['name']: idx for idx in new_schema.get("indexes", []) if isinstance(idx, dict)}

        for idx_name in set(old_indexes) - set(new_indexes):
            self.alter_statements.append(f"DROP INDEX `{idx_name}` ON `{table_name}`;")

        for idx_name in set(new_indexes) - set(old_indexes):
            idx = new_indexes[idx_name]
            unique_kw = "UNIQUE " if idx.get("unique") else ""
            self.alter_statements.append(
                f"CREATE {unique_kw}INDEX `{idx_name}` ON `{table_name}` ({', '.join(idx['columns'])});"
            )

        for idx_name in set(old_indexes) & set(new_indexes):
            if old_indexes[idx_name] != new_indexes[idx_name]:
                idx = new_indexes[idx_name]
                unique_kw = "UNIQUE " if idx.get("unique") else ""
                self.alter_statements.append(f"DROP INDEX `{idx_name}` ON `{table_name}`;")
                self.alter_statements.append(
                    f"CREATE {unique_kw}INDEX `{idx_name}` ON `{table_name}` ({', '.join(idx['columns'])});"
                )

    def _table_column_signature(self, table_schema: Dict):
        return tuple(
            (col["name"], col.get("type"), col.get("nullable", True))
            for col in sorted(table_schema.get("columns", []), key=lambda c: c["name"])
        )

    def _match_renamed_tables(self, old_schema: Dict, new_schema: Dict, dropped: set, added: set):
        """Match dropped/added table names by column signature (names+types+order).

        Only a UNIQUE signature match is treated as a confident rename.
        """
        renames = []
        ambiguous_comments = []
        still_dropped = set(dropped)
        still_added = set(added)

        by_signature = {}
        for name in dropped:
            sig = self._table_column_signature(old_schema[name])
            by_signature.setdefault(sig, {"old": [], "new": []})["old"].append(name)
        for name in added:
            sig = self._table_column_signature(new_schema[name])
            by_signature.setdefault(sig, {"old": [], "new": []})["new"].append(name)

        for sig, sides in by_signature.items():
            olds, news = sides["old"], sides["new"]
            if len(olds) == 1 and len(news) == 1:
                renames.append((olds[0], news[0]))
                still_dropped.discard(olds[0])
                still_added.discard(news[0])
            elif olds and news:
                ambiguous_comments.append(
                    f"-- POSSIBLE RENAME: confirm -- tables dropped {olds} vs created {news} "
                    f"share the same column signature; review before applying the drop+create below."
                )

        return renames, ambiguous_comments, still_dropped, still_added

    def compare_schemas(self, old_schema: Dict, new_schema: Dict) -> List[str]:
        """Generate SQL statements to transform old_schema -> new_schema.

        Statements are staged by type and emitted in a fixed order so the
        migration is valid regardless of dict/set iteration order:
        1) drop FKs (for dropped + modified tables), 2) DROP TABLE,
        3) CREATE TABLE (no inline FK), 4) other ALTERs, 5) add FKs.
        """
        self.sql_statements = []
        self.fk_drop_statements = []
        self.fk_add_statements = []
        self.drop_table_statements = []
        self.create_table_statements = []
        self.alter_statements = []
        self._staged_fk_drops = set()

        old_tables = set(old_schema.keys())
        new_tables = set(new_schema.keys())

        dropped_tables = old_tables - new_tables
        added_tables = new_tables - old_tables

        table_renames, ambiguous_table_comments, dropped_tables, added_tables = self._match_renamed_tables(
            old_schema, new_schema, dropped_tables, added_tables
        )
        self.alter_statements.extend(ambiguous_table_comments)

        # Renamed tables: emit RENAME, then diff the rest of the definition under the new name
        for old_name, new_name in table_renames:
            self.alter_statements.append(f"ALTER TABLE `{old_name}` RENAME TO `{new_name}`;")
            self._modify_table(new_name, old_schema[old_name], new_schema[new_name])

        # Drop tables (stage their FK drops first)
        for t in dropped_tables:
            self._stage_fk_drops_for_dropped_table(t, old_schema)
            self._drop_table(t)

        # Create tables
        for t in added_tables:
            self._create_table(t, new_schema[t])

        # Modify tables (unchanged table identity, both old and new sides present)
        for t in old_tables & new_tables:
            self._modify_table(t, old_schema[t], new_schema[t])

        deduped_fk_drops = list(dict.fromkeys(self.fk_drop_statements))
        self.sql_statements = (
            deduped_fk_drops
            + self.drop_table_statements
            + self.create_table_statements
            + self.alter_statements
            + self.fk_add_statements
        )
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
