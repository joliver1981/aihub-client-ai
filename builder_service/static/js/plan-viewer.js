/**
 * Plan Viewer
 * Renders execution plan cards inside the chat flow.
 * Handles plan approval/rejection UI.
 */

export class PlanViewer {
    /**
     * @param {function(string): void} onApprove - Called when user approves a plan
     * @param {function(string): void} onReject - Called when user rejects a plan
     */
    constructor(onApprove, onReject) {
        this.onApprove = onApprove;
        this.onReject = onReject;
    }

    /**
     * Create a plan card element for embedding in chat.
     * 
     * @param {object} plan
     * @param {string} plan.goal - What the plan accomplishes
     * @param {Array<{description: string, category: string, status: string}>} plan.steps
     * @param {string[]} plan.context_needed - Missing context items
     * @returns {HTMLElement}
     */
    createPlanCard(plan) {
        const card = document.createElement('div');
        card.className = 'plan-card msg-animate';

        // Header
        const header = document.createElement('div');
        header.className = 'plan-card-header';
        header.innerHTML = `
            <span class="section-label">BUILD PLAN</span>
            <span class="text-xs text-zinc-500 font-mono">${plan.steps?.length || 0} steps</span>
        `;

        // Steps
        const stepsList = document.createElement('div');
        stepsList.className = 'py-1';

        (plan.steps || []).forEach((step, i) => {
            const stepEl = document.createElement('div');
            stepEl.className = 'plan-step';
            stepEl.dataset.stepIndex = i;

            const dot = document.createElement('div');
            dot.className = `plan-step-dot ${step.status || ''}`;

            const info = document.createElement('div');
            info.className = 'flex-1 min-w-0';

            const desc = document.createElement('div');
            desc.className = 'text-sm text-zinc-300';
            desc.textContent = step.description;

            const meta = document.createElement('div');
            meta.className = 'text-[11px] text-zinc-600 font-mono mt-0.5';
            meta.textContent = step.category ? `[${step.category}]` : '';

            info.appendChild(desc);
            if (step.category) info.appendChild(meta);

            stepEl.appendChild(dot);
            stepEl.appendChild(info);
            stepsList.appendChild(stepEl);
        });

        // Context needed
        let contextEl = null;
        if (plan.context_needed?.length) {
            contextEl = document.createElement('div');
            contextEl.className = 'px-4 py-2 text-xs text-amber-400/80';
            contextEl.textContent = `Context needed: ${plan.context_needed.join(', ')}`;
        }

        // Actions
        const actions = document.createElement('div');
        actions.className = 'plan-card-actions';

        const approveBtn = document.createElement('button');
        approveBtn.className = 'btn-primary';
        approveBtn.textContent = 'Approve & Execute';
        approveBtn.addEventListener('click', () => {
            this._disableActions(actions);
            this.onApprove(plan.plan_id || 'current');
        });

        const rejectBtn = document.createElement('button');
        rejectBtn.className = 'btn-secondary';
        rejectBtn.textContent = 'Modify';
        rejectBtn.addEventListener('click', () => {
            this._disableActions(actions);
            this.onReject(plan.plan_id || 'current');
        });

        actions.appendChild(approveBtn);
        actions.appendChild(rejectBtn);

        // Assemble
        card.appendChild(header);
        card.appendChild(stepsList);
        if (contextEl) card.appendChild(contextEl);
        card.appendChild(actions);

        return card;
    }

    /**
     * Update a step's status in an existing plan card.
     * 
     * @param {HTMLElement} card - The plan card element
     * @param {number} stepIndex - Step index to update
     * @param {string} status - New status: running, completed, failed
     */
    updateStepStatus(card, stepIndex, status) {
        const step = card.querySelector(`[data-step-index="${stepIndex}"]`);
        if (!step) return;

        const dot = step.querySelector('.plan-step-dot');
        if (dot) {
            dot.className = `plan-step-dot ${status}`;
        }
    }

    /**
     * Add a progress bar to a plan card.
     * 
     * @param {HTMLElement} card
     * @param {number} percent - 0-100
     */
    setProgress(card, percent) {
        let bar = card.querySelector('.progress-bar');
        if (!bar) {
            bar = document.createElement('div');
            bar.className = 'progress-bar mx-4 mb-3';
            bar.innerHTML = '<div class="progress-bar-fill" style="width: 0%"></div>';
            // Insert before actions
            const actions = card.querySelector('.plan-card-actions');
            if (actions) {
                card.insertBefore(bar, actions);
            } else {
                card.appendChild(bar);
            }
        }

        const fill = bar.querySelector('.progress-bar-fill');
        if (fill) {
            fill.style.width = `${Math.min(100, Math.max(0, percent))}%`;
        }
    }

    _disableActions(actionsEl) {
        actionsEl.querySelectorAll('button').forEach(btn => {
            btn.disabled = true;
            btn.style.opacity = '0.5';
            btn.style.pointerEvents = 'none';
        });
    }
}
