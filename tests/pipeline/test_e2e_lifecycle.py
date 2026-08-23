"""End-to-end onboarding lifecycle integration test (Plan T, P5-S2).

Drives the FULL documented onboarding lifecycle through the real HTTP API
(FastAPI ``TestClient``, in-memory adapter, ``extract_epub_text`` mocked):

    1. **Import-as-new**   -> ``POST /api/pipeline/onboard`` creates a fresh
                             book in its own series (the non-destructive path).
    2. **subsequent walks**-> walk-generated output (walk_run, character_book,
                             character_span, render job) lands on a book.
    3. **Replace**         -> ``POST /api/pipeline/replace`` swaps that book's
                             document tree, clears its generated output, bumps
                             the version, and RETAINS its identity/ordering.
    4. **walks again**     -> walk output lands on the replaced book again.
    5. **fileless Re-onboard** -> ``POST /api/pipeline/reonboard`` clears the
                             generated output again and bumps the version.

Invariants asserted at every destructive step:
    * NO cross-book deletion  — a sibling book in the same series keeps its
      tree, characters, memberships, walk output, render rows, and filesystem
      render directory untouched when the main book is replaced/re-onboarded.
    * NO identity drift        — book id, series id, book number, and position
      are byte-identical before and after Replace and Re-onboard.

Run with the project venv (system python3.13 lacks ``audioop``):

    .venv/bin/python -m pytest tests/pipeline/test_e2e_lifecycle.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.pipeline.adapter import InMemorySQLiteAdapter
from app.pipeline.api_onboard import get_storage, router
from app.pipeline.assembly import export_annotated_script
from app.pipeline.populate import (
    _ensure_paragraph_text_column,
    _ensure_span_text_column,
    _insert_chapters_with_placeholders,
)

# ---------------------------------------------------------------------------
# Spine shapes (chapter dicts mirror replace/populate expectations)
# ---------------------------------------------------------------------------

_MAIN_BOOK = [
    {
        "id": "ch-main-1",
        "paragraphs": [
            {
                "id": "p-main-1",
                "spans": [
                    {
                        "id": "s-main-1",
                        "span_type": "sentence",
                        "text": "Main text one.",
                    }
                ],
            }
        ],
    },
    {
        "id": "ch-main-2",
        "paragraphs": [
            {
                "id": "p-main-2",
                "spans": [
                    {
                        "id": "s-main-2",
                        "span_type": "quotation",
                        "text": "Main text two.",
                    }
                ],
            }
        ],
    },
]

_NEW_BOOK = [
    {
        "id": "ch-new-1",
        "paragraphs": [
            {
                "id": "p-new-1",
                "spans": [
                    {
                        "id": "s-new-1",
                        "span_type": "sentence",
                        "text": "Brand new replacement text.",
                    }
                ],
            }
        ],
    }
]


def _relabel(chapters: list[dict], prefix: str) -> list[dict]:
    """Deep-copy ``chapters`` relabeling every id with ``prefix`` so the sibling
    book can coexist in the global PK namespace (chapter/paragraph/span id)."""
    out = []
    for i, ch in enumerate(chapters, 1):
        paragraphs = []
        for j, p in enumerate(ch["paragraphs"], 1):
            spans = [
                {
                    "id": f"{prefix}-s{i}-{j}-{k}",
                    "span_type": sp["span_type"],
                    "text": sp["text"],
                }
                for k, sp in enumerate(p["spans"], 1)
            ]
            paragraphs.append({"id": f"{prefix}-p{i}-{j}", "spans": spans})
        out.append({"id": f"{prefix}-ch{i}", "paragraphs": paragraphs})
    return out


# ---------------------------------------------------------------------------
# Seeding / simulation helpers
# ---------------------------------------------------------------------------


def _insert_series_book(
    storage: InMemorySQLiteAdapter,
    book_id: str,
    series_id: str = "s1",
    book_number: int = 1,
    position: int = 1,
    version: int = 1,
) -> None:
    """Insert a ``series`` (if absent) + a retained ``book`` row."""
    if not storage.execute_query("SELECT id FROM series WHERE id = ?", (series_id,)):
        storage.execute_insert("INSERT INTO series (id) VALUES (?)", (series_id,))
    storage.execute_insert(
        "INSERT INTO book (id, series_id, book_number, version, position) "
        "VALUES (?, ?, ?, ?, ?)",
        (book_id, series_id, book_number, version, position),
    )


def _insert_spine(
    storage: InMemorySQLiteAdapter, book_id: str, chapters: list[dict]
) -> None:
    """Build the document spine with the same populate helpers the pipeline uses."""
    _ensure_paragraph_text_column(storage)
    _ensure_span_text_column(storage)
    _insert_chapters_with_placeholders(book_id, chapters, storage)


def _identify(storage: InMemorySQLiteAdapter, book_id: str) -> dict:
    """Snapshot a book's identity/ordering fields (drift detector)."""
    return storage.execute_query(
        "SELECT id, series_id, book_number, position, version FROM book WHERE id = ?",
        (book_id,),
    )[0]


def _simulate_walk(
    storage: InMemorySQLiteAdapter,
    book_id: str,
    character_id: str,
    span_id: str,
) -> None:
    """Model a completed walk leaving generated output for *book_id*: a walk_run,
    a character membership, a span speaker junction, and a render job/chunk."""
    # A character row the walk created (characters are global and persist —
    # re-run the walk and the character already exists).
    if not storage.execute_query(
        "SELECT id FROM character WHERE id = ?", (character_id,)
    ):
        storage.execute_insert(
            "INSERT INTO character (id, name, aliases) VALUES (?, ?, '[]')",
            (character_id, character_id),
        )
    storage.execute_insert(
        "INSERT INTO walk_run (run_id, book_id, walk_name, status, created_ms) "
        "VALUES (?, ?, 'walk_2b_character_discovery', 'completed', 1)",
        (f"run-{book_id}", book_id),
    )
    storage.execute_insert(
        "INSERT INTO character_book (character_id, book_id, source, confidence) "
        "VALUES (?, ?, 'walk', 0.9)",
        (character_id, book_id),
    )
    storage.execute_insert(
        "INSERT INTO character_span (character_id, span_id, relation_type, source, "
        "confidence) VALUES (?, ?, 'speaker', 'walk', 0.9)",
        (character_id, span_id),
    )
    storage.execute_insert(
        "INSERT INTO render_job (job_id, book_id, mode, status, created_ms) "
        "VALUES (?, ?, 'batch', 'completed', 1)",
        (f"job-{book_id}", book_id),
    )
    storage.execute_insert(
        "INSERT INTO render_chunk (job_id, idx, status, wav_path) "
        "VALUES (?, 0, 'done', 'x.wav')",
        (f"job-{book_id}",),
    )


def _count(storage: InMemorySQLiteAdapter, table: str, book_id: str) -> int:
    """Count rows of *table* keyed by book_id for the given book."""
    return storage.execute_query(
        f"SELECT COUNT(*) AS cnt FROM {table} WHERE book_id = ?", (book_id,)
    )[0]["cnt"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def render_root(tmp_path, monkeypatch):
    """Point RENDER_ROOT at a throwaway directory (never the real data/)."""
    monkeypatch.setenv("RENDER_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def storage():
    """An in-memory adapter with schema initialised."""
    s = InMemorySQLiteAdapter()
    s.init_db()
    return s


@pytest.fixture
def client(storage):
    """A TestClient wired to the onboard router with the in-memory storage."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_storage] = lambda: storage
    return TestClient(app)


def _post_replace(client, book_id="book-main", filename="new.epub"):
    return client.post(
        "/api/pipeline/replace",
        data={"book_id": book_id},
        files={"file": (filename, b"fake epub", "application/epub+zip")},
    )


# ---------------------------------------------------------------------------
# The onboarding lifecycle end-to-end
# ---------------------------------------------------------------------------


class TestOnboardingLifecycle:
    def test_import_replace_walks_reonboard_lifecycle(
        self, client, storage, render_root
    ):
        # ---- Setup: main book A + sibling B in the SAME series, each already
        # ---- Import-as-new'd (populated spine) and with walk output.
        _insert_series_book(
            storage, "book-main", series_id="s1", book_number=2, position=3
        )
        _insert_series_book(
            storage, "book-sib", series_id="s1", book_number=1, position=1
        )
        _insert_spine(storage, "book-main", _MAIN_BOOK)
        _insert_spine(storage, "book-sib", _relabel(_MAIN_BOOK, "sib"))

        _simulate_walk(storage, "book-main", "c-main", "s-main-1")
        _simulate_walk(storage, "book-sib", "c-sib", "sib-s1-1-1")

        # Pre-walk render dirs on disk for both books.
        for book in ("book-main", "book-sib"):
            d = render_root / f"book-{book}"
            d.mkdir()
            (d / "manifest.json").write_text("{}")

        main_before = _identify(storage, "book-main")
        sib_before = _identify(storage, "book-sib")

        # ===================================================================
        # 1. Import-as-new: POST /onboard creates a genuinely NEW book in a
        #    fresh series (the non-destructive path) — it must not disturb A/B.
        # ===================================================================
        with patch(
            "app.pipeline.api_onboard.extract_epub_text",
            return_value={
                "series_id": "s-import",
                "book_id": "book-import",
                "chapters": [{"id": "ch2", "paragraphs": []}],
            },
        ):
            onboard_resp = client.post(
                "/api/pipeline/onboard",
                files={"file": ("import.epub", b"fake", "application/epub+zip")},
            )
        assert onboard_resp.status_code == 200
        # The new book was created in its own series; A/B are untouched.
        import_book = storage.execute_query(
            "SELECT id, series_id, book_number, position, version "
            "FROM book WHERE id = 'book-import'"
        )[0]
        assert import_book["series_id"] == "s-import"
        assert _identify(storage, "book-main") == main_before
        assert _identify(storage, "book-sib") == sib_before

        # ===================================================================
        # 2. Replace book-main: identity retained, version bumped, its own
        #    generated output cleared, sibling + import books untouched.
        # ===================================================================
        with patch(
            "app.pipeline.api_onboard.extract_epub_text",
            return_value={"chapters": _NEW_BOOK},
        ):
            resp = _post_replace(client, book_id="book-main")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "replaced"
        assert data["book_id"] == "book-main"
        assert data["version"] == 2
        assert data["chapters"] == 1

        # Identity/ordering drift check on the replaced book.
        main_after = _identify(storage, "book-main")
        assert main_after["id"] == main_before["id"]
        assert main_after["series_id"] == main_before["series_id"]
        assert main_after["book_number"] == main_before["book_number"]
        assert main_after["position"] == main_before["position"]
        assert main_after["version"] == 2

        # doc tree swapped: the replaced book's export shows only the new text.
        texts = [e["text"] for e in export_annotated_script("book-main", storage)]
        assert texts == ["Brand new replacement text."]

        # book-main's generated output cleared.
        assert _count(storage, "walk_run", "book-main") == 0
        assert _count(storage, "character_book", "book-main") == 0
        assert _count(storage, "render_job", "book-main") == 0
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM render_chunk WHERE job_id = 'job-book-main'"
            )[0]["cnt"]
            == 0
        )
        # its render dir removed from disk.
        assert not (render_root / "book-book-main").exists()

        # NO cross-book deletion: sibling keeps tree, character, memberships,
        # walk output, render rows, and its render dir.
        sib_after = _identify(storage, "book-sib")
        assert sib_after == sib_before  # byte-identical, even version unchanged
        sib_texts = [e["text"] for e in export_annotated_script("book-sib", storage)]
        assert len(sib_texts) == 2
        assert _count(storage, "character_book", "book-sib") == 1
        assert _count(storage, "walk_run", "book-sib") == 1
        assert _count(storage, "render_job", "book-sib") == 1
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM render_chunk WHERE job_id = 'job-book-sib'"
            )[0]["cnt"]
            == 1
        )
        assert (render_root / "book-book-sib" / "manifest.json").exists()
        # Import-as-new book untouched.
        assert _identify(storage, "book-import") == import_book
        assert _identify(storage, "book-main") == main_after  # stable still

        # ===================================================================
        # 3. Subsequent walks: output lands on the replaced book again.
        # ===================================================================
        _simulate_walk(storage, "book-main", "c-main", "s-new-1")
        assert _count(storage, "walk_run", "book-main") == 1
        assert _count(storage, "character_book", "book-main") == 1

        # ===================================================================
        # 4. Fileless Re-onboard: clears book-main's generated output again and
        #    bumps the version; identity + sibling/import books untouched.
        # ===================================================================
        resp = client.post("/api/pipeline/reonboard", json={"book_id": "book-main"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "reonboarded"
        assert resp.json()["version"] == 3

        after = _identify(storage, "book-main")
        assert after["id"] == main_before["id"]
        assert after["series_id"] == main_before["series_id"]
        assert after["book_number"] == main_before["book_number"]
        assert after["position"] == main_before["position"]
        assert after["version"] == 3

        # Re-onboard reset the freshly-generated output (memberships + span
        # junctions + scene presence), while PRESERVING the run ledger and
        # render rows — this is the documented generated-output reset, distinct
        # from Replace (which clears the whole run/render surface).
        assert _count(storage, "character_book", "book-main") == 0
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM character_span WHERE span_id = 's-new-1'"
            )[0]["cnt"]
            == 0
        )
        # Run ledger + render rows preserved by Re-onboard (not cleared).
        assert _count(storage, "walk_run", "book-main") == 1
        assert _count(storage, "render_job", "book-main") == 1
        # Sibling and imported books untouched.
        assert _identify(storage, "book-sib") == sib_before
        assert _identify(storage, "book-import") == import_book
