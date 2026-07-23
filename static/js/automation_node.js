/**
 * Automation workflow-node config UX (james 2026-07-21): pick the automation
 * from a DROPDOWN (no GUID guessing) and get the automation's declared inputs
 * rendered as individual fields with their defaults as placeholders (no raw
 * JSON authoring). Values are stored in the node config exactly as before
 * (automationId/automationName + inputs JSON), so the engine contract is
 * unchanged and nodes configured by hand/API still work.
 *
 * v2 (james 2026-07-22): "Build new with AI" — the escape-hatch drawer. The
 * node can now CREATE an automation in place: a slide-over chat wired (via the
 * main app's /automations/api/builder-chat relay) to the real Command Center
 * authoring agent. Default flow is inline-friendly: "Go live immediately" is
 * checked, the agent is told to skip the dry-run (the workflow itself is the
 * test harness), and the drawer promotes DETERMINISTICALLY via
 * POST /automations/api/<id>/promote before binding the result to the node.
 * Everything lives in this file (styles + DOM injected at setup) so
 * workflow.js and the engine need no changes.
 *
 * The dynamic fields carry NO name attribute on purpose — the designer's
 * generic saveNodeConfig collector harvests named fields only, and
 * AutomationNode.getConfig() supplies these values instead (same pattern as
 * AIExtractNode / ExcelExportNode).
 * Input values support workflow variables: ${variable_name} (dot paths too).
 */
const AutomationNode = (function () {
    let manifest = null;
    let _devOK = false;          // /automations/api/list succeeded → Developer+
    let _session = null;         // CC session id for the drawer conversation
    let _snapshot = {};          // automation_id -> current_version at drawer open
    let _busy = false;

    function _esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    async function _fetchList() {
        const r = await fetch('/automations/api/list');
        if (!r.ok) throw new Error('list failed: ' + r.status);
        const d = await r.json();
        return d.automations || [];
    }

    async function _loadDropdown(cur, preferId) {
        const sel = document.getElementById('autoNodeSelect');
        if (!sel) return [];
        sel.innerHTML = '<option value="">Loading automations…</option>';
        const autos = await _fetchList();
        sel.innerHTML = '<option value="">— choose an automation —</option>' +
            autos.map(a =>
                `<option value="${_esc(a.automation_id)}" data-name="${_esc(a.name)}">` +
                `${_esc(a.name)} ${a.pinned_version ? '(live v' + a.pinned_version + ')' : '(NOT promoted yet)'}` +
                `</option>`).join('');
        let target = preferId || cur.automationId || '';
        if (!target && cur.automationName) {
            const m = autos.find(a => (a.name || '').toLowerCase() === String(cur.automationName).toLowerCase());
            if (m) target = m.automation_id;
        }
        if (target) {
            sel.value = target;
            await _renderInputs(cur);
        }
        return autos;
    }

    async function setup(currentConfig) {
        const sel = document.getElementById('autoNodeSelect');
        if (!sel) return;
        const cur = currentConfig || {};
        _devOK = false;
        try {
            await _loadDropdown(cur, '');
            _devOK = true;
        } catch (e) {
            sel.innerHTML = `<option value="">could not load automations — are you signed in as a Developer?</option>`;
        }
        sel.onchange = () => _renderInputs(cur);
        _injectBuilderRow(cur);
    }

    /* ------------------------------------------------ Build-with-AI drawer */

    function _injectBuilderRow(cur) {
        const sel = document.getElementById('autoNodeSelect');
        if (!sel || document.getElementById('autoNodeBuildRow')) {
            const row = document.getElementById('autoNodeBuildRow');
            if (row) row.style.display = _devOK ? '' : 'none';
            return;
        }
        const row = document.createElement('div');
        row.id = 'autoNodeBuildRow';
        row.style.cssText = 'margin:6px 0 2px 0;display:flex;gap:8px;align-items:center';
        row.innerHTML =
            '<button type="button" class="btn btn-sm btn-outline-primary" id="autoNodeBuildBtn">➕ Build new with AI…</button>' +
            '<button type="button" class="btn btn-sm btn-outline-secondary" id="autoNodeRefreshBtn" title="Refresh the list">↻</button>' +
            '<span class="text-muted" style="font-size:11px">describe it in chat; it becomes a node here</span>';
        if (!_devOK) row.style.display = 'none';
        sel.insertAdjacentElement('afterend', row);
        document.getElementById('autoNodeRefreshBtn').onclick = () => _loadDropdown(cur, '').catch(() => {});
        document.getElementById('autoNodeBuildBtn').onclick = () => _openDrawer(cur);
    }

    function _drawerCSS() {
        if (document.getElementById('autoBuilderCSS')) return;
        const st = document.createElement('style');
        st.id = 'autoBuilderCSS';
        /* The designer is DARK (body{color:white}) — every color here is set
           EXPLICITLY, never inherited (james 2026-07-22: white-on-white). */
        st.textContent = `
#autoBuilderDrawer{position:fixed;top:0;right:0;height:100%;width:520px;max-width:92vw;z-index:10050;
  background:var(--bg-card,#0e1418);color:var(--text-primary,#e8eef2);
  border-left:1px solid var(--border-subtle,#2a3944);box-shadow:-6px 0 24px rgba(0,0,0,.5);
  display:flex;flex-direction:column;font-size:14px}
#autoBuilderDrawer .abd-resize{position:absolute;left:0;top:0;bottom:0;width:6px;cursor:ew-resize;z-index:1}
#autoBuilderDrawer .abd-resize:hover{background:rgba(120,170,200,.25)}
#autoBuilderDrawer .abd-head{padding:10px 14px;border-bottom:1px solid var(--border-subtle,#2a3944);
  display:flex;align-items:center;gap:8px}
#autoBuilderDrawer .abd-title{font-weight:600;flex:1;color:var(--text-primary,#e8eef2)}
#autoBuilderDrawer .abd-opts{padding:8px 14px;border-bottom:1px solid var(--border-subtle,#2a3944);
  background:rgba(255,255,255,.04);font-size:13px;color:var(--text-primary,#dbe5ea)}
#autoBuilderDrawer .abd-opts .text-muted{color:#8aa0ac!important}
#autoBuilderDrawer .abd-msgs{flex:1;overflow-y:auto;padding:12px 14px}
#autoBuilderDrawer .abd-msg{margin-bottom:10px;white-space:pre-wrap;word-break:break-word;line-height:1.45;
  color:var(--text-primary,#e2ebf0)}
#autoBuilderDrawer .abd-msg.user{background:#173243;border-radius:8px;padding:8px 10px}
#autoBuilderDrawer .abd-msg.agent{background:rgba(255,255,255,.05);border:1px solid var(--border-subtle,#2a3944);
  border-radius:8px;padding:8px 10px}
#autoBuilderDrawer .abd-status{padding:4px 14px;font-size:12px;color:#7f97a3;min-height:22px}
#autoBuilderDrawer .abd-bind{display:none;margin:0 14px 8px 14px;padding:10px;border:1px solid #3f7a4d;
  background:#12291a;border-radius:8px;font-size:13px;color:#cfe9d6}
#autoBuilderDrawer .abd-bind code{color:#8fd3a8}
#autoBuilderDrawer .abd-input{display:flex;gap:8px;padding:10px 14px;border-top:1px solid var(--border-subtle,#2a3944)}
#autoBuilderDrawer textarea{flex:1;resize:none;height:64px;background:#0b1013;color:#e8eef2;
  border:1px solid var(--border-subtle,#2a3944)}
#autoBuilderDrawer textarea::placeholder{color:#607482}
#autoBuilderDrawer textarea:focus{background:#0b1013;color:#e8eef2;border-color:#3d5866;box-shadow:none}
`;
        document.head.appendChild(st);
    }

    function _openDrawer(cur) {
        _drawerCSS();
        let d = document.getElementById('autoBuilderDrawer');
        if (d) { d.style.display = 'flex'; return; }
        d = document.createElement('div');
        d.id = 'autoBuilderDrawer';
        d.innerHTML =
            '<div class="abd-resize" id="abdResize" title="Drag to resize"></div>' +
            '<div class="abd-head"><span class="abd-title">🤖 Build automation with AI</span>' +
            '<button type="button" class="btn btn-sm btn-outline-secondary" id="abdClose">✕</button></div>' +
            '<div class="abd-opts"><label style="margin:0;cursor:pointer">' +
            '<input type="checkbox" id="abdGoLive" checked> 🚀 Go live immediately when built ' +
            '<span class="text-muted">(skips dry-run — you\'ll test it by running the workflow)</span></label></div>' +
            '<div class="abd-msgs" id="abdMsgs"><div class="abd-msg agent">Describe what this step must do — ' +
            'be as specific as you like (libraries, file formats, edge cases). Example: ' +
            '“Extract every embedded diagram image from the PDF at input pdf_path into an output folder ' +
            'and write a manifest JSON; use PyMuPDF.”</div></div>' +
            '<div class="abd-bind" id="abdBind"></div>' +
            '<div class="abd-status" id="abdStatus"></div>' +
            '<div class="abd-input"><textarea id="abdText" class="form-control" ' +
            'placeholder="Describe the automation to build…"></textarea>' +
            '<button type="button" class="btn btn-primary" id="abdSend">Send</button></div>';
        document.body.appendChild(d);
        document.getElementById('abdClose').onclick = () => { d.style.display = 'none'; };
        document.getElementById('abdSend').onclick = () => _send(cur);
        document.getElementById('abdText').addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); _send(cur); }
        });
        // The node-config dialog is a Bootstrap modal; its focus trap yanks
        // focus back from anything "outside" the modal — which the drawer is
        // (james 2026-07-22: "cannot type unless I close the config window").
        // Capture-phase guard: focus events inside the drawer never reach the
        // trap's document-level listener; focus elsewhere behaves as before.
        document.addEventListener('focusin', (ev) => {
            const dd = document.getElementById('autoBuilderDrawer');
            if (dd && dd.style.display !== 'none' && dd.contains(ev.target)) {
                ev.stopImmediatePropagation();
            }
        }, true);
        // Resizable via the left edge (same UX as the Studio panel); width sticks.
        try {
            const saved = parseInt(localStorage.getItem('auto_builder_w') || '', 10);
            if (saved) d.style.width = Math.min(Math.max(saved, 380), 900) + 'px';
        } catch (e) {}
        document.getElementById('abdResize').addEventListener('mousedown', (ev) => {
            ev.preventDefault();
            const move = (m) => {
                const w = Math.min(Math.max(window.innerWidth - m.clientX, 380), 900);
                d.style.width = w + 'px';
                try { localStorage.setItem('auto_builder_w', String(w)); } catch (e) {}
            };
            const up = () => {
                document.removeEventListener('mousemove', move);
                document.removeEventListener('mouseup', up);
            };
            document.addEventListener('mousemove', move);
            document.addEventListener('mouseup', up);
        });
        _session = null;
        _snapshot = {};
        _fetchList().then(autos => {
            autos.forEach(a => { _snapshot[a.automation_id] = a.current_version || 0; });
        }).catch(() => {});
    }

    function _append(kind, text) {
        const box = document.getElementById('abdMsgs');
        if (!box) return null;
        const el = document.createElement('div');
        el.className = 'abd-msg ' + kind;
        el.textContent = text;
        box.appendChild(el);
        box.scrollTop = box.scrollHeight;
        return el;
    }

    function _status(t) {
        const s = document.getElementById('abdStatus');
        if (s) s.textContent = t || '';
    }

    async function _send(cur) {
        if (_busy) return;
        const ta = document.getElementById('abdText');
        const msg = (ta.value || '').trim();
        if (!msg) return;
        ta.value = '';
        _append('user', msg);
        _busy = true;
        document.getElementById('abdSend').disabled = true;
        _status('Working…');
        try {
            const resp = await fetch('/automations/api/builder-chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: msg,
                    session_id: _session,
                    first: !_session,
                    skip_dry_run: !!(document.getElementById('abdGoLive') || {}).checked,
                    workflow_name: (typeof currentWorkflowName !== 'undefined' && currentWorkflowName) || '',
                    timezone: (Intl.DateTimeFormat().resolvedOptions() || {}).timeZone || ''
                })
            });
            if (!resp.ok) {
                let err = 'request failed (' + resp.status + ')';
                try { err = (await resp.json()).error || err; } catch (e) {}
                _append('agent', '⚠ ' + err);
                return;
            }
            await _consumeSSE(resp.body);
        } catch (e) {
            _append('agent', '⚠ ' + (e && e.message ? e.message : e));
        } finally {
            _busy = false;
            const b = document.getElementById('abdSend');
            if (b) b.disabled = false;
            _status('');
            _checkBuilt(cur).catch(() => {});
        }
    }

    async function _consumeSSE(bodyStream) {
        const reader = bodyStream.getReader();
        const dec = new TextDecoder();
        let buf = '';
        for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += dec.decode(value, { stream: true });
            let idx;
            while ((idx = buf.indexOf('\n\n')) >= 0) {
                const raw = buf.slice(0, idx);
                buf = buf.slice(idx + 2);
                let ev = '', dat = '';
                raw.split('\n').forEach(ln => {
                    if (ln.startsWith('event: ')) ev = ln.slice(7).trim();
                    else if (ln.startsWith('data: ')) dat += ln.slice(6);
                });
                if (!ev) continue;
                let payload = {};
                try { payload = JSON.parse(dat || '{}'); } catch (e) { continue; }
                if (ev === 'session' && payload.session_id) _session = payload.session_id;
                else if (ev === 'status') _status(payload.message || payload.phase || '');
                else if (ev === 'response') {
                    (payload.blocks || []).forEach(b => {
                        if (b && b.type === 'text' && b.content) _append('agent', b.content);
                    });
                    if (payload.session_id) _session = payload.session_id;
                }
            }
        }
    }

    /* After each agent turn: diff the automations list against the drawer-open
       snapshot; a NEW or newly-versioned automation with a saved version is the
       build result. One explicit click promotes (if "go live" is checked) and
       binds — deterministic API calls, never trusting chat prose. */
    async function _checkBuilt(cur) {
        const bar = document.getElementById('abdBind');
        if (!bar) return;
        let autos = [];
        try { autos = await _fetchList(); } catch (e) { return; }
        const fresh = autos.filter(a =>
            (a.current_version || 0) >= 1 &&
            ((_snapshot[a.automation_id] === undefined) ||
             (a.current_version || 0) > _snapshot[a.automation_id]));
        if (!fresh.length) return;
        fresh.sort((x, y) => String(y.updated_at || '').localeCompare(String(x.updated_at || '')));
        const a = fresh[0];
        const goLive = !!(document.getElementById('abdGoLive') || {}).checked;
        bar.style.display = 'block';
        bar.innerHTML = `⚡ <b>${_esc(a.name)}</b> v${a.current_version} is ready — ` +
            `<button type="button" class="btn btn-sm btn-success" id="abdBindBtn">` +
            (goLive ? '🚀 Go live & bind to this node' : 'Bind to this node (promote later)') +
            '</button>';
        document.getElementById('abdBindBtn').onclick = () => _bind(cur, a, goLive);
    }

    async function _bind(cur, a, goLive) {
        const bar = document.getElementById('abdBind');
        try {
            if (goLive) {
                const r = await fetch('/automations/api/' + encodeURIComponent(a.automation_id) + '/promote', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
                });
                if (!r.ok) {
                    let err = 'promote failed';
                    try { err = (await r.json()).error || err; } catch (e) {}
                    if (bar) bar.innerHTML = '⚠ ' + _esc(err);
                    return;
                }
            }
            await _loadDropdown(cur, a.automation_id);
            if (bar) {
                bar.innerHTML = '✅ <b>' + _esc(a.name) + '</b>' +
                    (goLive ? ' is LIVE and bound to this node.' : ' bound (not yet promoted).') +
                    ' Set its inputs below — values support ' +
                    '<code>${variable_name}</code> workflow variables. You can close this panel.';
            }
        } catch (e) {
            if (bar) bar.innerHTML = '⚠ ' + _esc(e && e.message ? e.message : String(e));
        }
    }

    /* --------------------------------------------------- inputs rendering */

    async function _renderInputs(cur) {
        const sel = document.getElementById('autoNodeSelect');
        const box = document.getElementById('autoNodeInputs');
        const meta = document.getElementById('autoNodeMeta');
        manifest = null;
        if (!sel || !box) return;
        if (!sel.value) {
            box.innerHTML = '<div class="text-muted small">Choose an automation to see its inputs.</div>';
            if (meta) meta.textContent = '';
            return;
        }
        box.innerHTML = '<div class="text-muted small">Loading inputs…</div>';
        try {
            const r = await fetch('/automations/api/' + encodeURIComponent(sel.value));
            const d = await r.json();
            manifest = (d.automation || {}).manifest || {};
            let existing = {};
            try {
                existing = typeof cur.inputs === 'string'
                    ? JSON.parse(cur.inputs || '{}') : (cur.inputs || {});
            } catch (e) { existing = {}; }
            const inputs = manifest.inputs || [];
            box.innerHTML = inputs.length
                ? inputs.map(inp => `
                    <div class="row mb-1 align-items-center">
                        <div class="col-4 small"><code>${_esc(inp.name)}</code></div>
                        <div class="col-8">
                            <input class="form-control form-control-sm auto-node-input"
                                data-name="${_esc(inp.name)}"
                                title="supports \${variable_name} workflow variables"
                                placeholder="default: ${_esc(inp.default == null ? '' : inp.default)}"
                                value="${_esc(existing[inp.name] != null ? existing[inp.name] : '')}">
                            ${inp.description ? `<div class="text-muted" style="font-size:11px">${_esc(inp.description)}</div>` : ''}
                        </div>
                    </div>`).join('')
                : '<div class="text-muted small">This automation declares no inputs.</div>';
            if (meta) {
                meta.textContent = `uses — connections: ${(manifest.connections || []).join(', ') || 'none'}`
                    + ` · secrets: ${(manifest.secrets || []).join(', ') || 'none'}`
                    + ` · leave a field empty to use its default`
                    + ` · values support \${variable_name} workflow variables`;
            }
        } catch (e) {
            box.innerHTML = '<div class="text-danger small">Could not load this automation\'s inputs.</div>';
        }
    }

    function getConfig() {
        const sel = document.getElementById('autoNodeSelect');
        const opt = sel && sel.selectedOptions && sel.selectedOptions[0];
        const overrides = {};
        document.querySelectorAll('.auto-node-input').forEach(el => {
            const v = el.value;
            if (v != null && String(v).trim() !== '') overrides[el.getAttribute('data-name')] = v;
        });
        return {
            automationId: sel ? sel.value : '',
            automationName: opt ? (opt.getAttribute('data-name') || '') : '',
            inputs: Object.keys(overrides).length ? JSON.stringify(overrides) : ''
        };
    }

    return { setup, getConfig };
})();
