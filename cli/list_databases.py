import json
import sys

import sqlalchemy

SYSTEM_SCHEMAS = {"information_schema", "mysql", "performance_schema", "sys"}


def list_databases(host: str, port: int, user: str, password: str) -> list:
    """Connect without selecting a database and list what's available -- used by
    onboarding, before any specific DB has been chosen to track."""
    url = f"mysql+mysqlconnector://{user}:{password}@{host}:{port}/"
    engine = sqlalchemy.create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(sqlalchemy.text("SHOW DATABASES"))
        names = [row[0] for row in rows]
    return [n for n in names if n not in SYSTEM_SCHEMAS]


def main():
    """Reads connection info as JSON from stdin (not argv, so credentials never
    show up in `ps`), writes {"databases": [...]} or {"error": "..."} to stdout."""
    try:
        payload = json.loads(sys.stdin.read())
        databases = list_databases(
            payload["host"], int(payload.get("port", 3306)), payload["user"], payload.get("password", "")
        )
        print(json.dumps({"databases": databases}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
