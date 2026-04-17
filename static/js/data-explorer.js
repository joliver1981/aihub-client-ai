/**
 * Data Explorer v2 — Main Controller
 * =====================================
 * SSE consumer, chat management, state coordination, theme control.
 * Orchestrates DETableRenderer, DEChartRenderer, and DEDashboard.
 */
(function () {
    'use strict';

    /* ── State ─────────────────────────────────────────────── */

    var _agents = [];
    var _selectedAgentId = '';
    var _conversationHistory = [];  // [{ role: 'Q'|'A', content: '...' }]
    var _queryRegistry = {};        // queryId -> { sql, agentId, data }
    var _isSending = false;

    /* ── Initialization ────────────────────────────────────── */

    function init() {
        // Restore theme
        var savedTheme = localStorage.getItem('de-theme');
        if (savedTheme === 'light') {
            document.getElementById('explorerPage').classList.add('light-mode');
            _updateThemeButton(true);
        }

        // Init dashboard grid
        if (window.DEDashboard) DEDashboard.init();

        // Load agents
        _loadAgents();

        // Load saved dashboards list
        _loadSavedDashboards();

        // Panel header title — double-click to rename
        var panelTitleEl = document.getElementById('dashboardTitleText');
        if (panelTitleEl) {
            panelTitleEl.addEventListener('dblclick', function (e) {
                e.stopPropagation();
                var input = document.createElement('input');
                input.type = 'text';
                input.className = 'de-dashboard-title-input';
                input.value = _activeDashboard.title || 'Dashboard';
                input.maxLength = 60;
                panelTitleEl.replaceWith(input);
                input.focus();
                input.select();

                function _commit() {
                    var newTitle = input.value.trim() || _activeDashboard.title;
                    var span = document.createElement('span');
                    span.id = 'dashboardTitleText';
                    span.textContent = newTitle;
                    input.replaceWith(span);
                    _activeDashboard.title = newTitle;
                    _updatePinButtonLabels();
                    _loadSavedDashboards(); // refresh sidebar names

                    // Re-attach dblclick on the new span
                    span.addEventListener('dblclick', panelTitleEl.__deRenameHandler);

                    // Persist if saved
                    if (_activeDashboard.id) {
                        fetch('/data_explorer/dashboard/' + _activeDashboard.id + '/rename', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ title: newTitle })
                        }).catch(function () {});
                    }
                }

                input.addEventListener('blur', _commit);
                input.addEventListener('keydown', function (ev) {
                    if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
                    if (ev.key === 'Escape') { input.value = _activeDashboard.title; input.blur(); }
                });
            });
            // Store reference for re-attachment
            panelTitleEl.__deRenameHandler = panelTitleEl.onclick;
        }

        // Textarea auto-resize and Enter key
        var textarea = document.getElementById('userInput');
        if (textarea) {
            textarea.style.height = '44px'; // consistent initial height
            textarea.style.overflow = 'hidden';
            textarea.addEventListener('input', function () {
                this.style.height = '44px';
                this.style.height = Math.min(this.scrollHeight, 120) + 'px';
                this.style.overflow = this.scrollHeight > 120 ? 'auto' : 'hidden';
            });
            textarea.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage();
                }
            });
        }
    }

    /* ── Agent Loading ─────────────────────────────────────── */

    function _loadAgents() {
        fetch('/get/user_data_agents')
            .then(function (r) { return r.json(); })
            .then(function (resp) {
                try {
                    _agents = JSON.parse(resp.data || '[]');
                } catch (e) {
                    _agents = resp.data || [];
                }
                _populateAgentDropdown();
            })
            .catch(function (err) {
                console.error('Failed to load agents:', err);
            });
    }

    function _populateAgentDropdown() {
        var dd = document.getElementById('agentDropdown');
        if (!dd || !_agents.length) return;

        dd.innerHTML = '<option value="">Select a data source</option>';
        _agents.forEach(function (agent) {
            var opt = document.createElement('option');
            opt.value = agent.agent_id;
            opt.textContent = agent.agent_description || 'Agent';
            dd.appendChild(opt);
        });

        // Auto-select first agent if available
        if (_agents.length > 0 && !sessionStorage.getItem('de-agent-id')) {
            dd.selectedIndex = 1;
        }

        // Restore last selected
        var saved = sessionStorage.getItem('de-agent-id');
        if (saved) dd.value = saved;

        _selectedAgentId = dd.value;
        _updateAgentInfo();

        dd.addEventListener('change', function () {
            _selectedAgentId = this.value;
            sessionStorage.setItem('de-agent-id', _selectedAgentId);
            _updateAgentInfo();
        });
    }

    function _updateAgentInfo() {
        var obj = document.getElementById('agentObjective');
        if (!obj) return;

        var agent = _agents.find(function (a) {
            return String(a.agent_id) === String(_selectedAgentId);
        });

        obj.textContent = agent ? (agent.agent_objective || '') : '';
    }

    /* ── Send Message (JSON fetch) ─────────────────────────── */

    // Rotating status messages for the loading animation
    var _statusMessages = [
        'Analyzing your question...',
        'Thinking...',
        'Building insights...',
        'Querying your data...',
        'Generating response...',
        'Preparing visualization...',
        'Organizing data...',
        'Almost there...'
    ];
    var _statusTimer = null;

    function _startStatusRotation() {
        var idx = 0;
        _showStatus(_statusMessages[0]);
        _statusTimer = setInterval(function () {
            idx = (idx + 1) % _statusMessages.length;
            _showStatus(_statusMessages[idx]);
        }, 2500);
    }

    function _stopStatusRotation() {
        if (_statusTimer) {
            clearInterval(_statusTimer);
            _statusTimer = null;
        }
        _hideStatus();
    }

    async function sendMessage() {
        if (_isSending) return;

        var textarea = document.getElementById('userInput');
        var question = (textarea.value || '').trim();
        if (!question) return;

        if (!_selectedAgentId) {
            _showToast('Please select a data source first.');
            return;
        }

        _isSending = true;
        textarea.value = '';
        textarea.style.height = '44px';
        document.getElementById('sendBtn').disabled = true;

        // Hide welcome state
        var welcome = document.getElementById('welcomeState');
        if (welcome) welcome.style.display = 'none';

        // Add user message to chat
        _addChatMessage('user', question);
        _conversationHistory.push({ role: 'Q', content: question });

        // Show rotating status + skeleton placeholder
        _startStatusRotation();
        var aiMsgEl = _addChatMessage('ai', '', true);

        try {
            var response = await fetch('/data_explorer/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    agent_id: _selectedAgentId,
                    question: question,
                    history: JSON.stringify(_conversationHistory)
                })
            });

            if (!response.ok) {
                var errBody = null;
                try { errBody = await response.json(); } catch (e) {}
                throw new Error((errBody && errBody.error) || 'Server returned ' + response.status);
            }

            var data = await response.json();

            if (data.error) {
                _setMessageContent(aiMsgEl, '<div style="color:var(--de-red);"><i class="fas fa-exclamation-triangle"></i> ' + _esc(data.error) + '</div>');
            } else {
                _renderResult(data, aiMsgEl);
            }

        } catch (err) {
            console.error('Chat error:', err);
            _setMessageContent(aiMsgEl, '<div style="color:var(--de-red);">Error: ' + _esc(err.message) + '. Please try again.</div>');
        }

        _stopStatusRotation();
        _isSending = false;
        document.getElementById('sendBtn').disabled = false;
        _scrollChatToBottom();
    }

    /* ── Render Result ─────────────────────────────────────── */

    function _renderResult(data, aiMsgEl) {
        var html = '';
        var queryId = data.query_id || null;
        var hasRichContent = data.rich_content_enabled && data.rich_content;

        // Store query for dashboard refresh
        if (queryId && data.query) {
            var sqlMatch = data.query.match(/=== Data Query ===\n([\s\S]*?)(?:\n===|$)/);
            var sql = sqlMatch ? sqlMatch[1].trim() : data.query;
            _queryRegistry[queryId] = {
                sql: sql,
                agentId: _selectedAgentId,
                data: data.table_data,
                chartImage: null  // Will be set if chart_image block found
            };
            // Check for chart_image in rich content
            if (data.rich_content && data.rich_content.blocks) {
                data.rich_content.blocks.forEach(function(b) {
                    if (b.type === 'chart_image' && b.content) {
                        _queryRegistry[queryId].chartImage = b.content;
                    }
                });
            }
        }

        // Try rich content rendering first
        if (hasRichContent && data.rich_content.blocks) {
            html = _renderRichContentBlocks(data.rich_content.blocks, queryId);
        }
        // Fallback: table data
        else if (data.table_data) {
            html = DETableRenderer.render(data.table_data, {
                title: 'Query Results',
                pinnable: true
            });
        }
        // Fallback: answer string
        else if (data.answer) {
            html = _renderTextAnswer(data.answer);
        }

        // Add SQL toggle if available
        if (data.query && data.query.trim()) {
            html += _buildSqlToggle(data.query, queryId);
        }

        // Add "pin to dashboard" actions
        if (queryId) {
            var hasChartImage = _queryRegistry[queryId] && _queryRegistry[queryId].chartImage;
            if (data.table_data || hasChartImage) {
                var _pinLabel = _getActiveDashboardLabel();
                html += '<div class="de-msg-actions">';
                if (data.table_data) {
                    html += '<button class="de-msg-action-btn" onclick="DataExplorer.pinResultToDashboard(\'' + queryId + '\', \'table\')"><i class="fas fa-thumbtack"></i> Pin Table → <span class="de-pin-btn-label">' + _esc(_pinLabel) + '</span></button>';
                }
                if (hasChartImage) {
                    html += '<button class="de-msg-action-btn" onclick="DataExplorer.pinResultToDashboard(\'' + queryId + '\', \'chart\')"><i class="fas fa-chart-bar"></i> Pin Chart → <span class="de-pin-btn-label">' + _esc(_pinLabel) + '</span></button>';
                }
                html += '</div>';
            }
        }

        _setMessageContent(aiMsgEl, html);

        // Add to conversation history (text only)
        var textContent = data.answer || 'Data displayed.';
        if (typeof textContent !== 'string') textContent = 'Data displayed.';
        if (textContent.length > 500) textContent = textContent.substring(0, 500) + '...';
        _conversationHistory.push({ role: 'A', content: textContent });

        _scrollChatToBottom();
    }

    /* ── Rich Content Block Renderer ───────────────────────── */

    function _renderRichContentBlocks(blocks, queryId) {
        var html = '';

        blocks.forEach(function (block) {
            var type = (block.type || 'text').toLowerCase();
            var content = block.content;
            var meta = block.metadata || {};

            switch (type) {
                case 'table':
                case 'html_table':
                    html += DETableRenderer.render(content, {
                        title: meta.title || 'Table',
                        pinnable: true
                    });
                    break;

                case 'chart':
                    if (typeof content === 'object' && (content.data || content.type)) {
                        html += DEChartRenderer.render(content, {
                            title: meta.title || 'Chart',
                            pinnable: true
                        });
                    } else if (typeof content === 'string' && (content.indexOf('data:image') !== -1 || content.indexOf('<img') !== -1)) {
                        html += DEChartRenderer.renderImage(content, { title: meta.title || 'Chart' });
                    }
                    break;

                case 'chart_image':
                    // Matplotlib-generated chart image (base64 PNG)
                    if (typeof content === 'string' && content.indexOf('data:image') !== -1) {
                        html += '<div class="de-chart-image-container" style="margin:12px 0;text-align:center;">';
                        html += '<img src="' + content + '" style="max-width:100%;border-radius:8px;border:1px solid var(--de-border);" alt="' + _esc(meta.title || 'Chart') + '" />';
                        html += '</div>';
                    }
                    break;

                case 'metrics':
                    html += _renderMetrics(content, meta);
                    break;

                case 'code':
                case 'sql':
                    html += _renderCodeBlock(content, type === 'sql' ? 'sql' : (meta.language || ''));
                    break;

                case 'alert':
                    html += '<div style="padding:10px 14px;border-radius:8px;background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);color:var(--de-yellow);margin:8px 0;"><i class="fas fa-exclamation-circle"></i> ' + _esc(String(content)) + '</div>';
                    break;

                case 'success':
                    html += '<div style="padding:10px 14px;border-radius:8px;background:rgba(52,211,153,0.1);border:1px solid rgba(52,211,153,0.3);color:var(--de-green);margin:8px 0;"><i class="fas fa-check-circle"></i> ' + _esc(String(content)) + '</div>';
                    break;

                case 'error':
                    html += '<div style="padding:10px 14px;border-radius:8px;background:rgba(251,113,133,0.1);border:1px solid rgba(251,113,133,0.3);color:var(--de-red);margin:8px 0;"><i class="fas fa-times-circle"></i> ' + _esc(String(content)) + '</div>';
                    break;

                case 'image':
                    var src = content;
                    html += '<div style="margin:8px 0;"><img src="' + src + '" style="max-width:100%;border-radius:8px;" /></div>';
                    break;

                case 'json':
                    html += _renderCodeBlock(typeof content === 'string' ? content : JSON.stringify(content, null, 2), 'json');
                    break;

                case 'list':
                    html += _renderList(content);
                    break;

                case 'text':
                case 'number':
                default:
                    html += _renderTextAnswer(String(content || ''));
                    break;
            }
        });

        return html;
    }

    /* ── Sub-renderers ─────────────────────────────────────── */

    function _renderTextAnswer(text) {
        // Convert newlines to <br>, escape HTML
        var escaped = _esc(text);
        escaped = escaped.replace(/\n/g, '<br>');
        // Basic markdown-like bold
        escaped = escaped.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        return '<div style="line-height:1.7;">' + escaped + '</div>';
    }

    function _renderMetrics(content, meta) {
        var html = '<div class="de-kpi-grid">';

        if (Array.isArray(content)) {
            content.forEach(function (m) {
                html += '<div class="de-kpi-card">';
                html += '<div class="de-kpi-value">' + _esc(String(m.value || m)) + '</div>';
                html += '<div class="de-kpi-label">' + _esc(m.label || m.name || '') + '</div>';
                html += '</div>';
            });
        } else if (typeof content === 'object') {
            Object.keys(content).forEach(function (key) {
                html += '<div class="de-kpi-card">';
                html += '<div class="de-kpi-value">' + _esc(String(content[key])) + '</div>';
                html += '<div class="de-kpi-label">' + _esc(key) + '</div>';
                html += '</div>';
            });
        }

        html += '</div>';
        return html;
    }

    function _renderCodeBlock(code, language) {
        var langClass = language ? 'language-' + language : '';
        var codeId = 'de-code-' + Date.now();
        var html = '<div class="de-code-block">';
        html += '<button class="de-code-copy" onclick="DataExplorer.copyCode(\'' + codeId + '\')">Copy</button>';
        html += '<pre><code id="' + codeId + '" class="' + langClass + '">' + _esc(String(code)) + '</code></pre>';
        html += '</div>';

        // Highlight after DOM insertion
        setTimeout(function () {
            var el = document.getElementById(codeId);
            if (el && window.Prism) Prism.highlightElement(el);
        }, 50);

        return html;
    }

    function _renderList(content) {
        var html = '<ul style="margin:8px 0;padding-left:20px;line-height:1.8;">';
        if (Array.isArray(content)) {
            content.forEach(function (item) {
                html += '<li>' + _esc(String(item)) + '</li>';
            });
        } else {
            html += '<li>' + _esc(String(content)) + '</li>';
        }
        html += '</ul>';
        return html;
    }

    function _buildSqlToggle(query, queryId) {
        var id = 'sql-' + (queryId || Date.now());
        var html = '<button class="de-sql-toggle" onclick="document.getElementById(\'' + id + '\').style.display = document.getElementById(\'' + id + '\').style.display === \'none\' ? \'block\' : \'none\'">';
        html += '<i class="fas fa-code"></i> Show Query</button>';
        html += '<div id="' + id + '" style="display:none;">';
        html += _renderCodeBlock(query, 'sql');
        html += '</div>';
        return html;
    }

    /* ── Chat DOM helpers ──────────────────────────────────── */

    function _addChatMessage(role, content, isPlaceholder) {
        var container = document.getElementById('chatMessages');
        if (!container) return null;

        var msgDiv = document.createElement('div');
        msgDiv.className = 'de-msg de-msg-' + (role === 'user' ? 'user' : 'ai');

        var avatarIcon = role === 'user' ? 'fa-user' : 'fa-robot';
        msgDiv.innerHTML =
            '<div class="de-msg-avatar"><i class="fas ' + avatarIcon + '"></i></div>' +
            '<div class="de-msg-body">' +
            (isPlaceholder ? _skeletonHtml() : _esc(content)) +
            '</div>';

        container.appendChild(msgDiv);
        _scrollChatToBottom();
        return msgDiv;
    }

    function _setMessageContent(msgEl, html) {
        if (!msgEl) return;
        var body = msgEl.querySelector('.de-msg-body');
        if (body) body.innerHTML = html;
    }

    function _skeletonHtml() {
        return '<div class="de-skeleton-loading">' +
            // Phase 1: Typing indicator with animated dots
            '<div class="de-typing-indicator">' +
            '<div class="de-typing-dot"></div>' +
            '<div class="de-typing-dot"></div>' +
            '<div class="de-typing-dot"></div>' +
            '</div>' +
            // Phase 2: Skeleton content (fades in after delay)
            '<div class="de-skeleton-content">' +
            // Text skeleton lines
            '<div class="de-skeleton-text">' +
            '<div class="de-skeleton-bar" style="width:85%"></div>' +
            '<div class="de-skeleton-bar" style="width:65%"></div>' +
            '</div>' +
            // Table skeleton
            '<div class="de-skeleton-table">' +
            '<div class="de-skeleton-table-header">' +
            '<div class="de-skeleton-bar de-skeleton-cell"></div>' +
            '<div class="de-skeleton-bar de-skeleton-cell"></div>' +
            '<div class="de-skeleton-bar de-skeleton-cell"></div>' +
            '</div>' +
            '<div class="de-skeleton-table-row">' +
            '<div class="de-skeleton-bar de-skeleton-cell"></div>' +
            '<div class="de-skeleton-bar de-skeleton-cell"></div>' +
            '<div class="de-skeleton-bar de-skeleton-cell"></div>' +
            '</div>' +
            '<div class="de-skeleton-table-row">' +
            '<div class="de-skeleton-bar de-skeleton-cell"></div>' +
            '<div class="de-skeleton-bar de-skeleton-cell"></div>' +
            '<div class="de-skeleton-bar de-skeleton-cell"></div>' +
            '</div>' +
            '<div class="de-skeleton-table-row">' +
            '<div class="de-skeleton-bar de-skeleton-cell"></div>' +
            '<div class="de-skeleton-bar de-skeleton-cell"></div>' +
            '<div class="de-skeleton-bar de-skeleton-cell"></div>' +
            '</div>' +
            '</div>' +
            // Insight text skeleton
            '<div class="de-skeleton-insight">' +
            '<div class="de-skeleton-bar" style="width:90%"></div>' +
            '<div class="de-skeleton-bar" style="width:70%"></div>' +
            '</div>' +
            '</div>' +
            '</div>';
    }

    function _scrollChatToBottom() {
        var chat = document.getElementById('chatArea');
        if (chat) {
            setTimeout(function () { chat.scrollTop = chat.scrollHeight; }, 50);
        }
    }

    /* ── Status Indicator ──────────────────────────────────── */

    function _showStatus(label) {
        var el = document.getElementById('statusIndicator');
        var labelEl = document.getElementById('statusLabel');
        if (el) el.style.display = 'flex';
        if (labelEl) labelEl.textContent = label;
    }

    function _hideStatus() {
        var el = document.getElementById('statusIndicator');
        if (el) el.style.display = 'none';
    }

    /* ── Theme ─────────────────────────────────────────────── */

    function toggleTheme() {
        var page = document.getElementById('explorerPage');
        var isLight = page.classList.toggle('light-mode');
        localStorage.setItem('de-theme', isLight ? 'light' : 'dark');
        _updateThemeButton(isLight);

        // Re-render charts with new theme
        if (window.DEChartRenderer) DEChartRenderer.refreshAll();
    }

    function _updateThemeButton(isLight) {
        var icon = document.getElementById('themeIcon');
        var label = document.getElementById('themeLabel');
        if (icon) icon.className = isLight ? 'fas fa-sun' : 'fas fa-moon';
        if (label) label.textContent = isLight ? 'Light Mode' : 'Dark Mode';
    }

    /* ── Panel (slide-out detail view) ─────────────────────── */

    function openPanel(title, bodyHtml, actions) {
        var panel = document.getElementById('detailPanel');
        var overlay = document.getElementById('panelOverlay');
        var titleEl = document.getElementById('panelTitle');
        var bodyEl = document.getElementById('panelBody');
        var actionsEl = document.getElementById('panelActions');

        if (titleEl) titleEl.textContent = title;
        if (bodyEl) bodyEl.innerHTML = bodyHtml;

        if (actionsEl) {
            actionsEl.innerHTML = '';
            if (actions) {
                actions.forEach(function (action) {
                    var btn = document.createElement('button');
                    btn.className = action.className || 'de-btn de-btn-sm de-btn-ghost';
                    btn.innerHTML = action.label;
                    btn.onclick = action.onClick;
                    actionsEl.appendChild(btn);
                });
            }
        }

        if (panel) panel.classList.add('open');
        if (overlay) overlay.classList.add('visible');
    }

    function closePanel() {
        var panel = document.getElementById('detailPanel');
        var overlay = document.getElementById('panelOverlay');
        if (panel) panel.classList.remove('open');
        if (overlay) overlay.classList.remove('visible');
    }

    /* ── Dashboard Slide-out Panel ────────────────────────── */

    var _dashPanelOpen = false;
    var _activeDashboard = { id: null, title: null }; // tracks which dashboard pins go to

    function _setActiveDashboard(id, title) {
        _activeDashboard.id = id;
        _activeDashboard.title = title || 'Dashboard';
        // Update sidebar active indicator
        var items = document.querySelectorAll('.de-saved-item');
        items.forEach(function (el) { el.classList.remove('de-active'); });
        if (id) {
            items.forEach(function (el) {
                if (el.dataset.dashId === id) el.classList.add('de-active');
            });
        } else {
            // Unsaved — mark the virtual item
            items.forEach(function (el) {
                if (el.dataset.dashId === '__unsaved__') el.classList.add('de-active');
            });
        }
        // Update pin button labels
        _updatePinButtonLabels();
        // Update panel title
        var titleEl = document.getElementById('dashboardTitleText');
        if (titleEl && title) titleEl.textContent = title;
    }

    function _getActiveDashboardLabel() {
        if (_activeDashboard.title) {
            var t = _activeDashboard.title;
            return t.length > 18 ? t.substring(0, 18) + '…' : t;
        }
        return 'Dashboard';
    }

    function _updatePinButtonLabels() {
        var label = _getActiveDashboardLabel();
        var btns = document.querySelectorAll('.de-pin-btn-label');
        btns.forEach(function (el) { el.textContent = label; });
    }

    function toggleDashboardPanel() {
        _dashPanelOpen = !_dashPanelOpen;
        var panel = document.getElementById('dashPanel');
        var overlay = document.getElementById('dashOverlay');
        var tabBtn = document.querySelector('.de-dash-tab-btn');
        
        if (_dashPanelOpen) {
            if (panel) panel.classList.add('open');
            if (overlay) overlay.classList.add('open');
            if (tabBtn) tabBtn.classList.add('active');
        } else {
            if (panel) panel.classList.remove('open');
            if (overlay) overlay.classList.remove('open');
            if (tabBtn) tabBtn.classList.remove('active');
        }
    }

    function _showDashTabStrip() {
        // No longer needed — panel opens from sidebar
    }

    function newDashboard() {
        if (window.DEDashboard) {
            DEDashboard.clearAll();
            DEDashboard.setDashboardId(null);
        }
        _setActiveDashboard(null, 'Untitled Dashboard');
        // Don't open the panel — just add to list and make active
        // The unsaved "Untitled" shows as a virtual item in the sidebar
        _loadSavedDashboards();
        _showToast('New dashboard created — pin items to get started!');
    }

    function collapseDashboard() {
        toggleDashboardPanel();
    }

    /* ── Dashboard Actions ─────────────────────────────────── */

    function clearDashboard() {
        if (window.DEDashboard) DEDashboard.clearAll();
        if (_dashPanelOpen) toggleDashboardPanel();
    }

    async function refreshDashboard() {
        if (!window.DEDashboard) return;
        _showToast('Refreshing dashboard...');
        var result = await DEDashboard.refreshAll();
        _showToast('Refreshed ' + (result.refreshed || 0) + ' of ' + (result.total || 0) + ' widgets.');
    }

    function saveDashboard() {
        // If dashboard already has a saved ID, save directly without prompting
        var existingId = window.DEDashboard ? DEDashboard.getDashboardId() : null;
        if (existingId && _activeDashboard.title) {
            _quickSaveDashboard(existingId, _activeDashboard.title);
            return;
        }

        // New/unsaved dashboard — show name dialog
        var modal = document.getElementById('saveDashboardModal');
        if (modal) modal.style.display = 'flex';

        var input = document.getElementById('dashboardNameInput');
        if (input) input.focus();
    }

    async function _quickSaveDashboard(dashboardId, title) {
        if (!window.DEDashboard) return;
        var layout = DEDashboard.serialize();
        try {
            var resp = await fetch('/data_explorer/dashboard/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: title,
                    layout: layout,
                    dashboard_id: dashboardId
                })
            });
            var data = await resp.json();
            if (data.dashboard_id) {
                _showToast('Dashboard saved!');
                _loadSavedDashboards();
            }
        } catch (err) {
            _showToast('Failed to save dashboard.');
            console.error(err);
        }
    }

    function closeSaveModal() {
        var modal = document.getElementById('saveDashboardModal');
        if (modal) modal.style.display = 'none';
    }

    async function confirmSaveDashboard() {
        var input = document.getElementById('dashboardNameInput');
        var title = (input ? input.value : '').trim() || 'Untitled Dashboard';

        if (!window.DEDashboard) return;

        var layout = DEDashboard.serialize();

        try {
            var resp = await fetch('/data_explorer/dashboard/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: title,
                    layout: layout,
                    dashboard_id: DEDashboard.getDashboardId()
                })
            });
            var data = await resp.json();
            if (data.dashboard_id) {
                DEDashboard.setDashboardId(data.dashboard_id);
                _setActiveDashboard(data.dashboard_id, title);
                _showToast('Dashboard saved!');
                _loadSavedDashboards();
            }
        } catch (err) {
            _showToast('Failed to save dashboard.');
            console.error(err);
        }

        closeSaveModal();
    }

    async function _loadSavedDashboards() {
        try {
            var resp = await fetch('/data_explorer/dashboard/list');
            var data = await resp.json();
            var list = document.getElementById('savedDashboardsList');
            if (!list) return;

            var dashboards = data.dashboards || [];
            list.innerHTML = '';

            // Show unsaved "Untitled" virtual item if active dashboard has no saved ID
            if (_activeDashboard.title && !_activeDashboard.id) {
                var unsaved = document.createElement('div');
                unsaved.className = 'de-saved-item de-active';
                unsaved.dataset.dashId = '__unsaved__';
                unsaved.innerHTML =
                    '<span class="de-saved-item-icon"><i class="fas fa-th-large"></i></span>' +
                    '<span class="de-saved-item-name" title="' + _esc(_activeDashboard.title) + '">' + _esc(_activeDashboard.title) + '</span>' +
                    '<span class="de-saved-item-open"><i class="fas fa-external-link-alt"></i></span>';
                unsaved.onclick = function () { if (!_dashPanelOpen) toggleDashboardPanel(); };
                _attachRenameBehavior(unsaved, null, _activeDashboard.title);
                list.appendChild(unsaved);
            }

            if (dashboards.length === 0 && !_activeDashboard.title) {
                list.innerHTML = '<div class="de-saved-empty">No saved dashboards yet.<br>Pin a result to get started!</div>';
                return;
            }

            dashboards.forEach(function (db) {
                var isActive = _activeDashboard.id === db.id;
                var item = document.createElement('div');
                item.className = 'de-saved-item' + (isActive ? ' de-active' : '');
                item.dataset.dashId = db.id;
                item.innerHTML =
                    '<span class="de-saved-item-icon"><i class="fas fa-th-large"></i></span>' +
                    '<span class="de-saved-item-name" title="' + _esc(db.title) + '">' + _esc(db.title) + '</span>' +
                    '<span class="de-saved-item-open"><i class="fas fa-external-link-alt"></i></span>' +
                    '<button class="de-btn-icon de-saved-item-delete" onclick="event.stopPropagation(); DataExplorer.deleteDashboard(\'' + db.id + '\')" title="Delete"><i class="fas fa-trash-alt"></i></button>';
                item.onclick = function () { _loadDashboard(db.id); };
                _attachRenameBehavior(item, db.id, db.title);
                list.appendChild(item);
            });

            // Auto-select first dashboard if nothing is active
            if (!_activeDashboard.id && !_activeDashboard.title && dashboards.length > 0) {
                var first = dashboards[0];
                _activeDashboard.id = first.id;
                _activeDashboard.title = first.title;
                _updatePinButtonLabels();
                var firstEl = list.querySelector('.de-saved-item');
                if (firstEl) firstEl.classList.add('de-active');
            }
        } catch (err) {
            console.warn('Could not load dashboards:', err);
        }
    }

    /* ── Inline Rename (double-click) ──────────────────────── */

    function _attachRenameBehavior(itemEl, dashId, currentTitle) {
        var nameEl = itemEl.querySelector('.de-saved-item-name');
        if (!nameEl) return;
        nameEl.addEventListener('dblclick', function (e) {
            e.stopPropagation();
            var input = document.createElement('input');
            input.type = 'text';
            input.className = 'de-saved-item-rename';
            input.value = currentTitle;
            input.maxLength = 60;
            nameEl.replaceWith(input);
            input.focus();
            input.select();

            function _commitRename() {
                var newTitle = input.value.trim() || currentTitle;
                var span = document.createElement('span');
                span.className = 'de-saved-item-name';
                span.title = newTitle;
                span.textContent = newTitle;
                input.replaceWith(span);
                _attachRenameBehavior(itemEl, dashId, newTitle);

                // Update active dashboard title if this is the active one
                if ((!dashId && !_activeDashboard.id) || (dashId && _activeDashboard.id === dashId)) {
                    _activeDashboard.title = newTitle;
                    _updatePinButtonLabels();
                    var panelTitle = document.getElementById('dashboardTitleText');
                    if (panelTitle) panelTitle.textContent = newTitle;
                }

                // Persist rename to backend if saved dashboard
                if (dashId) {
                    fetch('/data_explorer/dashboard/' + dashId + '/rename', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title: newTitle })
                    }).catch(function () { /* silent */ });
                }
            }

            input.addEventListener('blur', _commitRename);
            input.addEventListener('keydown', function (ev) {
                if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
                if (ev.key === 'Escape') { input.value = currentTitle; input.blur(); }
            });
        });
    }

    async function _loadDashboard(dashboardId) {
        try {
            var resp = await fetch('/data_explorer/dashboard/' + dashboardId);
            var data = await resp.json();
            if (data.layout && window.DEDashboard) {
                DEDashboard.deserialize(data.layout);
                DEDashboard.setDashboardId(dashboardId);

                // Set as active dashboard
                _setActiveDashboard(dashboardId, data.title || 'Dashboard');

                // Open the slide-out panel
                if (!_dashPanelOpen) toggleDashboardPanel();

                _showToast('Loaded: ' + (data.title || 'Dashboard'));
            }
        } catch (err) {
            _showToast('Failed to load dashboard.');
            console.error(err);
        }
    }

    async function deleteDashboard(dashboardId) {
        try {
            await fetch('/data_explorer/dashboard/' + dashboardId, { method: 'DELETE' });
            _showToast('Dashboard deleted.');
            _loadSavedDashboards();
        } catch (err) {
            _showToast('Failed to delete.');
        }
    }

    function toggleDashboardEdit() {
        if (window.DEDashboard) DEDashboard.toggleEditMode();
    }

    function collapseDashboard() {
        var area = document.getElementById('dashboardArea');
        if (area) area.classList.toggle('collapsed');
    }

    /* ── Pin Result to Dashboard ───────────────────────────── */

    async function pinResultToDashboard(queryId, pinType) {
        var qr = _queryRegistry[queryId];
        if (!qr || !window.DEDashboard) return;

        // Auto-create dashboard if none active
        if (!_activeDashboard.title && !_activeDashboard.id) {
            DEDashboard.clearAll();
            DEDashboard.setDashboardId(null);
            _setActiveDashboard(null, 'Untitled Dashboard');
            _loadSavedDashboards();
            _showToast('📊 Created new dashboard with your first pin!');
        }
        // Load existing dashboard if selected but not yet loaded into grid
        else if (_activeDashboard.id && DEDashboard.getDashboardId() !== _activeDashboard.id) {
            await _loadDashboard(_activeDashboard.id);
        }

        pinType = pinType || 'table';
        var dashLabel = _getActiveDashboardLabel();

        if (pinType === 'chart' && qr.chartImage) {
            DEDashboard.addWidget('image', {
                title: 'Chart — Query ' + queryId,
                queryId: queryId,
                sql: qr.sql,
                agentId: qr.agentId,
                src: qr.chartImage
            });
            _showToast('Chart pinned to "' + dashLabel + '"');
        } else if (qr.data) {
            DEDashboard.addWidget('table', {
                title: 'Query ' + queryId,
                queryId: queryId,
                sql: qr.sql,
                agentId: qr.agentId,
                data: qr.data
            });
            _showToast('Table pinned to "' + dashLabel + '"');
        }

        // Auto-open dashboard panel when pinning
        if (!_dashPanelOpen) toggleDashboardPanel();
    }

    /* ── Session Reset ─────────────────────────────────────── */

    async function resetSession() {
        try {
            await fetch('/data_explorer/reset', { method: 'POST' });
            _conversationHistory = [];
            _queryRegistry = {};
            document.getElementById('chatMessages').innerHTML = '';
            document.getElementById('welcomeState').style.display = '';
            _showToast('Session reset.');
        } catch (err) {
            console.error('Reset failed:', err);
        }
    }

    /* ── Suggestion Chips ──────────────────────────────────── */

    function askSuggestion(btn) {
        var text = btn.textContent.trim();
        var textarea = document.getElementById('userInput');
        if (textarea) {
            textarea.value = text;
            sendMessage();
        }
    }

    /* ── Copy Code ─────────────────────────────────────────── */

    function copyCode(codeId) {
        var el = document.getElementById(codeId);
        if (!el) return;
        navigator.clipboard.writeText(el.textContent).then(function () {
            _showToast('Copied to clipboard!');
        });
    }

    /* ── Toast Notification ────────────────────────────────── */

    function _showToast(message) {
        // Simple toast — auto-disappears
        var existing = document.querySelector('.de-toast');
        if (existing) existing.remove();

        var toast = document.createElement('div');
        toast.className = 'de-toast';
        toast.textContent = message;
        toast.style.cssText =
            'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);' +
            'background:var(--de-card);color:var(--de-text);' +
            'border:1px solid var(--de-border);border-radius:10px;' +
            'padding:10px 20px;font-size:13px;z-index:200;' +
            'box-shadow:var(--de-shadow);animation:deFadeIn 0.2s ease;';
        document.body.appendChild(toast);
        setTimeout(function () { toast.remove(); }, 3000);
    }

    /* ── Helpers ────────────────────────────────────────────── */

    function _esc(str) {
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    /* ── Expose Public API ─────────────────────────────────── */

    window.DataExplorer = {
        init: init,
        sendMessage: sendMessage,
        toggleTheme: toggleTheme,
        openPanel: openPanel,
        closePanel: closePanel,
        newDashboard: newDashboard,
        clearDashboard: clearDashboard,
        refreshDashboard: refreshDashboard,
        saveDashboard: saveDashboard,
        closeSaveModal: closeSaveModal,
        confirmSaveDashboard: confirmSaveDashboard,
        deleteDashboard: deleteDashboard,
        toggleDashboardEdit: toggleDashboardEdit,
        toggleDashboardPanel: toggleDashboardPanel,
        collapseDashboard: collapseDashboard,
        pinResultToDashboard: pinResultToDashboard,
        resetSession: resetSession,
        askSuggestion: askSuggestion,
        copyCode: copyCode,
    };

    // Auto-init on DOMContentLoaded
    document.addEventListener('DOMContentLoaded', init);
})();
