import json
import re
from typing import Dict, List, Set, Tuple, Optional
import sys

class SchemaComparator:
    def __init__(self):
        self.sql_statements = []
    
    def parse_constraint(self, constraint_str: str) -> Dict:
        """Parse constraint string to extract constraint information"""
        constraint = {
            'type': None,
            'columns': [],
            'name': None,
            'referenced_table': None,
            'referenced_columns': []
        }
        
        if 'PrimaryKeyConstraint' in constraint_str:
            constraint['type'] = 'PRIMARY_KEY'
            # Extract column names from PrimaryKeyConstraint
            if "Column('" in constraint_str:
                columns = re.findall(r"Column\('([^']+)'", constraint_str)
                constraint['columns'] = columns
        
        elif 'ForeignKeyConstraint' in constraint_str:
            constraint['type'] = 'FOREIGN_KEY'
            # Extract constraint name
            if "name='" in constraint_str:
                name_match = re.search(r"name='([^']+)'", constraint_str)
                if name_match:
                    constraint['name'] = name_match.group(1)
            
            # Extract column name and referenced table/column from the constraint string
            # Look for ForeignKey('table.column') pattern
            fk_matches = re.findall(r"ForeignKey\('([^.]+)\.([^']+)'\)", constraint_str)
            if fk_matches:
                constraint['referenced_table'] = fk_matches[0][0]
                constraint['referenced_columns'] = [fk_matches[0][1]]
                
                # Find the local column by looking for the column that has this ForeignKey
                col_fk_pattern = r"Column\('([^']+)', [^,]+, ForeignKey\('[^']+'\)"
                col_match = re.search(col_fk_pattern, constraint_str)
                if col_match:
                    constraint['columns'] = [col_match.group(1)]
        
        return constraint
    
    def get_column_definition(self, column: Dict) -> str:
        """Generate column definition for CREATE/ALTER statements"""
        col_def = f"{column['name']} {column['type']}"
        
        if not column['nullable']:
            col_def += " NOT NULL"
        
        if column['default'] and column['default'] != "None":
            col_def += f" DEFAULT {column['default']}"
        
        return col_def
    
    def compare_schemas(self, old_schema: Dict, new_schema: Dict) -> List[str]:
        """Compare two schemas and generate ALTER statements"""
        self.sql_statements = []
        
        # Get table names from both schemas
        old_tables = set(old_schema.keys())
        new_tables = set(new_schema.keys())
        
        # Tables to drop (exist in old_schema but not in new_schema)
        tables_to_drop = old_tables - new_tables
        
        # Tables to create (exist in new_schema but not in old_schema)
        tables_to_create = new_tables - old_tables
        
        # Tables to modify (exist in both schemas)
        tables_to_modify = old_tables & new_tables
        
        # First, drop foreign key constraints from tables that will be dropped or modified
        for table_name in sorted(tables_to_drop | tables_to_modify):
            if table_name in old_schema:
                self._drop_removed_foreign_keys(table_name, old_schema[table_name],new_schema)
        
        # Process table modifications (ALTER instead of DROP/CREATE when possible)
        for table_name in sorted(tables_to_modify):
            self._modify_table(table_name, old_schema[table_name], new_schema[table_name])
        
        # Process table drops
        for table_name in sorted(tables_to_drop):
            self._drop_table(table_name, old_schema[table_name])
        
        # Process table creations
        for table_name in sorted(tables_to_create):
            self._create_table(table_name, new_schema[table_name])
        
        # Add foreign key constraints for new and modified tables
        for table_name in sorted(tables_to_create | tables_to_modify):
            if table_name in new_schema:
                self._add_foreign_keys(table_name, new_schema[table_name])
        
        return self.sql_statements
    
  
    def _extract_last_fk_name(self, constraints: List[str]) -> Set[str]:
        """
        Extract only the last foreign key constraint name from the constraint list.
        """
        last_fk_name = None
        for constraint in constraints:
            if "ForeignKeyConstraint" in constraint:
                match = re.search(r"name='(.*?)'", constraint)
                if match:
                    last_fk_name = match.group(1)
        return {last_fk_name} if last_fk_name else set()



    def _drop_removed_foreign_keys(self, table_name: str, old_table_def: Dict, new_schema: Dict):
        """
        Generate DROP FOREIGN KEY statements for foreign keys that exist in old_table_def but not in new_schema.
        """
        old_constraints = old_table_def.get("constraints", [])
        new_constraints = new_schema.get(table_name, {}).get("constraints", [])

        old_fk_names = self._extract_last_fk_name(old_constraints)
        new_fk_names = self._extract_last_fk_name(new_constraints)

        to_drop = old_fk_names - new_fk_names

        for fk_name in to_drop:
            self.sql_statements.append(
                f"ALTER TABLE `{table_name}` DROP FOREIGN KEY `{fk_name}`;"
            )


    def _add_foreign_keys(self, table_name: str, table_schema: Dict):
        """Add foreign key constraints to a table"""
        for constraint_str in table_schema.get('constraints', []):
            constraint = self.parse_constraint(constraint_str)
            if constraint['type'] == 'FOREIGN_KEY':
                if (constraint['columns'] and constraint['referenced_table'] and 
                    constraint['referenced_columns']):
                    
                    column_name = constraint['columns'][0]
                    ref_table = constraint['referenced_table']
                    ref_column = constraint['referenced_columns'][0]
                    
                    if constraint['name']:
                        fk_stmt = f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint['name']} FOREIGN KEY ({column_name}) REFERENCES {ref_table}({ref_column});"
                    else:
                        fk_stmt = f"ALTER TABLE {table_name} ADD FOREIGN KEY ({column_name}) REFERENCES {ref_table}({ref_column});"
                    
                    self.sql_statements.append(fk_stmt)
    
    def _drop_table(self, table_name: str, table_schema: Dict):
        """Generate DROP TABLE statement (only called for tables that don't exist in target schema)"""
        self.sql_statements.append(f"DROP TABLE {table_name};")
    
    def _create_table(self, table_name: str, table_schema: Dict):
        """Generate CREATE TABLE statement"""
        columns = table_schema['columns']
        constraints = table_schema.get('constraints', [])

        # Build column definitions
        column_defs = []
        for column in columns:
            column_defs.append(self.get_column_definition(column))

        # Build primary key constraint definitions (only add if columns exist)
        constraint_defs = []
        for constraint_str in constraints:
            constraint = self.parse_constraint(constraint_str)

            if constraint['type'] == 'PRIMARY_KEY' and constraint['columns']:
                constraint_defs.append(f"PRIMARY KEY ({', '.join(constraint['columns'])})")

        # Create the CREATE TABLE statement
        all_defs = column_defs + constraint_defs
        create_stmt = f"CREATE TABLE {table_name} (\n    {', '.join(all_defs)}\n);"
        self.sql_statements.append(create_stmt)
    
    def _modify_table(self, table_name: str, old_schema: Dict, new_schema: Dict):
        """Generate ALTER TABLE statements for table modifications"""
        old_columns = {col['name']: col for col in old_schema['columns']}
        new_columns = {col['name']: col for col in new_schema['columns']}
        
        old_constraints = [self.parse_constraint(c) for c in old_schema.get('constraints', [])]
        new_constraints = [self.parse_constraint(c) for c in new_schema.get('constraints', [])]
        
        # Drop columns that don't exist in new schema
        columns_to_drop = set(old_columns.keys()) - set(new_columns.keys())
        for column_name in sorted(columns_to_drop):
            self.sql_statements.append(f"ALTER TABLE {table_name} DROP COLUMN {column_name};")
        
        # Add new columns
        columns_to_add = set(new_columns.keys()) - set(old_columns.keys())
        for column_name in sorted(columns_to_add):
            column = new_columns[column_name]
            col_def = self.get_column_definition(column)
            self.sql_statements.append(f"ALTER TABLE {table_name} ADD COLUMN {col_def};")
        
        # Modify existing columns
        common_columns = set(old_columns.keys()) & set(new_columns.keys())
        for column_name in sorted(common_columns):
            old_col = old_columns[column_name]
            new_col = new_columns[column_name]
            
            # Check if column definition has changed
            if (old_col['type'] != new_col['type'] or 
                old_col['nullable'] != new_col['nullable'] or
                old_col['default'] != new_col['default']):
                
                col_def = self.get_column_definition(new_col)
                self.sql_statements.append(f"ALTER TABLE {table_name} MODIFY COLUMN {col_def};")
        
        # Handle primary key changes
        old_pk_cols = set()
        new_pk_cols = set()
        
        for constraint in old_constraints:
            if constraint['type'] == 'PRIMARY_KEY':
                old_pk_cols.update(constraint['columns'])
        
        for constraint in new_constraints:
            if constraint['type'] == 'PRIMARY_KEY':
                new_pk_cols.update(constraint['columns'])
        
        if old_pk_cols != new_pk_cols:
            if old_pk_cols:
                self.sql_statements.append(f"ALTER TABLE {table_name} DROP PRIMARY KEY;")
            if new_pk_cols:
                self.sql_statements.append(f"ALTER TABLE {table_name} ADD PRIMARY KEY ({', '.join(sorted(new_pk_cols))});")

def main():
    if len(sys.argv) != 3:
        print("Usage: python schema_diff.py <old_schema.json> <new_schema.json>")
        sys.exit(1)

    old_schema_path = sys.argv[1]
    new_schema_path = sys.argv[2]

    with open(old_schema_path, "r") as f:
        old_schema = json.load(f)

    with open(new_schema_path, "r") as f:
        new_schema = json.load(f)

    # Generate SQL statements
    comparator = SchemaComparator()
    sql_statements = comparator.compare_schemas(old_schema, new_schema)

    output_file = "migration_output.sql"

    with open(output_file, "w") as f:
        f.write("-- SQL statements to transform old_schema to new_schema\n")
        f.write("-- ================================================\n\n")
        
        for i, statement in enumerate(sql_statements, 1):
            f.write(f"-- Statement {i}\n")
            f.write(statement + "\n\n")

        f.write(f"-- Total statements: {len(sql_statements)}\n")

    print(f"✅ SQL migration script written to: {output_file}")


    
if __name__ == "__main__":
    main()