/*
 * Shared client-side date helpers for scheduler UI surfaces
 * (document_processor/job_detail.html, document_scheduler.html, ...).
 *
 * The scheduler API and server templates emit NAIVE UTC datetimes — no 'Z',
 * e.g. "2026-08-30T15:55:00" or "2026-08-30 15:55:00". A bare `new Date(...)`
 * on such a string reads it as LOCAL time, shifting every display by the UTC
 * offset (+4h in EDT). All parsing/rendering of server datetimes must go
 * through these helpers, which stamp the 'Z' first and format in the
 * browser's local timezone.
 *
 * static/js/monitoring.js (workflow monitor) carries its own copies of the
 * same conventions — keep the two in sync.
 */

function normalizeUtcDateString(dateStr) {
    // Add 'Z' if the string looks like a UTC datetime but lacks a timezone
    if (typeof dateStr === 'string' && !dateStr.endsWith('Z') && !dateStr.includes('+') && !dateStr.includes('T')) {
        // Basic date/time format like "2025-05-16 22:27:00"
        dateStr = dateStr.replace(' ', 'T') + 'Z';
    } else if (typeof dateStr === 'string' && dateStr.match(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/)) {
        // Optional seconds + fractional seconds: SQL DATETIME isoformats with
        // microseconds (…:00.003000) — without this branch match, such values
        // skipped the 'Z' stamp and silently rendered as local (+4h).
        dateStr += 'Z';
    }
    return dateStr;
}

// Parse a server datetime (naive-UTC string or Date) into a Date object.
function parseUtcDate(value) {
    if (value instanceof Date) {
        return value;
    }
    return new Date(normalizeUtcDateString(value));
}

// Display format in the browser's local timezone, e.g. "8/30/2026, 11:55:00 AM".
function formatDateTime(value) {
    const date = parseUtcDate(value);
    return isNaN(date.getTime()) ? '' : date.toLocaleString();
}

// Local "YYYY-MM-DD HH:mm" — the format the datetimepicker input fields use.
function formatDateTimeForInput(value) {
    const date = parseUtcDate(value);
    if (isNaN(date.getTime())) {
        return '';
    }
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    return `${year}-${month}-${day} ${hours}:${minutes}`;
}

function getIntervalDescription(schedule) {
    const parts = [];
    if (schedule.interval_seconds) {
        parts.push(`${schedule.interval_seconds} second${schedule.interval_seconds !== 1 ? 's' : ''}`);
    }
    if (schedule.interval_minutes) {
        parts.push(`${schedule.interval_minutes} minute${schedule.interval_minutes !== 1 ? 's' : ''}`);
    }
    if (schedule.interval_hours) {
        parts.push(`${schedule.interval_hours} hour${schedule.interval_hours !== 1 ? 's' : ''}`);
    }
    if (schedule.interval_days) {
        parts.push(`${schedule.interval_days} day${schedule.interval_days !== 1 ? 's' : ''}`);
    }
    if (schedule.interval_weeks) {
        parts.push(`${schedule.interval_weeks} week${schedule.interval_weeks !== 1 ? 's' : ''}`);
    }
    return `Every ${parts.join(', ')}`;
}

// Server-rendered UTC values: <span data-utc="{{ dt.isoformat() }}Z"></span> → local text.
function convertDataUtcElements() {
    document.querySelectorAll('[data-utc]').forEach(el => {
        const date = parseUtcDate(el.dataset.utc || '');
        if (!isNaN(date.getTime())) {
            el.textContent = formatDateTimeForInput(date);
        }
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', convertDataUtcElements);
} else {
    convertDataUtcElements();
}
