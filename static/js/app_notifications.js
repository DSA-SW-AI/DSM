// static/js/app_notifications.js
// Custom Application Toast & Confirmation Modal Engine
// Replaces default browser JavaScript alerts & confirms with sleek, military-grade app popups.

(function () {
  // Inject Custom Notification Styles
  const style = document.createElement('style');
  style.textContent = `
    #appToastContainer {
      position: fixed;
      top: 24px;
      right: 24px;
      z-index: 999999;
      display: flex;
      flex-direction: column;
      gap: 12px;
      max-width: 420px;
      width: 90%;
      pointer-events: none;
    }
    .app-toast {
      pointer-events: auto;
      background: #ffffff;
      border-radius: 12px;
      padding: 16px 20px;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.15), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
      display: flex;
      align-items: center;
      gap: 14px;
      border-left: 5px solid #3b82f6;
      animation: appToastSlideIn 0.35s cubic-bezier(0.16, 1, 0.3, 1);
      transition: all 0.3s ease;
      font-family: 'Poppins', 'Inter', system-ui, -apple-system, sans-serif;
    }
    .app-toast.toast-success { border-left-color: #10b981; background: #ffffff; }
    .app-toast.toast-error { border-left-color: #ef4444; background: #ffffff; }
    .app-toast.toast-warning { border-left-color: #f59e0b; background: #ffffff; }
    .app-toast.toast-info { border-left-color: #3b82f6; background: #ffffff; }
    
    .app-toast-icon {
      width: 34px;
      height: 34px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 17px;
      flex-shrink: 0;
      font-weight: bold;
    }
    .toast-success .app-toast-icon { background: #d1fae5; color: #059669; }
    .toast-error .app-toast-icon { background: #fee2e2; color: #dc2626; }
    .toast-warning .app-toast-icon { background: #fef3c7; color: #d97706; }
    .toast-info .app-toast-icon { background: #dbeafe; color: #2563eb; }
    
    .app-toast-content { flex-grow: 1; }
    .app-toast-title { font-weight: 700; font-size: 13.5px; color: #0f172a; margin-bottom: 2px; }
    .app-toast-msg { font-size: 12.5px; color: #475569; line-height: 1.45; word-break: break-word; }
    .app-toast-close { cursor: pointer; color: #94a3b8; font-size: 20px; line-height: 1; margin-left: 8px; transition: color 0.2s; }
    .app-toast-close:hover { color: #0f172a; }
    
    @keyframes appToastSlideIn {
      from { transform: translateX(120%); opacity: 0; }
      to { transform: translateX(0); opacity: 1; }
    }
    @keyframes appToastFadeOut {
      from { transform: translateX(0); opacity: 1; }
      to { transform: translateX(120%); opacity: 0; }
    }

    /* Custom App Confirm Modal */
    #appConfirmModal {
      display: none;
      position: fixed;
      top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(15, 23, 42, 0.75);
      backdrop-filter: blur(6px);
      z-index: 999998;
      justify-content: center;
      align-items: center;
      font-family: 'Poppins', 'Inter', system-ui, -apple-system, sans-serif;
    }
    .app-confirm-card {
      background: #ffffff;
      border-radius: 16px;
      max-width: 440px;
      width: 90%;
      padding: 28px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
      animation: appModalPop 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      text-align: center;
      border: 1px solid #e2e8f0;
    }
    .app-confirm-icon {
      width: 52px; height: 52px;
      border-radius: 50%;
      background: #fef3c7;
      color: #d97706;
      display: flex; align-items: center; justify-content: center;
      font-size: 26px; margin: 0 auto 16px;
    }
    .app-confirm-title { font-size: 18px; font-weight: 700; color: #0f172a; margin-bottom: 8px; }
    .app-confirm-msg { font-size: 13.5px; color: #475569; margin-bottom: 24px; line-height: 1.5; }
    .app-confirm-actions { display: flex; gap: 12px; justify-content: center; }
    .app-confirm-btn {
      padding: 10px 22px; border-radius: 8px; font-weight: 600; font-size: 13.5px; cursor: pointer; border: none; transition: all 0.2s;
    }
    .btn-confirm-cancel { background: #f1f5f9; color: #475569; border: 1px solid #cbd5e0; }
    .btn-confirm-cancel:hover { background: #e2e8f0; }
    .btn-confirm-ok { background: #0f172a; color: #ffffff; }
    .btn-confirm-ok:hover { background: #1e293b; }
    
    @keyframes appModalPop {
      from { transform: scale(0.9); opacity: 0; }
      to { transform: scale(1); opacity: 1; }
    }
  `;
  document.head.appendChild(style);

  function ensureContainers() {
    if (!document.getElementById('appToastContainer')) {
      const toastContainer = document.createElement('div');
      toastContainer.id = 'appToastContainer';
      document.body.appendChild(toastContainer);
    }
    if (!document.getElementById('appConfirmModal')) {
      const confirmModal = document.createElement('div');
      confirmModal.id = 'appConfirmModal';
      confirmModal.innerHTML = `
        <div class="app-confirm-card">
          <div class="app-confirm-icon">⚠️</div>
          <h3 id="appConfirmTitle" class="app-confirm-title">Confirm Directive</h3>
          <p id="appConfirmMsg" class="app-confirm-msg">Are you sure you want to proceed?</p>
          <div class="app-confirm-actions">
            <button type="button" id="appConfirmCancelBtn" class="app-confirm-btn btn-confirm-cancel">Cancel</button>
            <button type="button" id="appConfirmOkBtn" class="app-confirm-btn btn-confirm-ok">Confirm</button>
          </div>
        </div>
      `;
      document.body.appendChild(confirmModal);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ensureContainers);
  } else {
    ensureContainers();
  }
})();

// Global showAppToast function
window.showAppToast = function (message, type = 'info', title = '') {
  let container = document.getElementById('appToastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'appToastContainer';
    document.body.appendChild(container);
  }

  const icons = {
    success: '✓',
    error: '✕',
    warning: '⚠️',
    info: 'ℹ️'
  };

  const defaultTitles = {
    success: 'Success',
    error: 'Notice',
    warning: 'Warning',
    info: 'System Information'
  };

  let msgStr = String(message || '');
  if (!type || type === 'info') {
    const lower = msgStr.toLowerCase();
    if (lower.includes('success') || lower.includes('successfully') || lower.includes('committed') || lower.includes('logged') || lower.includes('flagged')) {
      type = 'success';
    } else if (lower.includes('error') || lower.includes('failed') || lower.includes('unauthorized') || lower.includes('denied') || lower.includes('communication fail')) {
      type = 'error';
    } else if (lower.includes('warning') || lower.includes('please')) {
      type = 'warning';
    }
  }

  const toast = document.createElement('div');
  toast.className = `app-toast toast-${type}`;
  toast.innerHTML = `
    <div class="app-toast-icon">${icons[type] || 'ℹ️'}</div>
    <div class="app-toast-content">
      <div class="app-toast-title">${title || defaultTitles[type] || 'Notification'}</div>
      <div class="app-toast-msg">${msgStr}</div>
    </div>
    <span class="app-toast-close">&times;</span>
  `;

  toast.querySelector('.app-toast-close').onclick = () => removeToast(toast);
  container.appendChild(toast);

  const autoDismiss = setTimeout(() => removeToast(toast), 4500);

  function removeToast(t) {
    clearTimeout(autoDismiss);
    t.style.animation = 'appToastFadeOut 0.3s forwards';
    setTimeout(() => { if (t.parentNode) t.parentNode.removeChild(t); }, 300);
  }
};

// Global showAppConfirm function (Promise-based)
window.showAppConfirm = function (message, title = 'Confirm Directive') {
  return new Promise((resolve) => {
    let modal = document.getElementById('appConfirmModal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'appConfirmModal';
      modal.innerHTML = `
        <div class="app-confirm-card">
          <div class="app-confirm-icon">⚠️</div>
          <h3 id="appConfirmTitle" class="app-confirm-title">Confirm Directive</h3>
          <p id="appConfirmMsg" class="app-confirm-msg">Are you sure you want to proceed?</p>
          <div class="app-confirm-actions">
            <button type="button" id="appConfirmCancelBtn" class="app-confirm-btn btn-confirm-cancel">Cancel</button>
            <button type="button" id="appConfirmOkBtn" class="app-confirm-btn btn-confirm-ok">Confirm</button>
          </div>
        </div>
      `;
      document.body.appendChild(modal);
    }

    document.getElementById('appConfirmTitle').textContent = title;
    document.getElementById('appConfirmMsg').textContent = message;

    const cancelBtn = document.getElementById('appConfirmCancelBtn');
    const okBtn = document.getElementById('appConfirmOkBtn');

    modal.style.display = 'flex';

    function cleanup(res) {
      modal.style.display = 'none';
      cancelBtn.onclick = null;
      okBtn.onclick = null;
      resolve(res);
    }

    cancelBtn.onclick = () => cleanup(false);
    okBtn.onclick = () => cleanup(true);
  });
};

// Replace built-in browser window.alert with custom App Toast
window.alert = function (msg) {
  window.showAppToast(msg);
};
