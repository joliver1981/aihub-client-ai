/**
 * Page context provider for /mcp_servers (templates/mcp_servers.html).
 * Surfaces gateway status, configured servers, filters, and stats so the
 * Universal Assistant can answer questions like
 *   "what servers are connected?"  /  "why is the gateway down?"
 */
window.assistantPageContext = {
    page: 'mcp_servers',
    pageName: 'MCP Server Management',

    getPageData: function () {
        const data = {
            gateway: {
                statusText: '',
                isHealthy: null
            },
            stats: {
                totalServers: 0,
                lastTestOk: 0,
                totalTools: 0,
                agentsUsing: 0
            },
            filters: {
                type: '',
                search: ''
            },
            servers: [],
            visibleServerCount: 0,
            emptyState: false,
            modal: {
                addEditOpen: false,
                addEditTitle: '',
                directoryOpen: false
            },
            availableActions: []
        };

        const gatewayEl = document.getElementById('gatewayStatus');
        if (gatewayEl) {
            data.gateway.statusText = (gatewayEl.innerText || gatewayEl.textContent || '').trim().replace(/\s+/g, ' ');
            if (gatewayEl.classList.contains('alert-success')) data.gateway.isHealthy = true;
            else if (gatewayEl.classList.contains('alert-danger') || gatewayEl.classList.contains('alert-warning')) data.gateway.isHealthy = false;
        }

        const num = function (id) {
            const el = document.getElementById(id);
            if (!el) return 0;
            const n = parseInt((el.textContent || '0').replace(/[^0-9-]/g, ''), 10);
            return isNaN(n) ? 0 : n;
        };
        data.stats.totalServers = num('totalServers');
        data.stats.lastTestOk = num('activeServers');
        data.stats.totalTools = num('totalTools');
        data.stats.agentsUsing = num('agentCount');

        const filterType = document.getElementById('filterType');
        if (filterType) data.filters.type = filterType.value || '';
        const searchInput = document.getElementById('searchServers');
        if (searchInput) data.filters.search = (searchInput.value || '').trim();

        const rows = document.querySelectorAll('#serversTableBody tr');
        rows.forEach(function (row) {
            if (row.offsetParent === null) return;
            const cells = row.querySelectorAll('td');
            if (cells.length < 2) return;
            const server = {
                type: (cells[0] && cells[0].textContent || '').trim().split('\n')[0],
                name: (cells[1] && cells[1].textContent || '').trim().split('\n')[0],
                endpoint: cells[2] ? (cells[2].textContent || '').trim() : '',
                category: cells[3] ? (cells[3].textContent || '').trim() : '',
                status: cells[4] ? (cells[4].textContent || '').trim() : '',
                toolCount: cells[5] ? (cells[5].textContent || '').trim() : '',
                agentCount: cells[6] ? (cells[6].textContent || '').trim() : ''
            };
            data.servers.push(server);
        });
        data.visibleServerCount = data.servers.length;

        const noServersMsg = document.getElementById('noServersMessage');
        if (noServersMsg && noServersMsg.offsetParent !== null) {
            data.emptyState = true;
        }

        const serverModal = document.getElementById('serverModal');
        if (serverModal && serverModal.classList.contains('show')) {
            data.modal.addEditOpen = true;
            const title = document.getElementById('serverModalTitle');
            if (title) data.modal.addEditTitle = (title.textContent || '').trim();
        }

        if (data.emptyState) {
            data.availableActions.push('Click "Add Server" to configure your first MCP server');
            data.availableActions.push('Click "Server Directory" to browse pre-configured options');
        } else {
            data.availableActions.push('Add Server — register a new MCP endpoint');
            data.availableActions.push('Test All — verify every server is reachable');
            data.availableActions.push('Edit or delete a server from its row actions');
            if (data.gateway.isHealthy === false) {
                data.availableActions.push('Gateway is unhealthy — server connections will fail until it recovers');
            }
        }

        return data;
    }
};

console.log('MCP Servers assistant context loaded');
