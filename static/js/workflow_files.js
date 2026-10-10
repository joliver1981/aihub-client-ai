/* Workflow Files page (2026-10-10) — drop files for a workflow, collect its
 * results, retry what failed; Developers also set file drops up, share them
 * and copy results to SharePoint. API: /api/workflow-files (workflow_files_routes.py). */
(function () {
    'use strict';

    const page = document.getElementById('docPage');
    if (!page) return;
    const canManage = page.dataset.canManage === 'true';
    const ROLES = [
        ['inbox', 'Waiting', 'the folder it reads new files from'],
        ['processed', 'Processed', 'the folder it moves finished files to'],
        ['failed', 'Failed', 'the folder it moves files it could not process to'],
        ['outbox', 'Results', 'where it writes its results (a folder, or a workbook it adds to)'],
    ];
    const TAB_ORDER = ['inbox', 'outbox', 'failed', 'processed'];
    const LABEL = { inbox: 'Waiting', outbox: 'Results', failed: 'Failed', processed: 'Processed' };

    let currentId = page.dataset.workflowId || '';
    let detail = null;
    let activeTab = 'inbox';
    let pollTimer = null;
    let people = null;
    let note = null;          // the last action's message: survives re-renders (polling)

    // ------------------------------------------------------------ helpers
    function esc(s) {
        const d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }
    function fmtSize(n) {
        n = Number(n || 0);
        if (n < 1024) return n + ' B';
        if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
        return (n / 1024 / 1024).toFixed(1) + ' MB';
    }
    function fmtWhen(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        return isNaN(d) ? iso : d.toLocaleString();
    }
    function statusPill(status) {
        const s = String(status || '').toLowerCase();
        const cls = s === 'completed' ? 'ok' : (s === 'failed' || s === 'cancelled') ? 'bad'
            : s === 'running' ? 'warn' : '';
        return `<span class="wff-pill ${cls}">${esc(status || 'unknown')}</span>`;
    }
    async function api(method, url, body) {
        const opts = { method, headers: {} };
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        const r = await fetch(url, opts);
        let data = null;
        try { data = await r.json(); } catch (e) { data = null; }
        if (!r.ok) {
            const msg = (data && (data.message || data.error)) || `HTTP ${r.status}`;
            const err = new Error(msg);
            err.status = r.status;
            err.data = data;
            throw err;
        }
        return data || {};
    }
    function base() { return `/api/workflow-files/${encodeURIComponent(currentId)}`; }
    function dl(folder, name) {
        return `${base()}/download?folder=${encodeURIComponent(folder)}&name=${encodeURIComponent(name)}`;
    }
    function flash(el, text, kind) {
        if (!el) return;
        el.className = 'wff-msg ' + (kind === 'error' ? 'wff-err' : kind === 'warn' ? 'wff-warn' : 'wff-note');
        el.textContent = text;
    }
    function say(text, kind) {          // the message under the drop zone
        note = { text, kind };
        flash(document.getElementById('wffUploadMsg'), text, kind);
    }

    // --------------------------------------------------------------- list
    async function loadList() {
        const list = document.getElementById('wffList');
        let data;
        try {
            data = await api('GET', '/api/workflow-files' + (canManage ? '?candidates=1' : ''));
        } catch (e) {
            list.innerHTML = `<div class="wff-err p-3">${esc(e.message)}</div>`;
            return;
        }
        const rows = data.workflows || [];
        if (!rows.length) {
            list.innerHTML = canManage
                ? '<div class="wff-muted p-3">No workflow has file drops yet. Pick one below to set it up.</div>'
                : '<div class="wff-muted p-3">No workflow shares its files with you yet. Ask the workflow\'s owner '
                  + '(a Developer) to share it with you or one of your groups.</div>';
        } else {
            list.innerHTML = rows.map(w => {
                const c = w.counts || {};
                const last = w.last_run ? ` · last run ${statusPill(w.last_run.status)}` : '';
                return `<div class="wff-item ${String(w.workflow_id) === String(currentId) ? 'active' : ''}" data-id="${w.workflow_id}">
                    <div class="wff-name">${esc(w.name)}</div>
                    <div class="wff-meta">Waiting ${c.inbox || 0} · Results ${c.outbox || 0} · Failed ${c.failed || 0}${last}</div>
                </div>`;
            }).join('');
            list.querySelectorAll('.wff-item').forEach(el => el.addEventListener('click', () => select(el.dataset.id)));
        }
        const picker = document.getElementById('wffSetupPicker');
        if (canManage && data.candidates) {
            const sel = document.getElementById('wffCandidate');
            sel.innerHTML = '<option value="">Choose a workflow…</option>'
                + data.candidates.map(c => `<option value="${c.workflow_id}">${esc(c.name)}</option>`).join('');
            picker.style.display = '';
        }
        if (!currentId && rows.length) select(rows[0].workflow_id);
    }

    async function select(id) {
        currentId = String(id);
        note = null;
        try { history.replaceState(null, '', `/workflow-files/${currentId}`); } catch (e) { /* ignore */ }
        document.querySelectorAll('#wffList .wff-item').forEach(el =>
            el.classList.toggle('active', el.dataset.id === currentId));
        await loadDetail();
    }

    // ------------------------------------------------------------- detail
    async function loadDetail(keepTab) {
        const box = document.getElementById('wffDetail');
        if (!currentId) return;
        if (!keepTab) box.innerHTML = '<div class="wff-card wff-empty"><i class="fas fa-spinner fa-spin"></i> Loading…</div>';
        try {
            detail = await api('GET', base());
        } catch (e) {
            box.innerHTML = `<div class="wff-card wff-empty"><span class="wff-err">${esc(e.message)}</span></div>`;
            return;
        }
        if (!detail.set_up) {
            renderSetup(box, detail);
        } else {
            renderFiles(box, detail);
        }
        schedulePoll();
    }

    function schedulePoll() {
        clearTimeout(pollTimer);
        if (detail && detail.set_up && detail.running) {
            pollTimer = setTimeout(async () => { await loadDetail(true); loadList(); }, 4000);
        }
    }

    // -------------------------------------------------------------- setup
    function mappingRows(d, current) {
        const vars = d.variables || [];
        return ROLES.map(([role, label, what]) => {
            const chosen = (current && current[role] !== undefined) ? current[role] : (d.suggested || {})[role] || '';
            const opts = ['<option value="">(not used)</option>'].concat(vars.map(v =>
                `<option value="${esc(v.name)}" ${v.name === chosen ? 'selected' : ''}>${esc(v.name)}</option>`)).join('');
            const rv = (d.roles || {})[role];
            let state = '';
            if (rv && rv.variable) {
                state = rv.missing ? '<span class="wff-warn">variable no longer exists</span>'
                    : rv.managed ? '<span class="wff-pill ok">points here</span>'
                    : `<span class="wff-warn">points elsewhere: <code>${esc(rv.value)}</code></span>`;
            }
            return `<tr><td style="width:110px;"><strong>${label}</strong><div class="wff-note">${what}</div></td>
                <td><select class="form-control form-control-sm wff-input" data-role="${role}">${opts}</select>
                <div class="wff-note mt-1" data-value-for="${role}"></div>${state ? `<div class="mt-1">${state}</div>` : ''}</td></tr>`;
        }).join('');
    }

    function wireMapping(container, d) {
        const values = {};
        (d.variables || []).forEach(v => { values[v.name] = v.value; });
        container.querySelectorAll('select[data-role]').forEach(sel => {
            const show = () => {
                const note = container.querySelector(`[data-value-for="${sel.dataset.role}"]`);
                note.innerHTML = sel.value ? `now: <code>${esc(values[sel.value] || '(empty)')}</code>` : '';
            };
            sel.addEventListener('change', show);
            show();
        });
    }

    function readMapping(container) {
        const m = {};
        container.querySelectorAll('select[data-role]').forEach(sel => { m[sel.dataset.role] = sel.value; });
        return m;
    }

    function renderSetup(box, d) {
        if (!d.can_manage) {
            box.innerHTML = '<div class="wff-card wff-empty">File drops are not set up for this workflow.</div>';
            return;
        }
        if (d.is_code_flow) {
            box.innerHTML = `<div class="wff-card wff-empty"><strong>${esc(d.name)}</strong> is a code flow. `
                + 'File drops are for visual workflows.</div>';
            return;
        }
        const turnedOff = d.settings && d.settings.enabled === false;
        box.innerHTML = `<div class="wff-card">
            <div class="wff-card-head"><span>Set up file drops: ${esc(d.name)}</span></div>
            <div class="wff-card-body">
                ${turnedOff ? '<p class="wff-warn">File drops were turned off for this workflow. Setting it up again keeps its files.</p>' : ''}
                <p class="wff-note">The platform creates four folders for this workflow on the server and points the
                workflow variables you choose at them (saved like a Designer edit). People you share it with can then drop
                files here or in The Agent and collect the results, without Designer access.</p>
                ${(d.variables || []).length ? '' : '<p class="wff-warn">This workflow has no variables. Use variables for its folders in the Designer (for example ${inputFolder} in its Folder Selector), then come back.</p>'}
                <table class="wff-map w-100"><tbody>${mappingRows(d, null)}</tbody></table>
                <label class="wff-toggle"><input type="checkbox" id="wffSetupRun"> Start a run as soon as files arrive</label>
                <p class="wff-note">If this workflow is open in the Designer, reload it there after setting up, or saving it
                from the Designer puts the old folders back.</p>
                <button class="btn btn-sm wff-btn" id="wffSetupGo"><i class="fas fa-folder-plus"></i> Create folders and point the workflow at them</button>
                <div id="wffSetupMsg"></div>
            </div></div>`;
        wireMapping(box, d);
        document.getElementById('wffSetupGo').addEventListener('click', async (ev) => {
            const btn = ev.currentTarget;
            const msg = document.getElementById('wffSetupMsg');
            btn.disabled = true;
            try {
                const res = await api('POST', `${base()}/setup`, {
                    variables: readMapping(box), run_on_upload: document.getElementById('wffSetupRun').checked });
                const ch = Object.entries(res.changes || {}).map(([v, c]) => `${v} → ${c.to}`).join('; ');
                flash(msg, 'Set up. ' + ch, 'ok');
                await loadList();
                await loadDetail();
            } catch (e) {
                flash(msg, e.message, 'error');
            } finally {
                btn.disabled = false;
            }
        });
    }

    // -------------------------------------------------------------- files
    function renderFiles(box, d) {
        const c = d.counts || {};
        const s = d.settings || {};
        const running = d.running ? '<span class="wff-pill warn"><i class="fas fa-circle-notch fa-spin"></i> running</span>' : '';
        const email = (d.email_addresses || []).filter(a => a.address);
        const emailNote = email.length
            ? `<div class="wff-note mt-2"><i class="fas fa-envelope"></i> Email attachments to ${email.map(a =>
                `<strong>${esc(a.address)}</strong>${a.active ? '' : ' (inactive)'}`).join(', ')}${s.email_to_inbox ? ' and they land in Waiting' : ' (attachments are not saved: turned off below)'}.</div>`
            : '';
        box.innerHTML = `
        <div class="wff-card">
            <div class="wff-card-head">
                <span>${esc(d.name)} ${running}</span>
                <span>
                    <button class="btn btn-sm wff-btn" id="wffRunNow" ${c.inbox ? '' : 'disabled'} title="Run the workflow on the waiting files">
                        <i class="fas fa-play"></i> Run now${c.inbox ? ` (${c.inbox})` : ''}</button>
                    <button class="btn btn-sm wff-btn-ghost" id="wffRefresh" title="Refresh"><i class="fas fa-sync-alt"></i></button>
                </span>
            </div>
            <div class="wff-card-body">
                <div class="wff-drop" id="wffDrop">
                    <i class="fas fa-cloud-upload-alt fa-2x"></i>
                    <div class="mt-2">Drop files here, or click to choose</div>
                    <div class="wff-note">They go to Waiting${s.run_on_upload ? ' and a run starts as soon as they arrive' : ''}.</div>
                    <input type="file" id="wffFileInput" multiple style="display:none;">
                </div>
                ${s.run_on_upload ? '' : '<label class="wff-toggle"><input type="checkbox" id="wffRunAfter"> Run the workflow after this upload</label>'}
                <div class="wff-progress" id="wffProgress"><div></div></div>
                <div id="wffUploadMsg"></div>
                ${emailNote}
            </div>
            <div class="wff-tabs" id="wffTabs">
                ${TAB_ORDER.map(f => `<button class="wff-tab ${f === activeTab ? 'active' : ''}" data-tab="${f}">${LABEL[f]}<span class="wff-count">${c[f] || 0}</span></button>`).join('')}
            </div>
            <div id="wffTabBody"></div>
        </div>
        <div class="wff-card">
            <div class="wff-card-head"><span>Recent runs</span></div>
            <div id="wffRuns"></div>
        </div>
        ${d.can_manage ? '<div id="wffManage"></div>' : ''}`;

        wireUpload(d);
        if (note) flash(document.getElementById('wffUploadMsg'), note.text, note.kind);
        document.getElementById('wffRefresh').addEventListener('click', () => { loadDetail(true); loadList(); });
        document.getElementById('wffRunNow').addEventListener('click', runNow);
        document.querySelectorAll('#wffTabs .wff-tab').forEach(t => t.addEventListener('click', () => {
            activeTab = t.dataset.tab;
            document.querySelectorAll('#wffTabs .wff-tab').forEach(x => x.classList.toggle('active', x === t));
            renderTab(d);
        }));
        renderTab(d);
        renderRuns(d);
        if (d.can_manage) renderManage(d);
    }

    function renderTab(d) {
        const body = document.getElementById('wffTabBody');
        const files = (d.files || {})[activeTab] || [];
        if (!files.length) {
            const empty = { inbox: 'Nothing is waiting.', outbox: 'No results yet.', failed: 'Nothing has failed.',
                            processed: 'Nothing processed yet.' }[activeTab];
            body.innerHTML = `<div class="wff-muted p-3">${empty}</div>`;
            return;
        }
        const extraHead = activeTab === 'inbox' ? '<th>Dropped by</th>' : '';
        const head = `<tr><th>Name</th><th>Size</th><th>Changed</th>${extraHead}<th></th></tr>`;
        const rows = files.map(f => {
            const actions = [`<a href="${dl(activeTab, f.name)}" title="Download"><i class="fas fa-download"></i></a>`];
            if (activeTab === 'failed') actions.push(`<button class="btn btn-sm wff-btn-ghost ml-2" data-retry="${esc(f.name)}">Retry</button>`);
            if (activeTab === 'inbox' || d.can_manage) actions.push(`<button class="btn btn-sm wff-btn-ghost ml-2" data-del="${esc(f.name)}" title="Remove"><i class="fas fa-trash"></i></button>`);
            return `<tr><td>${esc(f.name)}</td><td>${fmtSize(f.size)}</td><td>${esc(fmtWhen(f.modified))}</td>
                ${activeTab === 'inbox' ? `<td>${esc(f.uploaded_by || '')}</td>` : ''}
                <td class="wff-act">${actions.join('')}</td></tr>`;
        }).join('');
        const retryAll = activeTab === 'failed' && files.length > 1
            ? '<div class="p-2 text-right"><button class="btn btn-sm wff-btn-ghost" id="wffRetryAll">Retry all failed files</button></div>' : '';
        body.innerHTML = `<table class="wff-files"><thead>${head}</thead><tbody>${rows}</tbody></table>${retryAll}`;
        body.querySelectorAll('[data-retry]').forEach(b => b.addEventListener('click', () => retry([b.dataset.retry])));
        body.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', () => removeFile(b.dataset.del)));
        const ra = document.getElementById('wffRetryAll');
        if (ra) ra.addEventListener('click', () => retry(null));
    }

    function renderRuns(d) {
        const box = document.getElementById('wffRuns');
        const runs = d.runs || [];
        if (!runs.length) {
            box.innerHTML = '<div class="wff-muted p-3">No runs yet.</div>';
            return;
        }
        box.innerHTML = runs.map(r => {
            const who = r.started_by ? `started by ${esc(r.started_by)}` : (r.initiator ? esc(r.initiator) : '');
            const took = (r.consumed || []).map(x => esc(x.name)).join(', ');
            const outs = (r.outputs || []).map(o => `<a href="${dl('outbox', o.name)}"><i class="fas fa-download"></i> ${esc(o.name)}</a>`).join(' &nbsp; ');
            const failed = (r.failed || []).map(n => esc(n)).join(', ');
            const sp = (r.sharepoint || []).filter(x => !x.ok).map(x => `${esc(x.name)}: ${esc(x.error)}`).join('; ');
            return `<div class="wff-run">
                <div>${statusPill(r.status)} <span class="wff-note">${esc(fmtWhen(r.finished_at || r.started_at))} ${who ? '· ' + who : ''}</span></div>
                ${took ? `<div class="wff-note mt-1">Took: ${took}</div>` : ''}
                ${outs ? `<div class="mt-1">Results: ${outs}</div>` : ''}
                ${failed ? `<div class="wff-warn mt-1">Failed: ${failed} — see the Failed tab to retry.</div>` : ''}
                ${sp ? `<div class="wff-err mt-1">SharePoint copy failed: ${sp}</div>` : ''}
            </div>`;
        }).join('');
    }

    // ------------------------------------------------------------ actions
    function wireUpload(d) {
        const drop = document.getElementById('wffDrop');
        const input = document.getElementById('wffFileInput');
        drop.addEventListener('click', () => input.click());
        input.addEventListener('change', () => { if (input.files.length) upload(input.files); input.value = ''; });
        ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => {
            e.preventDefault(); drop.classList.add('over'); }));
        ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => {
            e.preventDefault(); drop.classList.remove('over'); }));
        drop.addEventListener('drop', e => { if (e.dataTransfer.files.length) upload(e.dataTransfer.files); });
    }

    function upload(fileList) {
        const bar = document.getElementById('wffProgress');
        const runAfter = document.getElementById('wffRunAfter');
        const fd = new FormData();
        Array.from(fileList).forEach(f => fd.append('files', f, f.name));
        const xhr = new XMLHttpRequest();
        xhr.open('POST', `${base()}/upload${runAfter && runAfter.checked ? '?run=1' : ''}`);
        bar.style.display = 'block';
        bar.firstElementChild.style.width = '0';
        xhr.upload.onprogress = e => {
            if (e.lengthComputable) bar.firstElementChild.style.width = Math.round(100 * e.loaded / e.total) + '%';
        };
        xhr.onload = async () => {
            bar.style.display = 'none';
            let data = {};
            try { data = JSON.parse(xhr.responseText || '{}'); } catch (e) { data = {}; }
            if (xhr.status >= 400 && !(data.stored || []).length) {
                say(data.message || (data.errors || []).join('; ') || `Upload failed (HTTP ${xhr.status}).`, 'error');
                return;
            }
            const names = (data.stored || []).map(s => s.name);
            let text = `Added to Waiting: ${names.join(', ')}.`;
            if ((data.errors || []).length) text += ' Not added: ' + data.errors.join('; ') + '.';
            if (data.run) text += data.run.queued ? ' ' + data.run.message : ' A run started.';
            say(text, (data.errors || []).length ? 'warn' : 'ok');
            activeTab = 'inbox';
            await loadDetail(true);
            loadList();
        };
        xhr.onerror = () => { bar.style.display = 'none'; say('Upload failed: the server could not be reached.', 'error'); };
        say(`Uploading ${fileList.length} file(s)…`, 'ok');
        xhr.send(fd);
    }

    async function runNow() {
        try {
            const res = await api('POST', `${base()}/run`, {});
            say(res.queued ? res.message : 'A run started. This page updates when it finishes.', 'ok');
            await loadDetail(true);
            if (!detail.running && !res.queued) { detail.running = true; schedulePoll(); }
        } catch (e) {
            say(e.message, 'error');
        }
    }

    async function retry(names) {
        const run = confirm('Move the file(s) back to Waiting and start a run now?\n\nOK = retry and run; Cancel = just move them back.');
        try {
            const res = await api('POST', `${base()}/retry`, names ? { names, run } : { all: true, run });
            let text = `Back in Waiting: ${(res.moved || []).map(m => m.to).join(', ')}.`;
            if (res.run) text += res.run.queued ? ' ' + res.run.message : ' A run started.';
            say(text, 'ok');
            await loadDetail(true);
            loadList();
        } catch (e) {
            say(e.message, 'error');
        }
    }

    async function removeFile(name) {
        if (!confirm(`Remove ${name} from ${LABEL[activeTab]}? This cannot be undone.`)) return;
        try {
            await api('POST', `${base()}/delete`, { folder: activeTab, names: [name] });
            say(`Removed ${name}.`, 'ok');
            await loadDetail(true);
            loadList();
        } catch (e) {
            say(e.message, 'error');
        }
    }

    // -------------------------------------------------------------- manage
    function renderManage(d) {
        const box = document.getElementById('wffManage');
        const s = d.settings || {};
        const paths = d.paths || {};
        const shared = d.shared_with || { users: [], groups: [] };
        const sp = s.sharepoint;
        box.innerHTML = `
        <div class="wff-card">
            <div class="wff-card-head"><span>Sharing</span></div>
            <div class="wff-card-body">
                <p class="wff-note">Developers and Admins can always use this workflow's files. Share it with other people or
                groups so they can drop files, collect results and retry, without Designer access.</p>
                <div class="wff-note mb-2">Shared with: ${shared.users.concat(shared.groups).length
                    ? shared.users.map(u => `<span class="wff-pill">${esc(u.name)}</span>`).join('')
                      + shared.groups.map(g => `<span class="wff-pill"><i class="fas fa-users"></i> ${esc(g.name)}</span>`).join('')
                    : 'nobody yet'}</div>
                <button class="btn btn-sm wff-btn-ghost" id="wffShareEdit"><i class="fas fa-user-plus"></i> Change who it is shared with</button>
                <div id="wffShareBox" style="display:none;" class="mt-2">
                    <div class="row">
                        <div class="col-md-6"><label class="wff-label">People</label><select multiple class="form-control form-control-sm wff-input" id="wffShareUsers"></select></div>
                        <div class="col-md-6"><label class="wff-label">Groups</label><select multiple class="form-control form-control-sm wff-input" id="wffShareGroups"></select></div>
                    </div>
                    <div class="wff-note mt-1">Hold Ctrl (or Cmd) to choose several.</div>
                    <button class="btn btn-sm wff-btn mt-2" id="wffShareSave">Save sharing</button>
                </div>
                <div id="wffShareMsg"></div>
            </div>
        </div>
        <div class="wff-card">
            <div class="wff-card-head"><span>Settings</span></div>
            <div class="wff-card-body">
                <label class="wff-toggle"><input type="checkbox" data-setting="run_on_upload" ${s.run_on_upload ? 'checked' : ''}> Start a run as soon as files arrive</label>
                <label class="wff-toggle"><input type="checkbox" data-setting="notify_owner" ${s.notify_owner !== false ? 'checked' : ''}> Tell me (the owner${s.owner ? ': ' + esc(s.owner) : ''}) about runs nobody started, such as scheduled ones</label>
                <label class="wff-toggle"><input type="checkbox" data-setting="email_to_inbox" ${s.email_to_inbox !== false ? 'checked' : ''}> Save attachments of emails that trigger this workflow into Waiting</label>
                <div class="wff-note">To give it an email address: open an agent's Email settings (the Email button on the agent's chat page), turn on Trigger workflow and pick this workflow. Attachments sent to that address then land in Waiting.</div>
                <div id="wffSettingsMsg"></div>
            </div>
        </div>
        <div class="wff-card">
            <div class="wff-card-head"><span>Copy results to SharePoint</span></div>
            <div class="wff-card-body">
                <div class="wff-note mb-2">${sp ? `Each run's new and updated results are copied to <strong>${esc(sp.site_name || 'SharePoint')}</strong> ›
                    <strong>${esc(sp.drive_name || sp.drive_id)}</strong> › <strong>/${esc(sp.folder_path || '')}</strong>
                    (${sp.conflict_behavior === 'rename' ? 'keeping both when a name exists' : 'replacing the older copy'}).`
                    : 'Off. Results stay in Results on the server.'}</div>
                <button class="btn btn-sm wff-btn-ghost" id="wffSpEdit">${sp ? 'Change' : 'Set up'}</button>
                ${sp ? '<button class="btn btn-sm wff-btn-danger ml-2" id="wffSpOff">Stop copying</button>' : ''}
                <div id="wffSpBox" style="display:none;" class="mt-2"></div>
                <div id="wffSpMsg"></div>
            </div>
        </div>
        <div class="wff-card">
            <div class="wff-card-head"><span>Folders and variables</span></div>
            <div class="wff-card-body">
                <table class="wff-map w-100"><tbody>${mappingRows(d, (s.variables || {}))}</tbody></table>
                <button class="btn btn-sm wff-btn-ghost" id="wffRemap">Save and point these variables at the folders</button>
                <div id="wffRemapMsg"></div>
                <div class="mt-3 wff-note">Folders on the server:</div>
                ${ROLES.map(([r, label]) => `<div class="wff-path">${label}: ${esc(paths[r] || '')}</div>`).join('')}
                <div class="mt-3"><button class="btn btn-sm wff-btn-danger" id="wffTurnOff">Turn off file drops</button>
                <span class="wff-note ml-2">Files and folders stay; the workflow keeps its current variables.</span></div>
            </div>
        </div>`;

        wireMapping(box, d);
        box.querySelectorAll('[data-setting]').forEach(cb => cb.addEventListener('change', async () => {
            const msg = document.getElementById('wffSettingsMsg');
            try {
                await api('PUT', `${base()}/settings`, { [cb.dataset.setting]: cb.checked });
                await loadDetail(true);
                flash(document.getElementById('wffSettingsMsg'), 'Saved.', 'ok');
            } catch (e) {
                cb.checked = !cb.checked;
                flash(msg, e.message, 'error');
            }
        }));
        document.getElementById('wffRemap').addEventListener('click', async () => {
            const msg = document.getElementById('wffRemapMsg');
            try {
                const res = await api('POST', `${base()}/setup`, { variables: readMapping(box) });
                const ch = Object.entries(res.changes || {}).map(([v, c]) => `${v} → ${c.to}`).join('; ');
                await loadDetail(true);
                flash(document.getElementById('wffRemapMsg'), 'Saved. ' + ch, 'ok');
            } catch (e) {
                flash(msg, e.message, 'error');
            }
        });
        document.getElementById('wffTurnOff').addEventListener('click', async () => {
            if (!confirm('Turn off file drops for this workflow? People it is shared with lose access to its files here; '
                + 'the files and folders stay on the server.')) return;
            try {
                await api('PUT', `${base()}/settings`, { enabled: false });
                await loadList();
                await loadDetail();
            } catch (e) {
                alert(e.message);
            }
        });
        document.getElementById('wffShareEdit').addEventListener('click', () => openShare(d));
        document.getElementById('wffSpEdit').addEventListener('click', () => openSharePoint(d));
        const off = document.getElementById('wffSpOff');
        if (off) off.addEventListener('click', async () => {
            if (!confirm('Stop copying results to SharePoint?')) return;
            try {
                await api('PUT', `${base()}/settings`, { sharepoint: null });
                await loadDetail(true);
            } catch (e) {
                flash(document.getElementById('wffSpMsg'), e.message, 'error');
            }
        });
    }

    async function openShare(d) {
        const boxEl = document.getElementById('wffShareBox');
        const msg = document.getElementById('wffShareMsg');
        boxEl.style.display = '';
        try {
            if (!people) people = await api('GET', `${base()}/people`);
        } catch (e) {
            flash(msg, e.message, 'error');
            return;
        }
        const grants = (d.settings || {}).grants || { users: [], groups: [] };
        const us = new Set((grants.users || []).map(String));
        const gs = new Set((grants.groups || []).map(String));
        const roleName = { 1: 'User', 2: 'Developer', 3: 'Admin' };
        document.getElementById('wffShareUsers').innerHTML = (people.users || []).map(u =>
            `<option value="${u.id}" ${us.has(String(u.id)) ? 'selected' : ''}>${esc(u.name)} (${esc(u.user_name)}) · ${roleName[u.role] || ''}</option>`).join('');
        document.getElementById('wffShareGroups').innerHTML = (people.groups || []).map(g =>
            `<option value="${g.id}" ${gs.has(String(g.id)) ? 'selected' : ''}>${esc(g.name)} (${g.members})</option>`).join('');
        document.getElementById('wffShareSave').onclick = async () => {
            const pick = id => Array.from(document.getElementById(id).selectedOptions).map(o => Number(o.value));
            try {
                await api('PUT', `${base()}/access`, { users: pick('wffShareUsers'), groups: pick('wffShareGroups') });
                await loadDetail(true);
                flash(document.getElementById('wffShareMsg'), 'Sharing saved.', 'ok');
            } catch (e) {
                flash(msg, e.message, 'error');
            }
        };
    }

    // SharePoint: integration › site › library › folder (the integration's own browse API)
    async function openSharePoint(d) {
        const boxEl = document.getElementById('wffSpBox');
        const msg = document.getElementById('wffSpMsg');
        boxEl.style.display = '';
        boxEl.innerHTML = '<div class="wff-note"><i class="fas fa-spinner fa-spin"></i> Loading integrations…</div>';
        let ints;
        try {
            ints = ((await api('GET', '/api/integrations')).integrations || [])
                .filter(i => String(i.template_key || '').indexOf('sharepoint') === 0);
        } catch (e) {
            boxEl.innerHTML = '';
            flash(msg, 'Could not list integrations: ' + e.message, 'error');
            return;
        }
        if (!ints.length) {
            boxEl.innerHTML = '<div class="wff-warn">No SharePoint integration is set up. Add one on the Integrations page first.</div>';
            return;
        }
        const cur = (d.settings || {}).sharepoint || {};
        boxEl.innerHTML = `
            <label class="wff-label">Integration</label>
            <select class="form-control form-control-sm wff-input" id="wffSpInt">${ints.map(i =>
                `<option value="${i.integration_id}" ${String(i.integration_id) === String(cur.integration_id) ? 'selected' : ''}>${esc(i.integration_name)}${i.is_connected ? '' : ' (not connected)'}</option>`).join('')}</select>
            <label class="wff-label mt-2">Site</label>
            <select class="form-control form-control-sm wff-input" id="wffSpSite"><option value="">Loading…</option></select>
            <label class="wff-label mt-2">Library</label>
            <select class="form-control form-control-sm wff-input" id="wffSpDrive"><option value="">Choose a site first</option></select>
            <label class="wff-label mt-2">Folder in the library</label>
            <div class="d-flex gap-2"><input class="form-control form-control-sm wff-input" id="wffSpPath" placeholder="for example Reports/Invoices" value="${esc(cur.folder_path || '')}">
            <button class="btn btn-sm wff-btn-ghost" id="wffSpBrowse">Browse</button></div>
            <div id="wffSpFolders"></div>
            <label class="wff-toggle mt-2"><input type="checkbox" id="wffSpKeep" ${cur.conflict_behavior === 'rename' ? 'checked' : ''}> Keep both copies when a file of that name is there (otherwise the newer result replaces it)</label>
            <button class="btn btn-sm wff-btn mt-2" id="wffSpSave">Save</button>`;
        const sel = id => document.getElementById(id);
        const browse = (pathType, extra) => api('POST', `/api/integrations/${sel('wffSpInt').value}/sharepoint/browse`,
            Object.assign({ path_type: pathType }, extra || {}));
        async function loadSites() {
            sel('wffSpSite').innerHTML = '<option value="">Loading…</option>';
            try {
                const r = await browse('sites', { query: '*' });
                const sites = (r.data || {}).sites || [];
                sel('wffSpSite').innerHTML = '<option value="">Choose a site…</option>' + sites.map(s =>
                    `<option value="${esc(s.id)}" ${s.name === cur.site_name ? 'selected' : ''}>${esc(s.name)}</option>`).join('');
                if (sel('wffSpSite').value) loadDrives();
            } catch (e) {
                sel('wffSpSite').innerHTML = '<option value="">Could not list sites</option>';
                flash(msg, e.message, 'error');
            }
        }
        async function loadDrives() {
            const site = sel('wffSpSite').value;
            if (!site) return;
            sel('wffSpDrive').innerHTML = '<option value="">Loading…</option>';
            try {
                const r = await browse('drives', { site_id: site });
                const drives = (r.data || {}).drives || [];
                sel('wffSpDrive').innerHTML = '<option value="">Choose a library…</option>' + drives.map(dr =>
                    `<option value="${esc(dr.id)}" ${dr.id === cur.drive_id ? 'selected' : ''}>${esc(dr.name)}</option>`).join('');
            } catch (e) {
                flash(msg, e.message, 'error');
            }
        }
        const stack = [];
        async function listFolders() {
            const drive = sel('wffSpDrive').value;
            const list = sel('wffSpFolders');
            if (!drive) { flash(msg, 'Choose a library first.', 'warn'); return; }
            list.innerHTML = '<div class="wff-note">Loading…</div>';
            try {
                const top = stack[stack.length - 1];
                const r = await browse('items', { drive_id: drive, item_id: top ? top.id : '', top: 200 });
                const folders = ((r.data || {}).items || []).filter(i => i.type === 'folder');
                list.innerHTML = '<div class="wff-sp-list">'
                    + (stack.length ? '<div data-up="1"><i class="fas fa-level-up-alt"></i> Up</div>' : '')
                    + folders.map(f => `<div data-id="${esc(f.id)}" data-name="${esc(f.name)}"><i class="fas fa-folder"></i> ${esc(f.name)}</div>`).join('')
                    + (folders.length ? '' : '<div class="wff-note">No folders here.</div>') + '</div>';
                list.querySelectorAll('[data-id]').forEach(el => el.addEventListener('click', () => {
                    stack.push({ id: el.dataset.id, name: el.dataset.name });
                    sel('wffSpPath').value = stack.map(s => s.name).join('/');
                    listFolders();
                }));
                const up = list.querySelector('[data-up]');
                if (up) up.addEventListener('click', () => {
                    stack.pop();
                    sel('wffSpPath').value = stack.map(s => s.name).join('/');
                    listFolders();
                });
            } catch (e) {
                list.innerHTML = '';
                flash(msg, e.message, 'error');
            }
        }
        sel('wffSpInt').addEventListener('change', loadSites);
        sel('wffSpSite').addEventListener('change', loadDrives);
        sel('wffSpDrive').addEventListener('change', () => { stack.length = 0; sel('wffSpFolders').innerHTML = ''; });
        sel('wffSpBrowse').addEventListener('click', listFolders);
        sel('wffSpSave').addEventListener('click', async () => {
            const site = sel('wffSpSite'), drive = sel('wffSpDrive');
            if (!drive.value) { flash(msg, 'Choose a site and a library.', 'warn'); return; }
            try {
                await api('PUT', `${base()}/settings`, { sharepoint: {
                    integration_id: Number(sel('wffSpInt').value), drive_id: drive.value,
                    folder_path: sel('wffSpPath').value.trim(),
                    site_name: site.selectedOptions[0] ? site.selectedOptions[0].textContent : '',
                    drive_name: drive.selectedOptions[0] ? drive.selectedOptions[0].textContent : '',
                    conflict_behavior: sel('wffSpKeep').checked ? 'rename' : 'replace' } });
                await loadDetail(true);
                flash(document.getElementById('wffSpMsg'), 'Saved. The next run copies its results there.', 'ok');
            } catch (e) {
                flash(msg, e.message, 'error');
            }
        });
        loadSites();
    }

    // --------------------------------------------------------------- start
    document.getElementById('wffRefreshList').addEventListener('click', loadList);
    const goBtn = document.getElementById('wffCandidateGo');
    if (goBtn) goBtn.addEventListener('click', () => {
        const v = document.getElementById('wffCandidate').value;
        if (v) select(v);
    });
    const openedWith = currentId;     // /workflow-files/<id> (the Designer's Files button)
    loadList().then(() => { if (openedWith) loadDetail(); });
})();
