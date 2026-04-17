/**
 * Table Renderer
 * Creates styled HTML tables from structured block data.
 */

export class TableRenderer {
    /**
     * Create a table element from a block definition.
     * @param {object} block — table block data
     * @returns {HTMLElement} the table wrapper element
     */
    create(block) {
        const wrapper = document.createElement('div');
        wrapper.className = 'block-table';

        // Title bar
        if (block.title) {
            const titleBar = document.createElement('div');
            titleBar.className = 'block-table-title';
            titleBar.textContent = block.title;
            wrapper.appendChild(titleBar);
        }

        // Scroll container for wide tables
        const scrollWrap = document.createElement('div');
        scrollWrap.className = 'block-table-scroll';

        const table = document.createElement('table');

        // Header
        if (block.headers && block.headers.length > 0) {
            const thead = document.createElement('thead');
            const tr = document.createElement('tr');
            for (const header of block.headers) {
                const th = document.createElement('th');
                th.textContent = header;
                tr.appendChild(th);
            }
            thead.appendChild(tr);
            table.appendChild(thead);
        }

        // Body
        if (block.rows && block.rows.length > 0) {
            const tbody = document.createElement('tbody');
            for (const row of block.rows) {
                const tr = document.createElement('tr');
                for (const cell of row) {
                    const td = document.createElement('td');
                    td.textContent = String(cell ?? '');
                    tr.appendChild(td);
                }
                tbody.appendChild(tr);
            }
            table.appendChild(tbody);
        }

        scrollWrap.appendChild(table);
        wrapper.appendChild(scrollWrap);

        return wrapper;
    }
}
