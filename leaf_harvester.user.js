// ==UserScript==
// @name         Leaf Harvester
// @namespace    https://github.com/chur/leafspy
// @version      0.2.0
// @description  Silently captures TradeMe page data as you browse and POSTs to a local Python receiver.
// @match        *://www.trademe.co.nz/*
// @match        *://trademe.co.nz/*
// @run-at       document-start
// @grant        GM_xmlhttpRequest
// @grant        unsafeWindow
// @connect      localhost
// @connect      127.0.0.1
// ==/UserScript==

// ----------------------------------------------------------------------------
// Firefox + Tampermonkey: patches the page's window.fetch / XHR using
// unsafeWindow + exportFunction, which works under strict CSP (unlike the
// inline-<script> trick that Chrome handles but Firefox blocks).
// ----------------------------------------------------------------------------

console.log('[Leafspy] userscript loaded at', new Date().toISOString(), 'url:', location.href);

(function () {
    'use strict';

    const RECEIVER_URL = 'http://localhost:8765/capture';
    let captureCount = 0;

    // --------------------------------------------------------------------
    // 1. Patch window.fetch
    // --------------------------------------------------------------------
    try {
        const originalFetch = unsafeWindow.fetch;
        if (typeof originalFetch !== 'function') {
            console.warn('[Leafspy] unsafeWindow.fetch missing — cannot patch');
        } else {
            const wrappedFetch = exportFunction(function (input, init) {
                const promise = originalFetch.call(this, input, init);
                promise.then(response => {
                    try {
                        const url = (typeof input === 'string') ? input : (input && input.url) || '';
                        const ct = response.headers.get('content-type') || '';
                        if (ct.indexOf('application/json') !== -1 && response.ok) {
                            response.clone().json().then(data => {
                                send({ source: 'fetch', request_url: url, payload: data });
                            }).catch(() => {});
                        }
                    } catch (e) { /* swallow */ }
                }).catch(() => {});
                return promise;
            }, unsafeWindow);
            unsafeWindow.fetch = wrappedFetch;
            console.log('[Leafspy] window.fetch patched');
        }
    } catch (e) {
        console.error('[Leafspy] failed to patch fetch:', e);
    }

    // --------------------------------------------------------------------
    // 2. Patch XMLHttpRequest.prototype.open + send
    // --------------------------------------------------------------------
    try {
        const xhrProto = unsafeWindow.XMLHttpRequest.prototype;
        const originalOpen = xhrProto.open;
        const originalSend = xhrProto.send;

        const wrappedOpen = exportFunction(function (method, url) {
            this.__leafspy_url = url;
            return originalOpen.apply(this, arguments);
        }, unsafeWindow);

        const wrappedSend = exportFunction(function () {
            this.addEventListener('load', exportFunction(function () {
                try {
                    const ct = this.getResponseHeader('content-type') || '';
                    if (ct.indexOf('application/json') !== -1 && this.status >= 200 && this.status < 300) {
                        const data = JSON.parse(this.responseText);
                        send({ source: 'xhr', request_url: this.__leafspy_url, payload: data });
                    }
                } catch (e) { /* swallow */ }
            }, unsafeWindow));
            return originalSend.apply(this, arguments);
        }, unsafeWindow);

        xhrProto.open = wrappedOpen;
        xhrProto.send = wrappedSend;
        console.log('[Leafspy] XMLHttpRequest patched');
    } catch (e) {
        console.error('[Leafspy] failed to patch XHR:', e);
    }

    // --------------------------------------------------------------------
    // 3. Harvest __NEXT_DATA__ on document-ready (if present)
    // --------------------------------------------------------------------
    function harvestNextData() {
        try {
            const el = document.getElementById('__NEXT_DATA__');
            if (el) {
                const data = JSON.parse(el.textContent);
                send({ source: 'next_data', request_url: null, payload: data });
                console.log('[Leafspy] __NEXT_DATA__ harvested');
                return;
            }
            // TradeMe may use a differently-named hydration blob (e.g., #frend-state)
            const fallback = document.getElementById('frend-state');
            if (fallback) {
                try {
                    const data = JSON.parse(fallback.textContent);
                    send({ source: 'frend_state', request_url: null, payload: data });
                    console.log('[Leafspy] frend-state harvested');
                } catch (e) { /* not JSON */ }
            }
        } catch (e) { console.warn('[Leafspy] harvest error:', e); }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', harvestNextData);
    } else {
        harvestNextData();
    }

    // --------------------------------------------------------------------
    // 4. Send capture to the local receiver
    // --------------------------------------------------------------------
    function send(payload) {
        const body = Object.assign({}, payload, {
            page_url: location.href,
            captured_at: Date.now() / 1000,
        });
        GM_xmlhttpRequest({
            method: 'POST',
            url: RECEIVER_URL,
            headers: { 'Content-Type': 'application/json' },
            data: JSON.stringify(body),
            timeout: 5000,
            onload: function (res) {
                if (res.status === 200) {
                    captureCount += 1;
                    updateBadge('ok');
                } else {
                    console.warn('[Leafspy] receiver returned', res.status);
                    updateBadge('err');
                }
            },
            onerror: function (e) {
                console.warn('[Leafspy] receiver network error:', e);
                updateBadge('off');
            },
            ontimeout: function () {
                console.warn('[Leafspy] receiver timeout');
                updateBadge('off');
            }
        });
    }

    // --------------------------------------------------------------------
    // 5. Floating status badge
    // --------------------------------------------------------------------
    let badge = null;
    function ensureBadge() {
        if (badge) return;
        if (!document.body) {
            setTimeout(ensureBadge, 50);
            return;
        }
        badge = document.createElement('div');
        badge.id = 'leafspy-badge';
        badge.style.cssText = [
            'position:fixed', 'bottom:12px', 'right:12px', 'z-index:2147483647',
            'background:rgba(0,0,0,0.85)', 'color:#0f0',
            'font:12px/1.3 -apple-system, monospace',
            'padding:6px 9px', 'border-radius:6px', 'pointer-events:none',
            'box-shadow:0 2px 8px rgba(0,0,0,0.3)',
            'min-width:80px', 'text-align:center'
        ].join(';');
        badge.textContent = 'Leafspy: idle';
        document.body.appendChild(badge);
        console.log('[Leafspy] badge attached');
    }
    function updateBadge(state) {
        ensureBadge();
        if (!badge) return;
        const colors = { ok: '#0f0', err: '#fa0', off: '#f44' };
        const labels = { ok: 'Leafspy', err: 'Leafspy (err)', off: 'Leafspy (offline)' };
        badge.style.color = colors[state] || '#0f0';
        badge.textContent = `${labels[state] || 'Leafspy'}: ${captureCount}`;
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', ensureBadge);
    } else {
        ensureBadge();
    }

    // Probe receiver at startup so the badge reflects its state even with zero captures.
    GM_xmlhttpRequest({
        method: 'GET',
        url: 'http://localhost:8765/health',
        timeout: 2000,
        onload: function (res) {
            if (res.status === 200) {
                updateBadge('ok');
                console.log('[Leafspy] receiver health OK');
            } else {
                updateBadge('err');
            }
        },
        onerror: function () { updateBadge('off'); },
        ontimeout: function () { updateBadge('off'); }
    });
})();
