// 25-ss-text.js — 세그먼트 검색 — 자연어 질의(연속 뷰)
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

function _doTextSearchContinuous() {
  var input = $('segSearchInput');
  if (!input || !input.value.trim()) return;
  var nlText = input.value.trim();
  var btn = $('btnUnifiedSearch');
  if (btn) { btn.innerHTML = '<span class="btn-spinner"></span> ' + _t('js.generating_shape'); btn.disabled = true; btn.classList.add('btn-loading'); }

  // Engaging progress overlay during the (slow) LLM shape generation
  var _genTips = [
    _t('js.prompt_analyzing'),
    _t('js.model_interpreting'),
    _t('js.drafting_shape'),
    _t('js.smoothing_shape'),
    _t('js.finalizing_shape'),
  ];
  showLoading(_t('js.generating_from_text', nlText), _genTips);

  // Read window size and stride from Text mode settings
  var fixedLen = parseInt(($('textWindowLen') || {}).value) || DEFAULT_SEG_LEN;
  var textStride = parseInt(($('textStride') || {}).value) || Math.max(1, Math.floor(fixedLen / 4));

  // Step 1: Generate shape from NL
  fetch('/api/generate_pattern', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      description: nlText,
      length: fixedLen,
      dataset_key: S.datasetKey || '',
      col_name: S.primaryCol || '',
      model_key: getActiveQueryModel(),
    }),
  })
  .then(function(r) { return r.json(); })
  .then(function(shapeData) {
    if (shapeData.error) {
      hideLoading();
      alert(_t('js.alert_shape_gen_failed') + ' ' + shapeData.error);
      if (btn) { btn.textContent = _t('js.search'); btn.disabled = false; btn.classList.remove('btn-loading'); }
      return;
    }
    console.log('[TextSearch] Shape response:', {
      desc: shapeData.description,
      len: shapeData.values ? shapeData.values.length : 0,
      min: shapeData.values ? Math.min.apply(null, shapeData.values).toFixed(2) : 'N/A',
      max: shapeData.values ? Math.max.apply(null, shapeData.values).toFixed(2) : 'N/A',
      first5: shapeData.values ? shapeData.values.slice(0, 5).map(function(v) { return v.toFixed(2); }) : [],
    });
    var queryValues = shapeData.values;
    if (!queryValues || queryValues.length < 3) {
      hideLoading();
      alert(_t('js.alert_shape_short'));
      if (btn) { btn.textContent = _t('js.search'); btn.disabled = false; btn.classList.remove('btn-loading'); }
      return;
    }

    S.generatedPattern = {
      values: queryValues,
      description: shapeData.description || nlText,
      engine: shapeData.engine || '',
      model: shapeData.model || null,
      llm_used: !!shapeData.llm_used,
      fallback_reason: shapeData.fallback_reason || null,
    };
    var provenance = $('cqbGenDesc');
    if (provenance) {
      provenance.textContent = _patternProvenance(shapeData);
      provenance.hidden = false;
      provenance.title = shapeData.model || shapeData.engine || '';
    }

    // Step 2: Sliding window search with generated shape as external query
    if (btn) btn.innerHTML = '<span class="btn-spinner"></span> ' + _t('js.searching');
    showLoading(_t('js.searching_generated'),
                [_t('js.sliding_compare'), _t('js.sorting_candidates'), _t('js.removing_overlap')]);

    var sid = S.sid;
    var searchBody = {
      q_start: -1, q_end: -1,
      query_values: queryValues,
      fixed_len: fixedLen,
      stride: textStride,
      method: S.method || DEFAULT_METHOD,
      normalize: true,
      top_k: $('sliderTopK') ? (parseInt($('sliderTopK').value) || DEFAULT_TOP_K) : DEFAULT_TOP_K,  // S.topK 는 존재한 적이 없다
      nan_mode: S.nanMode || 'linear',
      nan_max_ratio: S.nanMaxRatio || 0.5,
      align: S.alignMode === 'calendar',   // S.calendarAlign 은 존재한 적이 없어 늘 false 였다
      exclude_query: false,
    };

    fetch('/api/search/' + sid, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(searchBody),
    })
    .then(function(r) { return r.json(); })
    .then(function(results) {
      hideLoading();
      if (btn) { btn.textContent = _t('js.search'); btn.disabled = false; btn.classList.remove('btn-loading'); }
      if (results.error) { alert(_t('js.alert_search_failed') + ' ' + results.error); return; }
      // Render results using existing continuous search result renderer
      drawMainChart();
      drawResults(results);
    })
    .catch(function(err) {
      hideLoading();
      if (btn) { btn.textContent = _t('js.search'); btn.disabled = false; btn.classList.remove('btn-loading'); }
      alert(_t('js.alert_search_failed') + ' ' + err.message);
    });
  })
  .catch(function(err) {
    hideLoading();
    if (btn) { btn.textContent = _t('js.search'); btn.disabled = false; btn.classList.remove('btn-loading'); }
    alert(_t('js.alert_shape_gen_failed') + ' ' + err.message);
  });
}

/* ══════════════════════════════════════════════════════════════════
   Classify All Segments by Preset
   ══════════════════════════════════════════════════════════════════
   ARCHITECTURE (DO NOT BREAK):
   ─────────────────────────────
   Two rendering paths based on overlay column count:

   (A) Single-column (S.overlayCols.length === 0):
       - Calls /api/segment_search/classify once for S.primaryCol
       - Callback invokes renderClassifyResults(data)
       - renderClassifyResults builds cls-group accordion with Plotly charts

   (B) Multi-column (S.overlayCols.length > 0):
       - Calls /api/segment_search/classify in parallel for EACH column
       - Each response rendered via _renderClassifyIntoPanel() into a tab panel
       - _renderClassifyIntoPanel builds the SAME cls-group accordion with Canvas charts
       - NO callback is invoked (rendering is inline)

   CHART RENDERING PATTERN (shared across all renderers):
   ─────────────────────────────────────────────────────────
   1. Build DOM: cls-group > cls-group-body > cls-group-grid > cls-seg-card
   2. Each cls-seg-card stores data-chart-id and data-chart-values
   3. group._renderCharts() is attached to each group element
   4. Charts are lazy-rendered: only when group is opened (click header)
   5. First non-empty group is auto-opened on render
   6. drawMiniCanvas renders into <canvas> elements (no Plotly for mini charts)
   ══════════════════════════════════════════════════════════════════ */
