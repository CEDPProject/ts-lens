// 10-segment.js — 세그먼트 뷰 — 그리드 · 스파크라인 · 예시 검색
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.


/* ═══════════════════════════════════════════════════════════════
   SEGMENT MODE
   ═══════════════════════════════════════════════════════════════ */

function toggleSegmentPanel() {
  var panel = $('segmentPanel');
  if (!panel) return;
  panel.classList.toggle('seg-collapsed');
  var btn = $('btnSegToggle');
  if (btn) btn.textContent = panel.classList.contains('seg-collapsed') ? 'Expand' : 'Collapse';
}

function loadSegmentMode(presetData) {
  _dataInfoUserInteracted = false;
  show('segmentPanel');
  hide('resultsSection');
  // reset collapsed state on segment mode re-entry
  $('segmentPanel').classList.remove('seg-collapsed');
  if ($('btnSegToggle')) $('btnSegToggle').textContent = _t('js.collapse');
  // reset generated pattern on segment mode entry
  S.generatedPattern = null;

  var title = presetData.title || 'Collection';
  $('segTitleText').textContent = title;
  if ($('segHint')) $('segHint').style.display = '';
  if ($('segSummaryBar')) $('segSummaryBar').hidden = true;

  var ds = DATASETS.find(function(d) { return d.key === S.datasetKey; });
  var srcCount = presetData.source_count || '?';
  var segLen = presetData.seg_length || '?';
  // 소스마다 길이가 다르면 대표값임을 밝힌다 (첫 소스 길이를 전체로 보이면 안 된다)
  var segLenNote = '';
  if (presetData.seg_length_varies && presetData.seg_length_range) {
    segLenNote = ' <span class="mono" style="color:var(--muted)">(' +
      presetData.seg_length_range[0].toLocaleString() + '~' +
      presetData.seg_length_range[1].toLocaleString() + ')</span>';
  }
  $('dataInfoContent').innerHTML =
    '<div class="info-grid">' +
      '<div class="info-row"><span class="info-label">' + _t('js.lbl_dataset') + '</span><span class="info-val">' + (ds ? ds.name : S.datasetKey) + '</span></div>' +
      '<div class="info-row"><span class="info-label">' + _t('js.lbl_column') + '</span><span class="info-val">' + presetData.title + '</span></div>' +
      '<div class="info-row"><span class="info-label">' + _t('js.lbl_segments') + '</span><span class="info-val mono">' + (typeof srcCount === 'number' ? srcCount.toLocaleString() : srcCount) + '</span></div>' +
      '<div class="info-row"><span class="info-label">' + _t('js.lbl_segment_length') + '</span><span class="info-val mono">' + (typeof segLen === 'number' ? segLen.toLocaleString() : segLen) + ' pts' + segLenNote + '</span></div>' +
      '<div class="info-row"><span class="info-label">' + _t('js.lbl_total') + '</span><span class="info-val mono">' + presetData.stats.count.toLocaleString() + ' ' + _t('js.pts') + '</span></div>' +
    '</div>';

  // load segment data (including overlay columns)
  showLoading('Loading segments...');
  var segUrl = '/api/segments/' + S.datasetKey + '/' + S.primaryCol;
  if (S.overlayCols && S.overlayCols.length > 0) {
    segUrl += '?overlay_cols=' + encodeURIComponent(S.overlayCols.join(','));
  }
  fetch(segUrl)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      hideLoading();
      if (data.error) { alert(data.error); return; }
      S.segments = data.segments;
      $('chipSegCount').innerHTML = '<span class="num">' + data.count.toLocaleString() + '</span>';
      drawSegmentGrid();
      updateActionBar();
      // collapse data info panel unless user has manually toggled it
      setTimeout(function() {
        var p = $('dataInfoPanel');
        if (p && !_dataInfoUserInteracted) p.classList.add('collapsed');
      }, 800);
    })
    .catch(function(err) { hideLoading(); alert(_t('js.alert_segment_load_failed') + ' ' + err.message); });
}

// Reload segments for collection datasets when time unit changes
function reloadSegmentsWithTimeUnit(timeUnit) {
  if (!S.datasetKey || !S.primaryCol) return;

  showLoading(timeUnit === 'none' ? 'Loading original segments...' : 'Splitting by ' + timeUnit + '...');

  var segUrl = '/api/segments/' + S.datasetKey + '/' + S.primaryCol;
  var params = [];

  // Add overlay columns
  if (S.overlayCols && S.overlayCols.length > 0) {
    params.push('overlay_cols=' + encodeURIComponent(S.overlayCols.join(',')));
  }

  // Add time unit (skip if 'none' - use original segments)
  if (timeUnit !== 'none') {
    params.push('time_unit=' + encodeURIComponent(timeUnit));
  }

  if (params.length > 0) {
    segUrl += '?' + params.join('&');
  }

  fetch(segUrl)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      hideLoading();
      if (data.error) { alert(data.error); return; }

      // Update segments
      S.segments = data.segments;
      $('chipSegCount').innerHTML = '<span class="num">' + data.count.toLocaleString() + '</span>';

      // Clear search results since segments changed
      S.segQueryIndex = -1;
      S.segResults = [];
      if ($('segSearchResults')) $('segSearchResults').hidden = true;
      if ($('classifyResults')) $('classifyResults').hidden = true;

      // Redraw segment grid
      drawSegmentGrid();
      updateActionBar();
    })
    .catch(function(err) { hideLoading(); alert(_t('js.alert_segment_reload_failed') + ' ' + err.message); });
}

// ---- Drill-down: explore single source from collection as continuous ----
function drilldownToSource(sourceKey, sourceLabel) {
  // Save current segment state
  S._drilldownBackup = {
    segments: S.segments,
    segQueryIndex: S.segQueryIndex,
    segResults: S.segResults,
    segSort: S.segSort,
    dataInfoHTML: $('dataInfoContent').innerHTML,
    chipSegQueryHidden: $('chipSegQuery').hidden,
    chipSegQueryText: $('chipSegQuery').textContent,
    btnSearchDisabled: $('btnSearch').disabled,
    segSummaryBarHidden: $('segSummaryBar') ? $('segSummaryBar').hidden : true,
    segSummaryBarHTML: $('segSummaryBar') ? $('segSummaryBar').innerHTML : '',
    datasetKey: S.datasetKey,
    primaryCol: S.primaryCol,
    overlayCols: S.overlayCols,
    sourceLabel: sourceLabel,
  };

  showLoading('Loading source...');

  var body = {
    dataset_key: S.datasetKey,
    primary_col: S.primaryCol,
    overlay_cols: S.overlayCols || [],
    source_key: sourceKey,
  };

  fetch('/api/load', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      hideLoading();
      if (data.error) { alert(data.error); S._drilldownBackup = null; return; }

      // Switch to continuous mode
      S.sid = data.sid;
      S.values = data.values;
      S.labels = data.labels;
      S.stats = data.stats;
      S.overlays = data.overlays || [];
      S.mode = 'continuous';
      S.segments = [];
      S.segQueryIndex = -1;
      S.segResults = [];

      // Hide segment panel, show main chart
      hide('segmentPanel');
      show('mainChartPanel');
      hide('resultsSection');
      if ($('classifyResults')) $('classifyResults').hidden = true;
      if ($('segSearchResults')) $('segSearchResults').hidden = true;

      // Show back button
      var backBtn = $('btnDrilldownBack');
      if (backBtn) {
        backBtn.hidden = false;
        var dsName = '';
        var ds = DATASETS.find(function(d) { return d.key === S.datasetKey; });
        if (ds) dsName = ds.name;
        backBtn.innerHTML = '&#8592; ' + _escapeHtml(dsName) + ' / <b>' + _escapeHtml(sourceLabel) + '</b>';
      }

      // Update chart title
      $('chartTitleText').textContent = data.title || sourceLabel;
      $('chipDataLen').innerHTML = '<span class="num">' + data.values.length.toLocaleString() + '</span>';

      // Switch to continuous mode pills
      _showContinuousModePills();
      setQueryMode('select');
      if ($('viewModeBtns')) $('viewModeBtns').hidden = true;

      // Update data info
      $('dataInfoContent').innerHTML =
        '<div class="info-grid">' +
          '<div class="info-row"><span class="info-label">' + _t('js.lbl_dataset') + '</span><span class="info-val">' + _escapeHtml(dsName || S.datasetKey) + '</span></div>' +
          '<div class="info-row"><span class="info-label">' + _t('js.lbl_source') + '</span><span class="info-val">' + _escapeHtml(sourceLabel) + '</span></div>' +
          '<div class="info-row"><span class="info-label">' + _t('js.lbl_column') + '</span><span class="info-val">' + _escapeHtml(data.title) + '</span></div>' +
          '<div class="info-row"><span class="info-label">' + _t('js.lbl_points') + '</span><span class="info-val mono">' + data.values.length.toLocaleString() + '</span></div>' +
        '</div>';

      drawMainChart();
    })
    .catch(function(err) {
      hideLoading();
      alert(_t('js.alert_drilldown_failed') + ' ' + err.message);
      S._drilldownBackup = null;
    });
}

function drilldownBack() {
  var backup = S._drilldownBackup;
  if (!backup) return;

  S._drilldownBackup = null;

  // Restore segment state
  S.segments = backup.segments;
  S.segQueryIndex = backup.segQueryIndex;
  S.segResults = backup.segResults;
  S.segSort = backup.segSort;
  S.mode = 'segment';
  $('dataInfoContent').innerHTML = backup.dataInfoHTML;
  $('chipSegQuery').hidden = backup.chipSegQueryHidden;
  $('chipSegQuery').textContent = backup.chipSegQueryText;
  $('btnSearch').disabled = backup.btnSearchDisabled;
  if ($('segSummaryBar')) {
    $('segSummaryBar').hidden = backup.segSummaryBarHidden;
    $('segSummaryBar').innerHTML = backup.segSummaryBarHTML;
  }

  // Hide back button
  var backBtn = $('btnDrilldownBack');
  if (backBtn) backBtn.hidden = true;

  // Switch panels
  hide('mainChartPanel');
  show('segmentPanel');
  hide('resultsSection');
  if ($('classifyResults')) $('classifyResults').hidden = true;
  if ($('segSearchResults')) $('segSearchResults').hidden = true;

  // Restore segment mode pills
  _showSegmentModePills();
  setQueryMode('sample');
  if ($('viewModeBtns')) $('viewModeBtns').hidden = false;

  drawSegmentGrid();
  updateActionBar();
}

var _segGridPage = 0, _segGridData = null, _segGridSort = null;
function drawSegmentGrid() {
  if (_segGridData !== S.segments || _segGridSort !== S.segSort) _segGridPage = 0;
  _segGridData = S.segments;
  _segGridSort = S.segSort;
  var grid = $('segmentGrid');
  grid.innerHTML = '';

  var segs = S.segments.slice();
  var hasResults = S.segResults.length > 0;

  // Add All button for sample query results
  if (hasResults) {
    var matchIndices = {};
    S.segResults.forEach(function(r) { matchIndices[r.index] = r; });
    var matchItems = S.segments.filter(function(seg) {
      return matchIndices[seg.index] && seg.index !== S.segQueryIndex;
    }).map(function(seg) {
      var info = _makeBkInfo(seg, S.primaryCol, 'segment:sample');
      var match = matchIndices[seg.index];
      info.similarity = match.similarity;
      info.distance = match.distance;
      info.search_method = S.method;
      info.normalize = $('chkNorm') ? $('chkNorm').checked : true;
      return info;
    });
    if (matchItems.length > 0) {
      var addAllBar = document.createElement('div');
      addAllBar.className = 'ssr-addall-bar';
      var addAllBtn = document.createElement('button');
      addAllBtn.className = 'bk-addall-btn';
      _bkAddAllBtnSetup(addAllBtn, matchItems, { showCount: true });
      addAllBar.appendChild(addAllBtn);
      grid.appendChild(addAllBar);
    }
  }

  if (S.segSort === 'similarity' && hasResults) {
    var simMap = {};
    S.segResults.forEach(function(r, i) { simMap[r.index] = { sim: r.similarity, rank: i + 1 }; });
    segs.sort(function(a, b) {
      if (a.index === S.segQueryIndex) return -1;
      if (b.index === S.segQueryIndex) return 1;
      var sa = simMap[a.index], sb = simMap[b.index];
      if (sa && sb) return sb.sim - sa.sim;
      if (sa) return -1;
      if (sb) return 1;
      return a.index - b.index;
    });
  }

  var simMap2 = {};
  S.segResults.forEach(function(r, i) { simMap2[r.index] = { sim: r.similarity, rank: i + 1 }; });

  var queryVals = null;
  if (S.segQueryIndex >= 0) {
    var qs = S.segments.find(function(s) { return s.index === S.segQueryIndex; });
    if (qs) queryVals = qs.values;
  }

  var pageSize = 120;
  var pageCount = Math.max(1, Math.ceil(segs.length / pageSize));
  _segGridPage = Math.min(_segGridPage, pageCount - 1);
  segs.slice(_segGridPage * pageSize, (_segGridPage + 1) * pageSize).forEach(function(seg) {
    var isQuery = seg.index === S.segQueryIndex;
    var matchInfo = simMap2[seg.index];
    var isMatch = !!matchInfo;
    // dim unmatched cards when results are shown
    var isDimmed = hasResults && !isQuery && !isMatch;

    var card = document.createElement('div');
    card.className = 'seg-card' +
      (isQuery ? ' is-query' : '') +
      (isMatch ? ' is-match' : '') +
      (isDimmed ? ' is-dimmed' : '');
    card.dataset.segIndex = seg.index;

    var canvas = document.createElement('canvas');
    canvas.className = 'seg-card-canvas';
    canvas.width = 320; canvas.height = 140;
    card.appendChild(canvas);

    // column legend (primary + overlay)
    var segOvs = seg.overlays || null;
    if (segOvs && segOvs.length > 0) {
      var ovColors = ['#E11D48', '#8B5CF6', '#0891B2', '#D97706'];
      var primaryColor = isQuery ? '#059669' : (isMatch ? '#F59E0B' : '#3B82F6');
      var legend = document.createElement('div');
      legend.className = 'seg-card-legend';
      var primaryName = S.primaryCol || 'primary';
      // find label from DATASETS
      var _ds = DATASETS.find(function(d) { return d.key === S.datasetKey; });
      if (_ds) {
        var _ci = _ds.columns.find(function(c) { return c.col_name === S.primaryCol; });
        if (_ci) primaryName = _ci.label || S.primaryCol;
      }
      legend.innerHTML = '<span class="seg-legend-item"><span class="seg-legend-line" style="background:' + primaryColor + '"></span>' + _escapeHtml(primaryName) + '</span>';
      segOvs.forEach(function(ov, oi) {
        var c = ovColors[oi % ovColors.length];
        legend.innerHTML += '<span class="seg-legend-item"><span class="seg-legend-line seg-legend-dash" style="border-color:' + c + '"></span>' + _escapeHtml(ov.name || 'overlay') + '</span>';
      });
      card.appendChild(legend);
    }

    // info row: label + similarity
    var info = document.createElement('div');
    info.className = 'seg-card-info';
    info.title = seg.label || '';
    info.innerHTML = '<span class="seg-card-label">' + _escapeHtml(seg.label) + '</span>';
    if (isMatch) {
      info.innerHTML += '<span class="seg-card-sim">' + (matchInfo.sim * 100).toFixed(1) + '%</span>';
    }
    card.appendChild(info);

    // display start/end/point count
    var vals = seg.values;
    var segLen = seg.length || vals.length;
    var timeRange = document.createElement('div');
    timeRange.className = 'seg-card-meta';
    var startLabel = seg.start || (seg.labels && seg.labels[0]) || '';
    var endLabel = seg.end || (seg.labels && seg.labels[seg.labels.length - 1]) || '';
    if (startLabel && endLabel) {
      // short display (date portion only)
      var s = startLabel.length > 16 ? startLabel.substring(5, 16) : startLabel;
      var e = endLabel.length > 16 ? endLabel.substring(5, 16) : endLabel;
      timeRange.textContent = s + ' ~ ' + e + '  (' + segLen + 'pts)';
    } else {
      timeRange.textContent = segLen + ' pts';
    }
    card.appendChild(timeRange);

    // Meta row: min/max/mean
    var cleanVals = vals.filter(function(v) { return v != null; });
    var mean = cleanVals.length ? cleanVals.reduce(function(a, b) { return a + b; }, 0) / cleanVals.length : 0;
    var mn = Infinity, mx = -Infinity;
    for (var vi = 0; vi < cleanVals.length; vi++) { if (cleanVals[vi] < mn) mn = cleanVals[vi]; if (cleanVals[vi] > mx) mx = cleanVals[vi]; }
    var meta = document.createElement('div');
    meta.className = 'seg-card-meta';
    meta.textContent = mn.toFixed(1) + ' ~ ' + mx.toFixed(1) + '  avg ' + mean.toFixed(1);
    card.appendChild(meta);

    // Badges
    if (isQuery) {
      var badge = document.createElement('span');
      badge.className = 'seg-card-query-badge';
      badge.textContent = _t('js.query');
      card.appendChild(badge);
    } else if (isMatch) {
      var rankEl = document.createElement('span');
      rankEl.className = 'seg-card-rank';
      rankEl.textContent = '#' + matchInfo.rank;
      card.appendChild(rankEl);
    }

    // Drill-down button (collection datasets only)
    if (S.datasetType === 'collection' && seg.source_key) {
      var drillBtn = document.createElement('button');
      drillBtn.className = 'seg-card-drill';
      drillBtn.title = _t('js.explore_source');
      drillBtn.innerHTML = '&#x2197;';  // north-east arrow
      drillBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        drilldownToSource(seg.source_key, seg.label);
      });
      card.appendChild(drillBtn);
    }

    // Basket [+] button
    var _segBkInfo = _makeBkInfo(seg, S.primaryCol, 'segment');
    if (isMatch) {
      var _segMatch = S.segResults.find(function(r) { return r.index === seg.index; });
      if (_segMatch) {
        _segBkInfo.similarity = _segMatch.similarity;
        _segBkInfo.distance = _segMatch.distance;
        _segBkInfo.search_method = S.method;
        _segBkInfo.normalize = $('chkNorm') ? $('chkNorm').checked : true;
      }
    }
    var _segBkKey = BasketManager._makeKey(_segBkInfo);
    var _segInBk = BasketManager._keySet.has(_segBkKey);
    var segBkBtn = document.createElement('button');
    segBkBtn.className = 'bk-add-btn seg-bk-btn' + (_segInBk ? ' bk-added' : '');
    segBkBtn.title = _segInBk ? 'Remove from Basket' : 'Add to Basket';
    segBkBtn.textContent = _segInBk ? '\u2713' : '+';
    segBkBtn.dataset.bkKey = _segBkKey;
    if (_segInBk) { card.classList.add('in-basket'); }
    (function(_info) {
      _bkWireBtn(segBkBtn, _info);
    })(_segBkInfo);
    card.appendChild(segBkBtn);

    card.addEventListener('click', function() {
      S.segQueryIndex = seg.index;
      S.segResults = [];
      S.segSort = 'date';
      $('chipSegQuery').hidden = false;
      $('chipSegQuery').textContent = _t('js.query_label', seg.label);
      $('btnSearch').disabled = false;
      if ($('segHint')) $('segHint').style.display = 'none';
      if ($('segSummaryBar')) $('segSummaryBar').hidden = true;
      document.querySelectorAll('.seg-sort').forEach(function(b) { b.classList.remove('active'); });
      var dateBtn = document.querySelector('.seg-sort[data-sort="date"]');
      if (dateBtn) dateBtn.classList.add('active');
      drawSegmentGrid();
      updateActionBar();
      // Sync sample mode sidebar
      if (_queryMode === 'sample') {
        _syncSampleSelection();
        updateUnifiedSearchBtn();
      }
    });

    grid.appendChild(card);

    drawSparkline(canvas, seg.values, 0, 1,  // min/range ignored, using local scale
      isQuery ? '#059669' : (isMatch ? '#F59E0B' : '#3B82F6'),
      isQuery ? 2.5 : 1.5,
      isMatch && queryVals ? queryVals : null,
      seg.overlays || null,
      isQuery ? S.segQueryNanIndices : null);
  });
  if (pageCount > 1) {
    var pager = document.createElement('div');
    pager.className = 'seg-grid-pager';
    pager.style.cssText = 'grid-column:1/-1;display:flex;gap:12px;align-items:center;justify-content:center;padding:12px';
    [-1, 1].forEach(function(direction) {
      var button = document.createElement('button');
      button.className = 'btn btn-secondary btn-sm';
      button.textContent = direction < 0 ? '←' : '→';
      button.setAttribute('aria-label', _t(direction < 0 ? 'js.segment_page_prev' : 'js.segment_page_next'));
      button.disabled = direction < 0 ? _segGridPage === 0 : _segGridPage === pageCount - 1;
      button.addEventListener('click', function() {
        _segGridPage += direction;
        drawSegmentGrid();
        grid.scrollIntoView({block: 'start'});
      });
      if (direction > 0) {
        var label = document.createElement('span');
        label.textContent = (_segGridPage + 1) + ' / ' + pageCount;
        pager.appendChild(label);
      }
      pager.appendChild(button);
    });
    grid.appendChild(pager);
  }
}

function drawSparkline(canvas, rawValues, globalMin, yRange, color, lineWidth, overlayValues, segOverlays, nanIndices) {
  var W = canvas.width, H = canvas.height;
  var ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);
  // null/NaN safe: interpolation for drawing
  var hasNull = rawValues.some(function(v) { return v == null; });
  var values = hasNull ? interpolateNulls(rawValues) : rawValues;
  var N = values.length;
  if (N === 1 && values[0] != null && Number.isFinite(values[0])) {
    ctx.beginPath();
    ctx.arc(W / 2, H / 2, Math.max(3, lineWidth * 2), 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    return;
  }
  if (N < 2) return;

  // Use LOCAL min/max for each segment (shape-focused like preset mode)
  var localMin = Infinity, localMax = -Infinity;
  for (var vi = 0; vi < values.length; vi++) {
    if (values[vi] != null) {
      if (values[vi] < localMin) localMin = values[vi];
      if (values[vi] > localMax) localMax = values[vi];
    }
  }
  var localRange = localMax - localMin || 1;

  var pad = { t: 4, r: 4, b: 4, l: 4 };
  var cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
  var toX = function(i) { return pad.l + (i / (N - 1)) * cw; };
  var toY = function(v) { return pad.t + ch * (1 - (v - localMin) / localRange); };

  ctx.beginPath();
  ctx.moveTo(toX(0), pad.t + ch);
  for (var i = 0; i < N; i++) ctx.lineTo(toX(i), toY(values[i]));
  ctx.lineTo(toX(N - 1), pad.t + ch);
  ctx.closePath();
  ctx.fillStyle = color + '30';  // more visible fill (was 15)
  ctx.fill();

  ctx.beginPath();
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  for (var j = 0; j < N; j++) {
    if (j === 0) ctx.moveTo(toX(j), toY(values[j]));
    else ctx.lineTo(toX(j), toY(values[j]));
  }
  ctx.stroke();

  // query overlay (for match comparison) - use its own local scale for shape comparison
  if (overlayValues && overlayValues.length === N) {
    var ovMin = Infinity, ovMax = -Infinity;
    for (var oi = 0; oi < overlayValues.length; oi++) {
      if (overlayValues[oi] != null) {
        if (overlayValues[oi] < ovMin) ovMin = overlayValues[oi];
        if (overlayValues[oi] > ovMax) ovMax = overlayValues[oi];
      }
    }
    var ovRange = ovMax - ovMin || 1;
    var toYov = function(v) { return pad.t + ch * (1 - (v - ovMin) / ovRange); };

    ctx.beginPath();
    ctx.strokeStyle = '#059669';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.globalAlpha = 0.5;
    for (var k = 0; k < N; k++) {
      if (k === 0) ctx.moveTo(toX(k), toYov(overlayValues[k]));
      else ctx.lineTo(toX(k), toYov(overlayValues[k]));
    }
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1.0;
  }

  // additional column overlays (multiple columns in split segments)
  if (segOverlays && segOverlays.length > 0) {
    var ovColors = ['#E11D48', '#8B5CF6', '#0891B2', '#D97706'];
    segOverlays.forEach(function(ov, oi) {
      if (!ov.values || ov.values.length < 2) return;
      // overlay drawn with its own scale
      var ovMin = Infinity, ovMax = -Infinity;
      ov.values.forEach(function(v) { if (v < ovMin) ovMin = v; if (v > ovMax) ovMax = v; });
      var ovRange = ovMax - ovMin || 1;
      var ovN = ov.values.length;
      var toXov = function(i) { return pad.l + (i / (ovN - 1)) * cw; };
      var toYov = function(v) { return pad.t + ch * (1 - (v - ovMin) / ovRange); };

      ctx.beginPath();
      ctx.strokeStyle = ovColors[oi % ovColors.length];
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 2]);
      ctx.globalAlpha = 0.5;
      for (var m = 0; m < ov.values.length; m++) {
        if (m === 0) ctx.moveTo(toXov(m), toYov(ov.values[m]));
        else ctx.lineTo(toXov(m), toYov(ov.values[m]));
      }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1.0;
    });
  }

  // mark NaN interpolated positions
  if (nanIndices && nanIndices.length > 0) {
    ctx.fillStyle = '#EF4444';
    nanIndices.forEach(function(idx) {
      if (idx < N) {
        ctx.beginPath();
        ctx.arc(toX(idx), toY(values[idx]), 3, 0, 2 * Math.PI);
        ctx.fill();
      }
    });
  }
}

function drawMiniCanvas(canvas, values, opts) {
  opts = opts || {};
  var dpr = window.devicePixelRatio || 1;
  var cssW = canvas.offsetWidth || 280;
  var cssH = canvas.offsetHeight || 60;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  var ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  if (opts.bgColor) {
    ctx.fillStyle = opts.bgColor;
    ctx.fillRect(0, 0, cssW, cssH);
  }

  var N = values.length;
  if (N < 2) return;

  var mn = Infinity, mx = -Infinity;
  for (var i = 0; i < N; i++) {
    if (values[i] != null) {
      if (values[i] < mn) mn = values[i];
      if (values[i] > mx) mx = values[i];
    }
  }
  var rng = mx - mn || 1;

  var pad = { t: 2, r: 4, b: 2, l: 4 };
  var cw = cssW - pad.l - pad.r, ch = cssH - pad.t - pad.b;
  var toX = function(i) { return pad.l + (i / (N - 1)) * cw; };
  var toY = function(v) { return pad.t + ch * (1 - (v - mn) / rng); };

  if (opts.fill) {
    ctx.beginPath();
    ctx.moveTo(toX(0), pad.t + ch);
    for (var i = 0; i < N; i++) {
      ctx.lineTo(toX(i), toY(values[i] != null ? values[i] : mn));
    }
    ctx.lineTo(toX(N - 1), pad.t + ch);
    ctx.closePath();
    ctx.fillStyle = opts.fillColor || 'rgba(5,150,105,0.06)';
    ctx.fill();
  }

  ctx.beginPath();
  ctx.strokeStyle = opts.color || 'rgba(5,150,105,1)';
  ctx.lineWidth = opts.lineWidth || 1.5;
  var started = false;
  for (var j = 0; j < N; j++) {
    if (values[j] == null) { started = false; continue; }
    if (!started) { ctx.moveTo(toX(j), toY(values[j])); started = true; }
    else ctx.lineTo(toX(j), toY(values[j]));
  }
  ctx.stroke();

  if (opts.overlays) {
    opts.overlays.forEach(function(ov) {
      if (!ov.values || ov.values.length < 2) return;
      var oMn = Infinity, oMx = -Infinity;
      for (var k = 0; k < ov.values.length; k++) {
        var v = ov.values[k];
        if (v != null) { if (v < oMn) oMn = v; if (v > oMx) oMx = v; }
      }
      var oRng = oMx - oMn || 1;
      var oN = ov.values.length;
      ctx.beginPath();
      ctx.strokeStyle = ov.color || '#2563EB';
      ctx.lineWidth = ov.lineWidth || 1.2;
      if (ov.dash) ctx.setLineDash(ov.dash);
      if (ov.opacity) ctx.globalAlpha = ov.opacity;
      var s = false;
      for (var m = 0; m < oN; m++) {
        if (ov.values[m] == null) { s = false; continue; }
        var x = pad.l + (m / (oN - 1)) * cw;
        var y = pad.t + ch * (1 - (ov.values[m] - oMn) / oRng);
        if (!s) { ctx.moveTo(x, y); s = true; } else ctx.lineTo(x, y);
      }
      ctx.stroke();
      if (ov.dash) ctx.setLineDash([]);
      if (ov.opacity) ctx.globalAlpha = 1.0;
    });
  }

  if (opts.queryValues && opts.queryValues.length > 0) {
    ctx.beginPath();
    ctx.strokeStyle = '#059669';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.globalAlpha = 0.6;
    var qN = opts.queryValues.length;
    var s2 = false;
    for (var q = 0; q < qN; q++) {
      if (opts.queryValues[q] == null) { s2 = false; continue; }
      var qx = pad.l + (q / (qN - 1)) * cw;
      var qy = toY(opts.queryValues[q]);
      if (!s2) { ctx.moveTo(qx, qy); s2 = true; } else ctx.lineTo(qx, qy);
    }
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1.0;
  }

  if (opts.nanIndices && opts.nanIndices.length > 0) {
    ctx.fillStyle = '#EF4444';
    opts.nanIndices.forEach(function(idx) {
      if (idx < N && values[idx] != null) {
        ctx.beginPath();
        ctx.arc(toX(idx), toY(values[idx]), 3, 0, 2 * Math.PI);
        ctx.fill();
      }
    });
  }
}

function searchSegments() {
  if (S._searchBusy) return;
  if (S.segQueryIndex < 0) return;

  var btn = $('btnSearch');
  setSearchBusy(true);
  btn.innerHTML = '<span class="btn-spinner"></span> ' + _t('js.searching'); btn.disabled = true; btn.classList.add('btn-loading');

  // segment length and count info
  var _segPts = 0;
  var _segCount = S.segments ? S.segments.length : 0;
  if (S.segments && _segCount > 0 && S.segments[0].values) {
    _segPts = S.segments[0].values.length;
  }
  var segTips = [
    'Comparing ' + _segPts + 'pts query against ' + (_segCount - 1) + ' segments 1:1',
    'Computing similarity for each segment using ' + S.method + ' distance...',
    'Normalizing scales with z-score...',
    'Sorting results by similarity...',
  ];
  if (_segPts > 500) segTips.push('Segment ' + _segPts + 'pts \u2192 auto-downsampled to 500pts');
  showLoading('Comparing segments...', segTips);

  // split mode vs DB segment mode
  var url, bodyData;
  var topK, threshold;
  if (S.filterMode === 'threshold') {
    topK = UNLIMITED_TOP_K;
    threshold = parseInt($('sliderThreshold').value) / 100;
  } else {
    topK = $('sliderTopK') ? parseInt($('sliderTopK').value) : DEFAULT_TOP_K;
    threshold = 0;
  }
  if (S._splitMode) {
    url = '/api/search_split_segments/' + S.sid;
    bodyData = {
      segments: S.segments,
      query_index: S.segQueryIndex,
      method: S.method,
      normalize: $('chkNorm') ? $('chkNorm').checked : true,
      top_k: topK,
      threshold: threshold,
    };
  } else {
    url = '/api/search_segments/' + S.datasetKey + '/' + S.primaryCol;
    bodyData = {
      segments: S.segments,  // Send current segments to avoid index mismatch
      query_index: S.segQueryIndex,
      method: S.method,
      normalize: $('chkNorm') ? $('chkNorm').checked : true,
      top_k: topK,
      threshold: threshold,
      overlay_cols: S.overlayCols || [],
    };
  }
  bodyData.nan_mode = 'interpolate';
  bodyData.nan_max_ratio = S.nanMaxRatio != null ? S.nanMaxRatio : 0.5;
  bodyData.nan_method = S.nanMethod || 'linear';

  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(bodyData),
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    hideLoading();
    if (data.error) { alert(data.error); btn.textContent = _t('js.search'); btn.disabled = false; btn.classList.remove('btn-loading'); return; }
    if (data._debug) console.log('[seg search debug]', data._debug);
    if (!data.results || data.results.length === 0) {
      showNoResults('seg', data._debug);
      btn.textContent = _t('js.search'); btn.disabled = false; btn.classList.remove('btn-loading');
      return;
    }
    S.segResults = data.results;
    S.segQueryNanIndices = (data.query && data.query.nan_indices) ? data.query.nan_indices : [];
    S.segSort = 'similarity';

    document.querySelectorAll('.seg-sort').forEach(function(b) { b.classList.remove('active'); });
    var simBtn = document.querySelector('.seg-sort[data-sort="similarity"]');
    if (simBtn) simBtn.classList.add('active');

    // top summary bar (inside segment panel)
    var bar = $('segSummaryBar');
    if (bar) {
      bar.hidden = false;
      bar.className = 'seg-summary-bar has-results';
      bar.innerHTML =
        '<div class="summary-stats-row">' +
        '<div class="seg-summary-stat"><div class="seg-stat-val" style="color:var(--green)">' + _escapeHtml(data.query.label) + '</div><div class="seg-stat-label">' + _t('js.sum_query') + '</div></div>' +
        '<div class="seg-summary-divider"></div>' +
        '<div class="seg-summary-stat"><div class="seg-stat-val" style="color:var(--blue)">' + data.summary.count + '</div><div class="seg-stat-label">' + _t('js.sum_matches') + '</div></div>' +
        '<div class="seg-summary-divider"></div>' +
        '<div class="seg-summary-stat"><div class="seg-stat-val" style="color:var(--amber)">' + (data.summary.best * 100).toFixed(1) + '%</div><div class="seg-stat-label">' + _t('js.sum_best') + '</div></div>' +
        '<div class="seg-summary-divider"></div>' +
        '<div class="seg-summary-stat"><div class="seg-stat-val">' + (data.summary.avg * 100).toFixed(1) + '%</div><div class="seg-stat-label">' + _t('js.sum_average') + '</div></div>' +
        '<div class="seg-summary-divider"></div>' +
        '<div class="seg-summary-stat"><div class="seg-stat-val" style="color:var(--muted)">' + (data.summary.worst * 100).toFixed(1) + '%</div><div class="seg-stat-label">' + _t('js.sum_worst') + '</div></div>' +
        (data.columns_used > 1 ? '<div class="seg-summary-divider"></div><div class="seg-summary-stat"><div class="seg-stat-val" style="color:var(--cyan)">' + data.columns_used + '</div><div class="seg-stat-label">' + _t('js.sum_columns') + '</div></div>' : '') +
        '</div>' +
        buildSettingsTags();
    }

    drawSegmentGrid();
    btn.textContent = _t('js.search'); btn.disabled = false; btn.classList.remove('btn-loading');
  })
  .catch(function(err) {
    hideLoading(); alert(_t('js.alert_search_failed') + ' ' + err.message);
    btn.textContent = _t('js.search'); btn.disabled = false; btn.classList.remove('btn-loading');
  }).finally(function() { setSearchBusy(false); });
}

document.querySelectorAll('.seg-sort').forEach(function(b) {
  b.addEventListener('click', function() {
    document.querySelectorAll('.seg-sort').forEach(function(q) { q.classList.remove('active'); });
    this.classList.add('active');
    S.segSort = this.dataset.sort;
    drawSegmentGrid();
  });
});

/* ── Segment ↔ Continuous Switch ─────────────────────────────── */
$('btnSegConcat').addEventListener('click', function() {
  // splitMode: return from split segments to original continuous
  if (S._splitMode && S._contBackup) {
    S.sid = S._contBackup.sid;
    S.values = S._contBackup.values;
    S.labels = S._contBackup.labels;
    S.stats = S._contBackup.stats;
    S.queryStart = S._contBackup.queryStart;
    S.queryEnd = S._contBackup.queryEnd;
    S.results = S._contBackup.results;
    S.overlays = S._contBackup.overlays;
    S.mode = 'continuous';
    S.segments = [];
    S.segQueryIndex = -1;
    S.segResults = [];
    S._splitMode = false;

    hide('segmentPanel');
    show('mainChartPanel');
    _showContinuousModePills();
    setQueryMode('select');
    if ($('viewModeBtns')) $('viewModeBtns').hidden = false;
    $('btnSegConcat').textContent = _t('js.full_view');

    // restore sidebar
    $('dataInfoContent').innerHTML = S._contBackup.dataInfoHTML;

    if (S.queryStart >= 0) {
      $('btnSearch').disabled = false;
      updateQueryInfo();
    } else {
      $('btnSearch').disabled = true;
      if ($('chartQueryBar')) $('chartQueryBar').hidden = true;
    }

    $('chipDataLen').innerHTML = '<span class="num">' + S.stats.count.toLocaleString() + '</span> pts';
    $('chipQuery').hidden = S.queryStart < 0;

    S._contBackup = null;
    drawMainChart();
    updateActionBar();
    return;
  }

  if (!S.datasetKey || !S.primaryCol) return;
  showLoading('Concatenating segments...');

  var concatUrl = '/api/concat_segments/' + S.datasetKey + '/' + S.primaryCol;
  if (S.overlayCols && S.overlayCols.length > 0) {
    concatUrl += '?overlay_cols=' + encodeURIComponent(S.overlayCols.join(','));
  }
  fetch(concatUrl)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      hideLoading();
      if (data.error) { alert(data.error); return; }

      // preserve existing segment state (for returning)
      S._segBackup = {
        segments: S.segments,
        segQueryIndex: S.segQueryIndex,
        segResults: S.segResults,
        segSort: S.segSort,
        dataInfoHTML: $('dataInfoContent').innerHTML,
        chipSegQueryHidden: $('chipSegQuery').hidden,
        chipSegQueryText: $('chipSegQuery').textContent,
        btnSearchDisabled: $('btnSearch').disabled,
        segSummaryBarHidden: $('segSummaryBar').hidden,
        segSummaryBarHTML: $('segSummaryBar').innerHTML,
      };

      // switch to continuous mode
      S.sid = data.sid;
      S.values = data.values;
      S.labels = data.labels;
      S.stats = data.stats;
      S.queryStart = -1;
      S.queryEnd = -1;
      S.results = [];
      S.mode = 'continuous';
      S._splitMode = false;  // Clear split mode flag when concatenating
      S.overlays = data.overlays || [];
      S._segBoundaries = data.seg_boundaries || [];
      S._gapRanges = data.gap_ranges || [];
      S._segNames = data.seg_names || [];
      S.sourceKey = ''; // A stitched display has no single source.

      // UI switch
      hide('segmentPanel');
      if ($('classifyResults')) $('classifyResults').hidden = true;
      if ($('segSearchResults')) $('segSearchResults').hidden = true;
      show('mainChartPanel');

      // Copy heatmap to full view if available
      var fvHeatmap = $('fullViewHeatmap');
      if (fvHeatmap) {
        var srcHeatmap = document.querySelector('.cls-corr-heatmap-wrap');
        if (srcHeatmap) {
          fvHeatmap.innerHTML = srcHeatmap.outerHTML;
          fvHeatmap.hidden = false;
        } else {
          fvHeatmap.innerHTML = '';
          fvHeatmap.hidden = true;
        }
      }
      _showContinuousModePills();
      setQueryMode('select');
      $('btnSearch').disabled = true;
      if ($('viewModeBtns')) $('viewModeBtns').hidden = false;

      $('chartTitleText').textContent = data.title + ' · ' + _t('js.source_full_display');
      $('chipDataLen').innerHTML = '<span class="num">' + data.stats.count.toLocaleString() + '</span> pts';
      $('chipQuery').hidden = true;

      resetResults();
      drawMainChart();

      // Gap region background + segment name labels
      if (S._gapRanges.length > 0 || S._segBoundaries.length > 1) {
        var shapes = [];
        var annotations = [];

        // gap region background + label
        for (var gi = 0; gi < S._gapRanges.length; gi++) {
          var gr = S._gapRanges[gi];
          // dark background + dashed border
          shapes.push({
            type: 'rect', xref: 'x', yref: 'paper',
            x0: gr[0] - 0.5, x1: gr[1] + 0.5, y0: 0, y1: 1,
            fillcolor: 'rgba(107,114,128,0.12)',
            line: { color: 'rgba(107,114,128,0.35)', width: 1, dash: 'dash' },
            layer: 'below',
          });
          // hatching effect (repeated vertical lines)
          var gapWidth = gr[1] - gr[0];
          var step = Math.max(4, Math.round(gapWidth / 8));
          for (var hi = gr[0]; hi <= gr[1]; hi += step) {
            shapes.push({
              type: 'line', xref: 'x', yref: 'paper',
              x0: hi, x1: hi + step * 0.6, y0: 0, y1: 1,
              line: { color: 'rgba(107,114,128,0.1)', width: 1 },
              layer: 'below',
            });
          }
          // center label
          annotations.push({
            x: (gr[0] + gr[1]) / 2, xref: 'x',
            y: 0.5, yref: 'paper',
            text: '<b>' + _t('js.gap') + '</b>',
            showarrow: false,
            font: { size: 10, color: 'rgba(107,114,128,0.5)', family: 'DM Mono' },
            xanchor: 'center', yanchor: 'middle',
          });
        }

        // segment boundary lines (at gap start and end)
        for (var bi = 0; bi < S._segBoundaries.length; bi++) {
          var bIdx = S._segBoundaries[bi];
          shapes.push({
            type: 'line', xref: 'x', yref: 'paper',
            x0: bIdx - 0.5, x1: bIdx - 0.5, y0: 0, y1: 1,
            line: { color: 'rgba(124,58,237,0.25)', width: 1, dash: 'dot' },
          });
          // segment name label
          if (bi < S._segNames.length) {
            annotations.push({
              x: bIdx + 2, xref: 'x',
              y: 1.02, yref: 'paper',
              text: S._segNames[bi],
              showarrow: false,
              font: { size: 9, color: 'rgba(124,58,237,0.6)', family: 'DM Sans' },
              xanchor: 'left', yanchor: 'bottom',
            });
          }
        }

        Plotly.relayout('plotMain', { shapes: shapes, annotations: annotations });
      }
    })
    .catch(function(err) { hideLoading(); alert(_t('js.alert_concat_failed') + ' ' + err.message); });
});

$('btnToSeg').addEventListener('click', function() {
  // Always open split modal for both continuous and collection datasets
  splitModalOpen();
});

/* ── Panel Collapse / Expand ──────────────────────────────────── */
document.querySelectorAll('.panel .panel-title').forEach(function(title) {
  title.addEventListener('click', function(e) {
    // ignore info-btn clicks
    if (e.target.closest('.info-btn')) return;
    var panel = title.closest('.panel');
    if (panel && panel.id === 'dataInfoPanel') {
      _dataInfoUserInteracted = true;
    }
    panel.classList.toggle('collapsed');
  });
});

/* ── Info Tooltip Buttons (position:fixed, escapes overflow) ──── */
function _closeAllInfoPopups() {
  document.querySelectorAll('.info-btn.active').forEach(function(b) {
    b.classList.remove('active');
  });
}
function _positionInfoPopup(btn) {
  var popup = btn.nextElementSibling;
  if (!popup || !popup.classList.contains('info-popup')) return;
  var r = btn.getBoundingClientRect();
  var pw = 260; // popup width
  var pad = 8;
  // horizontal: right-align to button, but keep on screen
  var left = Math.min(r.right - pw, window.innerWidth - pw - pad);
  left = Math.max(pad, left);
  // arrow position relative to popup
  var arrowRight = Math.max(8, Math.min(pw - 16, r.left + r.width / 2 - left - 4));
  // vertical: prefer below, flip above if no room
  popup.classList.remove('arrow-top', 'arrow-bottom');
  var spaceBelow = window.innerHeight - r.bottom - 10;
  if (spaceBelow >= 180) {
    popup.style.top = (r.bottom + 6) + 'px';
    popup.style.bottom = '';
    popup.classList.add('arrow-top');
  } else {
    popup.style.top = '';
    popup.style.bottom = (window.innerHeight - r.top + 6) + 'px';
    popup.classList.add('arrow-bottom');
  }
  popup.style.left = left + 'px';
  popup.style.right = 'auto';
  popup.style.setProperty('--arrow-right', arrowRight + 'px');
}
document.addEventListener('click', function(e) {
  var btn = e.target.closest('.info-btn');
  if (btn) {
    e.stopPropagation();
    var wasActive = btn.classList.contains('active');
    _closeAllInfoPopups();
    if (!wasActive) {
      btn.classList.add('active');
      _positionInfoPopup(btn);
    }
    return;
  }
  if (!e.target.closest('.info-popup')) {
    _closeAllInfoPopups();
  }
});
// Close popups on scroll (sidebar or window)
document.querySelectorAll('.sidebar, .panel-body-scroll').forEach(function(el) {
  el.addEventListener('scroll', _closeAllInfoPopups);
});
window.addEventListener('scroll', _closeAllInfoPopups);
