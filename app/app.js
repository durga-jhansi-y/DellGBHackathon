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

function setPage(view) {
  const page = pages[view] || pages.map;
  document.querySelectorAll('.nav-link').forEach((button) => button.classList.toggle('active', button.dataset.view === view));
  $('#page-crumb').textContent = page.crumb;
  $('#page-title').textContent = page.title;
  $('#page-subtitle').textContent = page.subtitle;
  $('#heading-eyebrow').textContent = page.eyebrow;
  $('#map-view').classList.toggle('hidden', view !== 'map');
  $('#secondary-view').classList.toggle('hidden', view === 'map');

  const library = '<div class="empty-card"><div class="empty-icon">▤</div><span class="eyebrow">DATASET LIBRARY · SAMPLE DATA</span><h2>Your offline datasets</h2><p>Manage imagery prepared for disconnected field work.</p><button class="button button-primary" data-action="download">＋ Download a dataset</button></div><div class="dataset-row"><div class="dataset-row-thumb">▧</div><div class="dataset-row-main"><strong>North Cascades National Park</strong><span>Washington, US · Satellite · 2.4 GB · Active generation NCNP-2024.08.2</span></div><span class="verified-pill">✓ Ready offline</span><button class="row-more" data-action="dataset">Details</button></div><div class="dataset-row"><div class="dataset-row-thumb muted-thumb">▧</div><div class="dataset-row-main"><strong>Olympic Peninsula</strong><span>Washington, US · Terrain imagery · 1.1 GB</span></div><span class="downloaded-label">Available to download</span><button class="row-more" data-action="download">＋ Add</button></div>';
  const findings = '<div class="empty-card"><div class="empty-icon">◇</div><span class="eyebrow">EXPERIMENTAL · SAMPLE RESULT</span><h2>Field findings</h2><p>Recognition is an asynchronous, experimental workflow. Review imagery context before treating a result as meaningful.</p><button class="button button-secondary" data-action="ask">Ask about an area</button></div><div class="dataset-row"><div class="finding-avatar">◇</div><div class="dataset-row-main"><strong>Possible slope change</strong><span>North Cascades · imagery Aug 21, 2024 · confidence 0.87</span></div><span class="downloaded-label">Unreviewed</span><button class="row-more" data-action="finding">View</button></div><div class="detail-callout">Model output is probabilistic, not a confirmed fact. This sample finding is not connected to a recognition service.</div>';
  const settings = '<div class="empty-card"><div class="empty-icon">⚙</div><span class="eyebrow">LOCAL DEVICE · PROTOTYPE</span><h2>Field-ready settings</h2><p>Control how offline maps use storage and network access.</p></div><div class="settings-card"><div><strong>Offline mode</strong><span>Keep map browsing on installed data only.</span></div><span class="verified-pill">On</span></div><div class="settings-card"><div><strong>Local storage</strong><span>12.8 GB used of 20 GB available.</span></div><span class="downloaded-label">Manage</span></div><div class="settings-card"><div><strong>Trusted publisher</strong><span>USGS Earth Explorer · sample trust record.</span></div><span class="verified-pill">✓ Trusted</span></div><div class="detail-callout">Prototype settings are illustrative and do not change device permissions or storage.</div>';
  $('#secondary-view').innerHTML = view === 'datasets' ? library : view === 'findings' ? findings : settings;
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
  const labels = { all: 'Showing all sample tiles', verified: 'Showing verified sample tiles', unverified: 'No unverified sample tiles in this sample map', failed: 'No failed tiles in this sample map' };
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
  $('#welcome-next').innerHTML = onboardingStep === 2 ? 'Explore sample map <span>→</span>' : 'Continue <span>→</span>';
}
function closeOnboarding() {
  localStorage.setItem('crystalGeoOnboarded', 'yes');
  firstRun.close();
}
$('#welcome-next').addEventListener('click', () => {
  if (onboardingStep < onboarding.length - 1) { onboardingStep += 1; showOnboardingStep(); return; }
  closeOnboarding();
  showToast('Sample map ready · offline prototype');
});
$('#welcome-skip').addEventListener('click', closeOnboarding);
if (!localStorage.getItem('crystalGeoOnboarded')) { showOnboardingStep(); firstRun.showModal(); }
