// 03-toast.js — 토스트 알림
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

(function() {
  var toastContainer = null;

  function ensureContainer() {
    if (!toastContainer) {
      toastContainer = document.createElement('div');
      toastContainer.className = 'toast-container';
      document.body.appendChild(toastContainer);
    }
    return toastContainer;
  }

  window.showToast = function(message, options) {
    options = options || {};
    var type = options.type || 'info'; // 'info', 'warning', 'error', 'success'
    var title = options.title || '';
    var duration = options.duration !== undefined ? options.duration : 4000;

    var container = ensureContainer();

    var toast = document.createElement('div');
    toast.className = 'toast toast-' + type;

    var iconSymbol = type === 'warning' ? '!' : type === 'error' ? '×' : type === 'success' ? '✓' : 'i';

    var html = '<div class="toast-icon">' + iconSymbol + '</div>'
      + '<div class="toast-content">'
      + (title ? '<div class="toast-title">' + title + '</div>' : '')
      + '<div class="toast-message">' + message + '</div>'
      + '</div>'
      + '<button class="toast-close" aria-label="Close">&times;</button>';

    toast.innerHTML = html;
    container.appendChild(toast);

    var closeBtn = toast.querySelector('.toast-close');
    function remove() {
      toast.classList.add('toast-hiding');
      setTimeout(function() {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
        if (container.children.length === 0 && toastContainer) {
          document.body.removeChild(toastContainer);
          toastContainer = null;
        }
      }, 300);
    }

    closeBtn.addEventListener('click', remove);

    if (duration > 0) {
      setTimeout(remove, duration);
    }
  };
})();
