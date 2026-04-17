/**
 * Data Explorer v2 — Chart Renderer
 * ====================================
 * Standalone chart module using Chart.js.
 * Theme-aware, supports type switching, expand-to-panel, and dashboard pinning.
 * Ported from chat-charts.js — does NOT modify the original.
 */
(function () {
    'use strict';

    // Color palette
    var COLORS = [
        '#3b82f6', '#06b6d4', '#a78bfa', '#34d399', '#fbbf24',
        '#fb7185', '#f97316', '#8b5cf6', '#14b8a6', '#ec4899',
        '#6366f1', '#84cc16', '#f43e5c', '#22d3ee', '#e879f9'
    ];

    var _chartCounter = 0;
    var _chartInstances = {}; // chartId -> Chart instance
    var _chartConfigs = {};   // chartId -> original config data

    /* ── Theme helpers ─────────────────────────────────────── */

    function isLightMode() {
        var page = document.getElementById('explorerPage');
        return page && page.classList.contains('light-mode');
    }

    function themeColors() {
        var light = isLightMode();
        return {
            gridColor: light ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.06)',
            textColor: light ? '#4b5563' : '#9ca3af',
            tooltipBg: light ? '#ffffff' : '#1f2937',
            tooltipText: light ? '#111827' : '#f9fafb',
            tooltipBorder: light ? '#e5e7eb' : '#374151',
        };
    }

    /* ── Merge theme defaults into config ──────────────────── */

    function mergeThemeDefaults(config, opts) {
        opts = opts || {};
        var tc = themeColors();
        var chartType = (config.type || opts.chartType || 'bar').toLowerCase();

        // Clone config
        var merged = JSON.parse(JSON.stringify(config));

        // Ensure data.datasets exist
        if (!merged.data) merged.data = {};
        if (!merged.data.datasets) merged.data.datasets = [];

        // Assign colors to datasets
        merged.data.datasets.forEach(function (ds, i) {
            var color = COLORS[i % COLORS.length];
            if (!ds.backgroundColor) {
                if (chartType === 'line') {
                    ds.backgroundColor = color + '20';
                    ds.borderColor = ds.borderColor || color;
                    ds.borderWidth = ds.borderWidth || 2;
                    ds.tension = ds.tension || 0.3;
                    ds.pointRadius = ds.pointRadius || 3;
                    ds.fill = ds.fill !== undefined ? ds.fill : true;
                } else if (chartType === 'pie' || chartType === 'doughnut') {
                    ds.backgroundColor = merged.data.labels
                        ? merged.data.labels.map(function (_, j) { return COLORS[j % COLORS.length]; })
                        : COLORS.slice(0, (ds.data || []).length);
                    ds.borderColor = isLightMode() ? '#ffffff' : '#111827';
                    ds.borderWidth = 2;
                } else {
                    ds.backgroundColor = color + 'cc';
                    ds.borderColor = color;
                    ds.borderWidth = 1;
                    ds.borderRadius = 4;
                }
            }
        });

        // Options
        if (!merged.options) merged.options = {};
        merged.options.responsive = true;
        merged.options.maintainAspectRatio = false;
        merged.options.animation = { duration: 500, easing: 'easeOutQuart' };

        // Plugins
        if (!merged.options.plugins) merged.options.plugins = {};

        // Legend
        merged.options.plugins.legend = Object.assign({
            display: merged.data.datasets.length > 1 || chartType === 'pie' || chartType === 'doughnut',
            position: 'top',
            labels: { color: tc.textColor, font: { family: "'Outfit', sans-serif", size: 12 }, padding: 12, usePointStyle: true }
        }, merged.options.plugins.legend || {});

        // Tooltip
        merged.options.plugins.tooltip = Object.assign({
            backgroundColor: tc.tooltipBg,
            titleColor: tc.tooltipText,
            bodyColor: tc.tooltipText,
            borderColor: tc.tooltipBorder,
            borderWidth: 1,
            cornerRadius: 8,
            padding: 10,
            titleFont: { family: "'Outfit', sans-serif", weight: '600' },
            bodyFont: { family: "'Outfit', sans-serif" }
        }, merged.options.plugins.tooltip || {});

        // Title
        if (opts.title) {
            merged.options.plugins.title = {
                display: true,
                text: opts.title,
                color: tc.textColor,
                font: { family: "'Outfit', sans-serif", size: 14, weight: '600' },
                padding: { bottom: 12 }
            };
        }

        // Scales (skip for pie/doughnut)
        if (chartType !== 'pie' && chartType !== 'doughnut') {
            if (!merged.options.scales) merged.options.scales = {};
            ['x', 'y'].forEach(function (axis) {
                if (!merged.options.scales[axis]) merged.options.scales[axis] = {};
                var s = merged.options.scales[axis];
                if (!s.ticks) s.ticks = {};
                s.ticks.color = tc.textColor;
                s.ticks.font = { family: "'Outfit', sans-serif", size: 11 };
                if (!s.grid) s.grid = {};
                s.grid.color = tc.gridColor;
                s.grid.drawBorder = false;
            });
        }

        // Set type
        merged.type = chartType;

        return merged;
    }

    /* ── Render a chart ────────────────────────────────────── */

    /**
     * Render a chart into an HTML container.
     * @param {Object} config - Chart.js-compatible config { type, data, options }
     * @param {Object} opts - { title, chartId, pinnable, onPin, containerEl }
     * @returns {string|HTMLElement} HTML string if no containerEl, else appends to containerEl
     */
    function renderChart(config, opts) {
        opts = opts || {};
        var chartId = opts.chartId || 'de-chart-' + (++_chartCounter);
        var chartType = (config.type || 'bar').toLowerCase();

        // Store the original config for type switching and pinning
        _chartConfigs[chartId] = { original: JSON.parse(JSON.stringify(config)), opts: opts };

        var merged = mergeThemeDefaults(config, { title: opts.title, chartType: chartType });

        // Build HTML
        var html = '<div class="de-chart-container" id="' + chartId + '-wrap">';

        // Chart type switcher
        html += '<div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:8px;">';
        html += '<div class="de-chart-type-switcher">';
        ['bar', 'line', 'pie', 'doughnut'].forEach(function (t) {
            var active = t === chartType ? ' active' : '';
            html += '<button class="de-chart-type-btn' + active + '" onclick="DEChartRenderer.switchType(\'' + chartId + '\', \'' + t + '\')">' + t.charAt(0).toUpperCase() + t.slice(1) + '</button>';
        });
        html += '</div>';

        html += '<div style="display:flex;gap:4px;">';
        html += '<button class="de-btn-icon" onclick="DEChartRenderer.expandChart(\'' + chartId + '\')" title="Expand"><i class="fas fa-expand"></i></button>';
        if (opts.pinnable !== false) {
            html += '<button class="de-btn-icon" onclick="DEChartRenderer.pinChart(\'' + chartId + '\')" title="Pin to Dashboard"><i class="fas fa-thumbtack"></i></button>';
        }
        html += '</div></div>';

        html += '<canvas id="' + chartId + '-canvas" style="width:100%;height:280px;"></canvas>';
        html += '</div>';

        if (opts.containerEl) {
            opts.containerEl.innerHTML = html;
            _initChart(chartId, merged);
            return opts.containerEl;
        }

        // Deferred init — caller must insert HTML then call initPending()
        setTimeout(function () { _initChart(chartId, merged); }, 50);
        return html;
    }

    /**
     * Render a chart image (base64 or URL)
     */
    function renderChartImage(src, opts) {
        opts = opts || {};
        var html = '<div class="de-chart-container">';
        html += '<img src="' + src + '" style="max-width:100%;border-radius:8px;" alt="' + (opts.title || 'Chart') + '" />';
        if (opts.pinnable !== false) {
            html += '<div style="margin-top:8px;"><button class="de-msg-action-btn" onclick="DEChartRenderer.pinImage(\'' + _esc(src) + '\', \'' + _esc(opts.title || 'Chart') + '\')"><i class="fas fa-thumbtack"></i> Pin to Dashboard</button></div>';
        }
        html += '</div>';
        return html;
    }

    /* ── Init Chart.js instance ────────────────────────────── */

    function _initChart(chartId, config) {
        var canvas = document.getElementById(chartId + '-canvas');
        if (!canvas) return;

        // Destroy existing
        if (_chartInstances[chartId]) {
            _chartInstances[chartId].destroy();
        }

        var ctx = canvas.getContext('2d');
        _chartInstances[chartId] = new Chart(ctx, config);
    }

    /* ── Switch chart type ─────────────────────────────────── */

    function switchType(chartId, newType) {
        var stored = _chartConfigs[chartId];
        if (!stored) return;

        var config = JSON.parse(JSON.stringify(stored.original));
        config.type = newType;
        stored.original.type = newType;

        var merged = mergeThemeDefaults(config, { title: stored.opts.title, chartType: newType });
        _initChart(chartId, merged);

        // Update active buttons
        var wrap = document.getElementById(chartId + '-wrap');
        if (wrap) {
            wrap.querySelectorAll('.de-chart-type-btn').forEach(function (btn) {
                btn.classList.toggle('active', btn.textContent.toLowerCase() === newType);
            });
        }
    }

    /* ── Expand chart in panel ─────────────────────────────── */

    function expandChart(chartId) {
        var stored = _chartConfigs[chartId];
        if (!stored) return;

        if (window.DataExplorer) {
            var config = JSON.parse(JSON.stringify(stored.original));
            var merged = mergeThemeDefaults(config, { title: stored.opts.title, chartType: config.type });

            var panelHtml = '<canvas id="panel-chart-canvas" style="width:100%;height:400px;"></canvas>';
            window.DataExplorer.openPanel(stored.opts.title || 'Chart', panelHtml, [
                {
                    label: '<i class="fas fa-download"></i> Download PNG',
                    className: 'de-btn de-btn-sm de-btn-ghost',
                    onClick: function () {
                        var c = document.getElementById('panel-chart-canvas');
                        if (!c) return;
                        var link = document.createElement('a');
                        link.download = (stored.opts.title || 'chart') + '.png';
                        link.href = c.toDataURL('image/png');
                        link.click();
                    }
                }
            ]);

            setTimeout(function () {
                var panelCanvas = document.getElementById('panel-chart-canvas');
                if (panelCanvas) {
                    new Chart(panelCanvas.getContext('2d'), merged);
                }
            }, 100);
        }
    }

    /* ── Pin to dashboard ──────────────────────────────────── */

    function pinChart(chartId) {
        var stored = _chartConfigs[chartId];
        if (!stored) return;

        if (window.DEDashboard) {
            window.DEDashboard.addWidget('chart', {
                title: stored.opts.title || 'Chart',
                chartId: chartId,
                config: JSON.parse(JSON.stringify(stored.original))
            });
        }
    }

    function pinImage(src, title) {
        if (window.DEDashboard) {
            window.DEDashboard.addWidget('image', { title: title, src: src });
        }
    }

    /* ── Re-render all charts (theme change) ───────────────── */

    function refreshAllCharts() {
        Object.keys(_chartConfigs).forEach(function (chartId) {
            var stored = _chartConfigs[chartId];
            if (!stored) return;
            var config = JSON.parse(JSON.stringify(stored.original));
            var merged = mergeThemeDefaults(config, { title: stored.opts.title, chartType: config.type });
            _initChart(chartId, merged);
        });
    }

    /* ── Helpers ────────────────────────────────────────────── */

    function _esc(str) {
        return String(str).replace(/'/g, "\\'").replace(/"/g, '&quot;');
    }

    /* ── Expose ────────────────────────────────────────────── */

    window.DEChartRenderer = {
        render: renderChart,
        renderImage: renderChartImage,
        switchType: switchType,
        expandChart: expandChart,
        pinChart: pinChart,
        pinImage: pinImage,
        refreshAll: refreshAllCharts,
        mergeThemeDefaults: mergeThemeDefaults,
        _instances: _chartInstances,
        _configs: _chartConfigs,
        COLORS: COLORS
    };
})();
