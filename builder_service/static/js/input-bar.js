/**
 * Input Bar
 * Manages the chat input, send button state, keyboard shortcuts,
 * file attachments (button + drag-and-drop), and staged file chips.
 * Supports multi-line input with auto-grow.
 */

export class InputBar {
    /**
     * @param {HTMLElement} container - The input bar container
     * @param {object} callbacks
     * @param {function(string, Array<{file_id: string, filename: string}>): void} callbacks.onSend
     * @param {function(File[]): Promise<Array<{file_id, filename, size, content_type}>>} callbacks.onUpload
     * @param {function(string): Promise<void>} callbacks.onDeleteFile
     */
    constructor(container, callbacks) {
        this.container = container;
        this.input = container.querySelector('#chat-input');
        this.sendBtn = container.querySelector('#btn-send');
        this.attachBtn = container.querySelector('#btn-attach');
        this.onSend = callbacks.onSend;
        this.onUpload = callbacks.onUpload;
        this.onDeleteFile = callbacks.onDeleteFile;
        this._disabled = false;
        this._maxHeight = 120;

        /** @type {Array<{file_id: string, filename: string, size: number, content_type: string}>} */
        this._stagedFiles = [];

        // Create hidden file input
        this._fileInput = document.createElement('input');
        this._fileInput.type = 'file';
        this._fileInput.multiple = true;
        this._fileInput.className = 'hidden';
        this._fileInput.accept = '*/*';
        container.appendChild(this._fileInput);

        // Create staged files container (inserted before the input bar)
        this._stagedContainer = document.createElement('div');
        this._stagedContainer.id = 'staged-files';
        this._stagedContainer.className = 'staged-files hidden';
        container.parentNode.insertBefore(this._stagedContainer, container);

        // Create drag-and-drop overlay for the chat area
        this._dropOverlay = document.createElement('div');
        this._dropOverlay.id = 'drop-overlay';
        this._dropOverlay.className = 'drop-overlay hidden';
        this._dropOverlay.innerHTML = `
            <div class="drop-overlay-content">
                <svg class="w-10 h-10 text-cyber-cyan mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"/>
                </svg>
                <span class="text-sm font-medium text-zinc-300">Drop files to attach</span>
                <span class="text-xs text-zinc-500 mt-1">Max 50MB per file</span>
            </div>
        `;

        // The drop overlay goes on the main chat area
        const chatContainer = document.getElementById('chat-container');
        if (chatContainer) {
            chatContainer.style.position = 'relative';
            chatContainer.appendChild(this._dropOverlay);
        }

        this._bindEvents();
        this._autoGrow();
    }

    _bindEvents() {
        // Track IME composition state (e.g. CJK input methods)
        this._isComposing = false;
        this.input.addEventListener('compositionstart', () => { this._isComposing = true; });
        this.input.addEventListener('compositionend', () => { this._isComposing = false; });

        // Send on Enter (shift+enter or ctrl+enter for newline)
        this.input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !this._isComposing) {
                e.preventDefault();
                e.stopPropagation();
                this._send();
            }
        });

        // Send button click
        this.sendBtn.addEventListener('click', () => this._send());

        // Update send button state and auto-grow based on input content
        this.input.addEventListener('input', () => {
            this._updateSendState();
            this._autoGrow();
        });

        // Handle paste to auto-grow and paste files
        this.input.addEventListener('paste', (e) => {
            const files = this._getFilesFromEvent(e);
            if (files.length > 0) {
                e.preventDefault();
                this._handleFiles(files);
            } else {
                setTimeout(() => this._autoGrow(), 0);
            }
        });

        // Attach button
        this.attachBtn.addEventListener('click', () => {
            if (!this._disabled) {
                this._fileInput.click();
            }
        });

        // File input change
        this._fileInput.addEventListener('change', () => {
            if (this._fileInput.files.length > 0) {
                this._handleFiles(Array.from(this._fileInput.files));
                this._fileInput.value = ''; // Reset so same file can be re-selected
            }
        });

        // Drag-and-drop on the entire main area
        this._setupDragDrop();
    }

    _setupDragDrop() {
        const main = document.querySelector('main');
        if (!main) return;

        let dragCounter = 0;

        main.addEventListener('dragenter', (e) => {
            e.preventDefault();
            e.stopPropagation();
            dragCounter++;
            if (dragCounter === 1 && !this._disabled) {
                this._dropOverlay.classList.remove('hidden');
            }
        });

        main.addEventListener('dragleave', (e) => {
            e.preventDefault();
            e.stopPropagation();
            dragCounter--;
            if (dragCounter <= 0) {
                dragCounter = 0;
                this._dropOverlay.classList.add('hidden');
            }
        });

        main.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
        });

        main.addEventListener('drop', (e) => {
            e.preventDefault();
            e.stopPropagation();
            dragCounter = 0;
            this._dropOverlay.classList.add('hidden');

            if (this._disabled) return;

            const files = this._getFilesFromEvent(e);
            if (files.length > 0) {
                this._handleFiles(files);
            }
        });
    }

    _getFilesFromEvent(e) {
        const files = [];
        if (e.dataTransfer?.files) {
            for (const f of e.dataTransfer.files) {
                files.push(f);
            }
        } else if (e.clipboardData?.files) {
            for (const f of e.clipboardData.files) {
                files.push(f);
            }
        }
        return files;
    }

    async _handleFiles(files) {
        if (!this.onUpload) return;

        // Show uploading state on attach button
        this.attachBtn.classList.add('uploading');

        try {
            const result = await this.onUpload(files);
            if (result && result.files) {
                for (const fileMeta of result.files) {
                    this._stagedFiles.push(fileMeta);
                }
                this._renderStagedFiles();
                this._updateSendState();
            }
        } catch (err) {
            console.error('File upload failed:', err);
        } finally {
            this.attachBtn.classList.remove('uploading');
        }
    }

    _renderStagedFiles() {
        if (this._stagedFiles.length === 0) {
            this._stagedContainer.classList.add('hidden');
            this._stagedContainer.innerHTML = '';
            return;
        }

        this._stagedContainer.classList.remove('hidden');
        this._stagedContainer.innerHTML = '';

        for (const file of this._stagedFiles) {
            const chip = document.createElement('div');
            chip.className = 'file-chip';
            chip.innerHTML = `
                <svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="${this._getFileIcon(file.content_type)}"/>
                </svg>
                <span class="file-chip-name" title="${this._escapeAttr(file.filename)}">${this._escapeHtml(this._truncateName(file.filename, 24))}</span>
                <span class="file-chip-size">${this._formatSize(file.size)}</span>
                <button class="file-chip-remove" title="Remove file" data-file-id="${file.file_id}">
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                    </svg>
                </button>
            `;

            // Bind remove button
            chip.querySelector('.file-chip-remove').addEventListener('click', (e) => {
                const fileId = e.currentTarget.dataset.fileId;
                this._removeFile(fileId);
            });

            this._stagedContainer.appendChild(chip);
        }
    }

    async _removeFile(fileId) {
        // Remove from staged list
        this._stagedFiles = this._stagedFiles.filter(f => f.file_id !== fileId);
        this._renderStagedFiles();
        this._updateSendState();

        // Delete from server
        if (this.onDeleteFile) {
            try {
                await this.onDeleteFile(fileId);
            } catch (err) {
                console.error('Failed to delete file:', err);
            }
        }
    }

    _getFileIcon(contentType) {
        if (!contentType) return 'M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z';
        if (contentType.startsWith('image/')) return 'M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z';
        if (contentType.includes('pdf')) return 'M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z';
        if (contentType.includes('spreadsheet') || contentType.includes('excel') || contentType.includes('csv'))
            return 'M3 10h18M3 14h18M3 18h18M3 6h18';
        return 'M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z';
    }

    _formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    _truncateName(name, maxLen) {
        if (name.length <= maxLen) return name;
        const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')) : '';
        const base = name.slice(0, name.length - ext.length);
        const truncBase = base.slice(0, maxLen - ext.length - 3);
        return truncBase + '...' + ext;
    }

    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    _escapeAttr(text) {
        return text.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    _autoGrow() {
        const singleLineHeight = 24;
        this.input.style.height = '0px';
        const scrollHeight = this.input.scrollHeight;
        const newHeight = Math.max(singleLineHeight, Math.min(scrollHeight, this._maxHeight));
        this.input.style.height = newHeight + 'px';
        this.input.style.overflowY = scrollHeight > this._maxHeight ? 'auto' : 'hidden';
        const isMultiline = newHeight > singleLineHeight + 4;
        this.container.classList.toggle('multiline', isMultiline);
    }

    _send() {
        if (this._disabled) return;
        const text = this.input.value.trim();
        const hasFiles = this._stagedFiles.length > 0;
        if (!text && !hasFiles) return;

        // Collect file IDs
        const attachments = this._stagedFiles.map(f => ({
            file_id: f.file_id,
            filename: f.filename,
        }));

        // Clear input and staged files
        this.input.value = '';
        this._stagedFiles = [];
        this._renderStagedFiles();
        this._updateSendState();
        this._autoGrow();
        this.container.classList.remove('multiline');

        this.onSend(text, attachments);
    }

    _updateSendState() {
        const hasText = this.input.value.trim().length > 0;
        const hasFiles = this._stagedFiles.length > 0;
        this.sendBtn.classList.toggle('active', (hasText || hasFiles) && !this._disabled);
    }

    /** Disable input during streaming. */
    disable() {
        this._disabled = true;
        this.input.disabled = true;
        this.input.placeholder = 'Agent is responding...';
        this._updateSendState();
    }

    /** Re-enable input after streaming completes. */
    enable() {
        this._disabled = false;
        this.input.disabled = false;
        this.input.placeholder = 'Describe what you want to build...';
        this._updateSendState();
        this.input.focus();
    }

    /** Set input value programmatically (for quick action chips). */
    setValue(text) {
        this.input.value = text;
        this._updateSendState();
        this._autoGrow();
        this.input.focus();
    }

    /** Focus the input. */
    focus() {
        this.input.focus();
    }

    /** Get staged file count. */
    get stagedFileCount() {
        return this._stagedFiles.length;
    }
}
