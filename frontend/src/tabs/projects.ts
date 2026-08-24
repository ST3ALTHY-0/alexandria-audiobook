/**
 * Projects tab — snapshot projects + multi-book navigation (Plan M).
 *
 * Lists every persisted book (GET /api/pipeline/books) grouped with its
 * owned snapshots, lets the user OPEN a book (canonical setPipelineBookId +
 * book-scoped invalidation), and saves / loads / deletes / renames snapshots
 * for the currently selected book against /api/pipeline/projects/*.
 *
 * Endpoints used:
 *   - GET    /api/pipeline/books                          → BookProjects list
 *   - POST   /api/pipeline/projects {book_id}          → auto-named snapshot
 *   - POST   /api/pipeline/projects/load {name, book_id} → restore (409 +
 *     Retry-After while a walk/render is active — retried exactly once)
 *   - DELETE /api/pipeline/projects/{name}
 *   - PATCH  /api/pipeline/projects/{name} {new_name}
 *
 * The snapshot NAME is always generated server-side ("Project {YYYY-MM-DD
 * HH:MM}" + optional " (N)" suffix); the frontend never proposes a name.
 */

import * as API from '../api';
import { state, setPipelineBookId, clearBookScopedState } from '../state';
import { showToast, showConfirm, escapeHtml } from '../utils';
import {
  clearUndoStack,
  loadSpans,
  loadSingleSpeakerToggle,
  resetRenderStateForBookReplacement,
} from './editor-pipeline';
import {
  loadWorkbench,
  loadWorkbenchConfig,
  clearUndoStack as clearWorkbenchUndoStack,
} from './workbench';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** A saved snapshot row (backend ProjectSnapshot DTO). */
export interface ProjectSnapshot {
  name: string;
  book_id: string;
  created_ms: number;
  size_bytes: number;
}

/**
 * A persisted book and its owned snapshots (BookProjects DTO, GET
 * /api/pipeline/books). Identity metadata is read-only — the frontend never
 * mutates book/series identity or position.
 */
export interface BookProjects {
  id: string;
  series_id: string | null;
  book_number: number | null;
  version: number;
  position: number | null;
  projects: ProjectSnapshot[];
}

/** Response of POST /api/pipeline/projects/load. */
export interface LoadProjectResult {
  status: string;
  name: string;
  book_id: string;
  re_render_required: boolean;
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

/**
 * Format a byte count as a compact human-readable string ("1.5 KB").
 */
export function formatSnapshotSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

/**
 * Format a unix-ms timestamp as a local date string.
 */
export function formatSnapshotDate(createdMs: number): string {
  return new Date(createdMs).toLocaleString();
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

/**
 * Render snapshot rows (name / created / size + Load / Delete / Rename
 * affordances) as an HTML string. All server-supplied content (the name) is
 * escaped — no raw innerHTML injection.
 */
export function renderProjectsList(snapshots: ProjectSnapshot[]): string {
  if (snapshots.length === 0) {
    return '<p class="text-muted mb-0">No saved projects yet. Save the current book to create a snapshot.</p>';
  }

  return snapshots
    .map((s) => {
      const safeName = escapeHtml(s.name);
      const safeDataName = escapeHtml(s.name);
      return `
        <div class="project-row d-flex justify-content-between align-items-center border rounded p-2 mb-2">
          <div class="me-3 overflow-hidden">
            <div class="fw-semibold text-truncate" title="${safeDataName}">${safeName}</div>
            <div class="small text-muted">${escapeHtml(formatSnapshotDate(s.created_ms))} &middot; ${escapeHtml(formatSnapshotSize(s.size_bytes))}</div>
          </div>
          <div class="d-flex gap-1 flex-shrink-0">
            <button type="button" class="btn btn-sm btn-outline-success" data-action="project-load" data-name="${safeDataName}" title="Load this snapshot into the current book"><i class="fas fa-download me-1"></i>Load</button>
            <button type="button" class="btn btn-sm btn-outline-primary" data-action="project-rename" data-name="${safeDataName}" title="Rename this snapshot"><i class="fas fa-edit me-1"></i>Rename</button>
            <button type="button" class="btn btn-sm btn-outline-danger" data-action="project-delete" data-name="${safeDataName}" title="Delete this snapshot"><i class="fas fa-trash me-1"></i>Delete</button>
          </div>
        </div>`;
    })
    .join('');
}

// ---------------------------------------------------------------------------
// List / Save / Load / Delete / Rename / Open
// ---------------------------------------------------------------------------

/**
 * Human-readable label for a book's identity metadata (series / number /
 * position / version). Returns a fallback when the book has no usable
 * identity metadata. Only ever used for display — escaped by callers.
 */
export function formatBookLabel(book: BookProjects): string {
  const parts: string[] = [];
  if (book.series_id) parts.push(`Series ${book.series_id}`);
  if (book.book_number != null) parts.push(`Book ${book.book_number}`);
  if (book.position != null) parts.push(`Pos ${book.position}`);
  if (book.version != null) parts.push(`v${book.version}`);
  return parts.length > 0 ? parts.join(' \u00b7 ') : 'Unnamed book';
}

/**
 * Render every persisted book, each grouped with its owned snapshots and an
 * Open control (or a "Current" badge when it is the selected book). All
 * server-supplied content (book id, series, position) is escaped — no raw
 * innerHTML injection.
 */
export function renderBooksList(books: BookProjects[]): string {
  if (books.length === 0) {
    return '<p class="text-muted mb-0">No books onboarded yet. Import an EPUB on the Script tab to create a book project.</p>';
  }

  return books
    .map((book) => {
      const safeId = escapeHtml(book.id);
      const isCurrent = book.id === state.pipelineBookId;
      const activeClass = isCurrent ? ' border-primary' : '';
      const label = escapeHtml(formatBookLabel(book));
      const openControl = isCurrent
        ? '<span class="badge bg-primary flex-shrink-0">Current</span>'
        : `<button type="button" class="btn btn-sm btn-outline-primary flex-shrink-0" data-action="project-open" data-book-id="${safeId}" title="Open this book as the current project"><i class="fas fa-folder-open me-1"></i>Open</button>`;
      return `
        <div class="book-project mb-3 border rounded p-2${activeClass}">
          <div class="d-flex justify-content-between align-items-center mb-2">
            <div class="me-2 overflow-hidden">
              <div class="fw-semibold text-truncate" title="${safeId}">${label}</div>
              <code class="small text-muted">${safeId}</code>
            </div>
            ${openControl}
          </div>
          ${renderProjectsList(book.projects)}
        </div>`;
    })
    .join('');
}

/** Load and render the book/project list for multi-book navigation. */
export async function loadProjects(): Promise<void> {
  const listEl = document.getElementById('projects-list');
  if (!listEl) return;

  try {
    const books = await API.get<BookProjects[]>('/api/pipeline/books');
    listEl.innerHTML = renderBooksList(books);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    showToast('Failed to load projects: ' + msg, 'error');
    listEl.innerHTML = '<p class="text-muted mb-0">Failed to load projects.</p>';
  }
}

/**
 * Open a persisted book as the current project.
 *
 * Non-destructive ordered transition (Plan M): validate the DTO identity,
 * invalidate the OLD book's render/editor/audio surfaces via the existing
 * comprehensive render reset primitive, clear workbench undo + book-scoped
 * shared state, then call the canonical `setPipelineBookId` exactly once and
 * load the selected book's data.
 *
 * Never calls onboard / replace / re-onboard and never loads a snapshot into
 * a different owner. If invalidation fails before the switch is committed,
 * the prior canonical book remains selected.
 */
export async function openBook(bookId: string): Promise<void> {
  if (!bookId) return;
  // Same-book no-op: keep the current selection and avoid a redundant reset.
  if (state.pipelineBookId === bookId) return;

  try {
    // 1) Invalidate the OLD book's render/editor/audio surfaces (includes
    //    render job cancellation, caches, undo stack, and playback).
    await resetRenderStateForBookReplacement();
    // 2) Clear the old book's workbench undo records + book-scoped shared
    //    state (render job handle, workbench, workbench config) WITHOUT
    //    touching pipelineBookId and without a second persistence key.
    clearWorkbenchUndoStack();
    clearBookScopedState();
    // 3) Commit the switch through the single canonical setter.
    setPipelineBookId(bookId);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    showToast('Failed to switch book: ' + msg, 'error');
    return;
  }

  showToast(`Opened book ${bookId}`, 'success');
  // Refresh the list so the new book is highlighted as current.
  await loadProjects();

  // Load the newly-selected book's data into the existing tab surfaces.
  try {
    await loadSpans();
    await loadSingleSpeakerToggle();
    await loadWorkbench(true);
    await loadWorkbenchConfig();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    console.error('Failed to load selected book data', e);
    showToast('Book switched, but some data failed to load: ' + msg, 'warning');
  }
}

/**
 * Save the current book as an auto-named snapshot.
 * POSTs {book_id} — the server generates the name; on success the returned
 * auto-name is surfaced and the list refreshes.
 */
export async function saveProject(): Promise<void> {
  if (!state.pipelineBookId) {
    showToast('No book onboarded. Go to the Script tab to onboard an EPUB first.', 'error');
    return;
  }

  try {
    const created = await API.post<ProjectSnapshot>('/api/pipeline/projects', {
      book_id: state.pipelineBookId,
    });
    showToast(`Saved snapshot "${created.name}"`, 'success');
    await loadProjects();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    showToast('Failed to save snapshot: ' + msg, 'error');
  }
}

/**
 * Load (restore) a snapshot into the current book.
 *
 * The 409 + Retry-After response (active walk/render — rule #10) is handled
 * by postWithRetryOnce with retryStatus 409: exactly ONE automatic retry
 * after the advertised delay. On success the snapshot list refreshes and the
 * editor spans reload (cross-tab refresh hook). When the backend reports
 * re_render_required=true (snapshot audio artifacts missing) an explicit
 * "re-render" notice is surfaced.
 */
export async function loadProject(name: string): Promise<void> {
  if (!state.pipelineBookId) {
    showToast('No book onboarded. Go to the Script tab to onboard an EPUB first.', 'error');
    return;
  }

  try {
    const result = await API.postWithRetryOnce<LoadProjectResult>(
      '/api/pipeline/projects/load',
      { name, book_id: state.pipelineBookId },
      409,
    );

    if (result.re_render_required) {
      showToast(
        `Snapshot "${result.name}" loaded — audio is missing, re-render required`,
        'warning',
      );
    } else {
      showToast(`Snapshot "${result.name}" loaded`, 'success');
    }

    await loadProjects();
    // Cross-tab refresh: reload the editor span table so the restored
    // snapshot's script is immediately visible on the Editor tab.
    await loadSpans();
    // ...and reflect the book's single-speaker flag in the editor toggle.
    await loadSingleSpeakerToggle();
    // ...and clear the editor's span-text undo stack: snapshot load replaces
    // the span set, so stale undo entries must not survive (Plan J, Phase 3).
    clearUndoStack();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    showToast('Failed to load snapshot: ' + msg, 'error');
  }
}

/**
 * Delete a snapshot (after confirmation) and refresh the list.
 */
export async function deleteProject(name: string): Promise<void> {
  const confirmed = await showConfirm(`Delete snapshot "${name}"? This cannot be undone.`);
  if (!confirmed) return;

  try {
    await API.del<{ status: string; name: string }>(
      `/api/pipeline/projects/${encodeURIComponent(name)}`,
    );
    showToast(`Snapshot "${name}" deleted`, 'success');
    await loadProjects();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    showToast('Failed to delete snapshot: ' + msg, 'error');
  }
}

/**
 * Rename a snapshot. Prompts for the new name, then PATCHes {new_name}.
 * A 409 duplicate surfaces the backend detail (already exists) via toast.
 */
export async function renameProject(name: string): Promise<void> {
  const newName = prompt(`Rename snapshot "${name}" to:`);
  if (newName === null) return; // user cancelled

  try {
    await API.patch<{ status: string; name: string }>(
      `/api/pipeline/projects/${encodeURIComponent(name)}`,
      { new_name: newName },
    );
    showToast(`Snapshot renamed to "${newName}"`, 'success');
    await loadProjects();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    showToast('Failed to rename snapshot: ' + msg, 'error');
  }
}

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------

let _projectsInitialized = false;

/**
 * Initialize the Projects tab.
 *
 * Idempotent: a module flag ensures the DOMContentLoaded handler is
 * registered at most once (matches initEditor's pattern).
 *
 * Wires:
 *   - #btn-project-save click → saveProject()
 *   - Delegated clicks on #projects-list for [data-action="project-load" |
 *     "project-delete" | "project-rename"], reading the snapshot name from
 *     the button's data-name attribute
 *   - Initial snapshot list load
 */
export function initProjects(): void {
  if (_projectsInitialized) return;
  _projectsInitialized = true;

  document.addEventListener('DOMContentLoaded', () => {
    const saveBtn = document.getElementById('btn-project-save');
    if (saveBtn) {
      saveBtn.addEventListener('click', () => {
        saveProject();
      });
    }

    const listEl = document.getElementById('projects-list');
    if (listEl) {
      listEl.addEventListener('click', (e) => {
        const target = e.target as HTMLElement;
        const btn = target.closest('button') as HTMLButtonElement | null;
        if (!btn) return;

        const action = btn.dataset.action;
        const name = btn.dataset.name;
        const bookId = btn.dataset.bookId;

        if (action === 'project-open') {
          if (bookId) openBook(bookId);
        } else if (action === 'project-load') {
          if (name) loadProject(name);
        } else if (action === 'project-delete') {
          if (name) deleteProject(name);
        } else if (action === 'project-rename') {
          if (name) renameProject(name);
        }
      });
    }

    loadProjects();
  });
}
