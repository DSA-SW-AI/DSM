/* ===================================================================
   Defence Space Administration (DSA) - DSM Progressive Web App Service Worker
   Cache Version: dsm-kiosk-v2.0.0
   Features:
     - Full offline support with Background Sync for clock-in/clock-out
     - IndexedDB-backed offline scan queue
     - Smart cache strategies per asset type
   =================================================================== */

const CACHE_NAME = 'dsm-kiosk-v2.1.0';
const OFFLINE_URL = '/offline';
const SYNC_TAG = 'parade-clock-sync';
const IDB_NAME = 'dsm-offline-queue';
const IDB_STORE = 'scan_queue';

// Core assets to pre-cache immediately during installation
const PRECACHE_ASSETS = [
  '/',
  '/clock-in',
  '/kiosk',
  '/attendance',
  '/offline',
  '/static/manifest.json',
  '/static/manifest-kiosk.json',
  '/static/favicon.ico',
  '/static/images/dsa_logo.png',
  '/static/images/dsa2.png',
  '/static/images/icons/icon-192x192.png',
  '/static/images/icons/icon-512x512.png',
  '/static/images/icons/icon-maskable-192x192.png',
  '/static/css/dashboard.css',
  '/static/js/pwa.js',
  '/static/js/jsqr.js',
  'https://cdnjs.cloudflare.com/ajax/libs/remixicon/3.5.0/remixicon.css',
  'https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap'
];

// =============================================================================
// IndexedDB helpers (used inside the Service Worker)
// =============================================================================
function openIDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, 2);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(IDB_STORE)) {
        const store = db.createObjectStore(IDB_STORE, { keyPath: 'id', autoIncrement: true });
        store.createIndex('status', 'status', { unique: false });
        store.createIndex('timestamp', 'timestamp', { unique: false });
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

function idbGetAll(db) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, 'readonly');
    const store = tx.objectStore(IDB_STORE);
    const req = store.index('status').getAll('pending');
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

function idbUpdate(db, id, patch) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, 'readwrite');
    const store = tx.objectStore(IDB_STORE);
    const getReq = store.get(id);
    getReq.onsuccess = () => {
      const record = getReq.result;
      if (record) { Object.assign(record, patch); store.put(record); }
      resolve();
    };
    getReq.onerror = () => reject(getReq.error);
  });
}

// 1. Install Event: Pre-cache static shell & offline fallback
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[PWA SW] Pre-caching core application shell & offline assets');
      // Use Promise.allSettled to ensure that one missing font/CDN file doesn't fail the whole install
      return Promise.allSettled(
        PRECACHE_ASSETS.map((url) =>
          cache.add(new Request(url, { cache: 'reload' })).catch((err) => {
            console.warn(`[PWA SW] Failed to cache ${url}:`, err);
          })
        )
      );
    }).then(() => self.skipWaiting())
  );
});

// 2. Activate Event: Clean up outdated cache versions & claim clients
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cache) => {
          if (cache !== CACHE_NAME) {
            console.log('[PWA SW] Deleting obsolete cache:', cache);
            return caches.delete(cache);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// 3. Fetch Event: Intelligent multi-tier caching strategy
self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);

  // Ignore socket.io, websocket, and real-time polling streams
  if (url.pathname.startsWith('/socket.io/') || url.pathname.includes('socket')) {
    return;
  }

  // ── Intercept offline clock-in / clock-out POST requests ─────────────────────
  if (request.method === 'POST' &&
    (url.pathname === '/api/parade/clock-in' || url.pathname === '/api/parade/clock-out')) {
    event.respondWith(handleOfflineClockPost(request, url.pathname));
    return;
  }

  // Ignore other non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // Strategy A: HTML Navigation Requests -> Network First with Offline Page Fallback
  if (request.mode === 'navigate' || request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(
      fetch(request)
        .then((networkResponse) => {
          // Clone and cache the successfully loaded page
          if (networkResponse && networkResponse.status === 200) {
            const responseClone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
          }
          return networkResponse;
        })
        .catch(async () => {
          // If network is down, try matching previously cached page
          const cachedResponse = await caches.match(request);
          if (cachedResponse) return cachedResponse;
          // For kiosk URLs, serve the cached kiosk page
          const kioskUrls = ['/clock-in', '/kiosk', '/attendance', '/clockin', '/parade-kiosk'];
          if (kioskUrls.some(k => url.pathname.startsWith(k))) {
            const kioskCache = await caches.match('/clock-in');
            if (kioskCache) return kioskCache;
          }
          // Otherwise, serve the dedicated offline page
          const offlineFallback = await caches.match(OFFLINE_URL);
          return offlineFallback || new Response('<h1>Offline</h1><p>Please check your network connection.</p>', {
            headers: { 'Content-Type': 'text/html' }
          });
        })
    );
    return;
  }

  // Strategy B: Static Assets (CSS, JS, Images, Fonts) -> Stale-While-Revalidate
  const isStaticAsset =
    url.pathname.startsWith('/static/') ||
    url.hostname.includes('cdn.jsdelivr.net') ||
    url.hostname.includes('cdnjs.cloudflare.com') ||
    url.hostname.includes('fonts.googleapis.com') ||
    url.hostname.includes('fonts.gstatic.com');

  if (isStaticAsset) {
    event.respondWith(
      caches.match(request).then((cachedResponse) => {
        const fetchPromise = fetch(request)
          .then((networkResponse) => {
            if (networkResponse && networkResponse.status === 200) {
              const responseClone = networkResponse.clone();
              caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
            }
            return networkResponse;
          })
          .catch(() => cachedResponse);

        return cachedResponse || fetchPromise;
      })
    );
    return;
  }

  // Strategy C: Default -> Network with Cache Fallback
  event.respondWith(
    fetch(request)
      .then((networkResponse) => networkResponse)
      .catch(() => caches.match(request))
  );
});

// =============================================================================
// Offline POST Handler: queues scan to IndexedDB when network unavailable
// =============================================================================
async function handleOfflineClockPost(request, endpoint) {
  let body = {};
  try { body = await request.clone().json(); } catch (e) { /* ignore */ }

  try {
    // Try the network first
    const networkResponse = await fetch(request);
    return networkResponse;
  } catch (networkError) {
    // Network is offline – store in IndexedDB queue
    console.log('[SW] Offline. Queuing scan:', endpoint);
    try {
      const db = await openIDB();
      const bodyForQueue = { ...body };
      const snapshotData = bodyForQueue.snapshot || null;
      delete bodyForQueue.snapshot; // keep IDB lean

      await new Promise((resolve, reject) => {
        const tx = db.transaction(IDB_STORE, 'readwrite');
        const store = tx.objectStore(IDB_STORE);
        const req = store.add({
          endpoint,
          body: bodyForQueue,
          snapshot: snapshotData,
          timestamp: Date.now(),
          status: 'pending',
          retries: 0
        });
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });

      // Register a Background Sync if the browser supports it
      if (self.registration && 'sync' in self.registration) {
        await self.registration.sync.register(SYNC_TAG);
      }

      // Notify all open kiosk pages so they can show the offline queued banner
      const allClients = await self.clients.matchAll({ type: 'window' });
      allClients.forEach(client => {
        client.postMessage({
          type: 'SCAN_QUEUED_OFFLINE',
          endpoint,
          barcode: body.barcode || body.code || '',
          timestamp: Date.now()
        });
      });

      // Return synthetic success so the UI can render the "queued" state
      return new Response(JSON.stringify({
        status: 'queued_offline',
        message: 'No network. Scan saved locally — will sync automatically when back online.',
        offline: true,
        barcode: body.barcode || body.code || ''
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      });
    } catch (idbError) {
      console.error('[SW] Failed to queue offline scan:', idbError);
      return new Response(JSON.stringify({
        status: 'error',
        message: 'Offline and local queue unavailable. Please try again when connected.'
      }), { status: 503, headers: { 'Content-Type': 'application/json' } });
    }
  }
}

// =============================================================================
// 4. Background Sync: replay offline queue when connection restored
// =============================================================================
self.addEventListener('sync', (event) => {
  if (event.tag === SYNC_TAG) {
    console.log('[SW] Background Sync fired: replaying offline scan queue');
    event.waitUntil(replayOfflineQueue());
  }
});

async function replayOfflineQueue() {
  let db;
  try { db = await openIDB(); } catch (e) {
    console.error('[SW] Cannot open IDB for sync:', e); return;
  }

  const pending = await idbGetAll(db);
  console.log(`[SW] ${pending.length} pending scan(s) to sync`);

  for (const record of pending) {
    if ((record.retries || 0) >= 10) {
      await idbUpdate(db, record.id, { status: 'failed', last_error: 'Max retries exceeded' });
      continue;
    }
    try {
      const response = await fetch(record.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(record.body)
      });

      if (response.ok) {
        const data = await response.json();
        await idbUpdate(db, record.id, { status: 'synced', synced_at: Date.now() });
        console.log(`[SW] Synced offline scan ${record.id}:`, data.status);

        const allClients = await self.clients.matchAll({ type: 'window' });
        allClients.forEach(client => {
          client.postMessage({
            type: 'SCAN_SYNCED',
            id: record.id,
            endpoint: record.endpoint,
            barcode: record.body.barcode || '',
            serverResponse: data
          });
        });
      } else {
        const data = await response.json().catch(() => ({}));
        const isFinal = response.status === 404;
        await idbUpdate(db, record.id, {
          status: isFinal ? 'failed' : 'pending',
          last_error: `Server ${response.status}: ${data.message}`,
          retries: (record.retries || 0) + 1
        });
      }
    } catch (err) {
      await idbUpdate(db, record.id, {
        last_error: err.message,
        retries: (record.retries || 0) + 1
      });
      console.warn(`[SW] Sync failed for record ${record.id}:`, err);
    }
  }
}

// =============================================================================
// 5. Push Notification Support
// =============================================================================
self.addEventListener('push', (event) => {
  let data = { title: 'DSA System Notification', body: 'You have a new update in DSM.', icon: '/static/images/icons/icon-192x192.png' };
  if (event.data) {
    try { data = event.data.json(); } catch (e) { data.body = event.data.text(); }
  }
  const options = {
    body: data.body || 'New notification received.',
    icon: data.icon || '/static/images/icons/icon-192x192.png',
    badge: '/static/images/icons/icon-96x96.png',
    vibrate: [100, 50, 100],
    data: { url: data.url || '/clock-in' }
  };
  event.waitUntil(self.registration.showNotification(data.title || 'Defence Space Administration', options));
});

// =============================================================================
// 6. Notification Click Action
// =============================================================================
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/clock-in';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(self.origin) && 'focus' in client) {
          return client.navigate(targetUrl).then((c) => c.focus());
        }
      }
      if (clients.openWindow) return clients.openWindow(targetUrl);
    })
  );
});

// =============================================================================
// 7. Message Handler: manual sync trigger from the page
// =============================================================================
self.addEventListener('message', (event) => {
  if (!event.data) return;
  if (event.data.type === 'FORCE_SYNC') {
    replayOfflineQueue().then(() => {
      if (event.source) event.source.postMessage({ type: 'SYNC_COMPLETE' });
    });
  }
  if (event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

