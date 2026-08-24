/**
 * Spec-first tests for multi-book Project navigation (frontend/src/tabs/projects.ts)
 * — Plan M phases 2-3.
 *
 * Backend contract (app/pipeline/api_operations.py, tested in
 * tests/pipeline/test_multi_book_projects.py):
 *   - GET /api/pipeline/books → BookProjects[] {id, series_id, book_number,
 *     version, position, projects: ProjectSnapshot[]}
 *
 * Frontend contract (Plan M):
 *   - Opening a book uses the canonical setPipelineBookId as the ONLY
 *     persistence path, invalidates book-scoped render/editor/workbench/undo
 *     state BEFORE the switch, and leaves the prior canonical book selected
 *     when invalidation fails before the switch commits.
 *   - Opening the already-selected book is a no-op (no reset, no re-persist).
 *   - Empty/invalid selections are handled safely (no crash, no mutation).
 *
 * Run with `npm test` (vitest run) from frontend/.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { state } from '../../src/state';
import * as API from '../../src/api';
import { showToast } from '../../src/utils';
import {
  resetRenderStateForBookReplacement,
  loadSpans,
  loadSingleSpeakerToggle,
} from '../../src/tabs/editor-pipeline';
import {
  loadWorkbench,
  loadWorkbenchConfig,
  clearUndoStack as clearWorkbenchUndoStack,
} from '../../src/tabs/workbench';
import { openBook, initProjects, BookProjects } from '../../src/tabs/projects';

// The canonical localStorage key that state.ts persists the selected book
// under. It is a private const in state.ts (not exported); the literal is
// documented by the pipeline-book-id-persistence skill contract.
const LS_PIPELINE_BOOK_ID = 'alexandria-pipeline-book-id';

vi.mock('../../src/api', () => ({
  get: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  postWithRetryOnce: vi.fn(() =>
    Promise.resolve({ status: 'ok', name: '', book_id: '', re_render_required: false }),
  ),
}));

vi.mock('../../src/utils', () => ({
  showToast: vi.fn(),
  showConfirm: vi.fn(),
  escapeHtml: (s: unknown) =>
    s == null
      ? ''
      : String(s)
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;')
          .replace(/'/g, '&#39;'),
}));

vi.mock('../../src/tabs/editor-pipeline', () => ({
  resetRenderStateForBookReplacement: vi.fn(() => Promise.resolve()),
  loadSpans: vi.fn(() => Promise.resolve()),
  loadSingleSpeakerToggle: vi.fn(() => Promise.resolve()),
  clearUndoStack: vi.fn(),
}));

vi.mock('../../src/tabs/workbench', () => ({
  loadWorkbench: vi.fn(() => Promise.resolve()),
  loadWorkbenchConfig: vi.fn(() => Promise.resolve()),
  clearUndoStack: vi.fn(),
}));

const OTHER_BOOKS: BookProjects[] = [
  {
    id: 'book-999',
    series_id: 'series-1',
    book_number: 9,
    version: 1,
    position: 9,
    projects: [],
  },
];

describe('openBook — canonical switch + invalidation ordering', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.pipelineBookId = 'book-123';
    state.pipelineRenderJobId = 'job-prev';
    state.workbench = {} as never;
    state.workbenchConfig = {} as never;
    localStorage.clear();
    localStorage.setItem(LS_PIPELINE_BOOK_ID, 'book-123');
    document.body.innerHTML = '<div id="projects-list"></div>';
    vi.mocked(API.get).mockResolvedValue(OTHER_BOOKS);
  });

  afterEach(() => {
    state.pipelineBookId = null;
    state.pipelineRenderJobId = null;
    state.workbench = null;
    state.workbenchConfig = null;
    localStorage.clear();
    document.body.innerHTML = '';
  });

  it('invalidates the old book, clears book-scoped state, then commits via setPipelineBookId exactly once', async () => {
    await openBook('book-999');

    // Invalidation before the switch.
    expect(resetRenderStateForBookReplacement).toHaveBeenCalledTimes(1);
    expect(clearWorkbenchUndoStack).toHaveBeenCalledTimes(1);

    // Canonical persistence: state changed + localStorage written.
    expect(state.pipelineBookId).toBe('book-999');
    expect(localStorage.getItem(LS_PIPELINE_BOOK_ID)).toBe('book-999');

    // Load the newly-selected book's data.
    expect(loadSpans).toHaveBeenCalled();
    expect(loadSingleSpeakerToggle).toHaveBeenCalled();
    expect(loadWorkbench).toHaveBeenCalledWith(true);
    expect(loadWorkbenchConfig).toHaveBeenCalled();
  });

  it('clears book-scoped shared state (render job, workbench, config) on switch', async () => {
    await openBook('book-999');

    expect(state.pipelineBookId).toBe('book-999');
    expect(state.pipelineRenderJobId).toBeNull();
    expect(state.workbench).toBeNull();
    expect(state.workbenchConfig).toBeNull();
  });

  it('is a no-op (no reset, no persistence, no data load) when opening the already-selected book', async () => {
    await openBook('book-123');

    expect(resetRenderStateForBookReplacement).not.toHaveBeenCalled();
    expect(clearWorkbenchUndoStack).not.toHaveBeenCalled();
    expect(loadSpans).not.toHaveBeenCalled();
    // Canonical state is untouched.
    expect(state.pipelineBookId).toBe('book-123');
    expect(localStorage.getItem(LS_PIPELINE_BOOK_ID)).toBe('book-123');
  });

  it('is a no-op for an empty / falsy book id', async () => {
    await openBook('');
    await openBook(undefined as unknown as string);

    expect(resetRenderStateForBookReplacement).not.toHaveBeenCalled();
    expect(state.pipelineBookId).toBe('book-123');
  });

  it('retains the prior canonical book when invalidation fails before the switch commits', async () => {
    vi.mocked(resetRenderStateForBookReplacement).mockRejectedValueOnce(new Error('cancel failed'));

    await openBook('book-999');

    // Switch was NOT committed: canonical state + persistence untouched.
    expect(state.pipelineBookId).toBe('book-123');
    expect(localStorage.getItem(LS_PIPELINE_BOOK_ID)).toBe('book-123');
    expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining('Failed to switch book'),
      'error',
    );
    // No data load happened for a book we did not switch to.
    expect(loadSpans).not.toHaveBeenCalled();
  });

  it('refreshes the project list after a successful switch', async () => {
    await openBook('book-999');

    // loadProjects() after the switch GETs the books endpoint.
    expect(API.get).toHaveBeenCalledWith('/api/pipeline/books');
  });
});

describe('initProjects — Open row action delegation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.pipelineBookId = 'book-123';
    localStorage.clear();
    localStorage.setItem(LS_PIPELINE_BOOK_ID, 'book-123');
    document.body.innerHTML = `
      <button id="btn-project-save"></button>
      <div id="projects-list">
        <button data-action="project-open" data-book-id="book-999">Open</button>
      </div>
    `;
    vi.mocked(API.get).mockResolvedValue([]);
    vi.mocked(API.post).mockResolvedValue({});
  });

  afterEach(() => {
    state.pipelineBookId = null;
    localStorage.clear();
    document.body.innerHTML = '';
  });

  it('delegates a project-open click to openBook (committing the switch)', async () => {
    initProjects();
    document.dispatchEvent(new Event('DOMContentLoaded'));

    (document.querySelector('[data-action="project-open"]') as HTMLButtonElement).click();
    // Let the async openBook transition settle.
    await vi.waitFor(() => expect(state.pipelineBookId).toBe('book-999'));

    expect(resetRenderStateForBookReplacement).toHaveBeenCalled();
    expect(localStorage.getItem(LS_PIPELINE_BOOK_ID)).toBe('book-999');
  });
});
