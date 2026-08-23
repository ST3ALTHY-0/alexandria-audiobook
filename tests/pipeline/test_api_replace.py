"""API-level tests for POST /api/pipeline/replace (Plan T Phase 4 P4-S5).

Exercises the endpoint via FastAPI ``TestClient`` with the storage dependency
overridden by the in-memory adapter (``dependency_overrides[get_storage]``)
and ``extract_epub_text`` mocked (no real EPUB).  Covers:

- unknown book -> 404
- invalid (non-EPUB) file -> 400
- active walk on the SAME book -> 503 + Retry-After: 5
- active walk on ANOTHER book -> 503 + Retry-After: 5
- successful Import-as-new (/onboard) unchanged
- unchanged /reonboard
- Retry-After / status-precedence contracts (404-before-503, 400-before-404)

Extraction-before-mutation: a failed extraction maps to HTTP 400 and the DB is
not mutated (P4-S2(a)).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.pipeline.adapter import InMemorySQLiteAdapter
from app.pipeline.api_onboard import get_storage, router
from app.pipeline.populate import (
    _ensure_paragraph_text_column,
    _ensure_span_text_column,
    _insert_chapters_with_placeholders,
)

_OLD = [
    {
        "id": "ch-old-1",
        "paragraphs": [
            {
                "id": "p-old-1",
                "spans": [
                    {"id": "s-old-1", "span_type": "sentence", "text": "Old text."}
                ],
            }
        ],
    }
]

_NEW = [
    {
        "id": "ch-new-1",
        "paragraphs": [
            {
                "id": "p-new-1",
                "spans": [
                    {"id": "s-new-1", "span_type": "sentence", "text": "New text."}
                ],
            }
        ],
    }
]


def _seed_book(storage: InMemorySQLiteAdapter, book_id: str = "b1") -> None:
    """Insert a series + book + a minimal document spine."""
    storage.execute_insert("INSERT INTO series (id) VALUES ('s1')")
    storage.execute_insert(
        "INSERT INTO book (id, series_id, book_number, version, position) "
        "VALUES (?, 's1', 1, 1, 1)",
        (book_id,),
    )
    _ensure_paragraph_text_column(storage)
    _ensure_span_text_column(storage)
    _insert_chapters_with_placeholders(book_id, _OLD, storage)


def _insert_walk_run(storage: InMemorySQLiteAdapter, run_id: str, book_id: str) -> None:
    storage.execute_insert(
        "INSERT INTO walk_run (run_id, book_id, walk_name, status, created_ms) "
        "VALUES (?, ?, 'walk_2b_character_discovery', 'running', 1000)",
        (run_id, book_id),
    )


@pytest.fixture
def storage():
    s = InMemorySQLiteAdapter()
    s.init_db()
    _seed_book(s)
    return s


@pytest.fixture
def client(storage):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_storage] = lambda: storage
    return TestClient(app)


def _post_replace(client, book_id="b1", filename="test.epub"):
    return client.post(
        "/api/pipeline/replace",
        data={"book_id": book_id},
        files={"file": (filename, b"fake epub", "application/epub+zip")},
    )


# ---------------------------------------------------------------------------
# P4-S5: error / contention / precedence contracts
# ---------------------------------------------------------------------------


class TestReplaceStatusPrecedence:
    def test_invalid_epub_returns_400_before_book_check(self, client):
        """Non-EPUB file -> 400 even for a legitimately existing book."""
        resp = client.post(
            "/api/pipeline/replace",
            data={"book_id": "b1"},
            files={"file": ("test.txt", b"nope", "text/plain")},
        )
        assert resp.status_code == 400
        assert "must be an EPUB" in resp.json()["detail"]

    def test_unknown_book_returns_404(self, client):
        """Unknown book -> 404 (checked before extraction / active-walk)."""
        resp = _post_replace(client, book_id="ghost")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_active_walk_same_book_returns_503(self, client, storage):
        """An active walk on the SAME book -> 503 + Retry-After: 5."""
        _insert_walk_run(storage, "run-same", "b1")
        with patch(
            "app.pipeline.api_onboard.extract_epub_text",
            return_value={"chapters": _NEW},
        ):
            resp = _post_replace(client, book_id="b1")
        assert resp.status_code == 503
        assert resp.headers["retry-after"] == "5"

    def test_active_walk_other_book_returns_503(self, client, storage):
        """An active walk on ANOTHER book -> 503 + Retry-After: 5 (global guard)."""
        _insert_walk_run(storage, "run-other", "b-other")
        with patch(
            "app.pipeline.api_onboard.extract_epub_text",
            return_value={"chapters": _NEW},
        ):
            resp = _post_replace(client, book_id="b1")
        assert resp.status_code == 503
        assert resp.headers["retry-after"] == "5"

    def test_404_before_503_precedence(self, client, storage):
        """Unknown book wins over an active walk (mirrors /reonboard)."""
        _insert_walk_run(storage, "run-x", "b-other")
        with patch(
            "app.pipeline.api_onboard.extract_epub_text",
            return_value={"chapters": _NEW},
        ):
            resp = _post_replace(client, book_id="ghost")
        assert resp.status_code == 404
        assert "retry-after" not in resp.headers


# ---------------------------------------------------------------------------
# P4-S2(a): extraction-before-mutation -> 400, DB untouched
# ---------------------------------------------------------------------------


class TestReplaceExtractionFailure:
    def test_extraction_failure_does_not_mutate_db(self, client, storage):
        """A failed extraction maps to 400 and leaves the tree, version, and
        generated outputs untouched."""
        with patch(
            "app.pipeline.api_onboard.extract_epub_text",
            side_effect=Exception("bad epub"),
        ):
            resp = _post_replace(client, book_id="b1")
        assert resp.status_code == 400
        assert "Failed to extract" in resp.json()["detail"]
        # DB untouched.
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM span WHERE id = 's-old-1'"
            )[0]["cnt"]
            == 1
        )
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM span WHERE id = 's-new-1'"
            )[0]["cnt"]
            == 0
        )
        assert (
            storage.execute_query(
                "SELECT version, position, book_number FROM book WHERE id = 'b1'"
            )[0]["version"]
            == 1
        )


# ---------------------------------------------------------------------------
# P4-S5: successful replace, unchanged /onboard, unchanged /reonboard
# ---------------------------------------------------------------------------


class TestReplaceSuccessAndUnchangedEndpoints:
    def test_successful_replace_returns_replaced_status(
        self, client, storage, monkeypatch
    ):
        """A successful replace returns 200 with status 'replaced', bumps
        version, retains identity, and swaps the spine."""
        monkeypatch.setenv("RENDER_ROOT", "/tmp/task-p4-render-root-b1")
        with patch(
            "app.pipeline.api_onboard.extract_epub_text",
            return_value={"chapters": _NEW},
        ):
            resp = _post_replace(client, book_id="b1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "replaced"
        assert data["book_id"] == "b1"
        assert data["version"] == 2
        assert data["chapters"] == 1
        row = storage.execute_query(
            "SELECT series_id, book_number, position, version FROM book WHERE id = 'b1'"
        )[0]
        assert row["series_id"] == "s1"
        assert row["book_number"] == 1
        assert row["position"] == 1
        assert row["version"] == 2
        # Old spine gone, new spine present.
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM span WHERE id = 's-old-1'"
            )[0]["cnt"]
            == 0
        )
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM span WHERE id = 's-new-1'"
            )[0]["cnt"]
            == 1
        )

    def test_onboard_import_as_new_unchanged(self, client):
        """Import-as-new (/onboard) still returns the standard new-book shape."""
        with (
            patch("app.pipeline.api_onboard.extract_epub_text") as mock_extract,
            patch("app.pipeline.api_onboard.populate_spine") as mock_populate,
        ):
            mock_extract.return_value = {
                "series_id": "s-new",
                "book_id": "b-new",
                "chapters": [{"id": "ch1", "title": "Chapter 1"}],
            }
            resp = client.post(
                "/api/pipeline/onboard",
                files={"file": ("new.epub", b"fake", "application/epub+zip")},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["book_id"], str) and data["book_id"]
        assert data["series_id"] == "s-new"
        assert data["chapters"] == 1
        mock_extract.assert_called_once()
        mock_populate.assert_called_once()

    def test_reonboard_unchanged(self, client, storage):
        """Fileless /reonboard still resets output and bumps version."""
        resp = client.post("/api/pipeline/reonboard", json={"book_id": "b1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["book_id"] == "b1"
        assert data["status"] == "reonboarded"
        assert (
            storage.execute_query("SELECT version FROM book WHERE id = 'b1'")[0][
                "version"
            ]
            == 2
        )

    def test_reonboard_404_before_503(self, client, storage):
        """Unknown book at /reonboard -> 404 even with an active walk."""
        _insert_walk_run(storage, "run-y", "b-other")
        resp = client.post("/api/pipeline/reonboard", json={"book_id": "ghost"})
        assert resp.status_code == 404
        assert "retry-after" not in resp.headers
