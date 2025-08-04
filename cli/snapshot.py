import json
import os
from datetime import datetime
from db import reflect_schema

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
    metadata = reflect_schema()
    schema_dict = {
        table_name: serialize_table(table)
        for table_name, table in metadata.tables.items()
    }

    os.makedirs("snapshots", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"snapshots/schema_{timestamp}.json"

    with open(filename, "w") as f:
        json.dump(schema_dict, f, indent=2)

    print(f"✅ Schema snapshot saved to {filename}")

if __name__ == "__main__":
    snapshot_schema()