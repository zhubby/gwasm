// Canvas focus, touch translation, pointer lock, audio, and OSK wiring.
(function () {
'use strict';

const host = window.gwasmHost;
const log = host.log;

host.wireInput = function wireInput(canvas) {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  log(`canvas ${canvas.width}x${canvas.height}`);

  canvas.focus();
  canvas.addEventListener('pointerdown', () => {
    if (!Module.oskIsActive && document.activeElement !== canvas) canvas.focus();
  }, true);

  if (location.port) {
    const originalOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      try {
        const parsed = new URL(url, location.href);
        if (parsed.hostname === location.hostname &&
            parsed.port !== location.port &&
            (parsed.port === '' || parsed.port === '80' || parsed.port === '443')) {
          parsed.protocol = location.protocol;
          parsed.port = location.port;
          log(`api: ${method} ${parsed.pathname}`);
          return originalOpen.call(this, method, parsed.toString(), ...rest);
        }
      } catch (error) {
        // Leave non-URL values unchanged.
      }
      return originalOpen.call(this, method, url, ...rest);
    };
  }

  const resumeAudio = () => {
    const context = Module.SDL2?.audioContext || Module.audioContext;
    if (context && context.state === 'suspended') {
      context.resume().then(() => log('audio: resumed')).catch(() => {});
    }
  };
  for (const event of ['pointerdown', 'keydown']) {
    window.addEventListener(event, resumeAudio, true);
  }

  let touchMode = localStorage.getItem('gwasm.touchMode') || 'dbltap';
  const DOUBLE_MS = 400;
  const DOUBLE_PIXELS = 10;
  let lastClick = null;
  let pendingTap = null;
  let touchId = 0;
  let activeTouch = null;

  window.gwasmPatchMode = (mode) => {
    if (!['onDemand', 'preload'].includes(mode)) {
      return log(`[warn] unknown patch mode ${mode}`);
    }
    localStorage.setItem('gwasm.patchMode', mode);
    log(`patch mode: ${mode} (reload to apply)`);
    return mode;
  };

  window.gwasmTouchMode = (mode) => {
    if (!['dbltap', 'translate', 'augment', 'off'].includes(mode)) {
      return log(`[warn] unknown mode ${mode}`);
    }
    touchMode = mode;
    localStorage.setItem('gwasm.touchMode', mode);
    log(`touch mode: ${mode}`);
    return mode;
  };

  const makeTouch = (x, y, identifier) => new Touch({
    identifier,
    target: canvas,
    clientX: x,
    clientY: y,
    pageX: x,
    pageY: y,
    screenX: x,
    screenY: y,
    radiusX: 5,
    radiusY: 5,
    rotationAngle: 0,
    force: 1,
  });

  const sendTouch = (type, touch) => {
    const ended = type === 'touchend' || type === 'touchcancel';
    canvas.dispatchEvent(new TouchEvent(type, {
      bubbles: true,
      cancelable: true,
      composed: true,
      touches: ended ? [] : [touch],
      targetTouches: ended ? [] : [touch],
      changedTouches: [touch],
    }));
  };

  let tapTimers = [];
  const cancelTaps = () => {
    tapTimers.forEach(clearTimeout);
    tapTimers = [];
  };
  const tapAt = (x, y, delay) => tapTimers.push(setTimeout(() => {
    const touch = makeTouch(x, y, ++touchId);
    sendTouch('touchstart', touch);
    tapTimers.push(setTimeout(() => sendTouch('touchend', touch), 30));
  }, delay));

  canvas.addEventListener('mousedown', (event) => {
    if (touchMode === 'off' || event.button !== 0) return;
    if (touchMode === 'dbltap') {
      cancelTaps();
      const now = performance.now();
      const nearby = lastClick && now - lastClick.time < DOUBLE_MS &&
        Math.hypot(event.clientX - lastClick.x,
                   event.clientY - lastClick.y) < DOUBLE_PIXELS;
      if (nearby) {
        lastClick = null;
        pendingTap = { x: event.clientX, y: event.clientY };
      } else {
        lastClick = { time: now, x: event.clientX, y: event.clientY };
      }
      return;
    }
    activeTouch = makeTouch(event.clientX, event.clientY, ++touchId);
    sendTouch('touchstart', activeTouch);
    if (touchMode === 'translate') event.stopImmediatePropagation();
  }, true);

  canvas.addEventListener('mousemove', (event) => {
    if (touchMode === 'off' || touchMode === 'dbltap' || !activeTouch) return;
    activeTouch = makeTouch(
      event.clientX, event.clientY, activeTouch.identifier);
    sendTouch('touchmove', activeTouch);
    if (touchMode === 'translate') event.stopImmediatePropagation();
  }, true);

  canvas.addEventListener('mouseup', (event) => {
    if (touchMode === 'dbltap') {
      if (event.button !== 0 || !pendingTap) return;
      const { x, y } = pendingTap;
      pendingTap = null;
      tapAt(x, y, 20);
      tapAt(x, y, 100);
      return;
    }
    if (touchMode === 'off' || event.button !== 0 || !activeTouch) return;
    const touch = makeTouch(event.clientX, event.clientY, activeTouch.identifier);
    activeTouch = null;
    sendTouch('touchend', touch);
    if (touchMode === 'translate') event.stopImmediatePropagation();
  }, true);

  for (const event of ['mouseleave', 'blur']) {
    canvas.addEventListener(event, () => {
      if (!activeTouch) return;
      const touch = activeTouch;
      activeTouch = null;
      sendTouch('touchcancel', touch);
    }, true);
  }

  window.gwasmTap = (x, y) => {
    const touch = makeTouch(x, y, ++touchId);
    sendTouch('touchstart', touch);
    setTimeout(() => sendTouch('touchend', touch), 40);
  };
  window.gwasmDoubleTap = (x, y) => {
    window.gwasmTap(x, y);
    setTimeout(() => window.gwasmTap(x, y), 120);
  };
  log(`touch mode: ${touchMode} (gwasmTouchMode('off') to disable)`);

  const DRAG_PIXELS = 4;
  let lockEnabled = localStorage.getItem('gwasm.pointerLock') !== 'off';
  let virtualCursor = null;
  let rightButton = null;

  window.gwasmPointerLock = (on) => {
    lockEnabled = !!on;
    localStorage.setItem('gwasm.pointerLock', on ? 'on' : 'off');
    if (!on && document.pointerLockElement === canvas) {
      document.exitPointerLock();
    }
    log(`pointer lock: ${on ? 'enabled' : 'disabled'}`);
    return lockEnabled;
  };

  canvas.addEventListener('mousedown', (event) => {
    if (event.button === 2) {
      rightButton = { x: event.clientX, y: event.clientY, locked: false };
    }
  }, true);

  canvas.addEventListener('mousemove', (event) => {
    if (lockEnabled && rightButton && !rightButton.locked && event.isTrusted &&
        Math.hypot(event.clientX - rightButton.x,
                   event.clientY - rightButton.y) > DRAG_PIXELS) {
      rightButton.locked = true;
      virtualCursor = { x: event.clientX, y: event.clientY };
      if (document.pointerLockElement !== canvas) {
        try {
          canvas.requestPointerLock();
        } catch (error) {
          log('[warn] pointer lock refused:', error.message);
        }
      }
    }
    if (document.pointerLockElement !== canvas ||
        !virtualCursor || !event.isTrusted) return;
    event.stopImmediatePropagation();
    virtualCursor.x += event.movementX;
    virtualCursor.y += event.movementY;
    canvas.dispatchEvent(new MouseEvent('mousemove', {
      bubbles: true,
      cancelable: true,
      composed: true,
      clientX: virtualCursor.x,
      clientY: virtualCursor.y,
      screenX: virtualCursor.x,
      screenY: virtualCursor.y,
      movementX: event.movementX,
      movementY: event.movementY,
      buttons: event.buttons,
      button: event.button,
      ctrlKey: event.ctrlKey,
      shiftKey: event.shiftKey,
      altKey: event.altKey,
      metaKey: event.metaKey,
    }));
  }, true);

  const releaseLock = () => {
    virtualCursor = null;
    rightButton = null;
    if (document.pointerLockElement === canvas) document.exitPointerLock();
  };
  canvas.addEventListener('mouseup', (event) => {
    if (event.button === 2) releaseLock();
  }, true);
  window.addEventListener('blur', releaseLock);
  document.addEventListener('pointerlockerror', () =>
    log('[warn] pointer lock failed (needs a user gesture, and a focused document)'));
  canvas.addEventListener('contextmenu', (event) => {
    if (!event.shiftKey) event.preventDefault();
  });

  Module.oskInput = {
    text: document.getElementById('osk-input-text'),
    email: document.getElementById('osk-input-email'),
    password: document.getElementById('osk-input-password'),
    number: document.getElementById('osk-input-number'),
    multiline: document.getElementById('osk-input-multiline'),
  };
  Module.oskIsModal = Module.isMobile;
  for (const type in Module.oskInput) {
    const element = Module.oskInput[type];
    if (!element) {
      log(`[warn] missing OSK element for "${type}"`);
      continue;
    }
    element.addEventListener('focus', () => {
      if (Module.oskActiveInput !== element) element.blur();
    });
    if (Module.oskIsModal) {
      element.parentElement.classList.add('osk-input-container-modal');
    }
  }
  log(`osk: ${Object.keys(Module.oskInput).length} fields, modal=${Module.oskIsModal}`);
};

})();
