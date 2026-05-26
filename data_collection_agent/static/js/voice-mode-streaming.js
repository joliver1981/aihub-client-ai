/**
 * voice-mode-streaming.js — Path B (streaming hybrid) voice controller.
 *
 * Wraps the existing platform `SpeechRecognitionManager` (Web Speech API +
 * Whisper fallback) for STT, the new `/api/data-collection/message-stream`
 * SSE endpoint for sentence-chunked agent responses, and the new
 * `/api/data-collection/tts` endpoint for per-sentence speech synthesis.
 *
 * Push-to-talk for v1 (tap mic, speak, tap to stop). Captions always
 * render in the existing chat — voice is additive, not replacing text.
 *
 * UX states (driven by `mic.dataset.state`):
 *   idle        — gradient mic, "Tap to speak"
 *   listening   — red pulse, "Listening…"
 *   processing  — gray spinner, "Thinking…"
 *   speaking    — blue pulse, "Speaking…" (tap to interrupt)
 *   error       — red, "Tap to retry"
 *
 * Path D (OpenAI Realtime) is layered on top of the same UX shell by
 * voice-mode.js, which prefers D and falls back to this controller.
 */

class StreamingVoiceController {
    /**
     * @param {DataCollectionApp} app    - the runtime app instance
     * @param {object} elements          - { $mic, $state, $voiceComposer, $textComposer, $voiceToggle, $muteToggle }
     */
    constructor(app, elements) {
        this.app = app;
        this.$mic = elements.$mic;
        this.$state = elements.$state;
        this.$voiceComposer = elements.$voiceComposer;
        this.$textComposer = elements.$textComposer;
        this.$voiceToggle = elements.$voiceToggle;
        this.$muteToggle = elements.$muteToggle;
        this.$cancel = elements.$cancel;

        this.active = false;     // voice mode on?
        this.muted = false;      // AI voice muted? (mic still works)
        this.busy = false;       // currently processing a turn?
        this.audioQueue = [];    // queued <audio> elements for sentence playback
        this.currentAudio = null;
        this.recognizer = null;  // SpeechRecognitionManager instance, lazy-created
        this.lastTranscript = '';
        // Bumped on every _stopPlayback() / deactivate(). In-flight
        // _enqueueTTS calls capture this at start; if it differs when
        // they finish, the response is discarded. Prevents the bug
        // where a TTS fetch started before stop completes after stop
        // and re-pollutes the queue.
        this._playbackGeneration = 0;
        // Counter for in-flight TTS fetches. We never auto-listen until
        // this is back to zero (and the queue is drained), otherwise
        // the mic can open while audio is still being fetched and
        // capture the AI's own speech as the user's reply.
        this._pendingTtsCount = 0;

        // Hands-free turn-taking state ------------------------------------
        // Defaults; overwritten by setSettings() once the server resolves
        // the schema/app/JWT settings hierarchy.
        this.settings = {
            auto_listen: true,
            silence_threshold_ms: 1500,
            listen_timeout_ms: 30000,
            auto_listen_only_when_collecting: true,
        };
        this._silenceTimer = null;        // fires when silence_threshold elapses after speech
        this._listenTimeoutTimer = null;  // fires if listen_timeout elapses with no speech
        this._lastResultAt = 0;           // timestamp of the most recent partial/final result
        this._heardSpeech = false;        // any partial result since mic opened?
        // Tracks the last phase reported by the agent so auto-listen can be
        // suppressed when the conversation is no longer collecting input
        // (review screen, submitted, error).
        this._lastPhase = 'collecting';

        // Belt-and-suspenders: cancel any pending browser-TTS utterances
        // left over from a previous page load. Chrome/Edge keep the
        // speechSynthesis queue alive across navigations, which would
        // otherwise cause stale audio (in the robotic fallback voice) to
        // start playing the moment this controller mounts.
        try { window.speechSynthesis && window.speechSynthesis.cancel(); } catch (_) {}

        // Prevent the SAME class of bug going forward: when the user
        // navigates away or closes the tab, flush both our audio queue
        // and the browser's speechSynthesis queue so nothing carries
        // over to whatever they open next. pagehide is the right event
        // (covers bfcache navigations); beforeunload is a fallback.
        const flushOnExit = () => {
            try { this._stopPlayback(); } catch (_) {}
            try { window.speechSynthesis && window.speechSynthesis.cancel(); } catch (_) {}
        };
        window.addEventListener('pagehide', flushOnExit);
        window.addEventListener('beforeunload', flushOnExit);

        this._wireMic();
    }

    /**
     * Apply server-resolved voice settings (default_on, auto_listen,
     * silence_threshold_ms, listen_timeout_ms, auto_listen_only_when_collecting).
     * Called by data-collection.js after fetching /voice-settings.
     */
    setSettings(settings) {
        if (!settings || typeof settings !== 'object') return;
        // Merge into existing — unknown keys ignored
        ['auto_listen', 'silence_threshold_ms', 'listen_timeout_ms',
         'auto_listen_only_when_collecting'].forEach(k => {
            if (settings[k] !== undefined) this.settings[k] = settings[k];
        });
    }

    /**
     * Update the controller's notion of the current conversation phase, so
     * it knows whether to auto-listen after the AI speaks. Called from the
     * SSE message-stream handler each time a metadata event comes in.
     */
    setPhase(phase) {
        if (phase) this._lastPhase = phase;
    }

    /**
     * Suppress the next auto-listen cycle. The next time the AI finishes
     * speaking, the mic will fall back to "Tap to speak" instead of
     * reopening automatically. Resets to default after one cycle so the
     * user's next manual tap restores normal hands-free flow.
     *
     * Triggered by the `pause_listening` action emitted by the agent's
     * pause_listening tool when the user verbally signals stop / nevermind /
     * pause / hold-on / etc.
     */
    pauseAutoListenOnce(reason) {
        this._suppressAutoListenOnce = true;
        if (reason) console.log('[voice] auto-listen paused:', reason);
        // If we were about to auto-listen, also stop any running mic
        if (this.recognizer && this.recognizer.isListening) {
            try { this.recognizer.stop(); } catch (_) {}
        }
        this._setState('idle', 'Tap to speak');
    }

    // ------------------------------------------------------------------
    // Public API used by voice-mode.js
    // ------------------------------------------------------------------

    /**
     * Activate voice mode. Persists the flag on the session, swaps the
     * composer UI, and prepares (but does not start) the recognizer.
     */
    async activate() {
        if (this.active) return;
        this.active = true;
        this.$voiceToggle.setAttribute('aria-pressed', 'true');
        this.$muteToggle.style.display = 'inline-flex';
        this.$textComposer.style.display = 'none';
        this.$voiceComposer.style.display = 'flex';
        this._setState('idle', 'Tap to speak');
        try {
            await this._persistVoiceMode(true);
        } catch (e) { /* non-fatal — server-side prompt addendum simply won't apply */ }
        try { localStorage.setItem('dca-voice-mode', 'on'); } catch (_) {}
    }

    /**
     * Deactivate voice mode. Stops any in-flight playback, restores the
     * text composer, and clears the recognizer.
     */
    async deactivate() {
        if (!this.active) return;
        this._stopPlayback();
        if (this.recognizer && this.recognizer.isListening) {
            try { this.recognizer.stop(); } catch (_) {}
        }
        this.active = false;
        this.$voiceToggle.setAttribute('aria-pressed', 'false');
        this.$muteToggle.style.display = 'none';
        this.$voiceComposer.style.display = 'none';
        this.$textComposer.style.display = 'flex';
        try {
            await this._persistVoiceMode(false);
        } catch (e) { /* non-fatal */ }
        try { localStorage.setItem('dca-voice-mode', 'off'); } catch (_) {}
    }

    /**
     * Toggle the AI's audio output. Mic input stays enabled either way.
     */
    setMuted(muted) {
        this.muted = !!muted;
        this.$muteToggle.setAttribute('aria-pressed', muted ? 'true' : 'false');
        const icon = this.$muteToggle.querySelector('i');
        if (icon) icon.className = muted ? 'fas fa-volume-xmark' : 'fas fa-volume-high';
        if (muted) this._stopPlayback();
    }

    // ------------------------------------------------------------------
    // Mic interaction
    // ------------------------------------------------------------------

    _wireMic() {
        this.$mic.addEventListener('click', () => this._onMicClick());
        // Spacebar keyboard shortcut for push-to-talk on desktop
        document.addEventListener('keydown', (e) => {
            if (!this.active) return;
            if (e.code !== 'Space') return;
            // Don't hijack space when typing in an input/textarea
            const t = e.target && e.target.tagName;
            if (t === 'INPUT' || t === 'TEXTAREA' || (e.target && e.target.isContentEditable)) return;
            if (this.busy && this.currentAudio) {
                e.preventDefault();
                this._onMicClick();
            } else if (!this.busy && (!this.recognizer || !this.recognizer.isListening)) {
                e.preventDefault();
                this._onMicClick();
            }
        });
    }

    _onMicClick() {
        // 1. Mid-AI-speech → user wants to interrupt
        if (this.currentAudio || this.audioQueue.length > 0) {
            this._stopPlayback();
            this._beginListening();
            return;
        }
        // 2. Currently listening → user is done, finalize
        if (this.recognizer && this.recognizer.isListening) {
            try { this.recognizer.stop(); } catch (_) {}
            return;
        }
        // 3. Currently processing a turn → ignore (server is busy)
        if (this.busy) return;
        // 4. Idle → start listening
        this._beginListening();
    }

    _beginListening() {
        this.lastTranscript = '';
        if (!this.recognizer) {
            // Lazy-create. Use the platform manager that already wraps
            // Web Speech + Whisper fallback. Wrap in try/catch so any
            // construction failure (e.g. class not on window under SES)
            // doesn't leave the mic visually stuck in listening state.
            if (typeof window.SpeechRecognitionManager !== 'function') {
                this._setState('error', 'Speech recognition unavailable in this browser');
                console.error('[voice] window.SpeechRecognitionManager is missing — '
                    + 'the dca-speech-recognition.js script may have failed to load.');
                setTimeout(() => this._setState('idle', 'Tap to speak'), 3000);
                return;
            }
            try {
                this.recognizer = this._createRecognizer();
            } catch (e) {
                console.error('[voice] failed to create recognizer:', e);
                this._setState('error', 'Could not initialize the mic');
                setTimeout(() => this._setState('idle', 'Tap to speak'), 3000);
                return;
            }
            console.log('[voice] SpeechRecognitionManager created; mode =', this.recognizer.mode,
                        '| supported =', this.recognizer.isSupported);
            if (this.recognizer.mode === 'none') {
                this._onRecognizerError(
                    'unsupported',
                    'Speech recognition not supported in this browser.'
                );
                return;
            }
        }
        this._setState('listening', 'Listening… (tap mic when done)');
        try {
            this.recognizer.start();
        } catch (e) {
            console.warn('[voice] start() threw:', e);
            this._onRecognizerError('start_failed', String(e));
            return;
        }
        // Hands-free timers: listen_timeout fires if the user never speaks;
        // silence_timer is armed by _noteSpeechActivity once they do.
        this._heardSpeech = false;
        this._lastResultAt = 0;
        this._clearVoiceTimers();
        if (this.settings.listen_timeout_ms > 0) {
            this._listenTimeoutTimer = setTimeout(() => {
                if (!this._heardSpeech && this.recognizer && this.recognizer.isListening) {
                    console.log('[voice] listen timeout — closing mic with no speech');
                    try { this.recognizer.stop(); } catch (_) {}
                }
            }, this.settings.listen_timeout_ms);
        }
    }

    /**
     * Called from the recognizer's onResult handler whenever the user says
     * something (interim or final). Refreshes the silence timer — if no
     * further speech comes in within `silence_threshold_ms`, we treat the
     * user as done and close the mic, which routes through onEnd ->
     * _onRecognizerEnd to send the transcript.
     */
    _noteSpeechActivity() {
        this._heardSpeech = true;
        this._lastResultAt = Date.now();
        if (this._silenceTimer) {
            clearTimeout(this._silenceTimer);
            this._silenceTimer = null;
        }
        const threshold = this.settings.silence_threshold_ms;
        if (threshold > 0) {
            this._silenceTimer = setTimeout(() => {
                // Confirm silence: nothing new came in since we armed the timer
                if (Date.now() - this._lastResultAt >= threshold - 50
                    && this.recognizer && this.recognizer.isListening) {
                    console.log('[voice] silence detected — closing mic');
                    try { this.recognizer.stop(); } catch (_) {}
                }
            }, threshold);
        }
    }

    _clearVoiceTimers() {
        if (this._silenceTimer) { clearTimeout(this._silenceTimer); this._silenceTimer = null; }
        if (this._listenTimeoutTimer) { clearTimeout(this._listenTimeoutTimer); this._listenTimeoutTimer = null; }
    }

    /**
     * Build the SpeechRecognitionManager. Pulled into its own method so the
     * constructor invocation stays in one place and exceptions are easy to
     * surface. The previous inline construction silently turned the mic red
     * when window.SpeechRecognitionManager was missing.
     */
    _createRecognizer() {
        return new window.SpeechRecognitionManager({
                // continuous: true keeps the recognizer running until the user
                // explicitly taps the mic to stop. This matches our push-to-talk
                // UX. With continuous=false some browsers end the session after
                // the first utterance + pause, which can race with the user
                // figuring out the UI.
                continuous: true,
                interimResults: true,
                useWhisperFallback: true,
                whisperEndpoint: '/api/transcribe',
                inputElementId: '__dca_voice_dummy__',
                buttonElementId: '__dca_voice_dummy_btn__',
                onStart: () => {
                    console.log('[voice] recognizer started; mode =', this.recognizer && this.recognizer.mode);
                },
                onResult: (r) => {
                    // Show what the mic is hearing live, so the user can SEE
                    // the system is working before they tap to stop.
                    this.lastTranscript = (r.final || r.full || '').trim();
                    const live = (r.full || r.final || '').trim();
                    if (live) {
                        // Truncate so it doesn't visually overflow
                        const display = live.length > 80 ? live.slice(0, 77) + '…' : live;
                        this._setState('listening', `"${display}"`);
                        this._noteSpeechActivity();
                    }
                },
                onEnd: (finalText) => {
                    // SpeechRecognitionManager passes its own finalTranscript
                    // here. Fall back to ours if it's missing.
                    if (finalText && finalText.trim()) this.lastTranscript = finalText.trim();
                    this._onRecognizerEnd();
                },
                onError: (code, msg) => this._onRecognizerError(code, msg),
            });
    }

    _onRecognizerEnd() {
        // Clear hands-free timers — they're scoped to the listening session
        this._clearVoiceTimers();
        // Recognizer ended naturally — we have lastTranscript ready
        const text = (this.lastTranscript || '').trim();
        console.log('[voice] recognizer ended; transcript =', JSON.stringify(text));
        if (!text) {
            // Nothing was captured. Most common causes:
            //   - User didn't speak loudly enough for Web Speech to register
            //   - Mic permission was denied silently (no onerror in some browsers)
            //   - Recognizer ended before any speech was detected
            this._setState('error', "Didn't catch anything — tap to try again");
            setTimeout(() => {
                if (!this.busy && !this.recognizer.isListening) {
                    this._setState('idle', 'Tap to speak');
                }
            }, 2500);
            return;
        }
        // Render the user's words as a chat bubble (the app already has _appendMsg)
        this.app._appendMsg('user', text);
        this._sendToAgentStreaming(text);
    }

    _onRecognizerError(code, msg) {
        console.warn('[voice] recognizer error:', code, msg);
        // Permission denied or the user cancelled — silently fall back to idle
        if (code === 'no-speech' || code === 'aborted') {
            this._setState('idle', 'Tap to speak');
            return;
        }
        this._setState('error', msg || 'Mic error — tap to retry');
        setTimeout(() => {
            if (!this.recognizer || !this.recognizer.isListening) {
                this._setState('idle', 'Tap to speak');
            }
        }, 2500);
    }

    // ------------------------------------------------------------------
    // SSE message-stream consumer
    // ------------------------------------------------------------------

    async _sendToAgentStreaming(text) {
        this.busy = true;
        this._setState('processing', 'Thinking…');

        // Prepare the assistant bubble we'll progressively fill
        const bubble = this._createAssistantBubble();

        // Watchdog: if NO event has arrived from the server in this window,
        // we treat the request as hung and abort. The first few events
        // come fast (`progress: received` is yielded immediately), so
        // anything close to a minute means something's stuck.
        const watchdogMs = 45_000;
        const abortCtrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
        let lastEventAt = Date.now();
        let watchdogTimer = setInterval(() => {
            if (Date.now() - lastEventAt > watchdogMs) {
                console.warn('[voice] SSE watchdog tripped — no events for', watchdogMs, 'ms; aborting');
                clearInterval(watchdogTimer); watchdogTimer = null;
                try { abortCtrl && abortCtrl.abort(); } catch (_) {}
                this._finishWithError(bubble,
                    'The server is taking too long. Please try again — your progress is saved.');
            }
        }, 5000);
        const cancelWatchdog = () => {
            if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null; }
        };

        // Use fetch + a manual SSE parser (EventSource doesn't support POST).
        let response;
        try {
            response = await fetch('/api/data-collection/message-stream', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
                body: JSON.stringify({
                    session_id: this.app.sessionId,
                    message: text,
                }),
                signal: abortCtrl ? abortCtrl.signal : undefined,
            });
        } catch (e) {
            cancelWatchdog();
            const aborted = e && (e.name === 'AbortError' || /abort/i.test(String(e)));
            if (!aborted) {
                this._finishWithError(bubble, 'Network error — please retry.');
            }
            return;
        }
        if (!response.ok || !response.body) {
            cancelWatchdog();
            const err = await response.text().catch(() => '');
            this._finishWithError(bubble, `Server error: ${err.slice(0, 200)}`);
            return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let firstChunkSeen = false;
        let metadata = null;
        let richBlocks = null;
        let actions = null;
        const tickWatchdog = () => { lastEventAt = Date.now(); };

        const handleEvent = (eventName, dataStr) => {
            tickWatchdog();
            let payload;
            try { payload = JSON.parse(dataStr); } catch { payload = dataStr; }

            if (eventName === 'transcript_chunk') {
                const chunk = (payload && payload.text) || '';
                this._appendToBubble(bubble, chunk);
                if (!firstChunkSeen) {
                    firstChunkSeen = true;
                    this._setState('speaking', 'Speaking…');
                }
                if (!this.muted) this._enqueueTTS(chunk);
            } else if (eventName === 'transcript_final') {
                // Bubble is already populated from the chunks
            } else if (eventName === 'rich_blocks') {
                richBlocks = payload;
            } else if (eventName === 'actions') {
                actions = payload;
            } else if (eventName === 'metadata') {
                metadata = payload;
            } else if (eventName === 'progress') {
                // Surface the current backend stage so the user sees that
                // SOMETHING is happening, instead of an indefinite
                // "Thinking…". Stage names match the SSE generator.
                const stage = (payload && payload.stage) || '';
                const labels = {
                    'received': 'Thinking…',
                    'extracting': 'Reading what you said…',
                    'extract_failed': 'Couldn’t pre-process — continuing…',
                    'agent_thinking': 'Thinking…',
                };
                this._setState('processing', labels[stage] || `Working… (${stage})`);
            } else if (eventName === 'error') {
                this._finishWithError(bubble, (payload && payload.error) || 'Stream error');
            }
        };

        try {
            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                // SSE messages are separated by blank lines (\n\n)
                let idx;
                while ((idx = buffer.indexOf('\n\n')) >= 0) {
                    const block = buffer.slice(0, idx);
                    buffer = buffer.slice(idx + 2);
                    let evName = 'message';
                    const dataLines = [];
                    block.split('\n').forEach(line => {
                        if (line.startsWith('event:')) evName = line.slice(6).trim();
                        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
                    });
                    if (dataLines.length) handleEvent(evName, dataLines.join('\n'));
                }
            }
        } catch (e) {
            cancelWatchdog();
            const aborted = e && (e.name === 'AbortError' || /abort/i.test(String(e)));
            if (!aborted) {
                this._finishWithError(bubble, `Stream interrupted: ${e.message || e}`);
            }
            return;
        }
        cancelWatchdog();

        // Render rich blocks (tables, recap, etc.) — explicit error
        // logging so a render failure doesn't silently disappear.
        if (richBlocks && richBlocks.length && this.app.renderer) {
            console.log('[voice] rendering %d rich block(s):', richBlocks.length,
                        richBlocks.map(b => (b && b.type) || '?'));
            const container = document.createElement('div');
            container.className = 'dca-rich-content';
            try {
                const html = this.app.renderer.render({ blocks: richBlocks });
                if (!html || !html.trim()) {
                    console.error('[voice] renderer returned empty HTML for blocks:',
                        JSON.stringify(richBlocks).slice(0, 500));
                    container.style.cssText = 'border:1px dashed #f87171;color:#fca5a5;padding:0.5rem;font-size:0.78rem;border-radius:6px;';
                    container.textContent =
                        `Renderer returned empty output for ${richBlocks.length} block(s) of types: `
                        + richBlocks.map(b => (b && b.type) || '?').join(', ');
                } else {
                    container.innerHTML = html;
                }
            } catch (e) {
                console.error('[voice] richContent render threw:', e,
                    'blocks were:', JSON.stringify(richBlocks).slice(0, 500));
                container.style.cssText = 'border:1px dashed #f87171;color:#fca5a5;padding:0.5rem;font-size:0.78rem;border-radius:6px;font-family:monospace;white-space:pre-wrap;';
                container.textContent = `Render error: ${e.message || e}\n\n`
                    + 'Block payload: ' + JSON.stringify(richBlocks, null, 2);
            }
            bubble.appendChild(container);
        } else if (richBlocks && richBlocks.length && !this.app.renderer) {
            console.error('[voice] got', richBlocks.length, 'rich blocks but no renderer initialized');
        }
        // Update progress panel and side-channel actions just like the regular path
        if (metadata) {
            this.app.lastMetadata = metadata;
            this.app._refreshProgress(metadata);
            // Track phase for the auto-listen gate
            this.setPhase(metadata.phase || metadata.status);
        }
        if (actions && actions.length) {
            this.app._handleActions(actions);
        }

        // After the response is fully streamed, wait for audio queue to drain
        // AND any in-flight TTS fetches to settle before deciding what to do.
        // _maybeReturnToIdle picks between "Tap to speak" and an automatic
        // re-listen based on phase + settings.
        this.busy = false;
        // If the queue + current audio + pending TTS are all empty, return
        // to idle now. Otherwise, individual playback / fetch completion
        // hooks will call _maybeReturnToIdle when each settles.
        if (!this.currentAudio
            && this.audioQueue.length === 0
            && (this._pendingTtsCount || 0) === 0) {
            this._maybeReturnToIdle();
        }
    }

    _createAssistantBubble() {
        const row = document.createElement('div');
        row.className = 'dca-msg-row assistant';
        const bubble = document.createElement('div');
        bubble.className = 'dca-msg-bubble';
        row.appendChild(bubble);
        this.app.$messages.appendChild(row);
        this.app.$messages.scrollTop = this.app.$messages.scrollHeight;
        return bubble;
    }

    _appendToBubble(bubble, text) {
        // Append with a leading space if the bubble already has content
        const existing = bubble.dataset.raw || '';
        const next = existing ? existing + ' ' + text : text;
        bubble.dataset.raw = next;
        bubble.innerHTML = this.app._textToHtml(next);
        this.app.$messages.scrollTop = this.app.$messages.scrollHeight;
    }

    _finishWithError(bubble, message) {
        if (bubble) {
            const errEl = document.createElement('div');
            errEl.style.cssText = 'color:#fca5a5;font-size:0.78rem;margin-top:0.4rem;';
            errEl.textContent = message;
            bubble.appendChild(errEl);
        }
        this.busy = false;
        this._setState('error', message.slice(0, 60));
        setTimeout(() => {
            if (!this.busy) this._setState('idle', 'Tap to speak');
        }, 3000);
    }

    // ------------------------------------------------------------------
    // TTS playback queue (chunked sentence-by-sentence)
    // ------------------------------------------------------------------

    async _enqueueTTS(text) {
        if (!text || !text.trim()) return;
        if (this.muted) return;
        if (!this.active) return;   // voice mode toggled off — drop silently
        const myGen = this._playbackGeneration;
        const stillCurrent = () => this.active && !this.muted
            && this._playbackGeneration === myGen;

        // Track this in-flight fetch so auto-listen won't open the mic
        // mid-fetch and capture the AI's own speech as the user's reply.
        this._pendingTtsCount = (this._pendingTtsCount || 0) + 1;
        const settle = () => {
            this._pendingTtsCount = Math.max(0, (this._pendingTtsCount || 0) - 1);
            // If everything's truly idle now, return the UI to its
            // proper resting state (which will auto-listen if appropriate).
            if (!this.busy
                && !this.currentAudio
                && this.audioQueue.length === 0
                && this._pendingTtsCount === 0) {
                this._maybeReturnToIdle();
            }
        };

        try {
            const resp = await fetch('/api/data-collection/tts', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text }),
            });

            if (!stillCurrent()) return;

            if (!resp.ok) { this._browserSpeak(text); return; }
            const ct = resp.headers.get('Content-Type') || '';
            if (ct.includes('application/json')) {
                const body = await resp.json().catch(() => ({}));
                if (!stillCurrent()) return;
                this._browserSpeak(text);
                return;
            }

            const blob = await resp.blob();
            if (!stillCurrent()) return;
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audio.addEventListener('ended', () => {
                URL.revokeObjectURL(url);
                this._playNextInQueue();
            });
            audio.addEventListener('error', () => {
                URL.revokeObjectURL(url);
                this._playNextInQueue();
            });

            if (!stillCurrent()) {
                try { URL.revokeObjectURL(url); } catch (_) {}
                return;
            }
            this.audioQueue.push(audio);
            if (!this.currentAudio) this._playNextInQueue();
        } catch (e) {
            console.warn('[voice] TTS request failed:', e);
            if (stillCurrent()) this._browserSpeak(text);
        } finally {
            settle();
        }
    }

    _playNextInQueue() {
        if (this.muted) {
            this.audioQueue.length = 0;
            this.currentAudio = null;
            this._maybeReturnToIdle();
            return;
        }
        if (this.audioQueue.length === 0) {
            this.currentAudio = null;
            this._maybeReturnToIdle();
            return;
        }
        this.currentAudio = this.audioQueue.shift();
        // Make sure mic state reflects "speaking"
        if (this.active && !this.busy) {
            this._setState('speaking', 'Speaking…');
        }
        this.currentAudio.play().catch(e => {
            console.warn('[voice] audio.play() rejected:', e);
            this._playNextInQueue();
        });
    }

    _stopPlayback() {
        // Bumping the generation invalidates any in-flight _enqueueTTS
        // promises so they can't push stale audio after we stop.
        this._playbackGeneration++;
        // Stop and tear down the currently-playing audio so the browser
        // won't snapshot it for bfcache.
        try {
            if (this.currentAudio) {
                this.currentAudio.pause();
                this.currentAudio.currentTime = 0;
                this.currentAudio.src = '';
                this.currentAudio.load();
            }
        } catch (_) {}
        this.currentAudio = null;
        // Also tear down anything queued so blob URLs get released and
        // the browser doesn't carry them forward.
        try {
            this.audioQueue.forEach(a => {
                try { a.pause(); a.src = ''; a.load(); } catch (_) {}
            });
        } catch (_) {}
        this.audioQueue.length = 0;
        // Also kill any in-flight browser speech
        try { window.speechSynthesis && window.speechSynthesis.cancel(); } catch (_) {}
    }

    _browserSpeak(text) {
        if (!('speechSynthesis' in window) || !text) return;
        try {
            const u = new SpeechSynthesisUtterance(text);
            u.rate = 1.0;
            u.pitch = 1.0;
            window.speechSynthesis.speak(u);
        } catch (e) {
            console.warn('[voice] browser TTS failed:', e);
        }
    }

    _maybeReturnToIdle() {
        if (this.busy || this.currentAudio || this.audioQueue.length > 0) return;
        // Audio fetches still in flight — those will call back into us
        // when they settle. Don't open the mic yet, otherwise it'll
        // pick up the AI's own speech a moment later.
        if ((this._pendingTtsCount || 0) > 0) return;

        // The AI just finished speaking. Decide whether to auto-reopen the
        // mic (hands-free flow) or fall back to "Tap to speak" (manual).
        if (this._shouldAutoListen()) {
            // Show the listening state IMMEDIATELY (red pulse) so the user
            // can see the mic is auto-engaging. The 250ms delay is there
            // so the audio device fully releases before we re-arm
            // getUserMedia; visually we want the red ring up the whole time.
            this._setState('listening', 'Listening…');
            setTimeout(() => {
                if (!this.active) return;
                if (this.busy || this.currentAudio || this.audioQueue.length > 0) return;
                if (this.recognizer && this.recognizer.isListening) return;
                this._beginListening();
            }, 250);
            return;
        }

        this._setState('idle', 'Tap to speak');
    }

    /**
     * Hands-free auto-listen gate. Returns true only when the conversation
     * is in a phase where we should automatically reopen the mic after the
     * AI finishes speaking.
     *
     * Suppressed when:
     *   - voice mode is off (defensive — shouldn't reach here)
     *   - the user has muted AI audio (manual control implied)
     *   - the auto_listen setting is off
     *   - auto_listen_only_when_collecting=true and the phase is anything
     *     other than collecting / section_confirm (so we DON'T re-arm the
     *     mic at the review screen, after submission, or on errors)
     */
    _shouldAutoListen() {
        if (!this.active) return false;
        if (!this.settings.auto_listen) return false;
        if (this.muted) return false;
        if (this._suppressAutoListenOnce) {
            // One-shot suppression honored — clear so the next normal cycle
            // (after a manual tap or another agent turn) resumes.
            this._suppressAutoListenOnce = false;
            return false;
        }
        if (this.settings.auto_listen_only_when_collecting) {
            const collectingPhases = new Set([
                'collecting', 'section_confirm', 'greeting',
                'in_progress',  // the session-status alias for 'collecting'
            ]);
            if (!collectingPhases.has(this._lastPhase)) return false;
        }
        return true;
    }

    // ------------------------------------------------------------------
    // Server-side voice_mode flag persistence
    // ------------------------------------------------------------------

    async _persistVoiceMode(enabled) {
        if (!this.app.sessionId) return;
        await fetch(`/api/data-collection/session/${this.app.sessionId}/voice-mode`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled }),
        });
    }

    // ------------------------------------------------------------------
    _setState(state, label) {
        this.$mic.dataset.state = state;
        if (label !== undefined) this.$state.textContent = label;
    }
}

window.StreamingVoiceController = StreamingVoiceController;
