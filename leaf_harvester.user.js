// ==UserScript==
// @name         Leaf Harvester
// @namespace    https://github.com/chur/leafspy
// @version      0.1.0
// @description  Silently captures TradeMe page data as you browse and POSTs to a local Python receiver.
// @match        https://www.trademe.co.nz/*
// @match        https://trademe.co.nz/*
// @run-at       document-start
// @grant        GM_xmlhttpRequest
// @connect      localhost
// @connect      127.0.0.1
// ==/UserScript==

(function () {
    'use strict';

    const RECEIVER_URL = 'http://localhost:8765/capture';
    let captureCount = 0;

    // ---- Inject fetch + XHR interceptors into the page context ----
    // Tampermonkey runs in an isolated world; we need to patch the page's
    // real window.fetch by injecting a <script> tag.
    const interceptorSrc = `
        (function () {
            const _fetch = window.fetch;
            window.fetch = async function (...args) {
                const response = await _fetch.apply(this, args);
                try {
                    const url = (typeof args[0] === 'string') ? args[0] : (args[0] && args[0].url);
                    const ct = response.headers.get('content-type') || '';
                    if (ct.indexOf('application/json') !== -1 && response.ok) {
                        response.clone().json().then(data => {
                            window.postMessage({
                                __leafspy: true,
                                source: 'fetch',
                                request_url: url,
                                payload: data
                            }, '*');
                        }).catch(() => {});
                    }
                } catch (e) { /* swallow */ }
                return response;
            };

            const _open = XMLHttpRequest.prototype.open;
            const _send = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function (method, url, ...rest) {
                this.__leafspy_url = url;
                return _open.apply(this, [method, url, ...rest]);
            };
            XMLHttpRequest.prototype.send = function (...args) {
                this.addEventListener('load', function () {
                    try {
                        const ct = this.getResponseHeader('content-type') || '';
                        if (ct.indexOf('application/json') !== -1 && this.status >= 200 && this.status < 300) {
                            const data = JSON.parse(this.responseText);
                            window.postMessage({
                                __leafspy: true,
                                source: 'xhr',
                                request_url: this.__leafspy_url,
                                payload: data
                            }, '*');
                        }
                    } catch (e) { /* swallow */ }
                });
                return _send.apply(this, args);
            };
        })();
    `;
    const s = document.createElement('script');
    s.textContent = interceptorSrc;
    (document.head || document.documentElement).prepend(s);
    s.remove(); // already executed

    // ---- Listen for postMessage from page context ----
    window.addEventListener('message', function (event) {
        if (!event.data || !event.data.__leafspy) return;
        send({
            source: event.data.source,
            request_url: event.data.request_url,
            payload: event.data.payload,
            page_url: window.location.href,
            captured_at: Date.now() / 1000
        });
    });

    // ---- Harvest __NEXT_DATA__ on document-end ----
    function harvestNextData() {
        const el = document.getElementById('__NEXT_DATA__');
        if (!el) return;
        try {
            const data = JSON.parse(el.textContent);
            send({
                source: 'next_data',
                request_url: null,
                payload: data,
                page_url: window.location.href,
                captured_at: Date.now() / 1000
            });
        } catch (e) { /* swallow */ }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', harvestNextData);
    } else {
        harvestNextData();
    }

    // ---- Send capture to receiver ----
    function send(body) {
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
                    updateBadge('err');
                }
            },
            onerror: function () { updateBadge('off'); },
            ontimeout: function () { updateBadge('off'); }
        });
    }

    // ---- Floating status badge ----
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
            'background:rgba(0,0,0,0.78)', 'color:#0f0', 'font:12px/1.3 -apple-system, monospace',
            'padding:6px 9px', 'border-radius:6px', 'pointer-events:none',
            'box-shadow:0 2px 8px rgba(0,0,0,0.3)'
        ].join(';');
        badge.textContent = 'Leafspy: 0';
        document.body.appendChild(badge);
    }
    function updateBadge(state) {
        ensureBadge();
        if (!badge) return;
        const colors = { ok: '#0f0', err: '#fa0', off: '#f44' };
        const labels = { ok: 'Leafspy', err: 'Leafspy (err)', off: 'Leafspy (offline)' };
        badge.style.color = colors[state] || '#0f0';
        badge.textContent = `${labels[state] || 'Leafspy'}: ${captureCount}`;
    }

    // Ensure the badge exists once body is available so users see the script is loaded.
    ensureBadge();
})();
