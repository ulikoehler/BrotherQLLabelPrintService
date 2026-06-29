// Brother QL Print Service — UI Logic

let currentFileId = null;
let currentPreviewData = null;
let currentPreviewPages = [];
let currentPreviewPageIdx = 0;
let settings = null;
let labels = [];
let models = [];

// --- Helpers ---

function el(id) { return document.getElementById(id); }

function getTheme() {
    return document.documentElement.getAttribute('data-theme') || 'light';
}

function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
}

el('theme-toggle').addEventListener('click', () => {
    setTheme(getTheme() === 'light' ? 'dark' : 'light');
});

function showToast(msg, type = '') {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.className = 'toast ' + type;
    toast.classList.remove('hidden');
    setTimeout(() => toast.classList.add('hidden'), 3000);
}

function fmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString();
}

// --- Tab switching ---

document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        el('tab-' + tab.dataset.tab).classList.add('active');

        if (tab.dataset.tab === 'history') loadHistory();
        if (tab.dataset.tab === 'settings') loadSettings();
    });
});

// --- Upload ---

const dropZone = el('drop-zone');
const fileInput = el('file-input');

dropZone.addEventListener('click', () => fileInput.click());

dropZone.addEventListener('dragover', e => {
    e.preventDefault();
    dropZone.classList.add('dragover');
});

dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));

dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
        uploadFile(e.dataTransfer.files[0]);
    }
});

fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) {
        uploadFile(fileInput.files[0]);
    }
});

async function uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);

    showToast('Uploading and converting...');

    try {
        const res = await fetch('/api/upload', { method: 'POST', body: formData });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Upload failed');
        }
        const data = await res.json();
        currentFileId = data.file_id;
        currentPreviewData = null;
        currentPreviewPages = [];
        currentPreviewPageIdx = 0;
        displayFileInfo(data);
        showToast('File ready', 'success');
        if (settings && settings.ui.show_preview && data.orientation.accepted) {
            el('btn-preview').click();
        }
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function displayFileInfo(data) {
    el('info-filename').textContent = data.original_filename;
    el('info-filetype').textContent = data.file_type.toUpperCase();
    el('info-pages').textContent = data.num_pages;
    el('info-dim-mm').textContent = `${data.dimensions_mm.width} × ${data.dimensions_mm.height} mm`;
    el('info-dim-px').textContent = `${data.dimensions_px.width} × ${data.dimensions_px.height} px`;

    const orient = data.orientation;
    el('info-orientation').textContent = orient.orientation || '—';
    el('info-rotation').textContent = orient.rotation ? `${orient.rotation}°` : '0°';
    el('info-orient-status').textContent = orient.accepted ? 'Auto-detected' : 'Needs input';

    const warning = el('orientation-warning');
    if (!orient.accepted) {
        warning.classList.remove('hidden');
        el('warning-text').textContent = orient.reason;
    } else {
        warning.classList.add('hidden');
    }

    el('file-info').classList.remove('hidden');
    el('preview-section').classList.add('hidden');
    el('btn-preview').disabled = false;
    el('btn-print').disabled = false;
}

// --- Preview ---

el('btn-preview').addEventListener('click', async () => {
    if (!currentFileId) return;

    const orientation = el('manual-orientation').value || null;
    const resize = el('manual-resize').checked;

    showToast('Generating preview...');

    try {
        const res = await fetch('/api/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                file_id: currentFileId,
                orientation: orientation,
                resize: resize,
            }),
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Preview failed');
        }
        const data = await res.json();
        currentPreviewData = data;
        currentPreviewPages = data.previews || [];
        currentPreviewPageIdx = 0;
        renderPreviewPage();
        el('preview-orientation-info').textContent =
            `Orientation: ${data.orientation}, Rotation: ${data.rotation}°, ${data.reason}`;
        el('preview-section').classList.remove('hidden');
        showToast('Preview ready', 'success');
    } catch (e) {
        showToast(e.message, 'error');
    }
});

function renderPreviewPage() {
    if (currentPreviewPages.length === 0) return;
    const page = currentPreviewPages[currentPreviewPageIdx];
    el('preview-img').src = page.preview_url + '?t=' + Date.now();

    const nav = el('preview-nav');
    const indicator = el('preview-page-indicator');
    const pageCount = el('preview-page-count');

    if (currentPreviewPages.length > 1) {
        nav.classList.remove('hidden');
        indicator.classList.remove('hidden');
        indicator.textContent = `(Page ${currentPreviewPageIdx + 1} of ${currentPreviewPages.length})`;
        pageCount.textContent = `${currentPreviewPageIdx + 1} / ${currentPreviewPages.length}`;
        el('btn-prev-page').disabled = currentPreviewPageIdx === 0;
        el('btn-next-page').disabled = currentPreviewPageIdx === currentPreviewPages.length - 1;
    } else {
        nav.classList.add('hidden');
        indicator.classList.add('hidden');
    }
}

el('btn-prev-page').addEventListener('click', () => {
    if (currentPreviewPageIdx > 0) {
        currentPreviewPageIdx--;
        renderPreviewPage();
    }
});

el('btn-next-page').addEventListener('click', () => {
    if (currentPreviewPageIdx < currentPreviewPages.length - 1) {
        currentPreviewPageIdx++;
        renderPreviewPage();
    }
});

el('btn-cancel-preview').addEventListener('click', () => {
    el('preview-section').classList.add('hidden');
});

// --- Print ---

el('btn-print').addEventListener('click', () => {
    if (!currentFileId) return;
    if (settings && settings.ui.show_preview) {
        // Generate preview first if not done
        if (!currentPreviewData) {
            el('btn-preview').click();
            return;
        }
    }
    doPrint();
});

el('btn-confirm-print').addEventListener('click', () => {
    doPrint();
});

async function doPrint() {
    if (!currentFileId) return;

    const orientation = el('manual-orientation').value || null;
    const resize = el('manual-resize').checked;
    const label = el('print-label').value || null;
    const copies = parseInt(el('print-copies').value) || 1;

    showToast('Sending to print queue...');

    try {
        const res = await fetch('/api/print', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                file_id: currentFileId,
                orientation: orientation,
                resize: resize,
                label: label,
                copies: copies,
            }),
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Print failed');
        }
        const data = await res.json();
        showToast(`Print job queued (status: ${data.status})`, 'success');
        el('preview-section').classList.add('hidden');
        el('file-info').classList.add('hidden');
        currentFileId = null;
        currentPreviewData = null;
        currentPreviewPages = [];
        currentPreviewPageIdx = 0;
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// --- History ---

async function loadHistory() {
    try {
        const res = await fetch('/api/queue');
        const items = await res.json();
        const list = el('history-list');

        if (items.length === 0) {
            list.innerHTML = '<p class="empty-state">No prints yet.</p>';
            return;
        }

        list.innerHTML = items.slice().reverse().map(item => {
            const thumb = item.preview_filename
                ? `<img class="history-thumb" src="/api/files/${item.preview_filename}" alt="preview">`
                : `<div class="history-thumb-placeholder">N/A</div>`;

            const statusClass = `status-${item.status}`;
            const pagesBadge = item.num_pages > 1
                ? `<span class="page-badge">${item.num_pages} pages</span>`
                : '';
            const errorInfo = item.page_error
                ? `<span style="color:var(--error)">Page error: ${item.page_error}</span>`
                : (item.error_message ? `<span style="color:var(--error)">Error: ${item.error_message}</span>` : '');

            const debugSection = item.debug_info
                ? `<details class="history-debug"><summary>Debug details</summary><pre>${item.debug_info}</pre></details>`
                : '';

            return `
                <div class="history-item">
                    ${thumb}
                    <div class="history-info">
                        <div class="filename">${item.original_filename} ${pagesBadge}</div>
                        <div class="meta">
                            <span>${fmtDate(item.timestamp)}</span>
                            <span>${item.width_mm}×${item.height_mm}mm</span>
                            <span>Label: ${item.label}</span>
                            <span>Copies: ${item.copies}</span>
                            ${errorInfo}
                        </div>
                        ${debugSection}
                    </div>
                    <div class="history-actions">
                        <span class="history-status ${statusClass}">${item.status}</span>
                        <button class="btn btn-secondary btn-small" onclick="removeHistoryItem('${item.id}')">Remove</button>
                    </div>
                </div>
            `;
        }).join('');
    } catch (e) {
        showToast('Failed to load history', 'error');
    }
}

window.removeHistoryItem = async (id) => {
    try {
        await fetch(`/api/queue/${id}`, { method: 'DELETE' });
        loadHistory();
        showToast('Removed', 'success');
    } catch (e) {
        showToast('Failed to remove', 'error');
    }
}

el('btn-refresh-history').addEventListener('click', loadHistory);

// --- Settings ---

async function loadSettings() {
    try {
        const [settingsRes, labelsRes, modelsRes] = await Promise.all([
            fetch('/api/settings').then(r => r.json()),
            fetch('/api/labels').then(r => r.json()),
            fetch('/api/models').then(r => r.json()),
        ]);

        settings = settingsRes;
        labels = Array.isArray(labelsRes) ? labelsRes : [];
        models = Array.isArray(modelsRes) ? modelsRes : [];

        // Populate label dropdowns
        const labelOpts = labels.map(l =>
            `<option value="${l.identifier}">${l.identifier} — ${l.name}</option>`
        ).join('');
        el('print-label').innerHTML = labelOpts;
        el('set-label').innerHTML = labelOpts;

        // Populate model dropdown
        el('set-model').innerHTML = models.map(m =>
            `<option value="${m}">${m}</option>`
        ).join('');

        // Set current values
        el('set-model').value = settings.printer.model;
        el('set-backend').value = settings.printer.backend;
        el('set-identifier').value = settings.printer.identifier;
        el('set-label').value = settings.printer.label;

        el('set-tape-width').value = settings.printing.tape_width_mm;
        el('set-rotate').value = settings.printing.rotate;
        el('set-threshold').value = settings.printing.threshold;
        el('set-copies').value = settings.printing.copies;
        el('set-dither').checked = settings.printing.dither;
        el('set-compress').checked = settings.printing.compress;
        el('set-cut').checked = settings.printing.cut;
        el('set-hq').checked = settings.printing.hq;
        el('set-dpi600').checked = settings.printing.dpi_600;
        el('set-copy-order').value = settings.printing.copy_order || 'sequential';
        el('set-on-print-error').value = settings.printing.on_print_error || 'stop';

        el('set-show-preview').checked = settings.ui.show_preview;
        el('set-max-history').value = settings.ui.max_history;

        // Set print label to current config
        if (settings.printer.label) {
            el('print-label').value = settings.printer.label;
        }
    } catch (e) {
        showToast('Failed to load settings', 'error');
    }
}

el('btn-save-settings').addEventListener('click', async () => {
    const update = {
        printer: {
            model: el('set-model').value,
            backend: el('set-backend').value,
            identifier: el('set-identifier').value,
            label: el('set-label').value,
        },
        printing: {
            tape_width_mm: parseInt(el('set-tape-width').value),
            rotate: el('set-rotate').value,
            threshold: parseInt(el('set-threshold').value),
            copies: parseInt(el('set-copies').value),
            dither: el('set-dither').checked,
            compress: el('set-compress').checked,
            cut: el('set-cut').checked,
            hq: el('set-hq').checked,
            dpi_600: el('set-dpi600').checked,
            copy_order: el('set-copy-order').value,
            on_print_error: el('set-on-print-error').value,
        },
        ui: {
            show_preview: el('set-show-preview').checked,
            max_history: parseInt(el('set-max-history').value),
        },
    };

    try {
        const res = await fetch('/api/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(update),
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Save failed');
        }
        settings = await res.json();
        el('settings-saved-msg').classList.remove('hidden');
        setTimeout(() => el('settings-saved-msg').classList.add('hidden'), 2000);
        showToast('Settings saved', 'success');
    } catch (e) {
        showToast(e.message, 'error');
    }
});

// --- Printer status ---

async function checkPrinterStatus() {
    const badge = el('printer-status-badge');
    const ident = settings?.printer?.identifier || 'unknown';
    const backend = settings?.printer?.backend || 'unknown';
    try {
        const res = await fetch('/api/printer/status');
        const data = await res.json();
        if (data.error) {
            badge.className = 'badge badge-offline';
            badge.textContent = 'Printer: Offline';
            badge.dataset.tooltip = `Backend: ${backend}\nIdentifier: ${ident}\nError: ${data.error}`;
        } else {
            badge.className = 'badge badge-online';
            badge.textContent = `Printer: ${settings?.printer?.model || 'Connected'}`;
            badge.dataset.tooltip = `Backend: ${backend}\nIdentifier: ${ident}\nStatus: ${data.status || 'OK'}`;
        }
    } catch (e) {
        badge.className = 'badge badge-unknown';
        badge.textContent = 'Printer: Unknown';
        badge.dataset.tooltip = `Backend: ${backend}\nIdentifier: ${ident}\nError: ${e.message || 'Unable to reach server'}`;
    }
}

// --- Init ---

(async function init() {
    await loadSettings();
    checkPrinterStatus();
    setInterval(checkPrinterStatus, 30000);
})();
