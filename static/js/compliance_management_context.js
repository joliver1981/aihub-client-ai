/**
 * Page context provider for /compliance_management
 * (templates/compliance_management.html).
 *
 * The page is breadcrumb-driven (Retailers → Document Sets → Versions),
 * and the visible content is rendered dynamically into #mainContent.
 * We capture the breadcrumb trail, any in-flight jobs, and a summary of
 * whatever level is currently shown.
 */
window.assistantPageContext = {
    page: 'compliance_management',
    pageName: 'Retailer Compliance',

    getPageData: function () {
        const data = {
            breadcrumb: [],
            currentLevel: 'unknown',
            retailers: [],
            documentSets: [],
            versions: [],
            requirements: { rowCount: 0, columns: [] },
            comparison: { hasResults: false, summary: '' },
            jobs: { inFlight: 0, items: [] },
            modal: { addRetailerOpen: false, addSetOpen: false },
            availableActions: []
        };

        const bc = document.getElementById('breadcrumb');
        if (bc) {
            bc.querySelectorAll('a, span:not(.sep)').forEach(function (el) {
                const txt = (el.textContent || '').trim();
                if (txt && txt !== '/') data.breadcrumb.push(txt);
            });
        }

        // Infer current level from breadcrumb depth + content fingerprints.
        const main = document.getElementById('mainContent');
        if (main) {
            if (main.querySelector('.retailer-grid')) {
                data.currentLevel = 'retailers';
                main.querySelectorAll('.retailer-card').forEach(function (card) {
                    const name = (card.querySelector('h4') ? card.querySelector('h4').textContent : '').trim();
                    const meta = (card.querySelector('.meta') ? card.querySelector('.meta').textContent : '').trim();
                    if (name) data.retailers.push({ name: name, meta: meta });
                });
            } else if (main.querySelector('.version-list')) {
                data.currentLevel = 'versions';
                main.querySelectorAll('.version-list li').forEach(function (li) {
                    const isCurrent = li.classList.contains('current');
                    const label = (li.textContent || '').trim().split('\n')[0];
                    data.versions.push({ label: label, current: isCurrent });
                });
            } else if (main.querySelector('.req-table')) {
                data.currentLevel = 'requirements';
                const table = main.querySelector('.req-table');
                table.querySelectorAll('th').forEach(function (th) {
                    const t = (th.textContent || '').trim();
                    if (t) data.requirements.columns.push(t);
                });
                data.requirements.rowCount = table.querySelectorAll('tbody tr').length;
            } else {
                // Document set listing
                const setCards = main.querySelectorAll('.retailer-card, .doc-set-card');
                if (setCards.length) {
                    data.currentLevel = 'document_sets';
                    setCards.forEach(function (card) {
                        const title = (card.querySelector('h4') ? card.querySelector('h4').textContent : '').trim();
                        if (title) data.documentSets.push({ category: title });
                    });
                }
            }

            const compSummary = main.querySelector('.summary-badge');
            if (compSummary) {
                data.comparison.hasResults = true;
                const badges = main.querySelectorAll('.summary-badge');
                const labels = [];
                badges.forEach(function (b) { labels.push((b.textContent || '').trim()); });
                data.comparison.summary = labels.join(' · ');
            }
        }

        const jobsContainer = document.querySelector('.jobs-container');
        if (jobsContainer) {
            jobsContainer.querySelectorAll('.job-row').forEach(function (row) {
                const state = ['queued', 'running', 'done', 'duplicate', 'error']
                    .find(function (s) { return row.classList.contains(s); }) || 'unknown';
                const filename = (row.querySelector('.job-filename') ? row.querySelector('.job-filename').textContent : '').trim();
                const msg = (row.querySelector('.job-msg') ? row.querySelector('.job-msg').textContent : '').trim();
                data.jobs.items.push({ state: state, filename: filename, message: msg });
                if (state === 'queued' || state === 'running') data.jobs.inFlight += 1;
            });
        }

        const addRetailer = document.getElementById('addRetailerModal');
        if (addRetailer && addRetailer.classList.contains('show')) data.modal.addRetailerOpen = true;
        const addSet = document.getElementById('addSetModal');
        if (addSet && addSet.classList.contains('show')) data.modal.addSetOpen = true;

        if (data.currentLevel === 'retailers') {
            data.availableActions.push('Click a retailer card to see its document sets');
            data.availableActions.push('Click "Add Retailer" to register a new one');
        } else if (data.currentLevel === 'document_sets') {
            data.availableActions.push('Click a document set to see its version history');
            data.availableActions.push('Add a new document set for this retailer');
        } else if (data.currentLevel === 'versions') {
            data.availableActions.push('Open a version to view its extracted requirements');
            data.availableActions.push('Upload a new version to extract its requirements');
            data.availableActions.push('Compare two versions to see what changed');
        } else if (data.currentLevel === 'requirements') {
            data.availableActions.push('Export this version to Excel');
        }

        return data;
    }
};

console.log('Compliance Management assistant context loaded');
