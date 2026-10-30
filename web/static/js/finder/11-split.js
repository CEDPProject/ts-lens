// 11-split.js — 연속 → 세그먼트 분할 모달
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.


/* ═══════════════════════════════════════════════════════════════
   SPLIT CONTINUOUS → SEGMENTS
   ═══════════════════════════════════════════════════════════════ */
var _splitLen = 24;
var _splitBoundaryMin = 0; // >0 = timestamp boundary split, 0 = point count split
var _fixedBoundaryMin = 0; // fixed-length preset boundary (min), 0 = custom input
var _dataIntervalMin = 0; // detected data interval (minutes)

function _detectDataInterval() {
  // detect data interval using median of first consecutive pairs
  if (!S.labels || S.labels.length < 2) return 0;
  var diffs = [];
  var samples = Math.min(S.labels.length - 1, 10); // check up to 10 pairs
  for (var k = 0; k < samples; k++) {
    try {
      var d0 = new Date(S.labels[k]), d1 = new Date(S.labels[k + 1]);
      if (isNaN(d0.getTime()) || isNaN(d1.getTime())) continue;
      var diff = (d1 - d0) / 60000; // minutes
      if (!isNaN(diff) && diff > 0 && diff < 525960) diffs.push(diff); // < 1 year
    } catch(e) {}
  }
  if (diffs.length === 0) return 0;
  // Return median
  diffs.sort(function(a, b) { return a - b; });
  var mid = Math.floor(diffs.length / 2);
  return diffs.length % 2 === 0 ? (diffs[mid - 1] + diffs[mid]) / 2 : diffs[mid];
}

function _minutesToPoints(minutes) {
  // convert minutes to data point count based on detected interval
  if (_dataIntervalMin <= 0) return Math.round(minutes / 60); // fallback: 1 point per hour
  return Math.round(minutes / _dataIntervalMin);
}

function splitModalOpen() {
  var container = $('splitPresetPills');
  if (!container) return;

  // Collection mode only if: (1) dataset type is collection AND (2) currently in segment mode
  var isCollection = (S.datasetType === 'collection' && S.mode === 'segment');
  container.innerHTML = '';

  // Unified options for both Collection and Continuous modes
  var options = [
    {minutes: -1, label: 'Original', unit: 'none'},
    {minutes: 1, label: '1min'},
    {minutes: 60, label: '1hour', unit: 'hour'},
    {minutes: 360, label: '6hour', unit: '6hour'},
    {minutes: 1440, label: '1day', unit: 'day'},
    {minutes: 10080, label: '1week', unit: 'week'},
    {minutes: 14400, label: '10day'},
    {minutes: 43200, label: isCollection ? '1month' : '30day', unit: 'month'},
    {minutes: 525600, label: '1year', unit: 'year'},
    {minutes: 0, label: 'Custom'}
  ];

  // Create pills
  options.forEach(function(opt) {
    // Skip certain options for collection mode
    if (isCollection && (opt.minutes === 1 || opt.minutes === 14400 || opt.minutes === 0)) return;

    var btn = document.createElement('button');
    btn.className = 'method-pill split-preset';
    btn.dataset.minutes = opt.minutes;
    if (opt.unit) btn.dataset.unit = opt.unit;
    btn.textContent = opt.label;

    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      container.querySelectorAll('.split-preset').forEach(function(b) { b.classList.remove('active'); });
      btn.classList.add('active');

      if (!isCollection) {
        var m = parseInt(btn.dataset.minutes);
        if (m === 0) {
          $('splitCustomGroup').hidden = false;
          _splitBoundaryMin = 0;
          _splitLen = parseInt($('inputSplitLen').value) || DEFAULT_SEG_LEN;
          // sync stride to window size
          if ($('inputSplitStride')) {
            $('inputSplitStride').value = _splitLen;
            $('inputSplitStride').max = _splitLen;
            _updateStrideHint();
          }
        } else if (m > 0) {
          $('splitCustomGroup').hidden = true;
          _splitBoundaryMin = m;
          _splitLen = _minutesToPoints(m);
          $('inputSplitLen').value = _splitLen;
        }
        _updateSplitPreview();
      }
    });

    container.appendChild(btn);
  });

  // Collection mode: simple setup
  if (isCollection) {
    $('splitCustomGroup').hidden = true;
    $('splitPreview').textContent = _t('js.split_select_unit');
    $('splitPreview').style.color = '';
    $('btnSplitConfirm').disabled = false;
    container.querySelector('.split-preset[data-minutes="-1"]').classList.add('active');
  }
  // Continuous mode: validation and preview
  else {
    $('splitCustomGroup').hidden = true;
    _dataIntervalMin = _detectDataInterval();
    console.log('[DEBUG Split Modal] Detected interval:', _dataIntervalMin, 'min, S.labels.length:', S.labels ? S.labels.length : 0);
    if (S.labels && S.labels.length >= 5) {
      console.log('[DEBUG Split Modal] First 5 timestamps:', S.labels.slice(0, 5));
    }
    _splitBoundaryMin = 1440; // default: 1day
    _splitLen = _minutesToPoints(1440);
    console.log('[DEBUG Split Modal] 1day = ', _splitLen, 'points');
    if (_splitLen < 3) _splitLen = 24;
    $('inputSplitLen').value = _splitLen;

    var firstValid = null;
    container.querySelectorAll('.split-preset').forEach(function(btn) {
      var m = parseInt(btn.dataset.minutes);
      btn.disabled = false;
      btn.title = '';
      btn.classList.remove('pill-disabled-reason', 'active');
      btn.removeAttribute('data-reason');

      if (m > 0) {
        var pts = _minutesToPoints(m);
        btn.title = '~' + pts + ' points';
        if (_dataIntervalMin > 0 && m < _dataIntervalMin) {
          btn.disabled = true;
          btn.classList.add('pill-disabled-reason');
          btn.setAttribute('data-reason', 'Too small for ' + _dataIntervalMin + 'min interval');
        } else if (pts < 1) {
          btn.disabled = true;
          btn.classList.add('pill-disabled-reason');
          btn.setAttribute('data-reason', 'Too large — only ' + pts + ' pts');
        } else if (!firstValid) {
          firstValid = btn;
        }
      }
    });

    var dayBtn = container.querySelector('.split-preset[data-minutes="1440"]');
    if (dayBtn && !dayBtn.disabled) {
      dayBtn.classList.add('active');
    } else if (firstValid) {
      firstValid.classList.add('active');
      _splitBoundaryMin = parseInt(firstValid.dataset.minutes);
      _splitLen = _minutesToPoints(_splitBoundaryMin);
      $('inputSplitLen').value = _splitLen;
    }
    _updateSplitPreview();
  }

  $('splitModal').hidden = false;
}

function _updateSplitPreview() {
  var n = S.values.length;
  var validCount = 0;
  for (var vi = 0; vi < n; vi++) {
    if (S.values[vi] !== null && S.values[vi] !== undefined && !isNaN(S.values[vi])) validCount++;
  }

  // boundary split mode
  if (_splitBoundaryMin > 0) {
    var estPts = _minutesToPoints(_splitBoundaryMin);
    var estCount = estPts > 0 ? Math.floor(validCount / estPts) : 0;
    if (estPts > validCount || estCount < 1) {
      $('splitPreview').textContent = _t('js.split_too_short');
      $('splitPreview').style.color = '#DC2626';
      if ($('btnSplitConfirm')) $('btnSplitConfirm').disabled = true;
      return;
    }
    $('splitPreview').style.color = '';
    if ($('btnSplitConfirm')) $('btnSplitConfirm').disabled = false;
    var boundaryName = {1:'min', 60:'hour', 360:'6h', 1440:'day', 10080:'week', 14400:'10d', 43200:'30d', 525600:'year'}[_splitBoundaryMin] || (_splitBoundaryMin + 'min');
    var html = '~<span class="num">' + estCount + '</span> segments (timestamp <span class="num-blue">' + boundaryName + '</span> boundary)';
    html += '<br>~<span class="num">' + estPts.toLocaleString() + '</span> pts per segment';
    if (estPts > 500) {
      html += '<br><span style="color:var(--amber);font-size:.8em;">* Downsampled to <span class="num-amber">500</span>pts for search</span>';
    }
    $('splitPreview').innerHTML = html;
    return;
  }

  // point count split mode (custom input)
  var strideVal = parseInt(($('inputSplitStride') || {}).value) || _splitLen;
  if (strideVal < 1) strideVal = 1;
  if (strideVal > _splitLen) strideVal = _splitLen;
  var segCount = strideVal >= _splitLen
    ? Math.floor(validCount / _splitLen)
    : Math.floor((validCount - _splitLen) / strideVal) + 1;

  if (_splitLen > validCount) {
    $('splitPreview').textContent = _t('js.split_exceeds', validCount);
    $('splitPreview').style.color = '#DC2626';
    if ($('btnSplitConfirm')) $('btnSplitConfirm').disabled = true;
    return;
  }
  if (segCount < 2) {
    $('splitPreview').textContent = _t('js.split_min_segments', segCount);
    $('splitPreview').style.color = '#D97706';
    if ($('btnSplitConfirm')) $('btnSplitConfirm').disabled = false;
    return;
  }

  $('splitPreview').style.color = '';
  if ($('btnSplitConfirm')) $('btnSplitConfirm').disabled = false;

  var text = segCount + ' segments (' + _splitLen + 'pts each';
  if (_dataIntervalMin > 0) {
    var totalMin = _dataIntervalMin * (_splitLen - 1);
    if (totalMin < 60) text += ' \u2248 ' + Math.round(totalMin) + 'min';
    else if (totalMin < 1440) text += ' \u2248 ' + (totalMin / 60).toFixed(1) + 'h';
    else text += ' \u2248 ' + (totalMin / 1440).toFixed(1) + 'd';
  }
  text += ')';
  if (strideVal < _splitLen) {
    var overlap = Math.round((1 - strideVal / _splitLen) * 100);
    text += ', stride ' + strideVal + ' (' + overlap + '% overlap)';
  }
  if (_splitLen > 500) {
    text += '\n* Downsampled to 500pts for search comparison';
  }
  $('splitPreview').textContent = text;
}

// preset buttons
document.querySelectorAll('.split-preset').forEach(function(btn) {
  btn.addEventListener('click', function(e) {
    e.stopPropagation();
    document.querySelectorAll('.split-preset').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    var minutes = parseInt(btn.dataset.minutes);
    if (minutes === 0) {
      // "custom" mode: point count based split
      $('splitCustomGroup').hidden = false;
      _splitBoundaryMin = 0;
      _splitLen = parseInt($('inputSplitLen').value) || DEFAULT_SEG_LEN;
    } else {
      // preset: timestamp boundary split
      $('splitCustomGroup').hidden = true;
      _splitBoundaryMin = minutes;
      _splitLen = _minutesToPoints(minutes);
      $('inputSplitLen').value = _splitLen;
    }
    _updateSplitPreview();
  });
});

if ($('inputSplitLen')) {
  $('inputSplitLen').addEventListener('input', function() {
    _splitLen = parseInt(this.value) || DEFAULT_SEG_LEN;
    _splitBoundaryMin = 0; // disable boundary split on custom input
    // sync stride default to match window
    if ($('inputSplitStride')) {
      var curStride = parseInt($('inputSplitStride').value) || 0;
      if (curStride === 0 || curStride > _splitLen) {
        $('inputSplitStride').value = _splitLen;
      }
      $('inputSplitStride').max = _splitLen;
      _updateStrideHint();
    }
    _updateSplitPreview();
  });
}
if ($('inputSplitStride')) {
  $('inputSplitStride').addEventListener('input', function() {
    _updateStrideHint();
    _updateSplitPreview();
  });
}
function _updateStrideHint() {
  var hint = $('strideHint');
  if (!hint) return;
  var w = parseInt(($('inputSplitLen') || {}).value) || DEFAULT_SEG_LEN;
  var s = parseInt(($('inputSplitStride') || {}).value) || w;
  if (s >= w) {
    hint.textContent = _t('js.stride_no_overlap');
  } else {
    var overlap = Math.round((1 - s / w) * 100);
    hint.textContent = overlap + '% overlap';
  }
}

// cancel
$('btnSplitCancel').addEventListener('click', function() {
  $('splitModal').hidden = true;
});

// execute split
$('btnSplitConfirm').addEventListener('click', function() {
  $('splitModal').hidden = true;

  var activeBtn = document.querySelector('.split-preset.active');
  if (!activeBtn) return;

  var minutes = parseInt(activeBtn.dataset.minutes);
  // Collection path only if: (1) dataset type is collection AND (2) currently in segment mode
  // If we're in continuous mode (after concat), always use split path
  var isCollection = (S.datasetType === 'collection' && S.mode === 'segment');

  // Handle "Original" selection
  if (minutes === -1) {
    if (!isCollection) return; // Ignore Original for continuous mode
    // Fall through to collection logic
  }

  if (isCollection) {
    // Collection mode: load segments with selected time unit
    var selectedUnit = activeBtn.dataset.unit || 'none';
    showLoading(selectedUnit === 'none' ? 'Loading original segments...' : 'Loading segments...');

    var segUrl = '/api/segments/' + S.datasetKey + '/' + S.primaryCol;
    var params = [];
    if (S.overlayCols && S.overlayCols.length > 0) {
      params.push('overlay_cols=' + encodeURIComponent(S.overlayCols.join(',')));
    }
    if (selectedUnit !== 'none') {
      params.push('time_unit=' + encodeURIComponent(selectedUnit));
    }
    if (params.length > 0) segUrl += '?' + params.join('&');

    fetch(segUrl)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        hideLoading();
        S.segments = data.segments;
        S.segQueryIndex = -1;
        S.segResults = [];
        S.segSort = 'date';
        S.mode = 'segment';
        S._segBackup = null;
        // Set split mode flag when time_unit filter is applied (needed for inline_segments in clustering)
        S._splitMode = (selectedUnit !== 'none');
        if (selectedUnit !== 'none' && window._setSelectedTimeUnit) window._setSelectedTimeUnit(selectedUnit);
        console.log('[DEBUG Load] Collection loaded - selectedUnit:', selectedUnit, 'S._splitMode:', S._splitMode, 'segments:', data.segments.length);

        var unitLabel = selectedUnit === 'none' ? 'Original' : activeBtn.textContent + ' · ' + _t('js.source_split_complete');
        // S.datasetKey 를 쓴다. S.datasetTitle 은 어디서도 정해지지 않아
        // 제목이 "undefined (1 day)" 로 찍혔다.
        $('segTitleText').textContent = (S.datasetKey || '') + ' (' + unitLabel + ')';

        hide('mainChartPanel');
        show('segmentPanel');
        show('patternPanel');
        _showSegmentModePills();
        setQueryMode('sample');
        $('btnSearch').disabled = true;
        if ($('viewModeBtns')) $('viewModeBtns').hidden = false;

        // Search Settings는 기본으로 닫힌 상태 유지 (사용자가 직접 열기)

        drawSegmentGrid();
        updateActionBar();
      })
      .catch(function(err) {
        hideLoading();
        alert(_t('js.alert_segment_load_failed') + ' ' + err.message);
      });
    return;
  }

  // Continuous mode: existing split logic
  if (!S.sid || (_splitBoundaryMin > 0 ? _splitLen < 1 : _splitLen < 3)) return;

  showLoading('Splitting into segments...');

  // backup existing continuous state
  S._contBackup = {
    sid: S.sid,
    values: S.values,
    labels: S.labels,
    stats: S.stats,
    queryStart: S.queryStart,
    queryEnd: S.queryEnd,
    results: S.results,
    overlays: S.overlays,
    dataInfoHTML: $('dataInfoContent').innerHTML,
  };

  var splitBody = {
    seg_len: _splitLen,
    overlay_values: S.overlays.map(function(ov) { return { name: ov.name, col_name: ov.col_name, values: ov.values }; }),
  };
  if (_splitBoundaryMin > 0) {
    splitBody.boundary_minutes = _splitBoundaryMin;
  } else {
    // Custom fixed-length: include stride
    var strideVal = parseInt(($('inputSplitStride') || {}).value) || _splitLen;
    if (strideVal < 1) strideVal = 1;
    if (strideVal > _splitLen) strideVal = _splitLen;
    splitBody.stride = strideVal;
  }

  console.log('[DEBUG Split] _splitBoundaryMin:', _splitBoundaryMin, '_splitLen:', _splitLen, 'boundary_minutes:', splitBody.boundary_minutes);

  fetch('/api/split_continuous/' + S.sid, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(splitBody),
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    hideLoading();
    if (data.error) {
      S._contBackup = null;
      hideLoading();
      alert(data.error);
      return;
    }

    // switch to segment mode
    S.segments = data.segments;
    S.segQueryIndex = -1;
    S.segResults = [];
    S.segSort = 'date';
    S.mode = 'segment';
    S._splitMode = true;
    var _unitMap = {60:'hour', 360:'6hour', 1440:'day', 10080:'week', 43200:'month', 525600:'year'};
    if (_splitBoundaryMin > 0 && _unitMap[_splitBoundaryMin] && window._setSelectedTimeUnit) {
      window._setSelectedTimeUnit(_unitMap[_splitBoundaryMin]);
    }
    console.log('[DEBUG Split] selectedTimeUnit:', window._getSelectedTimeUnit ? window._getSelectedTimeUnit() : 'N/A');

    hide('mainChartPanel');
    if ($('classifyResults')) $('classifyResults').hidden = true;
    if ($('segSearchResults')) $('segSearchResults').hidden = true;
    show('segmentPanel');
    show('patternPanel');
    show('dataInfoPanel');
    _showSegmentModePills();
    setQueryMode('sample');
    // Search Settings는 기본으로 닫힌 상태 유지 (사용자가 직접 열기)
    $('btnSearch').disabled = true;
    if ($('viewModeBtns')) $('viewModeBtns').hidden = false;

    // generate segment title
    var splitTitle = 'Split Segments';
    if (data.variable_length && data.length_stats) {
      // boundary split: variable length
      var ls = data.length_stats;
      var boundaryName = {1:'min', 60:'hour', 360:'6h', 1440:'day', 10080:'week', 14400:'10d', 43200:'30d', 525600:'year'}[data.boundary_minutes] || '';
      splitTitle += ' (' + boundaryName + ' intervals';
      if (ls.min === ls.max) {
        splitTitle += ', ' + ls.min + 'pts';
      } else {
        splitTitle += ', ' + ls.min + '~' + ls.max + 'pts';
      }
      splitTitle += ')';
    } else {
      // point count split: fixed length
      splitTitle += ' (' + _splitLen + 'pts';
      if (data.segments.length > 0 && data.segments[0].labels && data.segments[0].labels.length >= 2) {
        try {
          var _d0 = new Date(data.segments[0].labels[0]);
          var _d1 = new Date(data.segments[0].labels[1]);
          var _diffMin = (_d1 - _d0) / 60000;
          console.log('[DEBUG Split Result] First segment interval:', _diffMin, 'min, labels[0]:', data.segments[0].labels[0], 'labels[1]:', data.segments[0].labels[1], 'total labels:', data.segments[0].labels.length);
          if (!isNaN(_diffMin) && _diffMin > 0) {
            var totalMin = _diffMin * (_splitLen - 1);
            if (totalMin < 60) splitTitle += ' \u2248 ' + Math.round(totalMin) + 'min';
            else if (totalMin < 1440) splitTitle += ' \u2248 ' + (totalMin / 60).toFixed(1) + 'h';
            else splitTitle += ' \u2248 ' + (totalMin / 1440).toFixed(1) + 'd';
          }
        } catch(e) {}
      }
      splitTitle += ')';
    }
    $('segTitleText').textContent = splitTitle;
    if (data.source_preserved) $('segTitleText').textContent += ' · ' + _t('js.source_split_complete');
    $('chipSegCount').innerHTML = '<span class="num">' + data.count + '</span>';
    $('chipSegQuery').hidden = true;
    if ($('segHint')) $('segHint').style.display = '';
    if ($('segSummaryBar')) $('segSummaryBar').hidden = true;

    // update data info
    var ds = DATASETS.find(function(d) { return d.key === S.datasetKey; });
    // calculate segment time range
    var segTimeDesc = '';
    if (data.segments.length > 0 && data.segments[0].labels && data.segments[0].labels.length >= 2) {
      var seg0 = data.segments[0];
      var tFirst = seg0.labels[0];
      var tLast = seg0.labels[seg0.labels.length - 1];
      segTimeDesc = String(tFirst).replace(/:\d{2}$/, '') + ' ~ ' + String(tLast).replace(/:\d{2}$/, '');
      // estimate sampling interval (diff between first two labels)
      try {
        var d0 = new Date(seg0.labels[0]), d1 = new Date(seg0.labels[1]);
        var diffMs = d1 - d0;
        if (!isNaN(diffMs) && diffMs > 0) {
          var diffMin = diffMs / 60000;
          var intervalStr = diffMin < 60 ? diffMin + 'min' :
                            diffMin < 1440 ? (diffMin / 60) + 'h' :
                            (diffMin / 1440) + 'd';
          segTimeDesc += ' (interval ' + intervalStr + ')';
        }
      } catch(e) {}
    }
    var totalPts = S.values ? S.values.length : 0;
    // segment length display (variable vs fixed)
    var segLenDesc = '';
    if (data.variable_length && data.length_stats) {
      var ls = data.length_stats;
      segLenDesc = ls.min === ls.max ? ls.min.toLocaleString() + ' pts' : ls.min.toLocaleString() + '~' + ls.max.toLocaleString() + ' pts (median ' + ls.median.toLocaleString() + ')';
    } else {
      segLenDesc = _splitLen.toLocaleString() + ' pts';
    }
    $('dataInfoContent').innerHTML =
      '<div class="info-grid">' +
        '<div class="info-row"><span class="info-label">' + _t('js.lbl_dataset') + '</span><span class="info-val">' + (ds ? ds.name : S.datasetKey) + '</span></div>' +
        '<div class="info-row"><span class="info-label">' + _t('js.lbl_mode') + '</span><span class="info-val">' + _t('js.lbl_split_segments') + '</span></div>' +
        '<div class="info-row"><span class="info-label">' + _t('js.lbl_segments') + '</span><span class="info-val mono">' + data.count.toLocaleString() + '</span></div>' +
        '<div class="info-row"><span class="info-label">' + _t('js.lbl_segment_length') + '</span><span class="info-val mono">' + segLenDesc + '</span></div>' +
        (segTimeDesc ? '<div class="info-row"><span class="info-label">' + _t('js.lbl_sample_range') + '</span><span class="info-val mono" style="font-size:.72rem">' + segTimeDesc + '</span></div>' : '') +
        '<div class="info-row"><span class="info-label">' + _t('js.lbl_total') + '</span><span class="info-val mono">' + totalPts.toLocaleString() + ' ' + _t('js.pts') + '</span></div>' +
      '</div>';

    // "Full View" (go back) button
    $('btnSegConcat').hidden = false;
    $('btnSegConcat').textContent = _t('js.full_view');
    S._splitMode = true;

    hide('resultsSection');
    if ($('chartQueryBar')) $('chartQueryBar').hidden = true;

    drawSegmentGrid();
    updateActionBar();
  })
  .catch(function(err) { hideLoading(); alert(_t('js.alert_split_failed') + ' ' + err.message); });
});
