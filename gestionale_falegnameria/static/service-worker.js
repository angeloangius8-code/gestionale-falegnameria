const CACHE_NAME = 'falegnameria-v2';

self.addEventListener('install', (event) => {
  // Forza l'attivazione immediata del nuovo Service Worker
  self.skipWaiting();
  console.log('[Service Worker v2] Installato');
});

self.addEventListener('activate', (event) => {
  // Pulisce le vecchie cache registrate
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cache) => {
          if (cache !== CACHE_NAME) {
            console.log('[Service Worker] Rimozione vecchia cache:', cache);
            return caches.delete(cache);
          }
        })
      );
    })
  );
  return self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});