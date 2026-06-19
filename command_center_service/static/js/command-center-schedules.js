/* CC-native Scheduled Tasks panel — isolated add-on (no changes to command-center.js).
   Renders the signed-in user's scheduled tasks + results thread from /api/schedules with an
   unread badge. Auth via the cc_token JWT, same contract as chat. */
(function () {
  function token() { return localStorage.getItem('cc_token') || ''; }
  function userCtx() {
    try { return JSON.parse(localStorage.getItem('cc_user_context') || '{}'); }
    catch (e) { return {}; }
  }
  function headers() {
    const h = { 'Content-Type': 'application/json' };
    const t = token(); if (t) h['Authorization'] = 'Bearer ' + t;
    return h;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  const CCSchedules = {
    async refreshBadge() {
      try {
        const r = await fetch('/api/schedules', { headers: headers() });
        if (!r.ok) return;
        const d = await r.json();
        const b = document.getElementById('schedule-badge');
        if (!b) return;
        const n = d.unread_count || 0;
        if (n > 0) { b.textContent = n > 99 ? '99+' : String(n); b.style.display = ''; }
        else { b.style.display = 'none'; }
      } catch (e) { /* silent */ }
    },

    async open() {
      const ov = document.getElementById('schedules-overlay');
      if (ov) ov.style.display = 'flex';
      await this.loadTasks();
      await this.loadResults();
      await this.markRead();
      this.refreshBadge();
    },

    close() {
      const ov = document.getElementById('schedules-overlay');
      if (ov) ov.style.display = 'none';
    },

    async loadTasks() {
      const el = document.getElementById('schedules-tasks');
      if (!el) return;
      el.innerHTML = '<div style="opacity:.6">Loading…</div>';
      try {
        const r = await fetch('/api/schedules', { headers: headers() });
        const d = await r.json();
        const tasks = d.tasks || [];
        if (!tasks.length) {
          el.innerHTML = '<div style="opacity:.6">No scheduled tasks yet. Ask the agent to schedule one (e.g. “every weekday at 8am, …”).</div>';
          return;
        }
        el.innerHTML = tasks.map(function (t) {
          return '<div style="border:1px solid rgba(128,128,128,.3);border-radius:8px;padding:10px 12px;margin-bottom:8px;display:flex;justify-content:space-between;gap:10px">' +
            '<div><div style="font-weight:600">' + esc(t.task_name) + '</div>' +
            '<div style="font-size:12px;opacity:.7">' + esc(t.schedule_desc) +
            ' · last run: ' + esc(t.last_run || 'never') +
            (t.last_status ? ' [' + esc(t.last_status) + ']' : '') + '</div></div>' +
            '<button onclick="CCSchedules.cancel(\'' + esc(t.job_id) + '\')" ' +
            'style="align-self:center;background:none;border:1px solid rgba(128,128,128,.4);border-radius:6px;padding:4px 10px;cursor:pointer;color:inherit">Cancel</button>' +
            '</div>';
        }).join('');
      } catch (e) {
        el.innerHTML = '<div style="color:#c0490d">Could not load tasks.</div>';
      }
    },

    async loadResults() {
      const el = document.getElementById('schedules-results');
      if (!el) return;
      el.innerHTML = '<div style="opacity:.6">Loading…</div>';
      try {
        const r = await fetch('/api/schedules/results', { headers: headers() });
        const d = await r.json();
        const results = d.results || [];
        if (!results.length) { el.innerHTML = '<div style="opacity:.6">No results yet.</div>'; return; }
        const uc = userCtx();
        const q = 'user_id=' + encodeURIComponent(uc.user_id || '') +
          '&tenant_id=' + encodeURIComponent(uc.tenant_id || '') +
          '&role=' + encodeURIComponent(uc.role || 0);
        el.innerHTML = results.map(function (res) {
          const arts = (res.artifact_ids || []).map(function (id) {
            return '<a href="/api/artifacts/' + encodeURIComponent(id) + '/download?' + q + '" ' +
              'target="_blank" rel="noopener" style="display:inline-block;margin:6px 6px 0 0;font-size:12px;padding:3px 8px;border:1px solid rgba(128,128,128,.4);border-radius:6px;text-decoration:none;color:inherit">⬇ download</a>';
          }).join('');
          const border = res.unread ? '#c0490d' : 'rgba(128,128,128,.3)';
          const newTag = res.unread ? ' <span style="color:#c0490d;font-size:11px">● new</span>' : '';
          return '<div style="border:1px solid ' + border + ';border-radius:8px;padding:10px 12px;margin-bottom:8px">' +
            '<div style="display:flex;justify-content:space-between;gap:8px">' +
            '<span style="font-weight:600">' + esc(res.task_name) + newTag + '</span>' +
            '<span style="font-size:12px;opacity:.6">' + esc(res.ts) + '</span></div>' +
            '<div style="font-size:13px;white-space:pre-wrap;margin-top:6px">' + esc((res.summary || '').slice(0, 1500)) + '</div>' +
            arts + '</div>';
        }).join('');
      } catch (e) {
        el.innerHTML = '<div style="color:#c0490d">Could not load results.</div>';
      }
    },

    async cancel(jobId) {
      if (!window.confirm('Cancel this scheduled task? It will stop running.')) return;
      try { await fetch('/api/schedules/' + encodeURIComponent(jobId), { method: 'DELETE', headers: headers() }); }
      catch (e) { /* ignore */ }
      this.loadTasks();
    },

    async markRead() {
      try { await fetch('/api/schedules/results/read', { method: 'POST', headers: headers(), body: '{}' }); }
      catch (e) { /* ignore */ }
    }
  };

  window.CCSchedules = CCSchedules;
  // Refresh the unread badge shortly after load, once CC has resolved the token.
  document.addEventListener('DOMContentLoaded', function () {
    setTimeout(function () { CCSchedules.refreshBadge(); }, 2500);
  });
})();
