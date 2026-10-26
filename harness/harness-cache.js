// Snapshot range fetching and the two-tier chunk cache.
(function () {
'use strict';

const host = window.gwasmHost;
const log = host.log;
const SNAPSHOT_URL = 'Gw.snapshot';
const MEMORY_LIMIT = 256 * 1024 * 1024;

let snapshotSize = null;
let chunkSize = 262144;
let chunkHashes = [];
let persistentStore = null;
let memoryBytes = 0;
let burstBytes = 0;
let burstTimer = null;

const memory = new Map();
const storedHashes = new Set();
const inflight = new Map();
const stats = {
  reads: 0, bytes: 0, fromMemory: 0, fromStore: 0,
  fromNetwork: 0, coalesced: 0,
};

const hashOfKey = (url) => url.split('/').pop();
const chunkKey = (index) =>
  `/__chunk__/${chunkHashes[index] || 'idx-' + index}`;
const chunkRange = (offset, size) => [
  Math.floor(offset / chunkSize),
  Math.floor((offset + size - 1) / chunkSize),
];

function touch(index) {
  const buffer = memory.get(index);
  if (buffer !== undefined) {
    memory.delete(index);
    memory.set(index, buffer);
  }
  return buffer;
}

function put(index, buffer) {
  if (memory.has(index)) return;
  memory.set(index, buffer);
  memoryBytes += buffer.length;
  while (memoryBytes > MEMORY_LIMIT && memory.size > 1) {
    const oldest = memory.keys().next().value;
    memoryBytes -= memory.get(oldest).length;
    memory.delete(oldest);
  }
}

function chunkBytes(index) {
  const resident = touch(index);
  if (resident !== undefined) {
    stats.fromMemory++;
    return Promise.resolve(resident);
  }
  const pending = inflight.get(index);
  if (pending) {
    stats.coalesced++;
    return pending;
  }

  const request = (async () => {
    if (persistentStore) {
      try {
        const response = await persistentStore.match(chunkKey(index));
        if (response) {
          const buffer = new Uint8Array(await response.arrayBuffer());
          put(index, buffer);
          stats.fromStore++;
          return buffer;
        }
      } catch (error) {
        log('[warn] chunk cache read failed:', error.message);
      }
    }

    const start = index * chunkSize;
    const end = Math.min(start + chunkSize, snapshotSize) - 1;
    const response = await fetch(SNAPSHOT_URL, {
      headers: { Range: `bytes=${start}-${end}` },
    });
    if (!response.ok && response.status !== 206) {
      throw new Error(`chunk ${index}: HTTP ${response.status}`);
    }
    const buffer = new Uint8Array(await response.arrayBuffer());
    put(index, buffer);
    stats.fromNetwork++;

    if (persistentStore) {
      const hash = hashOfKey(chunkKey(index));
      storedHashes.add(hash);
      persistentStore.put(chunkKey(index), new Response(buffer))
        .catch(() => storedHashes.delete(hash));
    }
    return buffer;
  })();

  inflight.set(index, request);
  request.then(() => inflight.delete(index), () => inflight.delete(index));
  return request;
}

async function fetchChunks(first, last, progress) {
  for (let index = first; index <= last; index++) {
    const buffer = await chunkBytes(index);
    if (progress) progress(buffer.length);
  }
}

function readResident(offset, size) {
  const output = new Uint8Array(size);
  let position = offset;
  let written = 0;
  while (written < size) {
    const index = Math.floor(position / chunkSize);
    const buffer = touch(index);
    if (buffer === undefined) return null;
    const innerOffset = position - index * chunkSize;
    const take = Math.min(size - written, buffer.length - innerOffset);
    if (take <= 0) return null;
    output.set(buffer.subarray(innerOffset, innerOffset + take), written);
    written += take;
    position += take;
  }
  return output;
}

async function read(offset, size) {
  let data = readResident(offset, size);
  if (data === null) {
    const [first, last] = chunkRange(offset, size);
    await fetchChunks(first, last);
    data = readResident(offset, size);
  }
  if (data === null || data.length !== size) {
    throw new Error(`image read ${offset}+${size}: assembled ${data && data.length}`);
  }
  stats.reads++;
  stats.bytes += size;
  burstBytes += size;
  clearTimeout(burstTimer);
  burstTimer = setTimeout(() => {
    if (burstBytes > 4 * 1024 * 1024) {
      log(`image: read ${(burstBytes / 1048576).toFixed(1)}MB (mem ${stats.fromMemory}, ` +
          `store ${stats.fromStore}, net ${stats.fromNetwork} chunks)`);
    }
    burstBytes = 0;
  }, 400);
  return data;
}

async function cacheRange(offset, size, progress) {
  const [first, last] = chunkRange(offset, size);
  await fetchChunks(first, last, (bytes) => {
    try { progress(bytes); } catch (error) { log('[cache progress]', error); }
  });
}

function isCached(offset, size) {
  const [first, last] = chunkRange(offset, size);
  for (let index = first; index <= last; index++) {
    if (!memory.has(index) &&
        !storedHashes.has(hashOfKey(chunkKey(index)))) return 0;
  }
  return 1;
}

async function openPersistent() {
  if (!window.caches) return;
  persistentStore = await caches.open('gwasm.chunks.v1');
  for (const request of await persistentStore.keys()) {
    storedHashes.add(hashOfKey(request.url));
  }
  if (storedHashes.size) {
    log(`chunk store: ${storedHashes.size} chunks already resident`);
  }
}

function configure(index) {
  snapshotSize = index.size;
  if (index.chunkSize) chunkSize = index.chunkSize;
  chunkHashes = index.chunkHashes || [];
}

window.gwasmEvictMemory = () => {
  const count = memory.size;
  memory.clear();
  memoryBytes = 0;
  return count;
};

window.gwasmStats = () => {
  const result = {
    reads: stats.reads,
    readMB: +(stats.bytes / 1048576).toFixed(1),
    chunksFromMemory: stats.fromMemory,
    chunksFromCacheStore: stats.fromStore,
    chunksFromNetwork: stats.fromNetwork,
    chunksCoalesced: stats.coalesced,
    memoryCacheMB: +(memoryBytes / 1048576).toFixed(1),
    memoryCacheChunks: memory.size,
  };
  console.table ? console.table(result) : console.log(result);
  return result;
};

host.cache = {
  SNAPSHOT_URL,
  cacheRange,
  configure,
  isCached,
  openPersistent,
  read,
  size: () => snapshotSize,
};

})();
