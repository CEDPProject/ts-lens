// 22-ss-columns.js — 세그먼트 검색 — 컬럼 칩 · 프리셋 배지
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

var _COL_COLORS = { primary: [5,150,105], overlay: [[37,99,235],[217,119,6],[124,58,237],[220,38,38]] };

function _colRgb(ci) {
  return ci === 0 ? _COL_COLORS.primary : _COL_COLORS.overlay[(ci - 1) % _COL_COLORS.overlay.length];
}

function renderColChips() {
  var chipBar = $('colChipBar');
  var chipRow = $('colChipRow');
  if (!chipBar || !chipRow) return;
  chipRow.innerHTML = '';
  SS.overlayPresets = {};
  SS.activeColIdx = 0;

  var isMulti = S.overlayCols && S.overlayCols.length > 0;

  // All button: always enabled (multi-column supported via per-column tabs)
  var allBtn = document.querySelector('.seg-preset-all');
  if (allBtn) {
    allBtn.disabled = false;
    allBtn.title = isMulti ? _t('js.classify_each') : '';
  }

  if (!isMulti) {
    chipBar.hidden = true;
    return;
  }
  chipBar.hidden = false;

  var allCols = [S.primaryCol].concat(S.overlayCols);

  // ── Column chips ──
  allCols.forEach(function(col, ci) {
    var rgb = _colRgb(ci);
    var chip = document.createElement('button');
    chip.className = 'col-chip' + (ci === 0 ? ' active' : '');
    chip.dataset.idx = ci;
    chip.innerHTML =
      '<span class="col-chip-dot" style="background:rgb(' + rgb.join(',') + ');"></span>'
      + '<span class="col-chip-name">' + col + '</span>'
      + '<span class="col-chip-preset" id="chipPreset' + ci + '"></span>';

    chip.addEventListener('click', function() {
      SS.activeColIdx = ci;
      chipRow.querySelectorAll('.col-chip').forEach(function(c) { c.classList.remove('active'); });
      chip.classList.add('active');
      _highlightGridForCol(ci);
    });
    chipRow.appendChild(chip);
  });

  // Set initial preset label for primary
  _updateChipPresetLabel(0, SS.selectedPreset);
}

function _updateChipPresetLabel(ci, presetKey) {
  var el = $('chipPreset' + ci);
  if (!el) return;
  if (presetKey && presetKey !== '__all__') {
    var info = SS.presets[presetKey];
    el.textContent = info ? info.label : presetKey;
    el.className = 'col-chip-preset has-preset';
  } else {
    el.textContent = '';
    el.className = 'col-chip-preset';
  }
}

function _highlightGridForCol(ci) {
  // Show which preset is selected for this column in the grid
  var container = $('segPresetGrid');
  if (!container) return;
  var allCols = [S.primaryCol].concat(S.overlayCols || []);
  var col = allCols[ci];
  var currentPreset = null;
  if (ci === 0) {
    currentPreset = SS.selectedPreset;
  } else {
    currentPreset = SS.overlayPresets[col] || null;
  }
  container.querySelectorAll('.seg-preset-btn').forEach(function(btn) {
    var k = btn.dataset.preset;
    if (k === '__all__') return;
    btn.classList.toggle('active', k === currentPreset);
  });
  // Also deactivate __all__
  var allBtn = container.querySelector('.seg-preset-all');
  if (allBtn) allBtn.classList.remove('active');
}

/* ── Update preset badges with classify data ── */
function updatePresetBadges() {
  var container = $('segPresetGrid');
  if (!container || !SS.classifyData) return;
  var summary = SS.classifyData.group_summary || {};

  container.querySelectorAll('.seg-preset-btn').forEach(function(btn) {
    var key = btn.dataset.preset;
    // Remove old badges
    var old = btn.querySelector('.seg-preset-badge');
    if (old) old.remove();

    var info = summary[key];
    var count = info ? (info.count || 0) : 0;
    var bestSim = info ? (info.best_sim || 0) : 0;

    // Toggle dimmed
    btn.classList.toggle('dimmed', count === 0);

    if (count > 0) {
      var badge = document.createElement('span');
      badge.className = 'seg-preset-badge';
      badge.innerHTML = '<b>' + count + '</b> <em>' + (bestSim * 100).toFixed(0) + '%</em>';
      btn.appendChild(badge);
    }
  });
}

/* ── Clear preset badges (on data change) ── */
function clearPresetBadges() {
  SS.classifyData = null;
  var container = $('segPresetGrid');
  if (!container) return;
  container.querySelectorAll('.seg-preset-btn').forEach(function(btn) {
    var old = btn.querySelector('.seg-preset-badge');
    if (old) old.remove();
    btn.classList.remove('dimmed');
  });
}

/* ── Show matches for a single preset from cached classifyData ── */
function showPresetMatches(presetKey) {
  if (!SS.classifyData) return;
  var groups = SS.classifyData.groups || {};
  var summary = SS.classifyData.group_summary || {};
  var segs = groups[presetKey] || [];
  var info = summary[presetKey] || {};

  if (segs.length === 0) return;

  // Build a data structure compatible with renderSegSearchResults
  var shape = PRESET_SHAPES[presetKey] || [0,0.5,1,0.5,0];
  var presetInfo = SS.presets[presetKey] || {};
  var unitInfo = SS.timeUnits[SS.selectedTimeUnit] || {};

  var fakeData = {
    results: segs,
    query: {
      shape: shape,
      preset_name: presetInfo.label || presetKey,
      unit_label: unitInfo.label || SS.selectedTimeUnit,
      length: segs.length > 0 && segs[0].values ? segs[0].values.length : shape.length,
      type: 'preset',
    },
  };

  // Hide classify panel, show search results
  if ($('classifyResults')) $('classifyResults').hidden = true;
  renderSegSearchResults(fakeData, { hideDescribe: true });
}

/* ── Query Mode Toggle (unified) ── */
document.querySelectorAll('.query-mode').forEach(function(pill) {
  pill.addEventListener('click', function() {
    if (this.disabled) return;
    var mode = this.dataset.qmode;
    setQueryMode(mode);
  });
});

if ($('segSearchInput')) {
  $('segSearchInput').addEventListener('input', updateUnifiedSearchBtn);
  $('segSearchInput').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      if (S.mode === 'continuous') _doTextSearchContinuous();
      else doSegmentSearch();
    }
  });
}

/* 선택한 창 길이(1 day 등)를 실제 표본 간격으로 환산해 입력칸에 넣는다.
   예전에는 알약을 "누를 때만" 환산했고 _dataIntervalMin 이 0이면 조용히 넘어가,
   화면은 1 day 인데 실제로는 기본값 144 point 로 검색되는 어긋남이 있었다.
   (1시간 간격 자료면 144 point = 6일이라 결과가 일주일치로 나온다.) */
function syncTextWindowFromPill() {
  var btn = document.querySelector('.text-window-preset.active');
  var hint = $('textWindowHint');
  if (!btn) return;
  var m = parseInt(btn.dataset.minutes);
  if (!(m > 0)) {                      // Custom: 사용자가 적은 값을 그대로 둔다
    if (hint) hint.textContent = '';
    return;
  }
  if (typeof _detectDataInterval === 'function') _dataIntervalMin = _detectDataInterval();
  if (!(_dataIntervalMin > 0)) {
    if (hint) hint.textContent = _t('js.text_window_no_interval');
    return;
  }
  var pts = _minutesToPoints(m);
  if (!(pts >= 3)) {
    if (hint) hint.textContent = _t('js.text_window_too_short');
    return;
  }
  if ($('textWindowLen')) $('textWindowLen').value = pts;
  if ($('textStride')) $('textStride').value = Math.max(1, Math.floor(pts / 4));
  if (hint) hint.textContent = btn.textContent.trim() + ' = ' + pts + ' pt (' + _dataIntervalMin + ' min/pt)';
}
window.syncTextWindowFromPill = syncTextWindowFromPill;

// Text window preset pills
document.querySelectorAll('.text-window-preset').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.text-window-preset').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    var m = parseInt(btn.dataset.minutes);
    var customGroup = $('textCustomLenGroup');
    if (m === 0) {
      // Custom: show input, focus it
      if (customGroup) customGroup.hidden = false;
      if ($('textWindowHint')) $('textWindowHint').textContent = '';
      if ($('textWindowLen')) $('textWindowLen').focus();
    } else {
      if (customGroup) customGroup.hidden = true;
      syncTextWindowFromPill();
    }
  });
});

// Text hint chips: click to fill input (language-aware)
document.querySelectorAll('.text-hint-chip:not([data-verbalts])').forEach(function(chip) {
  chip.addEventListener('click', function() {
    var input = $('segSearchInput');
    if (input) {
      var lang = (document.cookie.match(/lang=(\w+)/) || [])[1] || 'ko';
      input.value = (lang === 'en' ? chip.dataset.qEn : chip.dataset.qKo) || chip.dataset.qEn || '';
      input.dispatchEvent(new Event('input'));
      input.focus();
    }
  });
});

/* ── Execute Segment Search ── */
