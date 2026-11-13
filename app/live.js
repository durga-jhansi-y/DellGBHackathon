/* Live-data layer for the prototype UI. Loaded after app.js; replaces the sample North Cascades
   data with the real catalog from the Crystal Geo API. If the API is unreachable the original
   sample prototype keeps working and a banner says so. */
(() => {
  const API = window.CrystalAPI;
  const q = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const S = { health: null, points: [], findingIds: new Set(), showFootprints: true, showFindings: true, selected: null };
  const fmt = (n, d = 4) => (n == null ? '—' : Number(n).toFixed(d));

  /* ---------------- boot ---------------- */
  async function boot() {
    try { S.health = await API.health(); }
    catch (e) {
      const b = document.createElement('div');
      b.className = 'live-banner';
      b.textContent = `Crystal Geo API not reachable at ${API.base}. Showing the built-in sample prototype. Start the API: python -m uvicorn backend.app:app --port 8000`;
      q('.main-content').insertBefore(b, q('.page-heading'));
      return;
    }
    document.body.classList.add('live');
    applyCopy();
    const images = await API.allImages();
    buildPoints(images);
    await refreshFindingIds();
    try { S.mapboxToken = (await API.config()).mapbox_token || null; } catch (e) { S.mapboxToken = null; }
    map.mount();
    viewer.mount();
    patchControls();
    renderInfoPanel();
    patchSetPage();
    setPage(currentView);
  }

  function applyCopy() {
    pages.map.eyebrow = 'FIELD WORKSPACE  ·  TEXAS FLOOD IMAGERY';
    pages.map.subtitle = 'Every image is checked against its published SHA-256 before it is shown.';
    pages.datasets.eyebrow = 'IMAGERY LIBRARY';
    pages.findings.eyebrow = 'AI-ASSISTED  ·  EXPERIMENTAL';
    pages.findings.subtitle = 'Qwen answers, saved with source, imagery date and model. Not human-reviewed.';
    datasetCatalog.length = 0;
    datasetCatalog.push({ id: 'utsa-flood', name: 'UTSA Texas Flood Imagery', region: 'Texas, US', kind: 'Aerial imagery (NOAA · USDA NAIP · USCG)',
      capture: '2016 – 2025', generation: 'utsa-flood-dev', size: '1.6 GB', publisher: 'UTSA via Hugging Face', state: 'ready', active: true, download: 100, verification: 100 });
    pendingSyncCount = 0; lastSyncLabel = 'just now';
    const chip = q('.dataset-chip');
    if (chip) { q('strong', chip).textContent = 'Texas Flood'; q('small', chip).textContent = `Aerial imagery · ${S.health.images.toLocaleString()} images`; }
    const attr = q('.map-attribution'); if (attr) attr.textContent = 'Imagery: NOAA · USDA NAIP · USCG · dataset CC-BY-NC-4.0';
    const st = q('.map-status'); if (st) { q('strong', st).textContent = 'SHA-256 checked'; q('small', st).textContent = 'Each image is verified when opened'; }
    const fl = q('.footer-left'); if (fl) fl.innerHTML = `<span class="online-indicator"></span> ${S.health.images.toLocaleString()} images served by the Crystal Geo API <span class="footer-separator">·</span> ${S.health.findings} AI findings saved`;
    modalCopy.details.body = `<p>The UTSA Texas flood imagery set: aerial photos of Hurricane Harvey (2017) and the Hill Country floods (2025).</p><div class="modal-facts"><div><span>Coverage</span><strong>Texas, US</strong></div><div><span>Capture period</span><strong>2016 – 2025</strong></div><div><span>Images</span><strong>${S.health.images.toLocaleString()} · 575 before/after pairs</strong></div><div><span>Package</span><strong>1.6 GB</strong></div></div><p>Dataset license: CC-BY-NC-4.0 (non-commercial). Aerial photos, not satellite images.</p>`;
    modalCopy.provenance.body = '<p>Know what you are looking at before you make a field decision.</p><div class="modal-facts"><div><span>Sources</span><strong>NOAA · USDA NAIP · USCG</strong></div><div><span>Catalog</span><strong>Hugging Face · UTSA-flood-images</strong></div><div><span>Integrity</span><strong>SHA-256 per image</strong></div><div><span>Publisher signature</span><strong>Not signed (development data)</strong></div></div><p>Hashes prove the file matches what the dataset published. They do not prove the imagery is accurate or current.</p>';
    modalCopy.verify.body = '<p>Each image is hashed on the server and compared with its published SHA-256 before it is sent. A mismatch is refused, never shown.</p><div class="modal-facts"><div><span>Check</span><strong>SHA-256 per image</strong></div><div><span>Signature</span><strong>Not signed (dev)</strong></div></div><p>Verification proves integrity, not real-world accuracy or freshness.</p>';
  }

  function buildPoints(images) {
    const groups = new Map();
    for (const im of images) {
      if (im.west == null) continue;
      const key = [im.west, im.south, im.east, im.north].map((v) => Number(v).toFixed(5)).join('|');
      (groups.get(key) || groups.set(key, []).get(key)).push(im);
    }
    S.points = [...groups.values()].map((g) => {
      const rep = g.slice().sort((a, b) => String(b.acquisition_date).localeCompare(String(a.acquisition_date)))[0];
      return { id: rep.image_id, ids: g.map((i) => i.image_id), paired: g.some((i) => i.paired_image_id), date: rep.acquisition_date, source: rep.source_agency,
        locality: rep.locality, w: rep.west, e: rep.east, s: rep.south, n: rep.north, lon: (rep.west + rep.east) / 2, lat: (rep.south + rep.north) / 2 };
    });
    S.noCoords = images.filter((i) => i.west == null).length;
  }

  async function refreshFindingIds() {
    try {
      const { items } = await API.findings({ limit: 500 });
      S.findingIds = new Set(items.flatMap((f) => [f.image_id, f.paired_image_id].filter(Boolean)));
      S.findingCount = items.length;
    } catch (e) { /* keep previous */ }
    map.draw();
  }

  /* ---------------- map ---------------- */
  const canvasMap = (() => {   // fallback used only if Leaflet cannot load
    let canvas, ctx, W = 0, H = 0, drag = null, tip, chipsEl;
    const view = { cx: -97, cy: 30, ppd: 40 };
    const kx = () => Math.cos(view.cy * Math.PI / 180);
    const toX = (lon) => W / 2 + (lon - view.cx) * view.ppd * kx();
    const toY = (lat) => H / 2 - (lat - view.cy) * view.ppd;
    const STEPS = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10];

    function mount() {
      const host = document.createElement('div');
      host.id = 'live-map';
      host.innerHTML = `<canvas id="live-canvas"></canvas><div class="live-chips" id="live-chips"></div>
        <div class="live-legend"><span><i style="background:#d4ed74"></i>Before/after pair</span><span><i style="background:#ffb454"></i>Single image (2025 aftermath)</span><span><i style="background:#7fd1ff"></i>Has AI finding</span></div>
        <div class="live-note">${S.points.length.toLocaleString()} locations${S.noCoords ? ` · ${S.noCoords} images have no coordinates` : ''}. Drag to pan, scroll to zoom, click a location.</div>
        <div class="live-tip" id="live-tip"></div>`;
      q('.map-surface').appendChild(host);
      canvas = q('#live-canvas'); ctx = canvas.getContext('2d'); tip = q('#live-tip'); chipsEl = q('#live-chips');
      const chips = [['All locations', S.points], ['Hurricane Harvey pairs', S.points.filter((p) => p.paired)], ['Hill Country 2025', S.points.filter((p) => !p.paired)]];
      chips.forEach(([label, pts], i) => {
        const b = document.createElement('button');
        b.className = 'live-chip' + (i === 0 ? ' active' : ''); b.textContent = `${label} (${pts.length})`;
        b.addEventListener('click', () => { chipsEl.querySelectorAll('.live-chip').forEach((c) => c.classList.toggle('active', c === b)); fit(pts); });
        chipsEl.appendChild(b);
      });
      new ResizeObserver(resize).observe(host);
      canvas.addEventListener('pointerdown', (ev) => { canvas.setPointerCapture(ev.pointerId); drag = { x: ev.clientX, y: ev.clientY, cx: view.cx, cy: view.cy, moved: false }; canvas.classList.add('dragging'); tip.style.display = 'none'; });
      canvas.addEventListener('pointermove', onMove);
      canvas.addEventListener('pointerup', (ev) => {
        canvas.classList.remove('dragging');
        if (drag && !drag.moved) { const hit = hitTest(ev); if (hit) viewer.open(hit.id); }
        drag = null;
      });
      canvas.addEventListener('pointerleave', () => { tip.style.display = 'none'; });
      canvas.addEventListener('wheel', (ev) => { ev.preventDefault(); zoomAt(ev, Math.pow(1.0018, -ev.deltaY)); }, { passive: false });
      resize();
    }

    function resize() {
      const host = q('#live-map'); if (!host) return;
      const r = host.getBoundingClientRect(); const dpr = window.devicePixelRatio || 1;
      if (!r.width || !r.height) return;
      const first = W === 0;
      W = r.width; H = r.height; canvas.width = W * dpr; canvas.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (first) fit(S.points); else draw();
    }

    function fit(pts) {
      if (!pts.length || !W) return;
      const lons = pts.flatMap((p) => [p.w, p.e]), lats = pts.flatMap((p) => [p.s, p.n]);
      const minLon = Math.min(...lons), maxLon = Math.max(...lons), minLat = Math.min(...lats), maxLat = Math.max(...lats);
      view.cx = (minLon + maxLon) / 2; view.cy = (minLat + maxLat) / 2;
      const dx = Math.max(maxLon - minLon, 0.01) * kx(), dy = Math.max(maxLat - minLat, 0.01);
      view.ppd = clamp(Math.min(W * 0.8 / dx, H * 0.8 / dy));
      draw();
    }
    const clamp = (v) => Math.min(600000, Math.max(5, v));

    function zoomAt(pos, f) {
      const r = canvas.getBoundingClientRect(); const px = (pos.clientX ?? r.left + W / 2) - r.left, py = (pos.clientY ?? r.top + H / 2) - r.top;
      const lon = view.cx + (px - W / 2) / (view.ppd * kx()), lat = view.cy - (py - H / 2) / view.ppd;
      view.ppd = clamp(view.ppd * f);
      view.cx = lon - (px - W / 2) / (view.ppd * kx()); view.cy = lat + (py - H / 2) / view.ppd;
      draw();
    }

    function onMove(ev) {
      if (drag) {
        const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
        if (drag.moved) { view.cx = drag.cx - dx / (view.ppd * kx()); view.cy = drag.cy + dy / view.ppd; draw(); }
        return;
      }
      const hit = hitTest(ev);
      canvas.style.cursor = hit ? 'pointer' : 'grab';
      if (hit) {
        const r = canvas.getBoundingClientRect();
        tip.innerHTML = `<strong>${esc(hit.id)}</strong> · ${esc(hit.date)} · ${esc(hit.source || '')}${hit.paired ? ' · before/after' : ''}`;
        tip.style.display = 'block'; tip.style.left = `${ev.clientX - r.left + 12}px`; tip.style.top = `${ev.clientY - r.top + 12}px`;
      } else tip.style.display = 'none';
    }

    function hitTest(ev) {
      const r = canvas.getBoundingClientRect(); const px = ev.clientX - r.left, py = ev.clientY - r.top;
      let best = null, bd = 12 * 12;
      for (const p of S.points) { const d = (toX(p.lon) - px) ** 2 + (toY(p.lat) - py) ** 2; if (d < bd) { bd = d; best = p; } }
      return best;
    }

    function draw() {
      if (!ctx || !W) return;
      ctx.fillStyle = '#0d1512'; ctx.fillRect(0, 0, W, H);
      // graticule
      const step = STEPS.find((s) => s * view.ppd >= 90) || 10; const dec = step < 0.01 ? 3 : step < 0.1 ? 2 : step < 1 ? 1 : 0;
      const lon0 = view.cx - W / 2 / (view.ppd * kx()), lon1 = view.cx + W / 2 / (view.ppd * kx()), lat0 = view.cy - H / 2 / view.ppd, lat1 = view.cy + H / 2 / view.ppd;
      ctx.lineWidth = 1; ctx.strokeStyle = '#1b2a24'; ctx.fillStyle = '#5c6e63'; ctx.font = '10px monospace';
      for (let lo = Math.ceil(lon0 / step) * step; lo <= lon1; lo += step) { const x = Math.round(toX(lo)) + .5; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); ctx.fillText(`${lo.toFixed(dec)}°`, x + 4, H - 8); }
      for (let la = Math.ceil(lat0 / step) * step; la <= lat1; la += step) { const y = Math.round(toY(la)) + .5; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); ctx.fillText(`${la.toFixed(dec)}°`, 6, y - 4); }
      // locations
      if (S.showFootprints) for (const p of S.points) {
        const x = toX(p.lon), y = toY(p.lat); if (x < -40 || y < -40 || x > W + 40 || y > H + 40) continue;
        const col = p.paired ? '#d4ed74' : '#ffb454'; const w = (p.e - p.w) * view.ppd * kx(), h = (p.n - p.s) * view.ppd;
        if (w > 12) { ctx.fillStyle = col + '22'; ctx.strokeStyle = col; ctx.fillRect(x - w / 2, y - h / 2, w, h); ctx.strokeRect(x - w / 2, y - h / 2, w, h); }
        else { ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x, y, 3.5, 0, 7); ctx.fill(); }
        if (S.showFindings && p.ids.some((id) => S.findingIds.has(id))) { ctx.strokeStyle = '#7fd1ff'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, Math.max(8, Math.min(w, h) / 2 + 4), 0, 7); ctx.stroke(); ctx.lineWidth = 1; }
        if (S.selected === p.id) { ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, Math.max(11, Math.min(w, h) / 2 + 7), 0, 7); ctx.stroke(); ctx.lineWidth = 1; }
      }
      // scale bar
      const kmPerPx = 111.32 / view.ppd; const nice = [0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500].find((k) => k / kmPerPx >= 60) || 500;
      const lab = q('#scale-label'), line = q('.scale-line');
      if (lab) lab.textContent = nice >= 1 ? `${nice} km` : `${nice * 1000} m`;
      if (line) line.style.width = `${Math.round(nice / kmPerPx)}px`;
    }

    return { mount, draw, resize, fit, zoomAt, centerZoom: (f) => zoomAt({}, f), fitAll: () => fit(S.points), showOverlay() {}, clearOverlay() {} };
  })();

  /* ---------------- live map (Leaflet + Mapbox / Esri / OpenStreetMap) ---------------- */
  const leafletMap = (() => {
    let m, layers = {}, order = [], current = null, markers, footprints, rings, overlay, warned = false;
    const col = (p) => (p.paired ? '#d4ed74' : '#ffb454');

    function mount() {
      document.body.classList.add('live-leaflet');
      const host = document.createElement('div');
      host.id = 'live-map';
      host.innerHTML = `<div id="leaflet"></div><div class="live-chips" id="live-chips"></div>
        <div class="live-legend"><span><i style="background:#d4ed74"></i>Before/after pair</span><span><i style="background:#ffb454"></i>Single image (2025 aftermath)</span><span><i style="background:#7fd1ff"></i>Has AI finding</span></div>
        <div class="live-note">${S.points.length.toLocaleString()} locations${S.noCoords ? ` · ${S.noCoords} images have no coordinates` : ''}. Click a location to open it and see the photo on the map.</div>`;
      q('.map-surface').appendChild(host);
      m = L.map(q('#leaflet'), { zoomControl: false, attributionControl: false, preferCanvas: true, minZoom: 3, maxZoom: 19 });
      L.control.attribution({ position: 'bottomleft', prefix: false }).addTo(m);
      L.control.scale({ position: 'bottomleft', imperial: false }).addTo(m);
      if (S.mapboxToken) layers.mapbox = { name: 'Mapbox satellite', layer: L.tileLayer(`https://api.mapbox.com/styles/v1/mapbox/satellite-streets-v12/tiles/{z}/{x}/{y}?access_token=${encodeURIComponent(S.mapboxToken)}`, { tileSize: 512, zoomOffset: -1, maxZoom: 19, attribution: '© <a href="https://www.mapbox.com/about/maps/">Mapbox</a> © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }) };
      layers.esri = { name: 'Esri satellite', layer: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Tiles © Esri — Maxar, Earthstar Geographics' }) };
      layers.osm = { name: 'Street map', layer: L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }) };
      order = Object.keys(layers);
      Object.values(layers).forEach((l) => l.layer.on('tileerror', () => { if (!warned) { warned = true; showToast('Basemap tiles did not load (offline, blocked, or bad token). Image locations still work.'); } }));
      setBase(order[0], false);
      markers = L.layerGroup().addTo(m); footprints = L.layerGroup(); rings = L.layerGroup().addTo(m);
      for (const p of S.points) {
        const open = () => viewer.open(p.id);
        L.circleMarker([p.lat, p.lon], { radius: 5, color: col(p), weight: 1, fillColor: col(p), fillOpacity: 0.85 })
          .bindTooltip(`<strong>${esc(p.id)}</strong> · ${esc(p.date)} · ${esc(p.source || '')}${p.paired ? ' · before/after' : ''}`).on('click', open).addTo(markers);
        footprints.addLayer(L.rectangle([[p.s, p.w], [p.n, p.e]], { color: col(p), weight: 1, fillOpacity: 0.08 }).on('click', open));
      }
      m.on('zoomend', syncFootprints);
      const chips = [['All locations', S.points], ['Hurricane Harvey pairs', S.points.filter((p) => p.paired)], ['Hill Country 2025', S.points.filter((p) => !p.paired)]];
      const chipsEl = q('#live-chips');
      chips.forEach(([label, pts], i) => {
        const b = document.createElement('button');
        b.className = 'live-chip' + (i === 0 ? ' active' : ''); b.textContent = `${label} (${pts.length})`;
        b.addEventListener('click', () => { chipsEl.querySelectorAll('.live-chip').forEach((c) => c.classList.toggle('active', c === b)); fit(pts); });
        chipsEl.appendChild(b);
      });
      new ResizeObserver(() => m.invalidateSize()).observe(host);
      requestAnimationFrame(() => { m.invalidateSize(); fit(S.points); draw(); });
    }

    function setBase(key, notify = true) {
      Object.values(layers).forEach((l) => m.removeLayer(l.layer));
      m.addLayer(layers[key].layer); current = key;
      if (notify) showToast(`Basemap: ${layers[key].name}`);
    }
    const toggleBase = () => setBase(order[(order.indexOf(current) + 1) % order.length]);
    const syncFootprints = () => { if (m.getZoom() >= 14 && S.showFootprints) m.addLayer(footprints); else m.removeLayer(footprints); };

    function draw() {
      if (!m) return;
      if (S.showFootprints) m.addLayer(markers); else m.removeLayer(markers);
      syncFootprints(); rings.clearLayers();
      for (const p of S.points) {
        if (S.showFindings && p.ids.some((id) => S.findingIds.has(id))) L.circleMarker([p.lat, p.lon], { radius: 10, color: '#7fd1ff', weight: 2, fill: false, interactive: false }).addTo(rings);
        if (S.selected === p.id) L.circleMarker([p.lat, p.lon], { radius: 14, color: '#ffffff', weight: 2, fill: false, interactive: false }).addTo(rings);
      }
    }

    function fit(pts) {
      if (!pts.length || !m) return;
      m.fitBounds(L.latLngBounds(pts.flatMap((p) => [[p.s, p.w], [p.n, p.e]])), { padding: [30, 30], maxZoom: 16 });
    }
    function clearOverlay() { if (overlay) { m.removeLayer(overlay); overlay = null; } }
    function showOverlay(img) {
      clearOverlay(); if (!m || img.west == null) return;
      const b = [[img.south, img.west], [img.north, img.east]];
      overlay = L.imageOverlay(API.fileUrl(img.image_id), b, { opacity: 0.92, interactive: false }).addTo(m);
      const w = q('#map-stage').clientWidth;
      m.fitBounds(b, { paddingTopLeft: [30, 60], paddingBottomRight: [Math.min(520, w * 0.58) + 30, 30], maxZoom: 18 });
    }
    return { mount, draw, fit, resize: () => m && m.invalidateSize(), fitAll: () => fit(S.points), centerZoom: (f) => (f > 1 ? m.zoomIn() : m.zoomOut()), showOverlay, clearOverlay, toggleBase };
  })();
  const map = window.L ? leafletMap : canvasMap;

  /* ---------------- viewer (before/after + AI) ---------------- */
  const viewer = (() => {
    let el;
    function mount() {
      el = document.createElement('div'); el.id = 'live-viewer'; el.setAttribute('role', 'dialog'); el.setAttribute('aria-label', 'Image viewer');
      q('#map-stage').appendChild(el);
    }
    function close() { el.classList.remove('open'); S.selected = null; map.clearOverlay(); map.draw(); }

    async function open(id) {
      S.selected = id; map.draw();
      el.classList.add('open'); el.innerHTML = '<p class="lv-sub" style="padding:20px">Loading…</p>';
      let before = null, after, paired = true;
      try { ({ before, after } = await API.pair(id)); }
      catch (e) { paired = false; try { after = await API.image(id); } catch (e2) { el.innerHTML = `<button class="lv-close">Close ✕</button><div class="lv-result err" style="margin:30px">${esc(e2.message)}</div>`; q('.lv-close', el).onclick = close; return; } }
      let detail = {}; try { detail = await API.image(after.image_id); } catch (e) { /* optional */ }
      map.showOverlay(after);   // draw the flood photo on the map at its real coordinates
      const tag = (t) => `<span class="cmp-tag ${t === 'l' ? 'l' : 'r'}">`;
      const media = paired
        ? `<div class="cmp"><img class="cmp-base" alt="Before: ${esc(before.image_id)}" src="${API.fileUrl(before.image_id)}"><img class="cmp-top" alt="After: ${esc(after.image_id)}" src="${API.fileUrl(after.image_id)}"><div class="cmp-line"></div>
            ${tag('l')}BEFORE · ${esc(before.acquisition_date)} · ${esc(before.source_agency)}</span>${tag('r')}AFTER · ${esc(after.acquisition_date)} · ${esc(after.source_agency)}</span>
            <input class="cmp-range" type="range" min="0" max="100" value="50" aria-label="Before and after slider"><div class="cmp-fail"></div></div>`
        : `<div class="cmp"><img alt="${esc(after.image_id)}" src="${API.fileUrl(after.image_id)}">${tag('r')}${esc(after.acquisition_date)} · ${esc(after.source_agency)}</span><div class="cmp-fail"></div></div>`;
      el.innerHTML = `<button class="lv-close">Close ✕</button><div>${media}</div>
        <div class="lv-side"><div><h3>${esc(after.locality || after.image_id)}</h3><p class="lv-sub">${paired ? `${esc(before.acquisition_date)} → ${esc(after.acquisition_date)} · ` : ''}${esc(after.image_id)}</p></div>
          <span class="lv-badge" id="lv-badge">Checking SHA-256…</span>
          <div class="lv-facts">
            <div><span>Source</span><strong>${paired ? `${esc(before.source_agency)} → ${esc(after.source_agency)}` : esc(after.source_agency)}</strong></div>
            <div><span>Size</span><strong>${esc(after.width_px)} × ${esc(after.height_px)} px</strong></div>
            <div><span>Event</span><strong>${esc(detail.event_name || '—')}</strong></div>
            <div><span>Resolution</span><strong>${detail.native_resolution_min_m != null ? esc(detail.native_resolution_min_m) + ' m' : '—'}</strong></div>
            <div><span>Bounds (W,S,E,N)</span><strong>${fmt(after.west)}, ${fmt(after.south)}, ${fmt(after.east)}, ${fmt(after.north)}</strong></div>
            <div><span>SHA-256 (after)</span><strong style="font-family:monospace">${esc(String(after.sha256 || '').slice(0, 16))}…</strong></div>
          </div>
          <div class="lv-ai">
            ${paired ? '<button id="lv-change">Compare before / after with Qwen</button>' : ''}
            <div class="lv-ai-row"><input id="lv-q" type="text" maxlength="500" value="Is there flooding visible? Which roads or buildings are affected?" aria-label="Question about the ${paired ? 'after ' : ''}image"><button id="lv-ask" class="secondary">Ask</button></div>
            <div id="lv-out"></div><div class="lv-hist" id="lv-hist"></div>
          </div></div>`;
      q('.lv-close', el).onclick = close;
      const range = q('.cmp-range', el); if (range) range.addEventListener('input', () => q('.cmp', el).style.setProperty('--pos', `${range.value}%`));
      verifyImages(el);
      const out = q('#lv-out', el);
      const run = async (btn, label, fn) => {
        btn.disabled = true; out.innerHTML = `<div class="lv-result">${esc(label)}…</div>`;
        try { out.innerHTML = fn(await (btn.__call())); } catch (e) { out.innerHTML = `<div class="lv-result err">${esc(e.message)}</div>`; }
        btn.disabled = false; refreshFindingIds(); loadHistory(after.image_id, el);
      };
      const ch = q('#lv-change', el);
      if (ch) { ch.__call = () => API.change(after.image_id); ch.onclick = () => run(ch, 'Qwen is comparing the two images', renderChange); }
      const ask = q('#lv-ask', el);
      ask.__call = () => API.ask(after.image_id, q('#lv-q', el).value.trim());
      ask.onclick = () => run(ask, 'Qwen is looking at the image', renderAnswer);
      loadHistory(after.image_id, el);
    }

    function verifyImages(root) {
      const imgs = [...root.querySelectorAll('.cmp img')]; let ok = 0, done = 0; const badge = q('#lv-badge', root);
      const finish = () => {
        if (done < imgs.length) return;
        if (ok === imgs.length) { badge.className = 'lv-badge ok'; badge.textContent = `✓ SHA-256 verified by server (${ok} image${ok > 1 ? 's' : ''})`; }
        else { badge.className = 'lv-badge bad'; badge.textContent = '! Verification failed · image not displayed'; const f = q('.cmp-fail', root); f.style.display = 'grid'; f.textContent = 'Verification failed. The server refused to serve an image that did not match its published SHA-256.'; }
      };
      imgs.forEach((im) => { const mark = (good) => { done++; if (good) ok++; finish(); }; if (im.complete && im.naturalWidth) mark(true); else { im.addEventListener('load', () => mark(true)); im.addEventListener('error', () => mark(false)); } });
    }

    const note = '<div class="lv-hist" style="margin-top:6px">Model-generated, not human-reviewed. Any confidence value is the model’s own estimate, not a measured accuracy.</div>';
    const renderChange = (r) => {
      const x = r.result || {};
      if (x.raw) return `<div class="lv-result"><span class="tag">MODEL-GENERATED · UNREVIEWED</span><div>${esc(x.raw)}</div>${note}</div>`;
      return `<div class="lv-result"><span class="tag">MODEL-GENERATED · UNREVIEWED</span><div><strong>Flooding visible after: ${esc(x.flooding_visible_after)}</strong></div><div>${esc(x.changes)}</div>${x.confidence != null ? `<div class="lv-hist">Model confidence: ${esc(x.confidence)}</div>` : ''}${note}</div>`;
    };
    const renderAnswer = (r) => `<div class="lv-result"><span class="tag">MODEL-GENERATED · UNREVIEWED</span><div>${esc(r.answer)}</div>${note}</div>`;

    async function loadHistory(imageId, root) {
      const h = q('#lv-hist', root); if (!h) return;
      try { const { items } = await API.findings({ image_id: imageId, limit: 3 }); h.textContent = items.length ? `${items.length}${items.length === 3 ? '+' : ''} saved finding(s) for this image · latest: ${items[0].kind} · ${String(items[0].created_at).slice(0, 16).replace('T', ' ')}` : 'No saved findings for this image yet.'; }
      catch (e) { h.textContent = ''; }
    }
    return { mount, open, close };
  })();

  /* ---------------- side panel, controls, findings page ---------------- */
  function renderInfoPanel() {
    const side = q('.info-panel'); if (!side) return;
    const paired = S.points.filter((p) => p.paired).length;
    side.innerHTML = `<div class="panel-header"><span class="panel-eyebrow">ACTIVE DATASET</span><span class="verified-pill"><span>✓</span> Hash-checked</span></div>
      <h2>UTSA Texas<br>Flood Imagery</h2><p class="panel-intro">Aerial photos of Hurricane Harvey (2017) and the Hill Country floods (2025).</p>
      <div class="detail-grid"><div><span class="detail-label">IMAGES</span><strong>${S.health.images.toLocaleString()}</strong></div><div><span class="detail-label">BEFORE / AFTER PAIRS</span><strong>${paired.toLocaleString()} locations</strong></div>
        <div><span class="detail-label">CAPTURE</span><strong>2016 – 2025</strong></div><div><span class="detail-label">SOURCES</span><strong>NOAA · USDA · USCG</strong></div></div>
      <div class="panel-rule"></div><div class="panel-section-title">INTEGRITY CHECK <span class="freshness">On open</span></div>
      <div class="integrity-row"><span class="integrity-icon">✓</span><span><strong>SHA-256 per image</strong><small>Checked against the published hash before display</small></span><span class="integrity-check">✓</span></div>
      <div class="integrity-row"><span class="integrity-icon">!</span><span><strong>Publisher signature</strong><small>Not signed · development data</small></span></div>
      <div class="assurance-note"><span>ⓘ</span><p>Verification confirms the file matches what was published. It doesn’t guarantee imagery is current or reflects real-world conditions.</p></div>
      <button class="panel-link" id="provenance-button">View source &amp; provenance <span>↗</span></button>`;
    q('#provenance-button').addEventListener('click', () => openModal('provenance'));
  }

  function rebind(id, handler) { const el = q(id); if (!el) return; const c = el.cloneNode(true); el.replaceWith(c); c.addEventListener('click', handler); return c; }
  function patchControls() {
    rebind('#zoom-in', () => map.centerZoom(1.6));
    rebind('#zoom-out', () => map.centerZoom(1 / 1.6));
    rebind('#recenter', () => { map.fitAll(); showToast('Map fitted to all locations'); });
    rebind('#imagery-toggle', (ev) => { const on = ev.currentTarget.querySelector('.toggle').classList.toggle('on'); S.showFootprints = on; map.draw(); showToast(on ? 'Image locations shown' : 'Image locations hidden'); });
    rebind('#findings-toggle', (ev) => { const on = ev.currentTarget.querySelector('.toggle').classList.toggle('on'); S.showFindings = on; map.draw(); showToast(on ? 'Highlighting locations with AI findings' : 'Findings highlight off'); });
    const ft = q('#findings-toggle .toggle'); if (ft) ft.classList.add('on');
    rebind('#locate-button', () => showToast('Location is not enabled in this prototype'));
    rebind('#layers-button', () => (map.toggleBase ? map.toggleBase() : showToast('Basemap switching needs the Leaflet map')));
  }

  function patchSetPage() {
    const base = window.setPage;
    window.setPage = function (view) { base(view); if (view === 'map') requestAnimationFrame(() => map.resize()); if (view === 'findings') renderFindings(); };
    const baseDatasets = window.renderDatasets;
    window.renderDatasets = function () {
      baseDatasets();
      const eb = q('#secondary-view .dataset-page-intro .eyebrow'); if (eb) eb.textContent = 'IMAGERY LIBRARY · LIVE CATALOG';
      const note = q('#secondary-view .catalog-note'); if (note) note.innerHTML = '<i></i> Live catalog · download and sync are prototype';
      const sc = q('#secondary-view .sync-copy span'); if (sc) sc.textContent = sc.textContent.replace('prototype queue', 'sync is a prototype');
    };
  }

  const summarize = (f) => {
    if (f.kind === 'change') { try { const x = JSON.parse((f.answer.match(/\{[\s\S]*\}/) || [''])[0]); return `Flooding visible after: ${x.flooding_visible_after}. ${x.changes || ''}`; } catch (e) { /* fall through */ } }
    return f.answer;
  };

  async function renderFindings() {
    const root = q('#secondary-view');
    root.innerHTML = `<div class="empty-card"><div class="empty-icon">◇</div><span class="eyebrow">EXPERIMENTAL · LIVE RESULTS</span><h2>Field findings</h2><p>Answers from Qwen, saved with imagery date and model. Model output is probabilistic, not a confirmed fact.</p></div>
      <form class="live-form" id="ff"><input id="ff-id" placeholder="Image id, e.g. TX001_2017_NOAA" value="${esc(S.selected || 'TX001_2017_NOAA')}" aria-label="Image id"><input id="ff-q" placeholder="Ask a question about this image" value="Is there flooding visible? One sentence." aria-label="Question"><button type="submit">Ask Qwen</button></form>
      <div id="ff-out"></div><div id="ff-list"><p class="lv-sub">Loading findings…</p></div>`;
    q('#ff', root).addEventListener('submit', async (ev) => {
      ev.preventDefault(); const btn = q('button', ev.currentTarget), out = q('#ff-out', root); btn.disabled = true;
      out.innerHTML = '<div class="lv-result">Qwen is looking at the image…</div>';
      try { const r = await API.ask(q('#ff-id', root).value.trim(), q('#ff-q', root).value.trim()); out.innerHTML = `<div class="lv-result"><span class="tag">MODEL-GENERATED · UNREVIEWED</span><div>${esc(r.answer)}</div></div>`; await refreshFindingIds(); fillList(root); }
      catch (e) { out.innerHTML = `<div class="lv-result err">${esc(e.message)}</div>`; }
      btn.disabled = false;
    });
    fillList(root);
  }

  async function fillList(root) {
    const list = q('#ff-list', root); if (!list) return;
    try {
      const { items } = await API.findings({ limit: 100 });
      list.innerHTML = items.length ? items.map((f) => `<div class="dataset-row"><div class="finding-avatar">◇</div><div class="dataset-row-main"><strong>${f.kind === 'change' ? 'Before/after change' : 'Question'} · ${esc(f.image_id)}</strong><span>imagery ${esc(f.imagery_time)} · ${esc(f.model_id)}${f.confidence != null ? ` · model confidence ${esc(f.confidence)}` : ''}</span>${f.question && f.kind === 'qa' ? `<p class="live-answer"><em>${esc(f.question)}</em></p>` : ''}<p class="live-answer">${esc(summarize(f)).slice(0, 320)}</p></div><span class="downloaded-label">Unreviewed</span><button class="row-more" data-open="${esc(f.image_id)}">View</button></div>`).join('')
        + '<div class="detail-callout">Model output is probabilistic, not a confirmed fact. Confidence values are the model’s own estimate.</div>'
        : '<div class="dataset-empty">No findings yet. Ask a question above or open an image on the map.</div>';
      list.querySelectorAll('[data-open]').forEach((b) => b.addEventListener('click', () => { setPage('map'); viewer.open(b.dataset.open); }));
    } catch (e) { list.innerHTML = `<div class="lv-result err">${esc(e.message)}</div>`; }
  }

  boot();
})();
