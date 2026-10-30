// 04-dataset.js — 데이터셋 목록 · 컬럼 선택 · 로드 · 초기화
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

/* ── Dataset Registry ─────────────────────────────────── */
var DATASETS = [];

function fetchDatasets() {
  fetch('/api/datasets')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      DATASETS = data.datasets;
      renderDatasetSelect(data.datasets);
      wireQuickStart();
      restoreFinderSelection();
    })
    .catch(function(err) { console.error('Failed to load datasets:', err); });
}

/* ── Restore dataset/column selection when returning from Discovery ── */
function restoreFinderSelection() {
  var raw = null;
  try { raw = sessionStorage.getItem('finder_restore'); } catch (e) {}
  if (!raw) return;
  try { sessionStorage.removeItem('finder_restore'); } catch (e) {}

  var sel;
  try { sel = JSON.parse(raw); } catch (e) { return; }
  if (!sel || !sel.datasetKey || !sel.primaryCol) return;
  if (!DATASETS.some(function(d) { return d.key === sel.datasetKey; })) return;

  var selEl = $('datasetSelect');
  if (selEl) {
    selEl.value = sel.datasetKey;
    selEl.dispatchEvent(new Event('change'));  // renders column chips synchronously
  }

  var cols = [sel.primaryCol].concat(sel.overlayCols || []).filter(Boolean);
  document.querySelectorAll('#columnChips .ds-chip').forEach(function(chip) {
    if (cols.indexOf(chip.dataset.col) >= 0) chip.classList.add('active');
  });
  updateLoadButton();

  var loading = loadDatasetColumn(sel.datasetKey, sel.primaryCol, sel.overlayCols || []);
  // Discovery 가 군집 대표 파형을 프리셋으로 넘겨 준 경우, 사용자가 다시
  // 세그먼트로 나누고 모양 사전에서 찾아 고르는 수고를 대신 밟아 준다.
  if (sel.preset) loading.then(function() { _resumeFromDiscovery(sel.preset, sel.unit); });
}

/* ── Discovery → Finder 이어받기 ──────────────────────────────
   사람이 하던 클릭(세그먼트 뷰 → 단위 선택 → 나누기 → 모양 사전 →
   프리셋 고르기)을 그대로 재생한다. 별도 경로를 만들지 않고 실제
   버튼을 눌러 진행하므로 분할 로직이 한 곳에만 남는다. */
function _resumeFromDiscovery(presetKey, unit) {
  var minutes = (SS.timeUnits[unit] || {}).minutes;
  splitModalOpen();
  var pill = minutes
    ? document.querySelector('.split-preset[data-minutes="' + minutes + '"]:not([disabled])')
    : null;
  if (pill) pill.click();
  var confirmBtn = $('btnSplitConfirm');
  if (!confirmBtn || confirmBtn.disabled) {
    if ($('splitModal')) $('splitModal').hidden = true;   // 못 나누면 조용히 물러난다
    return;
  }
  confirmBtn.click();

  // 세그먼트가 올라오면 모양 사전으로 넘어가 그 프리셋을 고른다.
  _waitFor(function() { return S.mode === 'segment' && S.segments && S.segments.length; }, function(ok) {
    if (!ok) return;
    // 목록을 다시 읽어 그린 뒤에 고른다. 먼저 고르면 재렌더가 선택을 지운다.
    loadPresets().then(function() { _pickPreset(presetKey); });
  });
}

function _pickPreset(presetKey) {
  _waitFor(function() {
      return document.querySelector('.seg-preset-btn[data-preset="' + presetKey + '"]');
    }, function(found) {
      if (!found) { if (window.showToast) showToast(_t('finder.discovery_handoff_failed')); return; }
      setQueryMode('preset');
      var btn = document.querySelector('.seg-preset-btn[data-preset="' + presetKey + '"]');
      if (!btn.classList.contains('active')) btn.click();
      btn.scrollIntoView({ block: 'nearest' });
      if (window.showToast) showToast(_t("finder.discovery_handoff_toast", btn.querySelector('.seg-preset-label').textContent));
    });
}

/* 조건이 참이 될 때까지 짧게 기다린다 (최대 15초). */
function _waitFor(cond, done, _left) {
  _left = _left == null ? 60 : _left;
  if (cond()) { done(true); return; }
  if (_left <= 0) { done(false); return; }
  setTimeout(function() { _waitFor(cond, done, _left - 1); }, 250);
}

function renderDatasetSelect(datasets) {
  var sel = $('datasetSelect');
  if (!sel) return;
  sel.innerHTML = '';
  var placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = _t('finder.select_dataset');
  sel.appendChild(placeholder);
  datasets.forEach(function(ds) {
    var opt = document.createElement('option');
    opt.value = ds.key;
    var typeLabel = ds.type === 'collection'
      ? '\u25A0 ' + _t('finder.dataset_segments', ds.source_count)
      : '\u25CB ' + _t('finder.dataset_continuous');
    var idxLabel = ds.index_type === 'numeric' ? ' [#]' : ds.index_type === 'text' ? ' [T]' : '';
    opt.textContent = (ds.name || ds.key) + '  \u2014 ' + typeLabel + idxLabel;
    sel.appendChild(opt);
  });
}

$('datasetSelect').addEventListener('change', function() {
  var key = this.value;
  var indicator = $('dsTypeIndicator');
  // Expand data selector bar when changing dataset
  var dsBar = $('dsSelectorBar');
  if (dsBar) dsBar.classList.remove('collapsed');
  var dsSummary = $('dsSelectorSummary');
  if (dsSummary) dsSummary.textContent = '';

  if (!key) {
    $('columnRow').style.display = 'none';
    if (indicator) { indicator.textContent = ''; indicator.className = 'ds-type-indicator'; }
    S.numericColumns = [];
    resetToEmpty();
    return;
  }
  var ds = DATASETS.find(function(d) { return d.key === key; });
  if (!ds || !ds.columns.length) {
    $('columnRow').style.display = 'none';
    if (indicator) { indicator.textContent = ''; indicator.className = 'ds-type-indicator'; }
    S.numericColumns = [];
    return;
  }
  // Populate numeric columns list for clustering
  S.numericColumns = ds.columns.map(function(c) { return c.col_name; });

  // show type indicator
  if (indicator) {
    var idxHint = ds.index_type === 'numeric' ? ' \u00B7 ordinal' : ds.index_type === 'text' ? ' \u00B7 text' : '';
    if (ds.type === 'collection') {
      indicator.textContent = _t('js.collection_segments', ds.source_count) + idxHint;
      indicator.className = 'ds-type-indicator type-collection';
    } else {
      indicator.textContent = _t('js.continuous') + idxHint;
      indicator.className = 'ds-type-indicator type-single';
    }
  }
  renderColumnChips(ds.columns);
  fetchPeriodicityAll(key);
});

function renderColumnChips(columns) {
  var row = $('columnRow');
  row.style.display = '';
  var container = $('columnChips');
  container.innerHTML = '';
  columns.forEach(function(c) {
    var chip = document.createElement('span');
    chip.className = 'ds-chip';
    chip.dataset.col = c.col_name;
    var textSpan = document.createElement('span');
    var text = c.label || c.col_name;
    if (c.unit) text += ' (' + c.unit + ')';
    textSpan.textContent = text;
    chip.appendChild(textSpan);
    chip.addEventListener('click', function() {
      chip.classList.toggle('active');
      updateLoadButton();
    });
    container.appendChild(chip);
  });
  updateLoadButton();
}

/* ── Batch periodicity analysis for column badges ── */
function fetchPeriodicityAll(datasetKey) {
  fetch('/api/periodicity_all/' + datasetKey)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (!data.columns) return;
      document.querySelectorAll('#columnChips .ds-chip').forEach(function(chip) {
        var col = chip.dataset.col;
        var info = data.columns[col];
        // Remove old badge
        var old = chip.querySelector('.col-prd-badge');
        if (old) old.remove();
        if (info && info.has) {
          var badge = document.createElement('span');
          badge.className = 'col-prd-badge';
          badge.title = info.period + ' (' + Math.round(info.strength * 100) + '%)';
          badge.textContent = info.period;
          chip.appendChild(badge);
        }
      });
    })
    .catch(function(e) { console.warn('[preset] Load failed:', e); });
}

function getSelectedCols() {
  var cols = [];
  document.querySelectorAll('#columnChips .ds-chip.active').forEach(function(c) {
    cols.push(c.dataset.col);
  });
  return cols;
}

function updateLoadButton() {
  var btn = $('btnLoadCols');
  var cols = getSelectedCols();
  btn.disabled = cols.length === 0;
  btn.textContent = cols.length > 0 ? 'Load (' + cols.length + ')' : 'Load';
}

$('btnLoadCols').addEventListener('click', function() {
  var dsKey = $('datasetSelect').value;
  if (!dsKey) return;
  var cols = getSelectedCols();
  console.log('[Load] btnLoadCols clicked. dsKey=' + dsKey + ', selectedCols=' + JSON.stringify(cols));
  if (cols.length === 0) return;
  loadDatasetColumn(dsKey, cols[0], cols.slice(1));
});

/* ── Data Selector Bar Collapse Toggle ── */
(function() {
  var collapseBtn = $('dsCollapseBtn');
  var selectorHeader = document.querySelector('.ds-selector-header');
  var selectorBar = $('dsSelectorBar');
  if (collapseBtn && selectorBar) {
    var toggleCollapse = function() {
      selectorBar.classList.toggle('collapsed');
    };
    collapseBtn.addEventListener('click', toggleCollapse);
    if (selectorHeader) {
      selectorHeader.addEventListener('click', function(e) {
        if (e.target === collapseBtn || collapseBtn.contains(e.target)) return;
        toggleCollapse();
      });
    }
  }
})();

/* ── Global Data Range ── */
function _populateGlobalDateRange(labels) {
  var row = $('globalDataRange');
  if (!row || !labels || labels.length === 0) {
    if (row) row.hidden = true;
    return;
  }
  var startStr = labels[0];
  var endStr = labels[labels.length - 1];
  // Store full range globally
  S._dateStartFull = startStr;
  S._dateEndFull = endStr;

  var bar = $('globalInfoBar');
  if (bar) {
    var ds = startStr.replace('T', ' ').slice(0, 19);
    var de = endStr.replace('T', ' ').slice(0, 19);
    bar.textContent = ds + ' ~ ' + de + '  |  ' + labels.length.toLocaleString() + ' ' + _t('js.pts');
  }
  var si = $('globalDateStart');
  var ei = $('globalDateEnd');
  if (si) { si.value = startStr.slice(0, 19); si.min = startStr.slice(0, 19); si.max = endStr.slice(0, 19); }
  if (ei) { ei.value = endStr.slice(0, 19); ei.min = startStr.slice(0, 19); ei.max = endStr.slice(0, 19); }
  row.hidden = false;
}

// Global date range reset button
if ($('globalDateReset')) {
  $('globalDateReset').addEventListener('click', function() {
    if (S._dateStartFull && $('globalDateStart')) $('globalDateStart').value = S._dateStartFull.slice(0, 19);
    if (S._dateEndFull && $('globalDateEnd')) $('globalDateEnd').value = S._dateEndFull.slice(0, 19);
  });
}

/* ── Data loading (auto-use preprocessed data if available) ── */
function loadDatasetColumn(datasetKey, primaryCol, overlayCols) {
  overlayCols = overlayCols || [];
  _dataInfoUserInteracted = false;
  showLoading(_t('js.loading_data'));

  var body = {
    dataset_key: datasetKey,
    primary_col: primaryCol,
    overlay_cols: overlayCols,
  };

  return fetch('/api/load', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    hideLoading();
    if (data.error) { alert(data.error); return; }

    // preprocessed data usage banner
    var banner = $('ppResultBanner');
    if (banner && data.using_preprocessed) {
      banner.innerHTML = _t('js.using_pp') + ' · <a href="/preprocess" style="color:var(--accent-dk);font-weight:600;">' + _t('js.manage_pp') + '</a>';
      banner.hidden = false;
    } else if (banner) {
      banner.hidden = true;
    }

    S.sid = data.sid;
    S.values = data.values;
    S.labels = data.labels;
    S.stats = data.stats;
    S.sourceKey = data.source_key || '';
    S.queryStart = -1;
    S.queryEnd = -1;
    S.results = [];
    S.overlays = data.overlays || [];
    S.showOverlay = true;
    S.mode = data.mode || 'continuous';
    S.datasetKey = datasetKey;
    S.primaryCol = primaryCol;
    S.overlayCols = overlayCols;
    // 표본 간격은 자료를 읽어야 알 수 있다. 선택돼 있는 창 길이를 지금 환산한다.
    if (typeof window.syncTextWindowFromPill === 'function') {
      try { window.syncTextWindowFromPill(); } catch (e) { console.error(e); }
    }
    // Persist selection for Discovery page access
    try {
      sessionStorage.setItem('finder_current_selection', JSON.stringify({
        datasetKey: datasetKey,
        primaryCol: primaryCol,
        overlayCols: overlayCols || [],
      }));
      console.log('[Finder] Saved finder_current_selection:', datasetKey, primaryCol);
    } catch (e) { console.error('[Finder] Failed to save selection:', e); }
    // Initialize selectedCols with loaded columns
    S.selectedCols = [primaryCol].concat(overlayCols || []).filter(Boolean);
    console.log('[Load] S.overlayCols set to:', JSON.stringify(S.overlayCols));
    console.log('[Load] S.selectedCols set to:', JSON.stringify(S.selectedCols));
    var mcNotice = $('multiColNotice');
    if (mcNotice) mcNotice.hidden = !(overlayCols && overlayCols.length > 0);
    // Reload presets for new dataset (custom presets are per-dataset)
    if (typeof loadPresets === 'function') loadPresets();
    S.datasetType = data.dataset_type;
    S.indexType = data.index_type || 'datetime';

    // Initialize time unit to 'none' for collections
    if (S.datasetType === 'collection' && window._setSelectedTimeUnit) {
      window._setSelectedTimeUnit('none');
    }
    // Populate global data range (datetime index only)
    if (S.indexType === 'datetime') {
      _populateGlobalDateRange(data.labels);
    } else {
      if ($('globalDataRange')) $('globalDataRange').hidden = true;
    }
    S.segments = [];
    S.segQueryIndex = -1;
    S.segResults = [];
    S.segSort = 'date';
    S.segQueryNanIndices = [];
    S.queryData = null;
    S.boundaries = [];
    S.boundaryMode = null;
    S.groundTruthLabels = data.ground_truth_labels || [];
    S.hasGroundTruth = data.has_ground_truth || false;
    S._splitMode = false;
    S._segBackup = null;
    S._contBackup = null;
    S._drilldownBackup = null; S._clusterMap = null;
    S._segBoundaries = null;
    if ($('btnDrilldownBack')) $('btnDrilldownBack').hidden = true;

    hide('emptyState');
    if (typeof clearPresetBadges === 'function') clearPresetBadges();

    // segment mode branch (collection dataset)
    if (S.mode === 'segment') {
      hide('mainChartPanel');
      if ($('classifyResults')) $('classifyResults').hidden = true;
      if ($('segSearchResults')) $('segSearchResults').hidden = true;
      show('patternPanel');
      show('dataInfoPanel');
      // segment mode: hide draw, show sample, select sample by default
      _showSegmentModePills();
      setQueryMode('sample');
      $('btnSearch').disabled = true;
      if ($('viewModeBtns')) $('viewModeBtns').hidden = false;
      loadSegmentMode(data);
      return;
    }
    // continuous mode: select draw by default, hide sample
    _showContinuousModePills();
    setQueryMode('select');
    hide('segmentPanel');
    // show view mode buttons for continuous data
    if ($('viewModeBtns')) $('viewModeBtns').hidden = false;

    // detect data interval (used for fixed-length/split presets)
    _dataIntervalMin = _detectDataInterval();
    // fixed-length preset buttons: disable presets shorter than data interval
    document.querySelectorAll('.fixed-preset').forEach(function(btn) {
      var m = parseInt(btn.dataset.minutes);
      btn.disabled = false;
      btn.title = '';
      btn.classList.remove('pill-disabled-reason');
      btn.removeAttribute('data-reason');
      if (m > 0) {
        var pts = _minutesToPoints(m);
        btn.title = pts + ' points';
        if (_dataIntervalMin > 0 && m < _dataIntervalMin) {
          btn.disabled = true;
          btn.title = _t('js.below_interval');
          btn.classList.add('pill-disabled-reason');
          btn.setAttribute('data-reason', 'interval > ' + _dataIntervalMin + 'min');
        } else if (pts < 3) {
          btn.disabled = true;
          btn.title = _t('js.not_enough_points');
          btn.classList.add('pill-disabled-reason');
          btn.setAttribute('data-reason', _t('js.only_pts', pts));
        }
      }
    });
    // sketch-preset buttons: same logic
    document.querySelectorAll('.sketch-preset').forEach(function(btn) {
      var m = parseInt(btn.dataset.minutes);
      btn.disabled = false;
      btn.title = '';
      btn.classList.remove('pill-disabled-reason');
      btn.removeAttribute('data-reason');
      if (m > 0) {
        var pts = _minutesToPoints(m);
        btn.title = pts + ' points';
        if (_dataIntervalMin > 0 && m < _dataIntervalMin) {
          btn.disabled = true;
          btn.title = _t('js.below_interval');
          btn.classList.add('pill-disabled-reason');
          btn.setAttribute('data-reason', 'interval > ' + _dataIntervalMin + 'min');
        } else if (pts < 3) {
          btn.disabled = true;
          btn.title = _t('js.not_enough_points');
          btn.classList.add('pill-disabled-reason');
          btn.setAttribute('data-reason', _t('js.only_pts', pts));
        }
      }
    });
    // Initialize sketch resolution label with default
    var defaultSketchPts = _minutesToPoints(1440);
    if (defaultSketchPts < 3) defaultSketchPts = 24;
    if ($('sketchResolutionLabel')) $('sketchResolutionLabel').textContent = _t('js.resolution_pts', defaultSketchPts);
    if ($('inputSketchLen')) $('inputSketchLen').value = defaultSketchPts;

    show('mainChartPanel');
    show('patternPanel');
    show('dataInfoPanel');

    var ds = DATASETS.find(function(d) { return d.key === datasetKey; });

    // selected columns list
    var allCols = [primaryCol].concat(overlayCols);
    var colLabels = allCols.map(function(cn) {
      var ci = ds ? ds.columns.find(function(c) { return c.col_name === cn; }) : null;
      return ci ? (ci.label || cn) : cn;
    }).join(', ');

    // per-column collapsible stats
    var primaryLabel = ds ? (function() {
      var ci = ds.columns.find(function(c) { return c.col_name === primaryCol; });
      return ci ? (ci.label || primaryCol) : primaryCol;
    })() : primaryCol;

    var colStatsHtml = buildColStatItem(primaryLabel, data.stats);

    if (S.overlays.length > 0) {
      S.overlays.forEach(function(ov) {
        if (!ov.stats) return;
        colStatsHtml += buildColStatItem(ov.name, ov.stats);
      });
    }

    $('dataInfoContent').innerHTML =
      '<div class="info-grid">' +
        '<div class="info-row"><span class="info-label">' + _t('js.lbl_dataset') + '</span><span class="info-val">' + _escapeHtml(ds ? ds.name : datasetKey) + '</span></div>' +
        '<div class="info-row"><span class="info-label">' + _t('js.lbl_columns') + '</span><span class="info-val">' + _escapeHtml(colLabels) + '</span></div>' +
      '</div>' +
      colStatsHtml +
      '<div class="prd-block" id="periodicityInfo">' +
        '<div class="prd-title">' + _t('js.periodicity') + '</div>' +
        '<div class="prd-empty">' + _t('js.analyzing') + '</div>' +
      '</div>';
    bindColStatToggles();

    // periodicity analysis
    fetchPeriodicity(data.sid);

    // fixed-length default (1 day based on data interval)
    $('chkFixed').checked = false;
    $('fixedLenGroup').hidden = true;
    var defaultFixedPts = _minutesToPoints(1440);
    if (defaultFixedPts < 3) defaultFixedPts = 24;
    $('inputFixedLen').value = defaultFixedPts;
    // Sync DrawQuery resolution with default fixed_len
    if (typeof DrawQueryAPI !== 'undefined') {
      DrawQueryAPI.setLength(defaultFixedPts);
    }

    // default fixed-length preset = 1 day boundary
    _fixedBoundaryMin = 1440;
    // pre-load boundary indices
    fetchBoundaries();

    $('chartTitleText').textContent = data.title;
    $('chipDataLen').innerHTML = '<span class="num">' + data.stats.count.toLocaleString() + '</span> ' + _t('js.pts');

    drawMainChart();
    resetResults();

    // collapse data info panel after load (save space), unless user opened it
    setTimeout(function() {
      var p = $('dataInfoPanel');
      if (p && !_dataInfoUserInteracted) p.classList.add('collapsed');
      // Also collapse data selector bar after load
      var dsBar = $('dsSelectorBar');
      var dsSummary = $('dsSelectorSummary');
      if (dsBar && dsSummary) {
        var sumText = S.datasetKey || '';
        if (S.primaryCol) sumText += ' / ' + S.primaryCol;
        if (S.overlayCols && S.overlayCols.length > 0) sumText += ' (+' + S.overlayCols.length + ')';
        dsSummary.textContent = sumText;
        dsBar.classList.add('collapsed');
      }
    }, 800);

    // Notify segment search module that data is ready
    document.dispatchEvent(new CustomEvent('dataColumnsLoaded'));
  })
  .catch(function(err) {
    hideLoading();
    alert(_t('js.alert_load_failed') + ' ' + err.message);
  });
}

/* ── Event Bindings ────────────────────────────────────────── */
function resetToEmpty() {
  _dataInfoUserInteracted = false;
  show('emptyState');
  ['mainChartPanel','dataInfoPanel','patternPanel','resultsSection','segmentPanel','segSearchResults','classifyResults']
    .forEach(hide);
  if ($('chartQueryBar')) $('chartQueryBar').hidden = true;
  $('columnRow').style.display = 'none';
  S.sid = null; S.values = []; S.labels = []; S.stats = null;
  S.queryStart = -1; S.queryEnd = -1; S.results = []; S.queryData = null;
  S.overlays = []; S.boundaries = []; S.boundaryMode = null;
  S.datasetKey = null; S.primaryCol = null; S.overlayCols = []; S.selectedCols = []; S.numericColumns = [];
  S.mode = 'continuous'; S.datasetType = null; S.indexType = 'datetime';
  S.segments = []; S.segQueryIndex = -1; S.segResults = []; S.segSort = 'date'; S.segQueryNanIndices = [];
  S._splitMode = false; S._segBackup = null; S._contBackup = null; S._drilldownBackup = null; S._clusterMap = null;
  S._segBoundaries = null; S._gapRanges = []; S._segNames = [];
  S.generatedPattern = null;
  S._dateStartFull = null; S._dateEndFull = null;
  _queryMode = 'select';  // Reset query mode to default
  if ($('globalDataRange')) $('globalDataRange').hidden = true;
  if ($('fullViewHeatmap')) { $('fullViewHeatmap').innerHTML = ''; $('fullViewHeatmap').hidden = true; }
  if (typeof clearSelectPreview === 'function') clearSelectPreview();
  updateActionBar();
  if (typeof clearPresetBadges === 'function') clearPresetBadges();
}

// load dataset list on page load
fetchDatasets();

// init basket manager + global listeners
BasketManager.init().then(function() {
  if (typeof BasketPanel !== 'undefined' && BasketPanel.init) BasketPanel.init();
});
BasketManager.onChange(function(counts) {
  var badge = $('basketBadge');
  if (badge) {
    if (counts.draft > 0) {
      badge.textContent = counts.draft;
      badge.style.display = 'inline-flex';
    } else {
      badge.textContent = '0';
      badge.style.display = 'none';
    }
  }
  document.querySelectorAll('[data-bk-key]').forEach(function(btn) {
    if (btn.dataset.bkGroupKeys) return;
    var inBk = BasketManager._keySet.has(btn.dataset.bkKey);
    btn.textContent = inBk ? '\u2713' : '+';
    btn.classList.toggle('bk-added', inBk);
    btn.title = inBk ? 'Remove from Basket' : 'Add to Basket';
    var card = btn.closest('.ssr-card, .cls-seg-card, .seg-card, .result-card, .clu-seg-card');
    if (card) card.classList.toggle('in-basket', inBk);
  });
  document.querySelectorAll('.bk-addall-btn[data-bk-group-keys]').forEach(function(btn) {
    try {
      var keys = JSON.parse(btn.dataset.bkGroupKeys);
      _bkAddAllBtnSync(btn, keys);
    } catch(e) {}
  });
});

/* ── 시작 카드 ─────────────────────────────────────────────
   빈 화면에서 한 번의 클릭으로 데이터셋 + 컬럼까지 올린다.
   카드 문구는 config/quick_start.yaml, 마크업은 index.html.
   DB에 없는 데이터셋 카드는 여기서 숨긴다. */
function wireQuickStart() {
  var wrap = $('quickStart');
  if (!wrap) return;
  var shown = 0;
  wrap.querySelectorAll('.quick-card').forEach(function(card) {
    var key = card.dataset.dataset;
    var ds = DATASETS.find(function(d) { return d.key === key; });
    var hasCol = ds && ds.columns.some(function(c) { return c.col_name === card.dataset.col; });
    if (!ds || !hasCol) { card.hidden = true; return; }
    shown++;
    var meta = card.querySelector('[data-role="ds-name"]');
    if (meta) meta.textContent = ds.name || ds.key;
    card.addEventListener('click', function() { startQuickCard(card); });
  });
  wrap.hidden = shown === 0;
}

/* 카드 한 장 = 데이터셋 선택 + 컬럼 선택 + 로드 + 예시 질의 채우기 */
function startQuickCard(card) {
  var key = card.dataset.dataset, col = card.dataset.col;
  var sel = $('datasetSelect');
  if (!sel) return;
  sel.value = key;
  sel.dispatchEvent(new Event('change'));   // 컬럼 칩을 동기적으로 그린다

  document.querySelectorAll('#columnChips .ds-chip').forEach(function(chip) {
    chip.classList.toggle('active', chip.dataset.col === col);
  });
  updateLoadButton();

  // 데이터가 올라오면 곧바로 문장 설명 모드로 넘기고 예시 질의를 채운다.
  // 카드가 약속한 문장이 화면에 보이는 채로 끝나야 다음 행동이 이어진다.
  var q = card.dataset.query || '';
  loadDatasetColumn(key, col, []).then(function() {
    if (typeof setQueryMode === 'function') setQueryMode('text');
    var el = $('segSearchInput');
    if (el && !el.value) el.value = q;
  }).catch(function() { /* 로드 실패는 loadDatasetColumn 이 이미 알린다 */ });
}
