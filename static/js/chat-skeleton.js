/**
 * chat-skeleton.js — Modern loading feedback with rotating status + shimmer
 * Used by chat.html & data_chat.html
 */
(function () {
    'use strict';

    const STATUS_MESSAGES = [
        'Analyzing your request\u2026',
        'Thinking\u2026',
        'Building insights\u2026',
        'Generating response\u2026',
        'Almost there\u2026',
        'Preparing visualization\u2026',
        'Organizing data\u2026'
    ];

    const ROTATE_INTERVAL_MS = 2500;

    /* ---- CSS injected once ---- */
    const SKELETON_CSS = `
        .skeleton-container {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            padding: 0.75rem 1rem;
            max-width: 60%;
            animation: skeletonFadeIn 0.3s ease-out;
        }
        /* Status line */
        .skeleton-status {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.82rem;
            color: var(--text-secondary, #a1a1aa);
            font-family: 'Outfit', sans-serif;
        }
        .skeleton-orb {
            width: 8px; height: 8px;
            border-radius: 50%;
            background: var(--cyber-cyan, #06b6d4);
            animation: orbPulse 1.5s ease-in-out infinite;
        }
        /* Shimmer bars */
        .skeleton-bars {
            display: flex;
            flex-direction: column;
            gap: 0.45rem;
        }
        .skeleton-bar {
            height: 12px;
            border-radius: 6px;
            background: linear-gradient(
                90deg,
                var(--bg-elevated, #111) 25%,
                var(--border-subtle, #27272a) 50%,
                var(--bg-elevated, #111) 75%
            );
            background-size: 200% 100%;
            animation: shimmer 1.5s infinite;
        }
        /* Chart skeleton */
        .skeleton-chart {
            display: flex;
            align-items: flex-end;
            gap: 6px;
            height: 80px;
            padding-top: 0.5rem;
        }
        .skeleton-chart-bar {
            flex: 1;
            border-radius: 4px 4px 0 0;
            background: linear-gradient(
                90deg,
                var(--bg-elevated, #111) 25%,
                var(--border-subtle, #27272a) 50%,
                var(--bg-elevated, #111) 75%
            );
            background-size: 200% 100%;
            animation: shimmer 1.5s infinite;
        }
        /* Table skeleton */
        .skeleton-table {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        .skeleton-table-row {
            display: flex;
            gap: 6px;
        }
        .skeleton-table-cell {
            flex: 1;
            height: 14px;
            border-radius: 4px;
            background: linear-gradient(
                90deg,
                var(--bg-elevated, #111) 25%,
                var(--border-subtle, #27272a) 50%,
                var(--bg-elevated, #111) 75%
            );
            background-size: 200% 100%;
            animation: shimmer 1.5s infinite;
        }
        .skeleton-table-row.header .skeleton-table-cell {
            height: 16px;
            opacity: 0.8;
        }

        @keyframes shimmer {
            0%   { background-position: 200% 0; }
            100% { background-position: -200% 0; }
        }
        @keyframes orbPulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50%      { opacity: 0.4; transform: scale(0.85); }
        }
        @keyframes skeletonFadeIn {
            from { opacity: 0; transform: translateY(6px); }
            to   { opacity: 1; transform: translateY(0); }
        }
    `;

    let styleInjected = false;
    function injectCSS() {
        if (styleInjected) return;
        const s = document.createElement('style');
        s.textContent = SKELETON_CSS;
        document.head.appendChild(s);
        styleInjected = true;
    }

    /**
     * Create a skeleton loading element.
     * @param {'text'|'chart'|'table'} variant - Which skeleton to show
     * @returns {{ element: HTMLElement, destroy: () => void }}
     */
    function createSkeleton(variant) {
        injectCSS();

        const container = document.createElement('div');
        container.className = 'skeleton-container';
        container.id = 'chat-skeleton-' + Date.now();

        // Status line
        const statusLine = document.createElement('div');
        statusLine.className = 'skeleton-status';
        statusLine.innerHTML = `<span class="skeleton-orb"></span><span class="skeleton-text"></span>`;
        container.appendChild(statusLine);

        const statusText = statusLine.querySelector('.skeleton-text');
        let msgIndex = 0;
        statusText.textContent = STATUS_MESSAGES[0];

        const rotateTimer = setInterval(() => {
            msgIndex = (msgIndex + 1) % STATUS_MESSAGES.length;
            statusText.style.opacity = '0';
            setTimeout(() => {
                statusText.textContent = STATUS_MESSAGES[msgIndex];
                statusText.style.opacity = '1';
            }, 200);
        }, ROTATE_INTERVAL_MS);

        // Shimmer content
        if (variant === 'chart') {
            const chart = document.createElement('div');
            chart.className = 'skeleton-chart';
            const heights = [45, 70, 55, 80, 40, 65, 50];
            heights.forEach((h, i) => {
                const bar = document.createElement('div');
                bar.className = 'skeleton-chart-bar';
                bar.style.height = h + '%';
                bar.style.animationDelay = (i * 0.1) + 's';
                chart.appendChild(bar);
            });
            container.appendChild(chart);
        } else if (variant === 'table') {
            const table = document.createElement('div');
            table.className = 'skeleton-table';
            // Header row
            const headerRow = document.createElement('div');
            headerRow.className = 'skeleton-table-row header';
            for (let c = 0; c < 4; c++) {
                const cell = document.createElement('div');
                cell.className = 'skeleton-table-cell';
                cell.style.animationDelay = (c * 0.1) + 's';
                headerRow.appendChild(cell);
            }
            table.appendChild(headerRow);
            // Data rows
            for (let r = 0; r < 4; r++) {
                const row = document.createElement('div');
                row.className = 'skeleton-table-row';
                row.style.opacity = String(1 - r * 0.15);
                for (let c = 0; c < 4; c++) {
                    const cell = document.createElement('div');
                    cell.className = 'skeleton-table-cell';
                    cell.style.animationDelay = ((r * 4 + c) * 0.05) + 's';
                    row.appendChild(cell);
                }
                table.appendChild(row);
            }
            container.appendChild(table);
        } else {
            // Default: text bars
            const bars = document.createElement('div');
            bars.className = 'skeleton-bars';
            const widths = ['92%', '78%', '85%', '45%'];
            widths.forEach((w, i) => {
                const bar = document.createElement('div');
                bar.className = 'skeleton-bar';
                bar.style.width = w;
                bar.style.animationDelay = (i * 0.15) + 's';
                bars.appendChild(bar);
            });
            container.appendChild(bars);
        }

        return {
            element: container,
            destroy() {
                clearInterval(rotateTimer);
                if (container.parentNode) {
                    container.parentNode.removeChild(container);
                }
            }
        };
    }

    // Export
    window.ChatSkeleton = { create: createSkeleton };
})();
