"""Spec-first tests for multi-book project navigation.

Plan M: a read-only books/projects enumeration surface so Import-as-new books
remain reopenable after a reload or book switch.

- ``PipelineStorage.list_books()`` — abstract contract implemented identically
  by ``SQLiteAdapter`` and ``InMemorySQLiteAdapter``. Returns only existing
  ``book`` columns (``id``, ``series_id``, ``book_number``, ``version``,
  ``position``) in deterministic ``series_id / book_number / position / id``
  order. Read-only and unparameterized (no input, no row mutation).
- ``GET /api/pipeline/books`` — one ``BookProjects`` DTO per persisted book:
  ``{id, series_id, book_number, version, position, projects}`` where
  ``projects`` is that book's owned Plan I ``ProjectSnapshot`` DTOs
  (``{name, book_id, created_ms, size_bytes}``), grouped by exact ``book_id``
  ownership. Orphan/unowned snapshot rows are omitted, never reassigned.
"""

from __future__ import annotations

import json
import tempfile

import pytest
from fastapi.testclient import TestClient

# Python 3.13 removed the stdlib ``audioop`` module; pydub 0.25.1 imports
# ``pyaudioop`` at import time. When absent, inject a minimal no-op shim so the
# pipeline API module graph can be imported for this read-only listing test,
# which never touches audio. No-op when the real module is installed.
try:  # pragma: no cover - environment probe
    import pyaudioop  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - environment probe
    import sys
    import types

    sys.modules["pyaudioop"] = types.ModuleType("pyaudioop")

from app.pipeline.adapter import InMemorySQLiteAdapter, SQLiteAdapter
from app.pipeline.api import get_storage, router


def _insert_book(storage, book_id, series_id, book_number, version, position):
    """Insert a ``book`` row with explicit identity metadata (FK-safe: the
    referenced ``series`` row is created idempotently first)."""
    storage.execute_insert("INSERT OR IGNORE INTO series (id) VALUES (?)", (series_id,))
    storage.execute_insert(
        "INSERT INTO book (id, series_id, book_number, version, position)"
        " VALUES (?, ?, ?, ?, ?)",
        (book_id, series_id, book_number, version, position),
    )


def _seed_snapshot(storage, name, book_id, created_ms):
    """Insert a project_snapshot row directly (payload is opaque here)."""
    storage.create_project_snapshot(
        name, book_id, json.dumps({"seed": name}), created_ms
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def storage():
    """In-memory SQLite adapter with the schema initialised (no spine needed —
    listing only reads ``book`` + ``project_snapshot`` tables)."""
    adapter = InMemorySQLiteAdapter()
    adapter.init_db()
    yield adapter
    adapter.close()


@pytest.fixture()
def client(storage):
    """FastAPI TestClient with the pipeline router and overridden storage."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_storage] = lambda: storage
    return TestClient(app)


# ---------------------------------------------------------------------------
# list_books adapter contract
# ---------------------------------------------------------------------------


class TestListBooksAdapter:
    def test_returns_only_existing_book_columns_in_deterministic_order(self, storage):
        _insert_book(storage, "b2", "s2", 1, 4, 1)
        _insert_book(storage, "b1", "s1", 2, 1, 2)
        _insert_book(storage, "b3", "s1", 1, 7, 3)

        rows = storage.list_books()

        # Series first, then book_number, then position, then id.
        assert [r["id"] for r in rows] == ["b3", "b1", "b2"]
        assert rows[0] == {
            "id": "b3",
            "series_id": "s1",
            "book_number": 1,
            "version": 7,
            "position": 3,
        }
        # Exactly the five existing book columns — no extra/mutated fields.
        assert set(rows[0].keys()) == {
            "id",
            "series_id",
            "book_number",
            "version",
            "position",
        }

    def test_sqlite_and_inmemory_implement_identical_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            sqlite = SQLiteAdapter(f"{tmp}/books.db")
            sqlite.init_db()
            imem = InMemorySQLiteAdapter()
            imem.init_db()
            try:
                for s in (sqlite, imem):
                    _insert_book(s, "b2", "s2", 1, 4, 1)
                    _insert_book(s, "b1", "s1", 2, 1, 2)
                    _insert_book(s, "b3", "s1", 1, 7, 3)
                assert sqlite.list_books() == imem.list_books()
            finally:
                sqlite.close()
                imem.close()

    def test_empty_db_returns_empty_list(self, storage):
        assert storage.list_books() == []


# ---------------------------------------------------------------------------
# GET /api/pipeline/books — BookProjects DTO
# ---------------------------------------------------------------------------


class TestListBooksEndpoint:
    def test_groups_snapshots_by_exact_book_id_ownership(self, client, storage):
        _insert_book(storage, "b1", "s1", 1, 1, 1)
        _insert_book(storage, "b2", "s1", 2, 1, 2)
        _seed_snapshot(storage, "A1", "b1", 3000)
        _seed_snapshot(storage, "A2", "b1", 2000)
        _seed_snapshot(storage, "B1", "b2", 1000)
        # Orphan snapshot whose book_id matches no persisted book — omitted.
        _seed_snapshot(storage, "Orphan", "ghost-book", 500)

        resp = client.get("/api/pipeline/books")
        assert resp.status_code == 200
        books = resp.json()

        # Two persisted books, in deterministic order; orphan never reassigned.
        assert [b["id"] for b in books] == ["b1", "b2"]
        b1, b2 = books

        s1 = len(json.dumps({"seed": "A1"}).encode("utf-8"))
        s2 = len(json.dumps({"seed": "A2"}).encode("utf-8"))
        assert b1["projects"] == [
            {"name": "A1", "book_id": "b1", "created_ms": 3000, "size_bytes": s1},
            {"name": "A2", "book_id": "b1", "created_ms": 2000, "size_bytes": s2},
        ]
        assert b2["projects"] == [
            {"name": "B1", "book_id": "b2", "created_ms": 1000, "size_bytes": s1},
        ]
        # size_bytes is the UTF-8 length of the JSON payload ("{\"seed\": \"A1\"}").
        assert b1["projects"][0]["size_bytes"] == s1

    def test_preserves_metadata_and_version_fidelity(self, client, storage):
        _insert_book(storage, "b1", "series-x", 3, 9, 7)

        resp = client.get("/api/pipeline/books")
        assert resp.status_code == 200
        (book,) = resp.json()
        assert book["id"] == "b1"
        assert book["series_id"] == "series-x"
        assert book["book_number"] == 3
        assert book["version"] == 9
        assert book["position"] == 7

    def test_book_with_no_snapshots_has_empty_projects_array(self, client, storage):
        _insert_book(storage, "b1", "s1", 1, 1, 1)
        resp = client.get("/api/pipeline/books")
        assert resp.status_code == 200
        assert resp.json()[0]["projects"] == []

    def test_empty_database_returns_empty_array(self, client):
        resp = client.get("/api/pipeline/books")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_listing_is_read_only_no_writes_no_active_walk_coordination(
        self, client, storage
    ):
        _insert_book(storage, "b1", "s1", 1, 1, 1)
        _seed_snapshot(storage, "A1", "b1", 1000)

        before_books = len(storage.list_books())
        before_snaps = len(storage.list_project_snapshots())
        before_walks = len(storage.execute_query("SELECT 1 FROM walk_run"))

        for _ in range(3):
            resp = client.get("/api/pipeline/books")
            assert resp.status_code == 200

        # No rows are created/mutated by listing — books, snapshots, or walks.
        assert len(storage.list_books()) == before_books
        assert len(storage.list_project_snapshots()) == before_snaps
        assert len(storage.execute_query("SELECT 1 FROM walk_run")) == before_walks
