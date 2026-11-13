/* Crystal Geo API client. Same-origin by default (the API serves this app at /app/);
   set localStorage.crystalApi = "http://host:8000" to point at another server. */
(() => {
  let stored = null;
  try {
    const q = new URLSearchParams(location.search).get('api');   // e.g. .../app/?api=http://localhost:8000
    if (q === 'reset') localStorage.removeItem('crystalApi');
    else if (q && /^https?:\/\/[^\s]+$/i.test(q)) localStorage.setItem('crystalApi', q);
    stored = localStorage.getItem('crystalApi');
  } catch (e) { /* storage blocked */ }
  const base = (stored || (location.protocol.startsWith('http') ? location.origin : 'http://localhost:8000')).replace(/\/$/, '');

  async function req(path, opts) {
    const r = await fetch(base + path, opts);
    if (!r.ok) {
      let detail = '';
      try { detail = (await r.json()).detail; } catch (e) { /* not JSON */ }
      throw new Error(typeof detail === 'string' && detail ? detail : `Request failed (HTTP ${r.status})`);
    }
    return r.json();
  }
  const post = (path, body) => req(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const qs = (q) => new URLSearchParams(Object.entries(q).filter(([, v]) => v !== undefined && v !== null && v !== '')).toString();
  const id = encodeURIComponent;

  window.CrystalAPI = {
    base,
    health: () => req('/health'),
    config: () => req('/config'),
    generation: () => req('/generation'),
    images: (q = {}) => req('/images?' + qs(q)),
    async allImages() {
      const out = []; let offset = 0, total = Infinity;
      while (offset < total) {
        const page = await req('/images?' + qs({ limit: 500, offset }));
        total = page.total; out.push(...page.items); offset += page.items.length;
        if (!page.items.length) break;
      }
      return out;
    },
    image: (imageId, full = false) => req(`/images/${id(imageId)}${full ? '?full=true' : ''}`),
    pair: (imageId) => req(`/pairs/${id(imageId)}`),
    fileUrl: (imageId) => `${base}/images/${id(imageId)}/file`,
    ask: (image_id, question) => post('/ai/ask', { image_id, question }),
    change: (image_id) => post('/ai/change', { image_id }),
    findings: (q = {}) => req('/findings?' + qs(q)),
  };
})();
