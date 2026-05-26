/**
 * debug-panel.js — runtime LLM inspector for the Data Collection Agent.
 *
 * Visible only when DCA_DEBUG_MODE or DATA_COLLECTION_TEST_MODE is on at the
 * server. Polls /api/data-collection/session/<id>/debug for the per-session
 * event ring buffer and renders each event as a collapsible card so you can
 * see system prompts, tool calls, voice-normalizer calls, LLM responses,
 * and errors as they happen.
 *
 * Designed to be a no-op when the panel isn't in the DOM (i.e. debug
 * mode disabled), so adding `<script src="...debug-panel.js">` is safe
 * everywhere.
 */

class DebugPanel {
    constructor(app) {
        this.app = app;
        this.$panel  = document.getElementById('dcaDebugPanel');
        this.$btn    = document.getElementById('dcaDebugBtn');
        this.$close  = document.getElementById('dcaDebugCloseBtn');
        this.$refresh= document.getElementById('dcaDebugRefreshBtn');
        this.$auto   = document.getElementById('dcaDebugAutoToggle');
        this.$clear  = document.getElementById('dcaDebugClearBtn');
        this.$events = document.getElementById('dcaDebugEvents');
        this.$empty  = document.getElementById('dcaDebugEmpty');
        this.$badge  = document.getElementById('dcaDebugBadge');
        this.$filters= document.querySelectorAll('.dca-debug-filter-chk input[data-filter-type]');
        this.$resizer= document.getElementById('dcaDebugResizer');

        if (!this.$panel || !this.$btn) return; // debug mode is off

        // Restore width from previous session
        this._restoreWidth();

        this.open = false;
        this.autoRefresh = true;
        this.lastTsMs = 0;
        this.events = [];
        this._timer = null;
        // Set of event keys (ts_ms+type) the user has expanded. Persisted
        // across re-renders so auto-refresh polling doesn't collapse them.
        this._openKeys = new Set();
        // Cached active filter snapshot; only when this changes do we
        // wipe-and-rebuild instead of appending.
        this._lastFilterKey = '';
        this._renderedKeys = new Set();
        this._wire();
        // Auto-toggle starts on; reflect that visually
        if (this.$auto) this.$auto.setAttribute('aria-pressed', 'true');
    }

    _wire() {
        this.$btn.addEventListener('click', () => this.toggle());
        if (this.$close) this.$close.addEventListener('click', () => this.close());
        if (this.$refresh) this.$refresh.addEventListener('click', () => this.fetchOnce(true));
        if (this.$auto)    this.$auto.addEventListener('click', () => this._toggleAuto());
        if (this.$clear)   this.$clear.addEventListener('click', () => this._clearBuffer());
        // Filter checkboxes — re-render on toggle
        this.$filters.forEach(c => c.addEventListener('change', () => this._render()));
        // Esc to close
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.open) this.close();
        });
        // Resize handle on the left edge — drag to resize
        if (this.$resizer) this._wireResize();
    }

    // -----------------------------------------------------------------
    // Width persistence + drag-to-resize
    // -----------------------------------------------------------------
    _restoreWidth() {
        try {
            const w = parseInt(localStorage.getItem('dca-debug-panel-width') || '', 10);
            if (w && w > 320 && w <= window.innerWidth) {
                this.$panel.style.width = w + 'px';
            }
        } catch (_) { /* non-fatal */ }
    }

    _wireResize() {
        let startX = 0;
        let startW = 0;
        let dragging = false;

        const onMove = (e) => {
            if (!dragging) return;
            // Right-anchored panel: dragging LEFT widens the panel.
            const dx = startX - e.clientX;
            let next = startW + dx;
            const minW = 320;
            const maxW = Math.min(window.innerWidth, 1400);
            if (next < minW) next = minW;
            if (next > maxW) next = maxW;
            this.$panel.style.width = next + 'px';
            // Don't let the browser select text while we're dragging
            e.preventDefault();
        };
        const onUp = () => {
            if (!dragging) return;
            dragging = false;
            this.$panel.classList.remove('resizing');
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            document.body.style.userSelect = '';
            try {
                const cur = parseInt(this.$panel.style.width, 10);
                if (cur) localStorage.setItem('dca-debug-panel-width', String(cur));
            } catch (_) {}
        };

        this.$resizer.addEventListener('mousedown', (e) => {
            dragging = true;
            startX = e.clientX;
            startW = this.$panel.getBoundingClientRect().width;
            this.$panel.classList.add('resizing');
            document.body.style.userSelect = 'none';
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
            e.preventDefault();
        });

        // Double-click handle to reset to default width
        this.$resizer.addEventListener('dblclick', () => {
            this.$panel.style.width = '';
            try { localStorage.removeItem('dca-debug-panel-width'); } catch (_) {}
        });
    }

    toggle() { this.open ? this.close() : this.openPanel(); }

    openPanel() {
        this.open = true;
        this.$panel.classList.add('open');
        this.$panel.setAttribute('aria-hidden', 'false');
        this.$btn.setAttribute('aria-pressed', 'true');
        // If the session isn't created yet (page just loaded), nudge the
        // panel to retry every 500ms until it appears, so the user
        // doesn't see "Waiting for session…" forever.
        let waits = 0;
        const tryFetch = () => {
            if (!this.open) return;
            if (this.app && this.app.sessionId) {
                this.fetchOnce(false);
                if (this.autoRefresh) this._startAuto();
                return;
            }
            this.fetchOnce(false);  // shows the warning
            if (waits++ < 20) setTimeout(tryFetch, 500);
        };
        tryFetch();
    }

    close() {
        this.open = false;
        this.$panel.classList.remove('open');
        this.$panel.setAttribute('aria-hidden', 'true');
        this.$btn.setAttribute('aria-pressed', 'false');
        this._stopAuto();
    }

    _toggleAuto() {
        this.autoRefresh = !this.autoRefresh;
        this.$auto.setAttribute('aria-pressed', this.autoRefresh ? 'true' : 'false');
        if (this.autoRefresh && this.open) this._startAuto();
        else this._stopAuto();
    }

    _startAuto() {
        this._stopAuto();
        this._timer = setInterval(() => this.fetchOnce(false), 1500);
    }
    _stopAuto() {
        if (this._timer) { clearInterval(this._timer); this._timer = null; }
    }

    async fetchOnce(reset) {
        const sid = this.app && this.app.sessionId;
        if (!sid) {
            this._setStatus('warn', 'Waiting for session to be created…');
            return;
        }
        if (reset) {
            this.lastTsMs = 0;
            this.events = [];
            this._renderedKeys.clear();
            this._lastFilterKey = '';
        }
        try {
            const url = `/api/data-collection/session/${encodeURIComponent(sid)}/debug`
                      + (this.lastTsMs ? `?since_ms=${this.lastTsMs}` : '');
            const resp = await fetch(url, { credentials: 'same-origin' });
            if (!resp.ok) {
                const text = await resp.text().catch(() => '');
                console.warn('[debug-panel] fetch non-OK', resp.status, text.slice(0, 300));
                this._setStatus('error',
                    `HTTP ${resp.status} from /debug. ${text.slice(0, 200)}`);
                return;
            }
            const body = await resp.json();
            if (body.status !== 'success') {
                this._setStatus('error', `unexpected response: ${JSON.stringify(body).slice(0, 200)}`);
                return;
            }
            if (body.enabled === false) {
                this._setStatus('warn',
                    'Server says debug mode is OFF. Set DATA_COLLECTION_TEST_MODE=True or DCA_DEBUG_MODE=1 and restart.');
                return;
            }
            this._setStatus('ok', '');
            const newEvents = body.events || [];
            if (newEvents.length) {
                this.events = this.events.concat(newEvents);
                this.lastTsMs = newEvents[newEvents.length - 1].ts_ms || this.lastTsMs;
                // Cap the in-memory event list to avoid unbounded growth
                if (this.events.length > 1000) {
                    this.events = this.events.slice(-1000);
                }
            }
            this._render();
        } catch (e) {
            console.warn('[debug-panel] fetch failed:', e);
            this._setStatus('error', `fetch failed: ${e.message || e}`);
        }
    }

    _setStatus(level, msg) {
        // Optional inline status banner inside the empty-state. Helps the
        // user see *why* no events are showing. Re-uses the empty-state
        // element rather than introducing a separate banner.
        if (!this.$empty) return;
        if (!msg && level === 'ok') {
            // Ok + empty → restore default empty-state text
            return;
        }
        const colors = {
            ok:    '',
            warn:  'color:#fde68a;',
            error: 'color:#fca5a5;',
        };
        this.$empty.style.cssText = `padding:1rem 0.85rem;text-align:center;${colors[level] || ''}`;
        this.$empty.textContent = msg || '';
        // Make sure empty banner is visible
        Array.from(this.$events.children).forEach(c => {
            if (c !== this.$empty) c.remove();
        });
        this._renderedKeys.clear();
        this.$events.appendChild(this.$empty);
        this.$empty.style.display = '';
    }

    async _clearBuffer() {
        const sid = this.app && this.app.sessionId;
        if (!sid) return;
        if (!confirm('Clear the debug buffer for this session?')) return;
        try {
            await fetch(`/api/data-collection/session/${encodeURIComponent(sid)}/debug`, {
                method: 'DELETE',
                credentials: 'same-origin',
            });
        } catch (_) {}
        this.events = [];
        this.lastTsMs = 0;
        this._renderedKeys.clear();
        this._openKeys.clear();
        // Force a wipe-and-rebuild on next _render
        this._lastFilterKey = '';
        this._render();
    }

    _activeFilter() {
        const set = new Set();
        this.$filters.forEach(c => { if (c.checked) set.add(c.dataset.filterType); });
        return set;
    }

    _eventKey(e) {
        // Stable per-event identity for tracking expanded state. ts_ms +
        // type is unique enough; ts_ms is integer ms so identical events
        // would have to land in the same millisecond.
        return `${e.ts_ms || 0}:${e.type || ''}`;
    }

    _render() {
        const filter = this._activeFilter();
        const list = this.events.filter(e => filter.has(e.type));
        const filterKey = Array.from(filter).sort().join(',');
        const filterChanged = filterKey !== this._lastFilterKey;
        this._lastFilterKey = filterKey;

        // Total tool-call count: every time a tool was actually
        // dispatched, the tool's own code emits a `tool_call` event.
        // (The `tool_call_count` field on `note` events from the
        // on_llm_end callback only counts what the LLM proposed; it
        // doesn't tell you what actually ran.) Count by tool name too
        // so the user can see distribution at a glance.
        const toolCallEvents = this.events.filter(e => e.type === 'tool_call');
        const toolCallTotal = toolCallEvents.length;
        const byTool = {};
        for (const e of toolCallEvents) {
            const name = (e.payload && e.payload.tool) || '?';
            byTool[name] = (byTool[name] || 0) + 1;
        }
        const toolBreakdown = Object.entries(byTool)
            .sort((a, b) => b[1] - a[1])
            .map(([n, c]) => `${n}×${c}`)
            .join(', ');
        if (this.$badge) {
            this.$badge.textContent =
                `${list.length} of ${this.events.length} events · ${toolCallTotal} tool calls`;
            this.$badge.title = toolBreakdown
                ? `Tool calls so far: ${toolBreakdown}`
                : 'No tool calls yet this session';
        }

        if (!list.length) {
            // Clear and show empty state
            Array.from(this.$events.children).forEach(c => {
                if (c !== this.$empty) c.remove();
            });
            this._renderedKeys.clear();
            this.$events.appendChild(this.$empty);
            this.$empty.style.display = '';
            this.$empty.textContent = this.events.length
                ? 'No events match the current filters.'
                : "No events yet. Send a message to see the LLM call flow appear here.";
            return;
        }
        this.$empty.style.display = 'none';

        // If filters changed, wipe and rebuild from scratch (preserving
        // open state for events we re-render). Otherwise append-only so
        // the user's currently-expanded card doesn't collapse mid-read.
        if (filterChanged) {
            Array.from(this.$events.children).forEach(c => {
                if (c !== this.$empty) c.remove();
            });
            this._renderedKeys.clear();
        }

        // Newest first — but we INSERT new ones at the top of the list to
        // avoid disturbing existing DOM nodes (which preserves their
        // expanded state and any text the user might be selecting).
        const reversed = list.slice().reverse();
        for (const e of reversed) {
            const key = this._eventKey(e);
            if (this._renderedKeys.has(key)) continue;
            const el = this._buildEventEl(e);
            this._renderedKeys.add(key);
            // Insert at top
            if (this.$events.firstChild && this.$events.firstChild !== this.$empty) {
                this.$events.insertBefore(el, this.$events.firstChild);
            } else {
                this.$events.appendChild(el);
            }
        }
    }

    _buildEventEl(e) {
        const key = this._eventKey(e);
        const t = (e.ts || '').slice(11, 19);  // HH:MM:SS
        const summary = this._summary(e);
        const body = this._body(e);

        const card = document.createElement('div');
        card.className = 'dca-debug-event';
        card.dataset.key = key;
        if (this._openKeys.has(key)) card.classList.add('open');

        // "Expand" button on every event — opens a large formatted
        // modal so the user can read long payloads (full system
        // prompt, raw LLM response, large extraction outputs)
        // without squeezing into the side panel.
        card.innerHTML = `
            <div class="dca-debug-event-header">
                <i class="fas fa-chevron-right dca-debug-chev"></i>
                <span class="dca-debug-event-type t-${this._escape(e.type)}">${this._escape(e.type)}</span>
                <span class="dca-debug-event-summary">${this._escape(summary)}</span>
                <span class="dca-debug-event-time">${this._escape(t)}</span>
                <button class="dca-debug-event-expand" title="Open in large window">
                    <i class="fas fa-up-right-and-down-left-from-center"></i>
                </button>
            </div>
            <pre class="dca-debug-event-body">${this._escape(body)}</pre>
        `;

        const header = card.querySelector('.dca-debug-event-header');
        const expandBtn = card.querySelector('.dca-debug-event-expand');
        header.addEventListener('click', (ev) => {
            // Don't toggle when the expand button itself was clicked.
            if (ev.target.closest('.dca-debug-event-expand')) return;
            const willOpen = !card.classList.contains('open');
            card.classList.toggle('open', willOpen);
            if (willOpen) this._openKeys.add(key);
            else          this._openKeys.delete(key);
        });
        if (expandBtn) {
            expandBtn.addEventListener('click', (ev) => {
                ev.stopPropagation();
                this._openInModal(e);
            });
        }
        return card;
    }

    _openInModal(e) {
        // Lazily create the modal once and reuse it.
        if (!this._modal) {
            const modal = document.createElement('div');
            modal.className = 'dca-debug-modal';
            modal.innerHTML = `
                <div class="dca-debug-modal-card">
                    <div class="dca-debug-modal-header">
                        <span class="dca-debug-modal-type"></span>
                        <span class="dca-debug-modal-time"></span>
                        <span class="dca-debug-modal-summary"></span>
                        <button class="dca-debug-modal-copy" title="Copy to clipboard">
                            <i class="fas fa-copy"></i>
                        </button>
                        <button class="dca-debug-modal-close" title="Close (Esc)">
                            <i class="fas fa-xmark"></i>
                        </button>
                    </div>
                    <pre class="dca-debug-modal-body"></pre>
                </div>
            `;
            document.body.appendChild(modal);
            this._modal = modal;
            modal.addEventListener('click', (ev) => {
                // Click the backdrop (not the card) closes
                if (ev.target === modal) this._closeModal();
            });
            modal.querySelector('.dca-debug-modal-close')
                 .addEventListener('click', () => this._closeModal());
            modal.querySelector('.dca-debug-modal-copy')
                 .addEventListener('click', () => this._copyModalBody());
            document.addEventListener('keydown', (ev) => {
                if (ev.key === 'Escape' && this._modal && this._modal.classList.contains('open')) {
                    this._closeModal();
                }
            });
        }
        const modal = this._modal;
        const t = (e.ts || '').slice(11, 19);
        const body = this._body(e);
        modal.querySelector('.dca-debug-modal-type').textContent = e.type || '';
        modal.querySelector('.dca-debug-modal-type').className =
            `dca-debug-modal-type dca-debug-event-type t-${e.type || ''}`;
        modal.querySelector('.dca-debug-modal-time').textContent = t;
        modal.querySelector('.dca-debug-modal-summary').textContent = this._summary(e) || '';
        modal.querySelector('.dca-debug-modal-body').textContent = body;
        modal.classList.add('open');
        // Stash for the copy button
        this._modalCurrentBody = body;
    }

    _closeModal() {
        if (this._modal) this._modal.classList.remove('open');
    }

    async _copyModalBody() {
        const text = this._modalCurrentBody || '';
        try {
            await navigator.clipboard.writeText(text);
            const btn = this._modal && this._modal.querySelector('.dca-debug-modal-copy');
            if (btn) {
                const orig = btn.innerHTML;
                btn.innerHTML = '<i class="fas fa-check"></i>';
                setTimeout(() => { btn.innerHTML = orig; }, 1200);
            }
        } catch (e) {
            console.warn('[debug-panel] clipboard write failed:', e);
        }
    }

    _summary(e) {
        const p = e.payload || {};
        switch (e.type) {
            case 'turn_start':
                return `phase=${p.phase || '?'} voice_mode=${p.voice_mode}`;
            case 'system_prompt':
                return `${p.length_chars || 0} chars — click to view`;
            case 'user_message':
                return p.message || '(empty)';
            case 'tool_call':
                return `${p.tool || '?'}(${this._compactArgs(p.args)})`;
            case 'tool_result':
                return `${p.tool || '?'} -> ${this._oneLine(p.result || '')}`;
            case 'voice_normalize': {
                const raw = (p.raw || '').toString();
                const cleaned = (p.cleaned || '').toString();
                const same = raw === cleaned ? '(unchanged)' : `-> ${cleaned}`;
                return `${p.field_id || '?'} (${p.field_type || '?'})  "${raw}" ${same}  [${p.confidence || '?'}]`;
            }
            case 'extract_call':
                return `pre-extract on: ${this._oneLine(p.message || '')}`;
            case 'extract_step':
                if (p.step === 'llm_attempts') {
                    const ok = (p.attempts || []).find(a => a.success);
                    return ok ? `attempts: ${(p.attempts || []).length}, succeeded on #${ok.attempt}`
                              : `attempts: ${(p.attempts || []).length}, ALL FAILED`;
                }
                if (p.step === 'llm_raw_response') {
                    return `raw response: ${this._oneLine(p.content || '')}`;
                }
                if (p.step === 'client_ready') {
                    return `client ready (${p.api_type}, model=${p.model || '?'})`;
                }
                if (p.step === 'prompt_built') {
                    return `prompt built (${p.prompt_chars || 0} chars)`;
                }
                return `step: ${p.step || '?'}`;
            case 'extract_result': {
                const applied = p.count_applied || 0;
                const returned = p.count_returned || 0;
                const status = p.status ? ` (${p.status})` : '';
                const rec = (p.records || [])
                    .map(r => `${r.section_id}.${r.field_id}=${JSON.stringify(r.final_value !== null && r.final_value !== undefined ? r.final_value : r.raw_value)}${r.applied ? '' : ' [SKIPPED]'}`)
                    .join('; ');
                return `applied ${applied}/${returned}${status}  ${rec}`;
            }
            case 'llm_response':
                return this._oneLine(p.response || '');
            case 'turn_end':
                return `phase=${p.phase || '?'}  actions=${(p.actions || []).length}`;
            case 'error':
                return `${p.where || ''}: ${p.error || ''}`;
            default:
                return JSON.stringify(p).slice(0, 120);
        }
    }

    _body(e) {
        try {
            return JSON.stringify(e.payload || {}, null, 2);
        } catch (_) {
            return String(e.payload);
        }
    }

    _compactArgs(args) {
        if (!args || typeof args !== 'object') return '';
        return Object.entries(args)
            .map(([k, v]) => `${k}=${this._oneLine(typeof v === 'string' ? v : JSON.stringify(v))}`)
            .join(', ');
    }

    _oneLine(s) {
        s = String(s).replace(/\s+/g, ' ').trim();
        return s.length > 80 ? s.slice(0, 77) + '…' : s;
    }

    _escape(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
}

window.DCADebugPanel = DebugPanel;
