/**
 * Block Renderer
 * Dispatches structured content blocks (text, chart, table) to their
 * respective renderers. Falls back to markdown for non-JSON responses.
 */

import { ChartRenderer } from './chart-renderer.js';
import { TableRenderer } from './table-renderer.js';

export class BlockRenderer {
    constructor() {
        this.chartRenderer = new ChartRenderer();
        this.tableRenderer = new TableRenderer();
    }

    /**
     * Render an array of typed blocks into a container element.
     * @param {Array} blocks — parsed JSON block array
     * @param {HTMLElement} containerEl — the bubble element to render into
     */
    renderBlocks(blocks, containerEl) {
        for (const block of blocks) {
            switch (block.type) {
                case 'text':
                    containerEl.appendChild(this._renderTextBlock(block));
                    break;
                case 'chart':
                    this.chartRenderer.create(block, containerEl);
                    break;
                case 'table':
                    containerEl.appendChild(this.tableRenderer.create(block));
                    break;
                default:
                    // Unknown block type — render as text if it has content
                    if (block.content) {
                        containerEl.appendChild(this._renderTextBlock(block));
                    }
            }
        }
    }

    /**
     * Render a text block using marked.js for full markdown support.
     * @param {object} block — text block with .content
     * @returns {HTMLElement}
     */
    _renderTextBlock(block) {
        const div = document.createElement('div');
        div.className = 'block-text';

        // Configure marked with highlight.js
        if (typeof marked !== 'undefined') {
            const renderer = new marked.Renderer();
            // Links open in new tab
            renderer.link = function({ href, title, text }) {
                const titleAttr = title ? ` title="${title}"` : '';
                return `<a href="${href}"${titleAttr} target="_blank" rel="noopener noreferrer">${text}</a>`;
            };

            div.innerHTML = marked.parse(block.content || '', {
                renderer,
                breaks: true,
                gfm: true,
            });

            // Apply syntax highlighting to code blocks
            if (typeof hljs !== 'undefined') {
                div.querySelectorAll('pre code').forEach(el => {
                    hljs.highlightElement(el);
                });
            }
        } else {
            // Fallback: basic HTML escaping + line breaks
            div.textContent = block.content || '';
        }

        return div;
    }

    /**
     * Render raw text as markdown (fallback when JSON parse fails).
     * @param {string} rawText — the raw response text
     * @param {HTMLElement} containerEl — the bubble element to render into
     */
    renderFallbackMarkdown(rawText, containerEl) {
        const div = document.createElement('div');
        div.className = 'block-text';

        if (typeof marked !== 'undefined') {
            div.innerHTML = marked.parse(rawText || '', {
                breaks: true,
                gfm: true,
            });
            if (typeof hljs !== 'undefined') {
                div.querySelectorAll('pre code').forEach(el => {
                    hljs.highlightElement(el);
                });
            }
        } else {
            // Absolute fallback: plain text
            div.textContent = rawText;
        }

        containerEl.appendChild(div);
    }

    /**
     * Destroy all Chart.js instances managed by this renderer.
     */
    destroy() {
        this.chartRenderer.destroyAll();
    }
}
