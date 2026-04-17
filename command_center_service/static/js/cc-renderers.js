/**
 * Command Center — Rich Content Renderers
 * Renders content blocks: charts, tables, maps, KPIs, artifacts, images.
 */

const CCRenderers = {
    _chartInstances: {},
    _mapInstances: {},
    _chartCounter: 0,
    _mapCounter: 0,

    /**
     * Render an array of content blocks into a container element.
     */
    renderBlocks(blocks, container) {
        if (!blocks || !Array.isArray(blocks)) return;

        blocks.forEach(block => {
            const el = this.renderBlock(block);
            if (el) container.appendChild(el);
        });
    },

    /**
     * Render a single content block. Returns an HTML element.
     */
    renderBlock(block) {
        const type = block.type || 'text';
        switch (type) {
            case 'text':     return this._renderText(block);
            case 'chart':    return this._renderChart(block);
            case 'table':    return this._renderTable(block);
            case 'kpi':      return this._renderKPI(block);
            case 'map':      return this._renderMap(block);
            case 'artifact': return this._renderArtifact(block);
            case 'image':    return this._renderImage(block);
            case 'meta': {
                // Subtle metadata for conversation coherence — visible but unobtrusive
                const meta = document.createElement('div');
                meta.className = 'cc-block-meta';
                meta.style.cssText = 'font-size:0.75rem;color:#888;margin-top:4px;font-style:italic;';
                meta.textContent = block.content || '';
                return meta;
            }
            default:
                console.warn('Unknown block type:', type);
                // Render unknown blocks as fenced JSON
                const pre = document.createElement('pre');
                const code = document.createElement('code');
                code.className = 'language-json hljs';
                const pretty = JSON.stringify(block, null, 2);
                code.textContent = pretty;
                if (window.hljs) try { code.innerHTML = hljs.highlight(pretty, { language: 'json' }).value; } catch(e) {}
                pre.appendChild(code);
                const wrapper = document.createElement('div');
                wrapper.className = 'cc-block cc-block-text';
                wrapper.appendChild(pre);
                return wrapper;
        }
    },

    _renderText(block) {
        const div = document.createElement('div');
        div.className = 'cc-block cc-block-text';
        let content = block.content || '';

        // 1. Handle double-encoded JSON blocks
        if (content.trim().startsWith('[{') && content.trim().endsWith('}]')) {
            try {
                const inner = JSON.parse(content);
                if (Array.isArray(inner) && inner.length > 0 && inner[0].type) {
                    this.renderBlocks(inner, div);
                    return div;
                }
            } catch(e) {}
        }

        // 2. Detect HTML content and render safely
        if (content.trim().startsWith('<') && content.includes('</')) {
            if (window.DOMPurify) {
                div.innerHTML = DOMPurify.sanitize(content, {
                    ALLOWED_TAGS: ['h1','h2','h3','h4','h5','h6','p','br','b','strong','i','em','u',
                                   'a','ul','ol','li','table','thead','tbody','tr','th','td','code',
                                   'pre','blockquote','img','span','div','hr','sub','sup','mark'],
                    ALLOWED_ATTR: ['href','src','alt','class','style','target','width','height'],
                });
            } else {
                div.innerHTML = content;
            }
            return div;
        }

        // 3. If it looks like raw JSON, try smart formatting
        if (content.trim().startsWith('{') && content.trim().endsWith('}')) {
            try {
                const obj = JSON.parse(content);
                
                // 3a. Detect pandas DataFrame JSON (has "schema" + "data" fields)
                if (obj.schema && obj.data && Array.isArray(obj.data)) {
                    const table = this._renderDataFrameTable(obj);
                    if (table) { div.appendChild(table); return div; }
                }
                
                // 3b. Detect array-of-objects (common data format)
                if (Array.isArray(obj) && obj.length > 0 && typeof obj[0] === 'object') {
                    const table = this._renderObjectArrayTable(obj);
                    if (table) { div.appendChild(table); return div; }
                }

                // 3c. Fallback: fence as highlighted JSON
                const pretty = JSON.stringify(obj, null, 2);
                const pre = document.createElement('pre');
                const code = document.createElement('code');
                code.className = 'language-json hljs';
                code.textContent = pretty;
                if (window.hljs) {
                    try { code.innerHTML = hljs.highlight(pretty, { language: 'json' }).value; } catch(e) {}
                }
                pre.appendChild(code);
                div.appendChild(pre);
                return div;
            } catch(e) {}
        }
        
        // 3d. Also catch JSON embedded in text (e.g., "📊 Agent: {json}")
        const jsonMatch = content.match(/(\{"schema":\{.*\})\s*$/s) || content.match(/(\{"schema":\{[^]*\})\s*$/);
        if (jsonMatch) {
            try {
                const obj = JSON.parse(jsonMatch[1]);
                if (obj.schema && obj.data && Array.isArray(obj.data)) {
                    // Render any text before the JSON as markdown
                    const textBefore = content.substring(0, content.indexOf(jsonMatch[1])).trim();
                    if (textBefore && window.marked) {
                        const textDiv = document.createElement('div');
                        textDiv.innerHTML = marked.parse(textBefore);
                        div.appendChild(textDiv);
                    }
                    const table = this._renderDataFrameTable(obj);
                    if (table) { div.appendChild(table); return div; }
                }
            } catch(e) {}
        }

        // 4. Render as markdown (default)
        if (window.marked) {
            div.innerHTML = marked.parse(content);
            // Apply syntax highlighting to any code blocks
            div.querySelectorAll('pre code').forEach(el => {
                if (window.hljs) {
                    try { hljs.highlightElement(el); } catch(e) {}
                }
            });
        } else {
            div.textContent = content;
        }
        return div;
    },

    _renderChart(block) {
        const wrapper = document.createElement('div');
        wrapper.className = 'cc-block cc-block-chart';

        if (block.title) {
            const title = document.createElement('h4');
            title.textContent = block.title;
            title.style.marginBottom = '8px';
            title.style.fontSize = '14px';
            wrapper.appendChild(title);
        }

        const canvas = document.createElement('canvas');
        const chartId = `cc-chart-${++this._chartCounter}`;
        canvas.id = chartId;
        canvas.style.maxHeight = '300px';
        wrapper.appendChild(canvas);

        // Build Chart.js config from block data
        requestAnimationFrame(() => {
            try {
                const data = block.data || [];
                const xKey = block.xKey || 'label';
                const yKeys = block.yKeys || ['value'];
                const colors = block.colors || ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444'];
                const chartType = block.chartType === 'area' ? 'line' : block.chartType || 'bar';

                const labels = data.map(d => d[xKey]);
                const datasets = yKeys.map((key, i) => ({
                    label: key,
                    data: data.map(d => d[key]),
                    backgroundColor: chartType === 'pie' || chartType === 'doughnut'
                        ? colors.slice(0, data.length)
                        : colors[i % colors.length] + '80',
                    borderColor: colors[i % colors.length],
                    borderWidth: chartType === 'line' ? 2 : 1,
                    fill: block.chartType === 'area',
                    tension: 0.3,
                }));

                const chart = new Chart(canvas, {
                    type: chartType,
                    data: { labels, datasets },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { labels: { color: '#e4e6f0' } },
                        },
                        scales: chartType !== 'pie' && chartType !== 'doughnut' ? {
                            x: { ticks: { color: '#8b8fa3' }, grid: { color: '#2a2e3d' } },
                            y: { ticks: { color: '#8b8fa3' }, grid: { color: '#2a2e3d' } },
                        } : undefined,
                    },
                });

                this._chartInstances[chartId] = chart;
            } catch (e) {
                console.error('Chart render error:', e);
                wrapper.innerHTML += `<p style="color:#ef4444">Chart error: ${e.message}</p>`;
            }
        });

        return wrapper;
    },

    _renderTable(block) {
        const headers = block.headers || [];
        const rows = block.rows || [];

        // Use interactive table engine for consistency
        const columns = headers.map(h => ({
            name: h,
            label: h,
            type: rows.length > 0 && typeof rows[0]?.[headers.indexOf(h)] === 'number' ? 'number' : 'string',
            align: 'left',
        }));

        const wrapper = document.createElement('div');
        wrapper.className = 'cc-block cc-block-table';

        if (block.title) {
            const title = document.createElement('h4');
            title.textContent = block.title;
            title.style.padding = '8px 12px';
            title.style.fontSize = '14px';
            wrapper.appendChild(title);
        }

        const tableEl = this._buildInteractiveTable(columns, rows, block.title || 'table');
        if (tableEl) wrapper.appendChild(tableEl);
        return wrapper;
    },

    _renderKPI(block) {
        const wrapper = document.createElement('div');
        wrapper.className = 'cc-block cc-block-kpi';

        (block.cards || []).forEach(card => {
            const cardEl = document.createElement('div');
            cardEl.className = 'cc-kpi-card';
            cardEl.innerHTML = `
                <div class="label">${card.label || ''}</div>
                <div class="value">${card.value || ''}</div>
                ${card.trend ? `<div class="trend ${card.trendDirection || ''}">${card.trend}</div>` : ''}
            `;
            wrapper.appendChild(cardEl);
        });

        return wrapper;
    },

    // Cached GeoJSON data for choropleth
    _geoJsonCache: null,
    _geoJsonLoading: false,
    _geoJsonCallbacks: [],

    /**
     * Load US states GeoJSON (cached after first load).
     */
    _loadGeoJSON(callback) {
        if (this._geoJsonCache) { callback(this._geoJsonCache); return; }
        this._geoJsonCallbacks.push(callback);
        if (this._geoJsonLoading) return;
        this._geoJsonLoading = true;

        fetch('/static/data/us-states.geojson')
            .then(r => r.json())
            .then(data => {
                this._geoJsonCache = data;
                this._geoJsonCallbacks.forEach(cb => cb(data));
                this._geoJsonCallbacks = [];
            })
            .catch(err => {
                console.error('Failed to load GeoJSON:', err);
                this._geoJsonCallbacks.forEach(cb => cb(null));
                this._geoJsonCallbacks = [];
            })
            .finally(() => { this._geoJsonLoading = false; });
    },

    /**
     * Get a cyan-gradient color for a choropleth value.
     * @param {number} value - the data value
     * @param {number} min - minimum value in dataset
     * @param {number} max - maximum value in dataset
     * @returns {string} CSS color
     */
    _getChoroplethColor(value, min, max) {
        if (max === min) return 'rgba(6, 182, 212, 0.6)';
        const ratio = (value - min) / (max - min);
        // Gradient from dark (low) to bright cyan (high)
        const r = Math.round(6 + ratio * 20);
        const g = Math.round(60 + ratio * 160);
        const b = Math.round(80 + ratio * 170);
        return `rgb(${r}, ${g}, ${b})`;
    },

    _renderMap(block) {
        const wrapper = document.createElement('div');
        wrapper.className = 'cc-block cc-block-map';

        if (block.title) {
            const title = document.createElement('h4');
            title.textContent = block.title;
            title.style.padding = '8px';
            title.style.fontSize = '14px';
            wrapper.appendChild(title);
        }

        const mapDiv = document.createElement('div');
        const mapId = `cc-map-${++this._mapCounter}`;
        mapDiv.id = mapId;
        mapDiv.style.height = block.regions ? '360px' : '280px';
        wrapper.appendChild(mapDiv);

        // Legend container (for choropleth)
        const legendDiv = document.createElement('div');
        legendDiv.className = 'cc-map-legend';
        legendDiv.style.display = 'none';
        wrapper.appendChild(legendDiv);

        const hasRegions = block.regions && Array.isArray(block.regions) && block.regions.length > 0;

        requestAnimationFrame(() => {
            try {
                const center = block.center || (hasRegions ? [39.8, -98.5] : [0, 0]);
                const zoom = block.zoom || (hasRegions ? 4 : 10);

                const map = L.map(mapId).setView(center, zoom);
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    attribution: '&copy; OpenStreetMap contributors',
                }).addTo(map);

                // Render point markers
                (block.markers || []).forEach(m => {
                    const marker = L.marker([m.lat, m.lng]).addTo(map);
                    if (m.popup || m.label) {
                        marker.bindPopup(m.popup || m.label);
                    }
                });

                // Render choropleth regions
                if (hasRegions) {
                    this._loadGeoJSON((geoData) => {
                        if (!geoData) {
                            console.error('GeoJSON not available for choropleth');
                            return;
                        }

                        // Build lookup: state name → {value, label}
                        const regionMap = {};
                        block.regions.forEach(r => {
                            const name = (r.name || '').trim();
                            regionMap[name.toLowerCase()] = r;
                        });

                        // Calculate min/max for color scale
                        const values = block.regions.map(r => r.value || 0).filter(v => v > 0);
                        const minVal = Math.min(...values);
                        const maxVal = Math.max(...values);

                        // Add GeoJSON layer with styling
                        const geoLayer = L.geoJSON(geoData, {
                            style: (feature) => {
                                const stateName = feature.properties.name || '';
                                const region = regionMap[stateName.toLowerCase()];
                                if (region) {
                                    return {
                                        fillColor: this._getChoroplethColor(region.value || 0, minVal, maxVal),
                                        fillOpacity: 0.75,
                                        weight: 1.5,
                                        color: '#1a1a2e',
                                        opacity: 1,
                                    };
                                }
                                // States without data — subtle gray
                                return {
                                    fillColor: '#2a2a3e',
                                    fillOpacity: 0.3,
                                    weight: 0.5,
                                    color: '#3a3a4e',
                                    opacity: 0.5,
                                };
                            },
                            onEachFeature: (feature, layer) => {
                                const stateName = feature.properties.name || '';
                                const region = regionMap[stateName.toLowerCase()];
                                if (region) {
                                    const label = region.label || `${stateName}: ${region.value}`;
                                    layer.bindPopup(`<strong>${stateName}</strong><br>${label}`);

                                    layer.on('mouseover', (e) => {
                                        e.target.setStyle({ weight: 3, fillOpacity: 0.9 });
                                        e.target.bringToFront();
                                    });
                                    layer.on('mouseout', (e) => {
                                        geoLayer.resetStyle(e.target);
                                    });
                                }
                            },
                        }).addTo(map);

                        // Fit map to data regions
                        const dataBounds = [];
                        geoLayer.eachLayer(l => {
                            const name = l.feature?.properties?.name?.toLowerCase();
                            if (name && regionMap[name]) {
                                dataBounds.push(l.getBounds());
                            }
                        });
                        if (dataBounds.length > 0) {
                            let combined = dataBounds[0];
                            dataBounds.forEach(b => combined = combined.extend(b));
                            map.fitBounds(combined, { padding: [20, 20] });
                        }

                        // Build legend
                        if (values.length > 1) {
                            legendDiv.style.display = 'flex';
                            const steps = 5;
                            let legendHTML = '<span class="cc-legend-label">Low</span>';
                            for (let i = 0; i < steps; i++) {
                                const v = minVal + (maxVal - minVal) * (i / (steps - 1));
                                const color = this._getChoroplethColor(v, minVal, maxVal);
                                legendHTML += `<span class="cc-legend-swatch" style="background:${color}"></span>`;
                            }
                            legendHTML += '<span class="cc-legend-label">High</span>';
                            legendDiv.innerHTML = legendHTML;
                        }

                        setTimeout(() => map.invalidateSize(), 100);
                    });
                }

                this._mapInstances[mapId] = map;
                setTimeout(() => map.invalidateSize(), 100);
            } catch (e) {
                console.error('Map render error:', e);
                mapDiv.innerHTML = `<p style="color:#ef4444;padding:12px">Map error: ${e.message}</p>`;
            }
        });

        return wrapper;
    },

    _renderArtifact(block) {
        const wrapper = document.createElement('div');
        wrapper.className = 'cc-block cc-block-artifact';

        const iconMap = {
            excel: '📊', pdf: '📄', csv: '📋', json: '🔧', image: '🖼️', pptx: '📽️', text: '📝',
        };

        wrapper.innerHTML = `
            <div class="icon">${iconMap[block.artifactType] || '📎'}</div>
            <div class="info">
                <div class="name">${block.name || 'Download'}</div>
                <div class="size">${block.size || ''} ${block.description || ''}</div>
            </div>
            <button class="download-btn" onclick="window.open('${block.download_url || '#'}')">Download</button>
        `;

        return wrapper;
    },

    _renderImage(block) {
        const wrapper = document.createElement('div');
        wrapper.className = 'cc-block cc-block-image';

        const img = document.createElement('img');
        img.src = block.src || '';
        img.alt = block.alt || 'Image';
        img.style.maxWidth = '100%';
        img.style.borderRadius = '8px';
        img.style.display = 'block';
        wrapper.appendChild(img);

        // Add download button for generated/base64 images
        if (block.src) {
            const toolbar = document.createElement('div');
            toolbar.className = 'cc-image-toolbar';

            const dlBtn = document.createElement('button');
            dlBtn.className = 'cc-image-download-btn';
            dlBtn.innerHTML = `
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="7 10 12 15 17 10"/>
                    <line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
                Download
            `;
            dlBtn.onclick = () => {
                const a = document.createElement('a');
                a.href = block.src;
                a.download = (block.alt || 'image').substring(0, 50).replace(/[^a-zA-Z0-9 ]/g, '') + '.png';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
            };
            toolbar.appendChild(dlBtn);
            wrapper.appendChild(toolbar);
        }

        return wrapper;
    },

    /**
     * Render a pandas DataFrame JSON (schema + data format) as an HTML table.
     */
    /**
     * Interactive table engine — pagination, search, sort, CSV export.
     * All table renderers feed into this shared engine.
     */
    _tableCounter: 0,

    _buildInteractiveTable(columns, allRows, title) {
        // columns: [{name, label, type, align}]
        // allRows: [[val, val, ...], ...]
        const tableId = `cc-tbl-${++this._tableCounter}`;
        const PAGE_SIZE = 25;

        // State (closure-based per table instance)
        let filteredRows = [...allRows];
        let sortCol = -1;
        let sortAsc = true;
        let currentPage = 0;
        let searchTerm = '';

        const wrapper = document.createElement('div');
        wrapper.className = 'cc-data-table-wrapper';
        wrapper.id = tableId;

        // ── Toolbar: search + badge + export ──
        const toolbar = document.createElement('div');
        toolbar.className = 'cc-table-toolbar';

        const searchInput = document.createElement('input');
        searchInput.type = 'text';
        searchInput.placeholder = 'Filter rows…';
        searchInput.className = 'cc-table-search';

        const badge = document.createElement('span');
        badge.className = 'cc-data-table-badge';

        const exportBtn = document.createElement('button');
        exportBtn.className = 'cc-table-export-btn';
        exportBtn.innerHTML = '⬇ Export Data';
        exportBtn.title = 'Export all rows as CSV';

        toolbar.appendChild(searchInput);
        toolbar.appendChild(badge);
        toolbar.appendChild(exportBtn);
        wrapper.appendChild(toolbar);

        // ── Table element ──
        const table = document.createElement('table');
        table.className = 'cc-data-table';

        const thead = document.createElement('thead');
        const headerRow = document.createElement('tr');
        columns.forEach((col, ci) => {
            const th = document.createElement('th');
            th.innerHTML = `${col.label} <span class="cc-sort-arrow"></span>`;
            th.style.cursor = 'pointer';
            th.style.userSelect = 'none';
            if (col.align) th.style.textAlign = col.align;
            th.addEventListener('click', () => {
                if (sortCol === ci) { sortAsc = !sortAsc; }
                else { sortCol = ci; sortAsc = true; }
                applySort();
                currentPage = 0;
                render();
            });
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        table.appendChild(tbody);
        wrapper.appendChild(table);

        // ── Pagination controls ──
        const pagination = document.createElement('div');
        pagination.className = 'cc-table-pagination';
        wrapper.appendChild(pagination);

        // ── Event handlers ──
        searchInput.addEventListener('input', () => {
            searchTerm = searchInput.value.toLowerCase();
            applyFilter();
            currentPage = 0;
            render();
        });

        exportBtn.addEventListener('click', () => {
            const csvRows = [columns.map(c => c.label).join(',')];
            const exportData = filteredRows.length ? filteredRows : allRows;
            exportData.forEach(row => {
                csvRows.push(row.map(v => {
                    const s = v != null ? String(v) : '';
                    return s.includes(',') || s.includes('"') || s.includes('\n')
                        ? '"' + s.replace(/"/g, '""') + '"' : s;
                }).join(','));
            });
            const blob = new Blob([csvRows.join('\n')], { type: 'text/csv;charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = (title || 'data').replace(/[^a-z0-9]/gi, '_') + '.csv';
            a.style.display = 'none';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setTimeout(() => URL.revokeObjectURL(url), 100);
        });

        // ── Engine functions ──
        function applyFilter() {
            if (!searchTerm) { filteredRows = [...allRows]; return; }
            filteredRows = allRows.filter(row =>
                row.some(v => v != null && String(v).toLowerCase().includes(searchTerm))
            );
        }

        function applySort() {
            if (sortCol < 0) return;
            const col = columns[sortCol];
            const isNum = col.type === 'number' || col.type === 'integer';
            filteredRows.sort((a, b) => {
                let va = a[sortCol], vb = b[sortCol];
                if (va == null) va = '';
                if (vb == null) vb = '';
                if (isNum) {
                    const na = parseFloat(String(va).replace(/[$,%]/g, '')) || 0;
                    const nb = parseFloat(String(vb).replace(/[$,%]/g, '')) || 0;
                    return sortAsc ? na - nb : nb - na;
                }
                return sortAsc
                    ? String(va).localeCompare(String(vb))
                    : String(vb).localeCompare(String(va));
            });
        }

        function render() {
            const total = filteredRows.length;
            const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
            if (currentPage >= pages) currentPage = pages - 1;
            const start = currentPage * PAGE_SIZE;
            const pageRows = filteredRows.slice(start, start + PAGE_SIZE);

            // Badge
            badge.textContent = searchTerm
                ? `${total} of ${allRows.length} rows`
                : `${allRows.length} row${allRows.length !== 1 ? 's' : ''}`;

            // Header sort arrows
            headerRow.querySelectorAll('.cc-sort-arrow').forEach((arrow, i) => {
                arrow.textContent = i === sortCol ? (sortAsc ? ' ▲' : ' ▼') : '';
            });

            // Body
            tbody.innerHTML = '';
            pageRows.forEach(row => {
                const tr = document.createElement('tr');
                row.forEach((val, ci) => {
                    const td = document.createElement('td');
                    td.textContent = val != null ? String(val) : '—';
                    if (columns[ci].align) td.style.textAlign = columns[ci].align;
                    if (typeof val === 'string' && val.startsWith('$'))
                        td.style.fontVariantNumeric = 'tabular-nums';
                    tr.appendChild(td);
                });
                tbody.appendChild(tr);
            });

            // Pagination
            if (total <= PAGE_SIZE) {
                pagination.innerHTML = '';
                return;
            }
            const from = start + 1;
            const to = Math.min(start + PAGE_SIZE, total);
            let pHtml = `<span class="cc-page-info">Showing ${from}–${to} of ${total}</span>`;
            pHtml += `<span class="cc-page-btns">`;
            pHtml += `<button ${currentPage === 0 ? 'disabled' : ''} data-page="${currentPage - 1}">‹ Prev</button>`;
            // Show max 7 page buttons
            const maxBtns = 7;
            let pStart = Math.max(0, currentPage - 3);
            let pEnd = Math.min(pages, pStart + maxBtns);
            if (pEnd - pStart < maxBtns) pStart = Math.max(0, pEnd - maxBtns);
            for (let p = pStart; p < pEnd; p++) {
                pHtml += `<button class="${p === currentPage ? 'active' : ''}" data-page="${p}">${p + 1}</button>`;
            }
            pHtml += `<button ${currentPage >= pages - 1 ? 'disabled' : ''} data-page="${currentPage + 1}">Next ›</button>`;
            pHtml += `</span>`;
            pagination.innerHTML = pHtml;

            pagination.querySelectorAll('button[data-page]').forEach(btn => {
                btn.addEventListener('click', () => {
                    const p = parseInt(btn.dataset.page);
                    if (p >= 0 && p < pages) { currentPage = p; render(); }
                });
            });
        }

        // Initial render
        render();
        return wrapper;
    },

    _renderDataFrameTable(df) {
        if (!df.schema || !df.data || !Array.isArray(df.data)) return null;
        const fields = df.schema.fields || [];
        if (fields.length === 0) return null;

        const columns = fields.map(f => ({
            name: f.name,
            label: this._formatColumnName(f.name),
            type: f.type,
            align: (f.type === 'integer' || f.type === 'number') ? 'right' : 'left',
        }));

        const allRows = df.data.map(row => fields.map(f => row[f.name]));
        return this._buildInteractiveTable(columns, allRows, 'data');
    },

    _renderObjectArrayTable(arr) {
        if (!arr || arr.length === 0) return null;
        const keys = Object.keys(arr[0]);
        if (keys.length === 0) return null;

        const columns = keys.map(k => ({
            name: k,
            label: this._formatColumnName(k),
            type: typeof arr[0][k] === 'number' ? 'number' : 'string',
            align: typeof arr[0][k] === 'number' ? 'right' : 'left',
        }));

        const allRows = arr.map(row => keys.map(k => row[k]));
        return this._buildInteractiveTable(columns, allRows, 'data');
    },

    /**
     * Convert snake_case/camelCase column names to Title Case.
     */
    _formatColumnName(name) {
        return name
            .replace(/_/g, ' ')
            .replace(/([a-z])([A-Z])/g, '$1 $2')
            .replace(/\b\w/g, c => c.toUpperCase());
    },
};
