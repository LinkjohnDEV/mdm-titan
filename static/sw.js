// MDM Titan Service Worker
// Cache: cache-first pra /static, network-first pra páginas, NUNCA cacheia /api.
// Push: notification + click + resubscribe.

const CACHE = 'mdm-v2';

const PRECACHE = [
  '/static/favicon.png',
  '/static/logoretangulo.png',
  '/static/logoquadrado.png',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(PRECACHE).catch(() => {})) // não falha se algum 404
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// ── FETCH ─────────────────────────────────────────────────────────────
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return;

  // /api/* — sempre rede, nunca cacheia
  if (url.pathname.startsWith('/api/')) return;

  // /static/* — cache-first
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(e.request).then(cached => {
        if (cached) return cached;
        return fetch(e.request).then(res => {
          if (res.ok) {
            const clone = res.clone();
            caches.open(CACHE).then(c => c.put(e.request, clone));
          }
          return res;
        });
      })
    );
    return;
  }

  // Páginas: network-first com fallback pro cache
  e.respondWith(
    fetch(e.request)
      .then(res => {
        if (res.ok && res.type === 'basic') {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});

// ── PUSH ──────────────────────────────────────────────────────────────
self.addEventListener('push', event => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: 'MDM Titan', body: event.data ? event.data.text() : '' };
  }
  const title = data.title || 'MDM Titan';
  const options = {
    body: data.body || '',
    icon: data.icon || '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    data: { url: data.url || '/dashboard' },
    tag: data.tag || 'mdm-push',
    renotify: true,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// ── NOTIFICATION CLICK ────────────────────────────────────────────────
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || '/dashboard';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
      // Se já tem aba aberta do app, foca nela e navega
      for (const c of clientList) {
        if ('focus' in c) {
          c.focus();
          if ('navigate' in c) {
            try { c.navigate(targetUrl); } catch (e) {}
          }
          return;
        }
      }
      // Senão, abre nova janela
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});

// ── PUSH SUBSCRIPTION CHANGE ─────────────────────────────────────────
// Quando o browser renova a subscription, re-subscreve e reenvia ao backend
self.addEventListener('pushsubscriptionchange', event => {
  event.waitUntil(
    fetch('/api/push/vapid')
      .then(r => r.json())
      .then(({ publicKey }) => {
        if (!publicKey) return;
        return self.registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(publicKey),
        });
      })
      .then(sub => {
        if (!sub) return;
        return fetch('/api/push/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ subscription: sub.toJSON() }),
          credentials: 'include',
        });
      })
      .catch(e => console.warn('[sw] pushsubscriptionchange falhou:', e))
  );
});

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}
