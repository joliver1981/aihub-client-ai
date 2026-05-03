/* model_tester frontend
 * Single-page app — vanilla JS, talks to /api/* on this Flask server.
 */

(() => {
  let SETTINGS = null;
  let EVALS = [];
  let CURRENT_RESULT = null;

  // ---------- Tab switching ----------
  document.querySelectorAll('nav .tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('nav .tab').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
      if (btn.dataset.tab === 'results') loadResults();
    });
  });

  // ---------- API helpers ----------
  async function apiGet(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    return r.json();
  }
  async function apiSend(method, path, body) {
    const r = await fetch(path, {
      method,
      headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    return r.json();
  }

  // ---------- Initial load ----------
  async function init() {
    SETTINGS = await apiGet('/api/settings');
    EVALS = await apiGet('/api/evals');
    populateModelPickers();
    populateEvalPicker();
    populateEvalsTable();
    populateModelsTable();
    bindRunHandlers();
    bindSettingsHandlers();
    bindEvalsHandlers();
    bindResultsHandlers();
  }

  function populateModelPickers() {
    const sel = document.getElementById('active-model');
    const settingSel = document.getElementById('active-model-setting');
    const judgeSel = document.getElementById('judge-model-setting');
    [sel, settingSel, judgeSel].forEach(s => s.innerHTML = '');
    Object.entries(SETTINGS.models).forEach(([id, cfg]) => {
      [sel, settingSel, judgeSel].forEach(s => {
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = cfg.label || id;
        s.appendChild(opt);
      });
    });
    sel.value = SETTINGS.active_model_id;
    settingSel.value = SETTINGS.active_model_id;
    judgeSel.value = SETTINGS.judge_model_id;
    updateModelInfo();
    sel.addEventListener('change', updateModelInfo);
  }

  function updateModelInfo() {
    const sel = document.getElementById('active-model');
    const cfg = SETTINGS.models[sel.value] || {};
    const parts = [cfg.provider];
    if (cfg.model) parts.push(cfg.model);
    if (cfg.deployment) parts.push('deployment=' + cfg.deployment);
    document.getElementById('active-model-info').textContent = '(' + parts.filter(Boolean).join(' / ') + ')';
  }

  function populateEvalPicker() {
    const sel = document.getElementById('eval-picker');
    sel.innerHTML = '<option value="">— ad-hoc (free chat) —</option>';
    EVALS.forEach(e => {
      const opt = document.createElement('option');
      opt.value = e.id;
      opt.textContent = `${e.id} — ${e.name}`;
      sel.appendChild(opt);
    });
  }

  // ---------- Run tab ----------
  function bindRunHandlers() {
    document.getElementById('load-eval-btn').addEventListener('click', loadSelectedEval);
    document.getElementById('run-btn').addEventListener('click', runChat);
    document.getElementById('run-all-btn').addEventListener('click', runAllOOB);
    ['system-prompt', 'user-prompt'].forEach(id => {
      const el = document.getElementById(id);
      el.addEventListener('input', () => {
        const t = el.value;
        document.getElementById(id === 'system-prompt' ? 'system-len' : 'user-len').textContent =
          `${t.length} chars`;
      });
    });
  }

  async function loadSelectedEval() {
    const id = document.getElementById('eval-picker').value;
    if (!id) {
      document.getElementById('system-prompt').value = '';
      document.getElementById('user-prompt').value = '';
      document.getElementById('eval-loaded-info').textContent = '';
      updateLengths();
      return;
    }
    const ev = await apiGet('/api/evals/' + id);
    document.getElementById('system-prompt').value = ev._resolved_system_prompt || '';
    document.getElementById('user-prompt').value = ev.user_prompt || '';
    document.getElementById('eval-loaded-info').textContent =
      `loaded ${id}: ${ev.name}`;
    updateLengths();
  }

  function updateLengths() {
    document.getElementById('system-len').textContent =
      `${document.getElementById('system-prompt').value.length} chars`;
    document.getElementById('user-len').textContent =
      `${document.getElementById('user-prompt').value.length} chars`;
  }

  async function runChat() {
    const status = document.getElementById('run-status');
    const out = document.getElementById('output');
    const verdict = document.getElementById('verdict');
    out.textContent = ''; verdict.innerHTML = '';
    status.textContent = 'running...';
    document.getElementById('run-btn').disabled = true;

    const evalId = document.getElementById('eval-picker').value || null;
    const body = {
      eval_id: evalId,
      model_id: document.getElementById('active-model').value,
      system_prompt: document.getElementById('system-prompt').value,
      user_prompt: document.getElementById('user-prompt').value,
      temperature: parseFloat(document.getElementById('temperature').value),
      max_tokens: parseInt(document.getElementById('max-tokens').value, 10),
      run_judge: document.getElementById('run-judge').checked,
    };
    try {
      const r = await apiSend('POST', '/api/run', body);
      CURRENT_RESULT = r;
      out.textContent = r.content || ('(error: ' + r.error + ')');
      renderVerdict(r);
      status.textContent = `done in ${r.elapsed_ms}ms`;
      // optionally also LLM judge
      if (document.getElementById('run-llm-judge').checked && r.ok) {
        status.textContent += ' — running LLM judge...';
        const j = await apiSend('POST', '/api/judge', {
          result_id: r.result_id,
          use_llm_judge: true,
        });
        renderVerdict(r, j);
        status.textContent = `done with LLM judge`;
      }
    } catch (e) {
      status.textContent = 'error: ' + e.message;
    } finally {
      document.getElementById('run-btn').disabled = false;
    }
  }

  function renderVerdict(result, judgeResp) {
    const v = document.getElementById('verdict');
    let html = '';
    const j = result.judge;
    if (j) {
      const cls = j.passed ? 'pass' : 'fail';
      html += `<h4>Structural: <span class="${cls}">${j.score}/${j.max_score} ${j.passed ? 'PASS' : 'FAIL'}</span></h4>`;
      if (j.details && j.details.checks) {
        j.details.checks.forEach(c => {
          html += `<div class="check"><span class="${c.passed ? 'pass' : 'fail'}">${c.passed ? '✓' : '✗'}</span> ${c.name} <span class="muted">— ${c.detail}</span></div>`;
        });
      }
      if (j.details) {
        html += `<div class="muted">add_node count: ${j.details.add_node_count} · total cmds: ${j.details.total_commands}</div>`;
      }
    }
    if (judgeResp && judgeResp.llm_judge) {
      const lj = judgeResp.llm_judge;
      html += `<h4>LLM judge</h4>`;
      if (lj.verdict) {
        html += `<pre>${JSON.stringify(lj.verdict, null, 2)}</pre>`;
      } else if (lj.raw) {
        html += `<pre>${lj.raw}</pre>`;
      } else if (lj.error) {
        html += `<div class="fail">${lj.error}</div>`;
      }
    }
    if (result.usage) {
      html += `<div class="muted">tokens: in=${result.usage.prompt_tokens || '?'} out=${result.usage.completion_tokens || '?'}</div>`;
    }
    v.innerHTML = html || '<span class="muted">no judge data</span>';
  }

  async function runAllOOB() {
    const status = document.getElementById('run-status');
    const oob = EVALS.filter(e => (e.tags || []).includes('oob'));
    if (oob.length === 0) {
      status.textContent = 'no OOB evals found';
      return;
    }
    document.getElementById('run-btn').disabled = true;
    document.getElementById('run-all-btn').disabled = true;
    let i = 0;
    const summary = [];
    for (const e of oob) {
      i++;
      status.textContent = `running ${i}/${oob.length}: ${e.id}...`;
      try {
        const r = await apiSend('POST', '/api/run', {
          eval_id: e.id,
          model_id: document.getElementById('active-model').value,
          temperature: parseFloat(document.getElementById('temperature').value),
          max_tokens: parseInt(document.getElementById('max-tokens').value, 10),
          run_judge: true,
        });
        const j = r.judge || {};
        summary.push(`${e.id}: ${j.passed ? 'PASS' : 'FAIL'} ${j.score || 0}/${j.max_score || 0} (${r.elapsed_ms}ms)`);
      } catch (err) {
        summary.push(`${e.id}: ERROR ${err.message}`);
      }
      document.getElementById('output').textContent = summary.join('\n');
    }
    status.textContent = `done. ${summary.filter(s => s.includes('PASS')).length}/${oob.length} passed`;
    document.getElementById('run-btn').disabled = false;
    document.getElementById('run-all-btn').disabled = false;
  }

  // ---------- Evals tab ----------
  function bindEvalsHandlers() {
    document.getElementById('new-eval-btn').addEventListener('click', () => openEvalEditor(null));
    document.getElementById('ee-save').addEventListener('click', saveEvalFromEditor);
    document.getElementById('ee-cancel').addEventListener('click', () => document.getElementById('eval-editor').classList.add('hidden'));
    document.getElementById('eval-search').addEventListener('input', populateEvalsTable);
  }

  function populateEvalsTable() {
    const tb = document.getElementById('evals-table-body');
    const filter = (document.getElementById('eval-search').value || '').toLowerCase();
    tb.innerHTML = '';
    EVALS.filter(e => {
      if (!filter) return true;
      return (e.name + ' ' + (e.tags || []).join(' ') + ' ' + e.id).toLowerCase().includes(filter);
    }).forEach(e => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${e.id}</td>
        <td>${e.name}</td>
        <td>${e.category || ''}</td>
        <td>${(e.tags || []).join(', ')}</td>
        <td class="actions">
          <button data-act="edit">Edit</button>
          <button data-act="run">Run</button>
          <button data-act="delete" class="danger">×</button>
        </td>`;
      tr.querySelector('[data-act="edit"]').addEventListener('click', () => openEvalEditor(e.id));
      tr.querySelector('[data-act="run"]').addEventListener('click', async () => {
        document.querySelector('nav .tab[data-tab="run"]').click();
        document.getElementById('eval-picker').value = e.id;
        await loadSelectedEval();
        runChat();
      });
      tr.querySelector('[data-act="delete"]').addEventListener('click', async () => {
        if (!confirm(`Delete eval ${e.id}?`)) return;
        await apiSend('DELETE', '/api/evals/' + e.id);
        EVALS = await apiGet('/api/evals');
        populateEvalPicker(); populateEvalsTable();
      });
      tb.appendChild(tr);
    });
  }

  async function openEvalEditor(id) {
    const editor = document.getElementById('eval-editor');
    if (id) {
      const ev = await apiGet('/api/evals/' + id);
      document.getElementById('ee-id').value = ev.id || '';
      document.getElementById('ee-name').value = ev.name || '';
      document.getElementById('ee-category').value = ev.category || '';
      document.getElementById('ee-description').value = ev.description || '';
      document.getElementById('ee-tags').value = (ev.tags || []).join(', ');
      document.getElementById('ee-spref').value = ev.system_prompt_ref || '';
      document.getElementById('ee-system').value = ev.system_prompt || '';
      document.getElementById('ee-user').value = ev.user_prompt || '';
      document.getElementById('ee-expected').value = JSON.stringify(ev.expected || {}, null, 2);
      editor.dataset.editingId = id;
    } else {
      ['ee-id', 'ee-name', 'ee-category', 'ee-description', 'ee-tags', 'ee-spref', 'ee-system', 'ee-user'].forEach(i => document.getElementById(i).value = '');
      document.getElementById('ee-expected').value = '{\n  "min_add_nodes": 1\n}';
      editor.dataset.editingId = '';
    }
    editor.classList.remove('hidden');
  }

  async function saveEvalFromEditor() {
    const editingId = document.getElementById('eval-editor').dataset.editingId;
    let expected;
    try {
      expected = JSON.parse(document.getElementById('ee-expected').value || '{}');
    } catch (e) { alert('expected must be valid JSON: ' + e.message); return; }
    const obj = {
      id: document.getElementById('ee-id').value || ('eval-' + Date.now()),
      name: document.getElementById('ee-name').value,
      category: document.getElementById('ee-category').value,
      description: document.getElementById('ee-description').value,
      tags: (document.getElementById('ee-tags').value || '').split(',').map(s => s.trim()).filter(Boolean),
      system_prompt_ref: document.getElementById('ee-spref').value || null,
      system_prompt: document.getElementById('ee-system').value || null,
      user_prompt: document.getElementById('ee-user').value,
      expected: expected,
    };
    if (editingId) {
      await apiSend('PUT', '/api/evals/' + editingId, obj);
    } else {
      await apiSend('POST', '/api/evals', obj);
    }
    document.getElementById('eval-editor').classList.add('hidden');
    EVALS = await apiGet('/api/evals');
    populateEvalPicker(); populateEvalsTable();
  }

  // ---------- Settings tab ----------
  function bindSettingsHandlers() {
    document.getElementById('settings-save').addEventListener('click', saveSettings);
    document.getElementById('add-model-btn').addEventListener('click', addModelRow);
  }

  function populateModelsTable() {
    const tb = document.getElementById('models-table-body');
    tb.innerHTML = '';
    Object.entries(SETTINGS.models).forEach(([id, cfg]) => addModelRowToTable(id, cfg));
  }

  function addModelRowToTable(id, cfg) {
    const tb = document.getElementById('models-table-body');
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input data-field="id" value="${escapeHtml(id)}"></td>
      <td><input data-field="label" value="${escapeHtml(cfg.label || '')}"></td>
      <td>
        <select data-field="provider">
          <option value="openai">openai</option>
          <option value="azure">azure</option>
          <option value="anthropic">anthropic</option>
          <option value="lmstudio">lmstudio</option>
        </select>
      </td>
      <td><input data-field="model" value="${escapeHtml(cfg.model || cfg.deployment || '')}" placeholder="model id or deployment"></td>
      <td><input data-field="endpoint" value="${escapeHtml(cfg.endpoint || '')}" placeholder="endpoint (azure/lmstudio)"></td>
      <td><input data-field="api_key_override" value="${escapeHtml(cfg.api_key_override || '')}" placeholder="(uses main app default)"></td>
      <td><button class="danger" data-act="del">×</button></td>
    `;
    tr.querySelector('[data-field="provider"]').value = cfg.provider || 'openai';
    tr.querySelector('[data-act="del"]').addEventListener('click', () => tr.remove());
    tb.appendChild(tr);
  }

  function addModelRow() {
    addModelRowToTable('new-model-' + Date.now().toString(36), {
      label: 'New model', provider: 'openai', model: '', api_key_override: '',
    });
  }

  async function saveSettings() {
    const models = {};
    document.querySelectorAll('#models-table-body tr').forEach(tr => {
      const get = f => tr.querySelector(`[data-field="${f}"]`).value;
      const id = get('id');
      if (!id) return;
      const provider = get('provider');
      const cfg = { label: get('label'), provider };
      const modelStr = get('model');
      const endpoint = get('endpoint');
      const keyOverride = get('api_key_override');
      if (provider === 'azure') {
        cfg.deployment = modelStr;
        cfg.endpoint = endpoint;
        cfg.api_version = SETTINGS.models[id]?.api_version || '2024-08-01-preview';
      } else {
        cfg.model = modelStr;
      }
      if (endpoint && provider === 'lmstudio') cfg.endpoint = endpoint;
      cfg.api_key_override = keyOverride || null;
      models[id] = cfg;
    });
    const payload = {
      active_model_id: document.getElementById('active-model-setting').value,
      judge_model_id: document.getElementById('judge-model-setting').value,
      models: models,
    };
    await apiSend('PUT', '/api/settings', payload);
    SETTINGS = await apiGet('/api/settings');
    populateModelPickers();
    populateModelsTable();
    alert('Settings saved.');
  }

  // ---------- Results tab ----------
  function bindResultsHandlers() {
    document.getElementById('results-refresh').addEventListener('click', loadResults);
    document.getElementById('rd-close').addEventListener('click', () => document.getElementById('result-detail').classList.add('hidden'));
    document.getElementById('rd-rejudge').addEventListener('click', rejudgeCurrentResult);
  }

  async function loadResults() {
    const list = await apiGet('/api/results');
    const tb = document.getElementById('results-table-body');
    tb.innerHTML = '';
    list.forEach(r => {
      const tr = document.createElement('tr');
      const judgeStr = r.judge_passed === undefined ? '' :
        (r.judge_passed ? `<span style="color:#56d364">PASS ${r.judge_score}/${r.judge_max_score}</span>` :
         `<span style="color:#f85149">FAIL ${r.judge_score}/${r.judge_max_score}</span>`);
      tr.innerHTML = `
        <td>${r.saved_at || ''}</td>
        <td>${r.eval_id || '<i>ad-hoc</i>'}</td>
        <td>${r.model_label || r.model_id || ''}</td>
        <td>${r.elapsed_ms || 0}ms</td>
        <td>${judgeStr}</td>
        <td class="actions">
          <button data-act="view">View</button>
          <button data-act="del" class="danger">×</button>
        </td>`;
      tr.querySelector('[data-act="view"]').addEventListener('click', () => viewResult(r.result_id));
      tr.querySelector('[data-act="del"]').addEventListener('click', async () => {
        if (!confirm('Delete this result?')) return;
        await apiSend('DELETE', '/api/results/' + r.result_id);
        loadResults();
      });
      tb.appendChild(tr);
    });
  }

  async function viewResult(rid) {
    const r = await apiGet('/api/results/' + rid);
    CURRENT_RESULT = r;
    document.getElementById('rd-title').textContent = `${r.eval_id || 'ad-hoc'} — ${r.model_label || r.model_id}`;
    document.getElementById('rd-meta').textContent =
      `saved ${r.saved_at} · ${r.elapsed_ms}ms · ${r.system_prompt_chars} sys / ${r.user_prompt_chars} user chars`;
    document.getElementById('rd-output').textContent = r.content || ('(error: ' + (r.error || 'none') + ')');
    document.getElementById('rd-judge').textContent = r.judge ? JSON.stringify(r.judge, null, 2) : '(no judge run)';
    document.getElementById('result-detail').classList.remove('hidden');
  }

  async function rejudgeCurrentResult() {
    if (!CURRENT_RESULT) return;
    const j = await apiSend('POST', '/api/judge', {
      result_id: CURRENT_RESULT.result_id,
      use_llm_judge: true,
    });
    document.getElementById('rd-judge').textContent = JSON.stringify(j, null, 2);
  }

  // ---------- Helpers ----------
  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[c]);
  }

  init();
})();
