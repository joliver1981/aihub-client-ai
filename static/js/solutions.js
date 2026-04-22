/* Solutions Gallery — consumer-side JS
 *
 * Two namespaced objects:
 *   SolutionsGallery  — powers /solutions (tile grid + upload)
 *   SolutionsInstallWizard — powers /solutions/install/<id> (preview → credentials → install → done)
 */

(function () {
    'use strict';

    function fetchJson(url, opts) {
        return fetch(url, Object.assign({ credentials: 'same-origin' }, opts || {}))
            .then(function (r) {
                return r.json().then(function (d) { d.__status = r.status; return d; });
            });
    }

    function el(tag, attrs, children) {
        var e = document.createElement(tag);
        if (attrs) {
            Object.keys(attrs).forEach(function (k) {
                if (k === 'class') e.className = attrs[k];
                else if (k === 'html') e.innerHTML = attrs[k];
                else if (k === 'onclick') e.onclick = attrs[k];
                else e.setAttribute(k, attrs[k]);
            });
        }
        (children || []).forEach(function (c) {
            if (c == null) return;
            e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
        });
        return e;
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // ─────────────────────────────────────────────────────────────
    // Gallery
    // ─────────────────────────────────────────────────────────────

    var SolutionsGallery = {
        init: function () {
            this.loadCatalog();
            var uploadInput = document.getElementById('uploadSolutionFile');
            if (uploadInput) uploadInput.addEventListener('change', this.onUpload.bind(this));
        },

        loadCatalog: function () {
            var loading = document.getElementById('solutionsLoading');
            var empty = document.getElementById('solutionsEmpty');
            var grid = document.getElementById('solutionsGrid');
            grid.innerHTML = '';

            fetchJson('/api/solutions/catalog').then(function (data) {
                loading.style.display = 'none';
                var entries = (data && data.solutions) || [];
                if (entries.length === 0) {
                    empty.style.display = '';
                    return;
                }
                entries.forEach(function (e) {
                    grid.appendChild(SolutionsGallery.renderTile(e));
                });
            }).catch(function (err) {
                loading.innerHTML = '<div class="alert alert-danger">Could not load catalog: ' + escapeHtml(err) + '</div>';
            });
        },

        renderTile: function (e) {
            var iconEl = e.has_icon
                ? el('img', { class: 'solution-tile-icon', src: '/api/solutions/' + encodeURIComponent(e.id) + '/preview/icon.png' })
                : el('div', { class: 'solution-tile-icon-placeholder' }, [String((e.name || 'S').charAt(0).toUpperCase())]);

            var tags = (e.tags || []).slice(0, 4).map(function (t) {
                return el('span', { class: 'solution-tile-tag' }, [t]);
            });

            var tile = el('a', { class: 'solution-tile', href: '/solutions/install/' + encodeURIComponent(e.id) }, [
                el('span', { class: 'solution-tile-source ' + (e.source || 'bundled') }, [e.source || 'bundled']),
                iconEl,
                el('h4', { class: 'solution-tile-name' }, [e.name || e.id]),
                el('div', { class: 'solution-tile-version' }, ['v' + (e.version || '1.0.0') + (e.vertical ? ' · ' + e.vertical : '')]),
                el('p', { class: 'solution-tile-desc' }, [e.description || '']),
                el('div', { class: 'solution-tile-tags' }, tags),
            ]);
            return tile;
        },

        onUpload: function (ev) {
            var f = ev.target.files[0];
            if (!f) return;

            // Stage the file on the server, then route the user into the
            // normal install wizard — so the Credentials step runs and the
            // conflict preview shows up just like clicking a gallery tile.
            var fd = new FormData();
            fd.append('file', f);

            fetch('/api/solutions/upload_stage', {
                method: 'POST',
                credentials: 'same-origin',
                body: fd,
            }).then(function (r) {
                return r.json().then(function (d) { d.__status = r.status; return d; });
            }).then(function (r) {
                ev.target.value = '';
                if (r.__status !== 200 || !r.install_url) {
                    alert('Upload failed: ' + (r.error || ('HTTP ' + r.__status)));
                    return;
                }
                window.location.href = r.install_url;
            }).catch(function (err) {
                ev.target.value = '';
                alert('Upload failed: ' + err);
            });
        },
    };

    // ─────────────────────────────────────────────────────────────
    // Install Wizard
    // ─────────────────────────────────────────────────────────────

    var SolutionsInstallWizard = {
        state: {
            solutionId: null,
            manifest: null,
            analysis: null,
            postInstall: [],
        },

        init: function (solutionId) {
            this.state.solutionId = solutionId;
            var self = this;

            Promise.all([
                fetchJson('/api/solutions/' + encodeURIComponent(solutionId)),
                fetchJson('/api/solutions/' + encodeURIComponent(solutionId) + '/readme'),
                fetch('/api/solutions/' + encodeURIComponent(solutionId) + '/analyze', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: '{}',
                }).then(function (r) { return r.json().catch(function () { return {}; }); }),
            ]).then(function (arr) {
                var detail = arr[0], readme = arr[1], analysis = arr[2];
                if (detail.__status !== 200) {
                    document.getElementById('stepPreview').innerHTML =
                        '<div class="alert alert-danger">Could not load solution: ' + escapeHtml(detail.error || '') + '</div>';
                    return;
                }
                self.state.manifest = detail.manifest || {};
                self.state.conflicts = (analysis && analysis.conflicts) || {};
                self.renderPreview(detail.entry || {}, self.state.manifest, (readme && readme.readme) || '');
                self.renderConflicts(self.state.conflicts);
            });

            document.getElementById('toCredentials').onclick = function () { self.gotoStep('credentials'); self.renderCredentials(); };
            document.getElementById('backToPreview').onclick = function () { self.gotoStep('preview'); };
            document.getElementById('toInstall').onclick = function () { self.gotoStep('install'); };
            document.getElementById('backToCredentials').onclick = function () { self.gotoStep('credentials'); };
            document.getElementById('runInstall').onclick = function () { self.runInstall(); };

            document.querySelectorAll('.install-steps .nav-link').forEach(function (a) {
                a.addEventListener('click', function (ev) { ev.preventDefault(); });
            });
        },

        gotoStep: function (step) {
            ['preview', 'credentials', 'install', 'done'].forEach(function (s) {
                var pane = document.getElementById('step' + s.charAt(0).toUpperCase() + s.slice(1));
                pane.style.display = (s === step) ? '' : 'none';
            });
            document.querySelectorAll('.install-steps .nav-link').forEach(function (a) {
                a.classList.toggle('active', a.getAttribute('data-step') === step);
            });
        },

        renderPreview: function (entry, manifest, readmeMd) {
            var name = manifest.name || entry.name || entry.id || 'Solution';
            var v = manifest.version || entry.version || '1.0.0';
            document.getElementById('installHeader').innerHTML =
                '<h1>' + escapeHtml(name) + '</h1>' +
                '<p class="text-muted">v' + escapeHtml(v) +
                (entry.vertical ? ' · ' + escapeHtml(entry.vertical) : '') +
                (manifest.author ? ' · by ' + escapeHtml(manifest.author) : '') + '</p>';

            var meta = document.getElementById('previewMeta');
            meta.innerHTML = '<p>' + escapeHtml(manifest.description || '') + '</p>';

            var a = manifest.assets || {};
            var parts = [];
            function chip(label, arr) {
                if (arr && arr.length) parts.push('<span class="asset-chip">' + escapeHtml(label) + ': ' + arr.length + '</span>');
            }
            chip('Agents', a.agents); chip('Tools', a.tools); chip('Workflows', a.workflows);
            chip('Integrations', a.integrations); chip('Connections', a.connections);
            chip('Environments', a.environments); chip('Knowledge', a.knowledge);
            if (a.data && (a.data.schema_sql || (a.data.seeds || []).length || (a.data.sample_inputs || []).length)) {
                parts.push('<span class="asset-chip">Data: ' +
                    (a.data.schema_sql ? 'schema + ' : '') +
                    ((a.data.seeds || []).length) + ' seeds, ' +
                    ((a.data.sample_inputs || []).length) + ' samples</span>');
            }
            document.getElementById('previewAssets').innerHTML =
                '<strong>What gets installed:</strong><br>' + (parts.join(' ') || '<em>Nothing declared</em>');

            var readmeEl = document.getElementById('previewReadme');
            if (readmeMd && window.marked) {
                readmeEl.innerHTML = window.marked.parse(readmeMd);
            } else {
                readmeEl.textContent = readmeMd || '';
            }
        },

        renderConflicts: function (conflicts) {
            if (!conflicts) return;
            var total = 0, lines = [];
            Object.keys(conflicts).forEach(function (kind) {
                var arr = conflicts[kind] || [];
                if (!arr.length) return;
                total += arr.length;
                lines.push('<li><strong>' + escapeHtml(kind) + ':</strong> ' + arr.map(escapeHtml).join(', ') + '</li>');
            });
            var assetsDiv = document.getElementById('previewAssets');
            if (!assetsDiv) return;
            if (total === 0) {
                assetsDiv.innerHTML += '<div class="mt-3 text-success"><i class="fas fa-check"></i> No name conflicts with existing items.</div>';
            } else {
                assetsDiv.innerHTML +=
                    '<div class="alert alert-warning mt-3 mb-0">' +
                        '<strong><i class="fas fa-exclamation-triangle"></i> ' + total + ' existing item(s) with matching names.</strong>' +
                        ' The <em>Conflict mode</em> on the next step controls what happens — ' +
                        '<code>rename</code> keeps both, <code>skip</code> leaves existing untouched, <code>overwrite</code> replaces them.' +
                        '<ul class="mb-0 mt-2" style="font-size:12px;">' + lines.join('') + '</ul>' +
                    '</div>';
            }
        },

        renderCredentials: function () {
            var form = document.getElementById('credentialsForm');
            form.innerHTML = '';
            var creds = (this.state.manifest && this.state.manifest.credentials) || [];
            if (!creds.length) {
                form.innerHTML = '<p class="text-muted"><em>This solution requires no credentials.</em></p>';
                return;
            }
            creds.forEach(function (c) {
                var id = 'cred_' + c.placeholder;
                var wrap = el('div', { class: 'form-group' });
                wrap.innerHTML =
                    '<label for="' + id + '">' + escapeHtml(c.label || c.placeholder) +
                    (c.required ? ' <span class="text-danger">*</span>' : '') + '</label>' +
                    '<input type="text" class="form-control" id="' + id +
                    '" data-placeholder="' + escapeHtml(c.placeholder) +
                    (c.sample_value ? '" placeholder="Sample: ' + escapeHtml(c.sample_value) : '') + '">' +
                    (c.description ? '<small class="form-text text-muted">' + escapeHtml(c.description) + '</small>' : '');
                form.appendChild(wrap);
            });
        },

        collectCredentials: function () {
            var out = {};
            document.querySelectorAll('#credentialsForm input[data-placeholder]').forEach(function (inp) {
                var ph = inp.getAttribute('data-placeholder');
                var val = inp.value.trim();
                if (val) out[ph] = val;
            });
            return out;
        },

        runInstall: function () {
            var self = this;
            var progress = document.getElementById('installProgress');
            var status = document.getElementById('installStatus');
            progress.style.display = '';
            status.textContent = 'Installing...';
            document.getElementById('runInstall').disabled = true;

            var body = {
                credentials: this.collectCredentials(),
                conflict_mode: document.getElementById('conflictMode').value,
                name_suffix: document.getElementById('nameSuffix').value,
            };

            fetch('/api/solutions/' + encodeURIComponent(this.state.solutionId) + '/install', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            }).then(function (r) { return r.json().then(function (d) { d.__status = r.status; return d; }); })
              .then(function (r) {
                  progress.style.display = 'none';
                  document.getElementById('runInstall').disabled = false;
                  self.renderDone(r);
                  self.gotoStep('done');
              }).catch(function (err) {
                  status.innerHTML = '<span class="text-danger">Install failed: ' + escapeHtml(err) + '</span>';
                  document.getElementById('runInstall').disabled = false;
              });
        },

        renderDone: function (result) {
            var summary = document.getElementById('installSummary');
            var assets = result.assets || [];
            var errs = result.errors || [];
            var html = '<h3>' + (result.success
                ? '<i class="fas fa-check-circle text-success"></i> Installed'
                : '<i class="fas fa-exclamation-triangle text-warning"></i> Completed with issues') + '</h3>';
            html += '<p>' + assets.length + ' asset' + (assets.length === 1 ? '' : 's') + ' installed.</p>';
            if (assets.length) {
                html += '<ul>';
                assets.forEach(function (a) {
                    html += '<li>' + escapeHtml(a.type) + ': ' + escapeHtml(a.name || '(unnamed)') +
                        (a.status ? ' — <em>' + escapeHtml(a.status) + '</em>' : '') + '</li>';
                });
                html += '</ul>';
            }
            if (errs.length) {
                html += '<div class="alert alert-warning"><strong>Warnings / errors:</strong><ul>';
                errs.forEach(function (e) { html += '<li>' + escapeHtml(e) + '</li>'; });
                html += '</ul></div>';
            }
            summary.innerHTML = html;

            var actions = document.getElementById('postInstallActions');
            actions.innerHTML = '';
            (result.post_install || []).forEach(function (a) {
                var btn = el('button', { class: 'btn btn-primary mr-2', onclick: function () { SolutionsInstallWizard.runPostInstall(a); } }, [a.label || a.type]);
                actions.appendChild(btn);
            });
            actions.appendChild(el('a', { class: 'btn btn-outline-secondary', href: '/solutions' }, ['Back to gallery']));
        },

        runPostInstall: function (a) {
            // Simple dispatch — full behavior handled by the destination pages.
            if (a.type === 'open_page' && a.target) {
                window.location.href = a.target;
            } else if (a.type === 'run_workflow' && a.target) {
                window.location.href = '/workflows?run=' + encodeURIComponent(a.target);
            } else if (a.type === 'chat_with_agent' && a.target) {
                window.location.href = '/agents?chat=' + encodeURIComponent(a.target);
            } else {
                alert('Action: ' + a.type + ' → ' + (a.target || '(no target)'));
            }
        },
    };

    window.SolutionsGallery = SolutionsGallery;
    window.SolutionsInstallWizard = SolutionsInstallWizard;
})();
