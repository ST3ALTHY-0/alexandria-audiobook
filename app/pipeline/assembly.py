"""Deterministic assembly — export annotated scripts for TTS rendering.

Provides ``export_annotated_script`` which walks the document spine in
presentation order and resolves each span's speaker via the
``character_span`` junction (``relation_type = 'speaker'``).  Spans with
no speaker attribution are presented as ``'NARRATOR'``.

Also provides re-onboarding utilities:

* ``reonboard_book`` — increments ``book.version``, surgically clears
  all walk-created junction/edge data (character spans, scenes,
  memberships, span instructions, voice assignments), and returns the
  new version number.  **Memberships (``character_book`` rows) are NOT
  carried over** — they are deleted and must be re-created by the next
  walk run.
* ``get_book_version`` — returns the current ``version`` for a book.
* ``replace_book_tree`` — atomic full document-tree replacement seam that
  retains the existing ``book`` identity, keyed by the module-level constant
  ``REPLACE_TABLE_SCOPE``.

Usage::

    from app.pipeline.adapter import PipelineStorage
    from app.pipeline.assembly import export_annotated_script

    storage: PipelineStorage = ...
    script = export_annotated_script("book-001", storage)
    # script == [{"speaker": "Alice", "text": "...", "instruct": "..."}, ...]
"""

from __future__ import annotations

import os
import shutil

from app.pipeline.adapter import PipelineStorage
from app.pipeline.populate import (
    _ensure_paragraph_text_column,
    _ensure_span_text_column,
    _insert_chapters_with_placeholders,
)


def export_annotated_script(book_id: str, storage: PipelineStorage) -> list[dict]:
    """Export the annotated script for *book_id* in presentation order.

    The document spine is traversed using the same join chain as the
    ``span_presentation`` VIEW (span → paragraph_span → scene_paragraph →
    chapter_scene → book_chapter → book), filtered to a single book and
    ordered by the positional edges.

    For each span the speaker is resolved via ``character_span`` with
    ``relation_type = 'speaker'``.  If no speaker junction exists the span
    is attributed to ``'NARRATOR'``.

    Parameters
    ----------
    book_id:
        Primary key of the book to export.
    storage:
        An active ``PipelineStorage`` implementation.

    Returns
    -------
    list[dict]
        Each entry has keys ``speaker`` (str), ``text`` (str), and
        ``instruct`` (str).  The list is ordered by presentation
        (global_index).
    """
    rows = storage.execute_query(
        """
        SELECT
            span.id,
            span.text,
            span.instruct,
            c.name    AS character_name
        FROM span
        JOIN paragraph_span  AS span_edge
            ON span.id = span_edge.child_id
        JOIN scene_paragraph AS paragraph_edge
            ON span_edge.parent_id = paragraph_edge.child_id
        JOIN chapter_scene   AS scene_edge
            ON paragraph_edge.parent_id = scene_edge.child_id
        JOIN book_chapter    AS chapter_edge
            ON scene_edge.parent_id = chapter_edge.child_id
        JOIN book
            ON chapter_edge.parent_id = book.id
              LEFT JOIN character_span AS cs
                  ON span.id = cs.span_id
                 AND cs.relation_type = 'speaker'
                  AND cs.rowid = (
                      SELECT cs_existing.rowid
                      FROM character_span AS cs_existing
                      WHERE cs_existing.span_id = span.id
                        AND cs_existing.relation_type = 'speaker'
                      ORDER BY cs_existing.human_override DESC,
                               cs_existing.rowid DESC
                      LIMIT 1
                  )
        LEFT JOIN character AS c
            ON cs.character_id = c.id
        WHERE book.id = ?
        ORDER BY
            book.position,
            chapter_edge.position,
            scene_edge.position,
            paragraph_edge.position,
            span_edge.position
        """,
        (book_id,),
    )

    result: list[dict] = []
    for row in rows:
        speaker = row["character_name"] if row["character_name"] else "NARRATOR"
        result.append(
            {
                "id": row["id"],
                "speaker": speaker,
                "text": row["text"] or "",
                "instruct": row["instruct"] or "",
            }
        )
    return result


# ---------------------------------------------------------------------------
# Re-onboarding & version management
# ---------------------------------------------------------------------------


def get_book_version(book_id: str, storage: PipelineStorage) -> int:
    """Return the current ``version`` number for *book_id*.

    Parameters
    ----------
    book_id:
        Primary key of the book.
    storage:
        An active ``PipelineStorage`` implementation.

    Returns
    -------
    int
        The current version (defaults to 1 for newly onboarded books).

    Raises
    ------
    ValueError
        If no book with *book_id* exists.
    """
    rows = storage.execute_query("SELECT version FROM book WHERE id = ?", (book_id,))
    if not rows:
        raise ValueError(f"Book '{book_id}' not found")
    return rows[0]["version"]


def has_active_run(storage: PipelineStorage) -> bool:
    """Return True if any ``walk_run`` row is active.

    Active means a ``pending`` or ``running`` run — a writer that could still
    execute or is currently executing. This is the single coordination check
    the API layer uses before clearing/replacing book data (CONTRACTS.md
    "Onboarding / re-onboarding / book-switching coordination"): replacement
    state must never become current while an active writer could still run.

    The ``walk_run`` table is authoritative for active runs (rows = truth), so
    this reads it directly rather than relying on in-memory status caches.

    This is deliberately process-wide: the single-active-walk invariant spans
    all books, so replacement of any book must wait for every active writer.

    Parameters
    ----------
    storage:
        An active ``PipelineStorage`` implementation.

    Returns
    -------
    bool
        True if at least one pending/running walk_run row exists.
    """
    rows = storage.execute_query(
        "SELECT 1 FROM walk_run WHERE status IN ('pending', 'running') LIMIT 1",
    )
    return bool(rows)


# ---------------------------------------------------------------------------
# Plan T (P1-S2): Replace cleanup ownership ALLOWLIST
# ---------------------------------------------------------------------------
# Destructive ``replace_book_tree`` may touch ONLY the tables enumerated here,
# each scoped to the retained book.  Scope keys:
#   ``book_id`` — row carries a ``book_id`` column; delete/update MUST filter on
#      the exact retained ``book_id`` (``WHERE book_id = ?``) so sibling-book
#      rows are never affected.
#   ``scene``   — no ``book_id`` column; the row links a book-scoped
#      scene/span/chapter and must be deleted by reachability FROM the retained
#      book's tree, BEFORE the referenced tree node it holds an FK to.
#   ``render``  — book-owned ``render_job`` rows (``book_id = ?``);
#      ``render_chunk`` cascades by ``job_id``; the durable
#      ``RENDER_ROOT/book-<book_id>/`` directory is removed with the job rows.
#   ``run``     — book-owned ``walk_run`` rows (``book_id = ?``);
#      ``walk_undo_entry`` cascades by ``run_id``.
#
# Tables absent from this dict must NEVER be touched by Replace cleanup:
# ``series``, ``character``, ``character_metadata``, ``character_series``,
# ``voice_config``, ``clone_reference``, and any sibling/other-book ``book``
# row.  ``character_metadata`` / ``character.voice_assignment_id`` are shared
# character state that is only conditionally cleared under the exact
# ``NOT EXISTS``-another-``character_book`` guard reonboard_book uses — never
# blanket-deleted.  See CONTRACTS.md "Replace ownership ALLOWLIST (P1-S2)".
REPLACE_TABLE_SCOPE: dict[str, str] = {
    # Graph1 TREE document spine
    "chapter": "book_id",
    "book_chapter": "book_id",
    "scene": "scene",
    "chapter_scene": "scene",
    "paragraph": "scene",
    "scene_paragraph": "scene",
    "span": "scene",
    "paragraph_span": "scene",
    # Graph2 junctions reachable from the retained book's tree
    "character_scene": "scene",
    "character_span": "scene",
    # Book-keyed generated projections, workbench, and book-scoped records
    "character_book": "book_id",
    "character_scene_generated": "book_id",
    "character_scene_manual": "book_id",
    "character_scene_absence": "book_id",
    "character_alias_merge": "book_id",
    "boundary_override": "book_id",
    "workbench_decision": "book_id",
    "workbench_provenance": "book_id",
    "workbench_generation": "book_id",
    "walk_review_item": "book_id",
    "walk_override": "book_id",
    "persona_revision": "book_id",  # book-scoped only; NULL-book_id rows protected
    "prompt_config_revision": "book_id",
    "project_snapshot": "book_id",
    # Book-owned run/render records
    "walk_run": "run",
    "walk_undo_entry": "run",
    "render_job": "render",
    "render_chunk": "render",
}


def _clear_span_junctions(
    storage: PipelineStorage, book_id: str, scene_ids: list[str]
) -> None:
    """Clear character-span and character-scene junctions for *book_id*.

    Deletes ``character_span`` rows reachable through the book tree,
    deletes ``character_scene`` rows for the book's scenes, and resets
    ``span.instruct`` to NULL for the book's spans.

    Parameters
    ----------
    storage:
        An active ``PipelineStorage`` implementation.
    book_id:
        Primary key of the book.
    scene_ids:
        Pre-snapshot of scene IDs belonging to this book.
    """
    # character_span: delete for all spans reachable through the book tree.
    storage.execute_delete(
        """DELETE FROM character_span
           WHERE span_id IN (
               SELECT span.id FROM span
               JOIN paragraph_span AS span_edge
                   ON span.id = span_edge.child_id
               JOIN scene_paragraph AS paragraph_edge
                   ON span_edge.parent_id = paragraph_edge.child_id
               JOIN chapter_scene AS scene_edge
                   ON paragraph_edge.parent_id = scene_edge.child_id
               JOIN book_chapter AS chapter_edge
                   ON scene_edge.parent_id = chapter_edge.child_id
               WHERE chapter_edge.parent_id = ?
           )""",
        (book_id,),
    )

    # character_scene: delete for the book's scenes (using snapshot).
    if scene_ids:
        placeholders = ",".join("?" for _ in scene_ids)
        storage.execute_delete(
            f"DELETE FROM character_scene WHERE scene_id IN ({placeholders})",
            tuple(scene_ids),
        )

    # Reset span.instruct to NULL for the book's spans (still needs
    # chapter_scene join).
    storage.execute_update(
        """UPDATE span SET instruct = NULL
           WHERE id IN (
               SELECT span.id FROM span
               JOIN paragraph_span AS span_edge
                   ON span.id = span_edge.child_id
               JOIN scene_paragraph AS paragraph_edge
                   ON span_edge.parent_id = paragraph_edge.child_id
               JOIN chapter_scene AS scene_edge
                   ON paragraph_edge.parent_id = scene_edge.child_id
               JOIN book_chapter AS chapter_edge
                   ON scene_edge.parent_id = chapter_edge.child_id
               WHERE chapter_edge.parent_id = ?
           )""",
        (book_id,),
    )


def _clear_memberships(
    storage: PipelineStorage, book_id: str, character_ids: list[str]
) -> None:
    """Clear character-book memberships and metadata for *book_id*.

    Deletes ``character_book`` rows (memberships are NOT carried over),
    deletes ``character_metadata`` rows for the linked characters, and
    resets ``character.voice_assignment_id`` to NULL.

    Parameters
    ----------
    storage:
        An active ``PipelineStorage`` implementation.
    book_id:
        Primary key of the book.
    character_ids:
        Pre-snapshot of character IDs linked to this book.
    """
    # Clear metadata and voice assignments only for characters that will no
    # longer belong to any book after this membership is removed.  Character
    # metadata and voice assignments are shared character state, so preserve
    # them when another book still references the character.
    if character_ids:
        placeholders = ",".join("?" for _ in character_ids)
        storage.execute_delete(
            f"""DELETE FROM character_metadata
                WHERE character_id IN ({placeholders})
                  AND NOT EXISTS (
                      SELECT 1 FROM character_book
                      WHERE character_book.character_id = character_metadata.character_id
                        AND character_book.book_id != ?
                  )""",
            (*character_ids, book_id),
        )
        storage.execute_update(
            f"""UPDATE character SET voice_assignment_id = NULL
                WHERE id IN ({placeholders})
                  AND NOT EXISTS (
                      SELECT 1 FROM character_book
                      WHERE character_book.character_id = character.id
                        AND character_book.book_id != ?
                  )""",
            (*character_ids, book_id),
        )

    # character_book: memberships NOT carried over — deleted entirely.
    storage.execute_delete("DELETE FROM character_book WHERE book_id = ?", (book_id,))


def _clear_persona_revisions(storage: PipelineStorage, book_id: str) -> None:
    """Discard book-scoped persona revisions from the prior onboarding."""
    storage.execute_update(
        """UPDATE persona_revision SET superseded_by = NULL
           WHERE superseded_by IN (
               SELECT persona_id FROM persona_revision WHERE book_id = ?
           )""",
        (book_id,),
    )
    storage.execute_delete("DELETE FROM persona_revision WHERE book_id = ?", (book_id,))


def _clear_scene_entities(
    storage: PipelineStorage, book_id: str, scene_ids: list[str]
) -> None:
    """Clear scene entities and edges for *book_id*.

    Deletes ``scene_paragraph`` edges, ``chapter_scene`` edges, and
    ``scene`` rows that belong to this book.  Also deletes the book-scoped
    workbench/tombstone rows that hold ``scene`` foreign keys without
    cascading deletes (``character_scene_absence``, ``character_alias_merge``,
    ``boundary_override``, ``character_scene_generated``,
    ``character_scene_manual``).

    Parameters
    ----------
    storage:
        An active ``PipelineStorage`` implementation.
    book_id:
        Primary key of the book.
    scene_ids:
        Pre-snapshot of scene IDs belonging to this book.
    """
    # Workbench projections and overrides retain scene foreign keys without
    # cascading deletes.  Clear them before removing the scenes so a failed
    # re-onboard cannot leave the book half-cleared.
    for table in (
        "character_scene_absence",
        "character_alias_merge",
        "boundary_override",
        "character_scene_generated",
        "character_scene_manual",
    ):
        storage.execute_delete(f"DELETE FROM {table} WHERE book_id = ?", (book_id,))

    # scene_paragraph edges: must be deleted before scenes (FK constraint).
    if scene_ids:
        placeholders = ",".join("?" for _ in scene_ids)
        storage.execute_delete(
            f"DELETE FROM scene_paragraph WHERE parent_id IN ({placeholders})",
            tuple(scene_ids),
        )

    # chapter_scene edges: delete for the book's chapters.
    storage.execute_delete(
        """DELETE FROM chapter_scene
           WHERE parent_id IN (
               SELECT id FROM chapter WHERE book_id = ?
           )""",
        (book_id,),
    )

    # scene rows: delete scenes that belonged to this book (using snapshot).
    if scene_ids:
        placeholders = ",".join("?" for _ in scene_ids)
        storage.execute_delete(
            f"DELETE FROM scene WHERE id IN ({placeholders})",
            tuple(scene_ids),
        )


def reonboard_book(book_id: str, storage: PipelineStorage) -> int:
    """Re-onboard a book by clearing walk outputs and bumping its version.

    The document tree (series, book, chapters, paragraphs, spans) is
    preserved.  Only walk-created junction/edge data is removed:

    * ``character_span`` rows for the book's spans
    * ``character_scene`` rows for the book's scenes
    * ``character_book`` rows for the book (**memberships are NOT
      carried over** — they must be re-created by the next walk run)
    * book-scoped ``persona_revision`` rows from the prior onboarding
    * ``character_metadata`` rows for characters no longer linked to any book
    * ``chapter_scene`` edges for the book's chapters
    * ``scene`` rows that belong to this book
    * ``span.instruct`` reset to NULL for the book's spans
    * ``character.voice_assignment_id`` reset to NULL for characters no
      longer linked to any book

    Characters themselves are **not** deleted — they may be shared
    across books in a series.

    Parameters
    ----------
    book_id:
        Primary key of the book to re-onboard.
    storage:
        An active ``PipelineStorage`` implementation.

    Returns
    -------
    int
        The new version number after incrementing.
    """
    with storage.transaction():
        # -- Snapshot IDs before destructive deletes ------------------------
        # character_ids: needed for metadata cleanup and voice_assignment reset.
        char_rows = storage.execute_query(
            "SELECT character_id FROM character_book WHERE book_id = ?",
            (book_id,),
        )
        character_ids = [r["character_id"] for r in char_rows]

        # scene_ids: needed for scene deletion after chapter_scene edges are
        # removed (the chapter_scene join is the standard way to find a book's
        # scenes, so we must snapshot before deleting those edges).
        scene_rows = storage.execute_query(
            """SELECT chapter_scene.child_id AS scene_id
               FROM chapter_scene
               JOIN book_chapter
                   ON chapter_scene.parent_id = book_chapter.child_id
               WHERE book_chapter.parent_id = ?""",
            (book_id,),
        )
        scene_ids = [r["scene_id"] for r in scene_rows]

        # -- Phase 1: Clear span/scene junctions ----------------------------
        _clear_span_junctions(storage, book_id, scene_ids)

        # -- Phase 2: Clear character memberships and metadata --------------
        _clear_memberships(storage, book_id, character_ids)

        # -- Phase 2b: Clear book-scoped persona output ----------------------
        _clear_persona_revisions(storage, book_id)

        # -- Phase 3: Clear scene entities and edges ------------------------
        _clear_scene_entities(storage, book_id, scene_ids)

        # -- Phase 4: Bump version ------------------------------------------
        storage.execute_update(
            "UPDATE book SET version = version + 1 WHERE id = ?", (book_id,)
        )
        return get_book_version(book_id, storage)


def replace_book_tree(
    book_id: str,
    chapters_data: list[dict],
    storage: PipelineStorage,
) -> dict:
    """Atomically replace an existing book's document tree and generated outputs.

    Destructively swaps the *book_id*'s document spine and all of its book-owned
    generated DB/filesystem outputs for a freshly extracted spine, while
    retaining the existing book's identity and ordering fields.

    **Retained** on the existing ``book`` row (never recreated, never
    renumbered): ``id``, ``series_id``, ``book_number``, and ``position``.  The
    ``version`` is bumped as part of the replacement.  This method creates NO
    new ``book`` row — the new tree is populated under the already-existing
    retained ``book`` via the spine helpers in ``app/pipeline/populate.py`` —
    and introduces no series-position or visibility controls.

    **Protected** (never touched): shared ``character`` identity rows,
    shared ``character_metadata`` / ``character.voice_assignment_id`` (cleared
    only under the ``NOT EXISTS``-another-``character_book`` guard shared with
    ``reonboard_book``), ``voice_config`` / ``clone_reference``, global
    ``persona_revision`` rows (``book_id IS NULL``), ``series``, and every
    sibling-book row.  The complete ownership ALLOWLIST is
    :data:`REPLACE_TABLE_SCOPE`.

    **Atomicity / ordering** (Plan T P1-S3): the whole DB replacement runs inside
    one adapter-owned ``transaction()`` (``BEGIN IMMEDIATE`` — the same atomic
    unit ``reonboard_book`` uses) so a failure anywhere — a missing dependency,
    an FK ``NO ACTION``, a population error — rolls the complete replacement
    back and leaves the prior tree and outputs intact.  Order: (1) snapshot the
    retained ``{id, series_id, book_number, position, version}`` and the book's
    chapter/scene/span IDs plus its ``character_book`` character set; (2) remove
    the old book-owned tree/output references in FK-safe (deep-first) order,
    deleting references to a node (``character_scene``, ``character_span``,
    generated/manual presence, workbench/boundary rows, memberships) before the
    node they reference; (3) populate the extracted tree under the retained
    book; (4) reconcile/clear book-scoped generated outputs and the book's
    render/walk records; (5) commit on transaction exit only after FK/ownership
    checks pass.  Only on a successful commit is the book's durable render
    directory ``RENDER_ROOT/book-<book_id>/`` removed.  The calling API layer
    enforces extraction-before-mutation, unknown-book ``404``, and ``503`` +
    ``Retry-After`` while a process-wide walk is active (``has_active_run``).

    Parameters
    ----------
    book_id:
        Primary key of the EXISTING book to replace (must already exist).
    chapters_data:
        Extracted spine data (same shape ``extract_epub_text`` returns):
        ``[{id, paragraphs: [{id, spans: [{id, span_type, text}]}]}]``.
    storage:
        An active ``PipelineStorage`` implementation.

    Returns
    -------
    dict
        ``{book_id, series_id, book_number, position, version, chapters}`` —
        the retained identity/ordering, the new ``version``, and the replaced
        chapter count.

    Raises
    ------
    ValueError
        If no book with *book_id* exists (mapped to HTTP ``404``) or if
        *chapters_data* is empty.
    """
    # Non-mutating seam validation: the retained book must exist and the staged
    # extraction must be non-empty before any transactional mutation begins.
    rows = storage.execute_query(
        "SELECT id, series_id, book_number, position, version FROM book WHERE id = ?",
        (book_id,),
    )
    if not rows:
        raise ValueError(f"Book '{book_id}' not found")
    if not chapters_data:
        raise ValueError("Cannot replace a book without chapters")
    retained = rows[0]

    with storage.transaction():
        # -- (1) Snapshot retained fields + book-owned ID sets --------------
        # The join chains below die with the tree, so snapshot BEFORE any
        # delete.  chapter_ids come straight off ``chapter.book_id``; the rest
        # walk the edge chain back to the retained book.
        chapter_rows = storage.execute_query(
            "SELECT id FROM chapter WHERE book_id = ?", (book_id,)
        )
        chapter_ids = [r["id"] for r in chapter_rows]

        scene_rows = storage.execute_query(
            """SELECT chapter_scene.child_id AS id
               FROM chapter_scene
               JOIN book_chapter
                   ON chapter_scene.parent_id = book_chapter.child_id
               WHERE book_chapter.parent_id = ?""",
            (book_id,),
        )
        scene_ids = [r["id"] for r in scene_rows]

        para_rows = storage.execute_query(
            """SELECT scene_paragraph.child_id AS id
               FROM scene_paragraph
               JOIN chapter_scene AS scene_edge
                   ON scene_paragraph.parent_id = scene_edge.child_id
               JOIN book_chapter
                   ON scene_edge.parent_id = book_chapter.child_id
               WHERE book_chapter.parent_id = ?""",
            (book_id,),
        )
        paragraph_ids = [r["id"] for r in para_rows]

        span_rows = storage.execute_query(
            """SELECT paragraph_span.child_id AS id
               FROM paragraph_span
               JOIN scene_paragraph AS paragraph_edge
                   ON paragraph_span.parent_id = paragraph_edge.child_id
               JOIN chapter_scene AS scene_edge
                   ON paragraph_edge.parent_id = scene_edge.child_id
               JOIN book_chapter
                   ON scene_edge.parent_id = book_chapter.child_id
               WHERE book_chapter.parent_id = ?""",
            (book_id,),
        )
        span_ids = [r["id"] for r in span_rows]

        char_rows = storage.execute_query(
            "SELECT character_id FROM character_book WHERE book_id = ?",
            (book_id,),
        )
        character_ids = [r["character_id"] for r in char_rows]

        # -- (2) Remove old book-owned tree/output references (FK-safe) -----
        # Character/span/scene junctions first (they reference the tree nodes).
        _clear_span_junctions(storage, book_id, scene_ids)

        # Memberships + conditionally-cleared shared metadata/voice.
        _clear_memberships(storage, book_id, character_ids)

        # Book-scoped persona output (global NULL-book_id rows preserved).
        _clear_persona_revisions(storage, book_id)

        # Scene-bound workbench/tombstone tables, scene edges, and scene rows.
        _clear_scene_entities(storage, book_id, scene_ids)

        # Workbench decision/provenance/generation (decision self-refs first,
        # and the child-of-decision tables are cleared by _clear_scene_entities).
        if paragraph_ids:
            ph = ",".join("?" for _ in paragraph_ids)
            # paragraph_span edges reference span+paragraph; clear before nodes.
            storage.execute_delete(
                f"DELETE FROM paragraph_span WHERE parent_id IN ({ph})",
                tuple(paragraph_ids),
            )
        storage.execute_delete(
            "DELETE FROM book_chapter WHERE parent_id = ?", (book_id,)
        )
        storage.execute_update(
            "UPDATE workbench_decision SET undone_by = NULL, supersedes_id = NULL "
            "WHERE book_id = ?",
            (book_id,),
        )
        storage.execute_delete(
            "DELETE FROM workbench_provenance WHERE book_id = ?", (book_id,)
        )
        storage.execute_delete(
            "DELETE FROM workbench_decision WHERE book_id = ?", (book_id,)
        )
        storage.execute_delete(
            "DELETE FROM workbench_generation WHERE book_id = ?", (book_id,)
        )

        # Walk review/override records keyed by book_id.
        storage.execute_delete(
            "DELETE FROM walk_review_item WHERE book_id = ?", (book_id,)
        )
        storage.execute_delete(
            "DELETE FROM walk_override WHERE book_id = ?", (book_id,)
        )

        # Book-scoped prompt/revision + snapshot records.
        storage.execute_update(
            "UPDATE prompt_config_revision SET base_revision = NULL, "
            "superseded_by = NULL WHERE book_id = ?",
            (book_id,),
        )
        storage.execute_delete(
            "DELETE FROM prompt_config_revision WHERE book_id = ?", (book_id,)
        )
        storage.execute_delete(
            "DELETE FROM project_snapshot WHERE book_id = ?", (book_id,)
        )

        # Book-owned walk runs + their undo journals (guard prevents live runs).
        storage.execute_delete(
            "DELETE FROM walk_undo_entry WHERE run_id IN "
            "(SELECT run_id FROM walk_run WHERE book_id = ?)",
            (book_id,),
        )
        storage.execute_delete("DELETE FROM walk_run WHERE book_id = ?", (book_id,))

        # Book-owned render records (rows = truth; the durable dir is removed
        # after commit).
        storage.execute_delete(
            "DELETE FROM render_chunk WHERE job_id IN "
            "(SELECT job_id FROM render_job WHERE book_id = ?)",
            (book_id,),
        )
        storage.execute_delete("DELETE FROM render_job WHERE book_id = ?", (book_id,))

        # Deep tree nodes: spans/paragraphs/chapters (scenes/edges already
        # handled by _clear_scene_entities; paragraph_span + book_chapter edges
        # cleared above).
        if span_ids:
            ph = ",".join("?" for _ in span_ids)
            storage.execute_delete(
                f"DELETE FROM span WHERE id IN ({ph})", tuple(span_ids)
            )
        if paragraph_ids:
            ph = ",".join("?" for _ in paragraph_ids)
            storage.execute_delete(
                f"DELETE FROM paragraph WHERE id IN ({ph})", tuple(paragraph_ids)
            )
        if chapter_ids:
            ph = ",".join("?" for _ in chapter_ids)
            storage.execute_delete(
                f"DELETE FROM chapter WHERE id IN ({ph})", tuple(chapter_ids)
            )

        # -- (3) Populate the NEW tree under the retained book --------------
        # Reuse populate.py low-level helpers.  We do NOT call
        # populate_initial_spine: it does ``INSERT INTO book`` (PK conflict with
        # the retained row) and picks a new position/renumbers — for Replace the
        # existing book row (id/series_id/book_number/position) is retained, so
        # only chapters/scenes/paragraphs/spans + their edges are created.
        _ensure_paragraph_text_column(storage)
        _ensure_span_text_column(storage)
        _insert_chapters_with_placeholders(book_id, chapters_data, storage)

        # -- (4) Bump version -----------------------------------------------
        storage.execute_update(
            "UPDATE book SET version = version + 1 WHERE id = ?", (book_id,)
        )
        new_version = get_book_version(book_id, storage)
        # -- (5) transaction() exit COMMITs only if nothing raised ----------

    # Remove ONLY this book's durable render directory/artifacts.  Runs after
    # the DB commit so a rollback never destroys rows whose dir still exists,
    # never deletes another book's directory, and tolerates missing files.
    _remove_book_render_dir(book_id)

    return {
        "book_id": retained["id"],
        "series_id": retained["series_id"],
        "book_number": retained["book_number"],
        "position": retained["position"],
        "version": new_version,
        "chapters": len(chapters_data),
    }


def _remove_book_render_dir(book_id: str) -> None:
    """Remove the durable render directory ``RENDER_ROOT/book-<book_id>/``.

    Uses the canonical render-root layout (``RENDER_ROOT`` read from the
    environment at call time, identical to ``get_render_root``), containment-
    checks the directory so Replace can NEVER delete another book's directory,
    and tolerates missing derived files (``ignore_errors=True``).

    Runs after Replace's DB transaction has committed, so a rolled-back
    replacement never loses a directory whose rows were restored.  Imported
    lazily because ``app.pipeline.tts_integration`` imports this module
    (``export_annotated_script``), which would otherwise be an import cycle.
    """
    from app.pipeline.tts_integration import get_render_root

    render_root = get_render_root()
    root_abs = os.path.realpath(render_root)
    render_dir = os.path.join(render_root, f"book-{book_id}")
    dir_abs = os.path.realpath(render_dir)

    # Never delete the render root itself or anything outside it.
    if dir_abs == root_abs:
        return
    try:
        if os.path.commonpath((root_abs, dir_abs)) != root_abs:
            return
    except ValueError:
        return

    if os.path.isdir(dir_abs):
        shutil.rmtree(dir_abs, ignore_errors=True)
