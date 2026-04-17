/**
 * Data Explorer v2 — Table Renderer
 * ===================================
 * Standalone table module with filtering, sorting, pagination, and CSV export.
 * Ported from richContentRenderer.js table logic — does NOT modify the original.
 */
(function () {
    'use strict';

    const PAGE_SIZE = 50;

    // Internal store: tableId -> { headers, allRows, filteredRows, sortCol, sortDir, page }
    const _tables = {};

    let _tableCounter = 0;

    /* ── Public API ────────────────────────────────────────── */

    /**
     * Render a table from structured data.
     * @param {Object} data - { headers: string[], rows: any[][] }  OR  Array of objects
     * @param {Object} opts - { title, tableId, pinnable, onPin }
     * @returns {string} HTML string
     */
    function renderTable(data, opts) {
        opts = opts || {};
        const tableId = opts.tableId || 'de-tbl-' + (++_tableCounter);

        let headers, rows;

        if (data && data.headers && data.rows) {
            headers = data.headers;
            rows = data.rows;
        } else if (Array.isArray(data) && data.length > 0 && typeof data[0] === 'object') {
            headers = Object.keys(data[0]);
            rows = data.map(function (obj) {
                return headers.map(function (h) { return obj[h]; });
            });
        } else {
            return '<div class="de-table-info">No table data available.</div>';
        }

        // Store state
        _tables[tableId] = {
            headers: headers,
            allRows: rows,
            filteredRows: rows,
            sortCol: -1,
            sortDir: 'none',
            page: 1
        };

        const totalRows = rows.length;
        const title = opts.title || ('Table (' + totalRows + ' rows)');
        const showRows = rows.slice(0, PAGE_SIZE);

        let html = '<div class="de-table-container" id="' + tableId + '">';

        // Toolbar
        html += '<div class="de-table-toolbar">';
        html += '<input type="text" class="de-table-filter" placeholder="Filter..." oninput="DETableRenderer.filter(\'' + tableId + '\', this.value)" />';
        html += '<span class="de-table-info">' + totalRows + ' rows</span>';
        html += '<button class="de-btn de-btn-sm de-btn-ghost" onclick="DETableRenderer.exportCSV(\'' + tableId + '\')" title="Export CSV"><i class="fas fa-download"></i> CSV</button>';
        if (opts.pinnable !== false) {
            html += '<button class="de-btn de-btn-sm de-btn-ghost" onclick="DETableRenderer.pinTable(\'' + tableId + '\')" title="Pin to Dashboard"><i class="fas fa-thumbtack"></i> Pin</button>';
        }
        html += '</div>';

        // Table
        html += '<div style="overflow-x:auto;">';
        html += '<table class="de-table">';

        // Header
        html += '<thead><tr>';
        for (var i = 0; i < headers.length; i++) {
            html += '<th onclick="DETableRenderer.sort(\'' + tableId + '\', ' + i + ')">' + _esc(headers[i]) + '</th>';
        }
        html += '</tr></thead>';

        // Body
        html += '<tbody id="' + tableId + '-body">';
        html += _renderRows(showRows, headers.length);
        html += '</tbody>';
        html += '</table></div>';

        // Footer
        if (totalRows > PAGE_SIZE) {
            html += '<div class="de-table-footer">';
            html += '<span class="de-table-info" id="' + tableId + '-page-info">Showing 1-' + Math.min(PAGE_SIZE, totalRows) + ' of ' + totalRows + '</span>';
            html += '<button class="de-load-more-btn" id="' + tableId + '-more" onclick="DETableRenderer.loadMore(\'' + tableId + '\')">Load More</button>';
            html += '</div>';
        }

        html += '</div>';
        return html;
    }

    /**
     * Get table data for dashboard serialization
     */
    function getTableData(tableId) {
        var t = _tables[tableId];
        if (!t) return null;
        return { headers: t.headers, rows: t.allRows };
    }


    /* ── Filter ────────────────────────────────────────────── */

    function filter(tableId, value) {
        var t = _tables[tableId];
        if (!t) return;

        var term = (value || '').toLowerCase();
        if (!term) {
            t.filteredRows = t.allRows;
        } else {
            t.filteredRows = t.allRows.filter(function (row) {
                return row.some(function (cell) {
                    return String(cell).toLowerCase().indexOf(term) !== -1;
                });
            });
        }
        t.page = 1;
        _rerender(tableId);
    }

    /* ── Sort ──────────────────────────────────────────────── */

    function sort(tableId, colIdx) {
        var t = _tables[tableId];
        if (!t) return;

        if (t.sortCol === colIdx) {
            t.sortDir = t.sortDir === 'asc' ? 'desc' : 'asc';
        } else {
            t.sortCol = colIdx;
            t.sortDir = 'asc';
        }

        t.filteredRows.sort(function (a, b) {
            var va = a[colIdx];
            var vb = b[colIdx];
            // Numeric comparison
            var na = Number(va), nb = Number(vb);
            if (!isNaN(na) && !isNaN(nb)) {
                return t.sortDir === 'asc' ? na - nb : nb - na;
            }
            // String comparison
            va = String(va || '').toLowerCase();
            vb = String(vb || '').toLowerCase();
            if (va < vb) return t.sortDir === 'asc' ? -1 : 1;
            if (va > vb) return t.sortDir === 'asc' ? 1 : -1;
            return 0;
        });

        t.page = 1;
        _rerender(tableId);

        // Update header sort indicators
        var container = document.getElementById(tableId);
        if (container) {
            var ths = container.querySelectorAll('th');
            ths.forEach(function (th, i) {
                th.classList.remove('sort-asc', 'sort-desc');
                if (i === colIdx) {
                    th.classList.add(t.sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
                }
            });
        }
    }

    /* ── Pagination ────────────────────────────────────────── */

    function loadMore(tableId) {
        var t = _tables[tableId];
        if (!t) return;
        t.page++;
        _rerender(tableId);
    }

    /* ── Export CSV ─────────────────────────────────────────── */

    function exportCSV(tableId) {
        var t = _tables[tableId];
        if (!t) return;

        var csv = t.headers.map(_csvCell).join(',') + '\n';
        t.filteredRows.forEach(function (row) {
            csv += row.map(_csvCell).join(',') + '\n';
        });

        var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        var link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = tableId + '.csv';
        link.click();
        URL.revokeObjectURL(link.href);
    }

    /* ── Pin to Dashboard ──────────────────────────────────── */

    function pinTable(tableId) {
        var t = _tables[tableId];
        if (!t) return;
        if (window.DEDashboard) {
            window.DEDashboard.addWidget('table', {
                title: 'Table (' + t.allRows.length + ' rows)',
                tableId: tableId,
                data: { headers: t.headers, rows: t.allRows }
            });
        }
    }

    /* ── Internal helpers ──────────────────────────────────── */

    function _rerender(tableId) {
        var t = _tables[tableId];
        if (!t) return;

        var showRows = t.filteredRows.slice(0, t.page * PAGE_SIZE);
        var tbody = document.getElementById(tableId + '-body');
        if (tbody) {
            tbody.innerHTML = _renderRows(showRows, t.headers.length);
        }

        // Update info
        var infoEl = document.getElementById(tableId + '-page-info');
        if (infoEl) {
            infoEl.textContent = 'Showing 1-' + showRows.length + ' of ' + t.filteredRows.length;
        }

        // Hide load more if all shown
        var moreBtn = document.getElementById(tableId + '-more');
        if (moreBtn) {
            moreBtn.style.display = showRows.length >= t.filteredRows.length ? 'none' : '';
        }

        // Row count info in toolbar
        var container = document.getElementById(tableId);
        if (container) {
            var info = container.querySelector('.de-table-info');
            if (info) info.textContent = t.filteredRows.length + ' rows';
        }
    }

    function _renderRows(rows, colCount) {
        var html = '';
        for (var r = 0; r < rows.length; r++) {
            html += '<tr>';
            for (var c = 0; c < colCount; c++) {
                var val = rows[r][c];
                html += '<td title="' + _esc(String(val != null ? val : '')) + '">' + _esc(String(val != null ? val : '')) + '</td>';
            }
            html += '</tr>';
        }
        return html;
    }

    function _esc(str) {
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function _csvCell(val) {
        var s = String(val != null ? val : '');
        if (s.indexOf(',') !== -1 || s.indexOf('"') !== -1 || s.indexOf('\n') !== -1) {
            return '"' + s.replace(/"/g, '""') + '"';
        }
        return s;
    }

    /* ── Expose ────────────────────────────────────────────── */

    window.DETableRenderer = {
        render: renderTable,
        filter: filter,
        sort: sort,
        loadMore: loadMore,
        exportCSV: exportCSV,
        pinTable: pinTable,
        getTableData: getTableData,
        _tables: _tables
    };
})();
