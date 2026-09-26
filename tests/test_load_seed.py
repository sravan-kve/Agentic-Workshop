import csv
import sqlite3
from pathlib import Path

import pytest

import load_seed

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "seed"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "app.db"
    load_seed.load(path, SEED)
    return path


def _csv_rows(name):
    with (SEED / f"{name}.csv").open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _columns(db, table):
    with sqlite3.connect(db) as conn:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_tables_have_the_columns_the_mcp_server_queries(db):
    assert _columns(db, "tickets") == ["ticket_id", "customer_id", "created_at", "text"]
    assert _columns(db, "customers") == ["customer_id", "name", "plan", "open_tickets"]


@pytest.mark.parametrize("table", ["tickets", "customers"])
def test_one_row_per_csv_row_with_matching_values(db, table):
    expected = _csv_rows(table)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        actual = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
    assert len(actual) == len(expected) > 0
    for got, want in zip(actual, expected):
        assert {k: str(v) for k, v in got.items()} == want


def test_open_tickets_is_an_integer(db):
    with sqlite3.connect(db) as conn:
        types = {r[0] for r in conn.execute("SELECT typeof(open_tickets) FROM customers")}
    assert types == {"integer"}


def test_known_rows_are_found(db):
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT customer_id FROM tickets WHERE ticket_id = 'T-1042'").fetchone() == ("C-77",)
        assert conn.execute("SELECT name FROM customers WHERE customer_id = 'C-77'").fetchone() == ("Northwind",)


def test_mcp_server_reads_the_loaded_db(db, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "mcp"))
    import triage_server

    monkeypatch.setattr(triage_server, "DB_PATH", db)
    assert triage_server.get_ticket("T-1042")["customer_id"] == "C-77"
    customer = triage_server.get_customer_history("C-77")
    assert customer["name"] == "Northwind"
    assert "T-1042" in customer["ticket_ids"]


@pytest.mark.parametrize("bad", ["tickets", "customers"])
def test_header_mismatch_fails_before_writing_anything(tmp_path, bad):
    seed = tmp_path / "seed"
    seed.mkdir()
    for name in ("tickets", "customers"):
        text = "id,wrong\n1,x\n" if name == bad else (SEED / f"{name}.csv").read_text(encoding="utf-8")
        (seed / f"{name}.csv").write_text(text, encoding="utf-8")
    path = tmp_path / "app.db"
    with pytest.raises(ValueError, match=f"{bad}.csv"):
        load_seed.load(path, seed)
    assert not path.exists()


def test_seed_files_are_only_read(tmp_path):
    before = {p.name: p.read_bytes() for p in SEED.glob("*.csv")}
    load_seed.load(tmp_path / "app.db", SEED)
    assert {p.name: p.read_bytes() for p in SEED.glob("*.csv")} == before


def _dump(path):
    with sqlite3.connect(path) as conn:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in ("tickets", "customers")}


def test_running_twice_leaves_identical_rows(tmp_path):
    path = tmp_path / "app.db"
    first_counts = load_seed.load(path, SEED)
    first = _dump(path)
    assert load_seed.load(path, SEED) == first_counts
    assert _dump(path) == first
    assert {t: len(rows) for t, rows in first.items()} == {t: len(_csv_rows(t)) for t in first}


def test_main_exits_cleanly_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(load_seed, "DB_PATH", tmp_path / "app.db")
    load_seed.main()
    load_seed.main()
    assert len(_dump(tmp_path / "app.db")["tickets"]) == len(_csv_rows("tickets"))


def test_rerun_replaces_stale_rows(tmp_path):
    path = tmp_path / "app.db"
    load_seed.load(path, SEED)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO tickets VALUES ('T-9999', 'C-77', 'now', 'stale')")
        conn.execute("DELETE FROM customers WHERE customer_id = 'C-77'")
    load_seed.load(path, SEED)
    assert _dump(path)["tickets"] == [tuple(r.values()) for r in _csv_rows("tickets")]
    assert len(_dump(path)["customers"]) == len(_csv_rows("customers"))


def test_failed_rerun_keeps_previous_database(tmp_path):
    path = tmp_path / "app.db"
    load_seed.load(path, SEED)
    before = _dump(path)
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "tickets.csv").write_text((SEED / "tickets.csv").read_text(encoding="utf-8"), encoding="utf-8")
    (seed / "customers.csv").write_text("customer_id,name,plan,open_tickets\nC-1,X,Team,many\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_seed.load(path, seed)
    assert _dump(path) == before
