/**
 * Chat Manager
 * Renders messages, processing pipeline, and streaming responses.
 *
 * v2: Block-aware rendering. Tokens are accumulated silently behind a
 * skeleton animation. On completion, the buffer is parsed as a JSON array
 * of typed content blocks (text/chart/table) and rendered via BlockRenderer.
 * If JSON parsing fails, falls back to markdown rendering.
 */

import { StreamingSkeleton } from './streaming-skeleton.js';
import { BlockRenderer } from './block-renderer.js';

const PROCESSING_ICONS = {
    brain: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z"/></svg>`,
    chat: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>`,
    search: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"/></svg>`,
    rocket: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M15.59 14.37a6 6 0 01-5.84 7.38v-4.8m5.84-2.58a14.98 14.98 0 006.16-12.12A14.98 14.98 0 009.631 8.41m5.96 5.96a14.926 14.926 0 01-5.841 2.58m-.119-8.54a6 6 0 00-7.381 5.84h4.8m2.58-5.84a14.927 14.927 0 00-2.58 5.84m2.699 2.7c-.103.021-.207.041-.311.06a15.09 15.09 0 01-2.448-2.448 14.9 14.9 0 01.06-.312m-2.24 2.39a4.493 4.493 0 00-1.757 4.306 4.493 4.493 0 004.306-1.758M16.5 9a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0z"/></svg>`,
    edit: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931z"/></svg>`,
    stream: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25H12"/></svg>`,
    users: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z"/></svg>`,
};

/**
 * Check if a parsed value is a valid block array (array of objects with .type).
 */
function isBlockArray(parsed) {
    return Array.isArray(parsed) && parsed.length > 0 && parsed.every(b => b && b.type);
}

/**
 * Repair literal newlines/tabs inside JSON string values.
 * LLMs sometimes output literal \n instead of \\n inside quoted strings,
 * which breaks JSON.parse(). This walks char-by-char and escapes them.
 */
function repairJsonStrings(text) {
    let result = '';
    let inString = false;
    let escape = false;
    for (let i = 0; i < text.length; i++) {
        const ch = text[i];
        if (escape) { escape = false; result += ch; continue; }
        if (ch === '\\' && inString) { escape = true; result += ch; continue; }
        if (ch === '"') { inString = !inString; result += ch; continue; }
        if (inString) {
            if (ch === '\n') { result += '\\n'; continue; }
            if (ch === '\r') { result += '\\r'; continue; }
            if (ch === '\t') { result += '\\t'; continue; }
        }
        result += ch;
    }
    return result;
}

/**
 * Extract a JSON block array from raw LLM output.
 * Handles literal newlines in strings, markdown fences, preamble text.
 * Returns the parsed array or null if extraction fails.
 *
 * Parsing order matters:
 *   1. Direct JSON.parse (fast path for clean output)
 *   2. Repair literal newlines then parse (most common LLM issue)
 *   3. Bracket-walking with repair fallback (handles preamble text)
 *   4. Fence stripping with anchored regex (last resort — anchored to avoid
 *      matching embedded ```sql etc. inside JSON string values)
 */
function extractJsonBlocks(raw) {
    if (!raw) return null;

    const text = raw.trim();

    // 1. Direct parse (fast path)
    try {
        const p = JSON.parse(text);
        if (isBlockArray(p)) return p;
    } catch { /* continue */ }

    // 2. Repair literal newlines in strings, then parse
    try {
        const repaired = repairJsonStrings(text);
        const p = JSON.parse(repaired);
        if (isBlockArray(p)) return p;
    } catch { /* continue */ }

    // 3. Bracket-walking: find the outermost JSON array in the text
    const firstBracket = text.indexOf('[');
    if (firstBracket !== -1) {
        let depth = 0;
        let inString = false;
        let escape = false;
        for (let i = firstBracket; i < text.length; i++) {
            const ch = text[i];
            if (escape) { escape = false; continue; }
            if (ch === '\\' && inString) { escape = true; continue; }
            if (ch === '"') { inString = !inString; continue; }
            if (inString) continue;
            if (ch === '[') depth++;
            else if (ch === ']') {
                depth--;
                if (depth === 0) {
                    const candidate = text.substring(firstBracket, i + 1);
                    try {
                        const parsed = JSON.parse(candidate);
                        if (isBlockArray(parsed)) return parsed;
                    } catch {
                        // Try with repair
                        try {
                            const parsed = JSON.parse(repairJsonStrings(candidate));
                            if (isBlockArray(parsed)) return parsed;
                        } catch { /* not valid */ }
                    }
                    break;
                }
            }
        }
    }

    // 4. Fence stripping — ANCHORED regex to avoid matching embedded code blocks
    const fenceMatch = text.match(/^\s*```(?:json)?\s*\n?([\s\S]*?)```\s*$/);
    if (fenceMatch) {
        const inner = fenceMatch[1].trim();
        try {
            const parsed = JSON.parse(inner);
            if (isBlockArray(parsed)) return parsed;
        } catch {
            try {
                const parsed = JSON.parse(repairJsonStrings(inner));
                if (isBlockArray(parsed)) return parsed;
            } catch { /* not valid */ }
        }
    }

    // 5. Deep repair: fix unescaped quotes inside JSON string values.
    // LLMs often output "content":"...for "lease"..." where the inner quotes
    // break JSON parsing. This attempts a structural repair.
    try {
        const deepRepaired = repairUnescapedQuotes(text);
        const p = JSON.parse(deepRepaired);
        if (isBlockArray(p)) return p;
    } catch { /* continue */ }

    // 6. Bracket-walking + deep repair combined
    if (firstBracket !== -1) {
        try {
            // Re-extract the candidate using a simpler approach: find last ]
            const lastBracket = text.lastIndexOf(']');
            if (lastBracket > firstBracket) {
                const candidate = text.substring(firstBracket, lastBracket + 1);
                const repaired = repairUnescapedQuotes(repairJsonStrings(candidate));
                const parsed = JSON.parse(repaired);
                if (isBlockArray(parsed)) return parsed;
            }
        } catch { /* not valid */ }
    }

    return null;
}

/**
 * Repair unescaped double quotes inside JSON string values.
 * Handles the common LLM pattern where quotes appear inside content strings,
 * e.g., "content":"Results for "lease" documents" → "content":"Results for \"lease\" documents"
 *
 * Strategy: Iteratively parse and fix errors. When JSON.parse fails, the error position
 * points to the character AFTER the problematic quote, so we check both pos and pos-1.
 */
function repairUnescapedQuotes(text) {
    // First apply standard repairs (literal newlines, tabs)
    let s = repairJsonStrings(text);

    // Try to parse immediately — fast path for valid JSON
    try {
        JSON.parse(s);
        return s;
    } catch (e) {
        // Get the position of the error
        const posMatch = e.message.match(/position\s+(\d+)/i);
        if (!posMatch) return s;

        let fixed = s;
        let attempts = 0;

        // Iteratively fix unescaped quotes at error positions
        while (attempts < 30) {
            attempts++;
            try {
                JSON.parse(fixed);
                return fixed; // Success!
            } catch (err) {
                const pm = err.message.match(/position\s+(\d+)/i);
                if (!pm) break;
                const pos = parseInt(pm[1]);

                if (pos < 0 || pos >= fixed.length) break;

                // JSON.parse error position points to the char AFTER the unescaped quote.
                // Check both pos and pos-1 for a quote that needs escaping.
                let quotePos = -1;

                // Check pos-1 first (most common case — error points to char after quote)
                if (pos > 0 && fixed[pos - 1] === '"') {
                    const before = pos > 1 ? fixed[pos - 2] : '';
                    const after = fixed[pos];

                    // Quote preceded by non-structural char and followed by word char → needs escaping
                    // E.g., ..."Test " → ..."Test \"
                    if (before && /[\w\s.,!?;:()\-]/.test(before) && after && /[\w]/.test(after)) {
                        quotePos = pos - 1;
                    }
                }

                // Also check pos itself (in case error points directly at the quote)
                if (quotePos === -1 && fixed[pos] === '"') {
                    const before = pos > 0 ? fixed[pos - 1] : '';
                    const after = pos < fixed.length - 1 ? fixed[pos + 1] : '';

                    // Quote between word/space chars → needs escaping
                    if (before && after &&
                        /[\w\s.,!?;:()\-]/.test(before) &&
                        /[\w\s.,!?;:()\-]/.test(after)) {
                        quotePos = pos;
                    }
                }

                // If we found a quote to escape, do it and retry
                if (quotePos !== -1) {
                    fixed = fixed.substring(0, quotePos) + '\\"' + fixed.substring(quotePos + 1);
                    continue;
                }

                // Can't identify the issue — give up
                break;
            }
        }
        return fixed;
    }
}

export class ChatManager {
    constructor(container) {
        this.container = container;
        this.messagesEl = container.querySelector('#chat-messages');
        this.welcomeEl = container.querySelector('#welcome-state');
        this.typingEl = document.getElementById('typing-indicator');
        this._processingEl = null;
        this._processingSteps = [];
        this._streamingBubble = null;
        this._blockRenderers = [];  // Track for Chart.js cleanup
    }

    hideWelcome() { if (this.welcomeEl) this.welcomeEl.style.display = 'none'; }
    showWelcome() { if (this.welcomeEl) this.welcomeEl.style.display = ''; }

    clear() {
        const msgs = this.messagesEl.querySelectorAll('.msg-row, .processing-state, .plan-card-wrapper');
        msgs.forEach(m => m.remove());
        this._processingEl = null;
        this._processingSteps = [];
        this._streamingBubble = null;
        this.destroyBlockRenderers();
    }

    /** Destroy all BlockRenderer instances (cleans up Chart.js canvases). */
    destroyBlockRenderers() {
        for (const renderer of this._blockRenderers) {
            try { renderer.destroy(); } catch { /* ok */ }
        }
        this._blockRenderers = [];
    }

    // ─── User Messages ──────────────────────────────────────

    addUserMessage(text, attachmentNames = []) {
        this.hideWelcome();
        const row = document.createElement('div');
        row.className = 'msg-row flex justify-end msg-animate';
        const bubble = document.createElement('div');
        bubble.className = 'msg-user px-4 py-3';

        let html = '';
        if (text) {
            html += `<p class="text-sm text-zinc-200">${this._escapeHtml(text)}</p>`;
        }
        if (attachmentNames.length > 0) {
            html += `<div class="msg-attachments mt-2">`;
            for (const name of attachmentNames) {
                html += `<span class="msg-attachment-badge">
                    <svg class="w-3 h-3 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"/>
                    </svg>
                    ${this._escapeHtml(name)}
                </span>`;
            }
            html += `</div>`;
        }

        bubble.innerHTML = html;
        row.appendChild(bubble);
        this.messagesEl.appendChild(row);
        this._scrollToBottom();
    }

    // ─── Processing Pipeline ────────────────────────────────

    /**
     * Show the processing pipeline animation.
     * Called when the agent starts working, BEFORE any tokens arrive.
     */
    showProcessing() {
        this.hideWelcome();
        this.removeProcessing();

        const row = document.createElement('div');
        row.className = 'processing-state';

        // Avatar
        const avatar = document.createElement('div');
        avatar.className = 'ai-avatar w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5';
        avatar.innerHTML = `<svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
        </svg>`;

        // Pipeline
        const pipeline = document.createElement('div');
        pipeline.className = 'processing-pipeline';
        pipeline.id = 'processing-pipeline';

        // Scan line
        const scanLine = document.createElement('div');
        scanLine.className = 'processing-scan-line';
        pipeline.appendChild(scanLine);

        row.appendChild(avatar);
        row.appendChild(pipeline);
        this.messagesEl.appendChild(row);

        this._processingEl = row;
        this._processingSteps = [];
        this._scrollToBottom();
    }

    /**
     * Add or update a step in the processing pipeline.
     * @param {string} phase - Phase identifier
     * @param {string} label - Human-readable label
     * @param {string} icon - Icon key from PROCESSING_ICONS
     */
    updateProcessingStep(phase, label, icon) {
        if (!this._processingEl) this.showProcessing();

        const pipeline = this._processingEl.querySelector('#processing-pipeline');
        if (!pipeline) return;

        // Mark all existing steps as done
        pipeline.querySelectorAll('.processing-step.active').forEach(el => {
            el.classList.remove('active');
            el.classList.add('done');
            const iconEl = el.querySelector('.processing-step-icon');
            if (iconEl) {
                iconEl.innerHTML = `<span class="processing-check">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5"/>
                    </svg>
                </span>`;
            }
        });

        // Insert new step before the scan line
        const scanLine = pipeline.querySelector('.processing-scan-line');
        const step = document.createElement('div');
        step.className = 'processing-step active';
        step.dataset.phase = phase;

        const iconHtml = PROCESSING_ICONS[icon] || PROCESSING_ICONS.brain;
        step.innerHTML = `
            <div class="processing-step-icon text-cyber-cyan">
                <div class="processing-spinner"></div>
            </div>
            <span class="processing-step-label">${this._escapeHtml(label)}</span>
        `;

        pipeline.insertBefore(step, scanLine);
        this._processingSteps.push({ phase, label, icon });
        this._scrollToBottom();
    }

    /** Remove the processing pipeline (when tokens start or on error). */
    removeProcessing() {
        if (this._processingEl) {
            this._processingEl.remove();
            this._processingEl = null;
            this._processingSteps = [];
        }
    }

    // ─── AI Messages (Block-Aware) ──────────────────────────

    /**
     * Start a new streaming AI message with skeleton loading.
     * Tokens accumulate silently; on complete(), blocks are parsed and rendered.
     */
    startAIMessage() {
        this.removeProcessing();

        const row = document.createElement('div');
        row.className = 'msg-row flex items-start gap-3 msg-animate';

        const avatar = document.createElement('div');
        avatar.className = 'ai-avatar w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5';
        avatar.innerHTML = `<svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
        </svg>`;

        const bubble = document.createElement('div');
        bubble.className = 'msg-ai px-4 py-3';

        row.appendChild(avatar);
        row.appendChild(bubble);
        this.messagesEl.appendChild(row);

        // Create skeleton inside the bubble
        const skeleton = new StreamingSkeleton(bubble);
        skeleton.show();

        let buffer = '';
        const blockRenderer = new BlockRenderer();
        this._blockRenderers.push(blockRenderer);

        this._streamingBubble = { row, bubble, buffer };

        return {
            appendToken: (token) => {
                buffer += token;
                skeleton.updateFromRawContent(buffer);
                this._scrollToBottom();
            },
            complete: () => {
                skeleton.remove();

                // Try to parse as structured blocks (with robust extraction)
                const blocks = extractJsonBlocks(buffer);
                if (blocks) {
                    blockRenderer.renderBlocks(blocks, bubble);
                } else {
                    // Fallback: render as markdown
                    console.debug('[BlockRenderer] JSON extraction failed, falling back to markdown. Buffer preview:', buffer.substring(0, 300));
                    blockRenderer.renderFallbackMarkdown(buffer, bubble);
                }

                // Add timestamp
                this._addTimestamp(bubble);
                this._streamingBubble = null;
                this._scrollToBottom();
            },
            getText: () => buffer,
            getBlockRenderer: () => blockRenderer,
        };
    }

    /**
     * Add a complete AI message (used when restoring history).
     * Tries to parse as blocks first, falls back to markdown.
     */
    addAIMessage(text) {
        this.removeProcessing();

        const row = document.createElement('div');
        row.className = 'msg-row flex items-start gap-3 msg-animate';

        const avatar = document.createElement('div');
        avatar.className = 'ai-avatar w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5';
        avatar.innerHTML = `<svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
        </svg>`;

        const bubble = document.createElement('div');
        bubble.className = 'msg-ai px-4 py-3';

        row.appendChild(avatar);
        row.appendChild(bubble);
        this.messagesEl.appendChild(row);

        const blockRenderer = new BlockRenderer();
        this._blockRenderers.push(blockRenderer);

        // Try structured blocks first (with robust extraction)
        const blocks = extractJsonBlocks(text || '');
        if (blocks) {
            blockRenderer.renderBlocks(blocks, bubble);
        } else {
            blockRenderer.renderFallbackMarkdown(text || '', bubble);
        }

        this._addTimestamp(bubble);
        this._scrollToBottom();
    }

    // ─── Plan Card (inline in chat) ─────────────────────────

    addPlanCard(plan, onApprove, onReject) {
        if (!plan || !plan.steps || plan.steps.length === 0) return;

        const hasDestructive = plan.has_destructive_steps ||
            plan.steps.some(s => s.is_destructive);

        const wrapper = document.createElement('div');
        wrapper.className = 'plan-card-wrapper msg-animate ml-11 mt-3 mb-2';

        const card = document.createElement('div');
        card.className = `plan-card${hasDestructive ? ' plan-card--destructive' : ''}`;

        // Header
        const header = document.createElement('div');
        header.className = 'plan-card-header';
        header.innerHTML = `
            <span class="section-label">${hasDestructive ? '⚠️ DESTRUCTIVE PLAN' : 'BUILD PLAN'}</span>
            <span class="text-xs text-zinc-500 font-mono">${plan.steps.length} steps</span>
        `;
        card.appendChild(header);

        // Destructive warning banner
        if (hasDestructive) {
            const warning = document.createElement('div');
            warning.className = 'plan-destructive-warning';
            warning.innerHTML = `
                <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/>
                </svg>
                <span>This plan contains destructive actions that cannot be undone.</span>
            `;
            card.appendChild(warning);
        }

        // Steps
        const stepsEl = document.createElement('div');
        stepsEl.className = 'py-1';
        plan.steps.forEach((step, i) => {
            const stepEl = document.createElement('div');
            stepEl.className = `plan-step${step.is_destructive ? ' plan-step--destructive' : ''}`;
            const icon = step.is_destructive
                ? '<svg class="w-3.5 h-3.5 text-rose-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>'
                : `<div class="plan-step-dot ${step.status || ''}"></div>`;
            stepEl.innerHTML = `
                ${icon}
                <div class="flex-1 min-w-0">
                    <div class="text-sm ${step.is_destructive ? 'text-rose-300' : 'text-zinc-300'}">${this._escapeHtml(step.description)}</div>
                    ${step.domain ? `<span class="text-[10px] font-mono text-zinc-600 mt-0.5 inline-block">${step.domain}/${step.action || ''}</span>` : ''}
                </div>
            `;
            stepsEl.appendChild(stepEl);
        });
        card.appendChild(stepsEl);

        // Actions
        if (plan.status === 'draft') {
            const actions = document.createElement('div');
            actions.className = 'plan-card-actions';

            const approveBtn = document.createElement('button');
            if (hasDestructive) {
                approveBtn.className = 'btn-danger';
                approveBtn.textContent = 'Confirm & Delete';
            } else {
                approveBtn.className = 'btn-primary';
                approveBtn.textContent = 'Approve & Execute';
            }
            approveBtn.addEventListener('click', () => {
                actions.querySelectorAll('button').forEach(b => { b.disabled = true; b.style.opacity = '0.5'; });
                onApprove();
            });

            const rejectBtn = document.createElement('button');
            rejectBtn.className = 'btn-secondary';
            rejectBtn.textContent = 'Modify';
            rejectBtn.addEventListener('click', () => {
                actions.querySelectorAll('button').forEach(b => { b.disabled = true; b.style.opacity = '0.5'; });
                onReject();
            });

            actions.appendChild(approveBtn);
            actions.appendChild(rejectBtn);
            card.appendChild(actions);
        }

        wrapper.appendChild(card);
        this.messagesEl.appendChild(wrapper);
        this._scrollToBottom();

        return card;
    }

    // ─── Status Badge ───────────────────────────────────────

    updateStatus(phase) {
        const statusEl = document.getElementById('agent-status');
        if (!statusEl) return;
        const dot = statusEl.querySelector('div');
        const text = statusEl.querySelector('span');

        const phaseConfig = {
            ready: { color: 'bg-emerald-400', label: 'ready' },
            thinking: { color: 'bg-amber-400', label: 'thinking' },
            streaming: { color: 'bg-cyber-cyan', label: 'streaming' },
            responding: { color: 'bg-cyber-cyan', label: 'responding' },
            querying: { color: 'bg-cyber-cyan', label: 'querying' },
            analyzing: { color: 'bg-amber-400', label: 'analyzing' },
            planning: { color: 'bg-cyber-violet', label: 'planning' },
            executing: { color: 'bg-amber-400', label: 'executing' },
            delegating: { color: 'bg-purple-400', label: 'delegating' },
        };

        const config = phaseConfig[phase] || phaseConfig.ready;
        dot.className = `w-1.5 h-1.5 rounded-full ${config.color}`;
        text.textContent = config.label;
        if (phase !== 'ready') dot.classList.add('status-pulse');
        else dot.classList.remove('status-pulse');
    }

    // ─── Error Messages ─────────────────────────────────────

    addErrorMessage(text) {
        this.hideWelcome();
        this.removeProcessing();
        const row = document.createElement('div');
        row.className = 'msg-row flex items-start gap-3 msg-animate';
        row.innerHTML = `
            <div class="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5 bg-rose-500/20 border border-rose-500/30">
                <svg class="w-4 h-4 text-rose-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/>
                </svg>
            </div>
            <div class="px-4 py-3 rounded-2xl rounded-tl-md bg-rose-500/10 border border-rose-500/20 max-w-[80%]">
                <p class="text-sm text-rose-300">${this._escapeHtml(text)}</p>
            </div>
        `;
        this.messagesEl.appendChild(row);
        this._scrollToBottom();
    }

    // ─── Helpers ─────────────────────────────────────────────

    showTyping() { this.typingEl?.classList.remove('hidden'); this._scrollToBottom(); }
    hideTyping() { this.typingEl?.classList.add('hidden'); }

    _scrollToBottom() {
        requestAnimationFrame(() => { this.container.scrollTop = this.container.scrollHeight; });
    }

    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    _addTimestamp(bubble) {
        const ts = document.createElement('div');
        ts.className = 'text-[11px] text-zinc-600 mt-2 font-mono';
        ts.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        bubble.appendChild(ts);
    }
}
