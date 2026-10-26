// Public Emscripten Module contract and platform services.
(function () {
'use strict';

const host = window.gwasmHost;
const log = host.log;
const cache = host.cache;

// Credentials intentionally never leave this closure and never enter browser
// persistence. A reload must forget them.
let volatileCredentials = null;

function patchEgl(environment) {
  if (!environment || typeof environment.eglCreateContext !== 'function') {
    return log('[warn] no eglCreateContext import — nothing will be presented');
  }
  const createContext = environment.eglCreateContext;
  environment.eglCreateContext = (...args) => {
    const visible = Module.canvas;
    visible.offscreen = new OffscreenCanvas(visible.width, visible.height);
    Module.canvas = visible.offscreen;
    const context = createContext(...args);
    Module.canvas = visible;
    Module.canvas.context = visible.getContext('bitmaprenderer');
    log(`egl context on offscreen ${visible.width}x${visible.height}`);
    return context;
  };

  const swap = environment.eglSwapBuffers;
  let firstFrame = true;
  environment.eglSwapBuffers = (...args) => {
    const result = swap(...args);
    if (result && Module.canvas.offscreen && Module.canvas.context) {
      Module.canvas.context.transferFromImageBitmap(
        Module.canvas.offscreen.transferToImageBitmap());
    }
    if (firstFrame) {
      firstFrame = false;
      log('first frame presented');
    }
    return result;
  };

  const setSize = environment.emscripten_set_canvas_element_size;
  if (typeof setSize === 'function') {
    environment.emscripten_set_canvas_element_size = (target, width, height) => {
      const result = setSize(target, width, height);
      if (result === 0 && Module.canvas.offscreen) {
        Module.canvas.offscreen.width = width;
        Module.canvas.offscreen.height = height;
      }
      return result;
    };
  }
}

Module = {
  canvas: document.getElementById('canvas'),
  print: (text) => log(text),
  printErr: (text) => log('[err]', text),

  instantiateWasm(imports, success) {
    patchEgl(imports.env);
    const url = host.state.useJspi ? 'Gw.jspi.wasm' : 'Gw.wasm';
    (async () => {
      try {
        let result;
        try {
          result = await WebAssembly.instantiateStreaming(fetch(url), imports);
        } catch (error) {
          log('[warn] streaming instantiate failed, falling back:', error.message);
          result = await WebAssembly.instantiate(
            await (await fetch(url)).arrayBuffer(), imports);
        }
        success(result.instance, result.module);
      } catch (error) {
        log('[err] wasm instantiation failed:', error.message);
        window.gwasmLoading?.fail('Could not start the game.');
      }
    })();
    return {};
  },

  locateFile: (path) =>
    (host.state.useJspi && path === 'Gw.wasm') ? 'Gw.jspi.wasm' : path,

  image: {
    _handles: new Map(),
    _next: 1,

    open(path) {
      const basename = String(path).replaceAll(String.fromCharCode(92), '/')
        .split('/').pop().toLowerCase();
      if (basename !== 'gw.snapshot') {
        log(`image.open ${path} -> 0 (not in the image)`);
        return 0;
      }
      const handle = this._next++;
      this._handles.set(handle, { path });
      log('image.open', path, '-> handle', handle);
      return handle;
    },

    fileSize(handle) {
      if (!this._handles.has(handle)) {
        log('[warn] image.fileSize on unknown handle', handle);
        return 0;
      }
      const size = cache.size();
      if (size === null) {
        log('[warn] image.fileSize but no snapshot size known');
        return 0;
      }
      return size;
    },

    close(handle) {
      log('image.close', handle);
      this._handles.delete(handle);
    },

    async readAsync(imageId, offset, _unused, buffer, bytes) {
      if (!this._handles.has(imageId)) {
        throw new Error('bad image handle ' + imageId);
      }
      const data = await cache.read(offset, bytes);
      Module.HEAPU8.set(data, buffer);
    },

    isCached(_handle, offset, size) {
      return cache.isCached(offset, size);
    },

    async cacheAsync(_handle, offset, size, progress) {
      await cache.cacheRange(offset, size, progress);
    },
  },

  socket: {
    connect(destination) {
      log('socket.connect', destination);
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
      const websocket = new WebSocket(
        `${scheme}://${location.host}/?dest=${encodeURIComponent(destination)}`);
      websocket.binaryType = 'arraybuffer';
      const socket = {
        onopen: null,
        onclose: null,
        onmessage: null,
        send: (data) => websocket.send(data),
        close: () => websocket.close(),
      };
      websocket.onopen = () => socket.onopen && socket.onopen();
      websocket.onclose = () => socket.onclose && socket.onclose();
      websocket.onerror = () => log('socket error (is gwasm.py still running?)');
      websocket.onmessage = (event) =>
        socket.onmessage && socket.onmessage(new Uint8Array(event.data));
      return socket;
    },
  },

  dns: {
    async resolve(name) {
      log('dns.resolve', name);
      const response = await fetch(`/dns?name=${encodeURIComponent(name)}`);
      if (!response.ok) {
        throw new Error('dns lookup failed: ' + response.status);
      }
      return (await response.text()).trim();
    },
  },

  shop: host.stub('shop'),
  adProvider: host.stub('adProvider'),
  browser: host.stub('browser'),
  events: host.stub('events'),
  ageSignals: host.stub('ageSignals'),

  secureStorage: {
    async getCredentials() {
      if (!volatileCredentials) {
        log('secureStorage: no credentials in this page — the module should prompt');
        throw new Error('no stored credentials');
      }
      log('secureStorage: returning credentials held in memory');
      return { ...volatileCredentials };
    },
    async storeCredentials(username, password) {
      volatileCredentials = { username, password };
      log('secureStorage: stored credentials in memory for this page only');
    },
    async clearCredentials() {
      volatileCredentials = null;
      log('secureStorage: cleared from memory');
    },
  },

  login: {
    hasProvider(name) {
      log(`login.hasProvider(${name}) -> false (no federated auth in this harness)`);
      return false;
    },
  },

  getPatchMode: async () =>
    localStorage.getItem('gwasm.patchMode') || 'onDemand',

  setStartupProgress(stage, a, b, c, d) {
    log(`[startup] ${stage}`,
        [a, b, c, d].filter((value) => value !== undefined).join(' '));
    const loading = window.gwasmLoading;
    if (!loading) return;
    const normalized = String(stage || '').toLowerCase();
    if (normalized === 'complete') return loading.done();
    if (normalized === 'downloading' && typeof a === 'number') {
      const eta = d > 0 ? `${Math.ceil(d / 60)} min remaining` : '';
      const rate = c > 0 ? `${(c / 1048576).toFixed(1)} MB/s` : '';
      return loading.set('Downloading game data…', a / 100,
        [rate, eta].filter(Boolean).join(' · '));
    }
    loading.set(host.startupLabels[normalized] || 'Loading…', null);
  },

  handleFatalReadError: () =>
    log('[err] module reported a fatal read error'),
  setBuildInfo: (info) =>
    log(`build info: program=${info.programId} build=${info.buildId}`),
  isMobile: /Android|iPhone|iPad|iPod|Mobile|Opera Mini|IEMobile/i
    .test(navigator.userAgent) || navigator.maxTouchPoints > 0,
  requestFullScreen: () => Module.canvas.requestFullscreen?.(),
  requestFullscreen: () => Module.canvas.requestFullscreen?.(),
  onRuntimeInitialized() { log('runtime initialised'); },
};

host.appendGlue = (source, candidates) => {
  host.state.useJspi = source === 'Gw.jspi.js';
  log('loading', source,
      `(wasm: ${host.state.useJspi ? 'Gw.jspi.wasm' : 'Gw.wasm'}) ...`);
  const script = document.createElement('script');
  script.src = source;
  script.onerror = () => {
    log(`[warn] ${source} not available`);
    if (candidates) host.loadGlue(candidates.slice(1));
  };
  document.body.appendChild(script);
};

})();
