// 12-query-mode.js — 질의 모드 전환 · 통합 검색 버튼
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

// ═══════════════════════════════════════════════════════════════
//  LLM Pattern Generation (natural language -> synthetic query -> existing search)
// ═══════════════════════════════════════════════════════════════

function getActiveQueryModel() {
  return window._llmQueryModel || (($('llmTextQuerySelect') || {}).value || '');
}

function getActiveDescribeModel() {
  return window._llmDescribeModel || (($('llmDescribeSelect') || {}).value || '');
}

/* ═══════════════════════════════════════════════════════════════
   Unified Query Mode (global)
   ═══════════════════════════════════════════════════════════════ */
var _queryMode = 'select';  // continuous: 'select'|'sketch'|'text' | segment: 'sample'|'preset'|'text'

/* ── Show/hide pills based on mode ──
   Continuous: Select + Sketch + Text
   Segment:    Sample + Preset + Sketch + Text  */
/* 못 쓰는 모드도 자리에 남긴다 — 사라지면 "왜 없지"가 되므로,
   흐리게 두고 title 로 어느 뷰에서 쓸 수 있는지 알린다. */
function _applyModePills(enabled, hint) {
  document.querySelectorAll('.query-mode').forEach(function(p) {
    var usable = enabled.indexOf(p.dataset.qmode) >= 0;
    p.hidden = false;
    p.disabled = !usable;
    p.classList.toggle('disabled', !usable);
    if (usable) p.removeAttribute('title');
    else p.title = hint;
  });
}
function _showSegmentModePills() {
  // 세그먼트 뷰에서는 차트 드래그가 없으므로 구간 선택만 막힌다.
  _applyModePills(['sketch', 'text', 'sample', 'preset'], _t('finder.mode_hint_continuous'));
}
function _showContinuousModePills() {
  _applyModePills(['select', 'sketch', 'text'], _t('finder.mode_hint_segment'));
}

function setQueryMode(mode) {
  var prevMode = _queryMode;
  _queryMode = mode;
  document.querySelectorAll('.query-mode').forEach(function(p) {
    p.classList.toggle('active', p.dataset.qmode === mode);
    // Lock non-active pills visually
    if (p.dataset.qmode !== mode) {
      p.classList.add('locked');
    } else {
      p.classList.remove('locked');
    }
  });
  // Show/hide mode sections (select and sketch are separate now)
  if ($('modeSelectSection')) $('modeSelectSection').hidden = (mode !== 'select');
  if ($('modeSketchSection')) $('modeSketchSection').hidden = (mode !== 'sketch');
  if ($('modeSampleSection'))  $('modeSampleSection').hidden  = (mode !== 'sample');
  if ($('modePresetSection'))  $('modePresetSection').hidden  = (mode !== 'preset');
  if ($('modeTextSection'))    $('modeTextSection').hidden    = (mode !== 'text');

  // Sketch in segment mode: auto-set window size to segment length
  if (mode === 'sketch' && S.mode === 'segment' && S.segments && S.segments.length > 0) {
    var segLen = S.segments[0].values ? S.segments[0].values.length : 24;
    if ($('inputSketchLen')) $('inputSketchLen').value = segLen;
    if ($('sketchResolutionLabel')) $('sketchResolutionLabel').textContent = _t('js.resolution_seg', segLen);
    if (typeof DrawQueryAPI !== 'undefined') {
      DrawQueryAPI.setLength(segLen);
      console.log('[Sketch] Auto-set length to segment size:', segLen);
    }
    // Hide window size presets in segment mode (auto-determined)
    var sketchPresets = document.querySelectorAll('.sketch-preset');
    sketchPresets.forEach(function(btn) { btn.parentElement.style.display = 'none'; });
  } else if (mode === 'sketch') {
    // Show presets in continuous mode
    var sketchPresets = document.querySelectorAll('.sketch-preset');
    sketchPresets.forEach(function(btn) { btn.parentElement.style.display = ''; });
  }

  // Text window settings: only in continuous mode (segments are pre-split)
  if ($('textWindowSettings')) $('textWindowSettings').hidden = (S.mode === 'segment');

  // ── Mode-Adaptive Settings Panels ──
  // Show Search Settings for Select/Sketch/Sample/Preset/Text
  if ($('searchDetailSection')) $('searchDetailSection').hidden = false;

  // Hide Z-score toggle for Preset/Text modes (already normalized)
  if ($('zscoreToggleRow')) $('zscoreToggleRow').hidden = (mode === 'preset' || mode === 'text');

  // Switch chart dragmode: only allow selection in select mode
  var plotEl = $('plotMain');
  if (plotEl && plotEl._fullLayout) {
    Plotly.relayout('plotMain', { dragmode: mode === 'select' ? 'select' : 'zoom', selectdirection: 'h' });
  }

  // ── Mutual exclusion: clear previous mode state ──
  if (prevMode !== mode) {
    // Always restore collapsed segment panel & hide result panels on mode switch
    var _sp = $('segmentPanel');
    if (_sp && _sp.classList.contains('seg-collapsed')) {
      _sp.classList.remove('seg-collapsed');
      if ($('btnSegToggle')) $('btnSegToggle').textContent = _t('js.collapse');
    }
    if ($('segSearchResults')) $('segSearchResults').hidden = true;
    if ($('classifyResults')) $('classifyResults').hidden = true;

    // Clear select mode state (chart selection)
    if (prevMode === 'select') {
      S.queryStart = -1;
      S.queryEnd = -1;
      S.results = [];
      S.queryData = null;
      if ($('chartQueryBar')) $('chartQueryBar').hidden = true;
      if ($('chipQuery')) $('chipQuery').hidden = true;
      if ($('resultsSection')) $('resultsSection').hidden = true;
      if ($('contSummaryBar')) { $('contSummaryBar').hidden = true; $('contSummaryBar').innerHTML = ''; }
      $('btnSearch').disabled = true;
      clearSelectPreview();
      if (S.values && S.values.length > 0) drawMainChart();
    }
    // Clear sketch mode state (drawn pattern)
    if (prevMode === 'sketch') {
      S.generatedPattern = null;
      if (typeof DrawQueryAPI !== 'undefined') DrawQueryAPI.clear();
      if ($('cqbGenDesc')) $('cqbGenDesc').hidden = true;
      if ($('resultsSection')) $('resultsSection').hidden = true;
      $('btnSearch').disabled = true;
    }
    // Clear sample mode state
    if (prevMode === 'sample') {
      if ($('sampleSelected')) $('sampleSelected').hidden = true;
    }
    // Clear preset/text mode state
    if (prevMode === 'preset' || prevMode === 'text') {
      // Clear query shape preview
      S.generatedPattern = null;
    }
    // Clear preset selection + badges
    if (prevMode === 'preset') {
      var grid = $('segPresetGrid');
      if (grid) grid.querySelectorAll('.seg-preset-btn').forEach(function(b) {
        b.classList.remove('active');
      });
      window._ssSelectedPreset = null;
      if (typeof clearPresetBadges === 'function') clearPresetBadges();
    }
    // Clear text input
    if (prevMode === 'text') {
      var inp = $('segSearchInput');
      if (inp) inp.value = '';
    }
    // Entering sample mode: sync sidebar with current seg query
    if (mode === 'sample') {
      _syncSampleSelection();
    }
  }

  updateUnifiedSearchBtn();
}

/* ── Sample mode: sync sidebar info with selected segment ── */
function _syncSampleSelection() {
  var sel = $('sampleSelected');
  var hint = $('sampleHint');
  if (S.segQueryIndex >= 0) {
    var seg = S.segments.find(function(s) { return s.index === S.segQueryIndex; });
    if (seg && sel) {
      sel.hidden = false;
      if (hint) hint.hidden = true;
      $('sampleSelName').textContent = seg.label;
      var startL = seg.start || (seg.labels && seg.labels[0]) || '';
      var endL = seg.end || (seg.labels && seg.labels[seg.labels.length - 1]) || '';
      var pts = seg.length || seg.values.length;
      var meta = pts + ' pts';
      if (startL && endL) {
        var s = startL.length > 16 ? startL.substring(5, 16) : startL;
        var e = endL.length > 16 ? endL.substring(5, 16) : endL;
        meta = s + ' ~ ' + e + '  (' + pts + 'pts)';
      }
      $('sampleSelMeta').textContent = meta;
    }
  } else {
    if (sel) sel.hidden = true;
    if (hint) hint.hidden = false;
  }
}

function setSearchBusy(busy) {
  S._searchBusy = busy;
  var btn = $('btnUnifiedSearch');
  if (btn) btn.classList.toggle('btn-loading', busy);
  updateUnifiedSearchBtn();
}

function updateUnifiedSearchBtn() {
  var btn = $('btnUnifiedSearch');
  if (!btn) return;
  if (S._searchBusy) {
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-spinner"></span> ' + _t('js.searching');
    return;
  }
  if (!S.datasetKey || !S.primaryCol) { btn.disabled = true; return; }

  if (_queryMode === 'select') {
    var hasQuery = S.queryStart >= 0 && S.queryEnd >= 0;
    btn.disabled = !hasQuery;
    btn.textContent = _t('js.search');
  } else if (_queryMode === 'sketch') {
    var hasDrawn = typeof DrawQueryAPI !== 'undefined' && DrawQueryAPI.hasValidPattern();
    btn.disabled = !hasDrawn;
    btn.textContent = _t('js.search');
  } else if (_queryMode === 'sample') {
    btn.disabled = S.segQueryIndex < 0;
    btn.textContent = _t('js.search_similar');
  } else if (_queryMode === 'preset') {
    btn.disabled = !window._ssSelectedPreset;
    btn.textContent = _t('js.search');
  } else if (_queryMode === 'text') {
    var input = $('segSearchInput');
    btn.disabled = !input || !input.value.trim();
    btn.textContent = _t('js.search');
  }
}
