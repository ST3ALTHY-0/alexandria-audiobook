"""Pipeline API — Onboarding endpoints.

Provides HTTP endpoints for onboarding EPUBs and re-onboarding books:
- POST /api/pipeline/onboard — accept an EPUB, extract text, populate spine
- POST /api/pipeline/reonboard — clear walk outputs, bump version

Also owns the production storage singleton (``_storage`` / ``_get_production_storage``)
and the ``get_storage`` FastAPI dependency, since onboarding is the primary
producer of storage instances.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.pipeline.adapter import PipelineStorage, SQLiteAdapter
from app.pipeline.assembly import get_book_version, has_active_run, reonboard_book
from app.pipeline.extract import extract_epub_text
from app.pipeline.populate import populate_spine
from app.pipeline.tts_integration import get_render_root
from app.pipeline.walks.runner import reconcile_and_replay


def _write_bytes(path: str, content: bytes) -> None:
    """Persist uploaded bytes to *path* with a blocking write (off the event loop)."""
    with open(path, "wb") as f:
        f.write(content)


# Retry-After advertized when re-onboard is blocked by an active walk
# (CONTRACTS.md contention contract — consistent with the app-level 503 mapping).
_REONBOARD_CONTENTION_RETRY_AFTER_SECONDS = 5


def _reject_if_walk_active(storage: PipelineStorage) -> None:
    """Reject book replacement while any walk writer is pending or running."""
    if has_active_run(storage):
        raise HTTPException(
            status_code=503,
            detail=(
                "A walk is still active — cancel it and retry "
                f"after {_REONBOARD_CONTENTION_RETRY_AFTER_SECONDS}s"
            ),
            headers={"Retry-After": str(_REONBOARD_CONTENTION_RETRY_AFTER_SECONDS)},
        )


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class ReonboardRequest(BaseModel):
    """Request body for POST /api/pipeline/reonboard."""

    book_id: str


# ---------------------------------------------------------------------------
# Dependency injection — overridable in tests
# ---------------------------------------------------------------------------

# Module-level singleton for production use.
_storage: PipelineStorage | None = None


def _get_production_storage() -> PipelineStorage:
    """Lazily create and return the production SQLiteAdapter singleton.

    Startup-only side effects on first acquisition: stale running
    render_job/walk_run rows are flipped to ``interrupted`` and every
    newly-interrupted ``walk_run``'s durable undo journal is replayed and
    cleared before any admission (``reconcile_and_replay``, which also covers
    the fresh-heartbeat crash gap), then manifests are rebuilt for completed
    render_job rows whose run dir exists and artifact-missing jobs are
    flagged (``rebuild_manifests``).
    """
    global _storage
    if _storage is None:
        db_path = os.environ.get("PIPELINE_DB_PATH", "./data/pipeline.db")
        adapter = SQLiteAdapter(db_path)
        adapter.init_db()
        # Startup-only reconciliation + journal replay (contract rule #5 / Plan S
        # P5-S3): one pass flips stale running render_job/walk_run rows to
        # interrupted BEFORE the API serves any request, then replays and clears
        # every interrupted walk_run's undo journal. ``start_of_day=True`` also
        # flips any still-``running`` row (fresh-heartbeat crash gap: nothing can
        # be live at first acquisition). Runs once, on first acquisition, before
        # any request can be handled or replacement writer admitted.
        reconcile_and_replay(adapter, start_of_day=True)
        # Startup-only manifest rebuild (contract rule #3 — rows = truth,
        # manifest = derived): regenerate manifest.json for completed jobs
        # whose run dir exists and flag artifact-missing jobs.  Runs AFTER
        # reconciliation so freshly-interrupted rows are never rebuilt.
        # RENDER_ROOT is read from the environment at call time.
        adapter.rebuild_manifests(get_render_root())
        _storage = adapter
    return _storage


def get_storage() -> PipelineStorage:
    """FastAPI dependency: return the pipeline storage adapter."""
    return _get_production_storage()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


# ---------------------------------------------------------------------------
# POST /api/pipeline/onboard
# ---------------------------------------------------------------------------


@router.post("/onboard")
async def onboard_epub(
    file: UploadFile = File(...),
    storage: PipelineStorage = Depends(get_storage),
) -> dict:
    """Accept an EPUB file, extract text, populate the spine, return book_id.

    A leftover interrupted-run undo journal from a prior crash is replayed at
    storage acquisition (``reconcile_and_replay``) before any admission
    proceeds, so a cancelled/interrupted run's journal is fully replayed before
    this new book is created. A walk still active (pending/running) is rejected
    with HTTP 503 + ``Retry-After`` (contention contract retained). The uploaded
    file is saved to a temporary location, then processed through
    extract_epub_text and populate_spine.
    """
    if not file.filename or not file.filename.lower().endswith(".epub"):
        raise HTTPException(status_code=400, detail="File must be an EPUB (.epub)")

    # The server-side global walk invariant is authoritative; do not begin
    # extraction/population while any book has a writer in flight.
    _reject_if_walk_active(storage)

    # Save uploaded file to temp location
    tmp_dir = tempfile.mkdtemp(prefix="pipeline_onboard_")
    # Never use the client-controlled filename as a filesystem path.  A unique
    # server-generated name also keeps cleanup confined to ``tmp_dir``.
    tmp_path = os.path.join(tmp_dir, f"{uuid.uuid4()}.epub")
    try:
        content = await file.read()
        await asyncio.to_thread(_write_bytes, tmp_path, content)

        # Generate a book_id
        book_id = str(uuid.uuid4())

        # Extract EPUB text
        try:
            result = extract_epub_text(tmp_path, book_id, storage)
        except Exception as exc:  # noqa: BLE001 — EPUB extraction may raise many types; mapped to HTTP 400
            raise HTTPException(
                status_code=400, detail=f"Failed to extract EPUB: {exc}"
            )

        # Populate spine
        try:
            populate_spine(
                result["series_id"],
                result["book_id"],
                result["chapters"],
                storage,
            )
        except Exception as exc:  # noqa: BLE001 — spine population may raise many types; mapped to HTTP 500
            raise HTTPException(
                status_code=500, detail=f"Failed to populate spine: {exc}"
            )

        return {
            "book_id": book_id,
            "series_id": result["series_id"],
            "chapters": len(result["chapters"]),
        }
    finally:
        # Clean up temp file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        if os.path.exists(tmp_dir):
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# POST /api/pipeline/reonboard
# ---------------------------------------------------------------------------


@router.post("/reonboard")
async def reonboard(
    request: ReonboardRequest,
    storage: PipelineStorage = Depends(get_storage),
) -> dict:
    """Re-onboard a book: clear walk outputs, bump version.

    Coordinates with the global walk controller (CONTRACTS.md "Onboarding /
    re-onboarding / book-switching coordination"): if any ``walk_run`` row is
    active (``pending``/``running``), replacement data may NOT
    become current while that writer could still run, so the synchronous path
    does NOT clear/replace and instead returns a documented conflict —
    HTTP 503 + ``Retry-After`` (consistent with the app-level contention
    mapping) — rather than hanging the request thread. The frontend cancels the
    active walk and awaits terminal cleanup before retrying.

    Existence is checked first: unknown books map to 404. Only after
    confirming the book exists and no active run remains does
    ``reonboard_book`` clear outputs / bump ``version``.

    Storage acquisition re-runs ``reconcile_and_replay`` before admission, so a
    leftover interrupted-run undo journal is fully replayed before this
    replacement clears outputs. The HTTP ``503`` + ``Retry-After`` contention
    contract is retained unchanged.
    """
    # 404 (unknown book) is preserved and checked BEFORE the active-run check.
    try:
        get_book_version(request.book_id, storage)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # 503 + Retry-After contention (global replacement while a writer is active).
    _reject_if_walk_active(storage)

    try:
        new_version = reonboard_book(request.book_id, storage)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "book_id": request.book_id,
        "version": new_version,
        "status": "reonboarded",
    }
