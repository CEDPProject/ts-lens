(function () {
  // Info tooltip buttons (position:fixed, escapes overflow)
  function _closeAllInfoPopups() {
    document.querySelectorAll('.info-btn.active').forEach(function(b) { b.classList.remove('active'); });
  }
  function _positionInfoPopup(btn) {
    var popup = btn.nextElementSibling;
    if (!popup || !popup.classList.contains('info-popup')) return;
    var r = btn.getBoundingClientRect();
    var pw = 260, pad = 8;
    var left = Math.min(r.right - pw, window.innerWidth - pw - pad);
    left = Math.max(pad, left);
    var arrowRight = Math.max(8, Math.min(pw - 16, r.left + r.width / 2 - left - 4));
    popup.classList.remove('arrow-top', 'arrow-bottom');
    if (window.innerHeight - r.bottom - 10 >= 180) {
      popup.style.top = (r.bottom + 6) + 'px'; popup.style.bottom = '';
      popup.classList.add('arrow-top');
    } else {
      popup.style.top = ''; popup.style.bottom = (window.innerHeight - r.top + 6) + 'px';
      popup.classList.add('arrow-bottom');
    }
    popup.style.left = left + 'px'; popup.style.right = 'auto';
    popup.style.setProperty('--arrow-right', arrowRight + 'px');
  }
  document.addEventListener('click', function(e) {
    var btn = e.target.closest('.info-btn');
    if (btn) {
      e.stopPropagation();
      var wasActive = btn.classList.contains('active');
      _closeAllInfoPopups();
      if (!wasActive) { btn.classList.add('active'); _positionInfoPopup(btn); }
      return;
    }
    if (!e.target.closest('.info-popup')) { _closeAllInfoPopups(); }
  });
  window.addEventListener('scroll', _closeAllInfoPopups);
})();
