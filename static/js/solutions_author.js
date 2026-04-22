/* Solutions Gallery — author (creator) JS
 *
 *   SolutionsAuthorList   — drafts + published lists
 *   SolutionsAuthorWizard — multi-step builder
 */

(function () {
    'use strict';

    function fetchJson(url, opts) {
        return fetch(url, Object.assign({ credentials: 'same-origin' }, opts || {}))
            .then(function (r) {
                return r.json().then(function (d) { d.__status = r.status; return d; }).catch(function () {
                    return { __status: r.status, error: 'non-JSON response' };
                });
            });
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function readFileAsBase64(file) {
        return new Promise(function (resolve, reject) {
            var r = new FileReader();
            r.onload = function () {
                // dataURL -> strip prefix
                var s = r.result;
                var idx = s.indexOf(',');
                resolve(idx >= 0 ? s.slice(idx + 1) : s);
            };
            r.onerror = function () { reject(new Error('read failed')); };
            r.readAsDataURL(file);
        });
    }

    // ─────────────────────────────────────────────────────────────
    // Drafts + published list
    // ─────────────────────────────────────────────────────────────

    var SolutionsAuthorList = {
        init: function () {
            this.loadDrafts();
            this.loadPublished();
        },

        loadDrafts: function () {
            var table = document.getElementById('draftsTable');
            var empty = document.getElementById('draftsEmpty');
            var body = document.getElementById('draftsBody');
            body.innerHTML = '';

            fetchJson('/api/solutions/drafts').then(function (data) {
                var drafts = (data && data.drafts) || [];
                if (!drafts.length) { empty.style.display = ''; table.style.display = 'none'; return; }
                empty.style.display = 'none'; table.style.display = '';
                drafts.forEach(function (d) {
                    var tr = document.createElement('tr');
                    tr.innerHTML =
                        '<td>' + escapeHtml(d.name || '(unnamed)') + '</td>' +
                        '<td><code>' + escapeHtml(d.id) + '</code></td>' +
                        '<td>' + escapeHtml(d.version) + '</td>' +
                        '<td><small class="text-muted">' + escapeHtml((d.updated_at || '').slice(0, 19)) + '</small></td>' +
                        '<td class="text-right">' +
                            '<a href="/solutions/author/edit/' + encodeURIComponent(d.draft_id) + '" class="btn btn-sm btn-outline-primary">Edit</a> ' +
                            '<button class="btn btn-sm btn-outline-danger" data-draft-id="' + escapeHtml(d.draft_id) + '">Delete</button>' +
                        '</td>';
                    body.appendChild(tr);
                });
                body.querySelectorAll('button[data-draft-id]').forEach(function (btn) {
                    btn.addEventListener('click', function () {
                        var id = btn.getAttribute('data-draft-id');
                        if (!confirm('Delete draft ' + id + '?')) return;
                        fetchJson('/api/solutions/drafts/' + encodeURIComponent(id), { method: 'DELETE' })
                            .then(function () { SolutionsAuthorList.loadDrafts(); });
                    });
                });
            });
        },

        loadPublished: function () {
            var container = document.getElementById('publishedList');
            fetchJson('/api/solutions/author/published').then(function (data) {
                var entries = (data && data.published) || [];
                if (!entries.length) { container.innerHTML = '<p class="text-muted"><em>No bundles in solutions_builtin/ yet.</em></p>'; return; }
                var html = '<ul class="mb-0">';
                entries.forEach(function (e) {
                    html += '<li><strong>' + escapeHtml(e.name || e.id) + '</strong> <code>' + escapeHtml(e.id) + '</code> v' + escapeHtml(e.version) + '</li>';
                });
                html += '</ul>';
                container.innerHTML = html;
            });
        },
    };

    // ─────────────────────────────────────────────────────────────
    // Wizard
    // ─────────────────────────────────────────────────────────────

    var SolutionsAuthorWizard = {
        state: {
            draftId: '',
            assets: {},                  // server-side available assets
            selections: {                // what user picked
                agent_ids: [], data_agent_ids: [], tool_names: [], workflow_names: [],
                integration_ids: [], connection_ids: [],
                environment_ids: [], knowledge_document_ids: [],
            },
            credentials: [],
            postInstall: [],
            iconFile: null,
            screenshotFiles: [],
            logoFile: null,
        },

        init: function (draftId) {
            this.state.draftId = draftId || '';
            var self = this;

            this.bindButtons();

            fetchJson('/api/solutions/author/assets').then(function (data) {
                self.state.assets = data || {};
                self.renderAllPickers();

                if (self.state.draftId) {
                    self.loadDraft(self.state.draftId);
                } else {
                    self.addCredentialRow(null);
                }
            });
        },

        bindButtons: function () {
            var self = this;
            document.getElementById('addCredBtn').onclick = function () { self.addCredentialRow(null); };
            document.getElementById('rescanBtn').onclick = function () { self.rescanPlaceholders(); };
            document.getElementById('addActionBtn').onclick = function () { self.addPostInstallRow(null); };
            var suggestBtn = document.getElementById('suggestActionsBtn');
            if (suggestBtn) suggestBtn.onclick = function () { self.suggestPostInstallActions(); };

            document.getElementById('saveDraftBtn').onclick = function () { self.saveDraft(); };
            document.getElementById('validateBtn').onclick = function () { self.validate(); };
            document.getElementById('downloadBtn').onclick = function () { self.buildAndDownload(); };
            document.getElementById('publishBtn').onclick = function () { self.buildAndPublish(); };
            document.getElementById('testInstallBtn').onclick = function () { self.testInstall(); };

            document.getElementById('iconFile').onchange = function (ev) {
                self.state.iconFile = ev.target.files[0] || null;
            };
            document.getElementById('screenshotFiles').onchange = function (ev) {
                self.state.screenshotFiles = Array.from(ev.target.files || []);
            };
            var logoInput = document.getElementById('brLogoFile');
            if (logoInput) {
                logoInput.onchange = function (ev) {
                    var f = ev.target.files[0] || null;
                    self.state.logoFile = f;
                    // Auto-fill the hidden logo_path field from the file's name.
                    var hidden = document.getElementById('brLogoPath');
                    var hint = document.getElementById('brLogoHint');
                    if (f) {
                        var ext = (f.name.split('.').pop() || 'png').toLowerCase();
                        var safe = 'logo.' + ext;
                        if (hidden) hidden.value = safe;
                        if (hint) hint.textContent = 'Will be bundled as preview/' + safe + '.';
                    } else {
                        if (hidden) hidden.value = '';
                        if (hint) hint.textContent = 'Choose an image to include in the bundle. If left blank, the tile icon from step 6 is used.';
                    }
                };
            }
        },

        renderAllPickers: function () {
            this.renderPicker('pickAgents',       this.state.assets.agents       || [], 'id',   'agent_ids');
            this.renderPicker('pickDataAgents',   this.state.assets.data_agents  || [], 'id',   'data_agent_ids');
            this.renderPicker('pickTools',        this.state.assets.tools        || [], 'name', 'tool_names');
            this.renderPicker('pickWorkflows',    this.state.assets.workflows    || [], 'name', 'workflow_names');
            this.renderPicker('pickIntegrations', this.state.assets.integrations || [], 'id',   'integration_ids');
            this.renderPicker('pickConnections',  this.state.assets.connections  || [], 'id',   'connection_ids');
            this.renderPicker('pickEnvironments', this.state.assets.environments || [], 'id',   'environment_ids');
            this.renderPicker('pickKnowledge',    this.state.assets.knowledge    || [], 'id',   'knowledge_document_ids');
            this.bindPickerCascades();
        },

        renderPicker: function (containerId, items, keyField, selectionKey) {
            var c = document.getElementById(containerId);
            if (!items.length) { c.innerHTML = '<small class="text-muted"><em>None available</em></small>'; return; }
            var searchId = containerId + '_search';
            var listId = containerId + '_list';
            var onlyId = containerId + '_only';
            var html =
                '<div class="asset-picker-toolbar">' +
                    '<input type="text" id="' + searchId + '" class="form-control form-control-sm asset-picker-search" placeholder="Search...">' +
                    '<label class="asset-only-toggle" title="Show only selected items">' +
                        '<input type="checkbox" id="' + onlyId + '"> only selected' +
                    '</label>' +
                '</div>' +
                '<div class="asset-picker-list" id="' + listId + '">';
            items.forEach(function (it, i) {
                var val = it[keyField];
                var id = containerId + '_' + i;
                var tipAttr = it.tooltip ? ' title="' + escapeHtml(it.tooltip) + '"' : '';
                var depsAttr = it.deps ? ' data-deps="' + escapeHtml(JSON.stringify(it.deps)) + '"' : '';
                var haystack = ((it.display || '') + ' ' + (it.tooltip || '')).toLowerCase();
                html +=
                    '<label class="asset-row" data-haystack="' + escapeHtml(haystack) + '"' + tipAttr + '>' +
                        '<input type="checkbox" id="' + id + '" value="' + escapeHtml(String(val)) +
                            '" data-selkey="' + selectionKey + '"' + depsAttr + '> ' +
                        '<span class="asset-label">' + escapeHtml(it.display || val) + '</span>' +
                        (it.tooltip ? '<span class="asset-hint">' + escapeHtml(it.tooltip) + '</span>' : '') +
                    '</label>';
            });
            html += '</div>';
            c.innerHTML = html;

            var searchInput = document.getElementById(searchId);
            var onlyToggle = document.getElementById(onlyId);
            var listEl = document.getElementById(listId);

            function applyFilter() {
                var q = searchInput.value.trim().toLowerCase();
                var onlySelected = onlyToggle.checked;
                listEl.querySelectorAll('.asset-row').forEach(function (row) {
                    var matchesQuery = !q || row.getAttribute('data-haystack').indexOf(q) >= 0;
                    var cb = row.querySelector('input[type="checkbox"]');
                    var matchesSelection = !onlySelected || (cb && cb.checked);
                    row.style.display = (matchesQuery && matchesSelection) ? '' : 'none';
                });
            }
            searchInput.addEventListener('input', applyFilter);
            onlyToggle.addEventListener('change', applyFilter);
            listEl.addEventListener('change', applyFilter);
        },

        bindPickerCascades: function () {
            var self = this;
            document.querySelectorAll('.asset-picker input[type="checkbox"]').forEach(function (cb) {
                cb.addEventListener('change', function () { self.onPickerChange(cb); });
            });
        },

        onPickerChange: function (cb) {
            var self = this;
            var key = cb.getAttribute('data-selkey');
            var depsAttr = cb.getAttribute('data-deps');
            var deps = {};
            if (depsAttr) {
                try { deps = JSON.parse(depsAttr) || {}; } catch (e) { deps = {}; }
            }

            if (cb.checked) {
                // Cascade-select dependencies and mark them auto-selected.
                Object.keys(deps).forEach(function (depKey) {
                    (deps[depKey] || []).forEach(function (depVal) {
                        var target = document.querySelector(
                            '.asset-picker input[data-selkey="' + depKey + '"][value="' + depVal + '"]'
                        );
                        if (target && !target.checked) {
                            target.checked = true;
                            target.closest('label').classList.add('auto-selected');
                            self.onPickerChange(target);
                        }
                    });
                });
            } else {
                // Cascade-uncheck: for each dep this parent brought in, check
                // whether any OTHER still-checked parent requires it. If not
                // — and the dep is still marked auto-selected — uncheck it
                // too, recursively. Direct user picks (no .auto-selected
                // class) are left alone.
                Object.keys(deps).forEach(function (depKey) {
                    (deps[depKey] || []).forEach(function (depVal) {
                        var target = document.querySelector(
                            '.asset-picker input[data-selkey="' + depKey + '"][value="' + depVal + '"]'
                        );
                        if (!target || !target.checked) return;
                        var label = target.closest('label');
                        if (!label || !label.classList.contains('auto-selected')) return;
                        if (!self.isStillRequired(depKey, depVal)) {
                            target.checked = false;
                            label.classList.remove('auto-selected');
                            self.onPickerChange(target);
                        }
                    });
                });
            }

            // Connection credentials always re-sync on any change.
            if (key === 'connection_ids') {
                self.syncConnectionCredentials();
            }
        },

        isStillRequired: function (depKey, depVal) {
            // Is any *currently-checked* item's deps list still pointing
            // at this (depKey, depVal)?
            var str = String(depVal);
            var checked = document.querySelectorAll('.asset-picker input[type="checkbox"]:checked[data-deps]');
            for (var i = 0; i < checked.length; i++) {
                var raw = checked[i].getAttribute('data-deps');
                if (!raw) continue;
                var d;
                try { d = JSON.parse(raw); } catch (e) { continue; }
                var arr = (d && d[depKey]) || [];
                for (var j = 0; j < arr.length; j++) {
                    if (String(arr[j]) === str) return true;
                }
            }
            return false;
        },

        syncConnectionCredentials: function () {
            // Current set of selected connection ids
            var selected = [];
            document.querySelectorAll('input[data-selkey="connection_ids"]:checked').forEach(function (cb) {
                var label = cb.closest('label');
                var display = (label && label.querySelector('.asset-label')) ? label.querySelector('.asset-label').textContent : cb.value;
                selected.push({ id: cb.value, display: display });
            });

            // Remove auto-generated rows that no longer have a matching connection
            document.querySelectorAll('.cred-row[data-auto-conn]').forEach(function (row) {
                var connId = row.getAttribute('data-auto-conn');
                if (!selected.some(function (s) { return s.id === connId; })) row.remove();
            });

            var sensitive = [
                { key: 'SERVER',   label: 'Database server',   desc: 'Hostname or IP of the database server for {name}' },
                { key: 'DATABASE', label: 'Database name',     desc: 'Target database name for {name}' },
                { key: 'USER',     label: 'Database user',     desc: 'Username for {name}' },
                { key: 'PASSWORD', label: 'Database password', desc: 'Password for {name}. Stored securely in LocalSecrets at install time.' },
            ];
            var existingPlaceholders = {};
            document.querySelectorAll('.cred-row .cred-placeholder').forEach(function (inp) {
                existingPlaceholders[inp.value.trim()] = true;
            });

            selected.forEach(function (s) {
                var safe = (s.display || 'conn').toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '');
                sensitive.forEach(function (sp) {
                    var placeholder = 'CONN_' + safe + '_' + sp.key;
                    if (existingPlaceholders[placeholder]) return;
                    // Only add if there isn't already an auto-row for this connection + key
                    var already = document.querySelector(
                        '.cred-row[data-auto-conn="' + s.id + '"][data-auto-key="' + sp.key + '"]'
                    );
                    if (already) return;
                    SolutionsAuthorWizard.addCredentialRow({
                        placeholder: placeholder,
                        label: sp.label + ' — ' + s.display,
                        required: true,
                        sample_value: '',
                        description: sp.desc.replace('{name}', s.display),
                        _autoConn: s.id,
                        _autoKey: sp.key,
                    });
                });
            });
        },

        collectSelections: function () {
            var numericKeys = {
                agent_ids: 1, data_agent_ids: 1, connection_ids: 1,
                environment_ids: 1, knowledge_document_ids: 1, integration_ids: 1,
            };
            var sel = {
                agent_ids: [], data_agent_ids: [], tool_names: [], workflow_names: [],
                integration_ids: [], connection_ids: [],
                environment_ids: [], knowledge_document_ids: [],
            };
            document.querySelectorAll('.asset-picker input[type="checkbox"]:checked').forEach(function (cb) {
                var key = cb.getAttribute('data-selkey');
                var v = cb.value;
                if (numericKeys[key]) {
                    var n = parseInt(v, 10);
                    sel[key].push(isNaN(n) ? v : n);
                } else {
                    sel[key].push(v);
                }
            });
            this.state.selections = sel;
            return sel;
        },

        addCredentialRow: function (data) {
            var c = data || { placeholder: '', label: '', required: true, sample_value: '', description: '' };
            var container = document.getElementById('credentialRows');
            var row = document.createElement('div');
            row.className = 'form-row cred-row mb-2';
            if (c._autoConn) {
                row.setAttribute('data-auto-conn', c._autoConn);
                row.setAttribute('data-auto-key', c._autoKey || '');
                row.classList.add('auto-generated');
            }
            row.innerHTML =
                '<div class="col-md-3"><input type="text" class="form-control cred-placeholder" placeholder="UPPER_SNAKE" value="' + escapeHtml(c.placeholder) + '"></div>' +
                '<div class="col-md-3"><input type="text" class="form-control cred-label" placeholder="Human label" value="' + escapeHtml(c.label) + '"></div>' +
                '<div class="col-md-2"><input type="text" class="form-control cred-sample" placeholder="Sample value" value="' + escapeHtml(c.sample_value || '') + '"></div>' +
                '<div class="col-md-3"><input type="text" class="form-control cred-desc" placeholder="Help text" value="' + escapeHtml(c.description || '') + '"></div>' +
                '<div class="col-md-1"><button class="btn btn-sm btn-outline-danger cred-remove">&times;</button></div>';
            container.appendChild(row);
            row.querySelector('.cred-remove').onclick = function () {
                row.remove();
                // User explicitly removed an auto row — ensure a re-sync
                // doesn't immediately recreate it while that connection
                // remains selected. We do this by stamping the auto-key back
                // on the user at delete time: a second checkbox change will
                // re-create it, which matches the user's intent.
            };
        },

        collectCredentials: function () {
            var out = [];
            document.querySelectorAll('.cred-row').forEach(function (row) {
                var ph = row.querySelector('.cred-placeholder').value.trim();
                if (!ph) return;
                out.push({
                    placeholder: ph,
                    label: row.querySelector('.cred-label').value.trim() || ph,
                    required: true,
                    sample_value: row.querySelector('.cred-sample').value.trim(),
                    description: row.querySelector('.cred-desc').value.trim(),
                });
            });
            return out;
        },

        rescanPlaceholders: function () {
            var self = this;
            var sel = this.collectSelections();
            var scanText = '';
            (sel.integration_names || []).forEach(function (n) { scanText += '${' + n + '}'; });

            fetchJson('/api/solutions/validate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ manifest: self.buildManifestBody(sel), scan_text: scanText }),
            }).then(function (res) {
                var existing = {};
                self.collectCredentials().forEach(function (c) { existing[c.placeholder] = true; });
                (res.discovered_placeholders || []).forEach(function (ph) {
                    if (!existing[ph]) {
                        self.addCredentialRow({ placeholder: ph, label: ph, required: true, sample_value: '', description: '' });
                    }
                });
            });
        },

        addPostInstallRow: function (data) {
            var self = this;
            var c = data || { type: 'run_workflow', target: '', label: '' };
            var container = document.getElementById('postInstallRows');
            var row = document.createElement('div');
            row.className = 'form-row pi-row mb-2';
            row.innerHTML =
                '<div class="col-md-3"><select class="form-control pi-type">' +
                    '<option value="run_workflow">Run workflow</option>' +
                    '<option value="chat_with_agent">Chat with agent</option>' +
                    '<option value="open_page">Open page</option>' +
                '</select></div>' +
                '<div class="col-md-4 pi-target-cell"></div>' +
                '<div class="col-md-4"><input type="text" class="form-control pi-label" placeholder="Button label" value="' + escapeHtml(c.label) + '"></div>' +
                '<div class="col-md-1"><button class="btn btn-sm btn-outline-danger pi-remove">&times;</button></div>';
            container.appendChild(row);
            var typeEl = row.querySelector('.pi-type');
            typeEl.value = c.type;
            self.renderPostInstallTarget(row, c.target);
            typeEl.onchange = function () { self.renderPostInstallTarget(row, ''); };
            row.querySelector('.pi-remove').onclick = function () { row.remove(); };
        },

        renderPostInstallTarget: function (row, currentValue) {
            // Render the target field based on the selected type. For
            // run_workflow / chat_with_agent we offer a dropdown built from
            // what the author has actually selected in step 2, so the value
            // is always a real thing the installer can find. open_page stays
            // a free-text URL field because the target is arbitrary.
            var cell = row.querySelector('.pi-target-cell');
            var type = row.querySelector('.pi-type').value;
            var options = [];

            if (type === 'run_workflow') {
                document.querySelectorAll('input[data-selkey="workflow_names"]:checked').forEach(function (cb) {
                    var label = cb.closest('label');
                    var display = (label && label.querySelector('.asset-label'))
                        ? label.querySelector('.asset-label').textContent : cb.value;
                    // Strip .json if present; the installer looks up by stem.
                    var stem = display.replace(/\.json$/i, '');
                    options.push({ value: stem, display: stem });
                });
            } else if (type === 'chat_with_agent') {
                var sources = ['agent_ids', 'data_agent_ids'];
                sources.forEach(function (key) {
                    document.querySelectorAll('input[data-selkey="' + key + '"]:checked').forEach(function (cb) {
                        var label = cb.closest('label');
                        var display = (label && label.querySelector('.asset-label'))
                            ? label.querySelector('.asset-label').textContent : cb.value;
                        options.push({ value: display, display: display });
                    });
                });
            }

            if (type === 'open_page') {
                cell.innerHTML =
                    '<input type="text" class="form-control pi-target" placeholder="/path/to/page" value="' +
                    escapeHtml(currentValue || '') + '">';
                return;
            }

            if (!options.length) {
                // Fallback to a free-text field with a helpful hint.
                var hintText = type === 'run_workflow'
                    ? 'No workflow selected — type name or pick one in step 2'
                    : 'No agent selected — type name or pick one in step 2';
                cell.innerHTML =
                    '<input type="text" class="form-control pi-target" placeholder="' + hintText +
                    '" value="' + escapeHtml(currentValue || '') + '">';
                return;
            }

            var html = '<select class="form-control pi-target">';
            html += '<option value="">— pick one —</option>';
            options.forEach(function (o) {
                var sel = (String(o.value) === String(currentValue || '')) ? ' selected' : '';
                html += '<option value="' + escapeHtml(o.value) + '"' + sel + '>' + escapeHtml(o.display) + '</option>';
            });
            html += '</select>';
            cell.innerHTML = html;
        },

        suggestPostInstallActions: function () {
            var self = this;
            // Gather the current selections' display names.
            function pickDisplays(selkey) {
                var out = [];
                document.querySelectorAll('input[data-selkey="' + selkey + '"]:checked').forEach(function (cb) {
                    var label = cb.closest('label');
                    if (label && label.querySelector('.asset-label')) {
                        out.push(label.querySelector('.asset-label').textContent);
                    }
                });
                return out;
            }
            var workflows = pickDisplays('workflow_names');
            var agents = pickDisplays('agent_ids').concat(pickDisplays('data_agent_ids'));

            // Skip rows that already reference the same target so we don't
            // create duplicates when the user clicks Suggest twice.
            var existing = {};
            document.querySelectorAll('.pi-row').forEach(function (row) {
                var t = (row.querySelector('.pi-type') || {}).value || '';
                var tgt = (row.querySelector('.pi-target') || {}).value || '';
                if (t && tgt) existing[t + ':' + tgt] = true;
            });

            var added = 0;
            workflows.forEach(function (w) {
                var stem = w.replace(/\.json$/i, '');
                if (existing['run_workflow:' + stem]) return;
                self.addPostInstallRow({
                    type: 'run_workflow', target: stem, label: 'Run ' + stem,
                });
                added++;
            });
            agents.forEach(function (a) {
                if (existing['chat_with_agent:' + a]) return;
                self.addPostInstallRow({
                    type: 'chat_with_agent', target: a, label: 'Chat with ' + a,
                });
                added++;
            });

            if (added === 0 && !workflows.length && !agents.length) {
                self.setResult(
                    'Nothing to suggest yet — pick at least one workflow or agent in step 2 first.',
                    'info'
                );
            } else if (added === 0) {
                self.setResult('All selected workflows/agents already have a suggested action.', 'info');
            }
        },

        collectPostInstall: function () {
            var out = [];
            document.querySelectorAll('.pi-row').forEach(function (row) {
                var t = row.querySelector('.pi-type').value;
                var target = row.querySelector('.pi-target').value.trim();
                var label = row.querySelector('.pi-label').value.trim();
                if (t && target && label) out.push({ type: t, target: target, label: label });
            });
            return out;
        },

        buildManifestBody: function (selections) {
            var tags = document.getElementById('mTags').value.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
            return {
                id: document.getElementById('mId').value.trim(),
                name: document.getElementById('mName').value.trim(),
                version: document.getElementById('mVersion').value.trim() || '1.0.0',
                vertical: document.getElementById('mVertical').value.trim(),
                tags: tags,
                description: document.getElementById('mDescription').value.trim(),
                author: document.getElementById('mAuthor').value.trim(),
                homepage_url: document.getElementById('mHomepage').value.trim(),
                credentials: this.collectCredentials(),
                post_install: this.collectPostInstall(),
                // Preview of what the bundler will emit into manifest.assets.*
                // at build time — without this, Validate would always report
                // post_install targets as missing because assets is empty.
                assets: this.previewAssets(),
            };
        },

        previewAssets: function () {
            function displaysFor(selkey) {
                var out = [];
                document.querySelectorAll('input[data-selkey="' + selkey + '"]:checked').forEach(function (cb) {
                    var label = cb.closest('label');
                    var display = (label && label.querySelector('.asset-label'))
                        ? label.querySelector('.asset-label').textContent.trim()
                        : cb.value;
                    if (display) out.push(display);
                });
                return out;
            }
            function stem(name) {
                return name.replace(/\.json$/i, '');
            }
            // Agents: bundler stores the description string. Combine custom + data agents.
            var agents = displaysFor('agent_ids').concat(displaysFor('data_agent_ids'));
            // Workflows: bundler stores "<stem>.json". The wizard's display is already the stem.
            var workflows = displaysFor('workflow_names').map(function (n) { return stem(n) + '.json'; });
            // Tools: stored as folder names.
            var tools = displaysFor('tool_names');
            // Integrations: bundler stores "<name>.json".
            var integrations = displaysFor('integration_ids').map(function (n) { return n + '.json'; });
            // Connections: stored as "<name>.json".
            var connections = displaysFor('connection_ids').map(function (n) { return n + '.json'; });
            // Environments: stored as "<name>.zip".
            var environments = displaysFor('environment_ids').map(function (n) { return n + '.zip'; });
            // Knowledge: per-doc entries; filenames.
            var knowledge = displaysFor('knowledge_document_ids');
            return {
                agents: agents,
                tools: tools,
                workflows: workflows,
                integrations: integrations,
                connections: connections,
                environments: environments,
                knowledge: knowledge,
                data: {},
            };
        },

        buildBranding: function () {
            return {
                display_name: document.getElementById('brDisplay').value.trim(),
                tagline: document.getElementById('brTagline').value.trim(),
                logo_path: document.getElementById('brLogoPath').value.trim(),
                primary_color: document.getElementById('brColor').value.trim(),
            };
        },

        collectPreviewFiles: function () {
            var self = this;
            var files = {};
            var promises = [];
            if (self.state.iconFile) {
                var ext = (self.state.iconFile.name.split('.').pop() || 'png').toLowerCase();
                promises.push(readFileAsBase64(self.state.iconFile).then(function (b64) {
                    files['icon.' + ext] = b64;
                }));
            }
            if (self.state.logoFile) {
                var lext = (self.state.logoFile.name.split('.').pop() || 'png').toLowerCase();
                promises.push(readFileAsBase64(self.state.logoFile).then(function (b64) {
                    files['logo.' + lext] = b64;
                }));
            }
            (self.state.screenshotFiles || []).forEach(function (f, i) {
                promises.push(readFileAsBase64(f).then(function (b64) {
                    files['screenshot_' + (i + 1) + '_' + (f.name || 'img.png')] = b64;
                }));
            });
            return Promise.all(promises).then(function () { return files; });
        },

        assembleRequestBody: function () {
            var self = this;
            var sel = this.collectSelections();
            return this.collectPreviewFiles().then(function (preview) {
                return {
                    manifest: self.buildManifestBody(sel),
                    selections: sel,
                    branding: self.buildBranding(),
                    readme: document.getElementById('readmeBody').value,
                    preview_files: preview,
                };
            });
        },

        saveDraft: function () {
            var self = this;
            var sel = this.collectSelections();
            var body = {
                manifest: this.buildManifestBody(sel),
                selections: sel,
                branding: this.buildBranding(),
                readme: document.getElementById('readmeBody').value,
            };
            var url = '/api/solutions/drafts' + (this.state.draftId ? '/' + encodeURIComponent(this.state.draftId) : '');
            var method = this.state.draftId ? 'PUT' : 'POST';
            fetchJson(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            }).then(function (res) {
                if (!self.state.draftId && res.draft_id) {
                    self.state.draftId = res.draft_id;
                    window.history.replaceState({}, '', '/solutions/author/edit/' + res.draft_id);
                }
                self.setResult('Draft saved.', 'success');
            }).catch(function (err) {
                self.setResult('Save failed: ' + err, 'danger');
            });
        },

        loadDraft: function (draftId) {
            var self = this;
            fetchJson('/api/solutions/drafts/' + encodeURIComponent(draftId)).then(function (d) {
                if (d.__status !== 200) { self.setResult('Could not load draft.', 'danger'); return; }
                var m = d.manifest || {};
                document.getElementById('mId').value = m.id || '';
                document.getElementById('mName').value = m.name || '';
                document.getElementById('mVersion').value = m.version || '1.0.0';
                document.getElementById('mVertical').value = m.vertical || '';
                document.getElementById('mTags').value = (m.tags || []).join(', ');
                document.getElementById('mDescription').value = m.description || '';
                document.getElementById('mAuthor').value = m.author || '';
                document.getElementById('mHomepage').value = m.homepage_url || '';
                document.getElementById('readmeBody').value = d.readme || '';
                var br = d.branding || {};
                document.getElementById('brDisplay').value = br.display_name || '';
                document.getElementById('brTagline').value = br.tagline || '';
                document.getElementById('brColor').value = br.primary_color || '';
                document.getElementById('brLogoPath').value = br.logo_path || '';

                (m.credentials || []).forEach(function (c) { self.addCredentialRow(c); });
                if (!(m.credentials || []).length) self.addCredentialRow(null);
                (m.post_install || []).forEach(function (p) { self.addPostInstallRow(p); });

                // Apply selections
                var sel = d.selections || {};
                Object.keys(sel).forEach(function (key) {
                    (sel[key] || []).forEach(function (v) {
                        document.querySelectorAll('input[data-selkey="' + key + '"]').forEach(function (cb) {
                            if (String(cb.value) === String(v)) cb.checked = true;
                        });
                    });
                });
            });
        },

        validate: function () {
            var self = this;
            var sel = this.collectSelections();
            fetchJson('/api/solutions/validate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ manifest: this.buildManifestBody(sel) }),
            }).then(function (r) {
                if (r.valid) {
                    self.setResult('Manifest is valid.', 'success');
                } else {
                    self.setResult('<strong>Invalid:</strong><ul><li>' + (r.errors || []).map(escapeHtml).join('</li><li>') + '</li></ul>', 'danger');
                }
            });
        },

        buildAndDownload: function () {
            var self = this;
            this.assembleRequestBody().then(function (body) {
                self.setResult('Building...', 'info');
                fetch('/api/solutions/build', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                }).then(function (resp) {
                    if (!resp.ok) {
                        return resp.json().then(function (j) { throw new Error(j.error || ('HTTP ' + resp.status)); });
                    }
                    return resp.blob().then(function (blob) {
                        var disposition = resp.headers.get('Content-Disposition') || '';
                        var m = disposition.match(/filename="?([^";]+)"?/);
                        var filename = (m && m[1]) || 'solution.zip';
                        var url = URL.createObjectURL(blob);
                        var a = document.createElement('a');
                        a.href = url; a.download = filename; document.body.appendChild(a); a.click();
                        setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 1000);
                        self.setResult('Downloaded ' + filename + '.', 'success');
                    });
                }).catch(function (err) {
                    self.setResult('Build failed: ' + err.message, 'danger');
                });
            });
        },

        buildAndPublish: function () {
            var self = this;
            this.assembleRequestBody().then(function (body) {
                self.setResult('Publishing...', 'info');
                fetchJson('/api/solutions/build/publish', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                }).then(function (r) {
                    if (r.__status && r.__status !== 200) {
                        self.setResult('Publish failed: ' + (r.error || r.__status), 'danger');
                    } else {
                        self.setResult('Published to ' + escapeHtml(r.path) + ' (' + r.bytes + ' bytes).', 'success');
                    }
                });
            });
        },

        testInstall: function () {
            var self = this;
            this.assembleRequestBody().then(function (body) {
                body.name_suffix = '_test';
                body.conflict_mode = 'rename';
                self.setResult('Running test install...', 'info');
                fetchJson('/api/solutions/test_install', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                }).then(function (r) {
                    var cls = r.success ? 'success' : 'warning';
                    var msg = 'Test install ' + (r.success ? 'ok' : 'had issues') +
                        ' — ' + ((r.assets || []).length) + ' assets installed.';
                    if (r.errors && r.errors.length) msg += '<ul><li>' + r.errors.map(escapeHtml).join('</li><li>') + '</li></ul>';
                    self.setResult(msg, cls);
                });
            });
        },

        setResult: function (html, level) {
            var el = document.getElementById('buildResult');
            el.innerHTML = '<div class="alert alert-' + (level || 'info') + '">' + html + '</div>';
        },
    };

    window.SolutionsAuthorList = SolutionsAuthorList;
    window.SolutionsAuthorWizard = SolutionsAuthorWizard;
})();
