/**
 * ProcureSphere 360 - Global Dashboard Inactivity Session Timeout
 *
 * Automatically signs out authenticated users after 5 minutes (300s) of
 * inactivity across all role dashboards and Django Admin.
 * Passive background HTMX polling (e.g. notification feed every 60s) does NOT
 * count as user activity and never resets or extends the idle timer.
 */
(function () {
    "use strict";

    if (window.__PS360_SESSION_TIMEOUT_INITIALIZED__) {
        return;
    }

    var cfg = window.PS360_SESSION_CONFIG || {};
    var bodyAuthenticated =
        document.body &&
        document.body.dataset &&
        document.body.dataset.userAuthenticated === "true";

    if (!window.PS360_SESSION_CONFIG && !bodyAuthenticated) {
        return;
    }

    window.__PS360_SESSION_TIMEOUT_INITIALIZED__ = true;

    var idleTimeoutSeconds = Number(cfg.idleTimeoutSeconds);
    if (!Number.isFinite(idleTimeoutSeconds) || idleTimeoutSeconds <= 0) {
        idleTimeoutSeconds = 300;
    }
    var idleTimeoutMs = idleTimeoutSeconds * 1000;

    var warningSeconds = Number(cfg.warningSeconds);
    if (!Number.isFinite(warningSeconds) || warningSeconds <= 0) {
        warningSeconds = 30;
    }
    var warningMs = Math.min(warningSeconds * 1000, Math.floor(idleTimeoutMs / 2));

    var keepAliveIntervalMs = Math.min(60000, Math.max(15000, Math.floor(idleTimeoutMs / 4)));
    var keepAliveUrl = cfg.keepAliveUrl || "/api/v1/auth/session-keepalive/";
    var logoutUrl = cfg.logoutUrl || "/logout/?reason=inactivity";
    var loginUrl = cfg.loginUrl || "/login/?session_expired=1";

    var STORAGE_ACTIVITY_KEY = "ps360_idle_last_activity_ms";
    var STORAGE_EXPIRED_KEY = "ps360_idle_expired_at_ms";

    var pageLoadedAt = Date.now();
    var lastActivityAt = pageLoadedAt;
    var lastKeepAliveAt = pageLoadedAt;
    var lastStorageWriteAt = 0;
    var keepAliveInFlight = false;
    var isLoggingOut = false;
    var lastMouseX = null;
    var lastMouseY = null;
    var lastMouseCheckAt = 0;
    var bannerEl = null;
    var bannerCountdownEl = null;
    var timerId = null;

    function safeStorageGet(key) {
        try {
            return window.localStorage ? window.localStorage.getItem(key) : null;
        } catch (err) {
            return null;
        }
    }

    function safeStorageSet(key, value) {
        try {
            if (window.localStorage) {
                window.localStorage.setItem(key, String(value));
            }
        } catch (err) {
            // Ignore private mode / quota errors
        }
    }

    function safeStorageRemove(key) {
        try {
            if (window.localStorage) {
                window.localStorage.removeItem(key);
            }
        } catch (err) {
            // Ignore storage errors
        }
    }

    // Clear stale timeout markers from previous sessions and record fresh login/page load activity.
    safeStorageRemove(STORAGE_EXPIRED_KEY);
    safeStorageSet(STORAGE_ACTIVITY_KEY, String(lastActivityAt));

    function getCsrfToken() {
        var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input && input.value) {
            return input.value;
        }
        var cookies = document.cookie ? document.cookie.split(";") : [];
        for (var i = 0; i < cookies.length; i += 1) {
            var part = cookies[i].trim();
            if (part.indexOf("csrftoken=") === 0) {
                return decodeURIComponent(part.substring("csrftoken=".length));
            }
        }
        return "";
    }

    function removeBanner() {
        if (bannerEl && bannerEl.parentNode) {
            bannerEl.parentNode.removeChild(bannerEl);
        }
        bannerEl = null;
        bannerCountdownEl = null;
    }

    function showOrUpdateBanner(remainingSec) {
        if (!document.body) {
            return;
        }
        if (!bannerEl) {
            bannerEl = document.createElement("div");
            bannerEl.id = "ps360-inactivity-warning";
            bannerEl.setAttribute("role", "alertdialog");
            bannerEl.setAttribute("aria-live", "assertive");
            bannerEl.style.cssText = [
                "position:fixed",
                "bottom:20px",
                "right:20px",
                "z-index:2147483000",
                "background:#1e293b",
                "color:#f8fafc",
                "border:1px solid rgba(251,191,36,0.6)",
                "border-left:4px solid #f59e0b",
                "border-radius:10px",
                "padding:14px 16px",
                "box-shadow:0 12px 32px rgba(15,23,42,0.35)",
                "max-width:340px",
                "font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif",
                "font-size:13px",
                "line-height:1.45"
            ].join(";");

            var title = document.createElement("div");
            title.style.cssText = "font-weight:700;color:#fbbf24;margin-bottom:4px;";
            title.textContent = "Inactivity Auto-Signout";

            var bodyText = document.createElement("div");
            bodyText.style.cssText = "margin-bottom:10px;color:#e2e8f0;";
            bannerCountdownEl = document.createElement("span");
            bodyText.appendChild(document.createTextNode("No activity detected. Signing out in "));
            bodyText.appendChild(bannerCountdownEl);
            bodyText.appendChild(document.createTextNode("."));

            var btnRow = document.createElement("div");
            btnRow.style.cssText = "display:flex;gap:8px;justify-content:flex-end;";

            var stayBtn = document.createElement("button");
            stayBtn.type = "button";
            stayBtn.textContent = "Stay Signed In";
            stayBtn.style.cssText = [
                "background:#f59e0b",
                "color:#0f172a",
                "border:none",
                "border-radius:6px",
                "padding:6px 12px",
                "font-size:12px",
                "font-weight:700",
                "cursor:pointer"
            ].join(";");
            stayBtn.addEventListener("click", function () {
                recordUserActivity(true);
            });

            btnRow.appendChild(stayBtn);
            bannerEl.appendChild(title);
            bannerEl.appendChild(bodyText);
            bannerEl.appendChild(btnRow);
            document.body.appendChild(bannerEl);
        }

        if (bannerCountdownEl) {
            bannerCountdownEl.textContent = remainingSec + "s";
        }
    }

    function getEffectiveLastActivity() {
        var sharedRaw = safeStorageGet(STORAGE_ACTIVITY_KEY);
        if (sharedRaw) {
            var sharedTs = Number(sharedRaw);
            if (Number.isFinite(sharedTs) && sharedTs > lastActivityAt) {
                lastActivityAt = sharedTs;
            }
        }
        return lastActivityAt;
    }

    function performAutoLogout(skipRemoteLogout) {
        if (isLoggingOut) {
            return;
        }
        isLoggingOut = true;
        if (timerId) {
            window.clearInterval(timerId);
            timerId = null;
        }
        removeBanner();
        safeStorageSet(STORAGE_EXPIRED_KEY, String(Date.now()));

        if (skipRemoteLogout) {
            window.location.replace(loginUrl);
            return;
        }

        var csrfToken = getCsrfToken();
        var headers = {
            "X-Requested-With": "XMLHttpRequest"
        };
        if (csrfToken) {
            headers["X-CSRFToken"] = csrfToken;
        }

        try {
            window.fetch(logoutUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: headers,
                keepalive: true
            }).catch(function () {
                // Ignore network failure and proceed directly to login screen
            }).finally(function () {
                window.location.replace(loginUrl);
            });
        } catch (err) {
            window.location.replace(loginUrl);
        }
    }

    function sendKeepAlive() {
        if (keepAliveInFlight || isLoggingOut) {
            return;
        }
        keepAliveInFlight = true;
        lastKeepAliveAt = Date.now();

        var csrfToken = getCsrfToken();
        var headers = {
            "X-Requested-With": "XMLHttpRequest",
            "X-PS-Activity-Keepalive": "1"
        };
        if (csrfToken) {
            headers["X-CSRFToken"] = csrfToken;
        }

        window.fetch(keepAliveUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: headers
        }).then(function (resp) {
            if (resp && (resp.status === 401 || resp.status === 403)) {
                performAutoLogout(true);
            }
        }).catch(function () {
            // Keep tracking client idle deadline even if a transient network glitch occurs
        }).finally(function () {
            keepAliveInFlight = false;
        });
    }

    function recordUserActivity(forceKeepAlive) {
        if (isLoggingOut) {
            return;
        }
        var now = Date.now();
        // If deadline has already passed (e.g. tab woke up from sleep after 5+ mins), expire immediately.
        if (now - getEffectiveLastActivity() >= idleTimeoutMs) {
            performAutoLogout(false);
            return;
        }

        lastActivityAt = now;
        removeBanner();

        if (now - lastStorageWriteAt >= 1000 || forceKeepAlive) {
            lastStorageWriteAt = now;
            safeStorageSet(STORAGE_ACTIVITY_KEY, String(now));
        }

        if (forceKeepAlive || (now - lastKeepAliveAt >= keepAliveIntervalMs)) {
            sendKeepAlive();
        }
    }

    function checkIdleState() {
        if (isLoggingOut) {
            return;
        }
        var expiredRaw = safeStorageGet(STORAGE_EXPIRED_KEY);
        if (expiredRaw) {
            var expiredTs = Number(expiredRaw);
            if (Number.isFinite(expiredTs) && expiredTs >= pageLoadedAt) {
                performAutoLogout(true);
                return;
            }
        }

        var now = Date.now();
        var effectiveLast = getEffectiveLastActivity();
        var elapsed = now - effectiveLast;
        var remaining = idleTimeoutMs - elapsed;

        if (remaining <= 0) {
            performAutoLogout(false);
            return;
        }

        if (remaining <= warningMs) {
            showOrUpdateBanner(Math.max(1, Math.ceil(remaining / 1000)));
        } else if (bannerEl) {
            removeBanner();
        }
    }

    function handleMouseMove(event) {
        var now = Date.now();
        if (now - lastMouseCheckAt < 1000) {
            return;
        }
        lastMouseCheckAt = now;

        var x = typeof event.clientX === "number" ? event.clientX : 0;
        var y = typeof event.clientY === "number" ? event.clientY : 0;
        if (lastMouseX === null || lastMouseY === null) {
            lastMouseX = x;
            lastMouseY = y;
            return;
        }
        if (Math.abs(x - lastMouseX) + Math.abs(y - lastMouseY) >= 4) {
            lastMouseX = x;
            lastMouseY = y;
            recordUserActivity(false);
        }
    }

    var activityEvents = [
        "pointerdown",
        "mousedown",
        "keydown",
        "touchstart",
        "wheel",
        "scroll",
        "input",
        "change"
    ];

    for (var i = 0; i < activityEvents.length; i += 1) {
        window.addEventListener(
            activityEvents[i],
            function () {
                recordUserActivity(false);
            },
            { passive: true, capture: true }
        );
    }

    window.addEventListener("mousemove", handleMouseMove, { passive: true, capture: true });

    // Real user-initiated non-passive HTMX requests refresh activity; passive poll requests never do.
    document.addEventListener("htmx:beforeRequest", function (event) {
        var detail = event && event.detail ? event.detail : null;
        var requestConfig = detail && detail.requestConfig ? detail.requestConfig : null;
        var headers = requestConfig && requestConfig.headers ? requestConfig.headers : {};
        var path = (requestConfig && requestConfig.path) || "";
        var isBackgroundPoll =
            headers["X-PS-Background-Poll"] === "true" ||
            headers["x-ps-background-poll"] === "true" ||
            path.indexOf("/notifications/htmx/feed/") !== -1 ||
            path.indexOf("/notifications/htmx/pending-actions/") !== -1;
        if (!isBackgroundPoll) {
            recordUserActivity(false);
        }
    });

    window.addEventListener("storage", function (event) {
        if (!event) {
            return;
        }
        if (event.key === STORAGE_EXPIRED_KEY && event.newValue) {
            var expiredTs = Number(event.newValue);
            if (Number.isFinite(expiredTs) && expiredTs >= pageLoadedAt) {
                performAutoLogout(true);
            }
        } else if (event.key === STORAGE_ACTIVITY_KEY && event.newValue) {
            var sharedTs = Number(event.newValue);
            if (Number.isFinite(sharedTs) && sharedTs > lastActivityAt) {
                lastActivityAt = sharedTs;
                removeBanner();
            }
        }
    });

    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) {
            checkIdleState();
        }
    });

    window.addEventListener("focus", checkIdleState);

    window.PS360SessionTimeout = {
        getIdleTimeoutSeconds: function () {
            return idleTimeoutSeconds;
        },
        getLastActivityAt: function () {
            return getEffectiveLastActivity();
        }
    };

    timerId = window.setInterval(checkIdleState, 1000);
})();
