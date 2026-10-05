/* ===================================================================
   Defence Space Administration (DSA) - DSM Progressive Web App Service Worker
   Cache Version: dsm-pwa-v1.0.0
   =================================================================== */

const CACHE_NAME = 'dsm-pwa-v1.0.0';
const OFFLINE_URL = '/offline';

// Core assets to pre-cache immediately during installation
const PRECACHE_ASSETS = [
  '/',
  '/offline',
  '/static/manifest.json',
  '/static/favicon.ico',
  '/static/images/dsa_logo.png',
  '/static/images/dsa2.png',
  '/static/images/icons/icon-192x192.png',
  '/static/images/icons/icon-512x512.png',
  '/static/images/icons/icon-maskable-192x192.png',
  '/static/css/dashboard.css',
  '/static/css/personnel.css',
  '/static/css/leave_pass.css',
  '/static/js/pwa.js',
  'https://cdn.jsdelivr.net/npm/remixicon@3.5.0/fonts/remixicon.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap'
];

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

  // Ignore non-GET requests (POST/PUT/DELETE should always go directly to server)
  if (request.method !== 'GET') {
    return;
  }

  // Ignore socket.io, websocket, and real-time polling streams
  if (url.pathname.startsWith('/socket.io/') || url.pathname.includes('socket')) {
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
          if (cachedResponse) {
            return cachedResponse;
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
      .then((networkResponse) => {
        return networkResponse;
      })
      .catch(() => caches.match(request))
  );
});

// 4. Push Notification Event Support
self.addEventListener('push', (event) => {
  let data = { title: 'DSA System Notification', body: 'You have a new update in DSM.', icon: '/static/images/icons/icon-192x192.png' };
  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data.body = event.data.text();
    }
  }

  const options = {
    body: data.body || 'New notification received.',
    icon: data.icon || '/static/images/icons/icon-192x192.png',
    badge: '/static/images/icons/icon-96x96.png',
    vibrate: [100, 50, 100],
    data: {
      url: data.url || '/dashboard'
    }
  };

  event.waitUntil(self.registration.showNotification(data.title || 'Defence Space Administration', options));
});

// 5. Notification Click Action
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/dashboard';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(self.origin) && 'focus' in client) {
          return client.navigate(targetUrl).then((c) => c.focus());
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
