/**
 * Data Explorer v2 — Dashboard Manager
 * =======================================
 * Gridstack.js integration for drag/drop/resize dashboard widgets.
 * Each widget stores its query_id, type, and config for save/load/refresh.
 */
(function () {
    'use strict';

    var _grid = null;
    var _widgets = {};     // widgetId -> { type, title, queryId, sql, agentId, config, data }
    var _widgetCounter = 0;
    var _editMode = false;
    var _dashboardId = null; // Set when loading a saved dashboard

    /* ── Initialize Gridstack ──────────────────────────────── */

    function init() {
        var el = document.getElementById('dashboardGrid');
        if (!el) return;

        _grid = GridStack.init({
            column: 12,
            cellHeight: 100,
            margin: 10,
            float: false,
            animate: true,
            disableResize: false,
            disableDrag: false,
            removable: false,
            acceptWidgets: false,
            minRow: 1,
            handle: '.de-widget-header',
            resizable: { handles: 'se,sw,e,w' },
        }, el);

        // When widgets move/resize, we could auto-save state here
        _grid.on('change', function () {
            // Could trigger auto-save in the future
        });
    }

    /* ── Add Widget ────────────────────────────────────────── */

    /**
     * Add a widget to the dashboard.
     * @param {string} type - 'table', 'chart', 'image', 'kpi', 'text'
     * @param {Object} opts - { title, queryId, sql, agentId, config, data, tableId, chartId }
     */
    function addWidget(type, opts) {
        opts = opts || {};
        var widgetId = 'de-widget-' + (++_widgetCounter);

        // Default grid sizes per type (12-col grid)
        var sizes = {
            table:  { w: 12, h: 4, minW: 4, minH: 2 },
            chart:  { w: 6,  h: 4, minW: 3, minH: 3 },
            image:  { w: 6,  h: 4, minW: 3, minH: 3 },
            kpi:    { w: 3,  h: 2, minW: 2, minH: 2 },
            text:   { w: 6,  h: 2, minW: 3, minH: 2 },
        };
        var size = sizes[type] || { w: 6, h: 3, minW: 3, minH: 2 };

        // Store widget metadata
        _widgets[widgetId] = {
            type: type,
            title: opts.title || 'Widget',
            queryId: opts.queryId || null,
            sql: opts.sql || null,
            agentId: opts.agentId || null,
            config: opts.config || null,
            data: opts.data || null,
            src: opts.src || null,
        };

        // Build widget content HTML
        var contentHtml = _buildWidgetContent(widgetId, type, opts);

        // Create the DOM element for Gridstack
        var itemEl = document.createElement('div');
        itemEl.className = 'grid-stack-item';
        itemEl.setAttribute('gs-w', size.w);
        itemEl.setAttribute('gs-h', size.h);
        if (size.minW) itemEl.setAttribute('gs-min-w', size.minW);
        if (size.minH) itemEl.setAttribute('gs-min-h', size.minH);
        itemEl.id = widgetId;

        var contentEl = document.createElement('div');
        contentEl.className = 'grid-stack-item-content';
        contentEl.innerHTML = contentHtml;
        itemEl.appendChild(contentEl);

        // Add to grid
        if (_grid) {
            _grid.addWidget(itemEl);
        }

        // Initialize charts inside the widget
        if (type === 'chart' && opts.config) {
            setTimeout(function () {
                _initWidgetChart(widgetId, opts.config, opts.title);
            }, 100);
        }

        // Show the dashboard toolbar
        _showToolbar();

        return widgetId;
    }

    /* ── Build widget content HTML ─────────────────────────── */

    function _buildWidgetContent(widgetId, type, opts) {
        var html = '';

        // Header
        html += '<div class="de-widget-header">';
        html += '<span class="de-widget-title" title="' + _esc(opts.title || '') + '">' + _esc(opts.title || 'Widget') + '</span>';
        html += '<div class="de-widget-actions">';
        if (type === 'chart') {
            html += '<button class="de-btn-icon" onclick="DEDashboard.expandWidget(\'' + widgetId + '\')" title="Expand"><i class="fas fa-expand"></i></button>';
        }
        html += '<button class="de-btn-icon" onclick="DEDashboard.removeWidget(\'' + widgetId + '\')" title="Remove"><i class="fas fa-times"></i></button>';
        html += '</div></div>';

        // Body
        html += '<div class="de-widget-body" id="' + widgetId + '-body">';

        if (type === 'table' && opts.data) {
            html += DETableRenderer.render(opts.data, {
                tableId: widgetId + '-tbl',
                title: opts.title,
                pinnable: false
            });
        } else if (type === 'chart') {
            html += '<canvas id="' + widgetId + '-canvas" style="width:100%;height:100%;"></canvas>';
        } else if (type === 'image' && opts.src) {
            html += '<img src="' + opts.src + '" style="width:100%;height:100%;object-fit:contain;border-radius:6px;" />';
        } else if (type === 'kpi') {
            html += _buildKpiHtml(opts);
        } else if (type === 'text') {
            html += '<div style="padding:8px;font-size:14px;line-height:1.6;color:var(--de-text);">' + (opts.content || '') + '</div>';
        } else {
            html += '<div class="de-table-info">No data</div>';
        }

        html += '</div>';
        return html;
    }

    function _buildKpiHtml(opts) {
        var html = '<div class="de-kpi-grid" style="padding:8px;">';
        if (opts.metrics && Array.isArray(opts.metrics)) {
            opts.metrics.forEach(function (m) {
                html += '<div class="de-kpi-card">';
                html += '<div class="de-kpi-value">' + _esc(String(m.value)) + '</div>';
                html += '<div class="de-kpi-label">' + _esc(m.label || '') + '</div>';
                html += '</div>';
            });
        }
        html += '</div>';
        return html;
    }

    function _initWidgetChart(widgetId, config, title) {
        var canvas = document.getElementById(widgetId + '-canvas');
        if (!canvas) return;

        var merged = DEChartRenderer.mergeThemeDefaults(
            JSON.parse(JSON.stringify(config)),
            { title: title, chartType: config.type || 'bar' }
        );

        // Remove title from widget charts (shown in header)
        if (merged.options && merged.options.plugins) {
            merged.options.plugins.title = { display: false };
        }

        new Chart(canvas.getContext('2d'), merged);
    }

    /* ── Remove Widget ─────────────────────────────────────── */

    function removeWidget(widgetId) {
        var el = document.getElementById(widgetId);
        if (el && _grid) {
            _grid.removeWidget(el);
        }
        delete _widgets[widgetId];

        // Hide toolbar if no widgets
        if (Object.keys(_widgets).length === 0) {
            _hideToolbar();
        }
    }

    /* ── Expand Widget (chart) ─────────────────────────────── */

    function expandWidget(widgetId) {
        var w = _widgets[widgetId];
        if (!w || w.type !== 'chart' || !w.config) return;

        if (window.DEChartRenderer && window.DataExplorer) {
            var merged = DEChartRenderer.mergeThemeDefaults(
                JSON.parse(JSON.stringify(w.config)),
                { title: w.title, chartType: w.config.type || 'bar' }
            );

            var panelHtml = '<canvas id="panel-widget-canvas" style="width:100%;height:400px;"></canvas>';
            window.DataExplorer.openPanel(w.title || 'Chart', panelHtml, [
                {
                    label: '<i class="fas fa-download"></i> Download PNG',
                    className: 'de-btn de-btn-sm de-btn-ghost',
                    onClick: function () {
                        var c = document.getElementById('panel-widget-canvas');
                        if (c) {
                            var link = document.createElement('a');
                            link.download = (w.title || 'chart') + '.png';
                            link.href = c.toDataURL('image/png');
                            link.click();
                        }
                    }
                }
            ]);

            setTimeout(function () {
                var c = document.getElementById('panel-widget-canvas');
                if (c) new Chart(c.getContext('2d'), merged);
            }, 100);
        }
    }

    /* ── Edit Mode (lock/unlock drag & resize) ─────────────── */

    function toggleEditMode() {
        _editMode = !_editMode;
        var gridEl = document.getElementById('dashboardGrid');
        var btn = document.getElementById('editModeBtn');

        if (_grid) {
            if (_editMode) {
                _grid.enableResize(true);
                _grid.enableMove(true);
            } else {
                _grid.enableResize(false);
                _grid.enableMove(false);
            }
        }

        if (gridEl) {
            gridEl.classList.toggle('editing', _editMode);
        }

        if (btn) {
            btn.innerHTML = _editMode
                ? '<i class="fas fa-unlock"></i> Editing'
                : '<i class="fas fa-lock"></i> Locked';
        }
    }

    /* ── Clear all widgets ─────────────────────────────────── */

    function clearAll() {
        if (_grid) {
            _grid.removeAll();
        }
        _widgets = {};
        _widgetCounter = 0;
        _dashboardId = null;
        _hideToolbar();
    }

    /* ── Serialize / Deserialize ───────────────────────────── */

    function serialize() {
        var layout = [];
        if (_grid) {
            var items = _grid.getGridItems();
            items.forEach(function (el) {
                var node = el.gridstackNode;
                var widgetId = el.id;
                var w = _widgets[widgetId];
                if (w && node) {
                    layout.push({
                        widgetId: widgetId,
                        type: w.type,
                        title: w.title,
                        queryId: w.queryId,
                        sql: w.sql,
                        agentId: w.agentId,
                        config: w.config,
                        data: w.data,
                        src: w.src,
                        position: { x: node.x, y: node.y, w: node.w, h: node.h }
                    });
                }
            });
        }
        return {
            dashboardId: _dashboardId,
            widgets: layout
        };
    }

    function deserialize(layoutData) {
        clearAll();
        if (!layoutData || !layoutData.widgets) return;

        _dashboardId = layoutData.dashboardId || null;

        layoutData.widgets.forEach(function (w) {
            var widgetId = addWidget(w.type, {
                title: w.title,
                queryId: w.queryId,
                sql: w.sql,
                agentId: w.agentId,
                config: w.config,
                data: w.data,
                src: w.src,
            });

            // Set position if grid supports it
            var el = document.getElementById(widgetId);
            if (el && _grid && w.position) {
                _grid.update(el, {
                    x: w.position.x,
                    y: w.position.y,
                    w: w.position.w,
                    h: w.position.h
                });
            }
        });
    }

    /* ── Refresh all queries ───────────────────────────────── */

    async function refreshAll() {
        var refreshPromises = [];

        Object.keys(_widgets).forEach(function (widgetId) {
            var w = _widgets[widgetId];
            if (w.sql && w.agentId && w.queryId && w.type === 'table') {
                refreshPromises.push(_refreshWidget(widgetId, w));
            }
        });

        if (refreshPromises.length === 0) {
            return { refreshed: 0, message: 'No refreshable widgets found.' };
        }

        var results = await Promise.allSettled(refreshPromises);
        var succeeded = results.filter(function (r) { return r.status === 'fulfilled'; }).length;
        return { refreshed: succeeded, total: refreshPromises.length };
    }

    async function _refreshWidget(widgetId, w) {
        try {
            var resp = await fetch('/data_explorer/refresh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sql: w.sql,
                    agent_id: w.agentId,
                    query_id: w.queryId
                })
            });
            var data = await resp.json();
            if (data.table_data) {
                w.data = data.table_data;
                var body = document.getElementById(widgetId + '-body');
                if (body) {
                    body.innerHTML = DETableRenderer.render(data.table_data, {
                        tableId: widgetId + '-tbl-r',
                        title: w.title,
                        pinnable: false
                    });
                }
            }
        } catch (err) {
            console.error('Failed to refresh widget ' + widgetId, err);
        }
    }

    /* ── Toolbar visibility ────────────────────────────────── */

    function _showToolbar() {
        // Show the tab strip on the right side
        var strip = document.getElementById('dashTabStrip');
        if (strip) strip.style.display = '';
    }

    function _hideToolbar() {
        var strip = document.getElementById('dashTabStrip');
        if (strip) strip.style.display = 'none';
    }

    /* ── Helpers ────────────────────────────────────────────── */

    function _esc(str) {
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function getWidgets() { return _widgets; }
    function getDashboardId() { return _dashboardId; }
    function setDashboardId(id) { _dashboardId = id; }
    function isEditMode() { return _editMode; }

    /* ── Expose ────────────────────────────────────────────── */

    window.DEDashboard = {
        init: init,
        addWidget: addWidget,
        removeWidget: removeWidget,
        expandWidget: expandWidget,
        toggleEditMode: toggleEditMode,
        clearAll: clearAll,
        serialize: serialize,
        deserialize: deserialize,
        refreshAll: refreshAll,
        getWidgets: getWidgets,
        getDashboardId: getDashboardId,
        setDashboardId: setDashboardId,
        isEditMode: isEditMode,
    };
})();
