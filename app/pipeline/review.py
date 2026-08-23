"""Unified review queue for the audiobook pipeline.

Owns the honest-union review queue: the junction live query
(low-confidence character junction records in the [0.5, 0.7) band) plus
pending ``walk_review_item`` rows recorded by walks 2g/2h/2i (ids prefixed
``walkitem:``), with an optional ``walk_name`` filter.  Accept/reject/
override dispatch on the ``walkitem:`` prefix; walk-side actions
transactionally restore ``prior_value`` (undo) and mark the row resolved.
Also exposes ``supersede_targets``, the completion-time per-target
supersede runs in a walk's FINAL transaction.

Usage::

    from app.pipeline.adapter import PipelineStorage
    from app.pipeline.review import ReviewManager

    storage: PipelineStorage = ...
    manager = ReviewManager(storage)
    items = manager.get_review_items("book-001")
    manager.accept_review_item("character_book:c1:b1")
    manager.resolve_review_action("reject", "walkitem:run-1:voice_profile:c1")
"""

from __future__ import annotations

import json
from typing import Any

from app.pipeline.adapter import PipelineStorage
from app.pipeline.ledger import CharacterLedger

# ---------------------------------------------------------------------------
# Confidence thresholds
# ---------------------------------------------------------------------------

#: Minimum confidence (inclusive) for items to appear in the review queue.
REVIEW_CONFIDENCE_MIN = 0.5
#: Maximum confidence (exclusive) for items to appear in the review queue.
REVIEW_CONFIDENCE_MAX = 0.7
#: Confidence value set when a review item is accepted.
CONFIDENCE_ACCEPTED = 1.0
#: Confidence value set when a review item is rejected.
CONFIDENCE_REJECTED = 0.0


# ---------------------------------------------------------------------------
# Item-ID helpers
# ---------------------------------------------------------------------------

# Format: "{junction_table}:{character_id}:{related_entity_id}[:{relation_type}]"
# Examples:
#   "character_book:c1:b1"
#   "character_scene:c1:sc1:present"
#   "character_span:c1:sp1:speaker"
#   "character_series:c1:s1"

_VALID_JUNCTION_TABLES = frozenset(
    {"character_book", "character_scene", "character_span", "character_series"}
)

# Maps junction table → (related_entity_column, table PK columns for UPDATE)
_JUNCTION_META: dict[str, dict[str, Any]] = {
    "character_book": {"related_col": "book_id", "extra_cols": []},
    "character_scene": {
        "related_col": "scene_id",
        "extra_cols": ["relation_type"],
    },
    "character_span": {
        "related_col": "span_id",
        "extra_cols": ["relation_type"],
    },
    "character_series": {"related_col": "series_id", "extra_cols": []},
}


def _parse_item_id(item_id: str) -> tuple[str, str, str, str | None]:
    """Parse an item_id into its junction identity components.

    Raises ``ValueError`` if the format is invalid.
    """
    parts = item_id.split(":")
    if len(parts) not in (3, 4):
        raise ValueError(
            f"Invalid item_id format: {item_id!r}. "
            f"Expected '{{table}}:{{character_id}}:{{entity_id}}[:{{relation_type}}]'"
        )
    junction_table, character_id, related_entity_id = parts[:3]
    if junction_table not in _VALID_JUNCTION_TABLES:
        raise ValueError(
            f"Unknown junction table: {junction_table!r}. "
            f"Must be one of {sorted(_VALID_JUNCTION_TABLES)}"
        )
    relation_type = parts[3] if len(parts) == 4 else None
    if relation_type is None and _JUNCTION_META[junction_table]["extra_cols"]:
        raise ValueError(
            f"Invalid item_id format: {item_id!r}. "
            f"{junction_table} items require relation_type"
        )
    if relation_type is not None and not _JUNCTION_META[junction_table]["extra_cols"]:
        raise ValueError(
            f"Invalid item_id format: {item_id!r}. "
            f"{junction_table} items do not accept relation_type"
        )
    return junction_table, character_id, related_entity_id, relation_type


def _make_item_id(
    junction_table: str,
    character_id: str,
    related_entity_id: str,
    relation_type: str | None = None,
) -> str:
    """Build an item_id from its components."""
    item_id = f"{junction_table}:{character_id}:{related_entity_id}"
    if relation_type is not None:
        item_id = f"{item_id}:{relation_type}"
    return item_id


def supersede_targets(
    storage: PipelineStorage,
    *,
    book_id: str,
    run_id: str,
    kind: str,
    target_ids: list[str],
) -> int:
    """Mark prior pending walk_review_item rows as superseded (completion-time).

    Runs in ONE journal-covered savepoint (the walk's FINAL transaction per
    contract rule #9): the affected rows are pre-SELECTed and every
    pending->superseded transition is captured per-row under THIS run's id
    before a batch ``UPDATE`` inside the ``review_supersede`` savepoint, so no
    unjournaled review-status change can survive a failed/cancelled run — a
    rollback reverts the flips and discards the entries, while a committed
    supersede is replayable (after-image CAS restores pending only if the flip
    landed and this run is later rolled back).  Scoped to rows of the
    same *kind* whose ``target_id`` is in *target_ids*, still ``pending``, and
    belonging to a different run (``run_id <> ?``).
    An empty *target_ids* is a no-op — nothing was regenerated this run, so
    nothing is superseded.  On failure or cancel the walk never reaches this
    helper, so nothing is superseded there either.

    Returns the number of rows updated.
    """
    if not target_ids:
        return 0

    placeholders = ", ".join("?" for _ in target_ids)
    where = (
        "book_id = ? AND run_id <> ? AND status = 'pending' AND kind = ? "
        f"AND target_id IN ({placeholders})"
    )
    args = (book_id, run_id, kind, *target_ids)

    # Pre-SELECT the exact rows this completion-time supersede will flip, so
    # each pending->superseded transition is journaled under THIS run's id.  On
    # replay, the after-image CAS restores them to pending only if the supersede
    # actually landed (i.e. this run was cancelled AFTER committing supersede),
    # while on cancellation INSIDE a savepoint the ROLLBACK already reverts the
    # flip and discards the journal entry.  No unjournaled review-status change
    # can survive a failed/cancelled run.
    rows = storage.execute_query(
        f"SELECT id, prior_value FROM walk_review_item WHERE {where}",
        args,
    )
    if not rows:
        with storage.savepoint("review_supersede"):
            return storage.execute_update(
                f"UPDATE walk_review_item SET status = 'superseded' WHERE {where}",
                args,
            )

    with storage.savepoint("review_supersede"):
        for item in rows:
            status_after = storage.execute_query(
                "SELECT status FROM walk_review_item WHERE id = ?",
                (item["id"],),
            )
            if not status_after or status_after[0]["status"] != "pending":
                continue
            storage.capture_undo(
                run_id,
                "walk_review_item",
                "update",
                row_pk=item["id"],
                before_json=json.dumps(
                    {
                        "id": item["id"],
                        "prior_value": item["prior_value"],
                        "status": "pending",
                    }
                ),
                after_json=json.dumps(
                    {
                        "id": item["id"],
                        "prior_value": item["prior_value"],
                        "status": "superseded",
                    }
                ),
            )
        return storage.execute_update(
            f"UPDATE walk_review_item SET status = 'superseded' WHERE {where}",
            args,
        )


class ReviewItemNotFoundError(LookupError):
    """Raised when an action targets a well-formed but unknown review item.

    The API layer maps this to HTTP 404.  ``ValueError`` continues to map to
    400 — this distinct type exists so "the id is malformed" (400) and "the
    id is fine but no such item exists" (404) stay distinguishable.
    """


# Maps walk item kind → the target-row write used by reject/override.
# Walk items have NO ``new_value`` column, so the value goes into the target
# row; the mapping is keyed by *kind* (CHECK-constrained, never interpolated
# from ``target_table``) — the validated allowlist for value writes.
_WALK_TARGET_WRITES: dict[str, str] = {
    "voice_profile": (
        "UPDATE character_metadata SET value = ? "
        "WHERE character_id = ? AND key = 'voice_profile'"
    ),
    "voice_assignment": ("UPDATE character SET voice_assignment_id = ? WHERE id = ?"),
    "instruction": ("UPDATE span SET instruct = ? WHERE id = ?"),
}


# ---------------------------------------------------------------------------
# Contextual review - +/-2 neighboring spans (Plan J, Phase 6 / DD UX #5)
# ---------------------------------------------------------------------------

#: Maximum number of neighboring span texts to include per side.
NEIGHBOR_WINDOW = 2


def _load_spans_in_presentation_order(
    book_id: str, storage: PipelineStorage
) -> list[dict[str, Any]]:
    """Load a book's spans in presentation order via the span_presentation VIEW.

    Identical join chain to walk_2i's ``_load_spans_in_presentation_order``
    (the VIEW is global; the book scope comes from ``bc.parent_id = ?``) -
    returns ``[{id, text}]`` ordered by ``sp.global_index``.  Called ONCE per
    ``get_review_items`` invocation and shared across every item, so the
    contextual-review enrichment costs exactly one query regardless of how
    many items the queue holds.
    """
    rows = storage.execute_query(
        """
        SELECT sp.id, s.text
        FROM span_presentation sp
        JOIN span s ON sp.id = s.id
        JOIN paragraph_span ps ON ps.child_id = sp.id
        JOIN scene_paragraph sp_edge ON sp_edge.child_id = ps.parent_id
        JOIN chapter_scene cs ON cs.child_id = sp_edge.parent_id
        JOIN book_chapter bc ON bc.child_id = cs.parent_id
        WHERE bc.parent_id = ?
        ORDER BY sp.global_index
        """,
        (book_id,),
    )
    return [{"id": row["id"], "text": row["text"] or ""} for row in rows]


def _resolve_span_reference(item: dict[str, Any]) -> str | None:
    """Return the span id an item references, or ``None`` when it has none.

    Phase 6 resolution rules:
      - ``character_span`` junction items use ``related_entity_id`` (the
        span_id - set by ``_get_span_review_items`` as ``csp.span_id``);
      - walk items of kind ``instruction`` use ``target_id`` (walk 2i
        records instruction items whose target row is the span, so
        target_id IS the span reference);
      - every other kind (character_book, character_scene, voice_profile,
        voice_assignment) returns ``None`` - empty before/after lists.
    """
    if item.get("junction_table") == "character_span":
        return item.get("related_entity_id")
    if item.get("kind") == "instruction":
        return item.get("target_id")
    return None


def _build_neighbors(
    span_ref: str | None,
    ordered_spans: list[dict[str, Any]],
    *,
    window: int = NEIGHBOR_WINDOW,
) -> dict[str, list[str]]:
    """Build ``{before, after}`` neighbor-span-TEXT lists for *span_ref*.

    *ordered_spans* is the book's spans in presentation order (the
    span_presentation VIEW chain).  When *span_ref* is ``None`` or not
    present in the order, both lists are empty.  Otherwise *before* holds up
    to *window* span texts immediately preceding the span and *after* up to
    *window* immediately following, preserving presentation order - the
    FIRST span yields empty before, the LAST span empty after.
    """
    if span_ref is None:
        return {"before": [], "after": []}
    for idx, span in enumerate(ordered_spans):
        if span["id"] == span_ref:
            return {
                "before": [
                    s["text"] for s in ordered_spans[max(0, idx - window) : idx]
                ],
                "after": [s["text"] for s in ordered_spans[idx + 1 : idx + 1 + window]],
            }
    return {"before": [], "after": []}


class ReviewManager:
    """Manage the unified review queue for a book.

    Two halves: junction records (low-confidence ``character_book`` /
    ``character_scene`` / ``character_span`` / ``character_series`` rows in
    the [0.5, 0.7) band) and pending ``walk_review_item`` rows
    (``walkitem:``-prefixed ids).  ``get_review_items`` returns the union
    (junction items first, then walk items); actions dispatch on the prefix
    and walk-side accept/reject/override run transactionally with
    value-restore.

    Parameters
    ----------
    storage:
        An active ``PipelineStorage`` implementation.
    """

    def __init__(self, storage: PipelineStorage) -> None:
        self._storage = storage
        self._ledger = CharacterLedger(storage)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_review_items(
        self,
        book_id: str,
        walk_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the HONEST UNION of the review queue for *book_id*.

        Two halves, junction items first then walk items (no other sort):

        1. Junction live query — the existing low-confidence
           ``character_book`` / ``character_scene`` / ``character_span``
           records with confidence in [0.5, 0.7).  Each dict includes an
           ``item_id`` key encoding the junction table and primary key
           (``{junction_table}:{character_id}:{entity_id}`` — byte-identical
           to the pre-union format) for use with accept/reject/override.
        2. Walk items — pending ``walk_review_item`` rows recorded by walks
           2g/2h/2i in their per-unit transaction.  These carry the raw
           table columns (``kind``, ``target_table``, ``target_id``,
           ``prior_value``, ``created_ms`` — there is no confidence /
           human_override on walk items) and ``item_id`` of the form
           ``walkitem:{id}`` so actions can dispatch on the prefix.

        Parameters
        ----------
        book_id:
            The book whose review queue to retrieve.
        walk_name:
            Optional filter — restrict junction entries to those whose
            ``source`` column contains *walk_name* (LIKE ``%walk_name%``
            heuristic) and walk entries to those whose run's ``walk_name``
            (via ``walk_run``) contains *walk_name*.  Without the filter
            every pending walk item for the book is included.
        """
        items: list[dict[str, Any]] = []

        # 1) character_book — delegate to CharacterLedger (existing logic)
        ledger_items = self._ledger.get_review_items(book_id, walk_name=walk_name)
        for row in ledger_items:
            row["related_entity_id"] = book_id
            row["item_id"] = _make_item_id(
                "character_book", row["character_id"], book_id
            )
            items.append(row)

        # 2) character_scene — scenes belonging to this book
        items.extend(self._get_scene_review_items(book_id, walk_name))

        # 3) character_span — spans belonging to this book
        items.extend(self._get_span_review_items(book_id, walk_name))

        # 4) walk_review_item — pending walk items (union tail)
        items.extend(self._get_walk_review_items(book_id, walk_name))

        # 5) Contextual review (Plan J, Phase 6 / DD UX workflow #5): every
        #    item gains ``neighbors = {before, after}`` — up to 2 span TEXTS
        #    in presentation order around the item's span reference.  The
        #    spans are loaded ONCE per call and shared across all items; the
        #    enrichment rides through the GET /review/{book_id} response
        #    unchanged (api_review.py returns the manager output verbatim).
        if items:
            ordered_spans = _load_spans_in_presentation_order(book_id, self._storage)
            for item in items:
                item["neighbors"] = _build_neighbors(
                    _resolve_span_reference(item), ordered_spans
                )

        return items

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def accept_review_item(self, item_id: str) -> None:
        """Accept a review item — set confidence to 1.0 and protect it from regeneration.

        Parameters
        ----------
        item_id:
            Encoded as ``"{junction_table}:{character_id}:{entity_id}"``
            or, for scene/span rows, with ``:relation_type`` appended.
        """
        junction_table, character_id, related_entity_id, relation_type = _parse_item_id(
            item_id
        )
        meta = _JUNCTION_META[junction_table]
        related_col = meta["related_col"]

        where = f"character_id = ? AND {related_col} = ?"
        params: list[Any] = [character_id, related_entity_id]
        if relation_type is not None:
            where += " AND relation_type = ?"
            params.append(relation_type)
        updated = self._storage.execute_update(
            f"UPDATE {junction_table} SET confidence = {CONFIDENCE_ACCEPTED}, human_override = 1 "
            f"WHERE {where}",
            tuple(params),
        )
        if updated == 0:
            raise ReviewItemNotFoundError(item_id)

    def reject_review_item(self, item_id: str) -> None:
        """Reject a review item — set confidence to 0.0 and mark
        ``human_override = 1``.

        Parameters
        ----------
        item_id:
            Encoded as ``"{junction_table}:{character_id}:{entity_id}"``
            or, for scene/span rows, with ``:relation_type`` appended.
        """
        junction_table, character_id, related_entity_id, relation_type = _parse_item_id(
            item_id
        )
        meta = _JUNCTION_META[junction_table]
        related_col = meta["related_col"]

        where = f"character_id = ? AND {related_col} = ?"
        params: list[Any] = [character_id, related_entity_id]
        if relation_type is not None:
            where += " AND relation_type = ?"
            params.append(relation_type)
        updated = self._storage.execute_update(
            f"UPDATE {junction_table} SET confidence = {CONFIDENCE_REJECTED}, human_override = 1 "
            f"WHERE {where}",
            tuple(params),
        )
        if updated == 0:
            raise ReviewItemNotFoundError(item_id)

    def override_review_item(
        self,
        item_id: str,
        new_value: Any,
    ) -> None:
        """Override a review item — set ``human_override = 1``,
        ``confidence = 1.0``, and optionally update junction columns.

        Parameters
        ----------
            item_id:
                Encoded as ``"{junction_table}:{character_id}:{entity_id}"``
                or, for scene/span rows, with ``:relation_type`` appended.
        new_value:
            If a ``dict``, its keys are treated as column names to update
            on the junction row (e.g. ``{"relation_type": "speaker"}``).
            Non-dict values are ignored (only dict overrides are
            supported for column updates).
        """
        junction_table, character_id, related_entity_id, relation_type = _parse_item_id(
            item_id
        )
        meta = _JUNCTION_META[junction_table]
        related_col = meta["related_col"]
        allowed_extra = set(meta["extra_cols"])

        # Build SET clause
        set_parts: list[str] = [
            f"confidence = {CONFIDENCE_ACCEPTED}",
            "human_override = 1",
        ]
        params: list[Any] = []

        if isinstance(new_value, dict):
            for col, val in new_value.items():
                if col in allowed_extra:
                    set_parts.append(f"{col} = ?")
                    params.append(val)
                # Silently ignore unknown columns to avoid SQL injection

        params.extend([character_id, related_entity_id])
        where = f"character_id = ? AND {related_col} = ?"
        if relation_type is not None:
            where += " AND relation_type = ?"
            params.append(relation_type)

        updated = self._storage.execute_update(
            f"UPDATE {junction_table} SET {', '.join(set_parts)} WHERE {where}",
            tuple(params),
        )
        if updated == 0:
            raise ReviewItemNotFoundError(item_id)

    def resolve_review_action(
        self,
        action: str,
        item_id: str,
        new_value: Any = None,
    ) -> None:
        """Resolve a review *action* on *item_id*, dispatching on the id prefix.

        ``walkitem:`` ids take the walk-side branch (``_resolve_walk_item``);
        everything else takes the existing junction branch (accept/reject/
        override as before — junction ids carry NO literal ``junction:``
        prefix).  Malformed ids raise ``ValueError`` (400); well-formed ids
        with no matching row raise ``ReviewItemNotFoundError`` (404).
        """
        if item_id.startswith("walkitem:"):
            self._resolve_walk_item(action, item_id[len("walkitem:") :], new_value)
            return

        if action == "accept":
            self.accept_review_item(item_id)
        elif action == "reject":
            self.reject_review_item(item_id)
        elif action == "override":
            self.override_review_item(item_id, new_value)
        else:
            raise ValueError(f"Unknown review action: {action!r}")

    def _resolve_walk_item(
        self,
        action: str,
        item_id: str,
        new_value: Any,
    ) -> None:
        """Resolve a walk-side review item (``walkitem:`` id, prefix stripped).

        Accept keeps the walk's generated value (no target write); reject
        restores ``prior_value`` into the target row; override writes
        *new_value* into the target row.  All three mark the item row
        ``resolved``.  The target write + status update run inside ONE
        ``storage.transaction()`` so the pair commits atomically and rolls
        back together on failure.

        This is the HUMAN resolution branch — it sets the item ``resolved`` and
        is DISTINCT from automatic cancelled-run replay (which restores the
        run's captured writes). A human accept/reject/override and an
        auto-replayed cancelled-run write are separate, and both remain
        preserved and queryable in the item's status history.
        """
        row = self._storage.execute_query(
            "SELECT kind, target_id, prior_value "
            "FROM walk_review_item WHERE id = ? AND status = 'pending'",
            (item_id,),
        )
        if not row:
            raise ReviewItemNotFoundError(item_id)

        kind = row[0]["kind"]
        target_id = row[0]["target_id"]
        prior_value = row[0]["prior_value"]

        if action == "override" and new_value is None:
            raise ValueError(
                "override on a walkitem: requires a new_value to write "
                "into the target row"
            )

        write_sql = _WALK_TARGET_WRITES.get(kind)
        if write_sql is None:
            raise ValueError(f"Unsupported walk item kind: {kind!r}")

        if action == "accept":
            # keep the walk's generated value — no target write
            writes: list[tuple[str, tuple[Any, ...]]] = []
        elif action == "reject":
            writes = [(write_sql, (prior_value, target_id))]
        elif action == "override":
            writes = [(write_sql, (new_value, target_id))]
        else:
            raise ValueError(f"Unknown review action: {action!r}")

        with self._storage.transaction():
            for sql, params in writes:
                self._storage.execute_update(sql, params)
            self._storage.execute_update(
                "UPDATE walk_review_item SET status = 'resolved' WHERE id = ?",
                (item_id,),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_scene_review_items(
        self,
        book_id: str,
        walk_name: str | None,
    ) -> list[dict[str, Any]]:
        """Query character_scene junctions for scenes belonging to *book_id*."""
        walk_filter = ""
        params: list[Any] = [book_id]

        if walk_name is not None:
            walk_filter = " AND cs.source LIKE ?"
            params.append(f"%{walk_name}%")

        rows = self._storage.execute_query(
            f"""SELECT c.id AS character_id, c.name AS character_name,
                       'pending' AS status,
                       'character_scene' AS junction_table,
                       cs.confidence, cs.source AS walk_name,
                       cs.scene_id AS related_entity_id,
                       cs.relation_type,
                       'Low-confidence character-scene association' AS reason
                  FROM character c
                  JOIN character_scene cs ON c.id = cs.character_id
                  JOIN chapter_scene csc ON cs.scene_id = csc.child_id
                  JOIN book_chapter bc ON csc.parent_id = bc.child_id
                 WHERE bc.parent_id = ?
                    AND cs.confidence >= {REVIEW_CONFIDENCE_MIN}
                    AND cs.confidence < {REVIEW_CONFIDENCE_MAX}
                   {walk_filter}
                 ORDER BY cs.confidence""",
            tuple(params),
        )

        for row in rows:
            row["item_id"] = _make_item_id(
                "character_scene",
                row["character_id"],
                row["related_entity_id"],
                row["relation_type"],
            )
        return rows

    def _get_span_review_items(
        self,
        book_id: str,
        walk_name: str | None,
    ) -> list[dict[str, Any]]:
        """Query character_span junctions for spans belonging to *book_id*."""
        walk_filter = ""
        params: list[Any] = [book_id]

        if walk_name is not None:
            walk_filter = " AND csp.source LIKE ?"
            params.append(f"%{walk_name}%")

        rows = self._storage.execute_query(
            f"""SELECT c.id AS character_id, c.name AS character_name,
                       'pending' AS status,
                       'character_span' AS junction_table,
                       csp.confidence, csp.source AS walk_name,
                       csp.span_id AS related_entity_id,
                       csp.relation_type,
                       'Low-confidence character-span association' AS reason
                  FROM character c
                  JOIN character_span csp ON c.id = csp.character_id
                  JOIN paragraph_span pse ON csp.span_id = pse.child_id
                  JOIN scene_paragraph spe ON pse.parent_id = spe.child_id
                  JOIN chapter_scene csc ON spe.parent_id = csc.child_id
                  JOIN book_chapter bc ON csc.parent_id = bc.child_id
                 WHERE bc.parent_id = ?
                    AND csp.confidence >= {REVIEW_CONFIDENCE_MIN}
                    AND csp.confidence < {REVIEW_CONFIDENCE_MAX}
                   {walk_filter}
                 ORDER BY csp.confidence""",
            tuple(params),
        )

        for row in rows:
            row["item_id"] = _make_item_id(
                "character_span",
                row["character_id"],
                row["related_entity_id"],
                row["relation_type"],
            )
        return rows

    def _get_walk_review_items(
        self,
        book_id: str,
        walk_name: str | None,
    ) -> list[dict[str, Any]]:
        """Query pending ``walk_review_item`` rows for *book_id* (union tail).

        Walk items are the low-confidence artifacts recorded by walks 2g
        (kind ``voice_profile``), 2h (``voice_assignment``) and 2i
        (``instruction``) inside their per-unit transaction.  Each returned
        dict carries exactly the raw table columns — ``kind``,
        ``target_table``, ``target_id``, ``prior_value``, ``created_ms`` —
        with ``item_id`` of the form ``walkitem:{id}`` (there is no
        confidence/human_override on walk items).

        With *walk_name* the walk items are filtered through the honest
        data-model link ``run_id → walk_run.walk_name`` (set by
        ``run_walk``): ``JOIN walk_run ON wri.run_id = walk_run.run_id AND
        walk_run.walk_name LIKE ?``.  Without the filter every pending walk
        item for the book is included, regardless of walk_name.
        """
        walk_filter = ""
        if walk_name is not None:
            walk_filter = (
                " JOIN walk_run ON wri.run_id = walk_run.run_id"
                " AND walk_run.walk_name LIKE ?"
            )
            params: list[Any] = [f"%{walk_name}%", book_id]
        else:
            params = [book_id]

        rows = self._storage.execute_query(
            f"""SELECT wri.id AS item_id, wri.kind, wri.target_table,
                       wri.status,
                       wri.target_id, wri.prior_value, wri.created_ms
                  FROM walk_review_item wri
                 {walk_filter}
                 WHERE wri.book_id = ? AND wri.status = 'pending'""",
            tuple(params),
        )

        for row in rows:
            row["item_id"] = f"walkitem:{row['item_id']}"
        return rows
