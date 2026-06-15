// MDM Titan — PWA install banner + push notifications
// Self-contained: injeta o banner no body, detecta plataforma, gerencia subscribe.

(function () {
  'use strict';

  const DISMISS_KEY = 'mdm.pwaInstallDismissedUntil';
  const DISMISS_DAYS = 14;

  // ── Helpers ──────────────────────────────────────────────────────────
  const isStandalone = () => {
    if (window.matchMedia('(display-mode: standalone)').matches) return true;
    if (window.navigator.standalone === true) return true; // iOS legacy
    return false;
  };

  const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent);
  const isAndroid = () => /android/i.test(navigator.userAgent);
  const isSafari = () => /^((?!chrome|android).)*safari/i.test(navigator.userAgent);

  const dismissed = () => {
    try {
      const until = parseInt(localStorage.getItem(DISMISS_KEY) || '0', 10);
      return until > Date.now();
    } catch (e) { return false; }
  };

  const dismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, String(Date.now() + DISMISS_DAYS * 24 * 60 * 60 * 1000));
    } catch (e) {}
  };

  // urlBase64 → Uint8Array (pra applicationServerKey)
  const urlBase64ToUint8 = (s) => {
    const padding = '='.repeat((4 - s.length % 4) % 4);
    const b64 = (s + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(b64);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  };

  // ── Banner HTML ──────────────────────────────────────────────────────
  function buildBanner() {
    const wrap = document.createElement('div');
    wrap.id = 'mdm-install-banner';
    wrap.innerHTML = `
      <style>
        #mdm-install-banner {
          position: fixed;
          left: 12px; right: 12px;
          bottom: calc(12px + env(safe-area-inset-bottom, 0));
          z-index: 1000;
          background: linear-gradient(135deg, #1e3a8a 0%, #0f172a 100%);
          color: #fff;
          border: 1px solid rgba(59,130,246,.35);
          border-radius: 16px;
          padding: 12px 14px;
          display: flex; align-items: center; gap: 12px;
          box-shadow: 0 20px 40px -10px rgba(0,0,0,.6);
          font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
          transform: translateY(120%);
          transition: transform .35s cubic-bezier(.4,0,.2,1);
        }
        #mdm-install-banner.show { transform: translateY(0); }
        #mdm-install-banner .ib-icon {
          width: 44px; height: 44px;
          background: #0b0f1a; border-radius: 10px;
          display: flex; align-items: center; justify-content: center;
          flex-shrink: 0;
        }
        #mdm-install-banner .ib-icon img { width: 38px; height: 38px; object-fit: contain; }
        #mdm-install-banner .ib-text { flex: 1; min-width: 0; }
        #mdm-install-banner .ib-title { font-size: 13px; font-weight: 700; }
        #mdm-install-banner .ib-sub   { font-size: 11px; opacity: .7; margin-top: 2px; }
        #mdm-install-banner .ib-btn   {
          background: #3b82f6; color: #fff;
          border: 0; border-radius: 10px;
          padding: 8px 14px; font-size: 12px; font-weight: 700;
          cursor: pointer; flex-shrink: 0;
          transition: background .15s;
        }
        #mdm-install-banner .ib-btn:hover { background: #2563eb; }
        #mdm-install-banner .ib-close {
          background: transparent; border: 0; color: rgba(255,255,255,.5);
          font-size: 22px; line-height: 1; padding: 0 6px;
          cursor: pointer; flex-shrink: 0;
        }
        @media (max-width: 480px) {
          #mdm-install-banner .ib-sub { display: none; }
          #mdm-install-banner .ib-btn { padding: 8px 12px; }
        }
        /* Modal iOS */
        #mdm-ios-modal {
          position: fixed; inset: 0;
          background: rgba(0,0,0,.7); backdrop-filter: blur(4px);
          z-index: 1100;
          display: none; align-items: center; justify-content: center;
          padding: 20px;
        }
        #mdm-ios-modal.show { display: flex; }
        #mdm-ios-modal .iosm-box {
          background: #0b0f1a; color: #fff;
          border: 1px solid rgba(59,130,246,.3);
          border-radius: 20px; padding: 24px;
          max-width: 380px; width: 100%;
          font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
          max-height: 90vh; overflow-y: auto;
        }
        #mdm-ios-modal h3 { font-size: 17px; font-weight: 800; margin: 0 0 14px; text-align: center; }
        #mdm-ios-modal ol { padding-left: 0; margin: 0 0 18px; list-style: none; }
        #mdm-ios-modal ol li {
          display: flex; align-items: center; gap: 12px;
          padding: 12px; background: rgba(59,130,246,.08);
          border: 1px solid rgba(59,130,246,.15);
          border-radius: 12px; margin-bottom: 8px;
          font-size: 13px;
        }
        #mdm-ios-modal .step-n {
          width: 28px; height: 28px; flex-shrink: 0;
          background: #3b82f6; border-radius: 50%;
          display: flex; align-items: center; justify-content: center;
          font-weight: 800; font-size: 13px;
        }
        #mdm-ios-modal .iosm-icon {
          display: inline-block; font-size: 18px; margin: 0 4px;
        }
        #mdm-ios-modal .iosm-close {
          width: 100%; padding: 12px;
          background: #3b82f6; color: #fff; border: 0;
          border-radius: 12px; font-weight: 700; font-size: 14px; cursor: pointer;
        }
      </style>
      <div class="ib-icon"><img src="/static/icons/icon-192.png" alt=""></div>
      <div class="ib-text">
        <div class="ib-title">Instalar MDM Titan</div>
        <div class="ib-sub">Acesso direto pela tela inicial</div>
      </div>
      <button class="ib-btn" data-act="install">Instalar</button>
      <button class="ib-close" data-act="close" aria-label="Fechar">×</button>
    `;
    return wrap;
  }

  function buildIosModal() {
    const m = document.createElement('div');
    m.id = 'mdm-ios-modal';
    m.innerHTML = `
      <div class="iosm-box">
        <h3>📲 Como instalar no iPhone</h3>
        <ol>
          <li><span class="step-n">1</span><span>Toque no botão <b>Compartilhar</b> <span class="iosm-icon">⬆️</span> na barra inferior do Safari</span></li>
          <li><span class="step-n">2</span><span>Role pra baixo e toque em <b>"Adicionar à Tela de Início"</b> <span class="iosm-icon">➕</span></span></li>
          <li><span class="step-n">3</span><span>Confirme tocando em <b>"Adicionar"</b> no canto superior direito</span></li>
        </ol>
        <button class="iosm-close" data-act="ios-close">Entendi</button>
      </div>
    `;
    return m;
  }

  // ── Install flow ─────────────────────────────────────────────────────
  let deferredPrompt = null;
  let banner = null;
  let iosModal = null;

  function showBanner(mode) {
    if (!banner) return;
    const btn = banner.querySelector('.ib-btn');
    btn.textContent = mode === 'ios' ? 'Como instalar' : 'Instalar';
    btn.dataset.mode = mode;
    requestAnimationFrame(() => banner.classList.add('show'));
  }

  function hideBanner() {
    if (banner) banner.classList.remove('show');
  }

  function maybeShowBanner() {
    if (isStandalone()) { hideBanner(); return; }
    if (dismissed()) return;
    if (isIOS() && isSafari()) {
      showBanner('ios');
    } else if (deferredPrompt) {
      showBanner('native');
    }
  }

  // ── Push subscribe ───────────────────────────────────────────────────
  async function getVapidKey() {
    try {
      const r = await fetch('/api/push/vapid');
      const j = await r.json();
      return j.publicKey;
    } catch (e) {
      console.warn('[push] não pegou VAPID:', e);
      return null;
    }
  }

  async function enablePushNotifications() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      alert('Seu navegador não suporta notificações push.');
      return false;
    }
    if (isIOS() && !isStandalone()) {
      alert('No iPhone, primeiro adicione o app à tela inicial pra receber notificações.\n\nToque em "Como instalar" no banner azul.');
      return false;
    }
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') {
      alert('Notificações não foram permitidas.');
      return false;
    }
    const reg = await navigator.serviceWorker.ready;
    const vapid = await getVapidKey();
    if (!vapid) { alert('Erro: chave VAPID não disponível.'); return false; }

    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8(vapid),
      });
    }
    const res = await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subscription: sub.toJSON() }),
      credentials: 'include',
    });
    if (!res.ok) {
      alert('Erro ao registrar notificações no servidor.');
      return false;
    }
    return true;
  }

  async function disablePushNotifications() {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (!sub) return true;
    await fetch('/api/push/unsubscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint: sub.endpoint }),
      credentials: 'include',
    });
    await sub.unsubscribe();
    return true;
  }

  async function hasPushPermission() {
    if (!('Notification' in window)) return false;
    if (Notification.permission !== 'granted') return false;
    if (!('serviceWorker' in navigator)) return false;
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      return !!sub;
    } catch (e) { return false; }
  }

  // ── Boot ─────────────────────────────────────────────────────────────
  function init() {
    // Banner
    banner = buildBanner();
    iosModal = buildIosModal();
    document.body.appendChild(banner);
    document.body.appendChild(iosModal);

    banner.addEventListener('click', async (e) => {
      const act = e.target.closest('[data-act]')?.dataset.act;
      if (!act) return;
      if (act === 'close') {
        hideBanner();
        dismiss();
      } else if (act === 'install') {
        const mode = banner.querySelector('.ib-btn').dataset.mode;
        if (mode === 'ios') {
          iosModal.classList.add('show');
        } else if (deferredPrompt) {
          deferredPrompt.prompt();
          const { outcome } = await deferredPrompt.userChoice;
          if (outcome === 'accepted') { hideBanner(); }
          deferredPrompt = null;
        }
      }
    });

    iosModal.addEventListener('click', (e) => {
      if (e.target === iosModal || e.target.closest('[data-act="ios-close"]')) {
        iosModal.classList.remove('show');
      }
    });

    // Captura beforeinstallprompt
    window.addEventListener('beforeinstallprompt', (e) => {
      e.preventDefault();
      deferredPrompt = e;
      maybeShowBanner();
    });
    window.addEventListener('appinstalled', () => {
      deferredPrompt = null;
      hideBanner();
    });

    // Verifica estado inicial após carregar
    setTimeout(maybeShowBanner, 1500);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Exporta API global
  window.MDMPush = {
    enable: enablePushNotifications,
    disable: disablePushNotifications,
    isEnabled: hasPushPermission,
    showInstallBanner: maybeShowBanner,
    test: async () => {
      const r = await fetch('/api/push/test', { method: 'POST', credentials: 'include' });
      return r.json();
    },
  };
})();
