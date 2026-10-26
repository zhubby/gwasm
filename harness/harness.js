// Shared foundation for the ordered classic host scripts.
//
// Module MUST be var: Emscripten glue declares the same global with var and
// adopts this object. A const or let declaration would collide at parse time.
var Module;

(function () {
'use strict';

const LOG_LINES = 400;
const lines = [];

function log(...args) {
  console.log(...args);
  lines.push(args.join(' '));
  if (lines.length > LOG_LINES) lines.splice(0, lines.length - LOG_LINES);
  const element = document.getElementById('log');
  if (element && element.style.display !== 'none') {
    element.textContent = lines.join('\n');
    element.scrollTop = element.scrollHeight;
  }
}

window.gwasmLog = (on = true) => {
  const element = document.getElementById('log');
  element.style.display = on ? 'block' : 'none';
  if (on) {
    element.textContent = lines.join('\n');
    element.scrollTop = element.scrollHeight;
  }
  localStorage.setItem('gwasm.log', on ? 'on' : 'off');
  return on;
};

const ASYNC_METHODS = new Set([
  'adProvider.showInterstitial', 'ageSignals.check',
  'shop.initialize', 'shop.inAppPurchase',
]);

function stub(name) {
  return new Proxy({}, {
    get: (_, key) => (...args) => {
      const method = `${name}.${String(key)}`;
      log('[stub]', method, args.length ? `(${args.length} args)` : '');
      return ASYNC_METHODS.has(method) ? Promise.resolve(undefined) : undefined;
    },
    has: () => true,
  });
}

window.gwasmHost = {
  log,
  stub,
  state: { useJspi: false },
  startupLabels: {
    connecting: 'Connecting…',
    downloading: 'Downloading game data…',
    decompressing: 'Decompressing…',
    loading: 'Loading…',
  },
};

})();
