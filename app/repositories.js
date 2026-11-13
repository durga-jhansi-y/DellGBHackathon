/* Deterministic prototype repositories. Replace these adapters with Flutter/Dart implementations when the mobile shell is introduced. */
(() => {
  const dataset = {
    id: 'north-cascades', name: 'North Cascades National Park', generation_id: 'NCNP-2024.08.2',
    capture_start: '2024-08-14', capture_end: '2024-08-28', publisher: 'USGS Earth Explorer',
    license: 'USGS public domain sample', resolution: '0.6 m / pixel', size: '2.4 GB', tile_count: 1248,
    verification: 'verified', offline: true
  };
  const findings = [{ id: 'sample-01', title: 'Possible slope change', confidence: 0.87, uncertainty: 'Moderate confidence',
    imagery_time: '2024-08-21', model_version: 'Demo model 0.1', reviewed_by_human: false, generation_id: dataset.generation_id }];
  const delay = (value) => Promise.resolve(value);
  class DatasetRepository { listInstalled() { return delay([dataset]); } getActive() { return delay(dataset); } }
  class TileRepository { async getVisibleTiles(view) { return { generation_id: view.generation_id, tiles: [], status: 'sample-only' }; } }
  class RecognitionRepository { listFindings() { return delay(findings); } ask() { return delay({ status: 'experimental', message: 'Recognition service is not connected in this prototype.' }); } }
  class TrustRepository { getStatus() { return delay({ generation_id: dataset.generation_id, status: dataset.verification, verified_tiles: dataset.tile_count }); } }
  window.CrystalGeoRepositories = { DatasetRepository, TileRepository, RecognitionRepository, TrustRepository, fixtures: { dataset, findings } };
})();
