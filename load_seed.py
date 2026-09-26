"""Load seed/tickets.csv and seed/customers.csv into app.db (SQLite)."""

import csv
import sqlite3
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEED_DIR = ROOT / "seed"
DB_PATH = ROOT / "app.db"

# Column names are fixed by mcp/triage_server.py.
TABLES = {
    "tickets": ("ticket_id", "customer_id", "created_at", "text"),
    "customers": ("customer_id", "name", "plan", "open_tickets"),
}
INTEGER_COLUMNS = {"open_tickets"}


def _read_rows(table: str, columns: tuple[str, ...], seed_dir: Path) -> list[tuple]:
    path = seed_dir / f"{table}.csv"
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != columns:
            raise ValueError(f"{path.name}: expected columns {list(columns)}, found {reader.fieldnames}")
        return [tuple(int(row[c]) if c in INTEGER_COLUMNS else row[c] for c in columns) for row in reader]


def load(db_path: Path = DB_PATH, seed_dir: Path = SEED_DIR) -> dict[str, int]:
    """Rebuild the tables in db_path from the seed CSVs; return the row count per table.

    Existing tables are dropped in the same transaction, so a re-run gives the same
    rows and a failed load leaves the previous database untouched.
    """
    data = {table: _read_rows(table, columns, seed_dir) for table, columns in TABLES.items()}
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("BEGIN")
        for table, columns in TABLES.items():
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            defs = ", ".join(f"{c} INTEGER" if c in INTEGER_COLUMNS else f"{c} TEXT" for c in columns)
            conn.execute(f"CREATE TABLE {table} ({defs})")
            marks = ", ".join("?" for _ in columns)
            conn.executemany(f"INSERT INTO {table} VALUES ({marks})", data[table])
    return {table: len(rows) for table, rows in data.items()}


def main() -> None:
    counts = load(DB_PATH, SEED_DIR)
    print(f"Loaded {counts['tickets']} tickets and {counts['customers']} customers into {DB_PATH.name}")


if __name__ == "__main__":
    main()
