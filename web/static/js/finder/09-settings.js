// 09-settings.js — 검색 설정 UI · 윈도우/정렬/NaN · 로딩 표시
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

/* ── Chart Mode ─────────────────────────────────────────────── */
function setMode(m) {
  Plotly.relayout('plotMain', { dragmode: m });
  document.querySelectorAll('.chart-mode-btn').forEach(function(b) { b.classList.remove('active'); });
}
$('btnModeSelect').addEventListener('click', function() { setMode('select'); this.classList.add('active'); });
$('btnModeZoom').addEventListener('click', function() { setMode('zoom'); this.classList.add('active'); });
$('btnModePan').addEventListener('click', function() { setMode('pan'); this.classList.add('active'); });
$('btnResetZoom').addEventListener('click', function() {
  Plotly.relayout('plotMain', { 'xaxis.autorange':true, 'yaxis.autorange':true });
});

/* ── Settings ──────────────────────────────────────────────────── */
/* ── Search settings summary chip (collapsed header) ──────────── */
function updateDetailSummary() {
  var el = $('searchDetailSummary');
  if (!el) return;
  var names = { euclidean: 'Euclidean', cosine: 'Cosine', dtw: 'DTW' };
  var parts = [names[S.method] || S.method];
  if ($('chkNorm') && $('chkNorm').checked) parts.push('Z-score');
  if (S.filterMode === 'threshold') {
    parts.push(($('sliderThreshold') ? $('sliderThreshold').value : 50) + '%');
  } else {
    parts.push('Top ' + ($('sliderTopK') ? $('sliderTopK').value : DEFAULT_TOP_K));
  }
  el.textContent = parts.join(' \u00b7 ');
}

document.querySelectorAll('.method-pill:not(.fixed-preset):not(.sketch-preset):not(.split-preset):not(.filter-mode):not(.seg-sort):not(.align-mode):not(.nan-mode):not(.query-mode):not(.seg-dist):not(.cluster-algo)').forEach(function(p) {
  p.addEventListener('click', function() {
    document.querySelectorAll('.method-pill:not(.fixed-preset):not(.sketch-preset):not(.split-preset):not(.filter-mode):not(.seg-sort):not(.align-mode):not(.nan-mode):not(.query-mode):not(.seg-dist):not(.cluster-algo)').forEach(function(q) { q.classList.remove('active'); });
    this.classList.add('active');
    S.method = this.dataset.method;
    updateDetailSummary();
  });
});

$('chkFixed').addEventListener('change', function(e) {
  $('fixedLenGroup').hidden = !e.target.checked;
  if (e.target.checked) {
    applyAlign();
  } else {
    S.boundaries = [];
  }
  if (S.queryStart >= 0) {
    updateQueryInfo();
    drawMainChart();
  }
});

document.querySelectorAll('.fixed-preset').forEach(function(b) {
  b.addEventListener('click', function() {
    document.querySelectorAll('.fixed-preset').forEach(function(q) { q.classList.remove('active'); });
    this.classList.add('active');
    var minutes = parseInt(this.dataset.minutes);
    if (minutes === 0) {
      // "custom" mode: no boundary snap
      $('customLenGroup').hidden = false;
      _fixedBoundaryMin = 0;
    } else {
      // preset: snap to calendar boundary
      $('customLenGroup').hidden = true;
      _fixedBoundaryMin = minutes;
      _dataIntervalMin = _detectDataInterval(); // recalculate on each click
      var pts = _minutesToPoints(minutes);
      if (pts < 3) pts = 3;
      $('inputFixedLen').value = pts;
      // Sync DrawQuery resolution with fixed_len
      if (typeof DrawQueryAPI !== 'undefined') {
        DrawQueryAPI.setLength(pts);
        console.log('[DrawQuery] Length synced with preset:', pts, 'pts (' + minutes + ' min)');
      }
    }
    applyAlign();
  });
});

/* ── Sketch Window Size Presets ─────────────────────────────────── */
document.querySelectorAll('.sketch-preset').forEach(function(b) {
  b.addEventListener('click', function() {
    document.querySelectorAll('.sketch-preset').forEach(function(q) { q.classList.remove('active'); });
    this.classList.add('active');
    var minutes = parseInt(this.dataset.minutes);
    var pts;
    if (minutes === 0) {
      // Custom mode
      $('sketchCustomLenGroup').hidden = false;
      pts = parseInt($('inputSketchLen').value) || DEFAULT_SEG_LEN;
    } else {
      $('sketchCustomLenGroup').hidden = true;
      _dataIntervalMin = _detectDataInterval(); // recalculate on each click
      pts = _minutesToPoints(minutes);
      if (pts < 3) pts = 3;
      $('inputSketchLen').value = pts;
    }
    // Update DrawQuery resolution
    if (typeof DrawQueryAPI !== 'undefined') {
      DrawQueryAPI.setLength(pts);
      console.log('[DrawQuery] Sketch length set:', pts, 'pts');
    }
    // Update resolution label
    $('sketchResolutionLabel').textContent = _t('js.resolution_pts', pts);
  });
});

// Custom sketch length input
if ($('inputSketchLen')) {
  $('inputSketchLen').addEventListener('change', function() {
    var pts = parseInt(this.value) || DEFAULT_SEG_LEN;
    if (pts < 3) pts = 3;
    this.value = pts;
    if (typeof DrawQueryAPI !== 'undefined') {
      DrawQueryAPI.setLength(pts);
    }
    $('sketchResolutionLabel').textContent = _t('js.resolution_pts', pts);
  });
}

function setAlignMode(mode) {
  S.alignMode = mode;
  document.querySelectorAll('.align-mode').forEach(function(b) { b.classList.remove('active'); });
  var active = document.querySelector('.align-mode[data-align="' + mode + '"]');
  if (active) active.classList.add('active');

  var strideInput = $('inputStride');
  var strideHint = $('strideAutoHint');

  if (mode === 'calendar') {
    // Calendar mode: auto-set stride to window size, disable input
    var windowSize = parseInt($('inputFixedLen').value) || DEFAULT_SEG_LEN;
    strideInput.value = windowSize;
    strideInput.disabled = true;
    strideInput.style.opacity = '0.5';
    if (strideHint) strideHint.style.display = 'block';
    fetchBoundaries();
  } else {
    // Free mode: enable stride input
    strideInput.disabled = false;
    strideInput.style.opacity = '1';
    if (strideHint) strideHint.style.display = 'none';
    S.boundaries = [];
  }
}

document.querySelectorAll('.align-mode').forEach(function(b) {
  b.addEventListener('click', function() {
    setAlignMode(this.dataset.align);
  });
});

function applyAlign() {
  var strideInput = $('inputStride');
  var strideHint = $('strideAutoHint');

  if (S.alignMode === 'calendar') {
    // Sync stride with window size in calendar mode + disable input
    var windowSize = parseInt($('inputFixedLen').value) || DEFAULT_SEG_LEN;
    strideInput.value = windowSize;
    strideInput.disabled = true;
    strideInput.style.opacity = '0.5';
    if (strideHint) strideHint.style.display = 'block';
    fetchBoundaries();
  } else {
    // Free mode: enable stride input
    strideInput.disabled = false;
    strideInput.style.opacity = '1';
    if (strideHint) strideHint.style.display = 'none';
  }
  adjustQueryToFixedLen();
}

function adjustQueryToFixedLen() {
  if (!$('chkFixed').checked || S.queryStart < 0) return;
  var fl = parseInt($('inputFixedLen').value) || DEFAULT_SEG_LEN;
  var N = S.values.length;
  if (S.alignMode === 'calendar') {
    S.queryStart = snapToNearest(S.queryStart);
    /* Use next calendar boundary as endpoint (accurate range even with data gaps) */
    var nextB = findNextBoundary(S.queryStart);
    S.queryEnd = nextB > S.queryStart ? nextB - 1 : Math.min(N - 1, S.queryStart + fl - 1);
  } else {
    S.queryEnd = Math.min(N - 1, S.queryStart + fl - 1);
  }
  if (S.queryEnd - S.queryStart < 2) {
    S.queryStart = -1; S.queryEnd = -1;
    $('btnSearch').disabled = true;
  } else {
    S.results = [];
    $('btnSearch').disabled = false;
    updateUnifiedSearchBtn();
    updateQueryInfo();
  }
  drawMainChart();
  updateActionBar();
}

$('inputFixedLen').addEventListener('change', function() {
  applyAlign();
  // Sync DrawQuery resolution with fixed_len
  if (typeof DrawQueryAPI !== 'undefined') {
    var fl = parseInt(this.value) || PRESET_RESOLUTION;
    DrawQueryAPI.setLength(fl);
    console.log('[DrawQuery] Length synced with fixed_len:', fl);
  }
});
$('sliderTopK').addEventListener('input', function(e) { $('topKVal').textContent = e.target.value; updateDetailSummary(); });
$('sliderThreshold').addEventListener('input', function(e) { $('thresholdVal').textContent = e.target.value + '%'; updateDetailSummary(); });

S.filterMode = 'topk'; // 'topk' | 'threshold'
document.querySelectorAll('.filter-mode').forEach(function(b) {
  b.addEventListener('click', function() {
    document.querySelectorAll('.filter-mode').forEach(function(q) { q.classList.remove('active'); });
    this.classList.add('active');
    S.filterMode = this.dataset.filter;
    $('filterTopK').hidden = S.filterMode !== 'topk';
    $('filterThreshold').hidden = S.filterMode !== 'threshold';
    updateDetailSummary();
  });
});
if ($('chkNorm')) $('chkNorm').addEventListener('change', updateDetailSummary);
updateDetailSummary();


// NaN interpolation method
S.nanMode = 'interpolate';
S.nanMaxRatio = 0.5;
S.nanMethod = 'linear';

// Populate NaN method dropdown from preprocessing API
(function() {
  var sel = $('selNanMethod');
  if (!sel) return;
  fetch('/api/preprocess/interpolation_methods').then(function(r){return r.json()}).then(function(data) {
    var methods = data.methods || [];
    methods.forEach(function(m) {
      var opt = document.createElement('option');
      opt.value = m.key;
      opt.textContent = m.label + ' - ' + m.description;
      sel.appendChild(opt);
    });
    if (data.default) sel.value = data.default;
    S.nanMethod = sel.value || 'linear';
  });
  sel.addEventListener('change', function() {
    S.nanMethod = this.value;

  });
})();

if ($('sliderNanRatio')) {
  $('sliderNanRatio').addEventListener('input', function(e) {
    $('nanRatioVal').textContent = e.target.value + '%';
    S.nanMaxRatio = parseInt(e.target.value) / 100;

  });
}

/* ── Settings Summary (for tags etc.) ───────────────────────────── */

function buildSettingsTags() {
  var methodNames = { euclidean: 'Euclidean', cosine: 'Cosine', dtw: 'DTW' };
  var tags = [];
  tags.push(methodNames[S.method] || S.method);
  tags.push(($('chkNorm') && $('chkNorm').checked) ? 'Z-score' : 'Raw');
  if (S.filterMode === 'threshold') {
    tags.push('>=' + ($('sliderThreshold') ? $('sliderThreshold').value : 50) + '%');
  } else {
    tags.push('Top ' + ($('sliderTopK') ? $('sliderTopK').value : DEFAULT_TOP_K));
  }
  if ($('chkFixed') && $('chkFixed').checked) {
    var fl = $('inputFixedLen') ? $('inputFixedLen').value : '';
    if (fl) tags.push(_t('js.fixed_pts', fl));
  }
  var stride = $('inputStride') ? parseInt($('inputStride').value) : 1;
  if (stride > 1) tags.push('stride ' + stride);
  return '<div class="summary-settings-row">' +
    tags.map(function(t) { return '<span class="ss-tag">' + t + '</span>'; }).join('') +
    '</div>';
}

/* ── Boundary Snap: fetch calendar boundary indices from server ───────── */
function fetchBoundaries() {
  if (!S.sid) { S.boundaries = []; return; }
  if (_fixedBoundaryMin <= 0) { S.boundaries = []; return; }
  var fl = parseInt($('inputFixedLen').value) || DEFAULT_SEG_LEN;
  fetch('/api/boundaries/' + S.sid + '?fixed_len=' + fl + '&boundary_minutes=' + _fixedBoundaryMin)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      S.boundaries = data.boundaries || [];
    })
    .catch(function() { S.boundaries = []; });
}

function snapToNearest(idx) {
  if (!S.boundaries || S.boundaries.length === 0) return idx;

  var best = S.boundaries[0];
  var bestDist = Math.abs(idx - best);
  for (var i = 1; i < S.boundaries.length; i++) {
    var d = Math.abs(idx - S.boundaries[i]);
    if (d < bestDist) { best = S.boundaries[i]; bestDist = d; }
    if (S.boundaries[i] > idx && d > bestDist) break;
  }
  return best;
}

/* Return first calendar boundary index greater than idx, or -1 if none */
function findNextBoundary(idx) {
  if (!S.boundaries || S.boundaries.length === 0) return -1;
  for (var i = 0; i < S.boundaries.length; i++) {
    if (S.boundaries[i] > idx) return S.boundaries[i];
  }
  return -1;
}

/* ── Periodicity Analysis ──────────────────────────────────────────── */
function fetchPeriodicity(sid) {
  fetch('/api/periodicity/' + sid)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var el = $('periodicityInfo');
      if (!el) return;

      var title = '<div class="prd-title">' + _t('js.periodicity') + '</div>';

      if (!data.has_periodicity) {
        el.innerHTML = title + '<div class="prd-empty">' + _t('js.no_periodicity') + '</div>';
        return;
      }

      var rows = '';
      if (data.periods && data.periods.length > 0) {
        data.periods.forEach(function(p, i) {
          var pct = Math.round(p.strength * 100);
          var isTop = (i === 0);
          var cls = isTop ? ' top' : ' rest';
          var tip = p.tooltip || '';
          rows +=
            '<div class="prd-row" title="' + tip + '">' +
              '<span class="prd-label' + (isTop ? ' top' : '') + '">' + p.description + '</span>' +
              '<div class="prd-bar-track"><div class="prd-bar-fill' + cls + '" style="width:' + pct + '%;"></div></div>' +
              '<span class="prd-pct' + (isTop ? ' top' : '') + '">' + pct + '%</span>' +
            '</div>';
        });
      }

      el.innerHTML = title + rows;
    })
    .catch(function() {
      var el = $('periodicityInfo');
      if (el) el.innerHTML =
        '<div class="prd-title">' + _t('js.periodicity') + '</div>' +
        '<div class="prd-empty">' + _t('js.analysis_failed') + '</div>';
    });
}

/* ── Utilities ────────────────────────────────────────────────── */
function show(id) { var e = $(id); if (e) e.hidden = false; }
function hide(id) { var e = $(id); if (e) e.hidden = true; }

var _loadingTimer = null;
var _loadingTipIdx = 0;
function showLoading(text, tips) {
  var o = document.querySelector('.loading-overlay');
  if (!o) { o = document.createElement('div'); o.className='loading-overlay';
    o.innerHTML='<div class="ld-ring"><div></div><div></div><div></div></div><div class="loading-text"></div><div class="loading-tip"></div>';
    document.body.appendChild(o); }
  var lt = o.querySelector('.loading-text');
  var tp = o.querySelector('.loading-tip');
  if (lt) lt.textContent = text || '';
  if (tp) tp.textContent = '';
  o.style.display = 'flex';
  // remove previous tip rotation
  if (_loadingTimer) { clearInterval(_loadingTimer); _loadingTimer = null; }
  // start tip rotation if tips provided
  if (tips && tips.length > 0) {
    _loadingTipIdx = 0;
    if (tp) tp.textContent = tips[0];
    _loadingTimer = setInterval(function() {
      _loadingTipIdx = (_loadingTipIdx + 1) % tips.length;
      if (tp) tp.textContent = tips[_loadingTipIdx];
    }, 2500);
  }
}
function hideLoading() {
  if (_loadingTimer) { clearInterval(_loadingTimer); _loadingTimer = null; }
  var o = document.querySelector('.loading-overlay');
  if (o) o.style.display = 'none';
}
