// 23-ss-search.js — 세그먼트 검색 — 실행 · 결과 렌더링
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

function doSegmentSearch() {
  var btn = $('btnUnifiedSearch');
  if (!btn || btn.disabled) return;

  console.log('[doSegmentSearch] called, _queryMode=' + _queryMode + ', SS.selectedPreset=' + SS.selectedPreset);

  // Clear previous classify badges (individual search replaces classify results)
  clearPresetBadges();

  // Read unified filter settings
  var topK = DEFAULT_TOP_K;
  if ($('sliderTopK')) topK = parseInt($('sliderTopK').value) || DEFAULT_TOP_K;

  var targetCol = S.primaryCol;

  var body = {
    dataset_key: S.datasetKey,
    col_name: targetCol,
    unit: SS.selectedTimeUnit,
    method: S.method,
    top_k: topK,
  };

  // Always send inline segments when segments exist to prevent backend re-segmentation
  if (S.segments && S.segments.length > 0) {
    body.inline_segments = S.segments.map(function(seg) {
      return {
        key: seg.key || seg.label || seg.source_key || ('seg_' + (seg.index || 0)),
        source_key: seg.source_key || '',
        source_label: seg.source_label || '',
        label: seg.label || '',
        timestamps: seg.timestamps || seg.labels || [],
        values: seg.values || [],
        overlays: (seg.overlays || []).map(function(ov, oi) {
          return {
            col_name: ov.col_name || ov.name || (S.overlayCols || [])[oi] || '',
            timestamps: ov.timestamps || ov.labels || seg.timestamps || seg.labels || [],
            values: ov.values || []
          };
        })
      };
    });
  }

  // Overlay columns + per-column presets
  if (S.overlayCols && S.overlayCols.length > 0) {
    body.overlay_cols = S.overlayCols;
    // Per-column preset (only when non-empty)
    if (SS.overlayPresets && Object.keys(SS.overlayPresets).length > 0) {
      body.overlay_presets = SS.overlayPresets;
    }
  }

  if (_queryMode === 'preset') {
    body.query_type = 'preset';
    body.preset_name = SS.selectedPreset;
    console.log('[SegSearch] Preset mode, preset_name:', SS.selectedPreset);
  } else if (_queryMode === 'sketch') {
    // Sketch mode: use drawn pattern
    if (typeof DrawQueryAPI !== 'undefined' && DrawQueryAPI.hasValidPattern()) {
      body.query_type = 'custom';
      body.query_values = DrawQueryAPI.getQueryValues();
      console.log('[SegSearch] Sketch mode with valid pattern:', body.query_values.length, 'points');
    } else {
      console.error('[SegSearch] Sketch mode but no valid pattern! DrawQueryAPI:', typeof DrawQueryAPI, 'hasValidPattern:', typeof DrawQueryAPI !== 'undefined' ? DrawQueryAPI.hasValidPattern() : 'N/A');
      alert(_t('js.alert_draw_pattern'));
      btn.textContent = _t('js.search');
      btn.disabled = false;
      return;
    }
  } else {
    body.query_type = 'llm';
    body.nl_description = ($('segSearchInput') || {}).value || '';
    body.model_key = SS.queryModel || '';
  }

  console.log('[SegSearch] Request:', JSON.stringify(body));

  btn.textContent = _t('js.searching');
  btn.disabled = true;

  fetch('/api/segment_search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    btn.textContent = _t('js.search');
    btn.disabled = false;
    updateUnifiedSearchBtn();

    if (data.error) {
      console.error('[SegSearch] Error:', data.error);
      alert(_t('js.alert_search_error') + ' ' + data.error);
      return;
    }

    console.log('[SegSearch] Results:', data.results ? data.results.length : 0);
    SS.results = data.results || [];
    renderSegSearchResults(data);
  })
  .catch(function(err) {
    console.error('[SegSearch] Fetch error:', err);
    btn.textContent = _t('js.search');
    btn.disabled = false;
    updateUnifiedSearchBtn();
    alert(_t('js.alert_search_failed') + ' ' + err.message);
  });
}

/* ── Render Segment Search Results ── */
function renderSegSearchResults(data, opts) {
  opts = opts || {};
  var panel = $('segSearchResults');
  if (!panel) return;
  // Hide classification results when showing search results
  if ($('classifyResults')) $('classifyResults').hidden = true;
  panel.hidden = false;

  // Auto-collapse segment panel to focus on search results
  var segPanel = $('segmentPanel');
  if (segPanel && !segPanel.hidden) {
    segPanel.classList.add('seg-collapsed');
    var tb = $('btnSegToggle');
    if (tb) tb.textContent = _t('js.expand');
  }

  var results = data.results || [];
  var queryInfo = data.query || {};
  // Use the server-confirmed column order. A requested column may be absent
  // from a source and must not shift the names attached to returned arrays.
  var resultOverlayCols = data.overlay_names || S.overlayCols || [];
  var queryDesc = queryInfo.preset_name || queryInfo.type || SS.selectedPreset || '';

  if (queryInfo.shape && queryInfo.shape.length > 0) {
    var ppLabel = queryDesc;
    if (queryInfo.unit_label) ppLabel += ' (' + queryInfo.unit_label + ', ' + queryInfo.length + 'pts)';
    S.generatedPattern = {
      values: queryInfo.shape,
      description: ppLabel,
      length: queryInfo.length || queryInfo.shape.length,
      engine: queryInfo.llm && queryInfo.llm.engine,
      model: queryInfo.llm && queryInfo.llm.model,
      llm_used: queryInfo.llm && queryInfo.llm.llm_used,
      fallback_reason: queryInfo.llm && queryInfo.llm.fallback_reason,
    };
  }

  if ($('ssrCount')) $('ssrCount').textContent = _t('js.query_count', results.length);
  if ($('ssrQuery')) $('ssrQuery').textContent = queryDesc;

  // Summary
  var summary = $('ssrSummary');
  if (summary && results.length > 0) {
    var bestSim = (results[0].similarity * 100).toFixed(1);
    var worstSim = (results[results.length - 1].similarity * 100).toFixed(1);
    var avgSim = ((results.reduce(function(s, r) { return s + r.similarity; }, 0) / results.length) * 100).toFixed(1);
    summary.innerHTML = '<strong>' + _t('js.best') + ':</strong> ' + bestSim + '%'
      + ' &middot; <strong>' + _t('js.average') + ':</strong> ' + avgSim + '%'
      + ' &middot; <strong>' + _t('js.minimum') + ':</strong> ' + worstSim + '%'
      + ' &middot; <strong>' + _t('js.method') + ':</strong> ' + _escapeHtml(S.method)
      + ' &middot; <strong>' + _t('js.unit') + ':</strong> ' + _escapeHtml(SS.selectedTimeUnit);
    if (data.overlay_cols_requested > data.overlay_cols_matched) {
      summary.innerHTML += '<br><span class="ssr-gt-summary">' + _t('js.missing_columns_warning') + '</span>';
    }
    if (data.segments_excluded_missing_overlay > 0) {
      summary.innerHTML += '<br><span class="ssr-gt-summary">'
        + _t('js.missing_segment_columns_warning', data.segments_excluded_missing_overlay) + '</span>';
    }
    if (data.dtw_prefiltered) {
      summary.innerHTML += '<br><span class="ssr-gt-summary">' + _t('js.dtw_prefilter_note') + '</span>';
    }
    // Ground truth precision/recall
    if (data.ground_truth_summary) {
      var gts = data.ground_truth_summary;
      summary.innerHTML += '<br><span class="ssr-gt-summary">'
        + '<strong>Precision:</strong> ' + (gts.precision * 100).toFixed(1) + '% '
        + '(' + gts.hits + '/' + gts.total_results + ')'
        + ' &middot; <strong>Recall:</strong> ' + (gts.recall * 100).toFixed(1) + '% '
        + '(' + gts.detected_labels + '/' + gts.total_labels + ')'
        + '</span>';
    }
  }

  // Grid
  var grid = $('ssrGrid');
  if (!grid) return;
  grid.innerHTML = '';

  // ── Column legend (top of result list) ──
  var ovColors = [[37,99,235], [217,119,6], [124,58,237], [220,38,38]];
  var nCols = 1 + resultOverlayCols.length;
  if (nCols > 1) {
    var legend = document.createElement('div');
    legend.className = 'ssr-col-legend';
    var qPresets = (queryInfo && queryInfo.overlay_presets) || {};
    var primaryPresetLabel = queryInfo.preset_name || '';
    var items = '<span class="ssr-legend-title">' + _t('js.combined_columns') + '</span>';
    items += '<span class="ssr-legend-item"><span class="ssr-legend-line" style="background:#059669;"></span>' + _escapeHtml(S.primaryCol || 'primary')
      + (primaryPresetLabel ? ' <em style="opacity:.6;font-size:.65rem;">(' + _escapeHtml(primaryPresetLabel) + ')</em>' : '') + '</span>';
    resultOverlayCols.forEach(function(col, i) {
      var rgb = ovColors[i % ovColors.length];
      var pLabel = qPresets[col] || primaryPresetLabel;
      items += '<span class="ssr-legend-item"><span class="ssr-legend-line" style="background:rgb(' + rgb.join(',') + ');"></span>' + _escapeHtml(col)
        + (pLabel ? ' <em style="opacity:.6;font-size:.65rem;">(' + _escapeHtml(pLabel) + ')</em>' : '') + '</span>';
    });
    legend.innerHTML = items;
    grid.appendChild(legend);
  }

  // ── Query shape card (first card) ──
  if (queryInfo.shape && queryInfo.shape.length > 0) {
    var qcard = document.createElement('div');
    qcard.className = 'ssr-card ssr-card-query';
    var qLabel = queryInfo.preset_name || queryInfo.type || '';
    if (queryInfo.unit_label) qLabel += ' / ' + queryInfo.unit_label;
    if (queryInfo.llm) qLabel += ' · ' + _patternProvenance(queryInfo.llm);
    // Per-column preset summary for query card
    var qPresetSummary = '';
    if (nCols > 1 && queryInfo.overlay_presets) {
      var parts = [];
      resultOverlayCols.forEach(function(col) {
        var p = queryInfo.overlay_presets[col];
        if (p && p !== queryInfo.preset_name) parts.push(col + ':' + p);
      });
      if (parts.length > 0) qPresetSummary = parts.join(', ');
    }
    qcard.innerHTML =
      '<div class="ssr-card-head">'
      + '<span class="ssr-card-rank" style="background:var(--green);color:#fff;">Q</span>'
      + '<span class="ssr-card-sim" style="background:var(--green-lt);color:var(--green);">' + _t('js.query') + '</span>'
      + '</div>'
      + '<div class="ssr-card-meta">' + _escapeHtml(qLabel) + ' &middot; ' + queryInfo.shape.length + 'pts'
      + (qPresetSummary ? ' &middot; ' + _escapeHtml(qPresetSummary) : '') + '</div>'
      + '<div class="ssr-card-chart" id="ssrChartQuery"></div>';
    grid.appendChild(qcard);
    setTimeout(function() {
      var el = document.getElementById('ssrChartQuery');
      if (!el) return;
      var qTraces = [{
        y: queryInfo.shape, type: 'scatter', mode: 'lines',
        line: { color: '#059669', width: 2 },
        fill: 'tozeroy', fillcolor: 'rgba(5,150,105,0.1)',
        connectgaps: false,
        hoverinfo: 'skip', name: S.primaryCol || 'primary',
      }];
      var qLayout = {
        margin: { t: 2, r: 4, b: 2, l: 4 },
        xaxis: { visible: false },
        yaxis: { visible: false },
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'rgba(236,253,245,0.4)',
        showlegend: false,
      };
      // Add overlay query shapes
      if (queryInfo.overlay_shapes && queryInfo.overlay_shapes.length > 0) {
        queryInfo.overlay_shapes.forEach(function(ovShape, oi) {
          if (ovShape && ovShape.length > 0) {
            var rgb = ovColors[oi % ovColors.length];
            qTraces.push({
              y: ovShape, type: 'scatter', mode: 'lines',
              yaxis: 'y' + (oi + 2),
              line: { color: 'rgba(' + rgb.join(',') + ',0.7)', width: 1.5 },
              connectgaps: false,
              hoverinfo: 'skip',
              name: resultOverlayCols[oi] || ('col' + oi),
            });
            qLayout['yaxis' + (oi + 2)] = { visible: false, overlaying: 'y' };
          }
        });
      }
      Plotly.newPlot('ssrChartQuery', qTraces, qLayout,
        { displayModeBar: false, responsive: true, staticPlot: true });
    }, 0);
  }

  // Color map for all columns (primary + overlays)
  var allColNames = [S.primaryCol || 'primary'].concat(resultOverlayCols);
  var allColColors = ['rgb(5,150,105)'].concat(ovColors.map(function(rgb) { return 'rgb(' + rgb.join(',') + ')'; }));
  // Store for modal reuse
  SS.queryInfo = queryInfo;
  SS.allColNames = allColNames;
  SS.allColColors = allColColors;
  SS.ovColors = ovColors;

  // "Add All Results" button
  if (results.length > 0) {
    var addAllBar = document.createElement('div');
    addAllBar.className = 'ssr-addall-bar';
    var addAllSearchBtn = document.createElement('button');
    addAllSearchBtn.className = 'bk-addall-btn';
    var searchItems = results.map(function(r) {
      return {
        source_key: r.source_key || '', col_name: S.primaryCol || '',
        start_ts: r.start_ts || '', end_ts: r.end_ts || '',
        point_count: r.point_count || (r.values ? r.values.length : 0),
        context: 'search:' + (SS.queryMode || 'sample'),
        values: r.values || [],
      };
    });
    _bkAddAllBtnSetup(addAllSearchBtn, searchItems, { showCount: true });
    addAllBar.appendChild(addAllSearchBtn);
    grid.appendChild(addAllBar);
  }

  results.forEach(function(r, idx) {
    var card = document.createElement('div');
    card.className = 'ssr-card';

    var simPct = (r.similarity * 100).toFixed(1);
    var simColor = r.similarity > 0.7 ? 'var(--green)' : r.similarity > 0.4 ? 'var(--amber)' : 'var(--red)';
    var gtBadge = '';
    if (r.ground_truth) {
      gtBadge = r.ground_truth.hit
        ? '<span class="ssr-gt-badge gt-hit">GT</span>'
        : '<span class="ssr-gt-badge gt-miss">-</span>';
    }

    // Per-column similarity bars (multi-column only)
    var colBarsHtml = '';
    if (nCols > 1 && r.col_similarities && r.col_similarities.length > 0) {
      colBarsHtml = '<div class="ssr-col-bars">';
      r.col_similarities.forEach(function(cs, ci) {
        var pct = (cs * 100).toFixed(0);
        var color = allColColors[ci % allColColors.length];
        colBarsHtml += '<div class="ssr-col-bar-row">'
          + '<span class="ssr-col-bar-dot" style="background:' + color + ';"></span>'
          + '<span class="ssr-col-bar-name">' + (allColNames[ci] || 'col' + ci) + '</span>'
          + '<span class="ssr-col-bar-track"><span class="ssr-col-bar-fill" style="width:' + pct + '%;background:' + color + ';"></span></span>'
          + '<span class="ssr-col-bar-val">' + pct + '%</span>'
          + '</div>';
      });
      colBarsHtml += '</div>';
    }

    var expandBtn = nCols > 1 ? '<button class="ssr-expand-btn" data-idx="' + idx + '">' + _t('js.detail') + '</button>' : '';

    card.innerHTML =
      '<div class="ssr-card-head">'
      + '<span class="ssr-card-rank">#' + (idx + 1) + '</span>'
      + '<span class="ssr-card-sim" style="background:' + (r.similarity > 0.7 ? 'var(--green-lt)' : r.similarity > 0.4 ? 'var(--amber-lt)' : 'var(--red-lt)') + ';color:' + simColor + ';">' + simPct + '%</span>'
      + gtBadge
      + '</div>'
      + '<div class="ssr-card-meta">'
      + (r.source_key || '') + ' &middot; <span class="ssr-meta-date">' + (r.start_ts || '') + ' ~ ' + (r.end_ts || '') + '</span>'
      + ' &middot; ' + (r.point_count || 0) + 'pts'
      + '</div>'
      + '<canvas class="ssr-card-chart" id="ssrChart' + idx + '"></canvas>'
      + colBarsHtml
      + '<div class="ssr-card-actions">'
      + (opts.hideDescribe ? '' : '<button class="ssr-describe-btn" data-idx="' + idx + '">' + _t('js.describe_btn') + '</button>')
      + expandBtn
      + _bkBtnHtml({ source_key: r.source_key, col_name: S.primaryCol, start_ts: r.start_ts, end_ts: r.end_ts, point_count: r.point_count || 0 })
      + '</div>';
    card.dataset.idx = idx;
    if (BasketManager.has({ source_key: r.source_key, col_name: S.primaryCol, start_ts: r.start_ts, end_ts: r.end_ts, point_count: r.point_count || 0 })) {
      card.classList.add('in-basket');
    }

    grid.appendChild(card);

    // Mini chart (Canvas)
    if (r.values && r.values.length > 0) {
      var chartEl = document.getElementById('ssrChart' + idx);
      if (chartEl) {
        var ovColors = ['rgba(37,99,235,0.7)','rgba(217,119,6,0.7)','rgba(124,58,237,0.7)','rgba(220,38,38,0.7)'];
        var overlays = [];
        if (r.overlay_values) {
          r.overlay_values.forEach(function(ov, oi) {
            if (ov && ov.length > 0) overlays.push({ values: ov, color: ovColors[oi % ovColors.length] });
          });
        }
        var gtHit = r.ground_truth && r.ground_truth.hit;
        drawMiniCanvas(chartEl, r.values, {
          color: 'rgba(5,150,105,1)', lineWidth: 1.5,
          fill: !opts.hideDescribe, fillColor: 'rgba(5,150,105,0.06)',
          overlays: overlays,
          bgColor: gtHit ? 'rgba(239,68,68,0.06)' : null,
        });
      }
    }
  });

  // Describe button events
  grid.querySelectorAll('.ssr-describe-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var idx = parseInt(this.dataset.idx);
      describeSegment(idx);
    });
  });

  // Basket [+] button events
  grid.querySelectorAll('.bk-add-btn').forEach(function(btn) {
    var card = btn.closest('.ssr-card');
    var idx = parseInt(card.dataset.idx);
    var r = results[idx];
    if (r) _bkWireBtn(btn, {
      source_key: r.source_key, col_name: S.primaryCol,
      start_ts: r.start_ts, end_ts: r.end_ts,
      point_count: r.point_count || (r.values ? r.values.length : 0),
      context: 'search:' + (_queryMode || 'sample'),
      values: r.values || [],
      similarity: r.similarity,
      distance: r.distance,
      search_method: S.method,
      normalize: true,
      columns: [S.primaryCol].concat(resultOverlayCols),
      series: [{ col_name: S.primaryCol, values: r.values || [], timestamps: r.timestamps || [] }].concat(
        (r.overlay_values || []).map(function(values, oi) {
          return { col_name: resultOverlayCols[oi] || ('column_' + (oi + 2)), values: values || [], timestamps: r.timestamps || [] };
        })
      ),
    });
  });

  // Detail expand/collapse events (multi-column)
  grid.querySelectorAll('.ssr-expand-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var card = this.closest('.ssr-card');
      var btnIdx = parseInt(this.dataset.idx);
      if (card.classList.contains('expanded')) {
        // Collapse
        card.classList.remove('expanded');
        var detail = card.querySelector('.ssr-detail');
        if (detail) detail.remove();
        this.textContent = _t('js.detail');
      } else {
        // Collapse any other expanded card
        grid.querySelectorAll('.ssr-card.expanded').forEach(function(c) {
          c.classList.remove('expanded');
          var d = c.querySelector('.ssr-detail');
          if (d) d.remove();
          var b = c.querySelector('.ssr-expand-btn');
          if (b) b.textContent = _t('js.detail');
        });
        // Expand this card
        card.classList.add('expanded');
        this.textContent = _t('js.collapse');
        _renderCardDetail(card, results[btnIdx], btnIdx, queryInfo, allColNames, allColColors, ovColors);
      }
    });
  });

  // Card click → open detail modal
  grid.querySelectorAll('.ssr-card:not(.ssr-card-query)').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('button')) return; // ignore button clicks
      var idx = parseInt(this.dataset.idx);
      if (!isNaN(idx)) openResultDetailModal(idx);
    });
  });

  // Scroll to results
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ── Result Detail Modal ── */
