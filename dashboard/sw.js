/* WARDAR Service Worker — PWA offline support */
const CACHE_NAME = 'wardar-pwa-v3';
const STATIC_ASSETS = [
  '/',
  'https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&display=swap',
  'https://unpkg.com/leaflet@1.9/dist/leaflet.css',
  'https://unpkg.com/leaflet@1.9/dist/leaflet.js',
];

self.addEventListener('install', event => {
  // Pre-cache core assets
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_ASSETS.filter(u => !u.startsWith('https://fonts'))))
      .catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  // Remove old caches
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Network-first for API + WebSocket — never cache live data
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws')) {
    return; // fall through to network
  }

  // Cache-first for static assets (fonts, CDN libraries)
  if (url.origin !== location.origin) {
    event.respondWith(
      caches.match(event.request)
        .then(cached => cached || fetch(event.request).then(res => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then(c => c.put(event.request, copy));
          }
          return res;
        }))
        .catch(() => new Response('', { status: 503 }))
    );
    return;
  }

  // Network-first with cache fallback for same-origin pages
  event.respondWith(
    fetch(event.request)
      .then(res => {
        if (res.ok && event.request.method === 'GET') {
          const copy = res.clone();
          caches.open(CACHE_NAME).then(c => c.put(event.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(event.request)
        .then(cached => cached || new Response(
          '<html><body style="background:#040406;color:#f59e0b;font-family:monospace;padding:2rem"><h1>WARDAR OFFLINE</h1><p>No network connection. Live data unavailable.</p></body></html>',
          { headers: { 'Content-Type': 'text/html' } }
        ))
      )
  );
});
