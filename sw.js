const CACHE_NAME = 'staer-radar-v1';
const FICHEIROS_APP = ['/', '/index.html', '/manifest.json'];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(FICHEIROS_APP))
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
    // Dados dos aviões vêm sempre em tempo real da API
    if (event.request.url.includes('/api/')) {
        event.respondWith(fetch(event.request));
        return;
    }
    // Restante interface: usa rede e recorre à cache se estiver offline
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});