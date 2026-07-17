/**
 * STAR/SID Designator — Frontend Application (v2 Controller Panel)
 *
 * Flow:
 *   1. Traffic List  →  select an aircraft
 *   2. SID / STAR toggle  →  choose procedure mode
 *   3. Procedure buttons  →  click to select a procedure
 *   4. ASSIGN button  →  assign selected proc to aircraft
 *      (if aircraft already has assignment, button becomes UNASSIGN)
 */

(() => {
    'use strict';

    // ─── State ─────────────────────────────────────────────────────────

    let ws = null;
    let reconnectTimer = null;
    let trafficState = {};
    let runwayConfig = {};
    let isDemo = false;
    let appStatus = 'ready';

    let selectedCallsign = null;   // currently selected aircraft
    let activeMode = 'sid';        // 'sid' | 'star'
    let selectedProcedure = null;  // currently highlighted procedure button

    // ─── DOM References ────────────────────────────────────────────────

    // Header
    const depCount          = document.getElementById('dep-count');
    const arrCount          = document.getElementById('arr-count');
    const statusEl          = document.getElementById('connection-status');
    const statusText        = statusEl.querySelector('.status-text');
    const modeBadge         = document.getElementById('mode-badge');
    const runwayInfo        = document.getElementById('runway-info');
    const toastContainer    = document.getElementById('toast-container');

    // Traffic list
    const trafficList       = document.getElementById('traffic-list');

    // Designator panel
    const assignedProcDisplay = document.getElementById('assigned-proc-display');
    const acInfoBar           = document.getElementById('ac-info-bar');
    const sidBtn              = document.getElementById('sid-btn');
    const starBtn             = document.getElementById('star-btn');
    const procGrid            = document.getElementById('proc-grid');
    const assignBtn           = document.getElementById('assign-btn');

    // Setup overlay
    const setupOverlay        = document.getElementById('setup-overlay');
    const detectionBox        = document.getElementById('detection-box');
    const detectedPathText    = document.getElementById('detected-path-text');
    const useDetectedBtn      = document.getElementById('use-detected-btn');
    const manualPathInput     = document.getElementById('manual-path-input');
    const savePathBtn         = document.getElementById('save-path-btn');
    const serverConnectionOverlay = document.getElementById('server-connection-overlay');

    // ─── WebSocket Connection ──────────────────────────────────────────

    function connect() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${location.host}/ws`;

        setConnectionStatus('connecting');
        serverConnectionOverlay.classList.remove('hidden');
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
            setConnectionStatus('offline');
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
        statusEl.className = `status-indicator ${status}`;
        statusText.textContent = label || status.toUpperCase();
    }

    // ─── Message Handling ──────────────────────────────────────────────

    function handleMessage(data) {
        switch (data.type) {
            case 'traffic_update':
                trafficState   = data.traffic       || {};
                runwayConfig   = data.runway_config  || {};
                isDemo         = data.demo_mode      || false;
                appStatus      = data.status         || 'ready';

                updateSetupOverlay(data.status, data.detected_path);
                updateConnectionIndicator(data.aurora_connected, isDemo);
                updateModeBadge();
                updateRunwayInfo();

                if (appStatus === 'ready') {
                    // If selected aircraft vanished, deselect
                    if (selectedCallsign && !trafficState[selectedCallsign]) {
                        selectedCallsign  = null;
                        selectedProcedure = null;
                    }
                    renderTrafficList();
                    renderDesignatorPanel();
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
            const sids     = data.sid_count     || 0;
            const stars    = data.star_count    || 0;
            showToast(`✓ Loaded ${airports} airports (${sids} SIDs, ${stars} STARs) successfully!`, 'success');
            setupOverlay.classList.add('hidden');
        } else {
            showToast(`✗ Configuration failed: ${data.error}`, 'error');
        }
    }

    function handleAssignResult(data) {
        // Re-enable assign button
        assignBtn.disabled = false;

        if (data.success) {
            showToast(`✓ ${data.procedure} → ${data.callsign}`, 'success');
            selectedProcedure = null; // clear selection after successful assign
        } else {
            showToast(`✗ Failed: ${data.callsign} — ${data.error || 'Unknown error'}`, 'error');
            assignBtn.textContent = 'ASSIGN';
        }
        renderDesignatorPanel();
    }

    function handleUnassignResult(data) {
        if (data.success) {
            showToast(`Cleared ${data.callsign}`, 'info');
        }
        renderDesignatorPanel();
    }

    // ─── Header Rendering ──────────────────────────────────────────────

    function updateModeBadge() {
        modeBadge.textContent = isDemo ? 'DEMO' : 'LIVE';
        modeBadge.className   = isDemo ? 'badge badge-demo' : 'badge badge-live';
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

    // ─── Traffic List Rendering ─────────────────────────────────────────

    function renderTrafficList() {
        const departures = [];
        const arrivals   = [];
        const overflys   = [];

        for (const ac of Object.values(trafficState)) {
            if (ac.type === 'departure')    departures.push(ac);
            else if (ac.type === 'arrival') arrivals.push(ac);
            else                            overflys.push(ac);
        }

        // Sort by callsign within each group
        const byCallsign = (a, b) => a.callsign.localeCompare(b.callsign);
        departures.sort(byCallsign);
        arrivals.sort(byCallsign);
        overflys.sort(byCallsign);

        // Update header counts
        depCount.textContent = `${departures.length} DEP`;
        arrCount.textContent = `${arrivals.length} ARR`;

        trafficList.innerHTML = '';

        const total = departures.length + arrivals.length + overflys.length;
        if (total === 0) {
            trafficList.innerHTML = `
                <div class="tl-empty">
                    <span class="tl-empty-icon">📡</span>
                    <span>No traffic</span>
                </div>`;
            return;
        }

        if (departures.length > 0) buildTrafficSection('DEPARTURES', departures, 'dep');
        if (arrivals.length   > 0) buildTrafficSection('ARRIVALS',   arrivals,   'arr');
        if (overflys.length   > 0) buildTrafficSection('OVERFLY',    overflys,   'ovr');
    }

    function buildTrafficSection(label, acList, dotClass) {
        const section = document.createElement('div');
        section.className = 'tl-section';

        const hdr = document.createElement('div');
        hdr.className = 'tl-section-header';
        hdr.textContent = label;
        section.appendChild(hdr);

        // Group aircraft by controlled aerodrome, sorted by ICAO then callsign
        const byAirport = {};
        for (const ac of acList) {
            const apt = ac.airport || 'UNKN';
            if (!byAirport[apt]) byAirport[apt] = [];
            byAirport[apt].push(ac);
        }
        const sortedApts = Object.keys(byAirport).sort();
        const multiAirport = sortedApts.length > 1;

        for (const apt of sortedApts) {
            // Only render the airport sub-header when there are multiple aerodromes
            if (multiAirport) {
                const aptHdr = document.createElement('div');
                aptHdr.className = 'tl-apt-header';
                aptHdr.innerHTML = `
                    <span class="tl-apt-icao">${escapeHtml(apt)}</span>
                    <span class="tl-apt-count">${byAirport[apt].length}</span>
                `;
                section.appendChild(aptHdr);
            }

            for (const ac of byAirport[apt]) {
                const isSelected = ac.callsign === selectedCallsign;

                const item = document.createElement('div');
                item.className = `tl-item${isSelected ? ' selected' : ''}${multiAirport ? ' indented' : ''}`;
                item.dataset.callsign = ac.callsign;

                item.innerHTML = `
                    <span class="tl-dot ${dotClass}"></span>
                    <span class="tl-callsign">${escapeHtml(ac.callsign)}</span>
                    ${!multiAirport
                        ? `<span class="tl-airport">${escapeHtml(apt)}</span>`
                        : ''}
                    ${ac.assigned
                        ? `<span class="tl-assigned-tag">${escapeHtml(ac.assigned)}</span>`
                        : ''}
                `;

                item.addEventListener('click', () => selectAircraft(ac.callsign));
                section.appendChild(item);
            }
        }

        trafficList.appendChild(section);
    }

    // ─── Aircraft Selection ─────────────────────────────────────────────

    function selectAircraft(callsign) {
        selectedCallsign  = callsign;
        selectedProcedure = null;

        const ac = trafficState[callsign];
        if (ac) {
            // Auto-switch mode to match aircraft type
            if (ac.type === 'departure')    activeMode = 'sid';
            else if (ac.type === 'arrival') activeMode = 'star';
            // overfly: leave current mode unchanged
        }

        updateModeToggleUI();
        renderTrafficList();      // refresh selected highlight
        renderDesignatorPanel();
    }

    // ─── Mode Toggle ───────────────────────────────────────────────────

    function setMode(mode) {
        activeMode        = mode;
        selectedProcedure = null;
        updateModeToggleUI();
        renderProcGrid();
        updateAssignButton();
    }

    function updateModeToggleUI() {
        sidBtn.classList.toggle('active',  activeMode === 'sid');
        starBtn.classList.toggle('active', activeMode === 'star');
    }

    sidBtn.addEventListener('click',  () => setMode('sid'));
    starBtn.addEventListener('click', () => setMode('star'));

    // ─── Designator Panel Rendering ────────────────────────────────────

    function renderDesignatorPanel() {
        const ac = selectedCallsign ? trafficState[selectedCallsign] : null;
        updateAssignedProcDisplay(ac);
        renderAcInfoBar(ac);
        renderProcGrid();
        updateAssignButton(ac);
    }

    /** Shows the currently assigned procedure name (read-only display box). */
    function updateAssignedProcDisplay(ac) {
        if (ac && ac.assigned) {
            assignedProcDisplay.textContent = ac.assigned;
            assignedProcDisplay.classList.remove('empty');
            assignedProcDisplay.classList.add('has-value');
        } else {
            assignedProcDisplay.textContent = '';
            assignedProcDisplay.classList.remove('has-value');
            assignedProcDisplay.classList.add('empty');
        }
    }

    /** Renders the aircraft info bar (callsign, type badge, route, altitude, runway). */
    function renderAcInfoBar(ac) {
        if (!ac) {
            acInfoBar.innerHTML = `<span class="ac-info-placeholder">Select an aircraft from the traffic list</span>`;
            return;
        }

        let typeLabel, typeClass;
        if (ac.type === 'departure') {
            typeLabel = 'Departure'; typeClass = 'dep';
        } else if (ac.type === 'arrival') {
            typeLabel = 'Arrival'; typeClass = 'arr';
        } else {
            typeLabel = 'Overfly'; typeClass = 'ovr';
        }

        const depCode = escapeHtml(ac.departure || '????');
        const arrCode = escapeHtml(ac.arrival   || '????');
        const altText = formatAltitude(ac.altitude);

        acInfoBar.innerHTML = `
            <span class="ac-callsign">${escapeHtml(ac.callsign)}</span>
            <span class="ac-type-badge ${typeClass}">Aircraft is ${typeLabel}</span>
            <span class="ac-route-mini">
                <span class="mini-dep">${depCode}</span>
                <span class="mini-arrow">→</span>
                <span class="mini-arr">${arrCode}</span>
            </span>
            <span class="ac-alt">${altText}</span>
            ${ac.runway ? `<span class="ac-rwy">RWY ${escapeHtml(ac.runway)}</span>` : ''}
        `;
    }

    /** Renders the procedure button grid. */
    function renderProcGrid() {
        procGrid.innerHTML = '';
        procGrid.className = `proc-grid ${activeMode}-mode`;

        const ac = selectedCallsign ? trafficState[selectedCallsign] : null;

        // ── No aircraft selected
        if (!ac) {
            procGrid.innerHTML = `
                <div class="proc-message">
                    <span class="proc-message-icon">✈</span>
                    <span>Select an aircraft to view procedures</span>
                </div>`;
            return;
        }

        // ── Overfly aircraft — no procedures
        if (ac.type !== 'departure' && ac.type !== 'arrival') {
            procGrid.innerHTML = `
                <div class="proc-message">
                    <span class="proc-message-icon">✈</span>
                    <span>Aircraft is Overfly — no SID/STAR procedures available</span>
                </div>`;
            return;
        }

        // ── Wrong mode for aircraft type
        const modeMatches = (ac.type === 'departure' && activeMode === 'sid') ||
                            (ac.type === 'arrival'   && activeMode === 'star');

        if (!modeMatches) {
            const correctLabel = ac.type === 'departure' ? 'SID' : 'STAR';
            const typeLabel    = ac.type === 'departure' ? 'Departure' : 'Arrival';
            procGrid.innerHTML = `
                <div class="proc-message mismatch">
                    <span class="proc-message-icon">↔</span>
                    <span>Aircraft is ${typeLabel} — switch to <strong>${correctLabel}</strong> mode</span>
                </div>`;
            return;
        }

        // ── No procedures available
        const allProcs = ac.all_procedures || [];
        if (allProcs.length === 0) {
            procGrid.innerHTML = `
                <div class="proc-message">
                    <span class="proc-message-icon">—</span>
                    <span>No procedures found for this aircraft</span>
                </div>`;
            return;
        }

        // ── Build suggestion lookup for visual ranking
        const suggestions = ac.suggestions || [];
        const suggMap = new Map(suggestions.map(s => [s.name, s]));

        for (const procName of allProcs) {
            const sugg        = suggMap.get(procName);
            const isBestMatch = sugg?.core_match === true;
            const isSuggested = !!sugg && !isBestMatch;
            const isSelected  = procName === selectedProcedure;

            const btn = document.createElement('button');
            let cls = 'proc-btn';
            if (isBestMatch) cls += ' best-match';
            else if (isSuggested) cls += ' suggested';
            if (isSelected) cls += ' selected';
            btn.className = cls;
            btn.textContent = procName;
            btn.title = sugg
                ? `${isBestMatch ? '★ Best match' : '● Suggested'} · ${sugg.overlap} shared fix${sugg.overlap !== 1 ? 'es' : ''}`
                : procName;

            btn.addEventListener('click', () => {
                selectedProcedure = procName;
                renderProcGrid();          // refresh highlights
                updateAssignButton();
            });

            procGrid.appendChild(btn);
        }
    }

    /** Enables / disables the ASSIGN/UNASSIGN button and updates its label. */
    function updateAssignButton(ac) {
        if (!ac) ac = selectedCallsign ? trafficState[selectedCallsign] : null;

        if (!ac) {
            assignBtn.disabled    = true;
            assignBtn.textContent = 'ASSIGN';
            assignBtn.className   = 'assign-main-btn';
            return;
        }

        if (ac.assigned) {
            // Already has an assignment → offer to UNASSIGN
            assignBtn.disabled    = false;
            assignBtn.textContent = 'UNASSIGN';
            assignBtn.className   = 'assign-main-btn unassign-mode';
        } else if (selectedProcedure) {
            assignBtn.disabled    = false;
            assignBtn.textContent = 'ASSIGN';
            assignBtn.className   = 'assign-main-btn';
        } else {
            assignBtn.disabled    = true;
            assignBtn.textContent = 'ASSIGN';
            assignBtn.className   = 'assign-main-btn';
        }
    }

    // ─── Assign Button Handler ──────────────────────────────────────────

    assignBtn.addEventListener('click', () => {
        const ac = selectedCallsign ? trafficState[selectedCallsign] : null;
        if (!ac) return;

        if (ac.assigned) {
            // UNASSIGN flow
            sendUnassign(ac.callsign);
            assignBtn.disabled    = true;
            assignBtn.textContent = 'CLEARING...';
        } else if (selectedProcedure) {
            // ASSIGN flow
            sendAssign(ac.callsign, selectedProcedure);
            assignBtn.disabled    = true;
            assignBtn.textContent = 'PUSHING...';
        }
    });

    // ─── Commands ──────────────────────────────────────────────────────

    function sendAssign(callsign, procedure) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'assign', callsign, procedure }));
        } else {
            showToast('Not connected to server', 'error');
        }
    }

    function sendUnassign(callsign) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'unassign', callsign }));
        } else {
            showToast('Not connected to server', 'error');
        }
    }

    function sendSetPath(path) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'set_path', path }));
        } else {
            showToast('Not connected to server', 'error');
        }
    }

    // ─── Utilities ─────────────────────────────────────────────────────

    function formatAltitude(alt) {
        if (!alt || alt === '0') return 'GND';
        const num = parseInt(alt, 10);
        if (isNaN(num)) return alt;
        if (num >= 10000) return `FL${Math.round(num / 100)}`;
        return `${num.toLocaleString()} ft`;
    }

    function escapeHtml(str) {
        if (!str) return '';
        return str
            .replace(/&/g,  '&amp;')
            .replace(/</g,  '&lt;')
            .replace(/>/g,  '&gt;')
            .replace(/"/g,  '&quot;')
            .replace(/'/g,  '&#039;');
    }

    function showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className   = `toast ${type}`;
        toast.textContent = message;
        toastContainer.appendChild(toast);

        setTimeout(() => {
            toast.style.animation = 'toastExit 0.3s ease-in forwards';
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    }

    // ─── Setup Modal Bindings ──────────────────────────────────────────

    useDetectedBtn.addEventListener('click', () => {
        const detectedPath = detectedPathText.textContent.trim();
        if (detectedPath) sendSetPath(detectedPath);
    });

    savePathBtn.addEventListener('click', () => {
        const manualPath = manualPathInput.value.trim();
        if (!manualPath) {
            showToast('Please enter a folder path first', 'error');
            return;
        }
        sendSetPath(manualPath);
    });

    // ─── Resizable Panel ───────────────────────────────────────────────

    function initResizablePanel() {
        const divider = document.querySelector('.panel-divider');
        const panel   = document.getElementById('traffic-list-panel');

        const MIN_W     = 160;
        const MAX_W     = 520;
        const DEFAULT_W = 260;

        let dragging  = false;
        let startX    = 0;
        let startW    = 0;

        divider.addEventListener('mousedown', (e) => {
            dragging = true;
            startX   = e.clientX;
            startW   = panel.offsetWidth;
            document.body.classList.add('resizing');
            e.preventDefault();
        });

        document.addEventListener('mousemove', (e) => {
            if (!dragging) return;
            const newW = Math.min(MAX_W, Math.max(MIN_W, startW + (e.clientX - startX)));
            panel.style.width = `${newW}px`;
        });

        document.addEventListener('mouseup', () => {
            if (!dragging) return;
            dragging = false;
            document.body.classList.remove('resizing');
        });

        // Double-click resets to default
        divider.addEventListener('dblclick', () => {
            panel.style.width = `${DEFAULT_W}px`;
        });
    }

    // ─── Initialize ────────────────────────────────────────────────────

    initResizablePanel();
    connect();
})();
