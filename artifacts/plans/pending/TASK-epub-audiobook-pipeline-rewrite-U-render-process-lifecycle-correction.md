# Task: Render and Processing Lifecycle Correction

## Problem Statement
Plans R, S, and T serialize walks, provide durable cancellation rollback, and make
onboard/re-onboard/replace transactional, but the processing gate still knows only
about walks. A render can therefore overlap a walk or another render, and a
destructive mutation can race a render. Snapshot restore also performs its active
row check before `_apply_snapshot_merge` opens its `BEGIN IMMEDIATE` transaction,
leaving a time-of-check/time-of-use gap. Finally, the frontend can retain a render
job, audio surface, or cache after onboarding, re-onboarding, or snapshot restore;
snapshot restore must invalidate audio even when its artifact-based
`re_render_required` flag is false.

This plan extends the existing row-backed lifecycle without changing the TTS
surface or the durable walk undo-journal policy. It introduces one process-wide
processing-writer admission invariant: a walk cannot execute while any render is
active, a render cannot be admitted while any walk or render is active, and
onboard/re-onboard/replace/snapshot restore cannot mutate while either class is
active. Every authoritative check is made by the transaction that admits or
mutates the row.

## Dependencies

- Plan R: `TASK-epub-audiobook-pipeline-rewrite-R-global-walk-lock-cancellation-savepoint`
  (global walk gate, cancellation checkpoints, transaction ownership, frontend
  walk cleanup).
- Plan S: `TASK-epub-audiobook-pipeline-rewrite-S-explicit-cancellation-rollback-override`
  (walk undo replay and gate-held terminalization).
- Plan T: `TASK-epub-audiobook-pipeline-rewrite-T-onboarding-replace-import`
  (transactional onboard/import-as-new, re-onboard, replace, and existing replace
  render reset).

## Phases

### Phase 1: Extend the backend gate to every processing writer

- [x] Define the canonical active-processing query/helper in
    **Note:** Added has_active_processing_writer(storage) in app/pipeline/assembly.py adjacent to has_active_run: single SQL over walk_run + render_job rows with status IN ('pending','running') (UNION ALL ... LIMIT 1). Preserved has_active_run unchanged (walk-only, used by api_onboard/assembly destructive mutations). Added ActiveProcessingWriterError(RuntimeError) and ACTIVE_WRITER_RETRY_AFTER_SECONDS=5 (mirrors app.app _CONCURRENT_WRITE_RETRY_AFTER_SECONDS). Exception raised only inside the admitting BEGIN IMMEDIATE transaction; API maps to 503+Retry-After.
  `app/pipeline/assembly.py` (for example
  `has_active_processing_writer(storage) -> bool`) over persisted `walk_run` and
  `render_job` rows with statuses `pending` or `running`; preserve
  `has_active_run` compatibility for callers that need walk-only reporting, and
  define the exception/error mapping used by 503 contention responses.
- [x] Update `app/pipeline/walks/runner.py::WalkRunner.run_walk_reserved` and
    **Note:** In app/pipeline/walks/runner.py run_walk_reserved, inside the SAME BEGIN IMMEDIATE transaction as the existing pending->running transition, added a render-active gate query (SELECT 1 FROM render_job WHERE status IN ('pending','running') LIMIT 1). When an active render blocks, this reservation's own pending walk_run row is deterministically terminalized to status='failed' with error 'A render is already active (process-wide active-writer gate)' and {status:'failed'} returned WITHOUT executing. Preserved: the existing walk gate only blocks on walk_run status='running' (NOT pending), so the nine pre-reserved run-all batch siblings still execute sequentially; Plan S reconcile/replay, cancellation, terminalization, gate-held replay ordering all unchanged. Kept the existing walk-block error string 'Another walk is already running (global single-active-walk gate)' intact to avoid breaking existing tests. run_all_walks_reserved needs no change (delegates to run_walk_reserved per child and inherits behavior). Updated run_walk_reserved docstring to the combined invariant.
  `run_all_walks_reserved` so the existing `BEGIN IMMEDIATE` pending-to-running
  gate also rejects an active render, while still allowing the pending sibling
  reservations of the same run-all batch to execute sequentially; keep the Plan S
  reconcile/replay, cancellation, terminalization, and gate-held replay ordering
  unchanged.
- [x] Update `app/pipeline/api_export.py::render` to perform render admission,
    **Note:** In app/pipeline/api_export.py render(): wrapped render admission, active-writer validation (has_active_processing_writer inside the transaction), and the initial render_job row INSERT (status='running') in ONE storage.transaction() (BEGIN IMMEDIATE). ActiveProcessingWriterError raised on contention is mapped to HTTPException 503 + Retry-After:5 with NO job ID exposed and NO row inserted (transaction rolls back). Two concurrent render requests serialize on BEGIN IMMEDIATE — the second sees the first's running row and is rejected, so they cannot both insert an admitted active row. The provided job_id is only registered in the in-process _render_jobs dict AFTER the admitted row commits (cancel_event channel only). No row is inserted on the rejection path.
  active-writer validation, and the initial `render_job` row insertion in one
  `storage.transaction()` (`BEGIN IMMEDIATE`), rejecting active walks and
  renders before exposing a job ID; ensure concurrent render requests cannot both
  insert an admitted active row.
- [x] Harden `app/pipeline/api_export.py::_run_render_job`, `cancel_render`, and
    **Note:** Hardened app/pipeline/api_export.py: (1) _run_render_job success path now mirrors terminal 'completed' back into the render_job row via _mark_job_row_terminal (idempotent; never clobbers output_dir/artifact); added a finally BaseException guard — if the row is still pending|running after the try/except (KeyboardInterrupt/SystemExit escapes render_audiobook's except Exception), finalize it 'failed' with 'Render terminated by unexpected exception' so no active render row is stranded and the combined gate is always released. _render_jobs.pop(job_id, None) retained in finally. (2) cancel_render now uses the persisted render_job row status as source of truth: a row that is terminal returns already_finished; only an active (pending|running) row with a live in-process job is cancellable (sets cancel_event). Row-less legacy in-process entries keep the historical dict fallback. _render_jobs retained only as the cancel_event channel; row status is authoritative.
  terminal-row helpers so every success, cancellation, ordinary exception, and
  `BaseException` path leaves the persisted render row terminal and releases the
  combined gate; retain `_render_jobs` only as the in-process cancellation signal
  and make row status the source of truth for cleanup/observability.
- [x] Replace walk-only contention wording and admission assumptions in
    **Note:** Updated contract wording. app/pipeline/api_walks.py: run_walk and run_all_walks docstrings now describe the combined process-wide active-writer invariant (walks contend with renders) and both block-messages; behavior unchanged. app/pipeline/api_export.py render() docstring documents transactional admission + 503+Retry-After + no-job-ID-on-rejection. CONTRACTS.md: added 'Combined Processing-Writer Invariant (Plan U)' subsection to the walk contention ledger (walk + render mutual contention, admitting-transaction ownership, atomic acquisition/release, terminal-release ordering, HTTP retry, startup-only reconciliation; pending walk siblings still execute sequentially) and 'Render job lifecycle & admission (Plan U)' to the render/export ledger (transactional admission, terminal release on every path incl. BaseException, row-backed cancellation, HTTP retry). Existing walk error strings and walk behavior untouched.
  `app/pipeline/api_walks.py`, `app/pipeline/api_export.py`, and the two relevant
  `CONTRACTS.md` ledgers with the combined process-wide invariant, transaction
  ownership requirement, terminal-release ordering, and HTTP retry behavior.

### Phase 2: Make all destructive backend mutations transaction-authoritative

- [x] Update `app/pipeline/assembly.py::reonboard_book` and
    **Note:** P2-S1 was implemented by a prior dispatch and verified now. reonboard_book and replace_book_tree (app/pipeline/assembly.py) call has_active_processing_writer(storage) inside their `with storage.transaction():` block and raise ActiveProcessingWriterError (replacing old has_active_run/ActiveWalkError in-transaction check). app/pipeline/api_onboard.py: _reject_if_writer_active replaced _reject_if_walk_active (combined walk+render fast reject, 503 + Retry-After: 5 via _CONTENTION_DETAIL); onboard_epub/reonboard/replace_epub catch (ActiveProcessingWriterError, ConcurrentTransactionError) -> HTTPException 503 + Retry-After. Verified has_active_processing_writer (assembly.py:189) unions walk_run+render_job pending/running with LIMIT 1, rows = truth. Tests: test_reonboard/test_replace/test_api_replace 52 passed. No defect found; work sound.
  `replace_book_tree` to use the combined active-processing check after entering
  their existing `storage.transaction()`; update
  `app/pipeline/api_onboard.py::onboard_epub`, `reonboard`, and `replace_epub`
  so any fast reject is only an optimization and the authoritative mutation
  check remains inside the same `BEGIN IMMEDIATE` transaction, with 404,
  503, and `Retry-After` mappings preserved.
- [x] Update `app/pipeline/api_operations.py::_apply_snapshot_merge` to repeat
    **Note:** app/pipeline/api_operations.py: added `if has_active_processing_writer(storage): raise ActiveProcessingWriterConflict` as the FIRST statement inside _apply_snapshot_merge's `with storage.transaction():` block, before the first span/character mutation (closes the TOCTOU gap — the endpoint's early _book_has_active_runs check is only a fast reject). load_project_snapshot now wraps the _apply_snapshot_merge call in try/except ActiveProcessingWriterConflict -> HTTPException 409 with the same detail message ("Cannot restore while a walk or render is active for this book; retry after 5s") and same Retry-After: 5 header as the early check; the early 409 check, manifest merge, and _audio_reference_missing/re_render_required calc all unchanged. ActiveProcessingWriterConflict class (previously added ~line 489) docstring already described the in-transaction wiring and remains accurate. Tests: test_snapshots.py 67 passed (uses venv).
  the combined active walk/render check immediately inside its existing
  `with storage.transaction():` block before the first span/character mutation;
  make `load_project_snapshot` retain its early 409 response for the common case
  but map the in-transaction race failure to the same 409 + `Retry-After`
  contract, leaving the manifest merge and audio-reference calculation unchanged.
- [x] Add a row-backed active-render listing/cancellation surface in
    **Note:** app/pipeline/api_export.py: added GET /api/pipeline/render_jobs/{book_id} -> list_active_render_jobs. Pure row-backed SELECT over render_job WHERE book_id = ? AND status IN ('pending','running') ORDER BY created_ms, returning job_id/mode/status/error/output_dir/created_ms/started_ms/finished_ms (empty list when none). NEVER consults the in-process _render_jobs dict, so crash-survived jobs (rows present, dict absent) are discoverable. Docstring documents the cancel-then-poll contract: POST /cancel_render then poll GET /render_status/{job_id} (or re-list) until the row is terminal (completed/failed/cancelled/interrupted/expired). Route named for consistency with /render_status/{job_id}, /cancel_render, /export/jobs/{job_id}. artifacts/designs/parts/epub-audiobook-pipeline-rewrite/CONTRACTS.md 'Render job lifecycle & admission (Plan U)' gained an 'Active-render listing surface' bullet documenting the row-backed listing, crash-survival, and the cancel-then-poll-until-terminal contract.
  `app/pipeline/api_export.py` (or reuse an existing equivalent if present) that
  lets the frontend discover all pending/running render jobs for a book, including
  jobs not present in the current page's `_render_jobs` dictionary; document that
  cancellation is followed by polling until the row is terminal.
- [x] Preserve startup-only reconciliation semantics: do not treat startup
    **Note:** Verified (no code changes): (1) Startup reconciliation remains startup-only, not a per-request gate — reconcile_and_replay is called from api_onboard._get_production_storage with start_of_day=True (api_onboard.py:122) and from run_walk_reserved admission (runner.py:877, reconcile_stale=False at admission); both unchanged by this plan. (2) Render admission depends only on persisted rows — has_active_processing_writer and render() read walk_run/render_job tables, never in-memory dicts. (3) git diff --name-only confirms app/tts.py and app/app.py are NOT in the changed list (unchanged by this plan). Phase 1 P1-S4 _run_render_job terminalization/pop and render admission already row-backed.
  reconciliation as a per-request gate, do not make render admission depend on
  in-memory dictionaries, and do not modify `app/tts.py` or the unrelated
  `app/app.py` process-state subsystem.

### Phase 3: Cancel/await both writer classes and invalidate frontend render state

- [ ] Extend `frontend/src/tabs/editor-pipeline.ts` with a render cleanup helper
  that discovers active render rows for a book, sends `pipelineCancelRender` for
  each cancellable job, and polls `pipelineRenderStatus` until every discovered
  row is terminal; make timeout, status-fetch failure, and cancellation failure
  return an unsafe result rather than allowing a destructive action to proceed.
- [ ] Extend `frontend/src/tabs/script.ts::waitForWalkCleanup` or add a sibling
  `waitForProcessingCleanup` that awaits both walk cleanup and render cleanup
  before onboarding a new book, re-onboarding, or replacing the current book;
  preserve current identity, polling, and error UI when cleanup cannot be proven
  complete, and keep the existing confirmation and cancellation ordering.
- [ ] In `frontend/src/tabs/script.ts::handleOnboard` and `handleReonboard`, call
  the full cleanup before the request and call the generalized render reset only
  after successful mutation; in `handleReplace`, replace the walk-only wait with
  the combined wait while retaining `resetRenderStateForBookReplacement` after
  backend success.
- [ ] In `frontend/src/tabs/editor-pipeline.ts` and `frontend/src/state.ts`,
  centralize source-change invalidation so it clears
  `state.pipelineRenderJobId`, `_currentRenderJobId`, render polling/settlement,
  cached spans/review data, preview playback, download/play/export-M4B
  affordances, and stale completion responses; retain a compatibility wrapper
  for the existing replace reset symbol unless all call sites are migrated.
- [ ] In `frontend/src/tabs/projects.ts::loadProject`, perform combined
  cancel-and-await before posting snapshot restore, invalidate all render identity
  and cache state after a successful restore, and always show/re-arm the
  re-render-required UI regardless of the backend's artifact-presence
  `re_render_required` value; failed restore requests must not optimistically
  discard the current book/render state.

### Phase 4: Add cross-class race, transaction, and UI regression coverage

- [ ] Extend `tests/pipeline/test_runner.py` and `tests/pipeline/test_api.py` to
  prove a running render blocks walk admission, a running walk blocks render
  admission, two render admissions cannot overlap, terminal render cleanup
  releases the gate, and render exception/`BaseException` paths do not strand a
  `running` row; retain existing same-book/cross-book walk and Plan S replay
  assertions.
- [ ] Extend `tests/pipeline/test_reonboard.py`, `tests/pipeline/test_replace.py`,
  `tests/pipeline/test_api_replace.py`, and lifecycle coverage in
  `tests/pipeline/test_e2e_lifecycle.py` to prove pending/running render rows
  block onboard/re-onboard/replace, that the authoritative transaction check
  wins after any outside fast check, and that unrelated books remain untouched.
- [ ] Extend `tests/pipeline/test_snapshots.py` with direct `_apply_snapshot_merge`
  active-row tests and a transaction-race regression that places an active walk
  or render after the endpoint's preliminary check but before merge admission;
  assert no span/character mutation commits and the documented 409 + retry
  response remains stable for both `re_render_required` outcomes.
- [ ] Extend `frontend/tests/frontend/test_script.test.ts` and
  `frontend/tests/frontend/test_editor.test.ts` for render discovery,
  cancel-before-wait ordering, terminal polling, timeout/failure preservation,
  import-as-new/re-onboard/replace invalidation, stale render completion guards,
  and clearing of render identity/cache/UI surfaces.
- [ ] Extend `frontend/tests/frontend/test_projects.test.ts` to assert snapshot
  restore cancels and awaits both writer types, resets render state only after
  success, and requires a new render even when the API returns
  `re_render_required: false`; assert failed/blocked restores retain prior state.
- [ ] Run the focused backend and frontend suites plus the applicable type/lint
  checks and update `artifacts/designs/parts/epub-audiobook-pipeline-rewrite/README.md`
  dependency order and both contract ledgers only after the implementation and
  tests demonstrate the combined gate and frontend lifecycle behavior.

## Completion Criteria

- At most one persisted `pending`/`running` processing writer is admitted across
  all books: walks contend with renders, renders contend with walks and renders,
  and no gate is released before terminal render cleanup or Plan S walk replay.
- Onboard, re-onboard, replace, and snapshot restore cannot mutate while either
  writer class is active; their authoritative checks execute after
  `BEGIN IMMEDIATE`, with existing HTTP status and retry semantics preserved.
- Snapshot restore repeats its active-row check inside `_apply_snapshot_merge`,
  commits no partial merge on a race, and frontend restore always invalidates
  audio/render identity and requires a new render, even when
  `re_render_required` is false.
- Frontend destructive actions cancel and await both active walks and renders,
  never switch/reset identity optimistically on an unsafe wait, and successful
  import-as-new/onboard, re-onboard, replace, and snapshot restore cannot retain
  stale render jobs, caches, playback, or result-surface controls.
- Cross-class backend race tests, snapshot transaction tests, frontend lifecycle
  tests, and required focused lint/type/test commands pass without changing
  `app/tts.py` or the unrelated process-state subsystem.
