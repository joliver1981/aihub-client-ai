/**
 * dca-tts-flush.js
 *
 * Defensive flush for the browser's window.speechSynthesis queue.
 *
 * Chrome and Edge persist the speechSynthesis utterance queue across page
 * navigations and reloads. If the user closed a tab mid-speech, those
 * utterances will start playing the moment the browser mounts the next
 * page — in the OS robot voice (since they bypass our nicer OpenAI TTS),
 * with content from a prior conversation. Awful UX, hard to track down.
 *
 * One-shot cancel() inside DOMContentLoaded isn't enough: Chrome can
 * begin playing utterances before JS runs, and in some versions auto-
 * resumes the queue if it was "paused" by the navigation. So:
 *   - We run the cancel inline in <head>, the earliest possible point.
 *   - We re-cancel every 100ms for the first 3 seconds.
 *   - We also cancel on visibilitychange / focus to catch the user
 *     coming back to a tab whose queue was paused.
 *
 * This script is intentionally TINY and side-effect-only. Include it
 * as the very first <script> on every DCA page (gallery, runtime,
 * my-sessions, etc.) before anything else.
 */
(function () {
    'use strict';
    var W = (typeof window !== 'undefined') ? window : null;
    if (!W) return;

    function flush() {
        try {
            if (W.speechSynthesis) {
                W.speechSynthesis.cancel();
            }
        } catch (_) { /* non-fatal */ }
    }

    // 1. Immediate flush — runs as soon as this script tag is parsed.
    flush();

    // 2. Repeated flush for the first 3s. Chrome occasionally re-queues
    //    utterances that were "in-flight" when the previous page unloaded;
    //    this catches them before the user hears them.
    var attempts = 0;
    var iv = setInterval(function () {
        flush();
        attempts++;
        if (attempts > 30) clearInterval(iv);   // 30 * 100ms = 3s
    }, 100);

    // 3. When the tab becomes visible again, flush again. Catches the
    //    case where the user was on another tab while a queue piled up.
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) flush();
    });
    W.addEventListener('focus', flush);

    // 4. Flush on the way OUT, too — so what we queue from THIS page
    //    doesn't leak into whatever the user opens next. The runtime
    //    voice controller does the same, but having it here covers
    //    pages without that controller (gallery, my-sessions).
    W.addEventListener('pagehide', flush);
    W.addEventListener('beforeunload', flush);

    // 5. bfcache (back/forward cache) defense — THE big one.
    //    Modern browsers snapshot a fully-running page (including
    //    <audio> elements with blob URLs loaded, paused mid-stream)
    //    and restore it byte-for-byte on back/forward navigation.
    //    Without this, the OpenAI TTS audio that was paused when the
    //    user left the page resumes playback the moment they come back
    //    — replaying past conversation in the nice human voice.
    //
    //    The defense:
    //      a. On pagehide, mark the page so we KNOW we got bfcached.
    //      b. On pageshow with event.persisted=true, we ARE being
    //         restored from bfcache — kill everything that could play
    //         audio, including any <audio> elements that snuck through.
    W.addEventListener('pagehide', function (e) {
        // Best-effort stop on every <audio> element so even if the
        // browser bfcaches us, audio doesn't resume on restore.
        try {
            document.querySelectorAll('audio').forEach(function (a) {
                try { a.pause(); a.currentTime = 0; a.src = ''; a.load(); } catch (_) {}
            });
        } catch (_) {}
    });
    W.addEventListener('pageshow', function (e) {
        if (e && e.persisted) {
            // We're being restored from bfcache. Don't trust ANY
            // existing audio state. Hard-reload for a clean page.
            try {
                document.querySelectorAll('audio').forEach(function (a) {
                    try { a.pause(); a.src = ''; } catch (_) {}
                });
            } catch (_) {}
            flush();
            try { W.location.reload(); } catch (_) {}
            return;
        }
        // Even on a normal show, flush once more — covers the rare
        // case where speechSynthesis was queued before this script ran.
        flush();
    });
})();
