/**
 * Sidebar
 * Manages the left sidebar session list and right sidebar plan details.
 */

export class Sidebar {
    /**
     * @param {object} opts
     * @param {HTMLElement} opts.sessionList - Container for session items
     * @param {HTMLElement} opts.rightSidebar - Right sidebar element
     * @param {function(string): void} opts.onSessionSelect - Called when user clicks a session
     * @param {function(): void} opts.onNewChat - Called when user clicks "New Chat"
     * @param {function(string): void} opts.onSessionDelete - Called when user deletes a session
     */
    constructor({ sessionList, rightSidebar, onSessionSelect, onNewChat, onSessionDelete }) {
        this.sessionListEl = sessionList;
        this.rightSidebar = rightSidebar;
        this.onSessionSelect = onSessionSelect;
        this.onNewChat = onNewChat;
        this.onSessionDelete = onSessionDelete;
        this.activeSessionId = null;

        // Bind new chat button
        document.getElementById('btn-new-chat')?.addEventListener('click', () => {
            this.onNewChat();
        });

        // Bind close right sidebar
        document.getElementById('btn-close-right')?.addEventListener('click', () => {
            this.hideRightSidebar();
        });
    }

    /** Render the session list. */
    renderSessions(sessions, activeId) {
        this.activeSessionId = activeId;
        this.sessionListEl.innerHTML = '';

        if (sessions.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'text-xs text-zinc-600 px-1 py-4 text-center';
            empty.textContent = 'No conversations yet';
            this.sessionListEl.appendChild(empty);
            return;
        }

        sessions.forEach(session => {
            const item = document.createElement('div');
            item.className = `session-item ${session.session_id === activeId ? 'active' : ''}`;
            item.dataset.sessionId = session.session_id;

            // Chat icon
            const icon = document.createElement('div');
            icon.innerHTML = `<svg class="w-3.5 h-3.5 text-zinc-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
            </svg>`;

            // Title
            const title = document.createElement('span');
            title.className = 'session-title';
            title.textContent = session.title || 'New Chat';

            // Delete button
            const del = document.createElement('button');
            del.className = 'session-delete w-5 h-5 flex items-center justify-center rounded hover:bg-zinc-700';
            del.innerHTML = `<svg class="w-3 h-3 text-zinc-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
            </svg>`;
            del.addEventListener('click', (e) => {
                e.stopPropagation();
                this.onSessionDelete(session.session_id);
            });

            item.appendChild(icon);
            item.appendChild(title);
            item.appendChild(del);

            item.addEventListener('click', () => {
                this.onSessionSelect(session.session_id);
            });

            this.sessionListEl.appendChild(item);
        });
    }

    /** Mark a session as active in the UI. */
    setActive(sessionId) {
        this.activeSessionId = sessionId;
        this.sessionListEl.querySelectorAll('.session-item').forEach(el => {
            el.classList.toggle('active', el.dataset.sessionId === sessionId);
        });
    }

    /** Show the right sidebar with plan details. */
    showRightSidebar() {
        this.rightSidebar.classList.remove('hidden');
        this.rightSidebar.classList.add('slide-in');
    }

    /** Hide the right sidebar. */
    hideRightSidebar() {
        this.rightSidebar.classList.add('hidden');
    }

    /** Update the right sidebar plan details content. */
    setPlanDetails(html) {
        const detailsEl = this.rightSidebar.querySelector('#plan-details');
        if (detailsEl) {
            detailsEl.innerHTML = html;
        }
    }

    /** Get the current right sidebar plan details content. */
    getPlanDetails() {
        const detailsEl = this.rightSidebar.querySelector('#plan-details');
        return detailsEl ? detailsEl.innerHTML : '';
    }

    /** Clear the right sidebar plan details content. */
    clearPlanDetails() {
        const detailsEl = this.rightSidebar.querySelector('#plan-details');
        if (detailsEl) {
            detailsEl.innerHTML = '';
        }
    }
}
