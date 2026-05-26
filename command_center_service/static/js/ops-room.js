/**
 * Ops Command Room — orchestrator
 * --------------------------------------------------------------
 * Sits ON TOP of the classic command-center.js + cc-renderers.js +
 * cc-memory.js stack. Does NOT fork them. Responsibilities:
 *
 *   1. Boot and own the dominant Leaflet map. Map points come from
 *      the *active session's persisted map blocks* (via
 *      /api/ops/session-points), NOT a parallel "active points"
 *      store — there is no such store in the CC today.
 *   2. Render the KPI tile strip — every value is sourced from
 *      /api/ops/kpis (sessions, traces_24h, in_flight, points in
 *      this session). No fabricated numbers.
 *   3. Drive the live ticker. Initial load comes from
 *      /api/ops/feed; updates stream in via /api/ops/stream (SSE
 *      broadcast from the chat pipeline). No mock heartbeat.
 *   4. Wire the layer-control overlay (categories derived from
 *      whatever kinds the points actually have) and the selection
 *      drawer (shows the originating message + provenance badges +
 *      a link to the trace inspector).
 *   5. Handle the chat-sidebar tab switch.
 *
 * Integration model: monkey-patch a handful of CC methods so every
 * SSE event the classic UI handles also produces a ticker entry +
 * (where applicable) a KPI bump / a map refresh. Patches are
 * additive — original behavior runs first and unchanged.
 *
 * Anything that the CC genuinely doesn't expose (per-field
 * re-enrichment-on-demand, multi-session aggregate "active points",
 * coverage / alert-rate metrics) has been REMOVED rather than
 * mocked. See CC_OPS_ROOM_DESIGN.md for the audit decisions.
 */

const OpsRoom = {
    // ── State ────────────────────────────────────────────────
    map: null,
    /** @type {Array<{point: object, marker: any}>} */
    markers: [],
    /** category-name → boolean (visible). Populated dynamically from
     *  the categories actually present in /api/ops/session-points. */
    categories: {},
    layers: { points: true },
    /** Latest /api/ops/session-points response. */
    sessionPoints: { points: [], session_map_block_count: 0 },
    /** Map<traceId, sessionId> — used so a ticker entry can deep-link
     *  into the inspect.html page. Populated when CC fires its
     *  `trace` event for the in-progress chat turn. */
    traceSessionMap: new Map(),
    tickEntries: [],
    tickMaxKept: 80,
    /** SSE EventSource for /api/ops/stream. */
    _opsStream: null,
    /** Polling timer for KPIs (cheap, every 10s). The in-flight value
     *  is also bumped/decremented synchronously from the SSE stream so
     *  it feels live; the 10s poll keeps sessions / traces_24h /
     *  map_pts current without flooding. */
    _kpiTimer: null,
    kpis: {
        sessions:        { label: 'SESSIONS',          value: '—', trend: '', tone: 'info' },
        traces_24h:      { label: 'TRACES · 24H',      value: '—', trend: '', tone: 'info' },
        in_flight:       { label: 'IN FLIGHT',         value: '0', trend: '', tone: 'warning' },
        map_pts_session: { label: 'MAP PTS · SESSION', value: '—', trend: '', tone: 'ok' },
    },
    selection: null,

    init() {
        this._renderKpis();
        this._initMap();
        this._initLayerPanel();
        this._initDrawerControls();
        this._initChatTabs();
        this._initSessionListOverlay();
        this._tickerInit();              // seeds from /api/ops/feed
        this._patchCC();
        this._refreshKpis();             // first KPI fetch
        this._refreshSessionPoints();    // first map points fetch
        this._connectOpsStream();        // open /api/ops/stream
        // Refresh KPIs every 10s. Cheap (counts files / sessions).
        this._kpiTimer = setInterval(() => this._refreshKpis(), 10_000);
    },

    // Build the ownership query string the /api/ops/* endpoints now
    // require (BUG-CC-OPS-NOAUTH fix). Mirrors command-center.js _ownerQS:
    // pulls user_id / tenant_id / role from the live CC instance's
    // userContext if available, otherwise from cached localStorage. Returns
    // an EMPTY string only if no identity is available at all — in that
    // case the server (with CC_OPS_AUTH_ENFORCE=1 default) responds 401,
    // which is the intended secure failure.
    _ownerQS() {
        const u = (window.CC && CC.userContext) || {};
        const qp = new URLSearchParams();
        // Fall back to localStorage cache if CC's userContext isn't loaded
        // yet — happens during the very first paint before token validation
        // resolves. Without this the first kpis/feed fetches 401 and the
        // ops UI seeds empty.
        const uid = (u.user_id !== undefined && u.user_id !== null)
            ? u.user_id
            : (localStorage.getItem('cc_user_id') || null);
        const tid = (u.tenant_id !== undefined && u.tenant_id !== null)
            ? u.tenant_id
            : (localStorage.getItem('cc_tenant_id') || null);
        const role = (u.role !== undefined && u.role !== null)
            ? u.role
            : (localStorage.getItem('cc_role') || null);
        if (uid !== null && uid !== '') qp.set('user_id', String(uid));
        if (tid !== null && tid !== '') qp.set('tenant_id', String(tid));
        if (role !== null && role !== '') qp.set('role', String(role));
        return qp.toString();
    },

    // ── KPI strip ────────────────────────────────────────────
    _renderKpis() {
        const strip = document.getElementById('ops-kpi-strip');
        if (!strip) return;
        strip.innerHTML = '';
        for (const k of Object.keys(this.kpis)) {
            const kpi = this.kpis[k];
            const tile = document.createElement('div');
            tile.className = `ops-kpi-tile ops-kpi-${kpi.tone}`;
            tile.dataset.kpi = k;
            tile.innerHTML = `
                <div class="ops-kpi-label">${kpi.label}</div>
                <div class="ops-kpi-value" data-kpi-value>${kpi.value}</div>
                <div class="ops-kpi-trend ${kpi.trendDir || ''}">${kpi.trend || ' '}</div>
            `;
            strip.appendChild(tile);
        }
    },

    _setKpi(key, value, trend = '', trendDir = '') {
        const kpi = this.kpis[key];
        if (!kpi) return;
        kpi.value = value;
        kpi.trend = trend;
        kpi.trendDir = trendDir;
        const tile = document.querySelector(`.ops-kpi-tile[data-kpi="${key}"]`);
        if (!tile) return;
        const vEl = tile.querySelector('[data-kpi-value]');
        if (vEl) vEl.textContent = value;
        const tEl = tile.querySelector('.ops-kpi-trend');
        if (tEl) {
            tEl.textContent = trend || ' ';
            tEl.className = `ops-kpi-trend ${trendDir}`;
        }
    },

    async _refreshKpis() {
        // Build session_id query so map_pts_session is scoped to whatever
        // session the chat is on. CC owns the session id; we read it from
        // localStorage or the live CC instance.
        const sid = (window.CC && CC.sessionId) || localStorage.getItem('cc_session_id') || '';
        const ownerQS = this._ownerQS();
        const parts = [];
        if (sid) parts.push(`session_id=${encodeURIComponent(sid)}`);
        if (ownerQS) parts.push(ownerQS);
        const url = parts.length
            ? `/api/ops/kpis?${parts.join('&')}`
            : '/api/ops/kpis';
        try {
            const resp = await fetch(url);
            if (!resp.ok) return;
            const data = await resp.json();
            for (const key of Object.keys(this.kpis)) {
                const k = data[key];
                if (!k || typeof k !== 'object') continue;
                this._setKpi(key, String(k.value), k.trend || '', '');
            }
            // graph_ready false → push a warn into the ticker (don't
            // overload a KPI tile with semantics it doesn't carry).
            if (data.graph_ready === false) {
                this._addTick({ kind: 'alert', text: 'graph · NOT READY (check /api/health)' });
            }
        } catch (e) {
            console.warn('[ops] KPI refresh failed:', e);
        }
    },

    // ── Map ──────────────────────────────────────────────────
    _initMap() {
        if (!window.L) {
            console.warn('[ops] Leaflet not loaded — map disabled');
            return;
        }
        const el = document.getElementById('ops-map');
        if (!el) return;

        // Wide initial view; bounds get tightened to actual data on the
        // first session-points fetch (see _renderSessionPoints).
        this.map = L.map(el, {
            center: [20, 0],
            zoom: 2,
            zoomControl: true,
            attributionControl: true,
        });
        el.classList.add('ops-map-dark');

        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 18,
        }).addTo(this.map);

        // No seed markers. The map starts empty until /api/ops/session-points
        // returns a session whose assistant messages contain map blocks.
    },

    _addPoint(p) {
        if (!this.map || !window.L) return;
        const html = `<div class="ops-marker ops-marker-${this._safeKindClass(p.kind)}"></div>`;
        const icon = L.divIcon({
            className: 'ops-divicon',
            html,
            iconSize: [14, 14],
            iconAnchor: [7, 7],
        });
        const marker = L.marker([p.lat, p.lng], { icon }).addTo(this.map);
        marker.on('click', () => this._showSelection(p));
        marker.bindTooltip(p.name || p.id, { direction: 'top', offset: [0, -8], className: 'ops-marker-popup' });
        this.markers.push({ point: p, marker });
    },

    _clearMarkers() {
        for (const m of this.markers) {
            try { this.map && this.map.removeLayer(m.marker); } catch (_) {}
        }
        this.markers = [];
    },

    /** Pull the active session's points from the backend and re-render. */
    async _refreshSessionPoints() {
        const sid = (window.CC && CC.sessionId) || localStorage.getItem('cc_session_id') || '';
        if (!sid) {
            // No session yet — just clear and bail.
            this._clearMarkers();
            this.sessionPoints = { points: [], session_map_block_count: 0 };
            this._refreshCategoryControls();
            return;
        }
        try {
            const ownerQS = this._ownerQS();
            const url = ownerQS
                ? `/api/ops/session-points?session_id=${encodeURIComponent(sid)}&${ownerQS}`
                : `/api/ops/session-points?session_id=${encodeURIComponent(sid)}`;
            const resp = await fetch(url);
            if (!resp.ok) return;
            const data = await resp.json();
            this.sessionPoints = data || { points: [], session_map_block_count: 0 };
            this._renderSessionPoints();
        } catch (e) {
            console.warn('[ops] session-points fetch failed:', e);
        }
    },

    _renderSessionPoints() {
        this._clearMarkers();
        const points = (this.sessionPoints && this.sessionPoints.points) || [];
        if (!points.length) {
            this._refreshCategoryControls();
            return;
        }
        // Apply category visibility (every category defaults to ON when first seen).
        for (const p of points) {
            const cat = this._safeKindClass(p.kind);
            if (!(cat in this.categories)) this.categories[cat] = true;
        }
        for (const p of points) {
            if (!this.layers.points) break;
            const cat = this._safeKindClass(p.kind);
            if (!this.categories[cat]) continue;
            this._addPoint(p);
        }
        // Fit bounds to the markers we actually rendered.
        if (this.markers.length && this.map && window.L) {
            try {
                const bounds = L.latLngBounds(this.markers.map(m => [m.point.lat, m.point.lng]));
                this.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 10 });
            } catch (_) { /* ignore */ }
        }
        this._refreshCategoryControls();
    },

    _safeKindClass(kind) {
        const k = String(kind || 'point').toLowerCase().replace(/[^a-z0-9_]/g, '');
        return k || 'point';
    },

    // ── Layer panel ──────────────────────────────────────────
    _initLayerPanel() {
        const panel = document.getElementById('ops-layer-panel');
        const toggle = document.getElementById('ops-layer-toggle');
        if (!panel || !toggle) return;

        toggle.addEventListener('click', () => {
            panel.classList.toggle('collapsed');
        });
        panel.addEventListener('mouseenter', () => panel.classList.remove('collapsed'));

        panel.querySelectorAll('input[type=checkbox][data-layer]').forEach(cb => {
            cb.addEventListener('change', () => {
                this.layers[cb.dataset.layer] = cb.checked;
                this._renderSessionPoints();
            });
        });
        const refresh = document.getElementById('ops-layer-refresh');
        if (refresh) {
            refresh.addEventListener('click', () => {
                this._refreshSessionPoints();
                this._addTick({ kind: 'info', text: 'map · refreshed from session' });
            });
        }
    },

    _refreshCategoryControls() {
        const list = document.getElementById('ops-cat-list');
        if (!list) return;
        const cats = Object.keys(this.categories).sort();
        if (!cats.length) {
            list.innerHTML = '<p class="ops-cat-empty">No categories yet.</p>';
            return;
        }
        list.innerHTML = '';
        for (const cat of cats) {
            const id = `ops-cat-cb-${cat}`;
            const checked = this.categories[cat] ? 'checked' : '';
            const label = document.createElement('label');
            label.innerHTML = `<input type="checkbox" id="${id}" ${checked}> ${this._esc(cat)}`;
            list.appendChild(label);
            const cb = label.querySelector('input');
            cb.addEventListener('change', () => {
                this.categories[cat] = cb.checked;
                this._renderSessionPoints();
            });
        }
    },

    // ── Selection drawer ─────────────────────────────────────
    _initDrawerControls() {
        const close = document.getElementById('ops-drawer-close');
        if (close) close.addEventListener('click', () => this._closeSelection());
        document.querySelectorAll('.ops-drawer-action').forEach(btn => {
            btn.addEventListener('click', () => this._handleDrawerAction(btn.dataset.action));
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') this._closeSelection();
        });
    },

    _showSelection(p) {
        this.selection = p;
        const drawer = document.getElementById('ops-selection-drawer');
        const eyebrow = document.getElementById('ops-drawer-eyebrow');
        const title = document.getElementById('ops-drawer-title');
        const body = document.getElementById('ops-drawer-body');
        if (!drawer || !title || !body) return;

        eyebrow.textContent = (p.kind || 'point').toUpperCase();
        title.textContent = p.name || p.id || 'Selection';
        const latStr = (typeof p.lat === 'number') ? p.lat.toFixed(4) : '—';
        const lngStr = (typeof p.lng === 'number') ? p.lng.toFixed(4) : '—';

        // Field rows. The provenance map for a marker is keyed by
        // marker-relative paths (lat / lng / label / detail) — see
        // routes/ops.py: _extract_marker_provenance.
        const fields = [
            ['id',     'ID',         this._esc(p.id || '—')],
            ['kind',   'Kind',       this._esc(p.kind || '—')],
            ['lat',    'Lat',        latStr],
            ['lng',    'Lng',        lngStr],
        ];
        if (p.detail) fields.push(['detail', 'Detail', this._esc(p.detail)]);

        const rows = fields.map(([key, label, html]) => `
            <dt>${this._esc(label)}</dt>
            <dd>${html}${this._renderProvenanceBadge(p._provenance, key)}</dd>
        `).join('');

        // Provenance for the originating block: we know the message_index
        // and block_title from the backend. If the response carried a
        // trace_id we'd link it directly; for now point at the inspector
        // page using the active session.
        const sid = (window.CC && CC.sessionId) || localStorage.getItem('cc_session_id') || '';
        const messageInfo = (typeof p.message_index === 'number')
            ? `message #${p.message_index + 1}` + (p.block_title ? ` · "${this._esc(p.block_title)}"` : '')
            : '—';

        body.innerHTML = `
            <dl>
                ${rows}
                <dt>From</dt><dd>${messageInfo}</dd>
                <dt>Session</dt><dd><code>${this._esc(sid || '—')}</code></dd>
            </dl>
            <div class="ops-drawer-hint">
                Source attribution comes from the <code>_provenance</code> sibling map
                on the original map block. See <code>docs/data-provenance.md</code>.
            </div>
        `;
        body.querySelectorAll('.ops-prov-badge').forEach(el => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                const tip = el.querySelector('.ops-prov-tip');
                if (!tip) return;
                const isOpen = !tip.hasAttribute('hidden');
                body.querySelectorAll('.ops-prov-tip').forEach(t => t.setAttribute('hidden', ''));
                if (!isOpen) tip.removeAttribute('hidden');
            });
        });

        drawer.classList.add('open');
        drawer.setAttribute('aria-hidden', 'false');
    },

    _renderProvenanceBadge(provMap, path) {
        if (!provMap || typeof provMap !== 'object') {
            return ` <span class="ops-prov-badge ops-prov-missing" title="No source recorded">?</span>`;
        }
        const entry = provMap[path];
        if (!entry || typeof entry !== 'object') {
            return ` <span class="ops-prov-badge ops-prov-missing" title="No source recorded">?</span>`;
        }
        const src = String(entry.source || 'unknown');
        const confidence = (typeof entry.confidence === 'number') ? entry.confidence : null;
        const lowConf = (confidence !== null && confidence < 0.3);
        const label = src.replace(/_/g, ' ');
        const cls = lowConf ? 'ops-prov-badge ops-prov-low' : 'ops-prov-badge';
        const ts = entry.timestamp ? this._esc(entry.timestamp) : '—';
        const detail = entry.source_detail ? this._esc(entry.source_detail) : '';
        const url = entry.source_url
            ? `<a href="${this._esc(entry.source_url)}" target="_blank" rel="noopener">${this._esc(entry.source_url)}</a>`
            : '—';
        const confStr = (confidence === null) ? '—' : confidence.toFixed(2);
        const notes = entry.notes ? this._esc(entry.notes) : '—';
        return ` <span class="${cls}" data-source="${this._esc(src)}" tabindex="0" title="Source: ${this._esc(label)} (${confStr})">${this._esc(label)}<span class="ops-prov-tip" hidden>
            <dt>Source</dt><dd>${this._esc(label)}${detail ? ' · ' + detail : ''}</dd>
            <dt>URL</dt><dd>${url}</dd>
            <dt>Timestamp</dt><dd>${ts}</dd>
            <dt>Confidence</dt><dd>${confStr}</dd>
            <dt>Notes</dt><dd>${notes}</dd>
        </span></span>`;
    },

    _closeSelection() {
        const drawer = document.getElementById('ops-selection-drawer');
        if (!drawer) return;
        drawer.classList.remove('open');
        drawer.setAttribute('aria-hidden', 'true');
        this.selection = null;
    },

    _handleDrawerAction(action) {
        const p = this.selection;
        if (!p) return;
        switch (action) {
            case 'ask': {
                const input = document.getElementById('user-input');
                if (input) {
                    input.value = `Tell me what you know about ${p.name || p.id} (lat ${p.lat}, lng ${p.lng}).`;
                    input.focus();
                }
                this._showChatTab('conversation');
                break;
            }
            case 'copy': {
                // Copy the point payload (incl. provenance) so the user can
                // paste it into a follow-up question or a bug report.
                try {
                    const text = JSON.stringify(p, null, 2);
                    if (navigator.clipboard && navigator.clipboard.writeText) {
                        navigator.clipboard.writeText(text);
                        this._addTick({ kind: 'ok', text: `point · copied to clipboard (${p.id})` });
                    } else {
                        // Fallback: dump into the chat input.
                        const input = document.getElementById('user-input');
                        if (input) input.value = '```json\n' + text + '\n```';
                    }
                } catch (e) {
                    this._addTick({ kind: 'warn', text: `point · copy failed (${e.message || e})` });
                }
                break;
            }
            case 'trace': {
                // Open the existing inspector page. We don't have a per-point
                // trace_id (points are extracted from a stored block, not a
                // live agent run), so we open the session-scoped inspector.
                const sid = (window.CC && CC.sessionId) || localStorage.getItem('cc_session_id') || '';
                const uid = (window.CC && CC.userId) || localStorage.getItem('cc_user_id') || 'anon';
                const tid = CC && CC.lastTraceId ? CC.lastTraceId : '';
                if (tid) {
                    const url = `/static/inspect.html?trace_id=${encodeURIComponent(tid)}&user_id=${encodeURIComponent(uid)}&session_id=${encodeURIComponent(sid)}`;
                    window.open(url, '_blank');
                } else {
                    // Fall back to the trace pane in the sidebar.
                    this._showChatTab('trace');
                    this._addTick({ kind: 'info', text: 'trace · no live trace_id yet — switched to TRACE pane' });
                }
                break;
            }
        }
    },

    // ── Chat tabs ────────────────────────────────────────────
    _initChatTabs() {
        document.querySelectorAll('.ops-chat-tab').forEach(btn => {
            btn.addEventListener('click', () => this._showChatTab(btn.dataset.tab));
        });
    },

    _showChatTab(name) {
        document.querySelectorAll('.ops-chat-tab').forEach(b => {
            b.classList.toggle('active', b.dataset.tab === name);
        });
        document.querySelectorAll('.ops-chat-pane').forEach(p => {
            p.hidden = p.dataset.pane !== name;
        });
    },

    // ── Sessions overlay ─────────────────────────────────────
    _initSessionListOverlay() {
        const overlay = document.getElementById('ops-session-overlay');
        if (!overlay) return;
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) this.toggleSessionList(false);
        });
        const list = document.getElementById('session-list');
        if (list) {
            list.addEventListener('click', (e) => {
                const item = e.target.closest('.cc-session-item');
                if (item) {
                    setTimeout(() => {
                        this.toggleSessionList(false);
                        // After a session switch, the new session's map points
                        // are usually different — refresh.
                        this._refreshSessionPoints();
                        this._refreshKpis();
                    }, 60);
                }
            });
        }
    },

    toggleSessionList(force) {
        const overlay = document.getElementById('ops-session-overlay');
        if (!overlay) return;
        const willOpen = (force === undefined) ? overlay.hasAttribute('hidden') : !!force;
        if (willOpen) {
            overlay.removeAttribute('hidden');
            if (window.CC && typeof CC.loadSessions === 'function') CC.loadSessions();
        } else {
            overlay.setAttribute('hidden', '');
        }
    },

    // ── Ticker ───────────────────────────────────────────────
    async _tickerInit() {
        // Seed from the backend's recent-activity endpoint. If it returns
        // nothing (fresh install), the ticker just stays empty until the
        // first SSE event arrives.
        try {
            const ownerQS = this._ownerQS();
            const url = ownerQS
                ? `/api/ops/feed?limit=10&${ownerQS}`
                : '/api/ops/feed?limit=10';
            const resp = await fetch(url);
            if (resp.ok) {
                const data = await resp.json();
                const entries = (data && data.entries) || [];
                // Render newest LAST so the ticker (newest-first via insertBefore)
                // ends up sorted descending.
                for (const e of entries.slice().reverse()) {
                    const t = e.ts ? Date.parse(e.ts) : Date.now();
                    this._addTick({ kind: e.kind || 'info', text: e.text, t, traceId: e.trace_id }, /*flash*/ false);
                }
            }
        } catch (e) {
            console.warn('[ops] ticker seed failed:', e);
        }
    },

    _connectOpsStream() {
        if (!window.EventSource) {
            console.warn('[ops] EventSource unavailable — ticker live updates disabled');
            return;
        }
        try {
            const ownerQS = this._ownerQS();
            const streamUrl = ownerQS
                ? `/api/ops/stream?${ownerQS}`
                : '/api/ops/stream';
            this._opsStream = new EventSource(streamUrl);
            this._opsStream.addEventListener('ops', (msg) => {
                let data = {};
                try { data = JSON.parse(msg.data); } catch (_) {}
                const t = data.ts ? Date.parse(data.ts) : Date.now();
                this._addTick({
                    kind: data.kind || 'info',
                    text: data.text || '',
                    t,
                    traceId: data.trace_id,
                    sessionId: data.session_id,
                });
                // Bump in-flight live so the KPI feels real-time even before
                // the 10s poll. The poll then corrects any drift.
                if (typeof data.text === 'string') {
                    if (data.text.includes('request open')) {
                        this._setKpi('in_flight', String(this._extractCount(data.text) || 1), 'live', '');
                    } else if (data.text.includes('request closed')) {
                        const n = this._extractCount(data.text);
                        this._setKpi('in_flight', String(n != null ? n : 0), 'live', '');
                    }
                }
            });
            this._opsStream.addEventListener('ready', () => {
                this._addTick({ kind: 'ok', text: 'ops · stream connected' });
            });
            this._opsStream.onerror = () => {
                // Browser will auto-reconnect; just log a soft warning.
                this._addTick({ kind: 'warn', text: 'ops · stream reconnecting…' });
            };
        } catch (e) {
            console.warn('[ops] failed to open ops stream:', e);
        }
    },

    /** Extract the "(N in flight)" count out of a broadcast text. */
    _extractCount(text) {
        const m = String(text).match(/\((\d+)\s+in flight\)/);
        return m ? parseInt(m[1], 10) : null;
    },

    _addTick(entry, flash = true) {
        const track = document.getElementById('ops-ticker-track');
        if (!track) return;
        const t = entry.t || Date.now();
        const tick = document.createElement('span');
        tick.className = 'ops-tick' + (flash ? ' is-new' : '');
        tick.innerHTML = `
            <span class="ops-tick-time">${this._fmtTime(new Date(t))}</span>
            <span class="ops-tick-kind kind-${entry.kind || 'info'}">${entry.kind || 'info'}</span>
            <span class="ops-tick-text">${this._esc(entry.text || '')}</span>
        `;
        tick.addEventListener('click', () => {
            if (entry.traceId) {
                // Open the inspector for this trace_id, scoped to whatever
                // user/session we know about.
                const sid = entry.sessionId
                    || (window.CC && CC.sessionId)
                    || localStorage.getItem('cc_session_id')
                    || '';
                const uid = (window.CC && CC.userId) || localStorage.getItem('cc_user_id') || 'anon';
                const url = `/static/inspect.html?trace_id=${encodeURIComponent(entry.traceId)}&user_id=${encodeURIComponent(uid)}&session_id=${encodeURIComponent(sid)}`;
                window.open(url, '_blank');
            } else {
                this._showChatTab('trace');
            }
        });
        track.insertBefore(tick, track.firstChild);
        this.tickEntries.unshift(entry);
        while (this.tickEntries.length > this.tickMaxKept) {
            this.tickEntries.pop();
            const last = track.lastChild;
            if (last) track.removeChild(last);
        }
        const counter = document.getElementById('ops-ticker-count');
        if (counter) counter.textContent = String(this.tickEntries.length);
        if (flash) setTimeout(() => tick.classList.remove('is-new'), 900);
    },

    // ── CC integration ───────────────────────────────────────
    _patchCC() {
        if (!window.CC) {
            console.warn('[ops] CC not loaded — patching skipped');
            return;
        }
        const origHandle = CC._handleEvent ? CC._handleEvent.bind(CC) : null;
        const origSetStatus = CC._setStatus ? CC._setStatus.bind(CC) : null;
        const origShowTasks = CC._showTasks ? CC._showTasks.bind(CC) : null;

        if (origHandle) {
            CC._handleEvent = (data) => {
                origHandle(data);
                try {
                    if (data._eventType === 'trace' && data.trace_id) {
                        // Remember the trace ↔ session pair so map ticker
                        // clicks can deep-link the right inspector.
                        const sid = data.session_id || CC.sessionId || '';
                        if (sid) this.traceSessionMap.set(data.trace_id, sid);
                    }
                    if (data.phase) {
                        this._addTick({
                            kind: 'info',
                            text: `phase · ${data.phase}${data.message ? ' — ' + data.message : ''}`,
                        });
                    }
                    if (data.blocks && Array.isArray(data.blocks)) {
                        const types = data.blocks.map(b => b.type).filter(Boolean);
                        if (types.length) {
                            this._addTick({
                                kind: 'ok',
                                text: `blocks · ${types.join(', ')}`,
                                traceId: data.trace_id || CC.lastTraceId,
                            });
                        }
                        // If the new response contained a map block, refresh
                        // the dominant map and KPIs — the chat persisted the
                        // assistant message, so /api/ops/session-points
                        // should now find a new map block.
                        if (types.includes('map')) {
                            // Slight delay to give the server time to flush
                            // the message JSON to disk before we re-query.
                            setTimeout(() => {
                                this._refreshSessionPoints();
                                this._refreshKpis();
                            }, 250);
                        }
                    }
                    if (data._eventType === 'builder_log' && data.log) {
                        this._addTick({ kind: 'info', text: `builder · ${data.log.length || 0} entries` });
                    }
                } catch (e) { console.warn('[ops] event mirror error', e); }
            };
        }

        if (origSetStatus) {
            CC._setStatus = (state, text) => {
                origSetStatus(state, text);
                // We DO NOT bump the IN FLIGHT KPI from here anymore — the
                // SSE broadcast carries an authoritative server-side count.
                // Setting it from a client status would mask the real value
                // when multiple operators chat simultaneously.
            };
        }

        if (origShowTasks) {
            CC._showTasks = (tasks) => {
                origShowTasks(tasks);
                try {
                    const inProg = (tasks || []).filter(t => t.status === 'in_progress').length;
                    if ((tasks || []).length > 0) this._showChatTab('trace');
                    if (inProg > 0) {
                        this._addTick({ kind: 'info', text: `tasks · ${tasks.length} total, ${inProg} in progress` });
                    }
                } catch (e) {}
            };
        }

        // When the user switches sessions via CC.loadSession, refresh the
        // map + KPIs (which are session-scoped).
        const origLoadSession = CC.loadSession ? CC.loadSession.bind(CC) : null;
        if (origLoadSession) {
            CC.loadSession = async (sessionId) => {
                await origLoadSession(sessionId);
                this._refreshSessionPoints();
                this._refreshKpis();
            };
        }
        const origCreateSession = CC.createSession ? CC.createSession.bind(CC) : null;
        if (origCreateSession) {
            CC.createSession = async () => {
                await origCreateSession();
                this._refreshSessionPoints();
                this._refreshKpis();
            };
        }
    },

    // ── Helpers ──────────────────────────────────────────────
    _fmtTime(d) {
        try {
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
        } catch (e) {
            return d.toISOString();
        }
    },

    _esc(s) {
        const div = document.createElement('div');
        div.textContent = String(s);
        return div.innerHTML;
    },
};

// Initialize after CC.init() so we patch a fully-bootstrapped CC.
document.addEventListener('DOMContentLoaded', () => {
    Promise.resolve().then(() => OpsRoom.init());
});

// Expose for console / drawer button onclicks
window.OpsRoom = OpsRoom;
