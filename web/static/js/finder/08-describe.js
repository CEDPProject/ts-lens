// 08-describe.js — 시계열 → 자연어 해설 패널
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

/* ── Selected Region LLM Description ──────────────────────────────── */
var _lastDescResult = null;  // cache last successful description
var _lastDescTimestamps = null;

/* 설명 상자(#tsDescPanel)는 Full View 차트 바로 위에 있다. Segment View 결과 카드도
   같은 상자를 쓰는데, 그때는 Full View(#mainChartPanel)가 숨겨져 있어 설명이 보이지
   않았다. 보여 주기 전에 지금 보이는 쪽(결과 목록 위 / 차트 위)으로 옮긴다. */
function placeDescPanel() {
  var panel = $('tsDescPanel'), full = $('mainChartPanel'), seg = $('segSearchResults'), plot = $('plotMain');
  if (!panel || !full || !plot) return;
  if (full.hidden && seg && !seg.hidden) {
    if (panel.parentNode !== seg) seg.insertBefore(panel, seg.firstChild);
  } else if (panel.nextElementSibling !== plot) {
    plot.parentNode.insertBefore(panel, plot);
  }
}

function _restoreLastDesc(btn) {
  if (btn) { btn.textContent = _t('js.llm_interpret'); btn.disabled = false; btn.classList.remove('btn-loading'); }
  var descPanel = $('tsDescPanel');
  if (_lastDescResult && descPanel) {
    var descTitle = $('tsDescTitle');
    if (descTitle) descTitle.textContent = _t('js.previous_result_cached');
    showQueryDescription(_lastDescResult, _lastDescTimestamps || []);
  } else {
    var descBody = $('tsDescBody');
    if (descBody) descBody.innerHTML = '<div style="padding:12px;color:var(--red,#e11d48);font-size:.78rem;"></div>';
    if (descBody && descBody.firstChild) descBody.firstChild.textContent = _t('js.llm_failed_no_cache');
  }
}

function describeSelectedRegion() {
  var q0 = S.queryStart, q1 = S.queryEnd;
  if (q0 < 0 || q1 < 0) return;

  var btn = $('qabDescribeBtn');
  if (btn) { btn.innerHTML = '<span class="btn-spinner"></span> ' + _t('js.analyzing'); btn.disabled = true; btn.classList.add('btn-loading'); }

  // show panel immediately (loading state)
  placeDescPanel();
  var descPanel = $('tsDescPanel');
  var descTitle = $('tsDescTitle');
  var descBody = $('tsDescBody');
  if (descPanel) descPanel.hidden = false;
  if (descTitle) descTitle.textContent = _t('js.analyzing_query');
  if (descBody) descBody.innerHTML = '<div style="font-size:.76rem;color:var(--text3);padding:8px 0;"></div>';
  if (descBody && descBody.firstChild) descBody.firstChild.textContent = _t('js.waiting_llm');

  // timestamps
  var timestamps = S.labels.slice(q0, q1 + 1);

  // use describe_segment_multi for multi-column, describe_segment otherwise
  var allCols = [S.primaryCol].concat(S.overlayCols || []);
  var hasMulti = allCols.length > 1 && S.overlays && S.overlays.length > 0;

  if (hasMulti) {
    // multi-column description
    var columns = [];
    // primary
    columns.push({
      col_name: S.primaryCol,
      values: S.values.slice(q0, q1 + 1),
    });
    // overlays
    for (var oi = 0; oi < S.overlays.length; oi++) {
      var ov = S.overlays[oi];
      columns.push({
        col_name: ov.col_name || S.overlayCols[oi] || ('col_' + oi),
        values: ov.values.slice(q0, q1 + 1),
      });
    }

    fetch('/api/describe_segment_multi', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        dataset_key: S.datasetKey || '',
        columns: columns,
        timestamps: timestamps,
        unit: '',
        model_key: getActiveDescribeModel(),
      }),
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (btn) { btn.textContent = _t('js.llm_interpret'); btn.disabled = false; btn.classList.remove('btn-loading'); }
      if (data.error) { _restoreLastDesc(btn); return; }
      if (data.columns) {
        data.columns.forEach(function(rc, i) {
          if (!rc.col_name && i < allCols.length) rc.col_name = allCols[i];
        });
      }
      _lastDescResult = data;
      _lastDescTimestamps = timestamps;
      showQueryDescription(data, timestamps);
    })
    .catch(function(err) {
      console.warn('Description failed:', err.message);
      _restoreLastDesc(btn);
    });

  } else {
    // single column description
    fetch('/api/describe_segment', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        dataset_key: S.datasetKey || '',
        col_name: S.primaryCol,
        values: S.values.slice(q0, q1 + 1),
        timestamps: timestamps,
        unit: '',
        model_key: getActiveDescribeModel(),
      }),
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (btn) { btn.textContent = _t('js.llm_interpret'); btn.disabled = false; btn.classList.remove('btn-loading'); }
      if (data.error) { _restoreLastDesc(btn); return; }
      _lastDescResult = data;
      _lastDescTimestamps = timestamps;
      showQueryDescription(data, timestamps);
    })
    .catch(function(err) {
      console.warn('Description failed:', err.message);
      _restoreLastDesc(btn);
    });
  }
}

// bilingual helper: show both languages (en / ko) if available
var _descLang = 'en';
function _escapeHtml(v) {
  return String(v == null ? '' : v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function _findByDataValue(root, selector, key, value) {
  var nodes = root ? root.querySelectorAll(selector) : [];
  for (var i = 0; i < nodes.length; i++) {
    if (nodes[i].dataset[key] === String(value)) return nodes[i];
  }
  return null;
}
function _bl(v) {
  if (!v) return '';
  if (typeof v === 'object' && (v.ko || v.en)) {
    var en = _escapeHtml(v.en || '');
    var ko = _escapeHtml(v.ko || '');
    if (en && ko && en !== ko) return en + ' <span style="color:var(--text3);font-size:.85em;">/ ' + ko + '</span>';
    return en || ko || '';
  }
  return _escapeHtml(v);
}
function _blTag(v) {
  if (!v) return '';
  if (typeof v === 'object' && (v.ko || v.en)) {
    var en = _escapeHtml(v.en || '');
    var ko = _escapeHtml(v.ko || '');
    if (en && ko && en !== ko) return en + ' / ' + ko;
    return en || ko || '';
  }
  return _escapeHtml(v);
}

function showQueryDescription(descData, timestamps) {
  console.log('[Desc] Response:', JSON.stringify(descData).slice(0, 500));
  var panel = $('tsDescPanel');
  var body = $('tsDescBody');
  var title = $('tsDescTitle');
  if (!panel || !body) return;

  // Store data for lang toggle
  panel._descData = descData;
  panel._timestamps = timestamps;

  placeDescPanel();
  panel.hidden = false;
  // start expanded — show results immediately
  panel.classList.add('desc-has-result');
  panel.classList.add('desc-expanded');
  if (title) {
    // build summary badges
    var badges = '';
    if (descData.columns && descData.columns.length > 0) {
      descData.columns.forEach(function(col) {
        badges += '<span class="desc-badge desc-badge-shape" style="margin-left:4px;">' + _bl(col.shape) + '</span>';
      });
    } else if (descData.shape) {
      badges += '<span class="desc-badge desc-badge-shape" style="margin-left:4px;">' + _bl(descData.shape) + '</span>';
    }
    title.innerHTML = _t('js.analysis_complete') + badges + ' <span class="desc-expand-hint">' + _t('js.click_for_details') + '</span>';
  }

  _renderDescBody(descData, body);

  // draw query chart
  setTimeout(function() {
    var chartEl = document.getElementById('descQueryChart');
    if (chartEl && S.queryStart >= 0 && S.queryEnd >= 0) {
      var qVals = S.values.slice(S.queryStart, S.queryEnd + 1);
      var traces = [{y: qVals, mode: 'lines', line: {color: '#059669', width: 1.5}, name: S.primaryCol}];
      // overlay columns
      if (S.overlays && S.overlays.length > 0) {
        var colors = ['#2563EB', '#D97706', '#7C3AED', '#DC2626'];
        S.overlays.forEach(function(ov, i) {
          traces.push({
            y: ov.values.slice(S.queryStart, S.queryEnd + 1),
            mode: 'lines', yaxis: 'y' + (i + 2),
            line: {color: colors[i % colors.length], width: 1.2},
            name: ov.col_name || S.overlayCols[i] || ''
          });
        });
      }
      var layout = {
        height: 60, margin: {t: 2, b: 2, l: 0, r: 0},
        xaxis: {visible: false}, yaxis: {visible: false},
        showlegend: traces.length > 1, legend: {x: 0, y: 1, font: {size: 9}},
        paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
      };
      // Add extra y-axes for overlays
      S.overlays && S.overlays.forEach(function(_, i) {
        layout['yaxis' + (i + 2)] = {visible: false, overlaying: 'y'};
      });
      Plotly.newPlot(chartEl, traces, layout, {displayModeBar: false, staticPlot: true});
    }
  }, 50);
}

function _renderDescBody(descData, body) {
  var html = '';

  // query chart
  html += '<div id="descQueryChart" style="width:100%;height:60px;margin-bottom:8px;"></div>';

  // Multi-column
  if (descData.columns && descData.columns.length > 0) {
    html += '<div class="desc-cols">';
    descData.columns.forEach(function(col) {
      html += '<div class="desc-col-card">';
      html += '<div class="desc-col-head">';
      html += '<span class="desc-col-name">' + _escapeHtml(col.col_label || col.col_name || '') + '</span>';
      if (col.shape) html += '<span class="desc-badge desc-badge-shape">' + _bl(col.shape) + '</span>';
      if (col.trend) html += '<span class="desc-badge desc-badge-trend">' + _bl(col.trend) + '</span>';
      if (col.volatility) html += '<span class="desc-badge desc-badge-vol">' + _bl(col.volatility) + '</span>';
      html += '</div>';
      if (col.summary) html += '<div class="desc-col-summary">' + _bl(col.summary) + '</div>';
      var features = col.features || [];
      if (features.length) {
        html += '<div class="desc-tags">';
        features.forEach(function(t) { html += '<span class="desc-tag">' + _blTag(t) + '</span>'; });
        html += '</div>';
      }
      html += '</div>';
    });
    html += '</div>';
    var corrs = descData.correlations || [];
    if (corrs.length) {
      html += '<div class="desc-corr">';
      corrs.forEach(function(c) { html += '<div class="desc-corr-item">' + _bl(c) + '</div>'; });
      html += '</div>';
    }
    var situation = descData.situation || descData.overall || '';
    if (situation) html += '<div class="desc-situation">' + _bl(situation) + '</div>';
    var tags = descData.tags || [];
    if (tags.length) {
      html += '<div class="desc-tags" style="margin-top:6px;">';
      tags.forEach(function(t) { html += '<span class="desc-tag desc-tag-accent">' + _blTag(t) + '</span>'; });
      html += '</div>';
    }
  } else {
    // Single column
    // pre-analysis results (2 line compact)
    if (descData.analysis && Object.keys(descData.analysis).length > 0) {
      var aItems = [];
      var a = descData.analysis;
      if (a.trend_direction) aItems.push(a.trend_direction);
      if (a.range_relative_volatility) aItems.push('std/range=' + _escapeHtml(a.range_relative_volatility));
      if (a.dominant_period) aItems.push('period=' + a.dominant_period);
      if (a.sudden_changes) aItems.push('sudden=' + a.sudden_changes);
      if (a.zero_ratio) aItems.push('zero=' + a.zero_ratio);
      if (a.segment_means) aItems.push(a.segment_means);
      if (aItems.length > 0) {
        html += '<div class="desc-analysis" style="font-size:.7rem;color:var(--text3);background:var(--surface3);padding:6px 10px;border-radius:6px;margin-bottom:6px;line-height:1.5;font-family:\'DM Mono\',monospace;">';
        html += aItems.join(' · ');
        html += '</div>';
      }
    }
    // LLM interpretation
    if (descData.shape || descData.trend || descData.features) {
      html += '<div class="desc-col-card" style="margin-top:6px;">';
      html += '<div class="desc-col-head">';
      html += '<span class="desc-col-name">' + _escapeHtml(S.primaryCol) + '</span>';
      if (descData.shape) html += '<span class="desc-badge desc-badge-shape">' + _bl(descData.shape) + '</span>';
      if (descData.trend) html += '<span class="desc-badge desc-badge-trend">' + _bl(descData.trend) + '</span>';
      if (descData.volatility) html += '<span class="desc-badge desc-badge-vol">' + _bl(descData.volatility) + '</span>';
      html += '</div>';
      var summary = descData.summary || descData.description || '';
      if (summary) html += '<div class="desc-col-summary">' + _bl(summary) + '</div>';
      var feats = descData.features || descData.characteristics || [];
      if (feats.length) {
        html += '<div class="desc-tags">';
        feats.forEach(function(t) { html += '<span class="desc-tag">' + _blTag(t) + '</span>'; });
        html += '</div>';
      }
      html += '</div>';
    }
    if (!html.replace(/<[^>]*>/g, '').trim()) {
      // Fallback: show raw_response, or analysis summary, or a clear message
      var fallback = descData.raw_response || descData.description || '';
      if (!fallback && descData.analysis) {
        var a = descData.analysis;
        var parts = [];
        if (a.trend_direction) parts.push('Trend: ' + a.trend_direction);
        if (a.range_relative_volatility) parts.push('std/range: ' + a.range_relative_volatility);
        if (a.dominant_period) parts.push('Period: ' + a.dominant_period);
        if (a.segment_means) parts.push(a.segment_means);
        fallback = parts.join(' · ') || '';
      }
      if (fallback) {
        html = '<div class="desc-col-card" style="margin-top:6px;">'
          + '<div class="desc-col-head"><span class="desc-col-name">' + _escapeHtml(S.primaryCol) + '</span>'
          + '<span class="desc-badge" style="background:var(--amber-lt,#fef3c7);color:var(--amber,#d97706);">' + _t('js.llm_incomplete') + '</span></div>'
          + '<div class="desc-col-summary" style="white-space:pre-wrap;font-size:.76rem;">' + _bl(fallback) + '</div></div>';
      } else {
        html = '<div style="padding:12px;color:var(--text3);font-size:.78rem;">' + _t('js.no_analysis') + '</div>';
      }
    }
  }

  body.innerHTML = html;
}

// description panel click -> expand/collapse
document.addEventListener('click', function(e) {
  var header = e.target.closest('.ts-desc-header');
  if (!header) return;
  var panel = header.closest('.ts-desc-panel');
  if (!panel || !panel.classList.contains('desc-has-result')) return;
  if (e.target.closest('.ts-desc-close')) return; // Close button handled separately
  panel.classList.toggle('desc-expanded');
});

/* ── Export ────────────────────────────────────────────────── */
$('btnExport').addEventListener('click', function() {
  if (!S.results.length) return;
  fetch('/api/export/' + S.sid, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ results: S.results }),
  }).then(function(r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.blob();
  }).then(function(blob) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'pattern_results.csv'; a.click();
    })
    .catch(function(err) { alert(_t('js.alert_export_failed') + ' ' + err.message); });
});
