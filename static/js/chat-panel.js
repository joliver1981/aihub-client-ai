/**
 * chat-panel.js — Right slide-out panel controller
 * Used by chat.html & data_chat.html for expanded chart/table views
 */
(function () {
    'use strict';

    let panelEl = null;
    let overlayEl = null;
    let isOpen = false;

    /**
     * Initialize the panel (call once on DOMContentLoaded).
     * Expects elements with ids: chat-panel, chat-panel-overlay
     */
    function init() {
        panelEl = document.getElementById('chat-panel');
        overlayEl = document.getElementById('chat-panel-overlay');

        if (!panelEl || !overlayEl) {
            console.warn('ChatPanel: #chat-panel or #chat-panel-overlay not found');
            return;
        }

        // Close on overlay click
        overlayEl.addEventListener('click', close);

        // Close button inside panel
        const closeBtn = panelEl.querySelector('.chat-panel-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', close);
        }

        // ESC key to close
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && isOpen) close();
        });
    }

    /**
     * Open panel with given title and HTML content.
     * @param {string} title
     * @param {string|HTMLElement} content - HTML string or DOM element
     * @param {{ actions?: Array<{label:string, icon?:string, onClick:Function, className?:string}> }} opts
     */
    function open(title, content, opts) {
        if (!panelEl) init();
        if (!panelEl) return;

        opts = opts || {};

        // Title
        const titleEl = panelEl.querySelector('.chat-panel-header h3');
        if (titleEl) titleEl.textContent = title || 'Detail';

        // Body
        const bodyEl = panelEl.querySelector('.chat-panel-body');
        if (bodyEl) {
            bodyEl.innerHTML = '';
            if (typeof content === 'string') {
                bodyEl.innerHTML = content;
            } else if (content instanceof HTMLElement) {
                bodyEl.appendChild(content);
            }
        }

        // Actions
        const actionsEl = panelEl.querySelector('.chat-panel-actions');
        if (actionsEl) {
            actionsEl.innerHTML = '';
            if (opts.actions && opts.actions.length) {
                opts.actions.forEach(function (action) {
                    const btn = document.createElement('button');
                    btn.className = action.className || 'chat-btn';
                    btn.innerHTML = (action.icon ? '<i class="' + action.icon + '"></i> ' : '') + action.label;
                    btn.addEventListener('click', action.onClick);
                    actionsEl.appendChild(btn);
                });
                actionsEl.style.display = 'flex';
            } else {
                actionsEl.style.display = 'none';
            }
        }

        // Show
        panelEl.classList.add('open');
        overlayEl.classList.add('open');
        isOpen = true;
    }

    /**
     * Close the panel.
     */
    function close() {
        if (!panelEl) return;
        panelEl.classList.remove('open');
        overlayEl.classList.remove('open');
        isOpen = false;
    }

    /**
     * Check if panel is open.
     */
    function getIsOpen() {
        return isOpen;
    }

    // Export
    window.ChatPanel = {
        init: init,
        open: open,
        close: close,
        isOpen: getIsOpen
    };
})();
