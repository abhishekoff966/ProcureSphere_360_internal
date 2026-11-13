// ProcureSphere 360 Client Application JS

// =============================================================================
// Styled Dialog & Toast System (replaces native confirm()/alert() popups)
// =============================================================================
(function () {
    'use strict';

    var TONES = {
        danger:  { icon: 'fa-triangle-exclamation', ring: 'ps-dialog-icon-danger',  btn: 'btn-danger' },
        warning: { icon: 'fa-triangle-exclamation', ring: 'ps-dialog-icon-warning', btn: 'btn-warning text-dark' },
        success: { icon: 'fa-circle-check',         ring: 'ps-dialog-icon-success', btn: 'btn-success' },
        info:    { icon: 'fa-circle-info',          ring: 'ps-dialog-icon-info',    btn: 'btn-primary' }
    };

    function escapeHtml(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    var backdrop = null;

    function ensureBackdrop() {
        if (backdrop) return backdrop;
        backdrop = document.createElement('div');
        backdrop.className = 'ps-dialog-backdrop';
        backdrop.hidden = true;
        document.body.appendChild(backdrop);
        backdrop.addEventListener('mousedown', function (e) {
            if (e.target === backdrop) close(false);
        });
        return backdrop;
    }

    var openerEl = null;

    function close(result) {
        if (!backdrop || backdrop.hidden) return;
        var el = backdrop.querySelector('.ps-dialog');
        if (el) {
            el.classList.add('ps-dialog-leaving');
            el.style.animation = 'psDialogOut .15s ease forwards';
        }
        var inst = backdrop;
        backdrop = null;
        setTimeout(function () {
            if (inst.parentNode) inst.parentNode.removeChild(inst);
        }, 160);
        document.body.classList.remove('ps-dialog-open');
        document.removeEventListener('keydown', onKeydown);
        var resolve = currentResolve;
        currentResolve = null;
        if (resolve) resolve(result);
        if (openerEl && document.contains(openerEl)) openerEl.focus();
        openerEl = null;
    }

    function onKeydown(e) {
        if (e.key === 'Escape') { e.preventDefault(); close(false); }
        else if (e.key === 'Tab') {
            if (!backdrop) return;
            var focusables = backdrop.querySelectorAll('.ps-dialog button:not([disabled])');
            if (!focusables.length) return;
            var first = focusables[0], last = focusables[focusables.length - 1];
            if (!e.shiftKey && e.target === last) { e.preventDefault(); first.focus(); }
            else if (e.shiftKey && e.target === first) { e.preventDefault(); last.focus(); }
            else if (!backdrop.contains(e.target)) { e.preventDefault(); first.focus(); }
        }
    }

    var currentResolve = null;

    /**
     * window.PSConfirm({ title, message, warning, details, tone, confirmLabel, cancelLabel, icon })
     *   details: string or array — semicolon-separated "Label = value" entries.
     * Returns a Promise<boolean> resolved on the user's choice.
     */
    window.PSConfirm = function (opts) {
        opts = opts || {};
        var tone = TONES[opts.tone] ? opts.tone : 'warning';
        var t = TONES[tone];
        var details = [];
        if (typeof opts.details === 'string' && opts.details.trim()) {
            opts.details.split(';').forEach(function (part) {
                var bits = part.split('=');
                if (bits.length >= 2 && bits[0].trim()) {
                    details.push({ label: bits.shift().trim(), value: bits.join('=').trim() });
                } else if (part.trim()) {
                    details.push({ label: '', value: part.trim() });
                }
            });
        } else if (Array.isArray(opts.details)) {
            details = opts.details.filter(function (d) { return d && (d.label || d.value); });
        }

        var icon = opts.icon || t.icon;
        var confirmLabel = opts.confirmLabel || 'Yes, continue';
        var cancelLabel = opts.cancelLabel || 'Cancel';

        var html =
            '<div class="ps-dialog" role="dialog" aria-modal="true" aria-labelledby="ps-dialog-title">' +
                '<div class="ps-dialog-icon ' + t.ring + '"><i class="fa-solid ' + escapeHtml(icon) + '"></i></div>' +
                '<h5 class="ps-dialog-title" id="ps-dialog-title">' + escapeHtml(opts.title || 'Are you sure?') + '</h5>' +
                (opts.message ? '<p class="ps-dialog-message">' + escapeHtml(opts.message) + '</p>' : '') +
                (details.length ? '<div class="ps-dialog-details">' + details.map(function (d) {
                    return '<div class="ps-dialog-detail-row">' +
                        (d.label ? '<span class="ps-dialog-detail-label">' + escapeHtml(d.label) + '</span>' : '') +
                        '<span class="ps-dialog-detail-value">' + escapeHtml(d.value) + '</span></div>';
                }).join('') + '</div>' : '') +
                (opts.warning ? '<div class="ps-dialog-warn"><i class="fa-solid fa-circle-exclamation me-2"></i>' + escapeHtml(opts.warning) + '</div>' : '') +
                '<div class="ps-dialog-actions">' +
                    '<button type="button" class="btn rounded-pill ps-dialog-btn ps-dialog-cancel btn-outline-secondary">' + escapeHtml(cancelLabel) + '</button>' +
                    '<button type="button" class="btn rounded-pill ps-dialog-btn ps-dialog-confirm ' + t.btn + '">' + escapeHtml(confirmLabel) + '</button>' +
                '</div>' +
            '</div>';

        openerEl = document.activeElement;
        var inst = ensureBackdrop();
        inst.hidden = false;
        inst.innerHTML = html;
        // rebuild singleton after removal so future confirms always work
        document.body.classList.add('ps-dialog-open');

        inst.querySelector('.ps-dialog-cancel').addEventListener('click', function () { close(false); });
        var confirmBtn = inst.querySelector('.ps-dialog-confirm');
        confirmBtn.addEventListener('click', function () { close(true); });

        setTimeout(function () { confirmBtn.focus(); }, 30);
        document.addEventListener('keydown', onKeydown);

        return new Promise(function (resolve) { currentResolve = resolve; });
    };

    // ---- Form interception: any <form data-confirm-title="..."> opens the styled dialog ----
    function optionsFromAttrs(form) {
        var attrs = form.attributes;
        function val(name) { return (attrs[name] ? attrs[name].value : '').trim(); }
        return {
            title: val('data-confirm-title') || 'Are you sure?',
            message: val('data-confirm-message'),
            warning: val('data-confirm-warning'),
            details: val('data-confirm-details'),
            tone: val('data-confirm-tone') || 'info',
            confirmLabel: val('data-confirm-confirm-label') || 'Yes, continue',
            cancelLabel: val('data-confirm-cancel-label') || 'Cancel',
            icon: val('data-confirm-icon')
        };
    }

    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (form.dataset.psConfirmed === 'true') {
            form.dataset.psConfirmed = 'false';
            return; // confirmed pass-through — let global handlers run
        }
        if (!form.hasAttribute('data-confirm-title') && !form.hasAttribute('data-confirm-message')) return;

        e.preventDefault();
        e.stopImmediatePropagation(); // hold the global submit manager until the dialog is answered

        var submitter = e.submitter;
        var opts = optionsFromAttrs(form);
        window.PSConfirm(opts).then(function (ok) {
            if (!ok) return;
            form.dataset.psConfirmed = 'true';
            try {
                form.requestSubmit(submitter);
            } catch (err) {
                form.submit();
            }
        });
    }, true);

    // ---- Toasts (replaces native alert()) ----
    var TOAST_TONES = {
        info:    { icon: 'fa-circle-info',      cls: 'ps-toast-info' },
        success: { icon: 'fa-circle-check',     cls: 'ps-toast-success' },
        warning: { icon: 'fa-triangle-exclamation', cls: 'ps-toast-warning' },
        danger:  { icon: 'fa-circle-xmark',     cls: 'ps-toast-danger' }
    };

    window.PSToast = function (message, tone, title) {
        tone = TOAST_TONES[tone] ? tone : 'info';
        var t = TOAST_TONES[tone];
        var container = document.querySelector('.ps-toast-container');
        if (!container) {
            container = document.createElement('div');
            container.className = 'ps-toast-container';
            document.body.appendChild(container);
        }
        var toast = document.createElement('div');
        toast.className = 'ps-toast ' + t.cls;
        toast.setAttribute('role', 'status');
        toast.innerHTML =
            '<div class="ps-toast-icon"><i class="fa-solid ' + t.icon + '"></i></div>' +
            '<div class="ps-toast-body">' +
                (title ? '<div class="ps-toast-title">' + escapeHtml(title) + '</div>' : '') +
                '<div class="ps-toast-message">' + escapeHtml(message) + '</div>' +
            '</div>' +
            '<button type="button" class="ps-toast-close" aria-label="Dismiss"><i class="fa-solid fa-xmark"></i></button>';
        container.appendChild(toast);

        var timer = setTimeout(function () { dismiss(); }, 5000);
        function dismiss() {
            if (!toast.parentNode) return;
            toast.classList.add('ps-toast-leaving');
            setTimeout(function () { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 220);
        }
        toast.querySelector('.ps-toast-close').addEventListener('click', function () {
            clearTimeout(timer); dismiss();
        });
    };
})();
document.addEventListener('DOMContentLoaded', function () {
    console.log('ProcureSphere 360 Interface Initialized.');

    // Auto-dismiss success and info alert banners after 4.5 seconds (BUG-018)
    document.querySelectorAll('.alert.alert-dismissible.alert-success, .alert.alert-dismissible.alert-info').forEach(function (alertEl) {
        setTimeout(function () {
            if (!document.body.contains(alertEl)) return;
            try {
                if (window.bootstrap && window.bootstrap.Alert) {
                    window.bootstrap.Alert.getOrCreateInstance(alertEl).close();
                    return;
                }
            } catch (err) {}
            alertEl.classList.remove('show');
            setTimeout(function () { if (alertEl.parentNode) alertEl.parentNode.removeChild(alertEl); }, 200);
        }, 4500);
    });


    // Auto-inject CSRF token into HTMX headers
    document.body.addEventListener('htmx:configRequest', function (evt) {
        const csrfToken = getCookie('csrftoken');
        if (csrfToken) {
            evt.detail.headers['X-CSRFToken'] = csrfToken;
        }
    });

    // Helper to extract cookies
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    // Determine intuitive loading label based on current button text/value
    function getLoadingLabel(btn) {
        if (btn.dataset.loadingText) {
            return btn.dataset.loadingText;
        }
        const text = (btn.textContent || btn.innerText || '').trim().toLowerCase();
        const val = (btn.value || '').trim().toLowerCase();

        if (text.includes('draft') || val.includes('draft')) {
            return 'Saving Draft...';
        }
        if (text.includes('approve') || val.includes('approved')) {
            return 'Approving...';
        }
        if (text.includes('reject') || val.includes('rejected')) {
            return 'Rejecting...';
        }
        if (text.includes('cancel')) {
            return 'Cancelling...';
        }
        if (text.includes('payment') || text.includes('process')) {
            return 'Processing Payment...';
        }
        if (text.includes('resolve')) {
            return 'Resolving...';
        }
        if (text.includes('save')) {
            return 'Saving...';
        }
        if (text.includes('submit')) {
            return 'Submitting...';
        }
        if (text.includes('delete')) {
            return 'Deleting...';
        }
        return 'Processing...';
    }

    // Set button into loading state
    window.setButtonLoading = function (btn, customText) {
        if (!btn || btn.classList.contains('btn-loading')) return;
        btn.dataset.originalHtml = btn.innerHTML;
        btn.dataset.originalDisabled = btn.disabled;
        btn.classList.add('btn-loading');
        btn.disabled = true;

        const label = customText || getLoadingLabel(btn);
        btn.innerHTML = `<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>${label}`;
    };

    // Restore button from loading state
    window.resetButtonLoading = function (btn) {
        if (!btn || !btn.dataset.originalHtml) return;
        btn.innerHTML = btn.dataset.originalHtml;
        btn.disabled = btn.dataset.originalDisabled === 'true';
        btn.classList.remove('btn-loading');
        delete btn.dataset.originalHtml;
        if (btn.form) {
            window.resetFormCancelButtons(btn.form);
        }
    };

    // Disable all cancel buttons and navigation actions associated with a form
    window.disableFormCancelButtons = function (form) {
        if (!form) return;
        const cancelElements = form.querySelectorAll(
            'a.btn, button:not([type="submit"]), [data-action="cancel"], .btn-cancel, #pr_cancel_btn, #pr_edit_cancel_btn, a[href*="requisitions"]'
        );
        cancelElements.forEach(function (el) {
            if (el.tagName === 'BUTTON') {
                el.disabled = true;
            }
            el.classList.add('disabled');
            el.setAttribute('aria-disabled', 'true');
            el.setAttribute('tabindex', '-1');
            el.style.pointerEvents = 'none';
            el.style.opacity = '0.5';
            el.style.cursor = 'not-allowed';
        });
        const formId = form.id;
        if (formId) {
            document.querySelectorAll(`[data-form-id="${formId}"], [data-form="${formId}"]`).forEach(function (el) {
                if (el.tagName === 'BUTTON') el.disabled = true;
                el.classList.add('disabled');
                el.setAttribute('aria-disabled', 'true');
                el.style.pointerEvents = 'none';
                el.style.opacity = '0.5';
                el.style.cursor = 'not-allowed';
            });
        }
    };

    // Restore cancel buttons and navigation actions associated with a form
    window.resetFormCancelButtons = function (form) {
        if (!form) return;
        const cancelElements = form.querySelectorAll(
            'a.btn, button:not([type="submit"]), [data-action="cancel"], .btn-cancel, #pr_cancel_btn, #pr_edit_cancel_btn, a[href*="requisitions"]'
        );
        cancelElements.forEach(function (el) {
            if (el.tagName === 'BUTTON') {
                el.disabled = false;
            }
            el.classList.remove('disabled');
            el.removeAttribute('aria-disabled');
            el.removeAttribute('tabindex');
            el.style.pointerEvents = '';
            el.style.opacity = '';
            el.style.cursor = '';
        });
        const formId = form.id;
        if (formId) {
            document.querySelectorAll(`[data-form-id="${formId}"], [data-form="${formId}"]`).forEach(function (el) {
                if (el.tagName === 'BUTTON') el.disabled = false;
                el.classList.remove('disabled');
                el.removeAttribute('aria-disabled');
                el.style.pointerEvents = '';
                el.style.opacity = '';
                el.style.cursor = '';
            });
        }
    };

    // Track clicked submit button on form and block rapid double-clicks
    document.addEventListener('click', function (e) {
        const btn = e.target.closest('button[type="submit"], input[type="submit"]');
        if (btn && btn.form) {
            if (btn.form.dataset.submitting === 'true' || btn.classList.contains('btn-loading') || btn.disabled) {
                e.preventDefault();
                e.stopImmediatePropagation();
                return false;
            }
            btn.form._ps360_clickedSubmitBtn = btn;
        }

        // Intercept clicks on Cancel buttons or links while a form is in submitting/loading state
        const cancelEl = e.target.closest('a.disabled, a[aria-disabled="true"], button[disabled], [data-action="cancel"], #pr_cancel_btn, #pr_edit_cancel_btn');
        if (cancelEl) {
            const form = cancelEl.closest('form') || document.querySelector('form[data-submitting="true"]');
            if (cancelEl.classList.contains('disabled') || cancelEl.getAttribute('aria-disabled') === 'true' || (form && form.dataset.submitting === 'true')) {
                e.preventDefault();
                e.stopImmediatePropagation();
                return false;
            }
        }
    }, true);

    // Global Form Double-Submit Protection & Loading State
    document.addEventListener('submit', function (e) {
        const form = e.target;
        if (!form || form.tagName !== 'FORM') return;

        // Skip forms marked to ignore auto-loading (e.g. instant download links or search filters)
        if (form.dataset.noLoading === 'true' || form.getAttribute('method') === 'GET') {
            return;
        }

        // 1. Prevent duplicate submission if already processing
        if (form.dataset.submitting === 'true') {
            e.preventDefault();
            e.stopImmediatePropagation();
            return false;
        }

        // 2. Check HTML5 form validity; if invalid, browser stops submission and we shouldn't lock
        if (typeof form.checkValidity === 'function' && !form.checkValidity()) {
            return;
        }

        // 3. Mark form as submitting
        form.dataset.submitting = 'true';

        // 4. Identify clicked button
        const clickedBtn = form._ps360_clickedSubmitBtn;
        const submitButtons = form.querySelectorAll('button[type="submit"], input[type="submit"]');

        // Preserve name & value of clicked button via hidden input so disabling doesn't strip it
        if (clickedBtn && clickedBtn.name && clickedBtn.value) {
            const existingHidden = form.querySelector(`input[type="hidden"][data-dynamic-param="${clickedBtn.name}"]`);
            if (!existingHidden) {
                const hiddenInput = document.createElement('input');
                hiddenInput.type = 'hidden';
                hiddenInput.name = clickedBtn.name;
                hiddenInput.value = clickedBtn.value;
                hiddenInput.setAttribute('data-dynamic-param', clickedBtn.name);
                form.appendChild(hiddenInput);
            }
        }

        // Put clicked button into loading state and disable all submit buttons on this form
        if (clickedBtn) {
            window.setButtonLoading(clickedBtn);
        }

        submitButtons.forEach(function (btn) {
            if (btn !== clickedBtn) {
                btn.disabled = true;
                btn.classList.add('btn-loading');
            }
        });

        // Disable Cancel buttons and navigation actions while form is submitting/loading
        window.disableFormCancelButtons(form);
    }, false);

    // Intercept logout links to trigger session termination and redirect directly to /login/
    document.addEventListener('click', function (e) {
        const logoutLink = e.target.closest('a[href="/logout/"], a[href^="/logout"], #signout-confirm-action-btn');
        if (logoutLink) {
            e.preventDefault();
            try {
                sessionStorage.removeItem('ps_nav_stack');
                sessionStorage.removeItem('ps_nav_index');
                sessionStorage.removeItem('ps_just_logged_in');
                sessionStorage.removeItem('ps_visited_subpage');
                sessionStorage.removeItem('ps_last_subpage_path');
                sessionStorage.removeItem('ps_active_tab');
            } catch (err) {}
            const targetUrl = logoutLink.getAttribute('href') || '/logout/';
            fetch(targetUrl, {
                method: 'GET',
                credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            }).finally(function () {
                window.location.replace('/login/');
            });
        }
    });

    // Restore buttons on page show (e.g. back/forward navigation or browser cache)
    window.addEventListener('pageshow', function (event) {
        if (event.persisted) {
            window.location.reload();
        }
        document.querySelectorAll('form[data-submitting="true"]').forEach(function (form) {
            delete form.dataset.submitting;
            const buttons = form.querySelectorAll('.btn-loading');
            buttons.forEach(function (btn) {
                window.resetButtonLoading(btn);
            });
            window.resetFormCancelButtons(form);
        });
    });

    // =========================================================================
    // Enterprise Portal Navigation & Sign-Out Guard Controller
    // Traps native browser Back arrow at ANY dashboard root to prevent accidental sign-out
    // Ensures subpages, query tabs, and in-page anchors navigate naturally without premature popups
    // =========================================================================
    (function initPortalNavigationController() {
        // Canonical dashboard roots across all enterprise roles
        function isDashboardRoot(pathname) {
            if (!pathname) pathname = window.location.pathname;
            const clean = pathname.replace(/\/+$/, '') || '/';
            const exactRoots = [
                '/dashboard',
                '/requester',
                '/requester/dashboard',
                '/approver',
                '/approver/dashboard',
                '/procurement',
                '/procurement/dashboard',
                '/finance',
                '/finance/dashboard',
                '/stores',
                '/stores/dashboard',
                '/legal',
                '/legal/dashboard',
                '/audit',
                '/audit/dashboard',
                '/superadmin',
                '/superadmin/dashboard',
                '/manager',
                '/manager/dashboard',
                '/vendor',
                '/vendor/dashboard',
                '/admin'
            ];
            return exactRoots.includes(clean);
        }

        const curPath = window.location.pathname;
        const onRoot = isDashboardRoot(curPath);

        // Track session depth & entry point to ensure back button navigates seamlessly across internal modules
        // and only prompts sign-out confirmation at the initial entry point.
        let isInitialEntry = false;
        try {
            if (!sessionStorage.getItem('ps_portal_initialized')) {
                sessionStorage.setItem('ps_portal_initialized', 'true');
                sessionStorage.setItem('ps_initial_entry_path', curPath);
                sessionStorage.setItem('ps_nav_depth', '0');
                isInitialEntry = true;
            } else {
                let depth = parseInt(sessionStorage.getItem('ps_nav_depth') || '0', 10);
                sessionStorage.setItem('ps_nav_depth', String(depth + 1));
            }

            if (!onRoot) {
                sessionStorage.setItem('ps_visited_subpage', 'true');
                sessionStorage.setItem('ps_last_subpage_path', curPath);
            }
        } catch (e) {}

        let signoutTimerInterval = null;

        function clearSignoutTimer() {
            if (signoutTimerInterval) {
                clearInterval(signoutTimerInterval);
                signoutTimerInterval = null;
            }
        }

        function showSignoutModal() {
            // Strict Guard: Under NO circumstance show the modal if not on a canonical dashboard root
            if (!isDashboardRoot(window.location.pathname)) {
                return;
            }

            const modalEl = document.getElementById('signout-confirm-modal');
            if (modalEl && typeof bootstrap !== 'undefined' && bootstrap.Modal) {
                const modal = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
                modal.show();
                const card = modalEl.querySelector('.modal-content');
                if (card) {
                    card.style.animation = 'none';
                    void card.offsetWidth;
                    card.style.animation = 'psDialogShake 0.35s ease';
                }

                // Auto-Dismiss Timer (6 seconds)
                clearSignoutTimer();
                const totalSeconds = 6;
                let remainingMs = totalSeconds * 1000;
                const stepMs = 100;

                const secEl = document.getElementById('signout-countdown-sec');
                const progEl = document.getElementById('signout-progress-bar');
                if (secEl) secEl.textContent = String(totalSeconds);
                if (progEl) progEl.style.width = '100%';

                signoutTimerInterval = setInterval(function () {
                    remainingMs -= stepMs;
                    if (remainingMs <= 0) {
                        clearSignoutTimer();
                        modal.hide();
                        return;
                    }
                    const sec = Math.ceil(remainingMs / 1000);
                    if (secEl) secEl.textContent = String(sec);
                    if (progEl) {
                        const pct = Math.max(0, (remainingMs / (totalSeconds * 1000)) * 100);
                        progEl.style.width = pct + '%';
                    }
                }, stepMs);

                // Stop timer if closed by user clicking "Stay on Dashboard" or "Sign Out"
                modalEl.removeEventListener('hidden.bs.modal', clearSignoutTimer);
                modalEl.addEventListener('hidden.bs.modal', clearSignoutTimer, { once: true });
            } else if (window.PSConfirm) {
                window.PSConfirm({
                    title: 'Sign Out Confirmation',
                    message: 'You are currently signed into ProcureSphere 360. Do you want to sign out of your session, or stay on your dashboard?',
                    confirmLabel: 'Sign Out',
                    cancelLabel: 'Stay on Dashboard',
                    tone: 'warning',
                    icon: 'fa-arrow-right-from-bracket'
                }).then(function (ok) {
                    if (ok) {
                        window.location.replace('/logout/');
                    }
                });
            }
        }

        // Arm the history barrier ONLY at the initial entry point on a dashboard root
        function armDashboardGuard() {
            if (!window.history || !window.history.pushState) return;
            const currentPath = window.location.pathname;
            if (!isDashboardRoot(currentPath)) return;

            // Only arm at initial entry point
            let navDepth = 0;
            try {
                navDepth = parseInt(sessionStorage.getItem('ps_nav_depth') || '0', 10);
            } catch (e) {}

            // If user navigated forward across internal modules (navDepth > 0), do not trap
            if (navDepth > 0 && !isInitialEntry) {
                return;
            }

            // Only push barrier if not already at guard state
            if (!window.history.state || !window.history.state.isPsGuard) {
                try {
                    window.history.replaceState(
                        { ps_portal: true, isPsBase: true, isInitialEntry: true, rootPath: currentPath },
                        document.title,
                        window.location.href
                    );
                    window.history.pushState(
                        { ps_portal: true, isPsGuard: true, isInitialEntry: true, rootPath: currentPath },
                        document.title,
                        window.location.href
                    );
                } catch (e) {}
            }
        }

        // Arm immediately on dashboard root page load ONLY at the initial entry point
        if (onRoot && isInitialEntry) {
            armDashboardGuard();
        }

        // Re-arm when shown via browser navigation ONLY if at initial entry point
        window.addEventListener('pageshow', function () {
            if (isDashboardRoot(window.location.pathname)) {
                let navDepth = 0;
                try {
                    navDepth = parseInt(sessionStorage.getItem('ps_nav_depth') || '0', 10);
                } catch (e) {}
                if (navDepth === 0) {
                    armDashboardGuard();
                }
            }
        });

        // Tab state tracking for in-page tabs
        let tabHistory = [];

        // Tab click listener (Bootstrap 5 tabs)
        document.addEventListener('shown.bs.tab', function (e) {
            const target = e.target.getAttribute('data-bs-target') || e.target.getAttribute('href');
            if (target && target.startsWith('#')) {
                tabHistory.push(target);
                try {
                    window.history.pushState(
                        { ps_portal: true, isTab: true, tabTarget: target },
                        document.title,
                        window.location.pathname + window.location.search + target
                    );
                } catch (err) {}
            }
        });

        // In-page section anchor links listener (e.g. #tax-info, #bank-info in vendor profile)
        document.addEventListener('click', function (e) {
            const anchor = e.target.closest('a[href^="#"]');
            if (anchor) {
                const href = anchor.getAttribute('href');
                if (href && href.length > 1 && !anchor.hasAttribute('data-bs-toggle') && !anchor.hasAttribute('data-bs-dismiss')) {
                    tabHistory.push(href);
                    try {
                        window.history.pushState(
                            { ps_portal: true, isSectionHash: true, hashTarget: href },
                            document.title,
                            window.location.pathname + window.location.search + href
                        );
                    } catch (err) {}
                }
            }
        });

        // Native Browser Back Button Interceptor
        if (window.history && window.history.pushState) {
            window.addEventListener('popstate', function (e) {
                const currentPath = window.location.pathname;
                const currentlyOnRoot = isDashboardRoot(currentPath);

                // Update session navigation depth on backward navigation
                try {
                    let navDepth = parseInt(sessionStorage.getItem('ps_nav_depth') || '0', 10);
                    if (navDepth > 0) {
                        sessionStorage.setItem('ps_nav_depth', String(navDepth - 1));
                    }
                } catch (err) {}

                // Case 1: Active in-page tab back navigation
                if (e.state && e.state.isTab && e.state.tabTarget) {
                    const tabBtn = document.querySelector(`[data-bs-target="${e.state.tabTarget}"], [href="${e.state.tabTarget}"]`);
                    if (tabBtn && typeof bootstrap !== 'undefined' && bootstrap.Tab) {
                        bootstrap.Tab.getOrCreateInstance(tabBtn).show();
                    }
                    return;
                }

                // Case 2: In-page section anchor navigation (e.g. #tax-info, #bank-info)
                if (e.state && e.state.isSectionHash && e.state.hashTarget) {
                    const el = document.querySelector(e.state.hashTarget);
                    if (el) {
                        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    }
                    return;
                }

                // If user backed out of tabs to the default tab
                if (tabHistory.length > 0 && (!e.state || (!e.state.isTab && !e.state.isSectionHash))) {
                    tabHistory = [];
                    const firstTab = document.querySelector('.nav-tabs .nav-link, .nav-pills .nav-link');
                    if (firstTab && typeof bootstrap !== 'undefined' && bootstrap.Tab) {
                        bootstrap.Tab.getOrCreateInstance(firstTab).show();
                    }
                    return;
                }

                // Case 3: On Subpages (e.g. /vendor/profile/, /vendor/sourcing/, /vendor/bids/, /superadmin/users/, etc.)
                // Normal browser navigation smoothly steps back between subpages, query tabs, or sections.
                // The signout modal NEVER appears under ANY circumstance on subpages!
                if (!currentlyOnRoot) {
                    return;
                }

                // Case 4: Arrived at / on Dashboard Root from internal navigation
                let cameFromSubpage = false;
                try {
                    if (sessionStorage.getItem('ps_visited_subpage') === 'true') {
                        cameFromSubpage = true;
                        sessionStorage.removeItem('ps_visited_subpage');
                    }
                } catch (err) {}

                let curNavDepth = 0;
                try {
                    curNavDepth = parseInt(sessionStorage.getItem('ps_nav_depth') || '0', 10);
                } catch (err) {}

                // If user just backed into the root from a subpage, or landed on isPsGuard,
                // or is navigating backwards through previously visited modules:
                // DO NOT show modal! Allow them to seamlessly return through previous pages in history.
                if (cameFromSubpage || (e.state && e.state.isPsGuard) || curNavDepth > 0) {
                    return;
                }

                // Case 5: User is AT the Initial Entry Point barrier attempting to exit backwards
                if (e.state && e.state.isPsBase) {
                    // Prevent repeated triggers: only show if modal is not already showing
                    const modalEl = document.getElementById('signout-confirm-modal');
                    const isAlreadyOpen = modalEl && (modalEl.classList.contains('show') || modalEl.style.display === 'block');
                    if (isAlreadyOpen) {
                        return;
                    }

                    // Re-push barrier state immediately so browser stays locked on dashboard root
                    try {
                        window.history.pushState(
                            { ps_portal: true, isPsGuard: true, isInitialEntry: true, rootPath: currentPath },
                            document.title,
                            window.location.href
                        );
                    } catch (err) {}

                    // Trigger the sign-out confirmation toggle modal ONLY at initial entry point
                    showSignoutModal();
                    return;
                }
            });
        }
    })();
});

// =============================================================================
// Universal Accessible Form Validation Engine (ProcureSphere 360)
// =============================================================================
(function () {
    'use strict';

    function escapeHtml(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function getFieldLabel(input) {
        if (!input) return 'Field';
        if (input.getAttribute('data-label')) {
            return input.getAttribute('data-label').trim();
        }
        if (input.getAttribute('aria-label')) {
            return input.getAttribute('aria-label').replace(/[\*]/g, '').trim();
        }
        if (input.id) {
            var forLabel = document.querySelector('label[for="' + input.id + '"]');
            if (forLabel) {
                var clone = forLabel.cloneNode(true);
                var smalls = clone.querySelectorAll('.small, .text-muted, .badge, i, span.text-muted');
                smalls.forEach(function (s) { s.remove(); });
                var txt = clone.textContent.replace(/[\*\(\)]/g, '').replace(/optional/gi, '').trim();
                if (txt) return txt;
            }
        }
        var parent = input.closest('.col, .col-md-6, .col-md-4, .col-md-8, .col-12, .mb-3, .mb-4, .form-group');
        if (parent) {
            var parentLabel = parent.querySelector('label.form-label, label');
            if (parentLabel && !parentLabel.classList.contains('form-check-label')) {
                var cloneP = parentLabel.cloneNode(true);
                var smallsP = cloneP.querySelectorAll('.small, .text-muted, .badge, i, span.text-muted');
                smallsP.forEach(function (s) { s.remove(); });
                var txtP = cloneP.textContent.replace(/[\*\(\)]/g, '').replace(/optional/gi, '').trim();
                if (txtP) return txtP;
            }
        }
        var ph = input.getAttribute('placeholder');
        if (ph && ph.length > 2 && !ph.toLowerCase().includes('enter') && !ph.toLowerCase().includes('e.g.')) {
            return ph.trim();
        }
        var name = input.getAttribute('name') || '';
        if (name) {
            return name.replace(/_/g, ' ').replace(/\b\w/g, function (l) { return l.toUpperCase(); }).trim();
        }
        return 'Field';
    }

    function showError(input, message) {
        if (!input || !message) return;
        input.classList.add('is-invalid');
        input.setAttribute('aria-invalid', 'true');

        var errorId = (input.id || input.name || 'field') + '-error';
        input.setAttribute('aria-describedby', errorId);

        var targetContainer = input.closest('.input-group') || input;
        var existingError = document.getElementById(errorId);
        if (!existingError) {
            existingError = document.createElement('div');
            existingError.id = errorId;
            existingError.className = 'invalid-feedback d-block mt-1 small fw-semibold';
            existingError.setAttribute('role', 'alert');
            targetContainer.parentNode.insertBefore(existingError, targetContainer.nextSibling);
        }
        existingError.innerHTML = '<i class="fa-solid fa-circle-exclamation me-1 text-danger"></i><span class="text-danger">' + escapeHtml(message) + '</span>';
        existingError.style.display = 'block';

        var col = input.closest('.col, .col-md-6, .col-md-4, .col-md-8, .col-12, .mb-3, .mb-4');
        if (col) {
            var legacyErrors = col.querySelectorAll('.text-danger:not(span.text-danger):not(label):not(i)');
            legacyErrors.forEach(function (el) {
                if (el !== existingError && !existingError.contains(el)) {
                    el.style.display = 'none';
                }
            });
        }
    }

    function clearError(input) {
        if (!input) return;
        input.classList.remove('is-invalid');
        input.removeAttribute('aria-invalid');

        var errorId = (input.id || input.name || 'field') + '-error';
        var errorEl = document.getElementById(errorId);
        if (errorEl) {
            errorEl.style.display = 'none';
            errorEl.remove();
        }
        if (input.getAttribute('aria-describedby') === errorId) {
            input.removeAttribute('aria-describedby');
        }
    }

    function isFieldRequired(input) {
        if (input.hasAttribute('required')) return true;
        var label = input.id ? document.querySelector('label[for="' + input.id + '"]') : null;
        if (label && label.innerHTML.includes('*')) return true;
        var parent = input.closest('.col, .col-md-6, .col-md-4, .col-md-8, .col-12, .mb-3, .mb-4');
        if (parent) {
            var pLabel = parent.querySelector('label');
            if (pLabel && pLabel.innerHTML.includes('*')) return true;
        }
        var name = (input.name || '').toLowerCase();
        var knownRequired = ['code', 'name', 'city', 'organization', 'department', 'year', 'period_number', 'start_date', 'end_date', 'allocated_amount', 'role', 'module', 'price_tolerance_pct', 'qty_tolerance_pct', 'delegate_id'];
        if (knownRequired.indexOf(name) !== -1) return true;
        return false;
    }

    function validateField(input) {
        if (!input || input.type === 'hidden' || input.disabled || input.style.display === 'none') {
            return null;
        }
        var hiddenParent = input.closest('[style*="display: none"], [style*="display:none"], .d-none');
        if (hiddenParent) {
            return null;
        }

        var label = getFieldLabel(input);
        var val = (input.value || '').trim();
        var isRequired = isFieldRequired(input);
        var inputName = (input.name || '').toLowerCase();
        var inputType = (input.type || '').toLowerCase();

        if (isRequired && (!val || val.length === 0)) {
            if (input.tagName.toLowerCase() === 'select') {
                return 'Select a ' + label.replace(/--.*--/g, '').trim() + '.';
            }
            return label + ' is required.';
        }

        if (!val) {
            return null;
        }

        if (inputType === 'email' || inputName.includes('email')) {
            var emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
            if (!emailPattern.test(val)) {
                return 'Enter a valid email address.';
            }
        }

        if (inputType === 'tel' || inputName.includes('phone')) {
            var phonePattern = /^\+?[0-9\s\-()]{7,20}$/;
            if (!phonePattern.test(val)) {
                return 'Enter a valid phone number.';
            }
        }

        if (inputName === 'code' || inputName.endsWith('_code')) {
            if (/^[0-9]+$/.test(val)) {
                return label + ' must contain at least one letter and cannot be numbers-only.';
            }
            if (!/[a-zA-Z]/.test(val)) {
                return label + ' must contain at least one letter.';
            }
            if (!/^[a-zA-Z0-9_\-\s\.\/]+$/.test(val)) {
                return label + ' contains invalid characters. Use letters, numbers, hyphens, dots, and underscores.';
            }
        }

        if (inputName === 'name' || inputName === 'city' || inputName.endsWith('_name')) {
            if (/^[0-9]+$/.test(val)) {
                return label + ' must contain letters and cannot be numbers-only.';
            }
            if (!/[a-zA-Z]/.test(val)) {
                return label + ' must contain letters.';
            }
        }

        if (inputName === 'year') {
            var yearInt = parseInt(val, 10);
            var currentYear = new Date().getFullYear();
            if (isNaN(yearInt) || yearInt < 2000 || yearInt > 2100) {
                return 'Please enter a valid 4-digit year between 2000 and 2100.';
            }
            if (yearInt < currentYear) {
                return 'Fiscal year cannot be earlier than the current year (' + currentYear + ').';
            }
        }

        if (inputName === 'period_number') {
            var periodInt = parseInt(val, 10);
            if (isNaN(periodInt) || periodInt < 1 || periodInt > 52) {
                return 'Period number must be between 1 and 52.';
            }
        }

        if (inputName === 'allocated_amount') {
            var amt = parseFloat(val);
            if (isNaN(amt) || amt <= 0) {
                return 'Allocated Amount must be greater than zero.';
            }
            if (amt >= 1000000000000) {
                return 'Allocated amount is too large. Use at most 12 digits before the decimal.';
            }
        }
        if (inputName === 'min_amount') {
            var minAmt = parseFloat(val);
            if (isNaN(minAmt) || minAmt < 0) {
                return 'Minimum amount cannot be negative.';
            }
        }

        if (inputName === 'price_tolerance_pct' || inputName === 'qty_tolerance_pct') {
            if (!/^\d+$/.test(val)) {
                return label + ' must be a whole number between 0 and 100.';
            }
            var pct = parseInt(val, 10);
            if (pct < 0 || pct > 100) {
                return label + ' must be between 0 and 100.';
            }
        }

        if (inputName === 'end_date') {
            var form = input.closest('form');
            if (form) {
                var startInput = form.querySelector('[name="start_date"]');
                if (startInput && startInput.value) {
                    if (val < startInput.value) {
                        return 'End date must be on or after the start date.';
                    }
                }
            }
        }

        if (inputName === 'max_amount') {
            var formP = input.closest('form');
            if (formP) {
                var minInput = formP.querySelector('[name="min_amount"]');
                if (minInput && minInput.value) {
                    var minVal = parseFloat(minInput.value);
                    var maxVal = parseFloat(val);
                    if (!isNaN(minVal) && !isNaN(maxVal) && maxVal < minVal) {
                        return 'Maximum amount cannot be less than minimum amount.';
                    }
                }
            }
        }

        return null;
    }

    function initForm(form) {
        if (!form || form.dataset.psValidatorInitialized === 'true') return;
        form.dataset.psValidatorInitialized = 'true';

        var fields = form.querySelectorAll('input, select, textarea');
        fields.forEach(function (field) {
            if (field.type === 'hidden' || field.type === 'submit' || field.type === 'button') return;

            field.addEventListener('blur', function () {
                var err = validateField(field);
                if (err) {
                    showError(field, err);
                }
            });

            field.addEventListener('input', function () {
                if (field.classList.contains('is-invalid')) {
                    var err = validateField(field);
                    if (!err) {
                        clearError(field);
                    }
                }
            });

            field.addEventListener('change', function () {
                if (field.classList.contains('is-invalid')) {
                    var err = validateField(field);
                    if (!err) {
                        clearError(field);
                    }
                }
            });
        });

        form.addEventListener('submit', function (e) {
            var submitter = e.submitter || document.activeElement;
            if (submitter && submitter.getAttribute('name') === 'action') {
                var actionVal = (submitter.getAttribute('value') || '').toUpperCase();
                if (actionVal === 'REJECT' || actionVal === 'REJECTED') {
                    var commentsField = form.querySelector('[name="comments"]');
                    if (commentsField && !commentsField.value.trim()) {
                        e.preventDefault();
                        e.stopImmediatePropagation();
                        showError(commentsField, 'Reason for rejection is required.');
                        commentsField.focus();
                        return false;
                    }
                }
            }

            var hasError = false;
            var firstInvalid = null;

            fields.forEach(function (field) {
                if (field.type === 'hidden' || field.type === 'submit' || field.type === 'button') return;
                var err = validateField(field);
                if (err) {
                    showError(field, err);
                    if (!firstInvalid) {
                        firstInvalid = field;
                    }
                    hasError = true;
                }
            });

            if (hasError) {
                e.preventDefault();
                e.stopImmediatePropagation();
                if (firstInvalid) {
                    firstInvalid.focus();
                    if (firstInvalid.scrollIntoView) {
                        firstInvalid.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                }
                return false;
            }
        }, true);
    }

    function initAll(root) {
        var container = root || document;
        var forms = container.querySelectorAll('form');
        forms.forEach(function (form) {
            initForm(form);
        });

        var invalidFields = container.querySelectorAll('.is-invalid, input[aria-invalid="true"]');
        invalidFields.forEach(function (field) {
            field.addEventListener('input', function () {
                var err = validateField(field);
                if (!err) {
                    clearError(field);
                }
            });
            field.addEventListener('change', function () {
                var err = validateField(field);
                if (!err) {
                    clearError(field);
                }
            });
        });
    }

    window.PSFormValidator = {
        init: initForm,
        initAll: initAll,
        showError: showError,
        clearError: clearError,
        validateField: validateField,
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            initAll(document.body);
        });
    } else {
        initAll(document.body);
    }

    document.addEventListener('htmx:afterSwap', function (e) {
        initAll(e.detail.target || document.body);
    });
    document.addEventListener('htmx:load', function (e) {
        initAll(e.detail.elt || document.body);
    });
})();
