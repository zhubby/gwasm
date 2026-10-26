// Final boot orchestration. Loaded after foundation, cache, Module and input.
(function () {
'use strict';

const host = window.gwasmHost;
const log = host.log;
let wired = false;

host.loadGlue = function loadGlue(candidates) {
  const source = candidates[0];
  if (!source) {
    window.gwasmLoading?.fail('No game build could be loaded.');
    return log('[err] no glue could be loaded — check that gwasm.py finished downloading');
  }
  if (wired) return host.appendGlue(source, candidates);
  wired = true;
  host.state.useJspi = source === 'Gw.jspi.js';
  host.wireInput(Module.canvas);
  host.appendGlue(source, candidates);
};

(async function boot() {
  if (localStorage.getItem('gwasm.log') === 'on') window.gwasmLog(true);
  if (!await window.gwasmLoading.waitForClient()) return;
  host.setLoading('Opening the local game cache…');

  try {
    await host.cache.openPersistent();
  } catch (error) {
    log('[warn] no Cache API; chunks will not survive reload:', error.message);
  }

  host.setLoading('Preparing browser storage…');
  try {
    if (navigator.storage?.persist && !await navigator.storage.persisted()) {
      log(await navigator.storage.persist()
        ? 'storage: persistence granted'
        : '[warn] storage persistence refused — the browser may evict image data');
    }
  } catch (error) {
    log('[warn] storage persistence check failed:', error.message);
  }

  host.setLoading('Reading the game data index…', 'snapshot-chunks.json');
  try {
    const response = await fetch('snapshot-chunks.json');
    if (response.ok) {
      const index = await response.json();
      host.cache.configure(index);
      log('snapshot:', index.size, 'bytes,',
          (index.chunkHashes || []).length, 'chunks of', index.chunkSize);
    } else {
      log(`[warn] no snapshot-chunks.json (HTTP ${response.status})`);
    }
  } catch (error) {
    log('[warn] could not read snapshot-chunks.json:', error.message);
  }

  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const jspi = !isIOS && ('Suspending' in WebAssembly);
  log(`build: ${jspi ? 'JSPI' : 'Asyncify'}` +
      (isIOS ? ' (iOS)' : jspi ? '' : ' (no WebAssembly.Suspending)'));
  host.loadGlue(jspi ? ['Gw.jspi.js', 'Gw.js'] : ['Gw.js', 'Gw.jspi.js']);
})();

})();
