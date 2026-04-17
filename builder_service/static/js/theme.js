/**
 * Theme Manager
 * Handles dark/light mode toggle via .light-mode class on body.
 */

export class ThemeManager {
    constructor() {
        this.isLight = false;
        this.toggleBtn = document.getElementById('btn-theme-toggle');

        // Restore saved preference
        const saved = localStorage.getItem('builder-theme');
        if (saved === 'light') {
            this.isLight = true;
            document.body.classList.add('light-mode');
        }

        this.toggleBtn?.addEventListener('click', () => this.toggle());
        this._updateIcon();
    }

    toggle() {
        this.isLight = !this.isLight;
        document.body.classList.toggle('light-mode', this.isLight);
        localStorage.setItem('builder-theme', this.isLight ? 'light' : 'dark');
        this._updateIcon();
    }

    _updateIcon() {
        if (!this.toggleBtn) return;
        // Sun icon for dark mode (click to go light), moon for light (click to go dark)
        if (this.isLight) {
            this.toggleBtn.innerHTML = `<svg class="w-4 h-4 text-zinc-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/>
            </svg>`;
        } else {
            this.toggleBtn.innerHTML = `<svg class="w-4 h-4 text-zinc-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/>
            </svg>`;
        }
    }
}
