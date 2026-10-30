// 24-ss-detail.js — 세그먼트 검색 — 상세 모달 · 구간 해설
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

function openResultDetailModal(idx) {
  var r = SS.results[idx];
  if (!r) return;
  var modal = $('resultDetailModal');
  if (!modal) return;
  modal.hidden = false;
  var box = modal.querySelector('.result-detail-box');
  if (box) box.classList.remove('fullscreen');

  // Populate header info
  var simPct = (r.similarity * 100).toFixed(1);
  var simColor = r.similarity > 0.7 ? 'var(--green)' : r.similarity > 0.4 ? 'var(--amber)' : 'var(--red)';
  var simBg = r.similarity > 0.7 ? 'var(--green-lt)' : r.similarity > 0.4 ? 'var(--amber-lt)' : 'var(--red-lt)';
  $('rdRank').textContent = '#' + (idx + 1);
  $('rdSim').style.background = simBg;
  $('rdSim').style.color = simColor;
  $('rdSim').textContent = simPct + '%';
  $('rdMeta').innerHTML = _escapeHtml(r.source_key || '') + ' &middot; '
    + _escapeHtml(r.start_ts || '') + ' ~ ' + _escapeHtml(r.end_ts || '')
    + ' &middot; ' + (r.point_count || 0) + 'pts';

  // Legend
  var legendEl = $('rdLegend');
  var qi = SS.queryInfo || {};
  if (qi.shape && qi.shape.length > 0) {
    legendEl.innerHTML =
      '<span><span style="display:inline-block;width:18px;height:2px;border-top:2px dashed #9CA3AF;"></span> Query</span>'
      + '<span><span style="display:inline-block;width:18px;height:3px;background:var(--green);border-radius:1px;"></span> Result</span>';
  } else {
    legendEl.innerHTML = '';
  }

  // Main chart (query overlay + result)
  var chartEl = $('rdChart');

  var traces = [];
  // Query shape (dashed)
  if (qi.shape && qi.shape.length > 0) {
    traces.push({
      y: qi.shape, type: 'scatter', mode: 'lines',
      line: { color: '#9CA3AF', width: 2, dash: 'dash' },
      hoverinfo: 'y', name: 'Query',
    });
  }
  // Result (primary)
  if (r.values && r.values.length > 0) {
    traces.push({
      y: r.values, type: 'scatter', mode: 'lines',
      yaxis: qi.shape ? 'y2' : 'y',
      line: { color: 'rgb(5,150,105)', width: 2.5 },
      fill: 'tozeroy', fillcolor: 'rgba(5,150,105,0.08)',
      hoverinfo: 'y', name: S.primaryCol || 'primary',
    });
  }
  // Overlay columns
  var ovColors = SS.ovColors || [[37,99,235],[217,119,6],[124,58,237],[220,38,38]];
  if (r.overlay_values && r.overlay_values.length > 0) {
    r.overlay_values.forEach(function(ovVals, oi) {
      if (ovVals && ovVals.length > 0) {
        var axN = qi.shape ? (oi + 3) : (oi + 2);
        var rgb = ovColors[oi % ovColors.length];
        traces.push({
          y: ovVals, type: 'scatter', mode: 'lines',
          yaxis: 'y' + axN,
          line: { color: 'rgba(' + rgb.join(',') + ',0.7)', width: 1.8 },
          hoverinfo: 'y',
          name: (S.overlayCols && S.overlayCols[oi]) || ('col' + oi),
        });
      }
    });
  }

  var layout = {
    margin: { t: 12, r: 20, b: 28, l: 40 },
    xaxis: { showgrid: true, gridcolor: 'rgba(0,0,0,.05)', zeroline: false },
    yaxis: { showgrid: true, gridcolor: 'rgba(0,0,0,.05)', zeroline: false },
    paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
    showlegend: true,
    legend: { orientation: 'h', y: -0.15, x: 0.5, xanchor: 'center', font: { size: 11 } },
  };
  if (qi.shape) layout.yaxis2 = { visible: true, overlaying: 'y', showgrid: false, zeroline: false };
  if (r.overlay_values) {
    r.overlay_values.forEach(function(_, oi) {
      var axN = qi.shape ? (oi + 3) : (oi + 2);
      layout['yaxis' + axN] = { visible: false, overlaying: 'y' };
    });
  }

  setTimeout(function() {
    Plotly.newPlot('rdChart', traces, layout, { displayModeBar: true, responsive: true, staticPlot: false });
  }, 50);

  // Per-column detail charts (multi-column)
  var colsEl = $('rdCols');
  var allColNames = SS.allColNames || [];
  var allColColors = SS.allColColors || [];
  if (allColNames.length > 1 && r.col_similarities) {
    var colsHtml = '';
    allColNames.forEach(function(col, ci) {
      var csPct = r.col_similarities ? (r.col_similarities[ci] * 100).toFixed(1) : '?';
      var color = allColColors[ci % allColColors.length];
      colsHtml += '<div class="result-detail-col-wrap">'
        + '<div class="result-detail-col-title">'
        + '<span class="ssr-col-bar-dot" style="background:' + color + ';"></span>'
        + col + ' <span class="result-detail-col-sim">' + csPct + '%</span>'
        + '</div>'
        + '<div class="result-detail-col-chart" id="rdCol' + ci + '"></div>'
        + '</div>';
    });
    colsEl.innerHTML = colsHtml;

    // Render per-column charts
    setTimeout(function() {
      allColNames.forEach(function(col, ci) {
        var cId = 'rdCol' + ci;
        var el = document.getElementById(cId);
        if (!el) return;
        var qShape = ci === 0 ? (qi.shape || null) : (qi.overlay_shapes && qi.overlay_shapes[ci - 1]) || null;
        var rVals = ci === 0 ? r.values : (r.overlay_values && r.overlay_values[ci - 1]) || null;
        var color = allColColors[ci % allColColors.length];
        var cTraces = [];
        if (qShape && qShape.length > 0) {
          cTraces.push({ y: qShape, type: 'scatter', mode: 'lines', line: { color: '#9CA3AF', width: 1.5, dash: 'dash' }, hoverinfo: 'y', name: 'Query' });
        }
        if (rVals && rVals.length > 0) {
          cTraces.push({ y: rVals, type: 'scatter', mode: 'lines', yaxis: qShape ? 'y2' : 'y', line: { color: color, width: 2 }, fill: 'tozeroy', fillcolor: color.replace('rgb', 'rgba').replace(')', ',0.08)'), hoverinfo: 'y', name: 'Result' });
        }
        var cLayout = {
          margin: { t: 4, r: 8, b: 4, l: 8 },
          xaxis: { visible: false }, yaxis: { visible: false },
          yaxis2: { visible: false, overlaying: 'y' },
          paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', showlegend: false,
        };
        Plotly.newPlot(cId, cTraces, cLayout, { displayModeBar: false, responsive: true, staticPlot: false });
      });
    }, 100);
  } else {
    colsEl.innerHTML = '';
  }

  // Store current index for reference
  SS._modalIdx = idx;
}

// Close modal
(function() {
  var modal = $('resultDetailModal');
  if (!modal) return;
  // Close button
  var closeBtn = $('rdClose');
  if (closeBtn) closeBtn.addEventListener('click', function() {
    modal.hidden = true;
    Plotly.purge('rdChart');
  });
  // Click outside
  modal.addEventListener('click', function(e) {
    if (e.target === modal) { modal.hidden = true; Plotly.purge('rdChart'); }
  });
  // ESC key
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && !modal.hidden) {
      var box = modal.querySelector('.result-detail-box');
      if (box && box.classList.contains('fullscreen')) {
        box.classList.remove('fullscreen');
        Plotly.Plots.resize('rdChart');
      } else {
        modal.hidden = true;
        Plotly.purge('rdChart');
      }
    }
  });
  // Reset zoom
  var rzBtn = $('rdResetZoom');
  if (rzBtn) rzBtn.addEventListener('click', function() {
    var el = document.getElementById('rdChart');
    if (el && el.data) {
      Plotly.relayout('rdChart', { 'xaxis.autorange': true, 'yaxis.autorange': true, 'yaxis2.autorange': true });
    }
  });
  // Fullscreen toggle
  var fsBtn = $('rdFullscreen');
  if (fsBtn) fsBtn.addEventListener('click', function() {
    var box = modal.querySelector('.result-detail-box');
    if (box) {
      box.classList.toggle('fullscreen');
      setTimeout(function() {
        Plotly.Plots.resize('rdChart');
        // Resize column charts too
        var colCharts = box.querySelectorAll('.result-detail-col-chart');
        colCharts.forEach(function(el) { if (el.id) Plotly.Plots.resize(el.id); });
      }, 50);
    }
  });
})();

/* ── Render expanded card detail (per-column charts) ── */
function _renderCardDetail(card, r, idx, queryInfo, allColNames, allColColors, ovColors) {
  var detail = document.createElement('div');
  detail.className = 'ssr-detail';

  // Legend for query vs result
  var legendHtml = '<div class="ssr-detail-legend">'
    + '<span><span style="display:inline-block;width:16px;height:2px;border-top:2px dashed #9CA3AF;"></span> Query</span>'
    + '<span><span style="display:inline-block;width:16px;height:2px;background:var(--green);border-radius:1px;"></span> Result</span>'
    + '</div>';

  // Per-column charts
  var chartsHtml = '<div class="ssr-detail-charts">';
  allColNames.forEach(function(col, ci) {
    var simPct = r.col_similarities ? (r.col_similarities[ci] * 100).toFixed(1) : '?';
    var color = allColColors[ci % allColColors.length];
    chartsHtml += '<div class="ssr-detail-chart-wrap">'
      + '<div class="ssr-detail-chart-title">'
      + '<span class="ssr-col-bar-dot" style="background:' + color + ';"></span>'
      + _escapeHtml(col) + ' <span class="ssr-detail-chart-sim">' + simPct + '%</span>'
      + '</div>'
      + '<div class="ssr-detail-chart" id="ssrDetail' + idx + '_' + ci + '"></div>'
      + '</div>';
  });
  chartsHtml += '</div>';

  detail.innerHTML = legendHtml + chartsHtml;
  card.appendChild(detail);

  // Render per-column Plotly charts
  allColNames.forEach(function(col, ci) {
    var chartId = 'ssrDetail' + idx + '_' + ci;
    var el = document.getElementById(chartId);
    if (!el) return;

    // Query shape for this column
    var qShape = ci === 0 ? queryInfo.shape
      : (queryInfo.overlay_shapes && queryInfo.overlay_shapes[ci - 1]) || null;
    // Result values for this column
    var rVals = ci === 0 ? r.values
      : (r.overlay_values && r.overlay_values[ci - 1]) || null;

    var color = allColColors[ci % allColColors.length];
    var traces = [];

    if (qShape && qShape.length > 0) {
      traces.push({
        y: qShape, type: 'scatter', mode: 'lines',
        line: { color: '#9CA3AF', width: 1.5, dash: 'dash' },
        connectgaps: false,
        hoverinfo: 'skip', name: 'Query',
      });
    }
    if (rVals && rVals.length > 0) {
      traces.push({
        y: rVals, type: 'scatter', mode: 'lines',
        yaxis: 'y2',
        line: { color: color, width: 2 },
        fill: 'tozeroy', fillcolor: color.replace('rgb', 'rgba').replace(')', ',0.08)'),
        connectgaps: false,
        hoverinfo: 'skip', name: 'Result',
      });
    }

    var layout = {
      margin: { t: 2, r: 4, b: 2, l: 4 },
      xaxis: { visible: false },
      yaxis: { visible: false },
      yaxis2: { visible: false, overlaying: 'y' },
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'transparent',
      showlegend: false,
    };

    Plotly.newPlot(chartId, traces, layout, { displayModeBar: false, responsive: true, staticPlot: true });
  });
}

/* ── TS-to-Text: Describe Segment ── */
function describeSegment(idx) {
  var r = SS.results[idx];
  if (!r || !r.values) return;

  var btn = document.querySelector('.ssr-describe-btn[data-idx="' + idx + '"]');
  if (btn) { btn.textContent = _t('js.describing'); btn.disabled = true; }

  var body = {
    dataset_key: S.datasetKey || '',
    values: r.values,
    col_name: S.primaryCol,
    unit: SS.selectedTimeUnit,
    model_key: getActiveDescribeModel(),
  };

  fetch('/api/describe_segment', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  .then(function(resp) { return resp.json(); })
  .then(function(data) {
    if (btn) { btn.textContent = _t('js.describe'); btn.disabled = false; btn.classList.remove('btn-loading'); }
    if (data.error) {
      alert(_t('js.alert_desc_error') + ' ' + data.error);
      return;
    }
    showDescriptionPanel(data, r, idx);
  })
  .catch(function(err) {
    if (btn) { btn.textContent = _t('js.describe'); btn.disabled = false; btn.classList.remove('btn-loading'); }
    alert(_t('js.alert_desc_failed') + ' ' + err.message);
  });
}

function showDescriptionPanel(descData, result, idx) {
  var panel = $('tsDescPanel');
  var body = $('tsDescBody');
  if (!panel || !body) return;

  // Current responses may contain bilingual {ko, en} values.  Keep this
  // result-card path aligned with the main description panel.
  var html = '<div class="ts-desc-col">'
    + '<div class="ts-desc-col-name">' + _escapeHtml(S.primaryCol) + '</div>';

  if (descData.shape) {
    html += '<span class="ts-desc-shape">' + _bl(descData.shape) + '</span>';
  }
  var descSummary = descData.summary || descData.description;
  if (descSummary) {
    html += '<div class="ts-desc-text">' + _bl(descSummary) + '</div>';
  }
  var descFeatures = descData.features || descData.characteristics || [];
  if (descFeatures.length > 0) {
    html += '<div class="ts-desc-tags">';
    descFeatures.forEach(function(tag) {
      html += '<span class="ts-desc-tag">' + _blTag(tag) + '</span>';
    });
    html += '</div>';
  }
  html += '</div>';

  // Segment info
  html += '<div style="font-size:.72rem;color:var(--text3);margin-top:8px;">'
    + '#' + (idx + 1) + ' &middot; '
    + _escapeHtml(result.source_key || '') + ' &middot; '
    + _escapeHtml(result.start_ts || '') + ' ~ ' + _escapeHtml(result.end_ts || '')
    + ' &middot; ' + _t('js.similarity') + ': ' + (result.similarity * 100).toFixed(1) + '%'
    + '</div>';

  body.innerHTML = html;
  // 제목이 이전 해석의 로딩 문구("Analyzing query...")로 남지 않게 하고,
  // Full View 와 같이 펼친 상태로 보여 준다(접힌 본문은 max-height 0 이라 안 보인다).
  if ($('tsDescTitle')) $('tsDescTitle').textContent = _t('js.analysis_complete');
  panel.classList.add('desc-has-result', 'desc-expanded');
  placeDescPanel();
  panel.hidden = false;
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

if ($('tsDescClose')) {
  $('tsDescClose').addEventListener('click', function() {
    if ($('tsDescPanel')) $('tsDescPanel').hidden = true;
  });
}

/* ── Show/Hide Segment Search Panel on dataset load ── */
var _origOnDatasetChange = null;

// Watch for dataset column load (hook into existing flow)
function onSegSearchDataReady() {
  if (!S.datasetKey || !S.primaryCol) return;

  // Show unified pattern panel + search settings
  if ($('patternPanel')) $('patternPanel').hidden = false;
  // Render column chips (multi-column)
  renderColChips();

  // Load time info for this dataset
  fetch('/api/segment_search/time_info', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset_key: S.datasetKey }),
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    SS.samplingMinutes = data.sampling_minutes;
    // Expose to DrawQuery for resolution sync
    window._samplingMinutes = SS.samplingMinutes;
    // Sync DrawQuery resolution with current fixed_len
    if (typeof DrawQueryAPI !== 'undefined') {
      var fl = parseInt($('inputFixedLen').value) || PRESET_RESOLUTION;
      DrawQueryAPI.setLength(fl);
    }
    // Auto-select appropriate time unit (only if NOT already in segment mode)
    if (SS.samplingMinutes && S.mode !== 'segment') {
      if (SS.samplingMinutes <= 60) SS.selectedTimeUnit = 'day';
      else if (SS.samplingMinutes <= 360) SS.selectedTimeUnit = 'week';
      else SS.selectedTimeUnit = 'month';
    }
    updateUnifiedSearchBtn();
  })
  .catch(function(e) { console.warn('[cluster] Query load failed:', e); });
}

// Hook into data loading via custom event (fired after loadDatasetColumn completes)
document.addEventListener('dataColumnsLoaded', function() {
  onSegSearchDataReady();
});
// Fallback: also listen for btnLoadCols click with timeout
var origLoadBtn = $('btnLoadCols');
if (origLoadBtn) {
  origLoadBtn.addEventListener('click', function() {
    setTimeout(function() {
      if (S.mode !== 'segment') onSegSearchDataReady();
    }, 2000);
  });
}

/* ── Unified Search Button ── */
if ($('btnUnifiedSearch')) {
  $('btnUnifiedSearch').addEventListener('click', function() {
    console.log('[UnifiedSearch] clicked, _queryMode=' + _queryMode + ', S.mode=' + S.mode + ', S.sid=' + S.sid);
    if (_queryMode === 'select') {
      // Delegate to continuous search (chart selection)
      doContinuousSearch();
    } else if (_queryMode === 'sketch') {
      if (S.mode === 'segment') {
        // Segment mode: use doSegmentSearch which handles sketch query values
        console.log('[UnifiedSearch] sketch mode in segment view - calling doSegmentSearch');
        doSegmentSearch();
      } else {
        // Continuous mode: sliding window search with sketched pattern
        console.log('[UnifiedSearch] sketch mode - calling doContinuousSearch');
        doContinuousSearch();
      }
    } else if (_queryMode === 'sample') {
      // Sample (QBE): delegate to segment search by card selection
      $('btnSearch').click();
    } else if (_queryMode === 'preset' && SS.selectedPreset === '__all__') {
      // All: classify all presets and show results
      console.log('[Search] Preset ALL triggered. _queryMode=' + _queryMode + ', S.mode=' + S.mode);
      var _clsBtn = $('btnUnifiedSearch');
      if (_clsBtn) { _clsBtn.innerHTML = '<span class="btn-spinner"></span> ' + _t('js.classifying'); _clsBtn.disabled = true; _clsBtn.classList.add('btn-loading'); }
      var _isMultiCls = S.overlayCols && S.overlayCols.length > 0;
      doClassifyAll(function(data) {
        if (_clsBtn) { _clsBtn.textContent = _t('js.search'); _clsBtn.disabled = false; _clsBtn.classList.remove('btn-loading'); }
        if (!_isMultiCls) renderClassifyResults(data);
        // multi-column: already rendered inside doClassifyAll
      });
    } else if (_queryMode === 'text' && S.mode === 'continuous') {
      // Text in continuous mode: LLM generates shape → sliding window search
      _doTextSearchContinuous();
    } else {
      // Preset or Text (segment mode) → segment search
      doSegmentSearch();
    }
  });
}

/* ── Text Search in Continuous Mode (NL → LLM shape → sliding window) ── */
