const $ = (selector) => document.querySelector(selector);
const toast = $('#toast');
let toastTimer;
let zoom = 1;
let zoomLabel = 2;

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 2600);
}

const pages = {
  map: { crumb: 'Map', title: 'Offline map', subtitle: 'Your imagery, ready when the network isn’t.', eyebrow: 'FIELD WORKSPACE  ·  NORTH CASCADES' },
  datasets: { crumb: 'Datasets', title: 'Datasets', subtitle: 'Manage the imagery you carry into the field.', eyebrow: 'OFFLINE LIBRARY' },
  findings: { crumb: 'Findings', title: 'Findings', subtitle: 'Review observations from your field work.', eyebrow: 'LOCAL RECOGNITION' },
  settings: { crumb: 'Settings', title: 'Settings', subtitle: 'Configure storage, network policy, and trust.', eyebrow: 'YOUR WORKSPACE' }
};

const datasetCatalog = [
  { id: 'north-cascades', name: 'North Cascades National Park', region: 'Washington, US', kind: 'Satellite imagery', capture: 'Aug 14 – 28, 2024', generation: 'NCNP-2024.08.2', size: '2.4 GB', publisher: 'USGS Earth Explorer', state: 'ready', active: true, download: 100, verification: 100 },
  { id: 'olympic', name: 'Olympic Peninsula', region: 'Washington, US', kind: 'Terrain imagery', capture: 'May 02 – 18, 2025', generation: 'OLYM-2025.05.1', size: '1.1 GB', publisher: 'USGS Earth Explorer', state: 'available', active: false, download: 0, verification: 0 },
  { id: 'rainier', name: 'Mount Rainier', region: 'Washington, US', kind: 'Satellite imagery', capture: 'Jul 11 – 23, 2025', generation: 'RAIN-2025.07.1', size: '860 MB', publisher: 'USGS Earth Explorer', state: 'available', active: false, download: 0, verification: 0 }
];
let datasetTab = 'installed';
const datasetTimers = new Map();
let currentView = 'map';
let pendingSyncCount = 2;
let lastSyncLabel = 'Nov 12, 2026';
let syncBusy = false;

function renderDatasets() {
  const installed = datasetCatalog.filter((dataset) => dataset.state !== 'available');
  const available = datasetCatalog.filter((dataset) => dataset.state === 'available');
  const rows = datasetTab === 'installed' ? installed : available;
  const isOnline = document.body.dataset.networkMode === 'online';
  const syncState = syncBusy ? 'syncing' : pendingSyncCount === 0 ? 'complete' : isOnline ? 'online' : 'waiting';
  const syncMessage = syncBusy ? 'Syncing queued updates…' : pendingSyncCount === 0 ? `Everything is up to date · last sync ${lastSyncLabel}` : isOnline ? `${pendingSyncCount} queued updates ready · last sync ${lastSyncLabel}` : `${pendingSyncCount} queued updates waiting · last sync ${lastSyncLabel}`;
  const syncAction = syncBusy ? 'Syncing…' : pendingSyncCount === 0 ? 'Up to date' : isOnline ? 'Sync now' : 'Waiting for connection';
  const card = (dataset) => {
    const stateLabel = { ready: dataset.active ? 'Active · verified' : 'Ready offline', downloading: 'Downloading', paused: 'Paused', verifying: 'Verifying package', failed: 'Download failed', available: 'Available' }[dataset.state];
    const actions = dataset.state === 'available'
      ? `<button class="button button-primary" data-action="download" data-id="${dataset.id}">↓ Download</button>`
      : dataset.state === 'downloading'
        ? `<button class="button button-secondary" data-action="pause" data-id="${dataset.id}">Ⅱ Pause</button>`
        : dataset.state === 'paused'
          ? `<button class="button button-primary" data-action="resume" data-id="${dataset.id}">▶ Resume</button><button class="button button-secondary" data-action="cancel" data-id="${dataset.id}">Cancel</button>`
          : dataset.state === 'verifying'
            ? `<button class="button button-secondary" disabled>Verifying…</button>`
            : dataset.state === 'failed'
              ? `<button class="button button-primary" data-action="download" data-id="${dataset.id}">Retry</button><button class="button button-secondary" data-action="cancel" data-id="${dataset.id}">Remove partial</button>`
              : `<button class="button button-secondary" data-action="verify" data-id="${dataset.id}">↻ Verify again</button>${dataset.active ? '' : `<button class="button button-secondary" data-action="activate" data-id="${dataset.id}">Make active</button>`}<button class="text-action" data-action="remove" data-id="${dataset.id}">Remove</button>`;
    const progress = dataset.state === 'downloading' || dataset.state === 'paused' ? `<div class="phase-row"><div><strong>Download</strong><span>${dataset.download}% · ${dataset.size}</span></div><div class="phase-track"><i style="width:${dataset.download}%"></i></div></div>` : '';
    const verify = dataset.state === 'verifying' ? `<div class="phase-row verify-phase"><div><strong>Verify package</strong><span>${dataset.verification}% · checking signature and tile hashes</span></div><div class="phase-track"><i style="width:${dataset.verification}%"></i></div></div>` : '';
    return `<article class="dataset-card" data-state="${dataset.state}"><div class="dataset-art"><span>OFFLINE REGION</span><b>▧</b><i class="art-contour"></i><i class="art-contour second"></i></div><div class="dataset-card-main"><div class="dataset-card-top"><div><h3>${dataset.name}</h3><p>${dataset.region} <span>·</span> ${dataset.kind}</p></div><span class="dataset-state ${dataset.state}"><i></i>${stateLabel}</span></div><div class="dataset-meta"><span><small>CAPTURE</small>${dataset.capture}</span><span><small>PACKAGE</small>${dataset.size}</span><span><small>GENERATION</small><code>${dataset.generation}</code></span><span><small>PUBLISHER</small>${dataset.publisher}</span></div>${progress}${verify}<div class="dataset-card-actions">${actions}</div></div></article>`;
  };
  $('#secondary-view').innerHTML = `<div class="dataset-page-intro"><div><span class="eyebrow">OFFLINE LIBRARY · SAMPLE CATALOG</span><h2>Maps for the field</h2><p>Download a region before you lose connection. A package is ready only after verification finishes.</p></div><div class="storage-meter"><span>DEVICE STORAGE</span><strong>12.8 <small>of 20 GB</small></strong><div><i></i></div></div></div>
    <div class="sync-card ${syncState}"><span class="sync-icon">${syncBusy ? '↻' : pendingSyncCount === 0 ? '✓' : '⇧'}</span><div class="sync-copy"><strong>${pendingSyncCount ? `${pendingSyncCount} pending updates` : 'Sync status'}</strong><span>${syncMessage} · prototype queue</span></div><button class="button ${isOnline && pendingSyncCount ? 'button-primary' : 'button-secondary'}" data-action="sync" ${!isOnline || pendingSyncCount === 0 || syncBusy ? 'disabled' : ''}>${syncAction}</button></div>
    <div class="dataset-tabs" role="tablist"><button class="dataset-tab ${datasetTab === 'installed' ? 'active' : ''}" data-tab="installed" role="tab" aria-selected="${datasetTab === 'installed'}">Installed <b>${installed.length}</b></button><button class="dataset-tab ${datasetTab === 'available' ? 'active' : ''}" data-tab="available" role="tab" aria-selected="${datasetTab === 'available'}">Available <b>${available.length}</b></button><span class="catalog-note"><i></i> Sample data · sync not connected</span></div><div class="dataset-card-list">${rows.length ? rows.map(card).join('') : '<div class="dataset-empty">No maps in this list yet.</div>'}</div>`;
  $('#secondary-view').querySelectorAll('[data-tab]').forEach((button) => button.addEventListener('click', () => { datasetTab = button.dataset.tab; renderDatasets(); }));
  $('#secondary-view').querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', () => button.dataset.action === 'sync' ? startMockSync() : handleDatasetAction(button.dataset.action, button.dataset.id)));
}

function startMockSync() {
  if (document.body.dataset.networkMode !== 'online' || syncBusy || pendingSyncCount === 0) return;
  syncBusy = true; renderDatasets();
  setTimeout(() => { pendingSyncCount = 0; syncBusy = false; lastSyncLabel = 'just now'; renderDatasets(); showToast('Sample queue synced · no live service connected'); }, 1400);
}

function handleDatasetAction(action, id) {
  const dataset = datasetCatalog.find((item) => item.id === id);
  if (!dataset) return;
  if (action === 'activate') {
    datasetCatalog.forEach((item) => { item.active = item.id === id; });
    renderDatasets(); showToast(`${dataset.name} is now the active offline map`); return;
  }
  if (action === 'remove' || action === 'cancel') {
    dataset.state = 'available'; dataset.download = 0; dataset.verification = 0; dataset.active = false;
    renderDatasets(); showToast('Sample package removed from the installed list'); return;
  }
  if (action === 'pause') {
    clearInterval(datasetTimers.get(id)); dataset.state = 'paused'; renderDatasets(); showToast('Download paused · resume any time'); return;
  }
  if (action === 'verify') {
    dataset.state = 'verifying'; dataset.verification = 12; renderDatasets();
    const timer = setInterval(() => { dataset.verification = Math.min(100, dataset.verification + 22); if (dataset.verification >= 100) { clearInterval(timer); dataset.state = 'ready'; showToast('Package verified · imagery is ready offline'); } renderDatasets(); }, 500);
    datasetTimers.set(id, timer); return;
  }
  if (action === 'download' || action === 'resume') {
    dataset.state = 'downloading'; renderDatasets(); showToast('Downloading sample map package');
    const timer = setInterval(() => {
      dataset.download = Math.min(100, dataset.download + 16);
      if (dataset.download >= 100) {
        clearInterval(timer); dataset.state = 'verifying'; dataset.verification = 8; renderDatasets();
        const verifyTimer = setInterval(() => { dataset.verification = Math.min(100, dataset.verification + 23); if (dataset.verification >= 100) { clearInterval(verifyTimer); dataset.state = 'ready'; datasetTab = 'installed'; showToast('Download complete · package verified and ready offline'); } renderDatasets(); }, 500);
        datasetTimers.set(id, verifyTimer);
      } else renderDatasets();
    }, 500);
    datasetTimers.set(id, timer);
  }
}

function setPage(view) {
  currentView = view;
  const page = pages[view] || pages.map;
  document.querySelectorAll('.nav-link').forEach((button) => button.classList.toggle('active', button.dataset.view === view));
  $('#page-crumb').textContent = page.crumb;
  $('#page-title').textContent = page.title;
  $('#page-subtitle').textContent = page.subtitle;
  $('#heading-eyebrow').textContent = page.eyebrow;
  $('#map-view').classList.toggle('hidden', view !== 'map');
  $('#secondary-view').classList.toggle('hidden', view === 'map');

  if (view === 'datasets') { datasetTab = 'installed'; renderDatasets(); return; }
  const findings = '<div class="empty-card"><div class="empty-icon">◇</div><span class="eyebrow">EXPERIMENTAL · SAMPLE RESULT</span><h2>Field findings</h2><p>Recognition is an asynchronous, experimental workflow. Review imagery context before treating a result as meaningful.</p><button class="button button-secondary" data-action="ask">Ask about an area</button></div><div class="dataset-row"><div class="finding-avatar">◇</div><div class="dataset-row-main"><strong>Possible slope change</strong><span>North Cascades · imagery Aug 21, 2024 · confidence 0.87</span></div><span class="downloaded-label">Unreviewed</span><button class="row-more" data-action="finding">View</button></div><div class="detail-callout">Model output is probabilistic, not a confirmed fact. This sample finding is not connected to a recognition service.</div>';
  const settings = '<div class="empty-card"><div class="empty-icon">⚙</div><span class="eyebrow">LOCAL DEVICE · PROTOTYPE</span><h2>Field-ready settings</h2><p>Control how offline maps use storage and network access.</p></div><div class="settings-card"><div><strong>Offline mode</strong><span>Keep map browsing on installed data only.</span></div><span class="verified-pill">On</span></div><div class="settings-card"><div><strong>Local storage</strong><span>12.8 GB used of 20 GB available.</span></div><span class="downloaded-label">Manage</span></div><div class="settings-card"><div><strong>Trusted publisher</strong><span>USGS Earth Explorer · sample trust record.</span></div><span class="verified-pill">✓ Trusted</span></div><div class="detail-callout">Prototype settings are illustrative and do not change device permissions or storage.</div>';
  $('#secondary-view').innerHTML = view === 'findings' ? findings : settings;
  $('#secondary-view').querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', () => {
    const action = button.dataset.action;
    if (action === 'dataset') openModal('details');
    else if (action === 'finding') showToast('Sample finding · confidence 0.87 · not human reviewed');
    else if (action === 'ask') showToast('Recognition is experimental and not connected yet');
    else showToast('Dataset download is a prototype placeholder');
  }));
}

document.querySelectorAll('.nav-link').forEach((button) => button.addEventListener('click', () => setPage(button.dataset.view)));

$('#imagery-toggle').addEventListener('click', (event) => {
  const button = event.currentTarget;
  const toggle = button.querySelector('.toggle');
  const enabled = toggle.classList.toggle('on');
  $('.map-art').style.opacity = enabled ? '1' : '.24';
  showToast(enabled ? 'Imagery layer is on' : 'Imagery layer is dimmed');
});

$('#findings-toggle').addEventListener('click', (event) => {
  const enabled = event.currentTarget.querySelector('.toggle').classList.toggle('on');
  $('.finding-pin').classList.toggle('show', enabled);
  showToast(enabled ? 'Showing sample finding' : 'Findings layer is off');
});

function setZoom(delta) {
  zoom = Math.min(1.5, Math.max(.8, zoom + delta));
  zoomLabel = Math.max(0.5, Math.round((2 / zoom) * 10) / 10);
  $('.map-art').style.transform = `scale(${zoom})`;
  $('#scale-label').textContent = `${zoomLabel} km`;
}
$('#zoom-in').addEventListener('click', () => setZoom(.1));
$('#zoom-out').addEventListener('click', () => setZoom(-.1));
$('#recenter').addEventListener('click', () => { zoom = 1; $('.map-art').style.transform = 'scale(1.03)'; $('#scale-label').textContent = '2 km'; showToast('Map centered on North Cascades'); });
$('#locate-button').addEventListener('click', () => showToast('Location is not enabled in this prototype'));
$('#layers-button').addEventListener('click', () => showToast('Imagery and findings layers are available above'));
$('#sync-button').addEventListener('click', () => showToast('You’re up to date · checked just now'));
$('#download-area').addEventListener('click', () => showToast('Area download planning is a prototype placeholder'));

const networkModeButton = $('#network-mode-toggle');
function setNetworkMode(mode, notify = false) {
  const online = mode === 'online';
  document.body.dataset.networkMode = online ? 'online' : 'offline';
  networkModeButton.classList.toggle('online', online);
  networkModeButton.setAttribute('aria-pressed', String(online));
  $('#network-mode-label').textContent = online ? 'Online mode · demo' : 'Offline mode';
  if (currentView === 'datasets') renderDatasets();
  if (notify) showToast(online ? 'Online mode selected · network services are not connected in this prototype' : 'Offline mode selected · using the sample local map');
}
setNetworkMode(localStorage.getItem('crystalGeoNetworkMode') || 'offline');
networkModeButton.addEventListener('click', () => {
  const nextMode = document.body.dataset.networkMode === 'online' ? 'offline' : 'online';
  localStorage.setItem('crystalGeoNetworkMode', nextMode);
  setNetworkMode(nextMode, true);
});

const modal = $('#modal');
const modalCopy = {
  details: {
    title: 'Dataset details',
    body: '<p>This sample package represents a verified offline imagery generation for North Cascades National Park.</p><div class="modal-facts"><div><span>Coverage</span><strong>North Cascades, Washington</strong></div><div><span>Capture period</span><strong>Aug 14 – 28, 2024</strong></div><div><span>Generation</span><strong>NCNP-2024.08.2</strong></div><div><span>Package</span><strong>2.4 GB · 186 tiles</strong></div></div><p>Verification status and imagery are illustrative prototype data.</p>'
  },
  provenance: {
    title: 'Source & provenance',
    body: '<p>Know what you are looking at before you make a field decision.</p><div class="modal-facts"><div><span>Publisher</span><strong>USGS Earth Explorer</strong></div><div><span>Source imagery</span><strong>Satellite · sample catalog</strong></div><div><span>Acquired</span><strong>Aug 14 – 28, 2024</strong></div><div><span>Publisher signature</span><strong>Ed25519 · shown as verified</strong></div><div><span>Integrity</span><strong>1,248 / 1,248 sample tiles</strong></div></div><p>Authenticity and integrity do not establish that imagery is accurate or current.</p>'
  },
  verify: {
    title: 'Imagery verification',
    body: '<p>The active generation is shown as verified in this prototype. A production app must verify the signed manifest and each requested tile before rendering it.</p><div class="modal-facts"><div><span>Manifest</span><strong>Signature verified</strong></div><div><span>Active generation</span><strong>NCNP-2024.08.2</strong></div><div><span>Visible tiles</span><strong>All checked</strong></div></div><p>Verification proves origin and integrity, not real-world accuracy or freshness.</p>'
  }
};
function openModal(which) {
  const content = modalCopy[which];
  $('#modal-title').textContent = content.title;
  $('#modal-content').innerHTML = `<div class="modal-body">${content.body}</div>`;
  modal.showModal();
}
$('#details-button').addEventListener('click', () => openModal('details'));
$('#provenance-button').addEventListener('click', () => openModal('provenance'));
$('#verify-info').addEventListener('click', () => openModal('verify'));
$('#map-info').addEventListener('click', () => openModal('verify'));
document.querySelectorAll('.map-filter').forEach((filter) => filter.addEventListener('click', () => {
  document.querySelectorAll('.map-filter').forEach((item) => {
    const selected = item === filter;
    item.classList.toggle('active', selected);
    item.setAttribute('aria-pressed', String(selected));
  });
  const labels = { all: 'All sample tile states shown', verified: 'Only verified base imagery shown', unverified: 'Showing the neutral unverified tile', failed: 'Showing the verification warning tile' };
  $('#map-stage').dataset.filter = filter.dataset.filter;
  showToast(labels[filter.dataset.filter]);
}));
$('#select-area').addEventListener('click', () => showToast('Area selection is ready for the recognition workflow prototype'));
$('#modal-close').addEventListener('click', () => modal.close());
$('#modal-done').addEventListener('click', () => modal.close());
modal.addEventListener('click', (event) => { if (event.target === modal) modal.close(); });

setPage('map');
const firstRun = $('#first-run');
const onboarding = [
  { title: 'Maps for the moments<br>you lose connection.', copy: 'Crystal Geo keeps trusted imagery available in the field. Download a map area before you head out.' },
  { title: 'Know what you’re<br>looking at.', copy: 'Check the source, capture date, and integrity status for your imagery. Verification confirms origin and integrity, not accuracy or freshness.' },
  { title: 'Bring a map<br>with you.', copy: 'Choose a dataset or import a package before going offline. This prototype includes a sample North Cascades map to explore.' }
];
let onboardingStep = 0;
function showOnboardingStep() {
  const step = onboarding[onboardingStep];
  $('#welcome-title').innerHTML = step.title;
  $('#welcome-copy').textContent = step.copy;
  document.querySelectorAll('.step-dot').forEach((dot, index) => dot.classList.toggle('active', index === onboardingStep));
  $('.step-count').textContent = `${onboardingStep + 1} / 3`;
  $('#welcome-next').innerHTML = onboardingStep === 2 ? 'Use sample map <span>→</span>' : 'Continue <span>→</span>';
  $('#welcome-import').classList.toggle('hidden', onboardingStep !== 2);
}
function closeOnboarding() {
  localStorage.setItem('crystalGeoOnboarded', 'yes');
  firstRun.close();
}
$('#welcome-next').addEventListener('click', () => {
  if (onboardingStep < onboarding.length - 1) { onboardingStep += 1; showOnboardingStep(); return; }
  closeOnboarding();
  setPage('map'); showToast('Sample map ready · offline prototype');
});
$('#welcome-import').addEventListener('click', () => $('#package-file').click());
$('#package-file').addEventListener('change', (event) => {
  if (event.target.files.length) { closeOnboarding(); setPage('datasets'); showToast('Package selected · importing is not connected in this prototype'); }
});
$('#welcome-skip').addEventListener('click', closeOnboarding);
if (!localStorage.getItem('crystalGeoOnboarded')) { showOnboardingStep(); firstRun.showModal(); }
