/**
 * chat-charts.js — Chart.js wrapper with dark/light theme & panel expansion
 * Used by chat.html & data_chat.html
 */
(function () {
    'use strict';

    const DEFAULT_COLORS = [
        '#06b6d4', // cyan
        '#34d399', // emerald
        '#fbbf24', // amber
        '#a78bfa', // violet
        '#fb7185', // rose
        '#38bdf8', // sky
        '#f472b6', // pink
        '#fb923c'  // orange
    ];

    /**
     * Detect if we are in light mode.
     */
    function isLightMode() {
        return document.body.classList.contains('light-mode');
    }

    /**
     * Get theme-appropriate colors for grids, text, etc.
     */
    function themeColors() {
        var light = isLightMode();
        return {
            gridColor: light ? 'rgba(0,0,0,0.08)' : 'rgba(255,255,255,0.06)',
            textColor: light ? '#334155' : '#a1a1aa',
            tooltipBg: light ? '#ffffff' : '#18181b',
            tooltipText: light ? '#0f172a' : '#ffffff',
            tooltipBorder: light ? '#e2e8f0' : '#3f3f46'
        };
    }

    /**
     * Render a Chart.js chart into a container element.
     * @param {HTMLElement} containerEl - The element to render into
     * @param {object} config - Chart.js configuration object
     * @param {object} [opts] - Optional: { title, expandable }
     * @returns {Chart} The Chart.js instance
     */
    function render(containerEl, config, opts) {
        opts = opts || {};
        var tc = themeColors();

        // Wrapper
        var wrapper = document.createElement('div');
        wrapper.className = 'chart-wrapper-modern';
        wrapper.style.position = 'relative';

        // Title
        if (opts.title) {
            var titleEl = document.createElement('div');
            titleEl.style.cssText = 'font-size:0.85rem;font-weight:600;color:var(--text-secondary);margin-bottom:0.5rem;';
            titleEl.textContent = opts.title;
            wrapper.appendChild(titleEl);
        }

        // Canvas
        var canvasWrap = document.createElement('div');
        canvasWrap.style.cssText = 'position:relative;height:280px;';
        var canvas = document.createElement('canvas');
        canvasWrap.appendChild(canvas);
        wrapper.appendChild(canvasWrap);

        // Expand hint
        if (opts.expandable !== false) {
            var hint = document.createElement('div');
            hint.className = 'chart-expand-hint';
            hint.textContent = 'Click to expand';
            wrapper.appendChild(hint);
        }

        containerEl.appendChild(wrapper);

        // Merge theme defaults into config
        var mergedConfig = mergeThemeDefaults(config, tc);

        var chart = new Chart(canvas, mergedConfig);

        // Click to expand into panel
        if (opts.expandable !== false && window.ChatPanel) {
            wrapper.style.cursor = 'pointer';
            wrapper.addEventListener('click', function () {
                expandChartToPanel(mergedConfig, opts.title || 'Chart Detail');
            });
        }

        return chart;
    }

    /**
     * Merge theme-aware defaults into a Chart.js config.
     */
    function mergeThemeDefaults(config, tc) {
        config = JSON.parse(JSON.stringify(config)); // deep clone

        // Ensure datasets have colors
        if (config.data && config.data.datasets) {
            config.data.datasets.forEach(function (ds, i) {
                if (!ds.backgroundColor) {
                    ds.backgroundColor = DEFAULT_COLORS[i % DEFAULT_COLORS.length];
                }
                if (!ds.borderColor && (config.type === 'line' || config.type === 'area')) {
                    ds.borderColor = DEFAULT_COLORS[i % DEFAULT_COLORS.length];
                }
            });
        }

        // Options defaults
        config.options = config.options || {};
        config.options.responsive = true;
        config.options.maintainAspectRatio = false;
        config.options.animation = config.options.animation || {
            duration: 600,
            easing: 'easeOutQuart'
        };

        // Scales
        if (config.type !== 'pie' && config.type !== 'doughnut') {
            config.options.scales = config.options.scales || {};
            ['x', 'y'].forEach(function (axis) {
                config.options.scales[axis] = config.options.scales[axis] || {};
                config.options.scales[axis].ticks = config.options.scales[axis].ticks || {};
                config.options.scales[axis].ticks.color = tc.textColor;
                config.options.scales[axis].ticks.font = { family: 'Outfit, sans-serif', size: 11 };
                config.options.scales[axis].grid = config.options.scales[axis].grid || {};
                config.options.scales[axis].grid.color = tc.gridColor;
            });
        }

        // Tooltip
        config.options.plugins = config.options.plugins || {};
        config.options.plugins.tooltip = config.options.plugins.tooltip || {};
        config.options.plugins.tooltip.backgroundColor = tc.tooltipBg;
        config.options.plugins.tooltip.titleColor = tc.tooltipText;
        config.options.plugins.tooltip.bodyColor = tc.tooltipText;
        config.options.plugins.tooltip.borderColor = tc.tooltipBorder;
        config.options.plugins.tooltip.borderWidth = 1;
        config.options.plugins.tooltip.titleFont = { family: 'Outfit, sans-serif' };
        config.options.plugins.tooltip.bodyFont = { family: 'Outfit, sans-serif' };

        // Legend
        config.options.plugins.legend = config.options.plugins.legend || {};
        config.options.plugins.legend.labels = config.options.plugins.legend.labels || {};
        config.options.plugins.legend.labels.color = tc.textColor;
        config.options.plugins.legend.labels.font = { family: 'Outfit, sans-serif', size: 12 };

        return config;
    }

    /**
     * Open chart in the slide-out panel at larger size.
     */
    function expandChartToPanel(config, title) {
        var panelContent = document.createElement('div');

        // Large canvas
        var canvasWrap = document.createElement('div');
        canvasWrap.style.cssText = 'position:relative;height:400px;margin-bottom:1rem;';
        var canvas = document.createElement('canvas');
        canvasWrap.appendChild(canvas);
        panelContent.appendChild(canvasWrap);

        window.ChatPanel.open(title, panelContent, {
            actions: [
                {
                    label: 'Download PNG',
                    icon: 'fas fa-download',
                    className: 'chat-btn',
                    onClick: function () {
                        var link = document.createElement('a');
                        link.download = (title || 'chart') + '.png';
                        link.href = canvas.toDataURL('image/png');
                        link.click();
                    }
                }
            ]
        });

        // Render chart after panel DOM is ready
        setTimeout(function () {
            var panelConfig = JSON.parse(JSON.stringify(config));
            new Chart(canvas, panelConfig);
        }, 50);
    }

    /**
     * Expand a base64 chart image into the panel.
     * @param {string} imgHtml - The <img> tag HTML
     * @param {string} title
     */
    function expandImageToPanel(imgHtml, title) {
        if (!window.ChatPanel) return;

        var panelContent = document.createElement('div');
        panelContent.innerHTML = '<div style="text-align:center;">' + imgHtml + '</div>';

        // Make image full-width in panel
        var img = panelContent.querySelector('img');
        if (img) {
            img.style.maxWidth = '100%';
            img.style.height = 'auto';
            img.style.borderRadius = '8px';
        }

        window.ChatPanel.open(title || 'Chart Detail', panelContent, {
            actions: [
                {
                    label: 'Download',
                    icon: 'fas fa-download',
                    className: 'chat-btn',
                    onClick: function () {
                        if (img && img.src) {
                            var link = document.createElement('a');
                            link.download = (title || 'chart') + '.png';
                            link.href = img.src;
                            link.click();
                        }
                    }
                }
            ]
        });
    }

    // Export
    window.ChatCharts = {
        render: render,
        expandImageToPanel: expandImageToPanel,
        DEFAULT_COLORS: DEFAULT_COLORS
    };
})();
