/**
 * Streaming Skeleton
 * Shows adaptive shimmer placeholders while tokens accumulate silently.
 * Detects block types in the raw JSON stream and adds appropriate skeletons.
 */

const STATUS_MESSAGES = [
    'Analyzing your request...',
    'Building insights...',
    'Generating response...',
    'Preparing visualization...',
    'Organizing data...',
];

export class StreamingSkeleton {
    constructor(containerEl) {
        this.container = containerEl;
        this.el = null;
        this._statusInterval = null;
        this._statusIndex = 0;
        this._hasChartSkeleton = false;
        this._hasTableSkeleton = false;
    }

    /**
     * Show the initial skeleton: pulsing orb + status message + text shimmer bars.
     */
    show() {
        this.remove();

        this.el = document.createElement('div');
        this.el.className = 'streaming-skeleton';

        // Status line with pulsing orb
        const status = document.createElement('div');
        status.className = 'skeleton-status';
        status.innerHTML = `
            <div class="skeleton-orb"></div>
            <span class="skeleton-status-text">${STATUS_MESSAGES[0]}</span>
        `;
        this.el.appendChild(status);

        // Text shimmer bars
        const textBlock = document.createElement('div');
        textBlock.className = 'skeleton-text-block';
        const widths = ['92%', '78%', '85%', '45%'];
        for (const w of widths) {
            const bar = document.createElement('div');
            bar.className = 'shimmer-bar';
            bar.style.width = w;
            bar.style.height = '12px';
            bar.style.marginBottom = '8px';
            textBlock.appendChild(bar);
        }
        this.el.appendChild(textBlock);

        this.container.appendChild(this.el);

        // Cycle status messages
        this._statusInterval = setInterval(() => {
            this._statusIndex = (this._statusIndex + 1) % STATUS_MESSAGES.length;
            const textEl = this.el?.querySelector('.skeleton-status-text');
            if (textEl) textEl.textContent = STATUS_MESSAGES[this._statusIndex];
        }, 2500);
    }

    /**
     * Adapt the skeleton as block types are detected in the raw accumulated content.
     * @param {string} rawText — the raw accumulated JSON string so far
     */
    updateFromRawContent(rawText) {
        if (!this.el) return;

        // Detect chart block type
        if (!this._hasChartSkeleton && /"type"\s*:\s*"chart"/.test(rawText)) {
            this._hasChartSkeleton = true;
            this._addChartSkeleton();
        }

        // Detect table block type
        if (!this._hasTableSkeleton && /"type"\s*:\s*"table"/.test(rawText)) {
            this._hasTableSkeleton = true;
            this._addTableSkeleton();
        }
    }

    /**
     * Add a chart skeleton placeholder.
     */
    _addChartSkeleton() {
        if (!this.el) return;

        const card = document.createElement('div');
        card.className = 'skeleton-chart';

        // Title shimmer
        const title = document.createElement('div');
        title.className = 'shimmer-bar';
        title.style.width = '40%';
        title.style.height = '14px';
        title.style.marginBottom = '16px';
        card.appendChild(title);

        // Bar chart shimmer — 7 bars at varying heights
        const barsRow = document.createElement('div');
        barsRow.className = 'skeleton-chart-bars';
        const heights = [60, 85, 45, 70, 55, 90, 40];
        for (const h of heights) {
            const bar = document.createElement('div');
            bar.className = 'shimmer-bar skeleton-chart-bar';
            bar.style.height = `${h}%`;
            barsRow.appendChild(bar);
        }
        card.appendChild(barsRow);

        this.el.appendChild(card);
    }

    /**
     * Add a table skeleton placeholder.
     */
    _addTableSkeleton() {
        if (!this.el) return;

        const card = document.createElement('div');
        card.className = 'skeleton-table';

        // Header row
        const header = document.createElement('div');
        header.className = 'skeleton-table-row skeleton-table-header';
        for (let i = 0; i < 4; i++) {
            const cell = document.createElement('div');
            cell.className = 'shimmer-bar';
            cell.style.width = `${60 + Math.random() * 30}%`;
            cell.style.height = '12px';
            header.appendChild(cell);
        }
        card.appendChild(header);

        // Data rows with decreasing opacity
        for (let r = 0; r < 4; r++) {
            const row = document.createElement('div');
            row.className = 'skeleton-table-row';
            row.style.opacity = `${1 - r * 0.2}`;
            for (let i = 0; i < 4; i++) {
                const cell = document.createElement('div');
                cell.className = 'shimmer-bar';
                cell.style.width = `${50 + Math.random() * 40}%`;
                cell.style.height = '10px';
                row.appendChild(cell);
            }
            card.appendChild(row);
        }

        this.el.appendChild(card);
    }

    /**
     * Remove the skeleton from the DOM and clean up.
     */
    remove() {
        if (this._statusInterval) {
            clearInterval(this._statusInterval);
            this._statusInterval = null;
        }
        if (this.el) {
            this.el.remove();
            this.el = null;
        }
        this._hasChartSkeleton = false;
        this._hasTableSkeleton = false;
        this._statusIndex = 0;
    }
}
