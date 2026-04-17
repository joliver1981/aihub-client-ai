/**
 * richContentRenderer.js
 * Frontend handler for rendering AI-analyzed rich content blocks
 */

class RichContentRenderer {
    constructor() {
        this.renderers = {
            'text': this.renderText.bind(this),
            'number': this.renderNumber.bind(this),
            'table': this.renderTable.bind(this),
            'chart': this.renderChart.bind(this),
            'code': this.renderCode.bind(this),
            'metrics': this.renderMetrics.bind(this),
            'json': this.renderJson.bind(this),
            'alert': this.renderAlert.bind(this),
            'success': this.renderSuccess.bind(this),
            'error': this.renderError.bind(this),
            'image': this.renderImage.bind(this),
            'html_table': this.renderHtmlTable.bind(this),
            'list': this.renderList.bind(this),
            'sql': this.renderSql.bind(this)
        };
        
        this.chartInstances = new Map();
        this.initializeLibraries();
    }
    
    initializeLibraries() {
        // Load required libraries dynamically if not already loaded
        this.loadChartJS();
        this.loadPrismJS();
    }
    
    loadChartJS() {
        if (typeof Chart === 'undefined') {
            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/chart.js';
            document.head.appendChild(script);
        }
    }
    
    loadPrismJS() {
        if (typeof Prism === 'undefined') {
            // Load Prism CSS
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = 'https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-tomorrow.min.css';
            document.head.appendChild(link);
            
            // Load Prism JS
            const script = document.createElement('script');
            script.src = 'https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js';
            script.onload = () => {
                // Load additional language components
                const languages = ['python', 'sql', 'javascript', 'json', 'bash'];
                languages.forEach(lang => {
                    const langScript = document.createElement('script');
                    langScript.src = `https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-${lang}.min.js`;
                    document.head.appendChild(langScript);
                });
            };
            document.head.appendChild(script);
        }
    }
    
    /**
     * Main render method - processes response and returns HTML
     */
    render(response) {
        try {
            // Handle different response formats
            if (typeof response === 'string') {
                // Simple text response
                return this.renderText({ content: response });
            }
            
            if (response.type === 'rich_content' && response.blocks) {
                // Rich content with multiple blocks
                return this.renderBlocks(response.blocks);
            }
            
            if (response.blocks) {
                // Direct blocks array
                return this.renderBlocks(response.blocks);
            }
            
            // Fallback to text rendering
            return this.renderText({ content: JSON.stringify(response) });
            
        } catch (error) {
            console.error('Error rendering rich content:', error);
            return this.renderError({ 
                content: 'Error rendering content. Please try again.' 
            });
        }
    }
    
    /**
     * Render multiple content blocks
     */
    renderBlocks(blocks) {
        const container = document.createElement('div');
        container.className = 'rich-content-container';
        
        blocks.forEach((block, index) => {
            const blockElement = document.createElement('div');
            blockElement.className = `rich-content-block block-${block.type}`;
            blockElement.dataset.blockIndex = index;
            
            // Add type indicator if specified
            if (block.metadata?.show_type_indicator) {
                const indicator = document.createElement('span');
                indicator.className = 'content-type-indicator';
                indicator.textContent = block.type.toUpperCase();
                blockElement.appendChild(indicator);
            }
            
            // Render the block content
            const renderer = this.renderers[block.type] || this.renderText.bind(this);
            const content = renderer(block);
            
            if (typeof content === 'string') {
                blockElement.innerHTML += content;
            } else {
                blockElement.appendChild(content);
            }
            
            container.appendChild(blockElement);
        });
        
        return container.outerHTML;
    }
    
    /**
     * Render text block
     */
    renderText(block) {
        const content = block.content || '';
        const style = block.metadata?.style || '';
        
        return `<div class="content-text ${style}">${this.escapeHtml(content).replace(/\n/g, '<br>')}</div>`;
    }

    /**
     * Render text block
     */
    renderNumber(block) {
        // Ensure content is a string
        if (typeof block.content !== 'string') {
            block = {
                ...block,
                content: String(block.content)
            };
        }

        const content = block.content || '';
        const style = block.metadata?.style || '';
        
        return `<div class="content-text ${style}">${this.escapeHtml(content).replace(/\n/g, '<br>')}</div>`;
    }
    
    /**
     * Render table block with interactive features
     */
    renderTable(block) {
        const tableId = `table-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        const metadata = block.metadata || {};

        // Get the default display mode from user preferences
        const defaultMode = typeof getDataframeDisplayMode === 'function' ? 
            getDataframeDisplayMode() : 'expanded';
        const isCompact = defaultMode === 'compact';
        
        // Handle different content structures
        let headers = [];
        let rows = [];
        
        if (block.content) {
            if (Array.isArray(block.content)) {
                // Original format: array of objects
                const data = block.content;
                if (data.length === 0) {
                    return '<div class="content-text">No data to display</div>';
                }
                headers = metadata.columns || Object.keys(data[0]);
                rows = data.map(row => headers.map(col => row[col]));
            } else if (block.content.headers && block.content.rows) {
                // New format: {headers: [...], rows: [[...], [...]]}
                headers = block.content.headers;
                rows = block.content.rows;
            } else {
                // Unknown format
                console.error('Unknown table format:', block.content);
                return '<div class="content-text">Unable to render table</div>';
            }
        } else {
            return '<div class="content-text">No data to display</div>';
        }
        
        if (headers.length === 0 || rows.length === 0) {
            return '<div class="content-text">No data to display</div>';
        }

        // Set button text and icon based on current mode
        const buttonIcon = isCompact ? 'fa-expand' : 'fa-compress';
        const buttonText = isCompact ? 'Expand' : 'Compact';
            
        let html = `
            <div class="content-table-wrapper ${isCompact ? 'compact-mode' : ''}">
                ${metadata.title ? `<h5 class="table-title">${metadata.title}</h5>` : ''}
                <div class="table-controls">
                    ${metadata.filterable ? `
                        <input type="text" class="table-filter" placeholder="Filter..." 
                               onkeyup="window.richContentRenderer.filterTable('${tableId}', this.value)">
                    ` : ''}
                    <div class="table-actions">
                        ${metadata.exportable ? `
                            <button class="btn btn-sm btn-outline-secondary" 
                                    onclick="window.richContentRenderer.exportTable('${tableId}')">
                                <i class="fas fa-download"></i> Export
                            </button>
                        ` : ''}
                        <button class="btn btn-sm btn-outline-secondary" 
                                onclick="window.richContentRenderer.toggleTableCompact('${tableId}')">
                            <i class="fas ${buttonIcon}"></i> ${buttonText}
                        </button>
                    </div>
                </div>
                <div class="content-table ${metadata.paginated ? 'paginated' : ''}" id="${tableId}">
                    <table class="table table-hover">
                        <thead>
                            <tr>
                                ${headers.map((header, idx) => `
                                    <th ${metadata.sortable ? `onclick="window.richContentRenderer.sortTable('${tableId}', ${idx})" style="cursor: pointer;"` : ''}>
                                        ${this.escapeHtml(String(header))}
                                        ${metadata.sortable ? '<i class="fas fa-sort"></i>' : ''}
                                    </th>
                                `).join('')}
                            </tr>
                        </thead>
                        <tbody>
        `;
        
        // Render rows (limit if paginated)
        const rowLimit = metadata.paginated ? 50 : rows.length;
        const displayRows = rows.slice(0, rowLimit);
        
        displayRows.forEach(row => {
            html += '<tr>';
            row.forEach(cell => {
                // Handle different cell types
                let cellContent = '';
                
                if (cell && typeof cell === 'object') {
                    if (cell.text && cell.url) {
                        // Cell is a link object
                        const url = this.processUrl(cell.url);
                        cellContent = `<a href="${this.escapeHtml(url)}" target="_blank" class="text-primary file-link">${this.escapeHtml(cell.text)}</a>`;
                    } else {
                        // Other object type - stringify it
                        cellContent = this.escapeHtml(JSON.stringify(cell));
                    }
                } else {
                    // Simple value
                    cellContent = this.escapeHtml(String(cell !== undefined && cell !== null ? cell : ''));
                }
                
                html += `<td>${cellContent}</td>`;
            });
            html += '</tr>';
        });
        
        html += `
                        </tbody>
                    </table>
                </div>
        `;
        
        if (metadata.paginated && rows.length > rowLimit) {
            html += `
                <div class="table-pagination">
                    <span>Showing 1-${rowLimit} of ${rows.length} rows</span>
                    <button class="btn btn-sm btn-primary" 
                            onclick="window.richContentRenderer.loadMoreRows('${tableId}')">
                        Load More
                    </button>
                </div>
            `;
        }
        
        if (metadata.total_rows && metadata.total_rows > rows.length) {
            html += `
                <div class="table-info">
                    <i class="fas fa-info-circle"></i> 
                    Total dataset contains ${metadata.total_rows} rows
                </div>
            `;
        }
        
        html += '</div>';
        
        // Store data for later use (convert to consistent format)
        if (!window.tableData) window.tableData = {};
        window.tableData[tableId] = {
            headers: headers,
            rows: rows,
            currentDisplayCount: rowLimit
        };
        
        return html;
    }

    renderTable_legacy(block) {
        const tableId = `table-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        const data = block.content || [];
        const metadata = block.metadata || {};
        
        if (data.length === 0) {
            return '<div class="content-text">No data to display</div>';
        }
        
        // Get columns from metadata or derive from data
        const columns = metadata.columns || (data[0] ? Object.keys(data[0]) : []);
        
        let html = `
            <div class="content-table-wrapper">
                ${metadata.title ? `<h5 class="table-title">${metadata.title}</h5>` : ''}
                <div class="table-controls">
                    ${metadata.filterable ? `
                        <input type="text" class="table-filter" placeholder="Filter..." 
                               onkeyup="window.richContentRenderer.filterTable('${tableId}', this.value)">
                    ` : ''}
                    <div class="table-actions">
                        ${metadata.exportable ? `
                            <button class="btn btn-sm btn-outline-secondary" 
                                    onclick="window.richContentRenderer.exportTable('${tableId}')">
                                <i class="fas fa-download"></i> Export
                            </button>
                        ` : ''}
                        <button class="btn btn-sm btn-outline-secondary" 
                                onclick="window.richContentRenderer.toggleTableCompact('${tableId}')">
                            <i class="fas fa-compress"></i> Compact
                        </button>
                    </div>
                </div>
                <div class="content-table ${metadata.paginated ? 'paginated' : ''}" id="${tableId}">
                    <table class="table table-hover">
                        <thead>
                            <tr>
                                ${columns.map((col, idx) => `
                                    <th ${metadata.sortable ? `onclick="window.richContentRenderer.sortTable('${tableId}', ${idx})" style="cursor: pointer;"` : ''}>
                                        ${col}
                                        ${metadata.sortable ? '<i class="fas fa-sort"></i>' : ''}
                                    </th>
                                `).join('')}
                            </tr>
                        </thead>
                        <tbody>
        `;
        
        // Render rows (limit if paginated)
        const rowLimit = metadata.paginated ? 50 : data.length;
        const displayData = data.slice(0, rowLimit);
        
        displayData.forEach(row => {
            html += '<tr>';
            columns.forEach(col => {
                const value = row[col] !== undefined ? row[col] : '';
                html += `<td>${this.escapeHtml(String(value))}</td>`;
            });
            html += '</tr>';
        });
        
        html += `
                        </tbody>
                    </table>
                </div>
        `;
        
        if (metadata.paginated && data.length > rowLimit) {
            html += `
                <div class="table-pagination">
                    <span>Showing 1-${rowLimit} of ${data.length} rows</span>
                    <button class="btn btn-sm btn-primary" 
                            onclick="window.richContentRenderer.loadMoreRows('${tableId}')">
                        Load More
                    </button>
                </div>
            `;
        }
        
        if (metadata.total_rows && metadata.total_rows > data.length) {
            html += `
                <div class="table-info">
                    <i class="fas fa-info-circle"></i> 
                    Total dataset contains ${metadata.total_rows} rows
                </div>
            `;
        }
        
        html += '</div>';
        
        // Store data for later use
        if (!window.tableData) window.tableData = {};
        window.tableData[tableId] = data;
        
        return html;
    }

    /**
     * Process URL to handle UNC paths and other special cases
     */
    processUrl(url) {
        if (!url) return '';
        
        // Handle UNC paths (\\server\share\path)
        if (url.startsWith('\\\\')) {
            // Convert UNC path to document serve URL
            return '/document/serve?path=' + encodeURIComponent(url);
        }
        
        // Already a proper URL or document serve path
        return url;
    }
    
    /**
     * Render chart block
     */
    renderChart(block) {
        // Check if this is an image-based chart (base64 or file path) or Chart.js data
        if (typeof block.content === 'string') {
            // Check if it's a base64 image or an image tag
            if (block.content.includes('data:image') || block.content.includes('<img')) {
                // This is an image-based chart (e.g., from matplotlib)
                return this.renderChartImage(block);
            } else if (block.content.endsWith('.png') || block.content.endsWith('.jpg') || 
                       block.content.endsWith('.jpeg') || block.content.endsWith('.svg')) {
                // This is a file path to a chart image
                return this.renderChartFromPath(block);
            }
        }
        
        // Otherwise, it's Chart.js data format
        const chartId = `chart-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        const chartType = block.metadata?.chart_type || 'bar';
        const title = block.metadata?.title || '';
        
        const html = `
            <div class="content-chart">
                ${title ? `<h5 class="chart-title">${title}</h5>` : ''}
                <div class="chart-container">
                    <canvas id="${chartId}"></canvas>
                </div>
                ${block.metadata?.downloadable ? `
                    <div class="chart-actions">
                        <button class="btn btn-sm btn-outline-secondary" 
                                onclick="window.richContentRenderer.downloadChart('${chartId}')">
                            <i class="fas fa-download"></i> Download
                        </button>
                    </div>
                ` : ''}
            </div>
        `;
        
        // Schedule chart creation after DOM update
        setTimeout(() => {
            this.createChart(chartId, chartType, block.content, block.metadata);
        }, 100);
        
        return html;
    }
    
    /**
     * Create Chart.js chart
     */
    createChart(canvasId, type, data, metadata) {
        const canvas = document.getElementById(canvasId);
        if (!canvas || typeof Chart === 'undefined') {
            setTimeout(() => this.createChart(canvasId, type, data, metadata), 500);
            return;
        }
        
        const ctx = canvas.getContext('2d');
        
        // Destroy existing chart if any
        if (this.chartInstances.has(canvasId)) {
            this.chartInstances.get(canvasId).destroy();
        }
        
        const chartConfig = {
            type: type,
            data: data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: metadata?.show_legend !== false
                    },
                    tooltip: {
                        enabled: metadata?.interactive !== false
                    }
                }
            }
        };
        
        // Add custom options from metadata
        if (metadata?.chart_options) {
            Object.assign(chartConfig.options, metadata.chart_options);
        }
        
        const chart = new Chart(ctx, chartConfig);
        this.chartInstances.set(canvasId, chart);
    }

    /**
     * Render chart as image (for matplotlib/base64 charts)
     */
    renderChartImage(block) {
        const title = block.metadata?.title || '';
        let imgHtml = '';
        
        // Check if content is already an img tag
        if (block.content.includes('<img')) {
            imgHtml = block.content;
        } else if (block.content.includes('data:image')) {
            // It's a base64 string
            imgHtml = `<img src="${block.content}" alt="Chart" style="max-width: 100%; height: auto;">`;
        }
        
        return `
            <div class="content-chart content-chart-image">
                ${title ? `<h5 class="chart-title">${title}</h5>` : ''}
                <div class="chart-container chart-image-container">
                    ${imgHtml}
                </div>
                ${block.metadata?.downloadable !== false ? `
                    <div class="chart-actions">
                        <button class="btn btn-sm btn-outline-secondary" 
                                onclick="window.richContentRenderer.downloadChartImage(this)">
                            <i class="fas fa-download"></i> Download
                        </button>
                    </div>
                ` : ''}
            </div>
        `;
    }
    
    /**
     * Render chart from file path (for temp chart files)
     */
    renderChartFromPath(block) {
        const title = block.metadata?.title || '';
        const path = block.content;
        
        // Convert file path to proper URL
        let imgUrl = path;
        if (path.startsWith('\\\\') || path.includes('\\')) {
            // Convert Windows path to web-accessible URL
            imgUrl = '/document/serve?path=' + encodeURIComponent(path);
        } else if (!path.startsWith('http') && !path.startsWith('/')) {
            // Relative path - might need adjustment based on your setup
            imgUrl = '/static/charts/' + path.replace(/^.*[\\\/]/, '');
        }
        
        return `
            <div class="content-chart content-chart-image">
                ${title ? `<h5 class="chart-title">${title}</h5>` : ''}
                <div class="chart-container chart-image-container">
                    <img src="${imgUrl}" alt="Chart" style="max-width: 100%; height: auto;"
                         onerror="this.onerror=null; this.src='data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNDAwIiBoZWlnaHQ9IjMwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMTAwJSIgaGVpZ2h0PSIxMDAlIiBmaWxsPSIjZjNmNGY2Ii8+PHRleHQgeD0iNTAlIiB5PSI1MCUiIGZvbnQtZmFtaWx5PSJBcmlhbCIgZm9udC1zaXplPSIxNiIgZmlsbD0iIzZiNzI4MCIgdGV4dC1hbmNob3I9Im1pZGRsZSI+Q2hhcnQgbm90IGF2YWlsYWJsZTwvdGV4dD48L3N2Zz4=';">
                </div>
                ${block.metadata?.downloadable !== false ? `
                    <div class="chart-actions">
                        <button class="btn btn-sm btn-outline-secondary" 
                                onclick="window.richContentRenderer.downloadChartImage(this)">
                            <i class="fas fa-download"></i> Download
                        </button>
                    </div>
                ` : ''}
            </div>
        `;
    }

    /**
     * Download chart image (for matplotlib/base64 charts)
     */
    downloadChartImage(button) {
        const container = button.closest('.content-chart-image');
        const img = container.querySelector('img');
        
        if (!img) return;
        
        const src = img.src;
        const a = document.createElement('a');
        
        if (src.includes('data:image')) {
            // Base64 image - can download directly
            a.href = src;
            a.download = `chart-${Date.now()}.png`;
            a.click();
        } else {
            // External URL - might need to fetch and convert
            // For now, just open in new tab
            window.open(src, '_blank');
        }
    }
    
    /**
     * Render code block with syntax highlighting
     */
    renderCode(block) {
        const language = block.metadata?.language || 'plaintext';
        const copyable = block.metadata?.copyable !== false;
        const lineNumbers = block.metadata?.line_numbers || false;
        const collapsible = block.metadata?.collapsible || false;
        const codeId = `code-${Date.now()}`;
        
        const html = `
            <div class="content-code-wrapper ${collapsible ? 'collapsible' : ''}">
                <div class="code-header">
                    <span class="code-language">${language.toUpperCase()}</span>
                    <div class="code-actions">
                        ${collapsible ? `
                            <button class="btn-code-toggle" onclick="window.richContentRenderer.toggleCode('${codeId}')">
                                <i class="fas fa-chevron-down"></i>
                            </button>
                        ` : ''}
                        ${copyable ? `
                            <button class="btn-code-copy" onclick="window.richContentRenderer.copyCode('${codeId}')">
                                <i class="fas fa-copy"></i> Copy
                            </button>
                        ` : ''}
                    </div>
                </div>
                <pre id="${codeId}" class="content-code ${lineNumbers ? 'line-numbers' : ''} language-${language}"><code class="language-${language}">${this.escapeHtml(block.content)}</code></pre>
            </div>
        `;
        
        // Schedule syntax highlighting after DOM update
        setTimeout(() => {
            if (typeof Prism !== 'undefined') {
                Prism.highlightElement(document.querySelector(`#${codeId} code`));
            }
        }, 100);
        
        return html;
    }
    
    /**
     * Render metrics/KPI cards
     */
    renderMetrics(block) {
        const metrics = block.content || [];
        const displayType = block.metadata?.display || 'cards';
        
        if (displayType === 'cards') {
            const html = `
                <div class="content-metrics">
                    ${metrics.map(metric => `
                        <div class="metric-card ${metric.trend ? `trend-${metric.trend}` : ''}">
                            <div class="metric-value">${this.escapeHtml(metric.value)}</div>
                            <div class="metric-label">${this.escapeHtml(metric.label)}</div>
                            ${metric.trend ? `
                                <div class="metric-trend">
                                    <i class="fas fa-arrow-${metric.trend}"></i>
                                </div>
                            ` : ''}
                        </div>
                    `).join('')}
                </div>
            `;
            return html;
        } else {
            // List display
            return `
                <div class="content-metrics-list">
                    ${metrics.map(metric => `
                        <div class="metric-item">
                            <span class="metric-label">${this.escapeHtml(metric.label)}:</span>
                            <span class="metric-value">${this.escapeHtml(metric.value)}</span>
                            ${metric.trend ? `<i class="fas fa-arrow-${metric.trend} trend-${metric.trend}"></i>` : ''}
                        </div>
                    `).join('')}
                </div>
            `;
        }
    }
    
    /**
     * Render JSON data with collapsible tree view
     */
    renderJson(block) {
        const jsonId = `json-${Date.now()}`;
        const data = block.content;
        
        const html = `
            <div class="content-json">
                <div class="json-header">
                    <span>JSON Data</span>
                    <button class="btn-json-toggle" onclick="window.richContentRenderer.toggleJson('${jsonId}')">
                        <i class="fas fa-chevron-down"></i> Toggle
                    </button>
                    <button class="btn-json-copy" onclick="window.richContentRenderer.copyJson('${jsonId}')">
                        <i class="fas fa-copy"></i> Copy
                    </button>
                </div>
                <pre id="${jsonId}" class="json-content"><code>${this.escapeHtml(JSON.stringify(data, null, 2))}</code></pre>
            </div>
        `;
        
        // Store JSON data for copy function
        if (!window.jsonData) window.jsonData = {};
        window.jsonData[jsonId] = data;
        
        return html;
    }
    
    /**
     * Render alert/warning message
     */
    renderAlert(block) {
        return `
            <div class="content-alert alert-warning">
                <i class="fas fa-exclamation-triangle"></i>
                <span>${this.escapeHtml(block.content)}</span>
            </div>
        `;
    }
    
    /**
     * Render success message
     */
    renderSuccess(block) {
        return `
            <div class="content-alert alert-success">
                <i class="fas fa-check-circle"></i>
                <span>${this.escapeHtml(block.content)}</span>
            </div>
        `;
    }
    
    /**
     * Render error message
     */
    renderError(block) {
        return `
            <div class="content-alert alert-error">
                <i class="fas fa-times-circle"></i>
                <span>${this.escapeHtml(block.content)}</span>
            </div>
        `;
    }
    
    /**
     * Render image with caption
     */
    renderImage(block) {
        const src = block.content;
        const caption = block.metadata?.caption || '';
        const zoomable = block.metadata?.zoomable !== false;
        
        return `
            <div class="content-image">
                <img src="${src}" 
                     alt="${caption}" 
                     ${zoomable ? 'onclick="window.richContentRenderer.zoomImage(this)"' : ''}
                     style="cursor: ${zoomable ? 'zoom-in' : 'default'};">
                ${caption ? `<div class="image-caption">${this.escapeHtml(caption)}</div>` : ''}
            </div>
        `;
    }
    
    /**
     * Render HTML table (already formatted)
     */
    renderHtmlTable(block) {
        return `<div class="content-html-table">${block.content}</div>`;
    }
    
    /**
     * Render list - handles both simple strings and link objects
     */
    renderList(block) {
        const items = Array.isArray(block.content) ? block.content : [block.content];
        const ordered = block.metadata?.ordered || false;
        
        const listTag = ordered ? 'ol' : 'ul';
        
        // Process each item - could be string, object with link, or object with multiple links
        const processedItems = items.map(item => {
            // Handle different item types
            if (typeof item === 'string') {
                // Simple string - check if it contains a URL pattern
                return this.processListItemText(item);
            } else if (item && typeof item === 'object') {
                // Check for object with multiple links array
                if (item.links && Array.isArray(item.links) && item.text) {
                    // Format: main text with multiple sub-links
                    const mainText = this.escapeHtml(item.text);
                    const linksHtml = item.links.map(link => {
                        return `<a href="${this.escapeHtml(link.url)}" target="_blank" class="text-primary file-link">${this.escapeHtml(link.text)}</a>`;
                    }).join(', ');
                    
                    return `
                        <div class="list-item-with-links">
                            <div class="item-main-text">${mainText}</div>
                            <div class="item-links">${linksHtml}</div>
                        </div>
                    `;
                }
                // Object - might be a link object with text and url
                else if (item.url && item.text) {
                    // Link object - ensure proper URL handling
                    let finalUrl = item.url;

                    if (item.url.startsWith('\\\\')) {
                        return `<a href="${'/document/serve?path=' + this.escapeHtml(finalUrl)}" target="_blank" class="text-primary file-link">${this.escapeHtml(item.text)}</a>`;
                    }
                    
                    // If it's already a /document/serve URL, use it as is
                    // Otherwise, check if we need to convert it
                    if (!item.url.startsWith('/document/serve') && 
                        !item.url.startsWith('http://') && 
                        !item.url.startsWith('https://')) {
                        // This might be a path that needs conversion
                        // But since backend should handle this, we'll trust the URL
                        console.warn('Unexpected URL format:', item.url);
                    }
                    
                    return `<a href="${this.escapeHtml(finalUrl)}" target="_blank" class="text-primary file-link">${this.escapeHtml(item.text)}</a>`;
                } else if (item.link && item.label) {
                    // Alternative link format
                    return `<a href="${this.escapeHtml(item.link)}" target="_blank" class="text-primary">${this.escapeHtml(item.label)}</a>`;
                } else if (item.href && item.name) {
                    // Another possible format
                    return `<a href="${this.escapeHtml(item.href)}" target="_blank" class="text-primary">${this.escapeHtml(item.name)}</a>`;
                } else if (item.text) {
                    // Object with just text property (no URL) - extract and render the text
                    return this.processListItemText(String(item.text));
                } else if (item.content) {
                    // Nested content
                    return this.processListItemText(String(item.content));
                } else {
                    // Try to stringify the object
                    try {
                        return this.escapeHtml(JSON.stringify(item));
                    } catch {
                        return this.escapeHtml(String(item));
                    }
                }
            } else {
                // Fallback - convert to string
                return this.escapeHtml(String(item));
            }
        });
        
        return `
            <${listTag} class="content-list">
                ${processedItems.map(item => `<li>${item}</li>`).join('')}
            </${listTag}>
        `;
    }

    renderList_legacy2(block) {
        const items = Array.isArray(block.content) ? block.content : [block.content];
        const ordered = block.metadata?.ordered || false;
        
        const listTag = ordered ? 'ol' : 'ul';
        
        // Process each item - could be string, object with link, or other
        const processedItems = items.map(item => {
            // Handle different item types
            if (typeof item === 'string') {
                // Simple string - check if it contains a URL pattern
                return this.processListItemText(item);
            } else if (item && typeof item === 'object') {
                // Object - might be a link object with text and url
                if (item.url && item.text) {
                    // Link object - ensure proper URL handling
                    let finalUrl = item.url;

                    if (item.url.startsWith('\\\\')) {
                        return `<a href="${'/document/serve?path=' + this.escapeHtml(finalUrl)}" target="_blank" class="text-primary file-link">${this.escapeHtml(item.text)}</a>`;
                    }
                    
                    // If it's already a /document/serve URL, use it as is
                    // Otherwise, check if we need to convert it
                    if (!item.url.startsWith('/document/serve') && 
                        !item.url.startsWith('http://') && 
                        !item.url.startsWith('https://')) {
                        // This might be a path that needs conversion
                        // But since backend should handle this, we'll trust the URL
                        console.warn('Unexpected URL format:', item.url);
                    }
                    
                    return `<a href="${this.escapeHtml(finalUrl)}" target="_blank" class="text-primary file-link">${this.escapeHtml(item.text)}</a>`;
                } else if (item.link && item.label) {
                    // Alternative link format
                    return `<a href="${this.escapeHtml(item.link)}" target="_blank" class="text-primary">${this.escapeHtml(item.label)}</a>`;
                } else if (item.href && item.name) {
                    // Another possible format
                    return `<a href="${this.escapeHtml(item.href)}" target="_blank" class="text-primary">${this.escapeHtml(item.name)}</a>`;
                } else if (item.text) {
                    // Object with just text property (no URL) - extract and render the text
                    return this.processListItemText(String(item.text));
                } else if (item.content) {
                    // Nested content
                    return this.processListItemText(String(item.content));
                } else {
                    // Try to stringify the object
                    try {
                        return this.escapeHtml(JSON.stringify(item));
                    } catch {
                        return this.escapeHtml(String(item));
                    }
                }
            } else {
                // Fallback - convert to string
                return this.escapeHtml(String(item));
            }
        });
        
        return `
            <${listTag} class="content-list">
                ${processedItems.map(item => `<li>${item}</li>`).join('')}
            </${listTag}>
        `;
    }
    
    /**
     * Process list item text to detect and convert URLs
     */
    processListItemText(text) {
        // Escape HTML first
        let processed = this.escapeHtml(text);
        
        // Detect and convert URLs to clickable links
        const urlRegex = /(https?:\/\/[^\s<>"]+)/gi;
        processed = processed.replace(urlRegex, (url) => {
            return `<a href="${url}" target="_blank" class="text-primary">${url}</a>`;
        });
        
        // Detect file paths (UNC or document serve paths)
        const uncPathRegex = /(\\\\[^\s<>"]+)/gi;
        processed = processed.replace(uncPathRegex, (path) => {
            const encodedPath = encodeURIComponent(path);
            return `<a href="/document/serve?path=${encodedPath}" target="_blank" class="text-primary file-link">${path}</a>`;
        });
        
        return processed;
    }
    
    /**
     * Render SQL query with syntax highlighting
     */
    renderSql(block) {
        // Use the code renderer with SQL language
        return this.renderCode({
            content: block.content,
            metadata: { ...block.metadata, language: 'sql' }
        });
    }
    
    // Utility methods
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    filterTable(tableId, filterValue) {
        const table = document.getElementById(tableId);
        const rows = table.getElementsByTagName('tbody')[0].getElementsByTagName('tr');
        
        for (let row of rows) {
            const text = row.textContent.toLowerCase();
            row.style.display = text.includes(filterValue.toLowerCase()) ? '' : 'none';
        }
    }
    
    sortTable(tableId, columnIndex) {
        const table = document.getElementById(tableId);
        const tbody = table.getElementsByTagName('tbody')[0];
        const rows = Array.from(tbody.getElementsByTagName('tr'));
        
        // Determine sort direction
        const th = table.getElementsByTagName('th')[columnIndex];
        const isAscending = th.dataset.sortOrder !== 'asc';
        th.dataset.sortOrder = isAscending ? 'asc' : 'desc';
        
        // Update sort icons
        table.querySelectorAll('th i').forEach(icon => {
            icon.className = 'fas fa-sort';
        });
        th.querySelector('i').className = `fas fa-sort-${isAscending ? 'up' : 'down'}`;
        
        // Sort rows
        rows.sort((a, b) => {
            const aValue = a.cells[columnIndex].textContent;
            const bValue = b.cells[columnIndex].textContent;
            
            // Try numeric comparison first
            const aNum = parseFloat(aValue);
            const bNum = parseFloat(bValue);
            
            if (!isNaN(aNum) && !isNaN(bNum)) {
                return isAscending ? aNum - bNum : bNum - aNum;
            }
            
            // Fall back to string comparison
            return isAscending ? 
                aValue.localeCompare(bValue) : 
                bValue.localeCompare(aValue);
        });
        
        // Reorder rows in DOM
        rows.forEach(row => tbody.appendChild(row));
    }
    
    exportTable_legacy(tableId) {
        const data = window.tableData[tableId];
        if (!data) return;
        
        // Convert to CSV
        const csv = this.convertToCSV(data);
        
        // Download
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `table-export-${Date.now()}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    }

    /**
     * Export table data to CSV
     */
    exportTable(tableId) {
        const tableInfo = window.tableData[tableId];
        if (!tableInfo) return;
        
        let csv = '';
        
        // Add headers
        csv += tableInfo.headers.map(h => `"${String(h).replace(/"/g, '""')}"`).join(',') + '\n';
        
        // Add rows
        tableInfo.rows.forEach(row => {
            csv += row.map(cell => {
                let value = '';
                if (cell && typeof cell === 'object' && cell.text) {
                    value = cell.text;
                } else {
                    value = String(cell !== undefined && cell !== null ? cell : '');
                }
                return `"${value.replace(/"/g, '""')}"`;
            }).join(',') + '\n';
        });
        
        // Download CSV
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `table_export_${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
    }
    
    convertToCSV(data) {
        if (!data.length) return '';
        
        const headers = Object.keys(data[0]);
        const csvHeaders = headers.join(',');
        
        const csvRows = data.map(row => {
            return headers.map(header => {
                const value = row[header];
                // Escape quotes and wrap in quotes if contains comma
                const escaped = String(value).replace(/"/g, '""');
                return escaped.includes(',') ? `"${escaped}"` : escaped;
            }).join(',');
        });
        
        return [csvHeaders, ...csvRows].join('\n');
    }
    
    toggleTableCompact(tableId) {
        const wrapper = document.getElementById(tableId).closest('.content-table-wrapper');
        wrapper.classList.toggle('compact-mode');
        
        const button = wrapper.querySelector('.fa-compress').parentElement;
        const icon = button.querySelector('i');
        
        if (wrapper.classList.contains('compact-mode')) {
            icon.className = 'fas fa-expand';
            button.innerHTML = '<i class="fas fa-expand"></i> Expand';
        } else {
            icon.className = 'fas fa-compress';
            button.innerHTML = '<i class="fas fa-compress"></i> Compact';
        }
    }
    
    copyCode(codeId) {
        const code = document.getElementById(codeId).textContent;
        navigator.clipboard.writeText(code).then(() => {
            // Show feedback
            const button = document.querySelector(`#${codeId}`).parentElement.querySelector('.btn-code-copy');
            const originalText = button.innerHTML;
            button.innerHTML = '<i class="fas fa-check"></i> Copied!';
            setTimeout(() => {
                button.innerHTML = originalText;
            }, 2000);
        });
    }
    
    copyJson(jsonId) {
        const data = window.jsonData[jsonId];
        const jsonString = JSON.stringify(data, null, 2);
        navigator.clipboard.writeText(jsonString).then(() => {
            // Show feedback
            const button = document.querySelector(`#${jsonId}`).parentElement.querySelector('.btn-json-copy');
            const originalText = button.innerHTML;
            button.innerHTML = '<i class="fas fa-check"></i> Copied!';
            setTimeout(() => {
                button.innerHTML = originalText;
            }, 2000);
        });
    }
    
    toggleCode(codeId) {
        const pre = document.getElementById(codeId);
        pre.classList.toggle('collapsed');
        
        const button = pre.parentElement.querySelector('.btn-code-toggle i');
        button.className = pre.classList.contains('collapsed') ? 
            'fas fa-chevron-right' : 'fas fa-chevron-down';
    }
    
    toggleJson(jsonId) {
        const pre = document.getElementById(jsonId);
        pre.classList.toggle('collapsed');
        
        const button = pre.parentElement.querySelector('.btn-json-toggle i');
        button.className = pre.classList.contains('collapsed') ? 
            'fas fa-chevron-right' : 'fas fa-chevron-down';
    }
    
    zoomImage(img) {
        // Create modal for image zoom
        const modal = document.createElement('div');
        modal.className = 'image-zoom-modal';
        modal.innerHTML = `
            <div class="image-zoom-content">
                <span class="image-zoom-close" onclick="this.parentElement.parentElement.remove()">
                    <i class="fas fa-times"></i>
                </span>
                <img src="${img.src}" alt="${img.alt}">
            </div>
        `;
        document.body.appendChild(modal);
        
        // Close on click outside
        modal.addEventListener('click', function(e) {
            if (e.target === modal) {
                modal.remove();
            }
        });
    }
    
    downloadChart(chartId) {
        const chart = this.chartInstances.get(chartId);
        if (!chart) return;
        
        const url = chart.toBase64Image();
        const a = document.createElement('a');
        a.href = url;
        a.download = `chart-${Date.now()}.png`;
        a.click();
    }
    
    /**
     * Load more rows for paginated tables
     */
    loadMoreRows(tableId) {
        const tableElement = document.getElementById(tableId);
        const tbody = tableElement.querySelector('tbody');
        const tableInfo = window.tableData[tableId];
        
        if (!tableInfo) return;
        
        const currentCount = tableInfo.currentDisplayCount || 50;
        const newCount = Math.min(currentCount + 50, tableInfo.rows.length);
        
        // Clear current rows
        tbody.innerHTML = '';
        
        // Render rows up to new count
        const displayRows = tableInfo.rows.slice(0, newCount);
        displayRows.forEach(row => {
            const tr = document.createElement('tr');
            row.forEach(cell => {
                const td = document.createElement('td');
                
                if (cell && typeof cell === 'object' && cell.text && cell.url) {
                    const url = this.processUrl(cell.url);
                    td.innerHTML = `<a href="${this.escapeHtml(url)}" target="_blank" class="text-primary file-link">${this.escapeHtml(cell.text)}</a>`;
                } else {
                    td.textContent = String(cell !== undefined && cell !== null ? cell : '');
                }
                
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        
        // Update stored count
        tableInfo.currentDisplayCount = newCount;
        
        // Update pagination info
        const paginationElement = tableElement.parentElement.querySelector('.table-pagination span');
        if (paginationElement) {
            paginationElement.textContent = `Showing 1-${newCount} of ${tableInfo.rows.length} rows`;
        }
        
        // Hide load more button if all rows are displayed
        if (newCount >= tableInfo.rows.length) {
            const loadMoreBtn = tableElement.parentElement.querySelector('.table-pagination button');
            if (loadMoreBtn) {
                loadMoreBtn.style.display = 'none';
            }
        }
    }

}

// Initialize global instance
window.richContentRenderer = new RichContentRenderer();
