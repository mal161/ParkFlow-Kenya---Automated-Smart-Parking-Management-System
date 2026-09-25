/**
 * ParkFlow Kenya - Base JavaScript
 * Common utilities and shared functionality
 */

// API base URL
const API_BASE = '';

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

        try {
            const response = await fetch(url, mergedOptions);
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.message || `HTTP ${response.status}`);
            }

            return data;
        } catch (error) {
            console.error('API Error:', error);
            throw error;
        }
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

    /**
     * Validate Kenyan registration number format
     * @param {string} reg - Registration number
     * @returns {boolean} Whether valid
     */
    validateRegistration(reg) {
        const clean = reg.replace(/\s+/g, '').toUpperCase();
        return /^K[A-Z]{2}\d{1,3}[A-Z]?$/.test(clean);
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