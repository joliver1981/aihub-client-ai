/* Command Center — Execution Inspector v6
 *
 * Three-panel layout: Flow | Detail | State
 * Structured renderers for each event type.
 */

// ── Utilities ───────────────────────────────────────────────────────────

function qs(name) {
  return new URLSearchParams(window.location.search).get(name);
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function fmtMs(ms) {
  if (ms == null) return '';
  if (ms < 1000) return ms + 'ms';
  return (ms / 1000).toFixed(1) + 's';
}

function badgeClass(level) {
  if (!level) return 'cc-badge-ok';
  const l = String(level).toLowerCase();
  if (l === 'error') return 'cc-badge-err';
  if (l === 'warning' || l === 'warn') return 'cc-badge-warn';
  return 'cc-badge-ok';
}

// ── Label mapping ───────────────────────────────────────────────────────

const LABELS = {
  trace_start: 'Trace Start',
  sse: 'SSE',
  status: 'Status',
  landscape: 'Landscape',
  landscape_error: 'Landscape Error',
  graph_input: 'Graph Input',
  node_start: 'Node Start',
  node_end: 'Node End',
  node_error: 'Node Error',
  route: 'Route',
  llm_call: 'LLM Call',
  tool_start: 'Tool Start',
  tool_end: 'Tool End',
  delegate_start: 'Delegate',
  delegate_end: 'Delegate End',
  delegate_error: 'Delegate Error',
  auto_confirm: 'Auto Confirm',
  distill_error: 'Distill Error',
  graph_done: 'Graph Done',
  response: 'Response',
  error: 'Error',
};

function labelFor(evt) {
  return LABELS[evt.event_type] || evt.event_type || 'event';
}

// ── Data fetching ───────────────────────────────────────────────────────

const userId = qs('user_id');
const sessionId = qs('session_id');
const traceId = qs('trace_id');

async function loadTrace() {
  const url = `/api/inspect/traces/${encodeURIComponent(traceId)}?user_id=${encodeURIComponent(userId)}&session_id=${encodeURIComponent(sessionId)}`;
  const resp = await fetch(url, { cache: 'no-store' });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  return data.events || [];
}

async function loadSummary() {
  try {
    const url = `/api/inspect/traces/${encodeURIComponent(traceId)}/summary?user_id=${encodeURIComponent(userId)}&session_id=${encodeURIComponent(sessionId)}`;
    const resp = await fetch(url, { cache: 'no-store' });
    if (!resp.ok) return null;
    return await resp.json();
  } catch { return null; }
}

// ── Stats bar ───────────────────────────────────────────────────────────

function renderStatsBar(summary) {
  const bar = document.getElementById('stats-bar');
  if (!summary || !summary.event_count) { bar.innerHTML = ''; return; }

  let html = '';
  html += `<span class="cc-stat"><span class="cc-stat-label">Duration</span> <span class="cc-stat-value">${fmtMs(summary.total_duration_ms)}</span></span>`;
  html += `<span class="cc-stat-sep">|</span>`;
  html += `<span class="cc-stat"><span class="cc-stat-label">LLM</span> <span class="cc-stat-value">${summary.llm_call_count} calls (${fmtMs(summary.llm_total_ms)})</span></span>`;
  html += `<span class="cc-stat-sep">|</span>`;
  html += `<span class="cc-stat"><span class="cc-stat-label">Tools</span> <span class="cc-stat-value">${summary.tool_call_count}</span></span>`;
  html += `<span class="cc-stat-sep">|</span>`;
  html += `<span class="cc-stat"><span class="cc-stat-label">Delegates</span> <span class="cc-stat-value">${summary.delegation_count}</span></span>`;
  if (summary.error_count > 0) {
    html += `<span class="cc-stat-sep">|</span>`;
    html += `<span class="cc-stat"><span class="cc-stat-label">Errors</span> <span class="cc-stat-value" style="color:#fca5a5">${summary.error_count}</span></span>`;
  }
  if (summary.intent) {
    html += `<span class="cc-stat-sep">|</span>`;
    html += `<span class="cc-stat"><span class="cc-stat-label">Intent</span> <span class="cc-stat-value">${escHtml(summary.intent)}</span></span>`;
  }

  // Path breadcrumb
  if (summary.path && summary.path.length) {
    html += `<span class="cc-stat-sep">|</span>`;
    html += '<span class="cc-stats-path">';
    summary.path.forEach((p, i) => {
      // Only show node_start names (strip "route_by_intent:gather_data" to just the node names)
      const display = p.includes(':') ? p.split(':').pop() : p;
      html += `<span class="cc-path-node">${escHtml(display)}</span>`;
      if (i < summary.path.length - 1) html += '<span class="cc-path-arrow">\u2192</span>';
    });
    html += '</span>';
  }

  bar.innerHTML = html;
}

// ── Flow panel ──────────────────────────────────────────────────────────

function flowSummary(evt) {
  const p = evt.payload || {};
  const et = evt.event_type;
  if (et === 'llm_call') return `${p.step || ''} ${fmtMs(p.elapsed_ms)}`;
  if (et === 'route') return `\u2192 ${p.choice || ''}`;
  if (et === 'node_start') return evt.node || '';
  if (et === 'node_end') return fmtMs(p.elapsed_ms);
  if (et === 'node_error') return (p.error || '').substring(0, 60);
  if (et === 'tool_start') return evt.node || '';
  if (et === 'tool_end') return `${evt.node || ''} ${(p.result_preview || '').substring(0, 40)}`;
  if (et === 'delegate_start') return `Agent ${p.agent_id || ''}`;
  if (et === 'delegate_end') return `${p.status || ''} ${fmtMs(p.elapsed_ms)}`;
  if (et === 'trace_start') return (p.user_message || '').substring(0, 50);
  if (et === 'status') return p.message || p.phase || '';
  if (et === 'error') return (p.error || evt.summary || '').substring(0, 60);
  return evt.summary || '';
}

let allEvents = [];

function renderFlow(events) {
  allEvents = events;
  const flow = document.getElementById('flow-nodes');
  flow.innerHTML = '';

  // Track nesting: events between node_start and node_end are nested
  let insideNode = false;

  events.forEach((evt, idx) => {
    const et = evt.event_type;

    if (et === 'node_start') insideNode = true;
    const isNested = insideNode && et !== 'node_start' && et !== 'node_end';
    if (et === 'node_end') insideNode = false;

    const el = document.createElement('div');
    el.className = `cc-flow-node cc-flow-node--${et}`;
    if (isNested) el.classList.add('cc-flow-node--nested');

    // Label row
    const labelEl = document.createElement('div');
    labelEl.className = 'cc-flow-label';
    labelEl.textContent = labelFor(evt);

    // Badge
    if (evt.level && evt.level !== 'info') {
      const badge = document.createElement('span');
      badge.className = `cc-flow-badge ${badgeClass(evt.level)}`;
      badge.textContent = evt.level.toUpperCase();
      labelEl.appendChild(badge);
    }

    // Elapsed badge for llm_call and node_end
    const elapsed = (evt.payload || {}).elapsed_ms;
    if ((et === 'llm_call' || et === 'node_end' || et === 'delegate_end') && elapsed != null) {
      const msBadge = document.createElement('span');
      msBadge.className = 'cc-flow-badge cc-badge-ms';
      msBadge.textContent = fmtMs(elapsed);
      labelEl.appendChild(msBadge);
    }

    el.appendChild(labelEl);

    // Summary line
    const summary = flowSummary(evt);
    if (summary) {
      const sumEl = document.createElement('div');
      sumEl.className = 'cc-flow-summary';
      sumEl.textContent = summary;
      el.appendChild(sumEl);
    }

    el.onclick = () => selectEvent(el, evt, idx);
    flow.appendChild(el);

    if (idx === 0) selectEvent(el, evt, idx);
  });
}

// ── Event selection ─────────────────────────────────────────────────────

function selectEvent(nodeEl, evt, idx) {
  document.querySelectorAll('.cc-flow-node.active').forEach(x => x.classList.remove('active'));
  nodeEl.classList.add('active');
  renderDetail(evt);
  renderState(evt, idx);
}

// ── Detail panel — dispatch by event type ───────────────────────────────

function renderDetail(evt) {
  const title = document.getElementById('detail-title');
  const body = document.getElementById('detail-body');
  title.textContent = `${labelFor(evt)} \u2022 ${evt.node || ''}`.trim();

  const renderers = {
    llm_call: renderLlmCall,
    route: renderRoute,
    node_start: renderNodeStart,
    node_end: renderNodeEnd,
    node_error: renderNodeError,
    tool_start: renderToolStart,
    tool_end: renderToolEnd,
    delegate_start: renderDelegateStart,
    delegate_end: renderDelegateEnd,
    trace_start: renderTraceStart,
    error: renderError,
  };

  const fn = renderers[evt.event_type] || renderGeneric;
  body.innerHTML = '';
  body.appendChild(fn(evt));
}

// ── Renderer: LLM Call ──────────────────────────────────────────────────

function renderLlmCall(evt) {
  const p = evt.payload || {};
  const frag = document.createElement('div');

  // Header
  const header = document.createElement('div');
  header.className = 'cc-llm-header';
  header.innerHTML = `
    <span class="cc-llm-step">${escHtml(p.step || 'LLM Call')}</span>
    <span class="cc-llm-meta">
      <span>${fmtMs(p.elapsed_ms)}</span>
      ${p.model ? `<span>model: ${escHtml(p.model)}</span>` : ''}
      <span>${(p.prompt_chars || 0).toLocaleString()} prompt chars</span>
      <span>${(p.response_chars || 0).toLocaleString()} response chars</span>
    </span>`;
  frag.appendChild(header);

  // Prompt section
  if (p.messages && p.messages.length) {
    const promptSec = makeCollapsible('Prompt (' + p.messages.length + ' messages)', true);
    const body = promptSec.querySelector('.cc-llm-section-body');

    p.messages.forEach(msg => {
      const role = msg.role || 'unknown';
      const msgEl = document.createElement('div');
      msgEl.className = `cc-llm-message cc-llm-message--${role}`;

      const roleEl = document.createElement('div');
      roleEl.className = 'cc-llm-role';
      roleEl.textContent = role;
      msgEl.appendChild(roleEl);

      const content = document.createElement('div');
      content.className = 'cc-llm-content';
      content.textContent = msg.content || '';
      msgEl.appendChild(content);

      if (msg.full_length && msg.content && msg.full_length > msg.content.length) {
        const trunc = document.createElement('div');
        trunc.className = 'cc-llm-truncated';
        trunc.textContent = `Showing ${msg.content.length.toLocaleString()} of ${msg.full_length.toLocaleString()} chars`;
        msgEl.appendChild(trunc);
      }

      body.appendChild(msgEl);
    });

    frag.appendChild(promptSec);
  }

  // Response section
  const respSec = makeCollapsible('Response', true);
  const respBody = respSec.querySelector('.cc-llm-section-body');

  const respMsg = document.createElement('div');
  respMsg.className = 'cc-llm-message cc-llm-message--assistant';
  const respRole = document.createElement('div');
  respRole.className = 'cc-llm-role';
  respRole.textContent = 'assistant';
  respMsg.appendChild(respRole);

  const respContent = document.createElement('div');
  respContent.className = 'cc-llm-content';
  respContent.textContent = p.response || '(empty)';
  respMsg.appendChild(respContent);

  if (p.response_chars && p.response && p.response_chars > p.response.length) {
    const trunc = document.createElement('div');
    trunc.className = 'cc-llm-truncated';
    trunc.textContent = `Showing ${p.response.length.toLocaleString()} of ${p.response_chars.toLocaleString()} chars`;
    respMsg.appendChild(trunc);
  }

  respBody.appendChild(respMsg);

  // Tool calls
  if (p.response_tool_calls) {
    const tcEl = document.createElement('div');
    tcEl.className = 'cc-llm-message cc-llm-message--tool';
    const tcRole = document.createElement('div');
    tcRole.className = 'cc-llm-role';
    tcRole.textContent = 'tool calls';
    tcEl.appendChild(tcRole);
    const tcContent = document.createElement('div');
    tcContent.className = 'cc-llm-content';
    tcContent.textContent = typeof p.response_tool_calls === 'string'
      ? p.response_tool_calls
      : JSON.stringify(p.response_tool_calls, null, 2);
    tcEl.appendChild(tcContent);
    respBody.appendChild(tcEl);
  }

  frag.appendChild(respSec);
  return frag;
}

function makeCollapsible(title, openByDefault) {
  const sec = document.createElement('div');
  sec.className = 'cc-llm-section' + (openByDefault ? ' open' : '');

  const titleEl = document.createElement('div');
  titleEl.className = 'cc-llm-section-title';
  titleEl.textContent = title;
  titleEl.onclick = () => sec.classList.toggle('open');

  const body = document.createElement('div');
  body.className = 'cc-llm-section-body';

  sec.appendChild(titleEl);
  sec.appendChild(body);
  return sec;
}

// ── Renderer: Route ─────────────────────────────────────────────────────

function renderRoute(evt) {
  const p = evt.payload || {};
  const frag = document.createElement('div');

  const header = document.createElement('div');
  header.className = 'cc-route-header';
  header.textContent = evt.node || 'Route';
  frag.appendChild(header);

  const choice = document.createElement('div');
  choice.className = 'cc-route-choice';
  choice.textContent = p.choice || '?';
  frag.appendChild(choice);

  // Context
  const ctx = document.createElement('div');
  ctx.className = 'cc-route-context';
  const fields = { ...p };
  delete fields.choice;
  ctx.appendChild(makeKvGrid(fields));
  frag.appendChild(ctx);

  return frag;
}

// ── Renderer: Node Start ────────────────────────────────────────────────

function renderNodeStart(evt) {
  const p = evt.payload || {};
  const frag = document.createElement('div');

  const header = document.createElement('div');
  header.className = 'cc-node-header';
  header.innerHTML = `<span class="cc-node-name">${escHtml(evt.node || 'Node')}</span>`;
  frag.appendChild(header);

  const ts = document.createElement('div');
  ts.style.cssText = 'font-size:11px;color:#71717a;margin-bottom:10px';
  ts.textContent = evt.ts ? evt.ts.replace('T', ' ').slice(0, 23) : '';
  frag.appendChild(ts);

  // State snapshot
  if (p.state && typeof p.state === 'object') {
    const sec = makeCollapsible('State at entry', true);
    sec.querySelector('.cc-llm-section-body').appendChild(makeKvGrid(p.state));
    frag.appendChild(sec);
  }

  return frag;
}

// ── Renderer: Node End ──────────────────────────────────────────────────

function renderNodeEnd(evt) {
  const p = evt.payload || {};
  const frag = document.createElement('div');

  const header = document.createElement('div');
  header.className = 'cc-node-header';
  header.innerHTML = `<span class="cc-node-name">${escHtml(evt.node || 'Node')}</span>`;
  frag.appendChild(header);

  if (p.elapsed_ms != null) {
    const el = document.createElement('div');
    el.className = 'cc-node-elapsed';
    el.textContent = fmtMs(p.elapsed_ms);
    frag.appendChild(el);
  }

  if (p.writes && p.writes.length) {
    const label = document.createElement('div');
    label.style.cssText = 'font-size:11px;color:#71717a;margin-bottom:4px';
    label.textContent = 'State fields written:';
    frag.appendChild(label);

    const tags = document.createElement('div');
    tags.className = 'cc-node-writes';
    p.writes.forEach(w => {
      const tag = document.createElement('span');
      tag.className = 'cc-node-write-tag';
      tag.textContent = w;
      tags.appendChild(tag);
    });
    frag.appendChild(tags);
  }

  return frag;
}

// ── Renderer: Node Error ────────────────────────────────────────────────

function renderNodeError(evt) {
  const p = evt.payload || {};
  const frag = document.createElement('div');
  frag.innerHTML = `
    <div class="cc-error-box">
      <div class="cc-error-title">${escHtml(evt.node || 'Error')} failed after ${fmtMs(p.elapsed_ms)}</div>
      <div class="cc-error-msg">${escHtml(p.error || 'Unknown error')}</div>
    </div>`;
  return frag;
}

// ── Renderer: Tool Start / End ──────────────────────────────────────────

function renderToolStart(evt) {
  const p = evt.payload || {};
  const frag = document.createElement('div');

  const header = document.createElement('div');
  header.className = 'cc-tool-header';
  header.textContent = (evt.node || 'Tool').replace('tool:', '');
  frag.appendChild(header);

  if (p.args) {
    const label = document.createElement('div');
    label.style.cssText = 'font-size:11px;color:#71717a;margin-bottom:4px';
    label.textContent = 'Arguments:';
    frag.appendChild(label);
    const args = document.createElement('div');
    args.className = 'cc-tool-args';
    args.textContent = typeof p.args === 'string' ? p.args : JSON.stringify(p.args, null, 2);
    frag.appendChild(args);
  }

  return frag;
}

function renderToolEnd(evt) {
  const p = evt.payload || {};
  const frag = document.createElement('div');

  const header = document.createElement('div');
  header.className = 'cc-tool-header';
  header.textContent = (evt.node || 'Tool').replace('tool:', '') + ' - Result';
  frag.appendChild(header);

  if (p.result_preview) {
    const result = document.createElement('div');
    result.className = 'cc-tool-args';
    result.textContent = p.result_preview;
    frag.appendChild(result);
  }

  return frag;
}

// ── Renderer: Delegate Start / End ──────────────────────────────────────

function renderDelegateStart(evt) {
  const p = evt.payload || {};
  const frag = document.createElement('div');

  frag.innerHTML = `<div class="cc-deleg-header">Delegate to Agent ${escHtml(String(p.agent_id || ''))}</div>`;

  if (p.question_preview) {
    const q = document.createElement('div');
    q.className = 'cc-deleg-question';
    q.textContent = p.question_preview;
    frag.appendChild(q);
  }

  if (p.is_data_agent != null) {
    const meta = document.createElement('div');
    meta.style.cssText = 'font-size:11px;color:#71717a;margin-top:6px';
    meta.textContent = `Type: ${p.is_data_agent ? 'Data Agent' : 'General Agent'}`;
    frag.appendChild(meta);
  }

  return frag;
}

function renderDelegateEnd(evt) {
  const p = evt.payload || {};
  const frag = document.createElement('div');

  frag.innerHTML = `
    <div class="cc-deleg-header">Delegate Result - Agent ${escHtml(String(p.agent_id || ''))}</div>
    <div style="font-size:11px;color:#71717a;margin-bottom:6px">
      Status: ${escHtml(p.status || 'unknown')} | ${fmtMs(p.elapsed_ms)}
    </div>`;

  if (p.text_preview) {
    const r = document.createElement('div');
    r.className = 'cc-deleg-result';
    r.textContent = p.text_preview;
    frag.appendChild(r);
  }

  return frag;
}

// ── Renderer: Trace Start ───────────────────────────────────────────────

function renderTraceStart(evt) {
  const p = evt.payload || {};
  const frag = document.createElement('div');
  frag.innerHTML = `<div class="cc-node-header"><span class="cc-node-name">Trace Start</span></div>`;

  if (p.user_message) {
    const q = document.createElement('div');
    q.className = 'cc-deleg-question';
    q.textContent = p.user_message;
    frag.appendChild(q);
  }

  const grid = {};
  if (p.user_context) {
    Object.entries(p.user_context).forEach(([k, v]) => { grid['user.' + k] = v; });
  }
  if (Object.keys(grid).length) frag.appendChild(makeKvGrid(grid));

  return frag;
}

// ── Renderer: Error ─────────────────────────────────────────────────────

function renderError(evt) {
  const p = evt.payload || {};
  const frag = document.createElement('div');
  frag.innerHTML = `
    <div class="cc-error-box">
      <div class="cc-error-title">${escHtml(evt.summary || 'Error')}</div>
      <div class="cc-error-msg">${escHtml(p.error || p.traceback || JSON.stringify(p, null, 2))}</div>
    </div>`;
  return frag;
}

// ── Renderer: Generic (JSON fallback with syntax highlighting) ──────────

function renderGeneric(evt) {
  const pre = document.createElement('pre');
  pre.className = 'cc-detail-pre';
  pre.innerHTML = syntaxHighlight(JSON.stringify(evt, null, 2));
  return pre;
}

function syntaxHighlight(json) {
  return json.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    (match) => {
      let cls = 'cc-json-number';
      if (/^"/.test(match)) {
        cls = /:$/.test(match) ? 'cc-json-key' : 'cc-json-string';
      } else if (/true|false/.test(match)) {
        cls = 'cc-json-bool';
      } else if (/null/.test(match)) {
        cls = 'cc-json-null';
      }
      return `<span class="${cls}">${match}</span>`;
    }
  );
}

// ── Key-value grid helper ───────────────────────────────────────────────

function makeKvGrid(obj) {
  const grid = document.createElement('div');
  grid.className = 'cc-kv-grid';

  function addRow(key, value) {
    const k = document.createElement('div');
    k.className = 'cc-kv-key';
    k.textContent = key;
    grid.appendChild(k);

    const v = document.createElement('div');
    v.className = 'cc-kv-value';

    if (value === null || value === undefined) {
      v.textContent = 'null';
      v.style.color = '#71717a';
      v.style.fontStyle = 'italic';
    } else if (typeof value === 'object') {
      v.textContent = JSON.stringify(value);
    } else {
      v.textContent = String(value);
    }
    grid.appendChild(v);
  }

  if (obj && typeof obj === 'object') {
    Object.entries(obj).forEach(([k, val]) => addRow(k, val));
  }

  return grid;
}

// ── State panel ─────────────────────────────────────────────────────────

function renderState(evt, idx) {
  const body = document.getElementById('state-body');

  // Find nearest node_start at or before this event
  let stateData = null;
  for (let i = idx; i >= 0; i--) {
    const e = allEvents[i];
    if (e.event_type === 'node_start' && e.payload && e.payload.state) {
      stateData = e.payload.state;
      break;
    }
  }

  if (!stateData) {
    body.innerHTML = '<div class="cc-state-empty">No state snapshot near this event</div>';
    return;
  }

  body.innerHTML = '';

  // Group state fields into sections
  const sections = [
    {
      title: 'Routing',
      fields: ['intent', 'pending_agent_selection', 'route_memory_match'],
    },
    {
      title: 'Delegation',
      fields: ['active_delegation'],
    },
    {
      title: 'Tasks',
      fields: ['sub_tasks', 'current_task_index'],
    },
    {
      title: 'Counts',
      fields: ['messages_len', 'render_blocks_len', 'delegation_results_keys'],
    },
    {
      title: 'Session',
      fields: ['session_resources_len', 'has_fallback_context'],
    },
  ];

  sections.forEach(sec => {
    // Check if any field in this section has data
    const hasData = sec.fields.some(f => stateData[f] != null && stateData[f] !== false);
    if (!hasData && sec.title !== 'Routing') return; // Always show Routing

    const details = document.createElement('details');
    details.className = 'cc-state-section';
    if (sec.title === 'Routing' || sec.title === 'Delegation') details.open = true;

    const summary = document.createElement('summary');
    summary.textContent = sec.title;
    details.appendChild(summary);

    const secBody = document.createElement('div');
    secBody.className = 'cc-state-section-body';

    sec.fields.forEach(field => {
      const val = stateData[field];
      const row = document.createElement('div');
      row.className = 'cc-state-value';

      if (val === null || val === undefined || val === false) {
        row.className += ' cc-state-value--null';
        row.textContent = `${field}: none`;
      } else if (typeof val === 'object' && !Array.isArray(val)) {
        row.innerHTML = `<strong>${escHtml(field)}:</strong>`;
        const sub = makeKvGrid(val);
        sub.style.marginLeft = '8px';
        sub.style.marginTop = '2px';
        row.appendChild(sub);
      } else if (Array.isArray(val)) {
        row.innerHTML = `<strong>${escHtml(field)}:</strong> (${val.length} items)`;
        if (val.length > 0 && val.length <= 10) {
          val.forEach(item => {
            const itemEl = document.createElement('div');
            itemEl.style.cssText = 'margin-left:8px;margin-top:2px;font-size:10px;color:#a1a1aa';
            if (typeof item === 'object' && item) {
              const desc = item.description || item.id || JSON.stringify(item);
              const status = item.status || '';
              let badge = '';
              if (status) {
                const cls = status === 'completed' ? 'success' : (status === 'failed' ? 'failed' : 'pending');
                badge = ` <span class="cc-state-badge cc-state-badge--${cls}">${escHtml(status)}</span>`;
              }
              itemEl.innerHTML = escHtml(String(desc).substring(0, 100)) + badge;
            } else {
              itemEl.textContent = String(item).substring(0, 100);
            }
            row.appendChild(itemEl);
          });
        }
      } else {
        row.innerHTML = `<strong>${escHtml(field)}:</strong> ${escHtml(String(val))}`;
      }

      secBody.appendChild(row);
    });

    details.appendChild(secBody);
    body.appendChild(details);
  });
}

// ── State panel toggle ──────────────────────────────────────────────────

function initToggle() {
  const btn = document.getElementById('btn-toggle-state');
  const main = document.querySelector('.cc-inspector-main');
  btn.onclick = () => {
    main.classList.toggle('state-hidden');
    btn.classList.toggle('active');
  };
  btn.classList.add('active'); // visible by default
}

// ── Main ────────────────────────────────────────────────────────────────

async function main() {
  document.getElementById('btn-refresh').onclick = () => main();

  const sub = document.getElementById('inspector-sub');
  sub.textContent = `Trace: ${traceId} | Session: ${sessionId} | User: ${userId}`;

  initToggle();

  try {
    const [events, summary] = await Promise.all([loadTrace(), loadSummary()]);
    renderStatsBar(summary);
    renderFlow(events);
  } catch (e) {
    sub.textContent = `Failed to load trace: ${e.message}`;
  }
}

main();
