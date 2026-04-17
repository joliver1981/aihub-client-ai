/**
 * PredictiveForecast — SPA Engine
 * State management, API client, router, component rendering
 */
;(function () {
  'use strict';

  /* ── Utilities ──────────────────────────────────────────────────────────── */
  const $ = (s, p) => (p || document).querySelector(s);
  const $$ = (s, p) => [...(p || document).querySelectorAll(s)];
  const html = (el, h) => { el.innerHTML = h; };
  const show = (el) => { if (el) el.style.display = ''; };
  const hide = (el) => { if (el) el.style.display = 'none'; };
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const fmtNum = (v, d = 4) => v == null ? '—' : Number(v).toFixed(d);

  /* ── API Client ─────────────────────────────────────────────────────────── */
  const API = {
    base: '/api',

    async _json(url, opts) {
      try {
        const res = await fetch(this.base + url, opts);
        const data = await res.json();
        if (!res.ok && !data.error) data.error = `HTTP ${res.status}`;
        return data;
      } catch (e) {
        return { error: e.message || 'Network error' };
      }
    },

    health()       { return this._json('/health'); },
    algorithms()   { return this._json('/algorithms'); },
    models()       { return this._json('/models'); },
    modelConfig(n) { return this._json('/models/' + encodeURIComponent(n)); },

    deleteModel(n) {
      return this._json('/models/' + encodeURIComponent(n), { method: 'DELETE' });
    },

    upload(file) {
      const fd = new FormData();
      fd.append('file', file);
      return this._json('/upload', { method: 'POST', body: fd });
    },

    train(payload) {
      return this._json('/train', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    },

    test(modelName, file) {
      const fd = new FormData();
      fd.append('model_name', modelName);
      fd.append('file', file);
      return this._json('/test', { method: 'POST', body: fd });
    },

    startAnalysis(modelName) {
      return this._json('/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_name: modelName }),
      });
    },

    analyzeStatus(jobId) { return this._json('/analyze/status/' + jobId); },
  };

  /* ── Application State ──────────────────────────────────────────────────── */
  const state = {
    // Current page
    page: 'dashboard',
    // Wizard step (1-5)
    trainStep: 1,
    // Upload result from API
    uploadData: null,
    // Loaded algorithms { key: displayName }
    algorithms: {},
    // All models from API
    models: [],
    // Feature columns selection { colName: boolean }
    featureSelection: {},
    // Test page file
    testFile: null,
  };

  /* ── Hyperparameter Definitions per Algorithm ───────────────────────────── */
  const HP_DEFS = {
    neural_network: [
      { key: 'epochs',     label: 'Epochs',        type: 'number', default: 100, min: 10, max: 2000 },
      { key: 'batch_size', label: 'Batch Size',     type: 'number', default: 32,  min: 8,  max: 512 },
      { key: 'layer_1',    label: 'Layer 1 Neurons', type: 'number', default: 128, min: 16, max: 1024 },
      { key: 'layer_2',    label: 'Layer 2 Neurons', type: 'number', default: 64,  min: 16, max: 512 },
      { key: 'dropout',    label: 'Dropout Rate',    type: 'number', default: 0.2, min: 0,  max: 0.8, step: 0.05 },
      { key: 'patience',   label: 'Early Stop Patience', type: 'number', default: 15, min: 3, max: 100 },
      { key: 'use_kfold',  label: 'Use K-Fold CV',  type: 'checkbox', default: true },
      { key: 'num_folds',  label: 'Number of Folds', type: 'number', default: 5, min: 2, max: 10 },
    ],
    xgboost: [
      { key: 'n_estimators',      label: 'Trees',          type: 'number', default: 500, min: 50, max: 5000 },
      { key: 'max_depth',         label: 'Max Depth',      type: 'number', default: 6,   min: 2,  max: 20 },
      { key: 'learning_rate',     label: 'Learning Rate',  type: 'number', default: 0.05, min: 0.001, max: 1, step: 0.01 },
      { key: 'subsample',         label: 'Subsample',      type: 'number', default: 0.8, min: 0.3, max: 1, step: 0.05 },
      { key: 'colsample_bytree',  label: 'Col Sample',     type: 'number', default: 0.8, min: 0.3, max: 1, step: 0.05 },
    ],
    random_forest: [
      { key: 'n_estimators',      label: 'Trees',          type: 'number', default: 300, min: 50, max: 3000 },
      { key: 'max_depth',         label: 'Max Depth (0=None)', type: 'number', default: 0, min: 0, max: 50 },
      { key: 'min_samples_split', label: 'Min Samples Split', type: 'number', default: 5, min: 2, max: 50 },
      { key: 'min_samples_leaf',  label: 'Min Samples Leaf',  type: 'number', default: 2, min: 1, max: 20 },
    ],
    lightgbm: [
      { key: 'n_estimators',  label: 'Trees',         type: 'number', default: 500, min: 50, max: 5000 },
      { key: 'max_depth',     label: 'Max Depth (-1=No Limit)', type: 'number', default: -1, min: -1, max: 50 },
      { key: 'learning_rate', label: 'Learning Rate', type: 'number', default: 0.05, min: 0.001, max: 1, step: 0.01 },
      { key: 'num_leaves',    label: 'Num Leaves',    type: 'number', default: 31, min: 8, max: 256 },
    ],
    gradient_boosting: [
      { key: 'n_estimators',  label: 'Trees',         type: 'number', default: 300, min: 50, max: 3000 },
      { key: 'max_depth',     label: 'Max Depth',     type: 'number', default: 5, min: 2, max: 20 },
      { key: 'learning_rate', label: 'Learning Rate', type: 'number', default: 0.05, min: 0.001, max: 1, step: 0.01 },
      { key: 'subsample',     label: 'Subsample',     type: 'number', default: 0.8, min: 0.3, max: 1, step: 0.05 },
    ],
  };

  /* ── Router / Navigation ────────────────────────────────────────────────── */
  function nav(page) {
    state.page = page;

    // Deactivate all nav items + pages
    $$('.nav-item').forEach(n => n.classList.remove('active'));
    $$('.page').forEach(p => p.classList.remove('active'));

    // Activate selected
    const navItem = $(`.nav-item[data-nav="${page}"]`);
    const pageEl  = $(`#page-${page}`);
    if (navItem) navItem.classList.add('active');
    if (pageEl)  pageEl.classList.add('active');

    // Page-specific init
    if (page === 'dashboard')  renderDashboard();
    if (page === 'models')     renderModelsPage();
    if (page === 'test')       initTestPage();
  }

  /* ── Dashboard ──────────────────────────────────────────────────────────── */
  async function renderDashboard() {
    const models = await API.models();
    state.models = Array.isArray(models) ? models : [];

    // Stats
    const count = state.models.length;
    const bestModel = state.models.reduce((best, m) => {
      const r2 = m.metrics?.test_r2 ?? -999;
      return r2 > (best?.metrics?.test_r2 ?? -999) ? m : best;
    }, null);

    $('#stat-models').textContent = count;
    $('#stat-best-r2').textContent = bestModel?.metrics?.test_r2 != null ? fmtNum(bestModel.metrics.test_r2) : '—';
    if (bestModel?.metrics?.test_r2 != null) {
      const r2 = bestModel.metrics.test_r2;
      const cls = r2 >= 0.85 ? 'good' : r2 >= 0.6 ? 'warn' : 'bad';
      $('#stat-best-r2').className = 'metric-value ' + cls;
    }
    $('#stat-best-algo').textContent = bestModel?.algorithm || '—';

    // Recent models list
    const listEl = $('#dash-models-list');
    const countEl = $('#dash-model-count');
    if (countEl) countEl.textContent = count ? `${count} model${count !== 1 ? 's' : ''}` : '';

    if (!count) {
      html(listEl, `<div class="empty-state"><div class="es-icon">&#128202;</div>
        <p>No models yet. Train your first model to get started!</p></div>`);
      return;
    }

    const recent = state.models.slice(0, 6);
    html(listEl, `<div class="models-grid">${recent.map(modelCardHTML).join('')}</div>`);
  }

  function modelCardHTML(m) {
    const r2 = m.metrics?.test_r2;
    const mse = m.metrics?.test_mse;
    const r2class = r2 != null ? (r2 >= 0.85 ? 'good' : r2 >= 0.6 ? 'warn' : 'bad') : '';
    return `
      <div class="model-card" id="mc-${esc(m.name)}">
        <div class="mc-name">${esc(m.name)}</div>
        <div class="mc-meta">${esc(m.algorithm || 'Unknown')} &middot; ${esc(m.timestamp || '')} &middot; ${m.training_rows || '?'} rows</div>
        <div class="mc-metrics">
          <div>R&sup2; <span class="mc-val ${r2class}">${fmtNum(r2)}</span></div>
          <div>MSE <span class="mc-val">${fmtNum(mse, 6)}</span></div>
          <div>Target <span class="mc-val" style="font-size:.78rem">${esc(m.target_column || '—')}</span></div>
        </div>
        <div class="mc-actions">
          <button class="btn btn-ghost btn-sm" onclick="App.viewModel('${esc(m.name)}')">Details</button>
          <button class="btn btn-danger btn-sm" onclick="App.deleteModelConfirm('${esc(m.name)}')">Delete</button>
        </div>
      </div>`;
  }

  /* ── Models Page ────────────────────────────────────────────────────────── */
  async function renderModelsPage() {
    const models = await API.models();
    state.models = Array.isArray(models) ? models : [];
    const area = $('#models-list-area');

    if (!state.models.length) {
      html(area, `<div class="empty-state"><div class="es-icon">&#128202;</div>
        <p>No models trained yet. Head to <a href="#" onclick="App.nav('train');return false">Train Model</a> to get started.</p></div>`);
      return;
    }

    html(area, `<div class="models-grid">${state.models.map(modelCardHTML).join('')}</div>`);
  }

  /* ── View Model Detail (modal-like overlay in result area) ──────────────── */
  async function viewModel(name) {
    const config = await API.modelConfig(name);
    if (config?.error) { toast('Error loading model: ' + config.error, 'danger'); return; }

    nav('models');
    const area = $('#models-list-area');
    const m = config.metrics || {};
    const ei = config.extra_info || {};

    let extraHTML = '';
    if (ei.fold_metrics) {
      extraHTML += `<div class="mt-md"><h4 style="font-size:.88rem;font-weight:600;margin-bottom:8px;">K-Fold Results</h4>
        <div class="profile-table-wrap"><table class="profile-table"><thead><tr><th>Fold</th><th>Val Loss</th><th>Epochs</th></tr></thead><tbody>
        ${ei.fold_metrics.map(f => `<tr><td>${f.fold}</td><td>${fmtNum(f.val_loss, 6)}</td><td>${f.epochs_run}</td></tr>`).join('')}
        </tbody></table></div></div>`;
    }
    if (ei.cv_mse_mean != null) {
      extraHTML += `<div class="mt-md text-sm text-dim">Cross-validation MSE: ${fmtNum(ei.cv_mse_mean, 6)} &plusmn; ${fmtNum(ei.cv_mse_std, 6)}</div>`;
    }

    // Feature analysis images
    let analysisHTML = '';
    const hasImages = await _checkAnalysisImages(name);
    if (hasImages) {
      const ts = Date.now();
      analysisHTML = `<div class="card mt-md"><div class="card-header">Feature Importance</div><div class="card-body">
        <img src="/api/model-files/${encodeURIComponent(name)}/feature_importance.png?t=${ts}" class="analysis-img" alt="Feature Importance" onerror="this.style.display='none'">
        <img src="/api/model-files/${encodeURIComponent(name)}/cumulative_importance.png?t=${ts}" class="analysis-img" alt="Cumulative Importance" onerror="this.style.display='none'">
      </div></div>`;
    }

    html(area, `
      <div class="mb-md"><button class="btn btn-ghost btn-sm" onclick="App.renderModelsPage()">&larr; Back to Models</button></div>
      <div class="card">
        <div class="card-header flex flex-between">
          <span>${esc(config.model_name)}</span>
          <span class="text-muted text-sm">${esc(config.timestamp || '')}</span>
        </div>
        <div class="card-body">
          <div class="grid grid-3 mb-md" style="gap:12px">
            <div><span class="form-label">Algorithm</span><div>${esc(config.algorithm_display || config.algorithm)}</div></div>
            <div><span class="form-label">Target</span><div>${esc(config.target_column)}</div></div>
            <div><span class="form-label">Scaler</span><div>${esc(config.scaler_type)}</div></div>
            <div><span class="form-label">Training Rows</span><div class="mono">${config.training_rows}</div></div>
            <div><span class="form-label">Encoded Features</span><div class="mono">${config.training_features}</div></div>
            <div><span class="form-label">Features Selected</span><div class="mono">${(config.feature_columns || []).length}</div></div>
          </div>

          <h4 style="font-size:.88rem;font-weight:600;margin-bottom:10px;">Metrics</h4>
          <div class="metrics-row">
            <div class="metric"><div class="metric-label">Train R&sup2;</div><div class="metric-value">${fmtNum(m.train_r2)}</div></div>
            <div class="metric"><div class="metric-label">Test R&sup2;</div><div class="metric-value ${m.test_r2 >= 0.85 ? 'good' : m.test_r2 >= 0.6 ? 'warn' : 'bad'}">${fmtNum(m.test_r2)}</div></div>
            <div class="metric"><div class="metric-label">Test MSE</div><div class="metric-value">${fmtNum(m.test_mse, 6)}</div></div>
            <div class="metric"><div class="metric-label">Test MAE</div><div class="metric-value">${fmtNum(m.test_mae, 6)}</div></div>
          </div>

          ${extraHTML}

          <div class="mt-md">
            <h4 style="font-size:.88rem;font-weight:600;margin-bottom:8px;">Feature Columns</h4>
            <div style="display:flex;flex-wrap:wrap;gap:6px">
              ${(config.feature_columns || []).map(c => `<span class="tag tag-good">${esc(c)}</span>`).join('')}
            </div>
          </div>

          <div class="mt-md flex gap-sm">
            <button class="btn btn-primary btn-sm" id="btn-run-analysis-${esc(name)}" onclick="App.runFeatureAnalysis('${esc(name)}')">Run Feature Analysis</button>
            <button class="btn btn-danger btn-sm" onclick="App.deleteModelConfirm('${esc(name)}')">Delete Model</button>
          </div>
          <div id="analysis-status-${esc(name)}" class="mt-sm text-sm text-dim"></div>
        </div>
      </div>
      ${analysisHTML}
    `);
  }

  async function _checkAnalysisImages(name) {
    try {
      const res = await fetch(`/api/model-files/${encodeURIComponent(name)}/feature_importance.png`, { method: 'HEAD' });
      return res.ok;
    } catch { return false; }
  }

  /* ── Feature Analysis ───────────────────────────────────────────────────── */
  async function runFeatureAnalysis(name) {
    const btn = $(`#btn-run-analysis-${name}`);
    const statusEl = $(`#analysis-status-${name}`);
    if (btn) btn.disabled = true;
    if (statusEl) statusEl.textContent = 'Starting analysis...';

    const res = await API.startAnalysis(name);
    if (res.error) {
      if (statusEl) statusEl.textContent = 'Error: ' + res.error;
      if (btn) btn.disabled = false;
      return;
    }

    // Poll for completion
    const jobId = res.job_id;
    const poll = setInterval(async () => {
      const s = await API.analyzeStatus(jobId);
      if (statusEl) statusEl.textContent = s.status || 'Processing...';
      if (s.complete) {
        clearInterval(poll);
        if (btn) btn.disabled = false;
        if (s.status === 'complete') {
          if (statusEl) statusEl.textContent = 'Analysis complete! Refreshing...';
          setTimeout(() => viewModel(name), 500);
        }
      }
    }, 2500);
  }

  /* ── Delete Model ───────────────────────────────────────────────────────── */
  async function deleteModelConfirm(name) {
    if (!confirm(`Delete model "${name}"? This cannot be undone.`)) return;
    const res = await API.deleteModel(name);
    if (res.success) {
      toast(`Model "${name}" deleted`, 'success');
      if (state.page === 'models') renderModelsPage();
      else if (state.page === 'dashboard') renderDashboard();
    } else {
      toast('Failed to delete: ' + (res.error || 'Unknown error'), 'danger');
    }
  }

  /* ── Train: Wizard Step Navigation ──────────────────────────────────────── */
  function trainStep(step) {
    state.trainStep = step;

    // Update stepper UI
    $$('#train-stepper .step').forEach((el) => {
      const s = parseInt(el.dataset.step);
      el.classList.remove('active', 'done');
      if (s === step) el.classList.add('active');
      else if (s < step) el.classList.add('done');
    });
    $$('#train-stepper .step-line').forEach((el, i) => {
      el.classList.toggle('done', (i + 1) < step);
    });

    // Toggle step panels
    $$('.train-step').forEach(s => hide(s));
    show($(`#train-step-${step}`));

    // Step-specific init
    if (step === 3) renderFeatureGrid();
  }

  /* ── Train Step 1: Upload ───────────────────────────────────────────────── */
  function initTrainUpload() {
    const dropzone = $('#train-dropzone');
    const fileInput = $('#train-file-input');

    if (!dropzone || !fileInput) return;

    dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('drag-over'); });
    dropzone.addEventListener('dragleave', () => { dropzone.classList.remove('drag-over'); });
    dropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropzone.classList.remove('drag-over');
      if (e.dataTransfer.files.length) handleTrainFile(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener('change', () => {
      if (fileInput.files.length) handleTrainFile(fileInput.files[0]);
    });
  }

  async function handleTrainFile(file) {
    const dropzone = $('#train-dropzone');
    html(dropzone, `<div class="spinner"></div><p class="mt-sm text-dim">Uploading & profiling ${esc(file.name)}...</p>`);

    const res = await API.upload(file);
    if (res.error) {
      html(dropzone, `<div class="dz-icon">&#128193;</div>
        <div class="dz-title" style="color:var(--c-danger)">Upload failed: ${esc(res.error)}</div>
        <div class="dz-sub">Try again</div>
        <input type="file" id="train-file-input" accept=".csv,.xlsx,.xls">`);
      initTrainUpload();
      return;
    }

    state.uploadData = res;
    await loadAlgorithms();
    renderProfileStep();
    trainStep(2);
  }

  /* ── Train Step 2: Profile & Configure ──────────────────────────────────── */
  async function loadAlgorithms() {
    if (Object.keys(state.algorithms).length) return;
    const algos = await API.algorithms();
    if (!algos.error) state.algorithms = algos;
  }

  function renderProfileStep() {
    const d = state.uploadData;
    if (!d) return;

    // Profile info
    $('#profile-info').textContent = `${d.summary.row_count} rows × ${d.summary.column_count} columns — ${d.filename}`;

    // Profile table
    const cols = d.summary.columns;
    html($('#profile-table-container'), `
      <table class="profile-table">
        <thead><tr><th>Column</th><th>Type</th><th>Missing</th><th>Unique</th><th>Min</th><th>Max</th><th>Mean</th></tr></thead>
        <tbody>${cols.map(c => `<tr>
          <td style="font-weight:500">${esc(c.name)}</td>
          <td><span class="badge ${c.is_numeric ? 'badge-num' : 'badge-cat'}">${c.is_numeric ? 'NUM' : 'CAT'}</span></td>
          <td>${c.missing} <span class="text-muted">(${c.missing_pct}%)</span></td>
          <td>${c.unique}</td>
          <td class="mono">${c.min != null ? fmtNum(c.min, 2) : '—'}</td>
          <td class="mono">${c.max != null ? fmtNum(c.max, 2) : '—'}</td>
          <td class="mono">${c.mean != null ? fmtNum(c.mean, 2) : '—'}</td>
        </tr>`).join('')}</tbody>
      </table>`);

    // Algorithm select
    const algoSel = $('#cfg-algorithm');
    html(algoSel, Object.entries(state.algorithms).map(([k, v]) =>
      `<option value="${esc(k)}">${esc(v)}</option>`
    ).join(''));
    algoSel.removeEventListener('change', onAlgorithmChange);
    algoSel.addEventListener('change', onAlgorithmChange);

    // Target select (numeric columns only)
    const targetSel = $('#cfg-target');
    const numCols = d.numerical_columns;
    html(targetSel, numCols.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join(''));

    // Model name suggestion
    const nameInput = $('#cfg-model-name');
    if (!nameInput.value) {
      const base = (d.filename || 'model').replace(/\.[^.]+$/, '').replace(/[^a-zA-Z0-9_-]/g, '_');
      nameInput.value = base + '_v1';
    }

    // Render hyperparameters for default algo
    renderHyperparams();
  }

  function onAlgorithmChange() {
    renderHyperparams();
  }

  function renderHyperparams() {
    const algo = $('#cfg-algorithm').value;
    const defs = HP_DEFS[algo] || [];
    const container = $('#hp-fields');
    if (!container) return;

    if (!defs.length) {
      html(container, '<p class="text-dim text-sm">No configurable hyperparameters for this algorithm.</p>');
      return;
    }

    html(container, defs.map(hp => {
      if (hp.type === 'checkbox') {
        return `<div class="form-group" style="grid-column:span 2">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" data-hp="${esc(hp.key)}" ${hp.default ? 'checked' : ''} style="width:18px;height:18px;accent-color:var(--c-primary)">
            <span class="form-label" style="margin:0;text-transform:none">${esc(hp.label)}</span>
          </label>
        </div>`;
      }
      return `<div class="form-group">
        <label class="form-label">${esc(hp.label)}</label>
        <input type="number" data-hp="${esc(hp.key)}" class="form-input"
               value="${hp.default}" min="${hp.min}" max="${hp.max}" ${hp.step ? `step="${hp.step}"` : ''}>
      </div>`;
    }).join(''));
  }

  function collectHyperparams() {
    const params = {};
    $$('#hp-fields [data-hp]').forEach(el => {
      const key = el.dataset.hp;
      if (el.type === 'checkbox') {
        params[key] = el.checked;
      } else {
        const v = parseFloat(el.value);
        if (!isNaN(v)) params[key] = v;
      }
    });
    // random_forest: convert max_depth=0 to null (means None in Python)
    if (params.max_depth === 0 && $('#cfg-algorithm').value === 'random_forest') {
      params.max_depth = null;
    }
    return params;
  }

  /* ── Train Step 3: Feature Selection ────────────────────────────────────── */
  function renderFeatureGrid() {
    const d = state.uploadData;
    if (!d) return;

    const target = $('#cfg-target').value;
    const cols = d.columns.filter(c => c !== target);
    const numSet = new Set(d.numerical_columns);
    const grid = $('#feat-grid');

    // Initialize selection (all selected by default)
    if (!Object.keys(state.featureSelection).length || state._lastTarget !== target) {
      state.featureSelection = {};
      cols.forEach(c => { state.featureSelection[c] = true; });
      state._lastTarget = target;
    }

    html(grid, cols.map(c => {
      const isNum = numSet.has(c);
      const sel = state.featureSelection[c];
      return `<div class="col-chip ${sel ? 'selected' : ''}" data-col="${esc(c)}" data-checked="${sel ? '1' : '0'}">
        <span>${esc(c)}</span>
        <span class="badge ${isNum ? 'badge-num' : 'badge-cat'}">${isNum ? 'NUM' : 'CAT'}</span>
      </div>`;
    }).join(''));

    // Update counts
    updateFeatureCounts();

    // Bind search
    const search = $('#feat-search');
    search.value = '';
    search.oninput = () => {
      const q = search.value.toLowerCase();
      $$('#feat-grid .col-chip').forEach(chip => {
        chip.style.display = chip.dataset.col.toLowerCase().includes(q) ? '' : 'none';
      });
    };

    // Bind chip clicks
    $$('#feat-grid .col-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const col = chip.dataset.col;
        const isNowSelected = !state.featureSelection[col];
        state.featureSelection[col] = isNowSelected;
        chip.dataset.checked = isNowSelected ? '1' : '0';
        chip.classList.toggle('selected', isNowSelected);
        updateFeatureCounts();
      });
    });

    $('#feat-total').textContent = cols.length;
  }

  function updateFeatureCounts() {
    const count = Object.values(state.featureSelection).filter(Boolean).length;
    $('#feat-count').textContent = count;
  }

  function featSelectAll() {
    Object.keys(state.featureSelection).forEach(k => { state.featureSelection[k] = true; });
    $$('#feat-grid .col-chip').forEach(chip => {
      chip.dataset.checked = '1';
      chip.classList.add('selected');
    });
    updateFeatureCounts();
  }

  function featDeselectAll() {
    Object.keys(state.featureSelection).forEach(k => { state.featureSelection[k] = false; });
    $$('#feat-grid .col-chip').forEach(chip => {
      chip.dataset.checked = '0';
      chip.classList.remove('selected');
    });
    updateFeatureCounts();
  }

  /* ── Train Step 4: Start Training ───────────────────────────────────────── */
  async function startTraining() {
    const features = Object.entries(state.featureSelection).filter(([, v]) => v).map(([k]) => k);
    if (!features.length) { toast('Select at least one feature column.', 'danger'); return; }

    const modelName = $('#cfg-model-name').value.trim();
    if (!modelName) { toast('Please enter a model name.', 'danger'); return; }

    const payload = {
      filepath:        state.uploadData.filepath,
      model_name:      modelName,
      algorithm:       $('#cfg-algorithm').value,
      scaler_type:     $('#cfg-scaler').value,
      target_column:   $('#cfg-target').value,
      feature_columns: features,
      hyperparams:     collectHyperparams(),
    };

    trainStep(4);
    $('#train-status-msg').textContent = `Training "${modelName}" with ${features.length} features using ${state.algorithms[payload.algorithm] || payload.algorithm}... This may take a few minutes.`;

    const res = await API.train(payload);
    renderTrainResults(res);
    trainStep(5);
  }

  /* ── Train Step 5: Results ──────────────────────────────────────────────── */
  function renderTrainResults(res) {
    const area = $('#train-result-area');

    if (res.error || !res.success) {
      const errors = res.errors ? res.errors.join('<br>') : (res.error || 'Training failed');
      html(area, `<div class="alert alert-danger">${errors}</div>`);
      return;
    }

    const m = res.metrics;
    const r2class = m.test_r2 >= 0.85 ? 'good' : m.test_r2 >= 0.6 ? 'warn' : 'bad';
    const ei = res.extra_info || {};

    let extraHTML = '';
    if (ei.fold_metrics) {
      extraHTML += `<div class="card mt-md"><div class="card-header">K-Fold Cross Validation</div><div class="card-body">
        <div class="profile-table-wrap"><table class="profile-table"><thead><tr><th>Fold</th><th>Val Loss</th><th>Epochs Run</th></tr></thead><tbody>
        ${ei.fold_metrics.map(f => `<tr><td>${f.fold}</td><td class="mono">${fmtNum(f.val_loss, 6)}</td><td class="mono">${f.epochs_run}</td></tr>`).join('')}
        </tbody></table></div></div></div>`;
    }
    if (ei.cv_mse_mean != null) {
      extraHTML += `<p class="mt-sm text-sm text-dim">CV MSE: ${fmtNum(ei.cv_mse_mean, 6)} &plusmn; ${fmtNum(ei.cv_mse_std, 6)}</p>`;
    }

    html(area, `
      <div class="alert alert-success">Model "<strong>${esc(res.model_name)}</strong>" trained successfully in ${res.elapsed_seconds}s.</div>
      <div class="metrics-row">
        <div class="metric"><div class="metric-label">Train R&sup2;</div><div class="metric-value">${fmtNum(m.train_r2)}</div></div>
        <div class="metric"><div class="metric-label">Test R&sup2;</div><div class="metric-value ${r2class}">${fmtNum(m.test_r2)}</div></div>
        <div class="metric"><div class="metric-label">Train MSE</div><div class="metric-value">${fmtNum(m.train_mse, 6)}</div></div>
        <div class="metric"><div class="metric-label">Test MSE</div><div class="metric-value">${fmtNum(m.test_mse, 6)}</div></div>
        <div class="metric"><div class="metric-label">Test MAE</div><div class="metric-value">${fmtNum(m.test_mae, 6)}</div></div>
      </div>
      ${extraHTML}
    `);
  }

  /* ── Reset Train Wizard ─────────────────────────────────────────────────── */
  function resetTrain() {
    state.uploadData = null;
    state.featureSelection = {};
    state.trainStep = 1;

    // Reset dropzone
    html($('#train-dropzone'), `<input type="file" id="train-file-input" accept=".csv,.xlsx,.xls">
      <div class="dz-icon">&#128193;</div>
      <div class="dz-title">Drag &amp; drop your training data here</div>
      <div class="dz-sub">CSV, XLSX, or XLS &mdash; up to 500 MB</div>`);
    initTrainUpload();

    // Reset form
    const nameInput = $('#cfg-model-name');
    if (nameInput) nameInput.value = '';

    trainStep(1);
  }

  /* ── Test Page ──────────────────────────────────────────────────────────── */
  async function initTestPage() {
    const models = await API.models();
    state.models = Array.isArray(models) ? models : [];

    const sel = $('#test-model-select');
    if (!state.models.length) {
      html(sel, '<option value="">No models available</option>');
      $('#btn-run-test').disabled = true;
      return;
    }

    html(sel, state.models.map(m =>
      `<option value="${esc(m.name)}">${esc(m.name)} — ${esc(m.algorithm || 'Unknown')}</option>`
    ).join(''));

    initTestUpload();
    checkTestReady();

    // Reset result area
    hide($('#test-result-area'));
    html($('#test-result-area'), '');
    hide($('#test-loading'));
    show($('#test-form-area'));
  }

  function initTestUpload() {
    const dropzone = $('#test-dropzone');
    const fileInput = $('#test-file-input');
    if (!dropzone || !fileInput) return;

    dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('drag-over'); });
    dropzone.addEventListener('dragleave', () => { dropzone.classList.remove('drag-over'); });
    dropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropzone.classList.remove('drag-over');
      if (e.dataTransfer.files.length) {
        state.testFile = e.dataTransfer.files[0];
        showTestFileName();
        checkTestReady();
      }
    });
    fileInput.addEventListener('change', () => {
      if (fileInput.files.length) {
        state.testFile = fileInput.files[0];
        showTestFileName();
        checkTestReady();
      }
    });
  }

  function showTestFileName() {
    if (!state.testFile) return;
    const dz = $('#test-dropzone');
    const title = $('.dz-title', dz);
    if (title) title.textContent = `Selected: ${state.testFile.name}`;
    dz.classList.add('drag-over');
  }

  function checkTestReady() {
    const btn = $('#btn-run-test');
    const hasModel = $('#test-model-select').value;
    const hasFile = state.testFile;
    btn.disabled = !(hasModel && hasFile);
  }

  async function runTest() {
    const modelName = $('#test-model-select').value;
    if (!modelName || !state.testFile) return;

    hide($('#test-form-area'));
    show($('#test-loading'));
    hide($('#test-result-area'));

    const res = await API.test(modelName, state.testFile);

    hide($('#test-loading'));
    show($('#test-result-area'));
    renderTestResults(res);
  }

  function renderTestResults(res) {
    const area = $('#test-result-area');

    if (res.error || !res.success) {
      html(area, `<div class="alert alert-danger">${esc(res.error || 'Test failed')}</div>
        <button class="btn btn-ghost mt-md" onclick="App.resetTest()">Try Again</button>`);
      return;
    }

    const m = res.metrics;
    const s = res.summary || {};
    const r2class = m.r2 >= 0.85 ? 'good' : m.r2 >= 0.6 ? 'warn' : 'bad';

    // Summary chips
    const summaryHTML = ['Excellent', 'Good', 'Fair', 'Poor'].map(cat => {
      const count = s[cat] || 0;
      const cls = cat.toLowerCase();
      return `<div class="summary-chip"><span class="dot dot-${cls}"></span> ${cat}: <strong>${count}</strong></div>`;
    }).join('');

    // Results table (limit to first 100 rows for performance)
    const rows = res.results || [];
    const displayRows = rows.slice(0, 100);
    const allCols = res.columns || (displayRows.length ? Object.keys(displayRows[0]) : []);
    // Priority columns to show
    const showCols = ['Accuracy', 'Predicted', 'Actual', 'Percentage', 'Confidence'];
    const tableCols = showCols.filter(c => allCols.includes(c));

    let tableHTML = '';
    if (displayRows.length) {
      tableHTML = `<div class="results-wrap"><table class="results-table">
        <thead><tr>${tableCols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
        <tbody>${displayRows.map(row => `<tr>${tableCols.map(c => {
          let val = row[c];
          if (c === 'Accuracy') {
            const cls = (val || '').toLowerCase();
            return `<td><span class="tag tag-${cls}">${esc(val)}</span></td>`;
          }
          if (c === 'Confidence') {
            const confClass = val === 'High' ? 'tag-excellent' : val === 'Medium' ? 'tag-fair' : 'tag-poor';
            return `<td><span class="tag ${confClass}">${esc(val)}</span></td>`;
          }
          if (typeof val === 'number') val = fmtNum(val, c === 'Percentage' ? 2 : 2);
          return `<td>${esc(val)}</td>`;
        }).join('')}</tr>`).join('')}</tbody>
      </table></div>`;
      if (rows.length > 100) {
        tableHTML += `<p class="text-sm text-muted mt-sm">Showing 100 of ${rows.length} rows</p>`;
      }
    }

    html(area, `
      <div class="alert alert-success">Test complete — ${esc(res.algorithm)} model "<strong>${esc(res.model_name)}</strong>"</div>
      <div class="metrics-row">
        <div class="metric"><div class="metric-label">R&sup2;</div><div class="metric-value ${r2class}">${fmtNum(m.r2)}</div></div>
        <div class="metric"><div class="metric-label">MSE</div><div class="metric-value">${fmtNum(m.mse, 6)}</div></div>
        <div class="metric"><div class="metric-label">MAE</div><div class="metric-value">${fmtNum(m.mae, 6)}</div></div>
        <div class="metric"><div class="metric-label">Total Delta</div><div class="metric-value">${fmtNum(m.total_delta, 2)}</div></div>
        <div class="metric"><div class="metric-label">CI Width</div><div class="metric-value">${fmtNum(res.overall_confidence, 4)}</div></div>
      </div>
      <div class="card">
        <div class="card-header flex flex-between">
          <span>Accuracy Summary</span>
          <span class="text-muted text-sm">${rows.length} predictions</span>
        </div>
        <div class="card-body">
          <div class="summary-row">${summaryHTML}</div>
        </div>
      </div>
      <div class="card">
        <div class="card-header">Predictions</div>
        <div class="card-body">${tableHTML || '<p class="text-dim">No predictions to display.</p>'}</div>
      </div>
      <div class="mt-md flex gap-sm">
        <button class="btn btn-primary" onclick="App.resetTest()">Test Another</button>
        <button class="btn btn-ghost" onclick="App.nav('dashboard')">Dashboard</button>
      </div>
    `);
  }

  function resetTest() {
    state.testFile = null;

    // Reset dropzone UI
    const dz = $('#test-dropzone');
    if (dz) {
      dz.classList.remove('drag-over');
      const title = $('.dz-title', dz);
      if (title) title.textContent = 'Drop test CSV / Excel here';
    }

    // Reset file input
    const fi = $('#test-file-input');
    if (fi) fi.value = '';

    hide($('#test-result-area'));
    hide($('#test-loading'));
    show($('#test-form-area'));
    checkTestReady();
  }

  /* ── Toast Notifications ────────────────────────────────────────────────── */
  function toast(msg, type = 'success') {
    const existing = $('#toast-container');
    const container = existing || document.createElement('div');
    if (!existing) {
      container.id = 'toast-container';
      container.style.cssText = 'position:fixed;top:20px;right:20px;z-index:10000;display:flex;flex-direction:column;gap:8px;';
      document.body.appendChild(container);
    }

    const t = document.createElement('div');
    t.className = `alert alert-${type}`;
    t.style.cssText = 'min-width:280px;max-width:420px;box-shadow:0 8px 32px rgba(0,0,0,.4);animation:fadeUp .3s ease;';
    t.textContent = msg;
    container.appendChild(t);

    setTimeout(() => {
      t.style.opacity = '0';
      t.style.transition = 'opacity .3s';
      setTimeout(() => t.remove(), 300);
    }, 4000);
  }

  /* ── Init ───────────────────────────────────────────────────────────────── */
  function init() {
    // Sidebar navigation
    $$('.nav-item[data-nav]').forEach(item => {
      item.addEventListener('click', () => nav(item.dataset.nav));
    });

    // Initialize train upload dropzone
    initTrainUpload();

    // Load dashboard on start
    nav('dashboard');
  }

  document.addEventListener('DOMContentLoaded', init);

  /* ── Public API ─────────────────────────────────────────────────────────── */
  window.App = {
    nav,
    trainStep,
    startTraining,
    resetTrain,
    runTest,
    resetTest,
    featSelectAll,
    featDeselectAll,
    viewModel,
    deleteModelConfirm,
    runFeatureAnalysis,
    renderModelsPage,
  };

})();
