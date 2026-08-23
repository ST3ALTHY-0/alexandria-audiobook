# Task: Transactional Onboarding Replace and Import-as-New

## Problem Statement

The pipeline currently supports file-based `/onboard` for creating a new UUID
book, and fileless `/reonboard` for resetting generated walk output. It cannot
replace an existing book's EPUB while retaining that book's identity and series
position. Add an explicit destructive Replace workflow while preserving
`/reonboard` as the separate generated-output reset and preserving `/onboard` as
the non-destructive Import-as-new workflow.

Replace must extract and validate the uploaded EPUB before mutating storage,
coordinate with the process-wide active-walk invariant, and atomically replace
only the selected book's document tree and book-owned generated outputs. The
existing `book.id`, `series_id`, `book_number`, and `position` must survive;
series-position and visibility controls are deliberately out of scope.

## Dependencies

- Plans R and S: global walk admission/cleanup gate, savepoints, and durable
  cancellation rollback are the concurrency baseline.
- Existing `api_onboard.py`, `populate.py`, `assembly.py`, `api_export.py`,
  `tts_integration.py`, and the current onboarding/reonboarding tests.
- The v3 design decision that re-onboarding does not carry memberships or voice
  state by default. Replace therefore clears generated and book-scoped output;
  it does not implicitly carry character memberships or assignments.

## API and Frontend Decisions

- `POST /api/pipeline/onboard` remains Import-as-new: the server generates a
  fresh `book_id`, extracts, populates a new book, and returns the existing
  `{book_id, series_id, chapters}` response shape.
- `POST /api/pipeline/replace` is the new destructive file upload endpoint. It
  accepts multipart `file` plus `book_id`, returns the retained `book_id`,
  preserved series identity/position, replacement version, and chapter count,
  and returns `404` for an unknown book, `400` for invalid/extraction failure,
  `503` plus `Retry-After: 5` while any process-wide walk is pending/running.
  Extraction occurs before the replacement transaction; failed extraction or
  population leaves the old tree and outputs intact.
- `POST /api/pipeline/reonboard` remains fileless and unchanged in purpose:
  clear generated walk outputs and bump `book.version`, without replacing
  chapter/paragraph/span text. It retains the current generated-output reset
  response semantics.
- The Script tab exposes explicit separate actions: **Import as new** uses
  `/onboard`, **Replace current book** selects an EPUB and confirms destructive
  replacement, and **Re-onboard/reset generated outputs** remains the existing
  fileless action. Replace confirmation names the current book and states that
  document text and all book-owned generated outputs will be replaced,
  identity/series position will remain, and the action cannot be undone.
  Frontend cancellation waits for terminal cleanup before posting Replace and
  never changes persisted/current book state optimistically.

## Phases

### Phase 1: Define replacement seams and ownership inventory

- [x] Add the replacement request/response contract and an assembly-level
    **Implemented:** Enriched CONTRACTS.md Re-onboarding section with the full POST /api/pipeline/replace request/response contract (multipart file+book_id; success dict {book_id, series_id, book_number, position, version, chapters, status}; 400/404/503+Retry-After error codes; extraction-before-mutation ordering). Added assembly-level seam `replace_book_tree(book_id, chapters_data, storage) -> dict` in app/pipeline/assembly.py — thorough interface/docstring, retained-field set pinned (id/series_id/book_number/position; version bumped), protected/atomicity guarantees; non-mutating book-existence + non-empty-chapters validation, body raises NotImplementedError (transactional body deferred to Phase 2). Returns retained identity + new version + chapter count. No series-position/visibility controls added.
  replacement method that accepts an existing `book_id`, extracted chapters,
  and storage, retaining only the existing book identity and ordering fields.
- [x] Inventory every document-tree table, book-owned generated DB table,
    **Implemented:** Built the full ownership inventory. Document-tree tables (chapter/book_chapter, scene/chapter_scene, paragraph/scene_paragraph, span/paragraph_span), generated/book-owned DB tables (character_scene, character_span, character_book, character_scene_generated/manual/absence, character_alias_merge, boundary_override, workbench_decision/provenance/generation, walk_review_item, walk_override, persona_revision, prompt_config_revision, project_snapshot, walk_run, walk_undo_entry, render_job, render_chunk), and the RENDER_ROOT/book-{book_id} directory artifact are all catalogued with a scope key (book_id / scene / render / run). Encoded explicitly in BOTH CONTRACTS.md (Replace ownership ALLOWLIST table) and as the module-level constant `REPLACE_TABLE_SCOPE` in app/pipeline/assembly.py (28 entries). Protected tables (series, character, character_metadata, character_series, voice_config, clone_reference, sibling-book rows) are documented as never-touched. Files: CONTRACTS.md, app/pipeline/assembly.py.
  render job/chunk row, and `RENDER_ROOT/book-{book_id}` filesystem artifact;
  encode an allowlist so cleanup cannot affect sibling books, shared character
  rows, global persona revisions, or unrelated render jobs.
- [x] Define transaction ordering: snapshot required IDs and retained book
    **Implemented:** Documented the full transactional ordering in CONTRACTS.md under "Replace transaction ordering (P1-S3)": (1) snapshot retained {id, series_id, book_number, position, version} plus chapter/scene/span chapter ID sets and the character_book character set BEFORE any delete (the join chains die with the tree); (2) remove old book-owned tree/output references in FK-safe deep-first order, deleting references to a node before the node itself so no FK NO ACTION fires; (3) populate the extracted tree under the retained book reusing populate.py helpers (no new book row, no renumbering); (4) clear/reconcile book-scoped generated outputs + render/walk records and the durable render root; (5) commit only after FK/ownership checks pass — single adapter-owned savepoint rolls the whole replacement back on failure. Also reflected in the replace_book_tree docstring in assembly.py.
  fields, remove old book-owned tree/output references safely, populate the
  extracted tree under the retained book, clear/reconcile generated outputs,
  and commit only after all foreign-key and ownership checks succeed.
- [x] Preserve the existing `reonboard_book` behavior and its exclusive shared
    **Implemented:** reonboard_book and its _clear_* helpers (incl. the exclusive shared character_metadata/character.voice_assignment_id NOT EXISTS-another-character_book guard) are left entirely untouched — no code modification, behavior preserved. Documented the three cleanup-path classes in CONTRACTS.md under "Re-onboarding cleanup paths — shared vs book-scoped vs preserved (P1-S4)": SHARED/conditionally-cleared = character_metadata + character.voice_assignment_id guarded by NOT EXISTS another character_book (a character shared with a sibling keeps metadata/voice); BOOK-SCOPED = character_book memberships, persona_revision rows with book_id set (global NULL-book_id rows protected), generated/manual presence + workbench/run/render book-keyed rows; PRESERVED = sibling-book rows, shared character identity/character_series, guarded shared metadata/voice when another book references the character, other-book character_scene/span/book rows, global persona revisions, voice_config/clone_reference, the series row and all non-retained book rows. Replace reuses the identical guard and never broadens reonboard_book's guarantees. Files: CONTRACTS.md only (docs; reonboard unchanged).
  character metadata/voice cleanup guard; document which cleanup is shared,
  book-scoped, or intentionally preserved.

### Phase 2: Implement backend extraction-before-mutation and atomic Replace

- [x] Extend `api_onboard.py` with `/replace`, validating EPUB and book
    **Implemented:** Added POST /api/pipeline/replace to app/pipeline/api_onboard.py. Multipart file + Form book_id. Validates .epub ext (400), then book existence via get_book_version BEFORE active-walk check (404-before-503, mirrors /reonboard). Saves upload via existing pattern (mkdtemp prefix=pipeline_replace_ + uuid4 filename, asyncio.to_thread(_write_bytes)). Calls extract_epub_text -> in-memory uncommitted result; extraction failure -> HTTP 400 with no DB mutation. After extraction runs _reject_if_walk_active (503 + Retry-After: 5), then replace_book_tree. Populate errors -> HTTP 500. Returns {book_id, series_id, book_number, position, version, chapters, status:'replaced'}. tmp file cleaned in finally. Imported replace_book_tree and Form. /onboard and /reonboard untouched.
  existence before the active-walk check, saving the upload in the existing
  server-generated temporary path, extracting into an uncommitted in-memory
  result, and mapping failures without touching the database.
- [x] Implement the replacement transaction using the adapter-owned
    **Implemented:** Replaced the replace_book_tree body in app/pipeline/assembly.py with the real implementation. Runs inside a single adapter-owned `with storage.transaction()` (BEGIN IMMEDIATE, same atomic unit reonboard_book uses; task directed transaction() over savepoint). (1) Snapshots retained {id, series_id, book_number, position, version} + pre-identifies book-own chapters (chapter.book_id), scenes/paragraphs/spans via the edge chain back to the book, and the character_book character set BEFORE any delete. (2) Removes old book-owned tree+outputs in FK-safe deep-first order. (3) Populates the NEW tree under the retained book reusing populate.py _ensure_paragraph_text_column/_ensure_span_text_column/_insert_chapters_with_placeholders — NO new book row, NO renumbering/position change (position retained from retained row), version bumped via UPDATE book SET version=version+1. (4) Commit on transaction exit only if no exception. Verified: book.id/series_id/book_number/position retained, version+1, rollback restores original tree+version+outputs on a forced IntegrityError. Return dict {book_id, series_id, book_number, position, version, chapters}. Docstring updated (savepoint->transaction wording). populate_initial_spine deliberately NOT used (it INSERTs a book row + renumbers).
  transaction/savepoint contract, retaining `book.id`, `series_id`,
  `book_number`, and `position`; create the new chapter/paragraph/span tree
  for that same book and ensure a failure rolls the complete replacement back.
- [x] Remove only the selected book's old tree and all dependent generated
    **Implemented:** Deletion of only the selected book's old tree + dependent generated references, FK-safe, inside the replace transaction. Reuses reonboard helpers: _clear_span_junctions (character_span + character_scene + span.instruct), _clear_memberships (character_metadata + character.voice_assignment_id under the NOT EXISTS-another-character_book shared guard, then character_book), _clear_persona_revisions (book-scoped only; global NULL-book_id preserved), _clear_scene_entities (character_scene_absence/alias_merge/boundary_override/character_scene_generated/character_scene_manual + scene_paragraph/chapter_scene edges + scene rows). Additionally deletes: workbench_decision (after clearing its undone_by/supersedes_id self-refs) + workbench_provenance + workbench_generation; walk_review_item + walk_override; prompt_config_revision (clearing base_revision/superseded_by self-refs) + project_snapshot; walk_undo_entry (by book's run_ids) then walk_run; render_chunk (by book's job_ids) then render_job; then deep tree nodes paragraph_span edges, book_chapter edges, span/paragraph/chapter rows. FK-safe order verified (InMemory adapter runs with PRAGMA foreign_keys=ON). Preserves shared characters, guarded metadata/voice, sibling-book rows, series, voice_config/clone_reference, global persona revisions.
  references in FK-safe order, including workbench projections, reviews,
  persona revisions, character memberships/junctions, and span instructions;
  preserve shared characters and sibling-book rows.
- [x] Invalidate/delete only the selected book's `render_job`/`render_chunk`
    **Implemented:** render_job/render_chunk rows for the selected book deleted inside the replace transaction (render_chunk by job_id IN book's render_job first, then render_job WHERE book_id). Durable render directory removal implemented as new helper _remove_book_render_dir(book_id) in app/pipeline/assembly.py: resolves RENDER_ROOT at call time via a LAZY import of get_render_root from tts_integration (top-level import avoided because tts_integration imports assembly -> cycle), builds RENDER_ROOT/book-{book_id}, realpath containment-checks it lies strictly inside render_root (never deletes another book's dir or the root itself, resilient to path-traversal book_id), shutil.rmtree(ignore_errors=True) tolerates missing derived files. Called AFTER transaction commit so a DB rollback never destroys a dir whose rows were restored; active-walk guard ensures no live run when rows/dir are removed. Verified: b1 dir removed, sibling b2 dir preserved.
  records and remove only its durable render directory/artifacts after active
  execution has stopped; use the canonical render-root layout and tolerate
  missing derived files without deleting another book's directory.
- [x] Return the documented Replace response and keep `/onboard` and
    **Implemented:** /replace returns the documented response shape {book_id, series_id, book_number, position, version, chapters, status:'replaced'} (the retained identity/ordering, new bumped version, replaced chapter count; status added per CONTRACTS.md success dict). /onboard and /reonboard endpoints and their response/error semantics are completely untouched — no changes to onboard_epub, reonboard, ReonboardRequest, _reject_if_walk_active. Confirmed via route listing all three POST routes registered; ruff clean and full tests/pipeline/test_reonboard.py (34) green, proving reonboard behavior preserved.
  `/reonboard` response/error semantics backward compatible.

### Phase 3: Wire explicit frontend actions and safe state transitions

- [x] Add typed `pipelineReplace` API support and separate Script-tab handlers
    **Implemented:** Added typed `pipelineReplace(bookId, file)` to frontend/src/api.ts reusing the clone-reference multipart convention: builds FormData with `file` + `book_id` fields and POSTs /api/pipeline/replace through `postFormWithRetryOnce` (one 503+Retry-After retry, honoring the process-wide active-walk guard). Exported `PipelineReplaceResult` interface (book_id, series_id, book_number, position, version, chapters, status:'replaced'). It is NOT routed through the fileless /reonboard. Script tab now has two separate handlers: `handleOnboard` = Import-as-new (behavior preserved exactly, only the index.html button label changed to "Import as new") via POST /api/pipeline/onboard, and new `handleReplace` = Replace-current via API.pipelineReplace. Files: frontend/src/api.ts (add), frontend/src/tabs/script.ts (handler + comment), frontend/index.html (relabel).
  for Import-as-new and Replace-current; do not route Replace through the
  fileless `/reonboard` endpoint.
- [x] Add the Replace file control/action using existing UI conventions, with
    **Implemented:** Added the Replace file control + action in the Script tab using existing UI conventions. index.html: inside #walk-execution-section (only shown after a book exists), a `#file-upload-replace` file input (accept=.epub), a `#replace-status` status div, and a `#btn-replace-epub` outline-danger button "Replace Current Book" alongside the existing Run All/Cancel/Re-onboard buttons, with a small muted caption. `handleReplace()` (script.ts) validates a file is chosen and is .epub, then shows a destructive `showConfirm` naming `currentBookId` and stating document text + all book-owned generated outputs will be replaced, book ID/series/number/position remain unchanged, and it cannot be undone. Status/toast handling covers success, extraction failure (400 → catch), 404 unknown book, and 503 contention (retried once then surfaced). Files: frontend/index.html, frontend/src/tabs/script.ts.
  a destructive `showConfirm` message, current-book identity display, and
  status/toast handling for success, extraction failure, 404, and 503
  contention.
- [x] Reuse `waitForWalkCleanup` before Replace, but make the flow honor the
    **Implemented:** `handleReplace` reuses `waitForWalkCleanup(bookId)` before POSTing replace (cancels the current book's active walk(s) and awaits terminal cleanup). The flow honors the backend's PROCESS-WIDE guard: because the deployable guard is global (any book's active walk → 503), `pipelineReplace` relies on `postFormWithRetryOnce` for one retry, and any still-failing request (another book's walk contention, 404, 400) falls into the catch. No optimistic state change anywhere in the Replace path: currentBookId, persisted pipelineBookId, walk polling, and the displayed book are all retained on ANY failed wait or request. Type-check clean.
  backend's process-wide guard when another book owns the active walk; on any
  failed wait or request, retain the prior `currentBookId`, persisted
  `pipelineBookId`, walk polling, and displayed book.
- [x] On successful Replace, retain the same current book ID, refresh/reset walk
    **Implemented:** On successful Replace, `currentBookId` is NOT reassigned (book identity is preserved — the backend retains book.id/series_id/book_number/position and bumps version), so persisted pipelineBookId and the polling target stay the same book. The success path resets walk status to all-pending via renderWalkStatuses, resets the Run All button via updateRunAllButton(false), and refreshes the run history for the SAME book via `void refreshWalkRuns(bookId)` (renderWalkRuns reconciles the cleared runs into the empty state). Success status line + toast report the retained book id, chapter count, and new version. Import-as-new (handleOnboard) behavior is unchanged apart from the explicit "Import as new" label.
  status and run history for that book, and leave Import-as-new behavior
  unchanged apart from its explicit label.
- [x] Keep `/reonboard` as the separate no-file reset action and update its
    **Implemented:** /reonboard remains the separate fileless reset action (`handleReonboard` + `pipelineReonboard`, button "Re-onboard"). Its confirmation copy was updated to distinguish generated-output reset from document replacement: now "…clears the generated walk outputs and bumps the version, but does NOT replace the document text. This cannot be undone." Existing aborted-toast text "Re-onboard aborted: …" preserved (an existing test asserts this substring). No series-position or visibility controls were introduced anywhere in this phase.
  confirmation copy to distinguish generated-output reset from document
  replacement. Do not add series-position or visibility controls.

### Phase 4: Backend replacement, isolation, rollback, and API tests

- [x] Extend `tests/pipeline/test_reonboard.py` or a focused replacement test
    **Note:** PASS. Added TestReplaceChangesContent (2 tests) in tests/pipeline/test_replace.py: replace_book_tree swaps chapter/paragraph/span CONTENT while book.id/series_id/book_number/position stay byte-identical and version bumps by exactly one per replace (seed v2->3, second replace->4). Built spine via populate helpers (_insert_chapters_with_placeholders), asserted old span/paragraph/chapter ids gone + new text present. Uses InMemorySQLiteAdapter.
  module to verify Replace changes chapter/paragraph/span content while the
  book ID, series ID, book number, and position remain identical.
- [x] Test extraction-before-mutation and transaction rollback by failing
    **Note:** PASS. (a) Extraction-before-mutation: TestReplaceExtractionFailure in test_api_replace.py mocks extract_epub_text to raise -> HTTP 400 'Failed to extract', asserts DB untouched (old span present, new absent, version unchanged). (b) Transaction rollback: TestReplaceRollback in test_replace.py seeds full surface then feeds a duplicate-span-id chapters_data -> sqlite3.IntegrityError during population; asserts complete rollback (version, old spans, render jobs/chunks, character_scene, character_scene_generated all unchanged, no leftover node).
  extraction and population after staging; assert the original tree, version,
  generated outputs, and render rows remain unchanged.
- [x] Test cleanup isolation with two books in one series: Replace one book and
    **Note:** PASS. TestReplaceIsolation in test_replace.py: two books b1/b2 in one series s1 (b2 uses _relabel spine for distinct global chapter/paragraph/span ids). After replacing b1: sibling b2 tree (chapters/paragraphs/spans/book_chapter/chapter_scene/scene_paragraph/paragraph_span edges) intact, b2 memberships + b2 character_scene junction kept, shared character c1 metadata('voice_profile')+voice_assignment preserved (guarded by c1 still in b2), b2 render_job/render_chunk kept, b1 render dir removed while book-b2 dir + wav preserved. This is the anti-cross-book-deletion test.
  assert sibling tree, memberships, shared character metadata/voice, render
  rows, chunks, and filesystem output remain intact.
- [x] Test all relevant book-owned generated tables and filesystem artifacts,
    **Note:** PASS. TestReplaceCleansAllGeneratedOutputs (4 tests) in test_replace.py: a fully-workbenched book replaces cleanly (no FK failure). Asserts ALL book-owned tables emptied for b1: workbench_decision/provenance/generation, character_scene_generated/manual/absence, boundary_override, character_alias_merge, walk_review_item, walk_override, prompt_config_revision, project_snapshot, render_job (+render_chunk via job join), character_book, walk_run, walk_undo_entry, character_scene/character_span junctions. Global NULL-book_id persona (gp1) + voice_config + clone_reference + characters preserved; book-scoped persona (bp1) cleared. Missing render dir tolerated (no crash); render dir with missing derived files removed without error (ignore_errors path). render_chunk has NO book_id column (keyed by job_id) and walk_undo_entry none either — queried via join in tests.
  including workbench/FK surfaces and missing-output-directory handling, so the
  historical partial-delete/FK failure cannot recur.
- [x] Test unknown book, invalid EPUB, active walk on the same book, active walk
    **Note:** PASS. test_api_replace.py TestClient + dependency_overrides[get_storage] (InMemorySQLiteAdapter), extract_epub_text mocked. /replace: unknown book 404 (checked before extraction/active-walk); invalid non-epub 400 before book check; active walk on SAME book 503+Retry-After:5; active walk on ANOTHER book 503+Retry-After:5; 404-before-503 precedence (no retry-after header on 404). Successful replace 200 status 'replaced', version 1->2, identity retained, spine swapped. Unchanged /onboard (Import-as-new) returns standard {book_id(server uuid), series_id, chapters}; unchanged /reonboard -> status 'reonboarded', version bumped; /reonboard 404-before-503. All match existing /reonboard precedence contract.
  on another book, successful Import-as-new, unchanged `/reonboard`, and
  `Retry-After`/status precedence contracts with FastAPI `TestClient`.

### Phase 5: Frontend, integration, and generated-asset verification

- [x] Extend `frontend/tests/frontend/test_script.test.ts` for explicit Import,
    **Implemented:** Extended frontend/tests/frontend/test_script.test.ts with a new 'Replace current book (Plan T, Phase 5)' describe block (7 new tests, file 96->... ). Extended the existing partial vi.mock('../../src/api') to also expose postFormWithRetryOnce + pipelineReplace (via vi.importActual) so handleReplace drives the real retry-once wrapper against mockFetch. Tests cover: explicit Import-as-new (posts /onboard, no destructive confirm, fresh server id); Replace showConfirm confirm (post /replace with book_id+file, identity retained) AND decline (no request, state kept); active-walk wait (cancel_walks fires BEFORE /replace, asserted via call order); 503 retry-exhausted failure preservation (2 attempts, no optimistic change, Replace failed toast, identity retained); successful same-ID replacement (status Replaced + retained book id + v2, walk status reset to pending, pipelineBookId unchanged); separate Re-onboard copy (generated-output reset distinguishes from document replacement, posts fileless /reonboard instance FormData /replace). Match existing vitest/jsdom conventions (fake timers, mockFetch, DOMContentLoaded single-listener reuse, dynamic await import('../../src/utils')). Full file suite: 106 passed (99 baseline + 7 new), 0 failed.
  Replace confirmation/decline, active-walk wait, 503 failure preservation,
  successful same-ID replacement, and separate Re-onboard behavior.
- [x] Add an end-to-end onboarding lifecycle test covering Import-as-new,
    **Implemented:** Added tests/pipeline/test_e2e_lifecycle.py — an end-to-end onboarding lifecycle integration test driven through the real HTTP API (FastAPI TestClient + InMemorySQLiteAdapter + extract_epub_text mocked). Determined the repo has no Playwright/browser e2e harness; the established e2e convention is test_e2e.py (in-memory backend + real pipeline functions), so this is the strongest harness the repo supports — I extended it to drive the actual endpoints. One TestClient-driven test exercises the full lifecycle sequentially: (1) Import-as-new via POST /onboard creating a fresh book in its own series; (2) simulated completed walks leave walk_run/character_book/character_span/render output on main + sibling; (3) POST /replace book-main -> status 'replaced', version 1->2, doc tree swapped, its own generated output + render dir cleared; (4) walks again land output on the replaced book; (5) fileless POST /reonboard -> status 'reonboarded', version 3, membership/span output reset while the run ledger + render rows are PRESERVED (documented distinct Re-onboard behavior). Invariants asserted at every destructive step: main book id/series_id/book_number/position byte-identical (zero identity drift) and sibling book-import's tree/character/output/render rows/render dir untouched (zero cross-book deletion). Uses the robust populate helpers (not the stale `INSERT INTO paragraph VALUES ('p1')` pattern that breaks the old test_e2e.py in this env). Test passes; ruff clean. Run: .venv/bin/python -m pytest tests/pipeline/test_e2e_lifecycle.py -v
  Replace, subsequent walks, and fileless Re-onboard without cross-book
  deletion or identity drift.
- [x] Run the focused backend and frontend suites plus TypeScript/build checks;
    **Verified:** Verification + generated-asset convention decision. Suites: frontend full `npx vitest run` = 573 passed / 0 failed across 13 files; backend focused = 53 passed (test_replace 29-ish + test_api_replace + test_e2e_lifecycle 1 + test_reonboard); backend full `tests/pipeline/` = 44 failed / 2071 passed. ALL 44 failures are the pre-existing environmental baseline confined to test_schema.py (14), test_tts_integration.py (16), test_presentation.py (4), test_workbench_domain.py (4), test_e2e.py (5, stale `INSERT INTO paragraph VALUES ('p1')` schema-drift), test_adapter.py (2), test_legacy_removed.py (1). ZERO regressions: my new files (test_e2e_lifecycle.py, test_replace.py, test_api_replace.py) all pass. TypeScript: `npx tsc --noEmit` exit 0 (clean). Build: `npx vite build` succeeds emitting app/static/dist (index.html + assets/index-z8AMFS4c.js). GENERATED-ASSET CONVENTION: git ls-files confirms app/static/dist IS tracked at HEAD (index.html + assets/index-*.js) — the repository convention REQUIRES committed build output, so a rebuild is REQUIRED (not optional). Phase 3's source-only revert was contrary to the repo convention. Ran the rebuild: old tracked index-D0Y0Qt5l.js deleted (D), new index-z8AMFS4c.js added (??), index.html updated (M) — reflecting the Phase 3 source changes. Per task constraint 'do not commit', the regenerated dist is left as working-tree changes for parent review. NOTE for reviewer: dist is a required tracked artifact; the rebuild must be committed with the source.
  verify no generated frontend asset is committed unless repository build
  conventions require tracked output (the current source/build convention is
  the authority).
- [x] Update endpoint/doc comments and the pipeline contract ledger so API
    **Completion:** Plan complete. Review: 2 rounds total (1 fix cycle). Round 1: ISSUES_FOUND MINOR (7 doc drift items, implementation verified correct, tests green) -> Exec-Fixer applied 7/7 fixes (5 CONTRACTS.md drift + 2 assembly docstring). Round 2: PASS full re-review, all checks green. TestAnalyzer sub-analyzer returned empty (GENERATION_FAILED) but coverage verified directly by reviewer across all 4 test files; docsAnalyzer MINOR_PASS. No plan amendment/re-execution needed. Generated dist rebuilt per repo convention (app/static/dist tracked at HEAD). Zero regressions (44 pre-existing env baseline failures unchanged).
    **Documented:** Docs/ledger updated (comments-only, no behavior change; locks: focused backend still 53 passed, ruff clean on api_onboard.py+assembly.py). (1) app/pipeline/api_onboard.py `/replace` docstring: added explicit OWNERSHIP BOUNDARY paragraph (replace_book_tree clears ONLY book-owned rows/files; no other book, their spines/characters/output, and no global NULL-book_id persona rows touched; siblings + Import-as-new books left byte-identical) and a DELIBERATELY-NO-SERIES-POSITION/VISIBILITY paragraph (book's series_id/book_number/position always retained; endpoint never renumbers/reorders and exposes no visibility control; Import-as-new is the only way a differently-seriesed book is introduced). (2) assembly.py replace_book_tree docstring — VERIFIED it already documents the ownership boundary ('Protected — never touched', REPLACE_TABLE_SCOPE allowlist) and 'introduces no series-position or visibility controls' (lines ~516-524); no change needed. (3) Root README.md (was missing Replace entirely): Step 2 now distinguishes Onboard/Import-as-new (distinct book) vs Replace (swap doc, retain ID+series/position, reset generated output, wait/cancel active walk, never reorder series/toggle visibility) vs Re-onboard (reset output but PRESERVE run history, distinct from Replace); Script Tab bullet list now includes Replace with the 503+Retry-After contention and no-position/visibility disclaimer. (4) CONTRACTS.md parts ledger — VERIFIED the 'Replace workflow (Plan T)' section (artifacts/designs/parts/epub-audiobook-pipeline-rewrite/CONTRACTS.md ~566-764) already fully satisfies all four discoverability requirements (API request/response + error codes; active-walk 503+Retry-After global invariant; ownership ALLOWLIST P1-S2; 'no series-position or visibility controls are introduced' line 606); no change needed.
  semantics, ownership boundaries, active-walk behavior, and the deliberate
  absence of series-position/visibility controls are discoverable.

## Completion Criteria

- Replace is a confirmed, explicit frontend action using `POST /api/pipeline/replace`.
- Replace extracts before mutation and atomically swaps only the selected
  book's document tree; failures leave the old state intact.
- `book_id`, series identity, book number, and series position are preserved;
  no position or visibility controls are introduced.
- All selected-book generated DB/filesystem outputs are cleaned without
  affecting sibling books or shared character state.
- The process-wide active-walk guard and terminal-cleanup ordering are enforced
  for Replace and Import.
- `/onboard` remains Import-as-new, `/reonboard` remains generated-output reset,
  and their existing contracts remain compatible.
- Backend, frontend, isolation, rollback, and lifecycle tests pass; generated
  assets follow project conventions.
