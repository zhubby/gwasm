(function docsHeartbeat() {
  const newId = () => Math.random().toString(36).slice(2) + Date.now().toString(36);
  let id = newId();
  const ping = (leaving) => {
    const url = '/ping?id=' + encodeURIComponent(id) + (leaving ? '&bye=1' : '');
    try {
      if (navigator.sendBeacon) navigator.sendBeacon(url);
      else fetch(url, { keepalive: true, method: 'POST' });
    } catch (_) {
      // The server watchdog remains the final lifecycle backstop.
    }
  };
  ping(false);
  setInterval(() => ping(false), 1000);
  addEventListener('pageshow', (event) => {
    if (!event.persisted) return;
    id = newId();
    ping(false);
  });
  addEventListener('pagehide', () => ping(true));
})();
