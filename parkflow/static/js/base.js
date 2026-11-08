/**
 * ParkFlow Kenya - Base JavaScript
 * Common utilities and shared functionality
 */

// Utility functions
const ParkFlow = {
    /**
     * Make an API request
     * @param {string} url - Endpoint URL
     * @param {Object} options - Fetch options
     * @returns {Promise<Object>} Response data
     */
    async request(url, options = {}) {
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.getCSRFToken(),
            },
            credentials: 'same-origin',
        };

        const mergedOptions = {
            ...defaultOptions,
            ...options,
            headers: {
                ...defaultOptions.headers,
                ...options.headers,
            },
        };

        let response;
        try {
            response = await fetch(url, mergedOptions);
        } catch (error) {
            console.error('Network error:', error);
            throw new Error('Network error - check your connection and retry.');
        }

        // Session expired: the login middleware redirected to an HTML page.
        if (response.redirected && new URL(response.url).pathname.startsWith('/accounts/login')) {
            window.location.href = response.url;
            throw new Error('Your session has expired. Please sign in again.');
        }

        const isJson = (response.headers.get('Content-Type') || '').includes('application/json');
        if (!isJson) {
            throw new Error(`Unexpected server response (HTTP ${response.status})`);
        }
        const data = await response.json();

        // Business failures (e.g. "parking full") come back as JSON with
        // success=false; hand them to the caller instead of throwing.
        if (!response.ok && data.success === undefined) {
            throw new Error(data.message || `HTTP ${response.status}`);
        }
        return data;
    },

    /**
     * Render label/value rows into a container safely (textContent only).
     * @param {HTMLElement} container - Target element (emptied first)
     * @param {Array<[string, string]>} rows - [label, value] pairs
     */
    renderDetails(container, rows) {
        const grid = document.createElement('div');
        grid.className = 'detail-grid';
        grid.style.cssText = 'display: grid; grid-template-columns: 1fr 1fr; gap: var(--spacing-sm); margin-bottom: var(--spacing-md);';
        for (const [label, value] of rows) {
            const cell = document.createElement('div');
            const strong = document.createElement('strong');
            strong.textContent = `${label}: `;
            cell.append(strong, document.createTextNode(String(value ?? '')));
            grid.appendChild(cell);
        }
        container.replaceChildren(grid);
    },

    /**
     * Get CSRF token from cookie
     * @returns {string} CSRF token
     */
    getCSRFToken() {
        const name = 'csrftoken';
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            cookie = cookie.trim();
            if (cookie.startsWith(name + '=')) {
                return cookie.substring(name.length + 1);
            }
        }
        return '';
    },

    /**
     * Format duration in minutes to human readable
     * @param {number} minutes - Duration in minutes
     * @returns {string} Formatted duration
     */
    formatDuration(minutes) {
        if (minutes < 60) {
            return `${minutes} min`;
        }
        const hours = Math.floor(minutes / 60);
        const mins = minutes % 60;
        return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
    },

    /**
     * Format currency in KSh
     * @param {number} amount - Amount in KSh
     * @returns {string} Formatted currency
     */
    formatCurrency(amount) {
        return `KSh ${Number(amount).toLocaleString()}`;
    },

    /**
     * Format datetime for display
     * @param {string} isoString - ISO datetime string
     * @returns {string} Formatted datetime
     */
    formatDateTime(isoString) {
        const date = new Date(isoString);
        return date.toLocaleString('en-KE', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    },

    /**
     * Show alert message
     * @param {string} message - Message text
     * @param {string} type - Alert type (success, error, warning, info)
     * @param {HTMLElement} container - Container element (optional)
     */
    showAlert(message, type = 'info', container = null) {
        const alert = document.createElement('div');
        alert.className = `alert alert-${type}`;
        alert.textContent = message;

        const target = container || document.querySelector('.messages') || document.querySelector('.main-content');
        if (target) {
            target.insertBefore(alert, target.firstChild);

            // Auto-remove after 5 seconds
            setTimeout(() => {
                alert.style.opacity = '0';
                alert.style.transition = 'opacity 0.3s';
                setTimeout(() => alert.remove(), 300);
            }, 5000);
        }
    },

    /**
     * Normalize Kenyan registration number
     * @param {string} reg - Registration number
     * @returns {string} Normalized registration number
     */
    normalizeRegistration(reg) {
        const clean = reg.replace(/\s+/g, '').toUpperCase();
        if (clean.length >= 4) {
            return clean.slice(0, 3) + ' ' + clean.slice(3);
        }
        return clean;
    },
};

// Global error handler for fetch
window.addEventListener('unhandledrejection', (event) => {
    console.error('Unhandled promise rejection:', event.reason);
    ParkFlow.showAlert('An unexpected error occurred. Please try again.', 'error');
});

// Auto-hide Django messages after 5 seconds
document.addEventListener('DOMContentLoaded', () => {
    const messages = document.querySelectorAll('.messages .alert');
    messages.forEach((msg) => {
        setTimeout(() => {
            msg.style.opacity = '0';
            msg.style.transition = 'opacity 0.3s';
            setTimeout(() => msg.remove(), 300);
        }, 5000);
    });
});

// Export for use in other scripts
window.ParkFlow = ParkFlow;