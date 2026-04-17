/**
 * Chart Renderer
 * Creates Chart.js charts from structured block data.
 * Tracks instances for proper cleanup (preventing canvas memory leaks).
 */

const DEFAULT_COLORS = [
    '#3b82f6', // blue
    '#10b981', // green
    '#f59e0b', // amber
    '#8b5cf6', // purple
    '#ef4444', // red
    '#06b6d4', // cyan
    '#ec4899', // pink
    '#f97316', // orange
];

export class ChartRenderer {
    constructor() {
        this._charts = [];
    }

    /**
     * Create a Chart.js chart from a block definition.
     * @param {object} block — chart block data
     * @param {HTMLElement} containerEl — parent element to append to
     * @returns {HTMLElement} the chart wrapper element
     */
    create(block, containerEl) {
        const wrapper = document.createElement('div');
        wrapper.className = 'block-chart';

        // Title
        if (block.title) {
            const title = document.createElement('div');
            title.className = 'block-chart-title';
            title.textContent = block.title;
            wrapper.appendChild(title);
        }

        // Canvas
        const canvasWrap = document.createElement('div');
        canvasWrap.className = 'block-chart-canvas-wrap';
        const canvas = document.createElement('canvas');
        canvasWrap.appendChild(canvas);
        wrapper.appendChild(canvasWrap);

        containerEl.appendChild(wrapper);

        // Create chart after DOM insertion (Chart.js needs the canvas in DOM)
        const ctx = canvas.getContext('2d');
        const chartType = block.chartType || 'bar';
        const chart = this._createChart(ctx, block, chartType);
        this._charts.push(chart);

        return wrapper;
    }

    /**
     * Create a Chart.js instance.
     */
    _createChart(ctx, block, chartType) {
        const data = block.data || [];
        const xKey = block.xKey || 'name';
        const yKeys = block.yKeys || ['value'];
        const colors = block.colors || DEFAULT_COLORS;

        const isPie = chartType === 'pie' || chartType === 'doughnut';
        const isArea = chartType === 'area';
        const type = isArea ? 'line' : (isPie ? 'pie' : chartType);

        const labels = data.map(d => d[xKey]);

        let datasets;

        if (isPie) {
            // Pie/doughnut: single dataset with multiple colors
            const firstKey = yKeys[0];
            datasets = [{
                data: data.map(d => d[firstKey]),
                backgroundColor: data.map((_, i) => colors[i % colors.length]),
                borderWidth: 0,
            }];
        } else {
            // Bar/line/area: one dataset per yKey
            datasets = yKeys.map((key, i) => ({
                label: key,
                data: data.map(d => d[key]),
                backgroundColor: this._withAlpha(colors[i % colors.length], isArea ? 0.15 : 0.85),
                borderColor: colors[i % colors.length],
                borderWidth: type === 'line' || isArea ? 2 : 0,
                fill: isArea,
                tension: type === 'line' || isArea ? 0.35 : 0,
                borderRadius: type === 'bar' ? 4 : 0,
                pointRadius: type === 'line' || isArea ? 3 : 0,
                pointHoverRadius: 5,
            }));
        }

        // Detect theme
        const isDark = !document.body.classList.contains('light-mode');
        const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
        const textColor = isDark ? '#a1a1aa' : '#64748b';

        return new Chart(ctx, {
            type,
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 600, easing: 'easeOutQuart' },
                plugins: {
                    legend: {
                        display: yKeys.length > 1 || isPie,
                        position: isPie ? 'right' : 'top',
                        labels: {
                            color: textColor,
                            font: { family: 'Outfit', size: 11 },
                            padding: 12,
                            boxWidth: 12,
                            boxHeight: 12,
                            borderRadius: 3,
                            useBorderRadius: true,
                        },
                    },
                    tooltip: {
                        backgroundColor: isDark ? '#18181b' : '#ffffff',
                        titleColor: isDark ? '#e4e4e7' : '#0f172a',
                        bodyColor: isDark ? '#a1a1aa' : '#475569',
                        borderColor: isDark ? '#27272a' : '#e2e8f0',
                        borderWidth: 1,
                        cornerRadius: 8,
                        padding: 10,
                        titleFont: { family: 'Outfit', weight: '500' },
                        bodyFont: { family: 'JetBrains Mono', size: 11 },
                    },
                },
                scales: isPie ? {} : {
                    x: {
                        grid: { color: gridColor, drawBorder: false },
                        ticks: { color: textColor, font: { family: 'Outfit', size: 11 } },
                    },
                    y: {
                        grid: { color: gridColor, drawBorder: false },
                        ticks: { color: textColor, font: { family: 'JetBrains Mono', size: 11 } },
                        beginAtZero: true,
                    },
                },
            },
        });
    }

    /**
     * Add alpha transparency to a hex color.
     */
    _withAlpha(hex, alpha) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return `rgba(${r},${g},${b},${alpha})`;
    }

    /**
     * Destroy all tracked chart instances to prevent memory leaks.
     */
    destroyAll() {
        for (const chart of this._charts) {
            try { chart.destroy(); } catch { /* already destroyed */ }
        }
        this._charts = [];
    }
}
