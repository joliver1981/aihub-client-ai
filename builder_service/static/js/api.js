/**
 * API Client
 * Handles all communication with the builder service backend.
 * Uses fetch + ReadableStream for SSE since EventSource doesn't support POST.
 */

const API_BASE = '/api';

export class ApiClient {
    constructor() {
        this._abortController = null;
    }

    /**
     * Send a chat message and receive an async stream of SSE events.
     * 
     * Yields objects like:
     *   { event: "token",  data: { text: "Hello" } }
     *   { event: "status", data: { phase: "responding" } }
     *   { event: "done",   data: { session_id: "..." } }
     * 
     * @param {string} sessionId 
     * @param {string} message 
     * @returns {AsyncGenerator<{event: string, data: object}>}
     */
    /**
     * Upload files to the server.
     * @param {File[]} files - Array of File objects
     * @param {string|null} sessionId - Optional session to associate files with
     * @returns {Promise<{files: Array<{file_id, filename, size, content_type}>}>}
     */
    async uploadFiles(files, sessionId = null) {
        const formData = new FormData();
        for (const file of files) {
            formData.append('files', file);
        }
        if (sessionId) {
            formData.append('session_id', sessionId);
        }

        const res = await fetch(`${API_BASE}/upload`, {
            method: 'POST',
            body: formData,
        });

        if (!res.ok) {
            const err = await res.text();
            throw new Error(`Upload failed (${res.status}): ${err}`);
        }

        return res.json();
    }

    /** Delete an uploaded file. */
    async deleteUpload(fileId) {
        await fetch(`${API_BASE}/uploads/${fileId}`, { method: 'DELETE' });
    }

    /**
     * Send a chat message and receive an async stream of SSE events.
     *
     * @param {string} sessionId
     * @param {string} message
     * @param {string[]|null} attachments - Optional array of file_ids
     * @returns {AsyncGenerator<{event: string, data: object}>}
     */
    async *streamChat(sessionId, message, attachments = null) {
        // Cancel any in-flight request
        this.abort();
        this._abortController = new AbortController();

        const body = {
            session_id: sessionId,
            message: message,
        };
        if (attachments && attachments.length > 0) {
            body.attachments = attachments;
        }

        const response = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            signal: this._abortController.signal,
        });

        if (!response.ok) {
            const err = await response.text();
            throw new Error(`Chat failed (${response.status}): ${err}`);
        }

        // Parse SSE from the response stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let currentEvent = 'message';

        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    const trimmed = line.trim();

                    if (trimmed.startsWith('event:')) {
                        currentEvent = trimmed.slice(6).trim();
                    } else if (trimmed.startsWith('data:')) {
                        const raw = trimmed.slice(5).trim();
                        try {
                            const data = JSON.parse(raw);
                            yield { event: currentEvent, data };
                        } catch {
                            yield { event: currentEvent, data: { text: raw } };
                        }
                        currentEvent = 'message';
                    }
                    // Skip empty lines and comments
                }
            }
        } finally {
            reader.releaseLock();
        }
    }

    /** Abort any in-flight streaming request. */
    abort() {
        if (this._abortController) {
            this._abortController.abort();
            this._abortController = null;
        }
    }

    /** Create a new session. */
    async createSession(title = 'New Chat') {
        const res = await fetch(`${API_BASE}/sessions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title }),
        });
        return res.json();
    }

    /** List all sessions. */
    async listSessions() {
        const res = await fetch(`${API_BASE}/sessions`);
        const data = await res.json();
        return data.sessions || [];
    }

    /** Delete a session. */
    async deleteSession(sessionId) {
        await fetch(`${API_BASE}/sessions/${sessionId}`, { method: 'DELETE' });
    }

    /** Fetch messages for a session. */
    async getMessages(sessionId) {
        const res = await fetch(`${API_BASE}/sessions/${sessionId}/messages`);
        if (!res.ok) return [];
        const data = await res.json();
        return data.messages || [];
    }

    /** Health check. */
    async health() {
        const res = await fetch(`${API_BASE}/health`);
        return res.json();
    }

    /** Validate an authentication token from the main Flask app. */
    async validateToken(token) {
        const res = await fetch(`${API_BASE}/auth/validate-token`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token }),
        });
        return res.json();
    }

    /** Create a session with user context from a token. */
    async createSessionWithToken(token, title = 'New Chat') {
        const res = await fetch(`${API_BASE}/sessions/with-user?token=${encodeURIComponent(token)}&title=${encodeURIComponent(title)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        if (!res.ok) {
            const error = await res.json();
            throw new Error(error.detail || 'Failed to create authenticated session');
        }
        return res.json();
    }
}
