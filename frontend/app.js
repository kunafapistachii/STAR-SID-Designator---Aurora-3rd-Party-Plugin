/**
 * STAR/SID Designator — Frontend Application
 *
 * Connects to the bridge server via WebSocket, receives real-time traffic
 * updates, renders aircraft cards in split panels (Departures/Arrivals),
 * and sends assign/unassign/set_path commands back to the server.
 */

(() => {
    'use strict';

    // ─── State ─────────────────────────────────────────────────────────

    let ws = null;
    let reconnectTimer = null;
    let trafficState = {};
    let runwayConfig = {};
    let isDemo = false;
    let appStatus = 'ready'; // 'ready' or 'needs_setup'

    // DOM references
    const depList = document.getElementById('departures-list');
    const arrList = document.getElementById('arrivals-list');
    const depEmpty = document.getElementById('dep-empty');
    const arrEmpty = document.getElementById('arr-empty');
    const depSearch = document.getElementById('dep-search');
    const arrSearch = document.getElementById('arr-search');
    const depCount = document.getElementById('dep-count');
    const arrCount = document.getElementById('arr-count');
    const statusEl = document.getElementById('connection-status');
    const statusDot = statusEl.querySelector('.status-dot');
    const statusText = statusEl.querySelector('.status-text');
    const modeBadge = document.getElementById('mode-badge');
    const runwayInfo = document.getElementById('runway-info');
    const toastContainer = document.getElementById('toast-container');

    // Overlay DOM references
    const setupOverlay = document.getElementById('setup-overlay');
    const detectionBox = document.getElementById('detection-box');
    const detectedPathText = document.getElementById('detected-path-text');
    const useDetectedBtn = document.getElementById('use-detected-btn');
    const manualPathInput = document.getElementById('manual-path-input');
    const savePathBtn = document.getElementById('save-path-btn');
    const serverConnectionOverlay = document.getElementById('server-connection-overlay');

    // ─── WebSocket Connection ──────────────────────────────────────────

    function connect() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${location.host}/ws`;

        setConnectionStatus('connecting');
        serverConnectionOverlay.classList.remove('hidden'); // Show reconnect spinner until open
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            serverConnectionOverlay.classList.add('hidden');
            showToast('Connected to Designator server', 'success');
            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
        };

        ws.onclose = () => {
            setConnectionStatus('offline'); // Falls back to offline status
            serverConnectionOverlay.classList.remove('hidden');
            scheduleReconnect();
        };

        ws.onerror = () => {
            setConnectionStatus('offline');
            serverConnectionOverlay.classList.remove('hidden');
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleMessage(data);
            } catch (e) {
                console.error('Failed to parse message:', e);
            }
        };
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connect();
        }, 3000);
    }

    function setConnectionStatus(status, label = '') {
        // Status classes: connected (green), offline (red), demo (amber), connecting (amber)
        statusEl.className = `status-indicator ${status}`;
        statusText.textContent = label || status.toUpperCase();
    }

    // ─── Message Handling ──────────────────────────────────────────────

    function handleMessage(data) {
        switch (data.type) {
            case 'traffic_update':
                trafficState = data.traffic || {};
                runwayConfig = data.runway_config || {};
                isDemo = data.demo_mode || false;
                appStatus = data.status || 'ready';
                
                // Update dynamic GUI setups
                updateSetupOverlay(data.status, data.detected_path);
                
                // Update true Aurora connection indicators
                updateConnectionIndicator(data.aurora_connected, isDemo);
                
                updateModeBadge();
                updateRunwayInfo();
                
                if (appStatus === 'ready') {
                    renderTraffic();
                }
                break;

            case 'assign_result':
                handleAssignResult(data);
                break;

            case 'unassign_result':
                handleUnassignResult(data);
                break;

            case 'set_path_result':
                handleSetPathResult(data);
                break;
        }
    }

    function updateConnectionIndicator(auroraConnected, demoMode) {
        if (demoMode) {
            setConnectionStatus('demo', 'AURORA: DEMO MODE');
        } else if (auroraConnected) {
            setConnectionStatus('connected', 'AURORA: ONLINE');
        } else {
            setConnectionStatus('offline', 'AURORA: OFFLINE');
        }
    }

    function updateSetupOverlay(status, detectedPath) {
        if (status === 'needs_setup') {
            setupOverlay.classList.remove('hidden');
            if (detectedPath) {
                detectedPathText.textContent = detectedPath;
                detectionBox.classList.remove('hidden');
            } else {
                detectionBox.classList.add('hidden');
            }
        } else {
            setupOverlay.classList.add('hidden');
        }
    }

    function handleSetPathResult(data) {
        if (data.success) {
            const airports = data.airport_count || 0;
            const sids = data.sid_count || 0;
            const stars = data.star_count || 0;
            showToast(`✓ Loaded ${airports} airports (${sids} SIDs, ${stars} STARs) successfully!`, 'success');
            setupOverlay.classList.add('hidden');
        } else {
            showToast(`✗ Configuration failed: ${data.error}`, 'error');
        }
    }


    function handleAssignResult(data) {
        const card = document.querySelector(
            `.aircraft-card[data-callsign="${data.callsign}"]`
        );
        if (card) {
            card.classList.remove('assigning');
        }

        if (data.success) {
            showToast(`✓ ${data.procedure} → ${data.callsign}`, 'success');
        } else {
            showToast(
                `✗ Failed: ${data.callsign} — ${data.error || 'Unknown error'}`,
                'error'
            );
        }
    }

    function handleUnassignResult(data) {
        if (data.success) {
            showToast(`Cleared ${data.callsign}`, 'info');
        }
    }

    // ─── Rendering ─────────────────────────────────────────────────────

    function updateModeBadge() {
        modeBadge.textContent = isDemo ? 'DEMO' : 'LIVE';
        modeBadge.className = isDemo ? 'badge badge-demo' : 'badge badge-live';
    }

    function updateRunwayInfo() {
        runwayInfo.innerHTML = '';
        for (const [icao, config] of Object.entries(runwayConfig)) {
            const depRwys = (config.dep_rwys || []).join('/');
            const arrRwys = (config.arr_rwys || []).join('/');

            const item = document.createElement('div');
            item.className = 'rwy-item';
            item.innerHTML = `
                <span class="rwy-icao">${icao}</span>
                ${depRwys ? `<span class="rwy-dep">D:${depRwys}</span>` : ''}
                ${arrRwys ? `<span class="rwy-arr">A:${arrRwys}</span>` : ''}
            `;
            runwayInfo.appendChild(item);
        }
    }

    function renderTraffic() {
        const depFilter = depSearch.value.toUpperCase().trim();
        const arrFilter = arrSearch.value.toUpperCase().trim();

        const departures = [];
        const arrivals = [];

        for (const [callsign, data] of Object.entries(trafficState)) {
            if (data.type === 'departure') {
                if (!depFilter || callsign.includes(depFilter)) {
                    departures.push(data);
                }
            } else if (data.type === 'arrival') {
                if (!arrFilter || callsign.includes(arrFilter)) {
                    arrivals.push(data);
                }
            }
        }

        // Sort by airport, then callsign
        departures.sort((a, b) => (a.airport + a.callsign).localeCompare(b.airport + b.callsign));
        arrivals.sort((a, b) => (a.airport + a.callsign).localeCompare(b.airport + b.callsign));

        // Update counts
        depCount.textContent = `${departures.length} DEP`;
        arrCount.textContent = `${arrivals.length} ARR`;

        // Render panels
        renderPanel(depList, departures, depEmpty, 'departure');
        renderPanel(arrList, arrivals, arrEmpty, 'arrival');
    }

    function renderPanel(container, aircraft, emptyEl, type) {
        // Group by airport
        const groups = {};
        for (const ac of aircraft) {
            const key = ac.airport || 'UNKNOWN';
            if (!groups[key]) groups[key] = [];
            groups[key].push(ac);
        }

        // Track existing cards to avoid unnecessary re-renders
        const existingCards = new Map();
        container.querySelectorAll('.aircraft-card').forEach((card) => {
            existingCards.set(card.dataset.callsign, card);
        });

        // Clear and rebuild
        container.innerHTML = '';

        if (aircraft.length === 0) {
            container.appendChild(emptyEl);
            return;
        }

        for (const [airport, acList] of Object.entries(groups)) {
            // Airport group header
            const groupEl = document.createElement('div');
            groupEl.className = 'airport-group';

            const rwyInfo = type === 'departure'
                ? runwayConfig[airport]?.dep_rwys?.join('/') || ''
                : runwayConfig[airport]?.arr_rwys?.join('/') || '';

            groupEl.innerHTML = `
                <div class="airport-group-header">
                    ${airport}
                    ${rwyInfo ? `<span class="rwy-tag">RWY ${rwyInfo}</span>` : ''}
                </div>
            `;

            for (const ac of acList) {
                const cardEl = createCard(ac, type);
                // Skip animation if card existed before
                if (existingCards.has(ac.callsign)) {
                    cardEl.style.animation = 'none';
                }
                groupEl.appendChild(cardEl);
            }

            container.appendChild(groupEl);
        }
    }

    function createCard(ac, type) {
        const card = document.createElement('div');
        card.className = `aircraft-card ${type}-card`;
        if (ac.assigned) card.classList.add('assigned');
        card.dataset.callsign = ac.callsign;

        // Format altitude
        const altText = formatAltitude(ac.altitude);

        // Format airports
        const depCode = ac.departure || '????';
        const arrCode = ac.arrival || '????';

        // Build suggestion options
        const suggestions = ac.suggestions || [];
        const allProcs = ac.all_procedures || [];
        const bestMatch = suggestions.length > 0 && suggestions[0].core_match
            ? suggestions[0].name
            : null;

        card.innerHTML = `
            <div class="card-row-1">
                <span class="callsign">${escapeHtml(ac.callsign)}</span>
                <div class="card-meta">
                    <span class="altitude">${altText}</span>
                    <span class="squawk">
                        <span class="squawk-dot"></span>
                        ${escapeHtml(ac.squawk || '----')}
                    </span>
                </div>
            </div>
            <div class="card-row-2">
                <span class="airports">
                    <span class="dep-code">${escapeHtml(depCode)}</span>
                    <span class="arrow">→</span>
                    <span class="arr-code">${escapeHtml(arrCode)}</span>
                </span>
                <span class="runway-tag">RWY ${escapeHtml(ac.runway || '--')}</span>
            </div>
            <div class="card-row-3">${escapeHtml(ac.route || 'No route filed')}</div>
            <div class="card-row-4">
                ${ac.assigned
                    ? renderAssignedState(ac)
                    : renderSelectState(ac, suggestions, allProcs, bestMatch)
                }
            </div>
        `;

        // Attach event listeners
        if (ac.assigned) {
            const unassignBtn = card.querySelector('.unassign-btn');
            if (unassignBtn) {
                unassignBtn.addEventListener('click', () => {
                    sendUnassign(ac.callsign);
                });
            }
        } else {
            const assignBtn = card.querySelector('.assign-btn');
            const select = card.querySelector('.procedure-select');
            if (assignBtn && select) {
                assignBtn.addEventListener('click', () => {
                    const procedure = select.value;
                    if (!procedure) {
                        showToast('Select a procedure first', 'error');
                        return;
                    }
                    sendAssign(ac.callsign, procedure);
                    card.classList.add('assigning');
                    assignBtn.classList.add('assigning');
                    assignBtn.textContent = 'PUSHING...';
                    assignBtn.disabled = true;
                });
            }
        }

        return card;
    }

    function renderAssignedState(ac) {
        return `
            <div class="assigned-state">
                <span class="assigned-label">
                    <span class="assigned-check">✓</span>
                    ${escapeHtml(ac.assigned)}
                </span>
                <button class="unassign-btn">CLEAR</button>
            </div>
        `;
    }

    function renderSelectState(ac, suggestions, allProcs, bestMatch) {
        // Build option list: suggested first, then remaining
        const suggestedNames = new Set(suggestions.map((s) => s.name));
        const remaining = allProcs.filter((name) => !suggestedNames.has(name));

        let options = '<option value="" disabled selected>Select procedure...</option>';

        // Suggested options (with star marker)
        for (const s of suggestions) {
            const marker = s.core_match ? '★ ' : '● ';
            options += `<option value="${escapeHtml(s.name)}" class="suggested">`;
            options += `${marker}${escapeHtml(s.name)} (${s.overlap} fix${s.overlap !== 1 ? 'es' : ''})`;
            options += `</option>`;
        }

        // Separator if both groups exist
        if (suggestions.length > 0 && remaining.length > 0) {
            options += '<option disabled>──────────────</option>';
        }

        // Remaining procedures
        for (const name of remaining) {
            options += `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`;
        }

        // Pre-select best match if available
        let selectHtml = `<select class="procedure-select">`;
        if (bestMatch) {
            selectHtml = `<select class="procedure-select" data-best="${escapeHtml(bestMatch)}">`;
            // Rebuild with best match selected
            options = '<option value="" disabled>Select procedure...</option>';
            for (const s of suggestions) {
                const marker = s.core_match ? '★ ' : '● ';
                const sel = s.name === bestMatch ? 'selected' : '';
                options += `<option value="${escapeHtml(s.name)}" class="suggested" ${sel}>`;
                options += `${marker}${escapeHtml(s.name)} (${s.overlap} fix${s.overlap !== 1 ? 'es' : ''})`;
                options += `</option>`;
            }
            if (suggestions.length > 0 && remaining.length > 0) {
                options += '<option disabled>──────────────</option>';
            }
            for (const name of remaining) {
                options += `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`;
            }
        }

        return `
            ${selectHtml}${options}</select>
            <button class="assign-btn">ASSIGN</button>
        `;
    }

    // ─── Commands ──────────────────────────────────────────────────────

    function sendAssign(callsign, procedure) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'assign',
                callsign: callsign,
                procedure: procedure,
            }));
        } else {
            showToast('Not connected to server', 'error');
        }
    }

    function sendUnassign(callsign) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'unassign',
                callsign: callsign,
            }));
        }
    }

    function sendSetPath(path) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'set_path',
                path: path,
            }));
        } else {
            showToast('Not connected to server', 'error');
        }
    }

    // ─── Utilities ─────────────────────────────────────────────────────

    function formatAltitude(alt) {
        if (!alt || alt === '0') return 'GND';
        const num = parseInt(alt, 10);
        if (isNaN(num)) return alt;
        if (num >= 10000) {
            return `FL${Math.round(num / 100)}`;
        }
        return `${num.toLocaleString()} ft`;
    }

    // Basic HTML escaping
    function escapeHtml(str) {
        if (!str) return '';
        return str
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;
        toastContainer.appendChild(toast);

        // Auto-remove after 4 seconds
        setTimeout(() => {
            toast.style.animation = 'toastExit 0.3s ease-in forwards';
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    }

    // ─── Setup Modal Bindings ──────────────────────────────────────────

    useDetectedBtn.addEventListener('click', () => {
        const detectedPath = detectedPathText.textContent.trim();
        if (detectedPath) {
            sendSetPath(detectedPath);
        }
    });

    savePathBtn.addEventListener('click', () => {
        const manualPath = manualPathInput.value.trim();
        if (!manualPath) {
            showToast('Please enter a folder path first', 'error');
            return;
        }
        sendSetPath(manualPath);
    });

    // ─── Search Handlers ───────────────────────────────────────────────

    depSearch.addEventListener('input', renderTraffic);
    arrSearch.addEventListener('input', renderTraffic);

    // ─── Initialize ────────────────────────────────────────────────────

    connect();
})();
