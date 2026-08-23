"""Assembly-level tests for ``replace_book_tree`` (Plan T Phase 4, P4-S1..S4).

Uses the in-memory SQLite adapter and injects the exact populate helpers that
``replace_book_tree`` itself calls so seeded and replaced spines are identical
in shape.

- P4-S1: Replace swaps chapter/paragraph/span *content* while the book id,
  series id, book number, and position stay byte-identical and the version is
  bumped.
- P4-S2: a population failure after the old tree is staged rolls the whole
  replacement back (original tree / version / generated outputs / render rows
  unchanged).
- P4-S3: replacing one book in a series leaves a sibling book's tree,
  memberships, shared character metadata/voice, render rows, chunks, and
  filesystem output untouched (no cross-book deletion).
- P4-S4: every book-owned generated table (workbench decision/provenance/
  generation, scene projections, alias merge, boundary override, walk review/
  override/run/undo, prompt-config revision, project snapshot, render
  job/chunk, persona revision, character_book) is cleared, a global NULL-book_id
  persona and shared voice_config/clone_reference survive, and a missing render
  directory (or one missing derived files) is tolerated.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.pipeline.adapter import InMemorySQLiteAdapter
from app.pipeline.assembly import get_book_version, replace_book_tree
from app.pipeline.populate import (
    _ensure_paragraph_text_column,
    _ensure_span_text_column,
    _insert_chapters_with_placeholders,
)


@pytest.fixture
def render_root(tmp_path, monkeypatch):
    """Point RENDER_ROOT at a throwaway directory (never the real data/)."""
    monkeypatch.setenv("RENDER_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def storage():
    """A bare (empty) in-memory adapter; tests seed their own books."""
    s = InMemorySQLiteAdapter()
    s.init_db()
    return s


# ---------------------------------------------------------------------------
# seeding helpers
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
    """Build the document spine the same way Replace does (populate helpers)."""
    _ensure_paragraph_text_column(storage)
    _ensure_span_text_column(storage)
    _insert_chapters_with_placeholders(book_id, chapters, storage)


def _relabel(chapters: list[dict], prefix: str) -> list[dict]:
    """Deep-copy ``chapters`` relabeling every id with ``prefix`` so sibling
    books can coexist in the global PK namespace (chapter/paragraph/span id)."""
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


def _scene_for_chapter(storage: InMemorySQLiteAdapter, chapter_id: str) -> str:
    """Return the (first) scene id linked to a chapter via chapter_scene."""
    rows = storage.execute_query(
        "SELECT child_id FROM chapter_scene WHERE parent_id = ? ORDER BY position",
        (chapter_id,),
    )
    assert rows, f"no scene for chapter {chapter_id!r}"
    return rows[0]["child_id"]


def _seed_characters(
    storage: InMemorySQLiteAdapter, *char_ids: str, voice_id: str = "vc1"
) -> str:
    """Seed voice_config + character rows; returns the first character id."""
    storage.execute_insert(
        "INSERT INTO voice_config (id, name) VALUES (?, 'Warm Female')", (voice_id,)
    )
    first = None
    for cid in char_ids:
        if first is None:
            first = cid
        storage.execute_insert(
            "INSERT INTO character (id, name, aliases) VALUES (?, ?, '[]')", (cid, cid)
        )
    return first


def _link_scene_character(
    storage: InMemorySQLiteAdapter, character_id: str, scene_id: str
) -> None:
    """Insert a Graph2 character_scene junction (no book_id column)."""
    storage.execute_insert(
        "INSERT INTO character_scene (character_id, scene_id, relation_type, "
        "source, confidence) VALUES (?, ?, 'present', 'walk', 0.9)",
        (character_id, scene_id),
    )


def _seed_full_surface(
    storage: InMemorySQLiteAdapter,
    book_id: str,
    scene_ids: list[str],
    character_ids: list[str],
) -> None:
    """Insert every book-owned generated concern for *book_id*.

    Mirrors the ownership ALLOWLIST: workbench decision/provenance/generation,
    generated/manual/absence presence, boundary override, alias merge,
    walk review/override, walk run + undo journal, prompt-config revision,
    project snapshot, and render job/chunk rows.  (persona_revision and the
    global NULL-book_id persona are added by the caller where relevant.)
    """
    c = character_ids[0]
    c2 = character_ids[1] if len(character_ids) > 1 else character_ids[0]
    sc = scene_ids[0] if scene_ids else None
    run_id = f"run-{book_id}"
    decision_id = f"d-{book_id}"

    # -- walk run + undo journal ------------------------------------------
    storage.execute_insert(
        "INSERT INTO walk_run (run_id, book_id, walk_name, status, created_ms) "
        "VALUES (?, ?, 'walk_2b_character_discovery', 'completed', 1)",
        (run_id, book_id),
    )
    storage.execute_insert(
        "INSERT INTO walk_undo_entry (run_id, seq, table_name, op, row_pk, "
        "before_json, after_json, created_ms) "
        "VALUES (?, 1, 'character_span', 'insert', 'x', NULL, '{}', 1)",
        (run_id,),
    )

    # -- workbench decision (needed by absence/alias/boundary/manual FK refs) --
    storage.execute_insert(
        "INSERT INTO workbench_decision (decision_id, book_id, target_kind, "
        "target_key, decision_type, base_revision, payload_json, status, "
        "source, created_ms) VALUES (?, ?, 'presence', ?, 'set', 0, '{}', "
        "'active', 'human', 1)",
        (decision_id, book_id, f"{sc}:{c}"),
    )
    storage.execute_insert(
        "INSERT INTO workbench_generation (generation_id, book_id, revision, "
        "updated_ms) VALUES (?, ?, 0, 1)",
        (f"wg-{book_id}", book_id),
    )
    storage.execute_insert(
        "INSERT INTO workbench_provenance (provenance_id, book_id, target_kind, "
        "target_key, generation_revision, source, created_ms) VALUES "
        "(?, ?, 'presence', ?, 0, 'walk', 1)",
        (f"wp-{book_id}", book_id, f"{sc}:{c}"),
    )

    # -- scene-bound projections / overrides --------------------------------
    if sc is not None:
        storage.execute_insert(
            "INSERT INTO character_scene_absence (book_id, scene_id, character_id, "
            "decision_id, active, created_ms) VALUES (?, ?, ?, ?, 1, 1)",
            (book_id, sc, c, decision_id),
        )
        storage.execute_insert(
            "INSERT INTO character_scene_generated (id, book_id, character_id, "
            "scene_id, relation_type, confidence, generation_revision, "
            "source_run_id) VALUES (?, ?, ?, ?, 'present', 0.9, 0, ?)",
            (f"g-{book_id}", book_id, c, sc, run_id),
        )
        storage.execute_insert(
            "INSERT INTO character_scene_manual (id, book_id, character_id, "
            "scene_id, relation_type, decision_id) VALUES "
            "(?, ?, ?, ?, 'present', ?)",
            (f"m-{book_id}", book_id, c, sc, decision_id),
        )
        storage.execute_insert(
            "INSERT INTO boundary_override (override_id, book_id, scene_id, "
            "decision_id, payload_json, active, created_ms) VALUES "
            "(?, ?, ?, ?, '{}', 1, 1)",
            (f"o-{book_id}", book_id, sc, decision_id),
        )
        storage.execute_insert(
            "INSERT INTO character_alias_merge (merge_id, book_id, canonical_id, "
            "member_id, merge_revision, decision_id, status, prior_member_name, "
            "prior_member_aliases_json, prior_member_voice_assignment_id, "
            "consequence_json, created_ms) VALUES (?, ?, ?, ?, 0, ?, 'active', "
            "'Old Name', '[]', NULL, '{}', 1)",
            (f"al-{book_id}", book_id, c2, c, decision_id),
        )

    # -- walk review + override --------------------------------------------
    storage.execute_insert(
        "INSERT INTO walk_review_item (id, book_id, run_id, kind, target_table, "
        "target_id, prior_value, status, created_ms) VALUES "
        "(?, ?, ?, 'voice_profile', 'character_metadata', ?, NULL, 'pending', 1)",
        (f"wi-{book_id}", book_id, run_id, c),
    )
    storage.execute_insert(
        "INSERT INTO walk_override (book_id, walk_name, key, value_json) "
        "VALUES (?, 'walk_2a_scene_segmentation', 'k', '{}')",
        (book_id,),
    )

    # -- prompt-config revision + project snapshot --------------------------
    storage.execute_insert(
        "INSERT INTO prompt_config_revision (revision_id, book_id, task, "
        "source_layers_json, settings_json, validation_json, author_id, "
        "created_ms) VALUES (?, ?, 'scene', '{}', '{}', '{}', 'local', 1)",
        (f"pr-{book_id}", book_id),
    )
    storage.execute_insert(
        "INSERT INTO project_snapshot (name, book_id, snapshot_json, created_ms) "
        "VALUES (?, ?, '{}', 1)",
        (f"ps-{book_id}", book_id),
    )

    # -- render job + chunk -------------------------------------------------
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


OLD_BOOK = [
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
    },
    {
        "id": "ch-old-2",
        "paragraphs": [
            {
                "id": "p-old-2",
                "spans": [
                    {"id": "s-old-2", "span_type": "quotation", "text": "Bye old."}
                ],
            }
        ],
    },
]

NEW_BOOK = [
    {
        "id": "ch-new-1",
        "paragraphs": [
            {
                "id": "p-new-1",
                "spans": [
                    {
                        "id": "s-new-1",
                        "span_type": "sentence",
                        "text": "Brand new chapter text.",
                    }
                ],
            }
        ],
    }
]


def _count(storage: InMemorySQLiteAdapter, table: str, ids: list[str]) -> int:
    """Count rows of *table* whose first column matches any of *ids*."""
    if not ids:
        return 0
    ph = ",".join("?" for _ in ids)
    rows = storage.execute_query(
        f"SELECT COUNT(*) AS cnt FROM {table} WHERE id IN ({ph})", tuple(ids)
    )
    return rows[0]["cnt"]


# ---------------------------------------------------------------------------
# P4-S1: content replacement while identity/ordering is retained
# ---------------------------------------------------------------------------


class TestReplaceChangesContent:
    def test_swaps_spine_content_but_retains_identity(self, storage):
        """Old chapter/paragraph/span ids/text are gone; new ones present;
        book identity/ordering byte-identical; version bumped."""
        _insert_series_book(
            storage, "b1", series_id="s1", book_number=7, position=3, version=2
        )
        _insert_spine(storage, "b1", OLD_BOOK)

        outcome = replace_book_tree("b1", NEW_BOOK, storage)

        # Retained identity / ordering / bumped version.
        assert outcome["book_id"] == "b1"
        assert outcome["series_id"] == "s1"
        assert outcome["book_number"] == 7
        assert outcome["position"] == 3
        assert outcome["version"] == 3
        assert outcome["chapters"] == 1
        row = storage.execute_query(
            "SELECT id, series_id, book_number, position, version FROM book WHERE id = 'b1'"
        )[0]
        assert row == {
            "id": "b1",
            "series_id": "s1",
            "book_number": 7,
            "position": 3,
            "version": 3,
        }

        # Old spine fully gone.
        assert _count(storage, "chapter", ["ch-old-1", "ch-old-2"]) == 0
        assert _count(storage, "paragraph", ["p-old-1", "p-old-2"]) == 0
        assert _count(storage, "span", ["s-old-1", "s-old-2"]) == 0
        # Whole scene layer rebuilt.
        assert storage.execute_query("SELECT COUNT(*) AS cnt FROM scene")[0]["cnt"] == 1

        # New spine present with the new text.
        assert _count(storage, "chapter", ["ch-new-1"]) == 1
        assert _count(storage, "paragraph", ["p-new-1"]) == 1
        assert _count(storage, "span", ["s-new-1"]) == 1
        spans = storage.execute_query("SELECT text FROM span WHERE id = 's-new-1'")
        assert spans[0]["text"] == "Brand new chapter text."
        edges = storage.execute_query(
            "SELECT child_id FROM book_chapter WHERE parent_id = 'b1'"
        )
        assert [e["child_id"] for e in edges] == ["ch-new-1"]

    def test_version_increments_one_per_replace(self, storage):
        """Each replace bumps version by exactly one."""
        _insert_series_book(storage, "b1", version=2)
        _insert_spine(storage, "b1", OLD_BOOK)
        replace_book_tree("b1", NEW_BOOK, storage)
        assert get_book_version("b1", storage) == 3
        replace_book_tree("b1", NEW_BOOK, storage)
        assert get_book_version("b1", storage) == 4


# ---------------------------------------------------------------------------
# P4-S2: transaction rollback on population failure after staging
# ---------------------------------------------------------------------------


class TestReplaceRollback:
    def test_population_failure_rolls_back_entire_replacement(self, storage):
        """A duplicate span id raises IntegrityError during population (after the
        old tree is deleted); the transaction rolls back completely so the
        original tree, version, generated outputs, and render rows are intact."""
        _insert_series_book(storage, "b1", version=2)
        _insert_spine(storage, "b1", OLD_BOOK)
        scene_ids = [
            _scene_for_chapter(storage, "ch-old-1"),
            _scene_for_chapter(storage, "ch-old-2"),
        ]
        _seed_characters(storage, "c1", "c2")
        _seed_full_surface(storage, "b1", scene_ids, ["c1", "c2"])
        _link_scene_character(storage, "c1", scene_ids[0])

        # Snapshot state BEFORE the failed replace.
        before = {
            "version": get_book_version("b1", storage),
            "old_spans": _count(storage, "span", ["s-old-1", "s-old-2"]),
            "render_jobs": storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM render_job WHERE book_id = 'b1'"
            )[0]["cnt"],
            "render_chunks": storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM render_chunk WHERE job_id = 'job-b1'"
            )[0]["cnt"],
            "char_scene": storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM character_scene WHERE character_id = 'c1'"
            )[0]["cnt"],
            "generated": storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM character_scene_generated WHERE book_id = 'b1'"
            )[0]["cnt"],
        }
        assert before["char_scene"] == 1

        # Duplicate span id forces PRIMARY KEY IntegrityError inside population.
        failing = [
            {
                "id": "ch-x",
                "paragraphs": [
                    {
                        "id": "p-x",
                        "spans": [
                            {"id": "s-x", "span_type": "sentence", "text": "a"},
                            {"id": "s-x", "span_type": "sentence", "text": "b"},
                        ],
                    }
                ],
            }
        ]

        with pytest.raises(sqlite3.IntegrityError):
            replace_book_tree("b1", failing, storage)

        # Complete rollback: nothing changed.
        assert get_book_version("b1", storage) == before["version"]
        assert _count(storage, "span", ["s-old-1", "s-old-2"]) == before["old_spans"]
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM render_job WHERE book_id = 'b1'"
            )[0]["cnt"]
            == before["render_jobs"]
        )
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM render_chunk WHERE job_id = 'job-b1'"
            )[0]["cnt"]
            == before["render_chunks"]
        )
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM character_scene WHERE character_id = 'c1'"
            )[0]["cnt"]
            == before["char_scene"]
        )
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM character_scene_generated WHERE book_id = 'b1'"
            )[0]["cnt"]
            == before["generated"]
        )
        # No leftover new node.
        assert _count(storage, "span", ["s-x"]) == 0
        # Book row identity untouched.
        row = storage.execute_query(
            "SELECT id, series_id, book_number, position FROM book WHERE id = 'b1'"
        )[0]
        assert row == {"id": "b1", "series_id": "s1", "book_number": 1, "position": 1}


# ---------------------------------------------------------------------------
# P4-S3: cleanup isolation — two books in one series
# ---------------------------------------------------------------------------


class TestReplaceIsolation:
    def test_sibling_book_untouched(self, storage, render_root):
        """Replacing b1 leaves sibling b2's tree, memberships, shared character
        metadata/voice, render rows, chunks, and filesystem output intact."""
        # -- Two books in the same series.
        _insert_series_book(storage, "b1", series_id="s1", book_number=1, position=1)
        _insert_series_book(storage, "b2", series_id="s1", book_number=2, position=2)

        # b1 spine + characters + outputs.
        _insert_spine(storage, "b1", OLD_BOOK)
        scene_ids_b1 = [
            _scene_for_chapter(storage, "ch-old-1"),
            _scene_for_chapter(storage, "ch-old-2"),
        ]
        _seed_characters(storage, "c1", "c2")
        _seed_full_surface(storage, "b1", scene_ids_b1, ["c1", "c2"])

        # b2 spine with globally-distinct ids + outputs.
        b2_spine = _relabel(OLD_BOOK, "b2")
        _insert_spine(storage, "b2", b2_spine)
        scene_ids_b2 = [
            _scene_for_chapter(storage, "b2-ch1"),
            _scene_for_chapter(storage, "b2-ch2"),
        ]
        _seed_full_surface(storage, "b2", scene_ids_b2, ["c1", "c2"])

        # -- Shared character membership + metadata + voice (both books).
        storage.execute_insert(
            "INSERT INTO character_book (character_id, book_id, source, confidence) "
            "VALUES ('c1', 'b1', 'walk', 0.9)"
        )
        storage.execute_insert(
            "INSERT INTO character_book (character_id, book_id, source, confidence) "
            "VALUES ('c1', 'b2', 'walk', 0.9)"
        )
        storage.execute_insert(
            "INSERT INTO character_metadata (character_id, key, value) "
            "VALUES ('c1', 'voice_profile', 'warm')"
        )
        storage.execute_update(
            "UPDATE character SET voice_assignment_id = 'vc1' WHERE id = 'c1'"
        )
        # One character_scene junction per book (b1's scene, b2's scene).
        _link_scene_character(storage, "c1", scene_ids_b1[0])
        _link_scene_character(storage, "c1", scene_ids_b2[0])

        # Filesystem render dirs for both books.
        (render_root / "book-b1").mkdir()
        (render_root / "book-b1" / "manifest.json").write_text("{}")
        (render_root / "book-b2").mkdir()
        (render_root / "book-b2" / "chunk-0.wav").write_bytes(b"data")

        replace_book_tree("b1", NEW_BOOK, storage)

        # -- Sibling b2 tree intact; b1's old spine gone.
        assert _count(storage, "chapter", ["b2-ch1", "b2-ch2"]) == 2
        assert _count(storage, "paragraph", ["b2-p1-1", "b2-p2-1"]) == 2
        assert _count(storage, "span", ["b2-s1-1-1", "b2-s2-1-1"]) == 2
        assert _count(storage, "chapter", ["ch-old-1", "ch-old-2"]) == 0
        edges = storage.execute_query(
            "SELECT child_id FROM book_chapter WHERE parent_id = 'b2'"
        )
        assert {e["child_id"] for e in edges} == {"b2-ch1", "b2-ch2"}
        scene_edges = storage.execute_query(
            "SELECT COUNT(*) AS cnt FROM chapter_scene WHERE parent_id IN "
            "(SELECT id FROM chapter WHERE book_id = 'b2')"
        )[0]["cnt"]
        assert scene_edges == 2
        para_edges = storage.execute_query(
            "SELECT COUNT(*) AS cnt FROM scene_paragraph WHERE parent_id = ?",
            (scene_ids_b2[0],),
        )[0]["cnt"]
        assert para_edges == 1
        span_edges = storage.execute_query(
            "SELECT COUNT(*) AS cnt FROM paragraph_span WHERE child_id IN "
            "('b2-s1-1-1', 'b2-s2-1-1')"
        )[0]["cnt"]
        assert span_edges == 2

        # -- b1's generated outputs cleared; b2's intact.
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM render_job WHERE book_id = 'b1'"
            )[0]["cnt"]
            == 0
        )
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM render_job WHERE book_id = 'b2'"
            )[0]["cnt"]
            == 1
        )
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM render_chunk WHERE job_id = 'job-b2'"
            )[0]["cnt"]
            == 1
        )
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM character_scene_generated WHERE book_id = 'b1'"
            )[0]["cnt"]
            == 0
        )
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM character_scene_generated WHERE book_id = 'b2'"
            )[0]["cnt"]
            == 1
        )
        # character_scene junctions: b1's (deleted scene) gone, b2's keened.
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM character_scene WHERE scene_id = ?",
                (scene_ids_b2[0],),
            )[0]["cnt"]
            == 1
        )

        # -- Shared character metadata/voice preserved (c1 still in b2).
        md = storage.execute_query(
            "SELECT value FROM character_metadata WHERE character_id = 'c1' AND key = 'voice_profile'"
        )
        assert md[0]["value"] == "warm"
        voice = storage.execute_query(
            "SELECT voice_assignment_id FROM character WHERE id = 'c1'"
        )
        assert voice[0]["voice_assignment_id"] == "vc1"
        memberships = storage.execute_query(
            "SELECT book_id FROM character_book WHERE character_id = 'c1'"
        )
        assert [m["book_id"] for m in memberships] == ["b2"]

        # -- Filesystem: b1 render dir removed, b2 dir preserved.
        assert not (render_root / "book-b1").exists()
        assert (render_root / "book-b2").is_dir()
        assert (render_root / "book-b2" / "chunk-0.wav").exists()


# ---------------------------------------------------------------------------
# P4-S4: full generated-table + filesystem artifact cleanup, missing-dir safe
# ---------------------------------------------------------------------------


class TestReplaceCleansAllGeneratedOutputs:
    def test_clears_all_book_owned_tables_and_preserves_global_persona(
        self, storage, render_root
    ):
        """A fully-workbenched book replaces cleanly (no FK failure) and every
        book-scoped generated table is emptied while the global NULL-book_id
        persona and shared voice_config/clone_reference survive."""
        _insert_series_book(storage, "b1", book_number=2, position=4)
        _insert_spine(storage, "b1", OLD_BOOK)
        scene_ids = [
            _scene_for_chapter(storage, "ch-old-1"),
            _scene_for_chapter(storage, "ch-old-2"),
        ]
        _seed_characters(storage, "c1", "c2")
        _link_scene_character(storage, "c1", scene_ids[0])
        # Book-scoped membership (not shared) so metadata/voice are cleared.
        storage.execute_insert(
            "INSERT INTO character_book (character_id, book_id, source, confidence) "
            "VALUES ('c1', 'b1', 'walk', 0.9)"
        )
        storage.execute_insert(
            "INSERT INTO character_metadata (character_id, key, value) "
            "VALUES ('c1', 'description', 'heroine')"
        )
        storage.execute_update(
            "UPDATE character SET voice_assignment_id = 'vc1' WHERE id = 'c1'"
        )
        # Book-scoped + global persona revisions.
        storage.execute_insert(
            "INSERT INTO persona_revision (persona_id, character_id, book_id, "
            "revision, fields_json, evidence_json, aliases_json, scene_scope, "
            "review_state, protected, voice_consequences_json, author_id, "
            "created_ms, superseded_by) VALUES ('bp1', 'c1', 'b1', 1, '{}', "
            "'[]', '[]', 'book', 'accepted', 0, '{}', 'local', 1, NULL)"
        )
        storage.execute_insert(
            "INSERT INTO persona_revision (persona_id, character_id, book_id, "
            "revision, fields_json, evidence_json, aliases_json, scene_scope, "
            "review_state, protected, voice_consequences_json, author_id, "
            "created_ms, superseded_by) VALUES ('gp1', 'c1', NULL, 2, '{}', "
            "'[]', '[]', 'book', 'accepted', 1, '{}', 'local', 2, NULL)"
        )
        _seed_full_surface(storage, "b1", scene_ids, ["c1", "c2"])
        # Clone reference (shared/never-touched).
        storage.execute_insert(
            "INSERT INTO clone_reference (reference_id, voice_id, owner_id, "
            "relative_path, original_filename, media_type, byte_size, "
            "duration_ms, sha256, created_ms) VALUES ('cref1', 'vc1', 'local', "
            "'ref/voice.wav', 'voice.wav', 'audio/wav', 100, 500, 'abc', 1)"
        )
        (render_root / "book-b1").mkdir()
        (render_root / "book-b1" / "manifest.json").write_text("{}")

        replace_book_tree("b1", NEW_BOOK, storage)

        # -- Every book-owned generated table emptied for b1.
        book_owned = {
            "workbench_decision",
            "workbench_provenance",
            "workbench_generation",
            "character_scene_generated",
            "character_scene_manual",
            "character_scene_absence",
            "boundary_override",
            "character_alias_merge",
            "walk_review_item",
            "walk_override",
            "prompt_config_revision",
            "project_snapshot",
            "render_job",
            "character_book",
        }
        for table in book_owned:
            rows = storage.execute_query(f"SELECT * FROM {table} WHERE book_id = 'b1'")
            assert rows == [], f"{table} still has b1 rows: {rows!r}"
        # render_chunk is keyed by job_id (no book_id column): verify via the job.
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM render_chunk WHERE job_id = 'job-b1'"
            )[0]["cnt"]
            == 0
        )
        # Run/undo journal gone.
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM walk_run WHERE book_id = 'b1'"
            )[0]["cnt"]
            == 0
        )
        assert (
            storage.execute_query("SELECT COUNT(*) AS cnt FROM walk_undo_entry")[0][
                "cnt"
            ]
            == 0
        )
        # Junctions reachable from the tree gone.
        assert (
            storage.execute_query("SELECT COUNT(*) AS cnt FROM character_span")[0][
                "cnt"
            ]
            == 0
        )
        assert (
            storage.execute_query("SELECT COUNT(*) AS cnt FROM character_scene")[0][
                "cnt"
            ]
            == 0
        )
        # Tree fully swapped.
        assert _count(storage, "span", ["s-old-1", "s-old-2"]) == 0
        assert _count(storage, "span", ["s-new-1"]) == 1

        # -- Protected / shared state preserved.
        assert storage.execute_query(
            "SELECT persona_id FROM persona_revision WHERE persona_id = 'gp1'"
        ) == [{"persona_id": "gp1"}]
        assert (
            storage.execute_query(
                "SELECT persona_id FROM persona_revision WHERE book_id = 'b1'"
            )
            == []
        )
        # Shared character rows + voice_config + clone_reference intact.
        assert _count(storage, "character", ["c1", "c2"]) == 2
        assert (
            storage.execute_query("SELECT COUNT(*) AS cnt FROM voice_config")[0]["cnt"]
            == 1
        )
        assert storage.execute_query(
            "SELECT reference_id FROM clone_reference WHERE reference_id = 'cref1'"
        ) == [{"reference_id": "cref1"}]
        # b1 render directory removed.
        assert not (render_root / "book-b1").exists()

    def test_clears_sibling_preserved_across_series(self, storage, render_root):
        """A second book in a different series keeps its render dir + outputs."""
        _insert_series_book(storage, "b1", series_id="s1")
        _insert_series_book(storage, "b2", series_id="s2", book_number=1, position=1)
        _insert_spine(storage, "b1", OLD_BOOK)
        _insert_spine(storage, "b2", _relabel(OLD_BOOK, "b2"))
        scene_ids_b1 = [
            _scene_for_chapter(storage, "ch-old-1"),
            _scene_for_chapter(storage, "ch-old-2"),
        ]
        scene_ids_b2 = [
            _scene_for_chapter(storage, "b2-ch1"),
            _scene_for_chapter(storage, "b2-ch2"),
        ]
        _seed_characters(storage, "c1")
        _seed_full_surface(storage, "b1", scene_ids_b1, ["c1"])
        _seed_full_surface(storage, "b2", scene_ids_b2, ["c1"])
        (render_root / "book-b2").mkdir()
        (render_root / "book-b2" / "chunk-0.wav").write_bytes(b"x")

        replace_book_tree("b1", NEW_BOOK, storage)

        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM render_job WHERE book_id = 'b1'"
            )[0]["cnt"]
            == 0
        )
        assert (
            storage.execute_query(
                "SELECT COUNT(*) AS cnt FROM render_job WHERE book_id = 'b2'"
            )[0]["cnt"]
            == 1
        )
        assert not (render_root / "book-b1").exists()
        assert (render_root / "book-b2").is_dir()
        # Only b2's chapters remain (b1's old spine cleared).
        assert _count(storage, "chapter", ["b2-ch1", "b2-ch2"]) == 2
        assert _count(storage, "chapter", ["ch-old-1", "ch-old-2"]) == 0

    def test_missing_render_dir_is_tolerated(self, storage, render_root):
        """A book with no render directory on disk replaces successfully."""
        _insert_series_book(storage, "b1")
        _insert_spine(storage, "b1", OLD_BOOK)
        # No book-b1 render dir created.
        replace_book_tree("b1", NEW_BOOK, storage)
        assert get_book_version("b1", storage) == 2
        assert _count(storage, "span", ["s-new-1"]) == 1

    def test_missing_derived_files_are_removed_without_error(
        self, storage, render_root
    ):
        """A render dir with missing derived files (e.g. no manifest) is removed
        without raising (ignore_errors=True tolerance)."""
        _insert_series_book(storage, "b1")
        _insert_spine(storage, "b1", OLD_BOOK)
        (render_root / "book-b1").mkdir()
        (render_root / "book-b1" / "partial.wav").write_bytes(b"x")
        replace_book_tree("b1", NEW_BOOK, storage)
        assert not (render_root / "book-b1").exists()
