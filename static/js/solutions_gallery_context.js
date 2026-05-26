/**
 * Page context provider for /solutions/gallery (templates/solutions_gallery.html).
 * The grid is rendered dynamically by SolutionsGallery.init(); we read the
 * resulting cards to expose what the user is looking at.
 */
window.assistantPageContext = {
    page: 'solutions_gallery',
    pageName: 'Solutions Gallery',

    getPageData: function () {
        const data = {
            state: 'unknown',          // 'loading' | 'empty' | 'populated'
            solutionCount: 0,
            solutions: [],
            hasFileSelectedForUpload: false,
            availableActions: []
        };

        const loading = document.getElementById('solutionsLoading');
        const empty = document.getElementById('solutionsEmpty');
        const grid = document.getElementById('solutionsGrid');

        if (loading && loading.offsetParent !== null) {
            data.state = 'loading';
        } else if (empty && empty.offsetParent !== null) {
            data.state = 'empty';
        } else if (grid) {
            const cards = grid.querySelectorAll('.solution-card, .card, [data-solution-id]');
            data.solutionCount = cards.length;
            cards.forEach(function (card) {
                if (card.offsetParent === null) return;
                const titleEl = card.querySelector('h3, h4, h5, .solution-title, .card-title');
                const descEl = card.querySelector('p, .solution-description, .card-text');
                const versionEl = card.querySelector('.solution-version, .version, .badge');
                const installedBadge = card.querySelector('.installed, .badge-success');
                const item = {
                    title: titleEl ? (titleEl.textContent || '').trim().split('\n')[0] : '',
                    description: descEl ? (descEl.textContent || '').trim().split('\n')[0] : '',
                    version: versionEl ? (versionEl.textContent || '').trim() : '',
                    installed: !!installedBadge,
                    id: card.dataset && card.dataset.solutionId ? card.dataset.solutionId : null
                };
                if (item.description.length > 200) item.description = item.description.slice(0, 200) + '…';
                if (item.title) data.solutions.push(item);
            });
            data.state = data.solutions.length > 0 ? 'populated' : 'empty';
        }

        const uploadInput = document.getElementById('uploadSolutionFile');
        if (uploadInput && uploadInput.files && uploadInput.files.length > 0) {
            data.hasFileSelectedForUpload = true;
        }

        if (data.state === 'empty') {
            data.availableActions.push('Click "Author" to create a new solution from scratch');
            data.availableActions.push('Click "Upload Solution" to install a .zip bundle');
        } else if (data.state === 'populated') {
            data.availableActions.push('Click any solution card to install or view details');
            data.availableActions.push('Click "Author" to build a new solution');
            data.availableActions.push('Click "Upload Solution" to install a .zip a teammate shared with you');
        }

        return data;
    }
};

console.log('Solutions Gallery assistant context loaded');
