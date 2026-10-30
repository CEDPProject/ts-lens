// 06-search.js — 연속 구간 검색 실행 · 결과 그리기
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

function doContinuousSearch() {
  if (S._searchBusy) return;
  console.log('[doContinuousSearch] called, S.mode=' + S.mode + ', S.sid=' + S.sid);
  if (S.mode === 'segment') {
    searchSegments();
    return;
  }
  var hasManualQuery = S.queryStart >= 0 && S.queryEnd >= 0;
  var hasGenPattern = S.generatedPattern && S.generatedPattern.values;
  var hasDrawnPattern = typeof DrawQueryAPI !== 'undefined' && DrawQueryAPI.hasValidPattern();
  console.log('[doContinuousSearch] hasManualQuery=' + hasManualQuery + ', hasGenPattern=' + !!hasGenPattern + ', hasDrawnPattern=' + hasDrawnPattern);
  if (!hasManualQuery && !hasGenPattern && !hasDrawnPattern) return;

  var btn = $('btnSearch');
  var fixedOn = $('chkFixed').checked;
  var isCalendar = S.alignMode === 'calendar';

  // For sketch mode, use sketch window size
  var sketchLen = 0;
  if (_queryMode === 'sketch' && $('inputSketchLen')) {
    sketchLen = parseInt($('inputSketchLen').value) || 0;
  }

  // Sketch 는 자기 창 길이를 쓴다. 예전에는 숨어 있는 Select 패널의 Fixed Length
  // 토글이 켜져 있으면 스케치 길이가 그 값으로 덮여, 화면과 다른 길이로 검색됐다.
  var isSketch = (_queryMode === 'sketch' && sketchLen > 0);
  var searchLen = isSketch ? sketchLen : (fixedOn ? (parseInt($('inputFixedLen').value) || DEFAULT_SEG_LEN) : 0);
  var body = {
    method: S.method, normalize: $('chkNorm') ? $('chkNorm').checked : true,
    stride: parseInt($('inputStride').value) || 1,
    fixed_len: searchLen,
    align: isCalendar && searchLen > 0,   // 정렬은 창 길이가 정해져야 뜻이 있다
  };

  // Priority: drawn pattern > generated pattern > manual selection
  if (hasDrawnPattern) {
    body.query_values = DrawQueryAPI.getQueryValues();
    body.query_start = 0;
    body.query_end = 0;
    body.normalize = true;
    console.log('[Search] Using drawn pattern:', body.query_values.length, 'points');
  } else if (hasGenPattern) {
    body.query_values = S.generatedPattern.values;
    body.query_start = 0;
    body.query_end = 0;
    body.normalize = true;  // generated patterns use z-score
  } else {
    body.query_start = S.queryStart;
    body.query_end = S.queryEnd;
  }

  if (S.filterMode === 'threshold') {
    body.threshold = parseInt($('sliderThreshold').value) / 100;
    body.top_k = UNLIMITED_TOP_K;
  } else {
    body.top_k = $('sliderTopK') ? parseInt($('sliderTopK').value) : DEFAULT_TOP_K;
  }
  console.log('[Search] filterMode=' + S.filterMode + ', top_k=' + body.top_k + ', sliderTopK.value=' + ($('sliderTopK') ? $('sliderTopK').value : 'N/A'));
  body.nan_mode = 'interpolate';
  body.nan_max_ratio = S.nanMaxRatio != null ? S.nanMaxRatio : 0.5;
  body.nan_method = S.nanMethod || 'linear';

  // query length and total data info
  var _qLen = hasDrawnPattern ? body.query_values.length :
              hasGenPattern ? S.generatedPattern.values.length :
              (S.queryEnd - S.queryStart + 1);
  if (body.fixed_len > 0) _qLen = body.fixed_len;
  var _totalPts = S.values ? S.values.length : 0;
  var _winCount = Math.max(0, Math.floor((_totalPts - _qLen) / body.stride) + 1);

  setSearchBusy(true);
  btn.innerHTML = '<span class="btn-spinner"></span> ' + _t('js.searching'); btn.disabled = true; btn.classList.add('btn-loading');
  var searchTips = [
    _t('js.search_progress', _qLen.toLocaleString(), _winCount.toLocaleString(), body.stride),
    _t('js.scanning_points', _totalPts.toLocaleString(), body.method),
    _t('js.normalizing_zscore'),
    _t('js.sorting_candidates'),
    _t('js.removing_overlap'),
  ];
  if (_qLen > 500) searchTips.push(_t('js.downsample_query', _qLen.toLocaleString()));
  showLoading(_t('js.searching_segments'), searchTips);

  fetch('/api/search/' + S.sid, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body),
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    hideLoading();
    if (data.error) { alert(data.error); btn.textContent=_t('js.search_similar'); btn.disabled=false; return; }
    if (data._debug) console.log('[search debug]', data._debug);
    if (!data.results || data.results.length === 0) {
      showNoResults('cont', data._debug);
      btn.textContent = _t('js.search_similar'); btn.disabled = false; btn.classList.remove('btn-loading');
      return;
    }
    S.results = data.results;
    S.queryData = data.query;
    drawMainChart();
    drawResults(data);
    btn.textContent = _t('js.search_similar'); btn.disabled = false; btn.classList.remove('btn-loading');
  })
  .catch(function(err) {
    hideLoading(); alert(_t('js.alert_search_failed') + ' ' + err.message);
    btn.textContent = _t('js.search_similar'); btn.disabled = false; btn.classList.remove('btn-loading');
  }).finally(function() { setSearchBusy(false); });
}

$('btnSearch').addEventListener('click', function() {
  doContinuousSearch();
});

/* ── Results Display ─────────────────────────────────────────────── */
function drawResults(data) {
  // Safety check: ensure query and results exist
  if (!data || !data.query || !data.results) {
    console.error('[drawResults] Invalid data:', data);
    return;
  }

  show('resultsSection');
  var grid = $('resultsGrid');
  grid.innerHTML = '';

  var hasSourceRange = Number.isInteger(data.query.start) && Number.isInteger(data.query.end) && data.query.start >= 0 && data.query.end >= data.query.start;
  var resultNormalize = typeof data.normalized === 'boolean'
    ? data.normalized
    : ((_queryMode === 'sketch' || _queryMode === 'text')
      ? true : ($('chkNorm') ? $('chkNorm').checked : true));
  var qLabel = hasSourceRange ? labelRange(data.query.start, data.query.end) : _t('js.generated_shape');

  // content area summary bar
  var csb = $('contSummaryBar');
  if (csb) {
    if (data.summary && data.summary.count) {
      csb.hidden = false;
      var alignHtml = '';
      if (data.align_info) {
        var modeMap = { daily: 'daily', weekly: 'weekly', monthly: 'monthly' };
        if (data.align_info.applied) {
          alignHtml = '<div class="seg-summary-divider"></div><div class="seg-summary-stat"><div class="seg-stat-val" style="color:var(--green);font-size:.85rem">' +
            (modeMap[data.align_info.mode] || data.align_info.mode) + ' ' + _t('js.boundary') + '</div><div class="seg-stat-label">' + _t('js.search_mode') + '</div></div>';
        } else if (data.align_info.requested) {
          alignHtml = '<div class="seg-summary-divider"></div><div class="seg-summary-stat"><div class="seg-stat-val" style="color:var(--amber);font-size:.85rem">' + _t('js.stride_fallback') + '</div><div class="seg-stat-label">' + _t('js.search_mode') + '</div></div>';
        }
      }
      csb.innerHTML =
        '<div class="summary-stats-row">' +
        '<div class="seg-summary-stat"><div class="seg-stat-val" style="color:var(--green)">' + qLabel + '</div><div class="seg-stat-label">' + _t('js.query') + '</div></div>' +
        '<div class="seg-summary-divider"></div>' +
        '<div class="seg-summary-stat"><div class="seg-stat-val" style="color:var(--blue)">' + data.summary.count + '</div><div class="seg-stat-label">' + _t('js.matches') + '</div></div>' +
        '<div class="seg-summary-divider"></div>' +
        '<div class="seg-summary-stat"><div class="seg-stat-val" style="color:var(--amber)">' + (data.summary.best * 100).toFixed(1) + '%</div><div class="seg-stat-label">' + _t('js.best_similarity') + '</div></div>' +
        '<div class="seg-summary-divider"></div>' +
        '<div class="seg-summary-stat"><div class="seg-stat-val">' + (data.summary.avg * 100).toFixed(1) + '%</div><div class="seg-stat-label">' + _t('js.average') + '</div></div>' +
        '<div class="seg-summary-divider"></div>' +
        '<div class="seg-summary-stat"><div class="seg-stat-val" style="color:var(--muted)">' + (data.summary.worst * 100).toFixed(1) + '%</div><div class="seg-stat-label">' + _t('js.worst') + '</div></div>' +
        (data.columns_used > 1 ? '<div class="seg-summary-divider"></div><div class="seg-summary-stat"><div class="seg-stat-val" style="color:var(--cyan)">' + data.columns_used + '</div><div class="seg-stat-label">' + _t('js.combined_columns') + '</div></div>' : '') +
        alignHtml +
        '</div>' +
        buildSettingsTags() +
        (data.overlay_cols_requested > data.overlay_cols_matched
          ? '<div class="search-result-note">' + _t('js.missing_columns_warning') + '</div>' : '') +
        (data.dtw_prefiltered
          ? '<div class="search-result-note">' + _t('js.dtw_prefilter_note') + '</div>' : '');
    } else {
      csb.hidden = true;
    }
  }
  grid.appendChild(createCard(data.query.values, null, 'Query', qLabel, 'Reference', '#059669', true,
    data.query.overlay_values, data.overlay_names, data.query.nan_indices));

  // "Add All Results" button
  if (data.results.length > 0) {
    var addAllBar = document.createElement('div');
    addAllBar.className = 'ssr-addall-bar';
    var addAllBtn = document.createElement('button');
    addAllBtn.className = 'bk-addall-btn';
    var drawItems = data.results.map(function(r) {
      var item = {
        source_key: r.source_key || S.sourceKey || S.datasetKey || '',
        col_name: S.primaryCol || '',
        start_ts: r.start_ts || S.labels[r.start] || String(r.start),
        end_ts: r.end_ts || S.labels[r.end] || String(r.end),
        point_count: r.values ? r.values.length : (r.end - r.start + 1),
        context: 'search:draw',
        values: r.values || [],
        similarity: r.similarity,
        distance: r.distance,
        search_method: S.method,
        normalize: resultNormalize,
      };
      return _withBkSeries(item, r.values, r.overlay_values, data.overlay_names,
        r.timestamps || S.labels.slice(r.start, r.end + 1));
    });
    _bkAddAllBtnSetup(addAllBtn, drawItems, { showCount: true });
    addAllBar.appendChild(addAllBtn);
    grid.appendChild(addAllBar);
  }

  data.results.forEach(function(r, i) {
    var sim = (r.similarity * 100).toFixed(1) + '%';
    var rLabel = (r.source_label ? _escapeHtml(r.source_label) + ' · ' : '') + labelRange(r.start, r.end);
    var bkInfo = {
      source_key: r.source_key || S.sourceKey || S.datasetKey || '',
      col_name: S.primaryCol || '',
      start_ts: r.start_ts || S.labels[r.start] || String(r.start),
      end_ts: r.end_ts || S.labels[r.end] || String(r.end),
      point_count: r.values ? r.values.length : (r.end - r.start + 1),
      context: 'search:draw',
      values: r.values || [],
      similarity: r.similarity,
      distance: r.distance,
      search_method: S.method,
      normalize: resultNormalize,
    };
    _withBkSeries(bkInfo, r.values, r.overlay_values, data.overlay_names,
      r.timestamps || S.labels.slice(r.start, r.end + 1));
    var card = createCard(r.values, data.query.values, '#'+(i+1), rLabel, sim, '#F59E0B', false,
      r.overlay_values, data.overlay_names, null, bkInfo);
    card.addEventListener('click', function() {
      Plotly.relayout('plotMain', {
        'xaxis.range': [Math.max(0,r.start-30), Math.min(S.labels.length-1,r.end+30)]
      });
      card.style.outline = '2px solid #F59E0B';
      setTimeout(function() { card.style.outline = ''; }, 1500);
    });
    grid.appendChild(card);
  });
}
