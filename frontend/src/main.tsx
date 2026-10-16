import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './index.css';
import { installConnectServiceFetchHook } from '@/components/connect/connectService';
import { getUrlBase } from './urlHelpers';

// TODO: Migrate all fetch('/api/...') calls in service files to use apiFetch from apiClient.ts
// This ensures URL base is correctly applied. For now, services use native fetch which works
// in production because the server handles URL base via WSGI DispatcherMiddleware, but
// proper migration to apiFetch is needed for consistency.

installConnectServiceFetchHook();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// Dismiss splash screen once React has mounted
requestAnimationFrame(() => {
  const splash = document.getElementById('splash');
  if (splash) {
    splash.classList.add('hide');
    setTimeout(() => splash.remove(), 500);
  }
});

// =============================================================================
// ?sw_reset=1 — Break-glass debug flag for kiosk/tablet
// Unregisters all service workers, clears all Cache Storage, reloads once.
// Removes the param after use to prevent reload loops.
// =============================================================================
if (new URLSearchParams(window.location.search).has('sw_reset')) {
  (async () => {
    console.log('[SW Reset] Break-glass: clearing all SW + caches');
    if ('serviceWorker' in navigator) {
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map(r => r.unregister()));
      console.log('[SW Reset] Unregistered', regs.length, 'service worker(s)');
    }
    if ('caches' in window) {
      const names = await caches.keys();
      await Promise.all(names.map(n => caches.delete(n)));
      console.log('[SW Reset] Deleted caches:', names);
    }
    // Remove the ?sw_reset param and reload once (prevents loop)
    const url = new URL(window.location.href);
    url.searchParams.delete('sw_reset');
    window.location.replace(url.toString());
  })();
} else if ('serviceWorker' in navigator) {
  // =============================================================================
  // Normal SW registration + update listener
  // =============================================================================
  const urlBase = getUrlBase();
  const swUrl = urlBase ? `${urlBase}/sw.js` : '/sw.js';
  const swScope = urlBase ? `${urlBase}/` : '/';
  
  navigator.serviceWorker.register(swUrl, { scope: swScope }).then((reg) => {
    // Force check for update on every page load
    reg.update().catch(() => {});

    // Listen for a new SW waiting to activate
    const onNewSW = () => {
      if (reg.waiting) {
        showUpdateToast(reg.waiting);
      }
    };

    // If a waiting worker already exists (e.g. from a previous visit)
    if (reg.waiting) {
      showUpdateToast(reg.waiting);
    }

    // A new SW has been installed and is waiting
    reg.addEventListener('updatefound', () => {
      const newWorker = reg.installing;
      if (!newWorker) return;
      newWorker.addEventListener('statechange', () => {
        if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
          // New SW installed while old one is still controlling — show toast
          onNewSW();
        }
      });
    });
  }).catch(() => {});

  // Auto-reload when the new SW takes control (after skipWaiting)
  let refreshing = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (refreshing) return;
    refreshing = true;
    window.location.reload();
  });
}

// =============================================================================
// Update toast — lightweight, no React dependency (runs before/outside React)
// =============================================================================
function showUpdateToast(waitingSW: ServiceWorker) {
  // Don't show duplicate toasts
  if (document.getElementById('sw-update-toast')) return;

  const toast = document.createElement('div');
  toast.id = 'sw-update-toast';
  toast.setAttribute('role', 'alert');
  Object.assign(toast.style, {
    position: 'fixed',
    bottom: '24px',
    left: '50%',
    transform: 'translateX(-50%)',
    zIndex: '99999',
    background: 'rgba(30, 30, 30, 0.95)',
    backdropFilter: 'blur(12px)',
    WebkitBackdropFilter: 'blur(12px)',
    border: '1px solid rgba(255, 255, 255, 0.15)',
    borderRadius: '16px',
    padding: '14px 24px',
    display: 'flex',
    alignItems: 'center',
    gap: '16px',
    boxShadow: '0 8px 32px rgba(0, 0, 0, 0.5)',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    animation: 'swToastIn 0.3s ease both',
  });

  toast.innerHTML = `
    <span style="color: rgba(255,255,255,0.85); font-size: 15px; font-weight: 500;">
      Update available
    </span>
    <button id="sw-update-btn" style="
      background: #3b82f6;
      color: #fff;
      border: none;
      border-radius: 10px;
      padding: 8px 18px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      white-space: nowrap;
    ">Refresh</button>
  `;

  // Inject animation keyframe if not already present
  if (!document.getElementById('sw-toast-style')) {
    const style = document.createElement('style');
    style.id = 'sw-toast-style';
    style.textContent = `
      @keyframes swToastIn {
        from { opacity: 0; transform: translateX(-50%) translateY(20px); }
        to   { opacity: 1; transform: translateX(-50%) translateY(0); }
      }
    `;
    document.head.appendChild(style);
  }

  document.body.appendChild(toast);

  // On click: tell waiting SW to activate, then controllerchange listener reloads
  toast.querySelector('#sw-update-btn')!.addEventListener('click', () => {
    waitingSW.postMessage('SKIP_WAITING');
    toast.remove();
  });

  // Auto-apply after 10 seconds if user hasn't interacted (kiosk may be unattended)
  setTimeout(() => {
    if (document.getElementById('sw-update-toast')) {
      waitingSW.postMessage('SKIP_WAITING');
      toast.remove();
    }
  }, 10_000);
}
