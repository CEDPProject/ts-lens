// 27-ss-init.js — 세그먼트 검색 — 초기화 · Discovery 이동
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

function setupDetailToggle(toggleId, bodyId) {
  var toggle = $(toggleId);
  var body = $(bodyId);
  if (!toggle || !body) return;

  toggle.addEventListener('click', function() {
    var isExpanded = body.classList.contains('open');
    body.classList.toggle('open');
    var arrow = toggle.querySelector('.common-detail-arrow');
    if (arrow) arrow.textContent = isExpanded ? '\u25B6' : '\u25BC';  // ▶ or ▼
  });
}
setupDetailToggle('searchDetailToggle', 'searchDetailBody');

window._setSelectedTimeUnit = function(unit) {
  SS.selectedTimeUnit = unit;
};
window._getSelectedTimeUnit = function() {
  return SS.selectedTimeUnit;
};

/* ── Init ── */
loadScopedLLMSelectors();
loadPresets();

// ── AI Auto Discovery: navigate to standalone page ──
// The full discovery flow now lives on /discovery (static/js/discovery.js).
// Hand over the current dataset/column selection via sessionStorage.
// Shared handler: hand over selection and navigate to the Discovery page.
// Reachable from both the Full View header (#btnToDiscover) and the
// Segment View header (#btnToDiscoverSeg).
function goToDiscovery() {
  if (!S.datasetKey) { alert(_t('js.alert_load_dataset')); return; }
  // A primary column is required — Discovery clusters one column's segments.
  if (!S.primaryCol) { alert(_t('js.alert_select_column') || _t('js.alert_load_dataset')); return; }
  try {
    sessionStorage.setItem('discovery_selection', JSON.stringify({
      datasetKey: S.datasetKey,
      primaryCol: S.primaryCol,
      overlayCols: S.overlayCols || [],
    }));
  } catch (e) {}
  window.location.href = '/discovery';
}
if ($('btnToDiscover')) $('btnToDiscover').addEventListener('click', goToDiscovery);
if ($('btnToDiscoverSeg')) $('btnToDiscoverSeg').addEventListener('click', goToDiscovery);

// Show view mode buttons when data is loaded
window._showViewModeButtons = function() {
  if ($('viewModeBtns')) $('viewModeBtns').hidden = false;
};
