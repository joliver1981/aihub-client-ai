/**
 * cc-studio.js — the Automation Studio panel (Studio Phase A frontend).
 *
 * Docks beside the chat and reacts, live, while CC builds or runs an
 * Automation. All data comes from two read paths — the per-session studio
 * state hint (what is CC doing right now) and the run event sidecar (what is
 * the run doing right now) — plus two write actions (checkpoint decision,
 * abort). The panel NEVER parses chat text and never invents state: every
 * pill, check and outcome is a value the backend actually recorded.
 *
 * Developer-role only (mirrors the CC automation tools gate).
 */
const CCStudio = (() => {
    const PHASES = [
        ['gather',  'Gather'],
        ['create',  'Create'],
        ['code',    'Write code'],
        ['dry_run', 'Dry-run'],
        ['confirm', 'Confirm'],
        ['promote', 'Promote'],
        ['live',    'Live'],
    ];
    const STATE_POLL_MS = 2500;
    const ACTIVE_POLL_MS = 5000;
    const EVENTS_POLL_MS = 1500;
    const MAX_LOG_LINES = 300;

    let stateTimer = null, activeTimer = null, eventsTimer = null;
    let lastVersion = -1, lastAutomationId = null, lastSavedVersion = null;
    let liveRun = null, eventsCursor = 0, runMeta = {};
    let egressSeen = new Set();
    let mainAppUrl = '';
    let hiddenByUser = false;

    // ── plumbing ────────────────────────────────────────────────────────
    function _hdrs() {
        return { 'Authorization': 'Bearer ' + (window.CC && CC.token || localStorage.getItem('cc_token') || '') };
    }
    async function _get(url) {
        const r = await fetch(url, { headers: _hdrs() });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
    }
    async function _post(url, body) {
        const r = await fetch(url, {
            method: 'POST',
            headers: { ..._hdrs(), 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
        return r.json().catch(() => ({}));
    }
    function _isDev() {
        // james 2026-07-21: the panel NEVER opened for anyone because this
        // gate read only localStorage.cc_user_context — which many token
        // flows never store — so init() bailed and the poller never started
        // (chat kept working; it only needs the token). Fall back to the CC
        // JWT itself: its payload carries the verified role claim.
        try {
            const uc = JSON.parse(localStorage.getItem('cc_user_context') || '{}');
            if (parseInt(uc.role, 10) >= 2) return true;
        } catch (e) { /* fall through to the token claim */ }
        try {
            const tok = (window.CC && CC.token) || localStorage.getItem('cc_token') || '';
            const claims = JSON.parse(atob(tok.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
            return parseInt(claims.role, 10) >= 2;
        } catch (e) { return false; }
    }
    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g,
            c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    // ── lifecycle ───────────────────────────────────────────────────────
    function init() {
        // Always start the timer; the Developer check runs per-tick inside
        // _pollState so a token that arrives AFTER page load (async
        // bootstrap) still activates the panel. Non-developers never get
        // studio state server-side, so the panel stays closed for them
        // regardless.
        if (stateTimer) return;
        stateTimer = setInterval(_pollState, STATE_POLL_MS);
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) return;       // timers keep running; polls no-op fast
        });
    }

    function hide() {
        hiddenByUser = true;
        $('studio-panel').style.display = 'none';
        _stopLive();
    }

    function _show() {
        if ($('studio-panel').style.display === 'none') {
            $('studio-panel').style.display = 'flex';
        }
    }

    // ── state poll (the build hint) ─────────────────────────────────────
    async function _pollState() {
        if (document.hidden || !(window.CC && CC.sessionId)) return;
        if (!_isDev()) return;                 // per-tick: heals late-arriving tokens
        let data;
        try {
            data = await _get('/api/studio/state?session_id=' + encodeURIComponent(CC.sessionId));
        } catch (e) { return; }
        mainAppUrl = data.main_app_url || mainAppUrl;
        const st = data.state;
        // Open on ANY active authoring state — the design's Moment 1 is
        // "watch it being BUILT": during the create phase there is a name and
        // working=true but no automation_id yet; the old id-only gate kept
        // the panel shut until creation finished (james: never saw it open).
        if (!st || !(st.automation_id || st.name || st.working)) return;
        if (st.version === lastVersion) return; // no change
        if (st.version !== lastVersion && hiddenByUser && st.version > lastVersion && lastVersion !== -1) {
            hiddenByUser = false;               // new activity re-opens a dismissed panel
        }
        lastVersion = st.version;
        if (!hiddenByUser) { _show(); _render(st); }
    }

    function _render(st) {
        $('studio-name').textContent = st.name || st.automation_id || '';
        $('studio-dot').classList.toggle('idle', !st.working);
        const mc = $('studio-mc-link');
        if (mainAppUrl) mc.href = mainAppUrl.replace(/\/$/, '') + '/automations/';

        // phase rail
        const phase = st.phase || 'gather';
        const idx = Math.max(0, PHASES.findIndex(p => p[0] === phase));
        $('studio-rail').innerHTML = PHASES.map(([key, label], i) => {
            const cls = i < idx ? 'done' : (i === idx ? 'now' : '');
            const tick = i < idx ? '✓' : (i === idx ? '●' : String(i + 1));
            return `<span class="cc-studio-step ${cls}"><span class="t">${tick}</span>${label}</span>`;
        }).join('');

        // automation focus changed → refetch manifest/code if the hint lacks them
        if (st.automation_id !== lastAutomationId) {
            lastAutomationId = st.automation_id;
            lastSavedVersion = null;
            egressSeen = new Set();
            if (!st.manifest || !st.code) _fetchDetail(st.automation_id);
        }
        if (st.manifest) _renderContract(st.manifest);
        if (st.code && st.saved_version !== lastSavedVersion) {
            lastSavedVersion = st.saved_version;
            _typewriter(st.code, st.saved_version);
        }
        if (st.last_run) _renderRunResult(st.last_run);
        _ensureActivePolling(st.automation_id);
    }

    async function _fetchDetail(automationId) {
        try {
            const d = await _get('/api/studio/automation/' + encodeURIComponent(automationId));
            const a = d.automation || {};
            if (a.manifest) _renderContract(a.manifest);
            if (a.code && lastSavedVersion === null) {
                lastSavedVersion = a.current_version;
                _typewriter(a.code, a.current_version);
            }
        } catch (e) { /* hint-only; authoritative fetch can fail quietly */ }
    }

    // ── code typewriter: real saved code, revealed line by line ────────
    function _typewriter(code, version) {
        const sec = $('studio-code-section');
        sec.style.display = '';
        $('studio-code-ver').textContent = version ? ('v' + version) : 'draft';
        const el = $('studio-code');
        const lines = String(code).split('\n').slice(0, 400);
        el.innerHTML = lines.map(l => `<span class="ln">${esc(l) || ' '}</span>`).join('');
        const spans = el.querySelectorAll('.ln');
        const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
        spans.forEach((s, i) => {
            if (reduce) { s.classList.add('on'); return; }
            setTimeout(() => {
                s.classList.add('on');
                if (i % 6 === 0) el.scrollTop = el.scrollHeight;
            }, Math.min(i * 45, 9000));
        });
    }

    // ── the contract card (the manifest, rendered) ─────────────────────
    function _renderContract(m) {
        const sec = $('studio-contract-section');
        sec.style.display = '';
        const chips = [];
        (m.connections || []).forEach(c => chips.push(`<span class="cc-studio-chip">conn <b>${esc(c)}</b></span>`));
        (m.secrets || []).forEach(s => chips.push(`<span class="cc-studio-chip">secret <b>${esc(s)}</b></span>`));
        (m.inputs || []).forEach(i => chips.push(
            `<span class="cc-studio-chip">input <b>${esc(i.name)}</b>${i.default !== undefined ? ' = ' + esc(i.default) : ''}</span>`));
        (m.packages || []).forEach(p => chips.push(`<span class="cc-studio-chip">pkg <b>${esc(p)}</b></span>`));
        (m.outputs || []).forEach(o => {
            const what = o.kind === 'file' ? esc(o.path || '') : `${esc(o.kind)} ${esc(o.remote_dir || '')}`;
            chips.push(`<span class="cc-studio-chip out">✓ ${what}</span>`);
        });
        $('studio-contract').innerHTML = chips.join('') ||
            '<span class="cc-studio-chip">no declarations yet</span>';
    }

    // ── run result (dry-run theater / final verdicts) ───────────────────
    function _renderRunResult(run) {
        const sec = $('studio-verify-section');
        sec.style.display = '';
        $('studio-verify-title').textContent = run.dry_run ? 'Dry-run result' : 'Run result';
        const checks = [];
        (run.verify_report || []).forEach(entry => {
            (entry.checks || []).forEach(c => {
                const cls = c.ok === true ? 'ok' : (c.ok === false ? 'bad' : 'na');
                const mark = c.ok === true ? '✓' : (c.ok === false ? '✗' : '?');
                const target = entry.path || entry.name || entry.kind || '';
                checks.push(`<div class="cc-studio-check ${cls}"><span class="b">${mark}</span>` +
                    `<span>${esc(target)}<span class="s">${esc(c.note || c.check || '')}</span></span></div>`);
            });
        });
        $('studio-verify').innerHTML = checks.join('');
        const out = $('studio-outcome');
        const status = run.status || '?';
        out.className = 'cc-studio-outcome ' + status;
        out.textContent = (
            status === 'success' ? '✓ Verified — success' :
            status === 'failed' ? '✗ Failed' :
            status === 'unverified' ? '? Ran, but a declared output could not be verified' :
            status === 'aborted' ? '■ Aborted by user' :
            status === 'skipped' ? '⏭ Skipped — a run was already in progress' : status
        ) + (run.exit_code != null ? ` · exit ${run.exit_code}` : '');
    }

    // ── live run: active poll → event feed → gate/abort ────────────────
    function _ensureActivePolling(automationId) {
        if (activeTimer) return;
        activeTimer = setInterval(async () => {
            if (document.hidden || !lastAutomationId) return;
            let d;
            try { d = await _get('/api/studio/active'); } catch (e) { return; }
            const mine = (d.active || []).find(r => r.automation_id === lastAutomationId);
            if (mine && (!liveRun || liveRun.run_id !== mine.run_id)) {
                liveRun = mine; eventsCursor = 0; runMeta = {}; egressSeen = new Set();
                $('studio-live-log').textContent = '';
                $('studio-egress').innerHTML = '';
                $('studio-live-section').style.display = '';
                $('studio-abort').style.display = '';
                if (!hiddenByUser) _show();
                _startEventsPoll();
            } else if (!mine && liveRun) {
                _finishLive();
            }
        }, ACTIVE_POLL_MS);
    }

    function _startEventsPoll() {
        if (eventsTimer) clearInterval(eventsTimer);
        eventsTimer = setInterval(_pollEvents, EVENTS_POLL_MS);
    }

    async function _pollEvents() {
        if (!liveRun || document.hidden) return;
        let d;
        try {
            d = await _get(`/api/studio/runs/${encodeURIComponent(liveRun.run_id)}/events?after=${eventsCursor}`);
        } catch (e) { return; }
        eventsCursor = d.next || eventsCursor;
        (d.events || []).forEach(_applyEvent);
        _renderGate(d.pending_checkpoint);
        _renderBudget();
        const status = d.run && d.run.status;
        if (status && !['running', 'waiting', 'aborting'].includes(status)) _finishLive(d);
    }

    function _applyEvent(ev) {
        if (ev.type === 'run_started') {
            runMeta.startedMs = Date.parse(ev.ts) || Date.now();
            runMeta.timeout = ev.timeout || 600;
        } else if (ev.type === 'log') {
            const el = $('studio-live-log');
            const line = document.createElement('span');
            if (ev.stream === 'err') line.className = 'err';
            line.textContent = ev.line + '\n';
            el.appendChild(line);
            while (el.childNodes.length > MAX_LOG_LINES) el.removeChild(el.firstChild);
            el.scrollTop = el.scrollHeight;
        } else if (ev.type === 'egress') {
            if (!egressSeen.has(ev.dest)) {
                egressSeen.add(ev.dest);
                $('studio-egress').insertAdjacentHTML('beforeend',
                    `<span class="cc-studio-chip">▸ ${esc(ev.dest)}</span>`);
            }
        }
    }

    function _renderBudget() {
        if (!runMeta.startedMs) return;
        const pct = Math.min(100, ((Date.now() - runMeta.startedMs) / 1000) / runMeta.timeout * 100);
        $('studio-budget').style.width = pct.toFixed(1) + '%';
        const s = Math.floor((Date.now() - runMeta.startedMs) / 1000);
        $('studio-elapsed').textContent =
            `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')} / ${Math.floor(runMeta.timeout / 60)}:${String(runMeta.timeout % 60).padStart(2, '0')}`;
    }

    function _renderGate(pending) {
        const gate = $('studio-gate');
        if (pending && !pending.decision) {
            gate.dataset.checkpointId = pending.checkpoint_id;
            $('studio-gate-msg').textContent = pending.message || 'Checkpoint';
            gate.style.display = '';
        } else {
            gate.style.display = 'none';
        }
    }

    function _finishLive(lastPayload) {
        if (eventsTimer) { clearInterval(eventsTimer); eventsTimer = null; }
        $('studio-gate').style.display = 'none';
        $('studio-abort').style.display = 'none';
        if (lastPayload && lastPayload.run) {
            _renderRunResult({
                status: lastPayload.run.status,
                exit_code: lastPayload.run.exit_code,
                verify_report: lastPayload.run.verify_report,
                dry_run: false,
            });
        }
        liveRun = null;
    }

    // ── actions (the only two writes) ───────────────────────────────────
    async function decide(decision) {
        if (!liveRun) return;
        const cid = $('studio-gate').dataset.checkpointId;
        if (!cid) return;
        $('studio-gate').style.display = 'none';
        await _post(`/api/studio/runs/${encodeURIComponent(liveRun.run_id)}/checkpoints/${encodeURIComponent(cid)}/decision`,
                    { decision });
    }

    async function abortRun() {
        if (!liveRun) return;
        if (!confirm('Abort this run? It will stop within a few seconds and record the outcome "aborted".')) return;
        await _post(`/api/studio/runs/${encodeURIComponent(liveRun.run_id)}/abort`, {});
    }

    document.addEventListener('DOMContentLoaded', () => setTimeout(init, 1500));
    return { init, hide, decide, abortRun };
})();
