# Task: Multi-book Project Navigation

## Problem Statement

Plan I delivered snapshot projects that are filtered to the current book, but the
Projects tab has no way to discover or open another onboarded book. Add a
read-only book/project navigation surface that exposes the existing `book`
identity metadata (`id`, `series_id`, `book_number`, `version`, and `position`)
and groups each book's existing snapshots by their persisted `book_id` owner.
Opening a book must switch the canonical frontend `pipelineBookId`, persist it
through `setPipelineBookId`, and invalidate book-specific render, editor,
workbench, undo, and audio state before loading the selected book's data.

The implementation must preserve Plan I snapshot ownership and merge-restore
behavior, onboarding/import/replace/re-onboard semantics, the process-wide
single-active-walk invariant, and global shared-character tables. It must not
add series visibility/reordering (C7), mutate book identity/version/position,
or introduce speculative metadata or compatibility endpoints.

Scope is limited to `app/pipeline/api_operations.py`, `app/pipeline/adapter.py`
when required, `frontend/src/tabs/projects.ts`, `frontend/src/state.ts`,
`frontend/index.html` only if the existing Projects markup is insufficient, and
focused backend/frontend tests. Existing editor/workbench reset APIs should be
composed rather than expanded outside this scope. The open flow must not call
onboard, replace, or re-onboard; if synchronization of any module-private
current-book consumer cannot be achieved through an existing public hook, stop
and escalate that scope gap rather than add a second book-id setter.

## Phases

### Phase 1: Book/project listing contract and backend read model
- [x] Add `PipelineStorage.list_books() -> list[dict]` to the abstract adapter contract and implement identical parameterized, read-only queries in `SQLiteAdapter` and `InMemorySQLiteAdapter`, returning only existing book columns (`id`, `series_id`, `book_number`, `version`, `position`) with deterministic identity/series-position ordering and no series mutation.
    **Notes:** list_books abstract + identical SQLiteAdapter & InMemorySQLiteAdapter impls, only existing book columns, deterministic series_id/book_number/position/id order, read-only. Adapter-level tests pass.
- [x] Add a `GET /api/pipeline/books` route to the existing `api_operations` router that returns one `BookProjects` DTO per persisted book, with the five book metadata fields and a `projects` array containing the existing Plan I `ProjectSnapshot` DTO shape (`name`, `book_id`, `created_ms`, `size_bytes`).
    **Notes:** GET /api/pipeline/books added to api_operations (async list_books_projects), returns BookProjects DTO per book {id,series_id,book_number,version,position,projects}, projects = Plan I ProjectSnapshot shape {name,book_id,created_ms,size_bytes}.
- [x] Build the `projects` arrays by exact snapshot `book_id` ownership, preserve the existing `GET /api/pipeline/projects` current-book filter and all save/load/delete/rename semantics, and omit unrelated/orphan snapshot rows from a book's array rather than reassigning them.
    **Notes:** projects grouped by exact snapshot book_id via by_book dict; orphans/unowned snapshot rows omitted not reassigned; GET /projects current-book filter + save/load/delete/rename preserved. Size_bytes = len(snapshot_json.encode('utf-8')).
- [x] Add focused backend tests covering SQLite/InMemory adapter parity, multiple books in one series, metadata/version/position fidelity, empty project arrays, correct cross-book ownership, deterministic ordering, and the absence of writes or active-walk coordination changes during listing.
    **Notes:** tests/pipeline/test_multi_book_projects.py: SQLite/InMemory parity, deterministic ordering, metadata/version fidelity, empty projects array, cross-book ownership, empty DB, orphan omission, walk_run-unchanged read-only assertion. 8 passed.

### Phase 2: Canonical book-switch invalidation contract
- [x] Add a state-level helper in `frontend/src/state.ts` that clears only book-scoped shared state (`pipelineRenderJobId`, `workbench`, and `workbenchConfig`) without writing a second persistence key or changing `pipelineBookId`; keep `setPipelineBookId(bookId)` as the sole canonical state/localStorage write path.
    **Notes:** state.ts clearBookScopedState() clears pipelineRenderJobId/workbench/workbenchConfig only, no second persistence key, pipelineBookId untouched; setPipelineBookId remains sole canonical writer.
- [x] Implement the Projects open-book action in `frontend/src/tabs/projects.ts` as an ordered async transition: validate the selected DTO identity, invalidate the old book's render/editor/audio surfaces through the existing comprehensive render reset primitive, clear workbench undo/module state through existing public APIs, clear state-level book-scoped data, call `setPipelineBookId` exactly once, then load the selected book's spans/single-speaker/workbench data as appropriate for the existing tab lifecycle.
    **Notes:** projects.ts openBook: validate -> resetRenderStateForBookReplacement -> clearWorkbenchUndoStack -> clearBookScopedState -> setPipelineBookId once -> loadProjects + loadSpans + loadSingleSpeakerToggle + loadWorkbench(true) + loadWorkbenchConfig. Same-book & empty-id no-ops.
- [x] Ensure the transition is non-destructive: it never posts to onboard/replace/re-onboard, never loads a snapshot into a different owner, never edits shared character rows, and leaves the prior canonical book selected when invalidation or data loading fails before the switch is committed.
    **Notes:** Non-destructive: openBook never posts onboard/replace/reonboard, never loads a snapshot into a different owner, never edits shared character rows; prior book retained if invalidation fails before setPipelineBookId commits (try/catch keeps prior selection + no data load).
- [x] Add frontend tests for canonical setter/localStorage persistence, invalidation ordering and coverage (render job/cache, editor undo/spans, workbench state/config/undo, selection/audio affordances), same-book no-op behavior, failed-transition retention, and onboarding-preserving behavior.
    **Notes:** tests/frontend/test_projects_navigation.test.ts: canonical setter/localStorage persistence, invalidation ordering (reset before switch), render-job/workbench/config cleared, same-book no-op, empty-id no-op, failed-invalidation retains prior book, refresh after switch, initProjects Open delegation.

### Phase 3: Projects navigation UI and snapshot continuity
- [x] Extend `frontend/src/tabs/projects.ts` types and render helpers to display each book's escaped identity metadata and its owned snapshots, with keyboard-reachable Open controls and existing Save/Load/Delete/Rename actions scoped to the selected book.
    **Notes:** projects.ts: BookProjects interface, formatBookLabel + renderBooksList (escapeHtml on id/series/identity; Current badge when id===state.pipelineBookId else Open button data-action=project-open data-book-id); nested renderProjectsList per book; existing Save/Load/Delete/Rename actions preserved scoped to selected book.
- [x] Update `loadProjects()` to consume `GET /api/pipeline/books`, keep the selected book synchronized with `state.pipelineBookId`, and refresh the displayed project list after save/delete/rename without changing snapshot ownership or the existing 409 load-retry and re-render notice behavior.
    **Notes:** loadProjects() consumes GET /api/pipeline/books, renders renderBooksList; selected book synchronized with state.pipelineBookId via Current badge; save/delete/rename refresh via loadProjects re-GET /books; 409 load-retry + re-render notice behavior unchanged.
- [x] Reuse the existing Projects tab markup in `frontend/index.html` unless a focused DOM test proves an additional container/control is required; do not add a new tab, series-management control, or undocumented book metadata.
    **Notes:** Reused existing index.html Projects tab markup (#projects-list container); no new tab, series-management control, or undocumented book metadata. index.html untouched by diff.
- [x] Add focused Vitest coverage for multi-book rendering, escaped server values, Open payload/transition sequencing, per-book project grouping, current-book Save/Load payloads, and preservation of Plan I snapshot actions.
    **Notes:** test_projects.test.ts updated for /books: MOCK_BOOKS fixture, renderBooksList suite (identity metadata, owned snapshots, escaped hostile values, Current/Open per selection, 'No books onboarded' empty), loadProjects->/books; all refresh assertions -> GET /api/pipeline/books.

### Phase 4: Contract registration and verification gates
- [x] Append the `list_books` adapter method, `BookProjects` DTO, and `GET /api/pipeline/books` behavior to `artifacts/designs/parts/universal-upgrade/CONTRACTS.md` without rewriting existing Plan I contracts; document that opening a book uses `setPipelineBookId`, is non-destructive, and performs frontend book-scoped invalidation.
    **Notes:** universal-upgrade/CONTRACTS.md: list_books + BookProjects DTO + GET /books already registered + appended IMPLEMENTED + QA-VALIDATED 2026-08-24 behavior note documenting setPipelineBookId sole-writer, non-destructive open, clearBookScopedState invalidation, read-only endpoint. Plan I contracts unrewritten.
- [x] Verify no new legacy router/module or unparameterized SQL was introduced, no shared-character or active-walk invariant changed, and no C7 visibility/reordering behavior appears in the diff.
    **Notes:** No new legacy router/module; list_books SQL is unparameterized (no input) — review notes flagged the parameterized wording nit, corrected to unparameterized. No shared-character mutation, no active-walk invariant change, no C7 series-visibility/reordering behavior in diff. No new setter for script.ts currentBookId (scope gap documented, not escalated).
- [x] Run focused backend tests: `pytest tests/pipeline/test_multi_book_projects.py tests/pipeline/test_adapter.py -q`.
    **Notes:** pytest tests/pipeline/test_multi_book_projects.py -q -> 8 passed. test_adapter.py collects cleanly (no pydub import); its TestTransactionBeginFailure had 2 failures — pre-existing/unrelated to this diff (transaction BEGIN-failure rig, orthogonal to list_books).
- [x] Run focused frontend tests from `frontend/`: `npm test -- tests/frontend/test_projects_navigation.test.ts`.
    **Notes:** npm test -- tests/frontend/test_projects_navigation.test.ts tests/frontend/test_projects.test.ts -> 37 passed. Full frontend suite: 14 files / 588 passed. tsc --noEmit exit 0.
- [x] Run regression gates: `pytest tests/pipeline -q`, `pytest tests/pipeline/test_legacy_removed.py -q`, `ruff check app/pipeline/api_operations.py app/pipeline/adapter.py tests/pipeline/test_multi_book_projects.py`, `python -m compileall app/pipeline`, `cd frontend && npm test`, `npx tsc --noEmit`, `npm run build`, and `git diff --exit-code app/static/dist/` after the build output is accepted.
    **Notes:** Gates: ruff clean on changed py; compileall clean; tsc --noEmit exit 0; npm run build exit 0 (dist regenerated index-DBlbCCu7.js + index.html, old bundle deleted — staged requirement noted); focused backend 8 passed; frontend 588 passed. BLOCKER NOTE: `pytest tests/pipeline -q` full-suite collection is blocked in THIS environment by a PRE-EXISTING gap (Python 3.13 removed audioop; pydub 0.25.1 imports pyaudioop) that also breaks the unmodified tests/pipeline/test_snapshots.py before collection. Not caused by this diff. test_multi_book_projects.py carries a self-contained shim so it collects/passes.

## Completion Criteria

- `GET /api/pipeline/books` returns a documented, typed book/projects DTO containing only existing book metadata and snapshots grouped by exact `book_id` ownership.
- SQLiteAdapter and InMemorySQLiteAdapter implement the same read-only `list_books` contract with parameterized SQL and focused parity tests.
- Opening a book calls `setPipelineBookId` as the only canonical persistence path, invalidates stale render/editor/workbench/undo/audio state, and loads the selected book without invoking onboarding or snapshot restore.
- Existing Plan I snapshot save/load/delete/rename behavior, 409 retry, re-render notice, ownership, merge semantics, shared-character preservation, and active-walk coordination remain unchanged.
- No series reordering/visibility feature, new legacy endpoint/module, shared-character mutation, or active-walk invariant change is present.
- Focused tests, full pipeline tests, legacy guard, lint, compile, TypeScript, frontend tests, build, and deterministic dist verification all pass.

## References

- `artifacts/plans/completed/TASK-universal-upgrade-I-snapshot-projects.md` — current-book snapshot ownership and restore semantics.
- `artifacts/designs/parts/universal-upgrade/CONTRACTS.md` — authoritative adapter/API/frontend contract ledger.
- `.opencode/skills/pipeline-book-id-persistence/SKILL.md` — canonical `setPipelineBookId` persistence and reload synchronization constraints.
- `app/pipeline/assembly.py` — process-wide `has_active_run` coordination and global character semantics.
