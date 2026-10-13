/**
 * Defence Space Administration (DSA) - DSM PWA Client Engine
 * Handles Service Worker registration, install prompt banner, and online/offline status toasts.
 */

(function () {
  'use strict';

  // 1. Service Worker Registration
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker
        .register('/sw.js', { scope: '/' })
        .then(function (registration) {
          console.log('[PWA] Service Worker registered with root scope:', registration.scope);

          // Check for service worker updates
          registration.onupdatefound = function () {
            const installingWorker = registration.installing;
            if (installingWorker) {
              installingWorker.onstatechange = function () {
                if (installingWorker.state === 'installed' && navigator.serviceWorker.controller) {
                  console.log('[PWA] New update available! Refreshing or notifying user.');
                  showPwaToast('New update available. Refreshing for latest features...', 'info');
                }
              };
            }
          };
        })
        .catch(function (error) {
          console.warn('[PWA] Service Worker registration failed:', error);
        });
    });
  }

  // 2. Install Prompt Handling
  let deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', function (e) {
    // Prevent default mini-infobar on mobile
    e.preventDefault();
    deferredPrompt = e;
    console.log('[PWA] beforeinstallprompt event captured');

    // Show Install Banner / Button if available
    const installBtn = document.getElementById('pwaInstallBtn');
    const installBanner = document.getElementById('pwaInstallBanner');

    if (installBtn) {
      installBtn.style.display = 'inline-flex';
    }
    if (installBanner) {
      installBanner.style.display = 'flex';
    }
  });

  window.installPWA = function () {
    if (!deferredPrompt) {
      console.log('[PWA] No install prompt deferred or already installed.');
      return;
    }
    deferredPrompt.prompt();
    deferredPrompt.userChoice.then(function (choiceResult) {
      if (choiceResult.outcome === 'accepted') {
        console.log('[PWA] User accepted the PWA install prompt');
      } else {
        console.log('[PWA] User dismissed the PWA install prompt');
      }
      deferredPrompt = null;
      const installBtn = document.getElementById('pwaInstallBtn');
      const installBanner = document.getElementById('pwaInstallBanner');
      if (installBtn) installBtn.style.display = 'none';
      if (installBanner) installBanner.style.display = 'none';
    });
  };

  window.addEventListener('appinstalled', function () {
    console.log('[PWA] App was successfully installed to device!');
    showPwaToast('DSA App installed successfully!', 'success');
    deferredPrompt = null;
    const installBtn = document.getElementById('pwaInstallBtn');
    const installBanner = document.getElementById('pwaInstallBanner');
    if (installBtn) installBtn.style.display = 'none';
    if (installBanner) installBanner.style.display = 'none';
  });

  // 3. Online / Offline Status Toast
  function showPwaToast(message, type) {
    let container = document.getElementById('pwa-toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'pwa-toast-container';
      container.style.cssText = `
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 999999;
        display: flex;
        flex-direction: column;
        gap: 8px;
        pointer-events: none;
      `;
      document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    const bgColor = type === 'offline' ? '#e03b2f' : type === 'online' || type === 'success' ? '#10b981' : '#0b2d6b';
    const icon = type === 'offline' ? 'ri-wifi-off-line' : type === 'online' || type === 'success' ? 'ri-checkbox-circle-line' : 'ri-information-line';

    toast.style.cssText = `
      background: ${bgColor};
      color: #ffffff;
      padding: 12px 20px;
      border-radius: 10px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13.5px;
      font-weight: 500;
      box-shadow: 0 10px 25px rgba(0,0,0,0.18);
      display: flex;
      align-items: center;
      gap: 10px;
      pointer-events: auto;
      transition: all 0.3s ease;
      transform: translateY(20px);
      opacity: 0;
    `;
    toast.innerHTML = `<i class="${icon}" style="font-size: 17px;"></i> <span>${message}</span>`;
    container.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => {
      toast.style.transform = 'translateY(0)';
      toast.style.opacity = '1';
    });

    // Animate out
    setTimeout(() => {
      toast.style.transform = 'translateY(20px)';
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  window.addEventListener('online', function () {
    showPwaToast('Back Online. Live synchronization active.', 'online');
  });

  window.addEventListener('offline', function () {
    showPwaToast('You are offline. Cached records remain accessible.', 'offline');
  });
})();
