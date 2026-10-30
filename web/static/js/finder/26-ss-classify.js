// 26-ss-classify.js — 세그먼트 검색 — 전체 분류(Classify All)
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

function doClassifyAll(callback) {
  console.log('[ClassifyAll] called. S.mode=' + S.mode + ', S.datasetKey=' + S.datasetKey + ', S.primaryCol=' + S.primaryCol + ', S._splitMode=' + S._splitMode + ', segments=' + (S.segments ? S.segments.length : 0));
  // GUARD: Only run in segment mode
  if (S.mode !== 'segment') { console.warn('[ClassifyAll] BLOCKED: S.mode is "' + S.mode + '", not "segment"'); return; }
  if (!S.datasetKey || !S.primaryCol) { console.warn('[ClassifyAll] BLOCKED: no datasetKey or primaryCol'); return; }
  var allCols = [S.primaryCol].concat(S.overlayCols || []);
  var isMulti = allCols.length > 1;
  console.log('[ClassifyAll] isMulti=' + isMulti + ', allCols=' + JSON.stringify(allCols) + ', overlayCols=' + JSON.stringify(S.overlayCols));

  if (!isMulti) {
    // Single column: original behavior
    var body = {
      dataset_key: S.datasetKey,
      col_name: S.primaryCol,
      unit: SS.selectedTimeUnit,
      method: S.method,
    };

    // Always send inline segments when segments exist to prevent backend re-segmentation
    if (S.segments && S.segments.length > 0) {
      body.inline_segments = S.segments.map(function(seg) {
        return {
          key: seg.key || seg.label || seg.source_key || ('seg_' + (seg.index || 0)),
          source_key: seg.source_key || '',
          timestamps: seg.timestamps || seg.labels || [],
          values: seg.values || []
        };
      });
    }

    console.log('[ClassifyAll] Fetching /api/segment_search/classify with body:', JSON.stringify(body).substring(0, 200));
    fetch('/api/segment_search/classify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      console.log('[ClassifyAll] Response received. error?', data.error, 'groups?', data.groups ? Object.keys(data.groups).length : 0, 'total?', data.segments_total);
      if (data.error) { console.warn('Classify error:', data.error); return; }
      SS.classifyData = data;
      updatePresetBadges();
      if (callback) { console.log('[ClassifyAll] Calling callback (renderClassifyResults)'); callback(data); }
    })
    .catch(function(err) { console.warn('Classify failed:', err.message); });
    return;
  }

  // Multi-column: parallel requests, render each as it arrives
  console.log('[ClassifyAll] MULTI-COLUMN path taken');
  window._mcChartIdx = 0;  // Reset chart counter for fresh IDs
  var panel = $('classifyResults');
  if (!panel) { console.warn('[ClassifyAll] BLOCKED: classifyResults panel not found'); return; }
  if ($('segSearchResults')) $('segSearchResults').hidden = true;
  if ($('resultsSection')) $('resultsSection').hidden = true;
  panel.hidden = false;

  collapseDataForResults();

  var colors = ['#059669','#2563eb','#d97706','#7c3aed','#e11d48','#0891b2','#6d28d9','#ea580c'];

  // Build tab skeleton immediately
  var tabsHtml = '<div class="cls-col-tabs">';
  allCols.forEach(function(col, ci) {
    tabsHtml += '<button class="cls-col-tab' + (ci === 0 ? ' active' : '') + '" data-col="' + _escapeHtml(col) + '" style="--tab-color:' + colors[ci % colors.length] + ';">'
      + _escapeHtml(col) + (col === S.primaryCol ? ' <span style="font-size:.6rem;opacity:.6;">(primary)</span>' : '')
      + '</button>';
  });
  tabsHtml += '</div>';

  var panelsHtml = '';
  allCols.forEach(function(col, ci) {
    panelsHtml += '<div class="cls-col-panel' + (ci === 0 ? ' active' : '') + '" data-col="' + _escapeHtml(col) + '">'
      + '<div style="padding:40px;text-align:center;color:var(--text3);font-size:.8rem;">'
      + '<span class="btn-spinner" style="border-color:rgba(0,0,0,.1);border-top-color:' + colors[ci % colors.length] + ';width:20px;height:20px;display:inline-block;margin-right:8px;"></span>'
      + 'Classifying <strong>' + _escapeHtml(col) + '</strong>...</div></div>';
  });
  panel.innerHTML = tabsHtml + panelsHtml;

  // Wire tab clicks
  panel.querySelectorAll('.cls-col-tab').forEach(function(tab) {
    tab.addEventListener('click', function() {
      panel.querySelectorAll('.cls-col-tab').forEach(function(t) { t.classList.remove('active'); });
      panel.querySelectorAll('.cls-col-panel').forEach(function(p) { p.classList.remove('active'); });
      tab.classList.add('active');
      var target = _findByDataValue(panel, '.cls-col-panel', 'col', tab.dataset.col);
      if (target) target.classList.add('active');
    });
  });
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

  var doneCount = 0;
  var combinedData = null; // store primary result for badge updates

  allCols.forEach(function(col, ci) {
    var colBody = {
      dataset_key: S.datasetKey,
      col_name: col,
      unit: SS.selectedTimeUnit,
      method: S.method,
    };

    // Always send inline segments when segments exist to prevent backend re-segmentation
    if (S.segments && S.segments.length > 0) {
      var _usedOverlay = false;
      colBody.inline_segments = S.segments.map(function(seg) {
        var vals = seg.values || [];
        if (col !== S.primaryCol && seg.overlays) {
          // Try col_name match (collection datasets have col_name in overlays)
          var ov = seg.overlays.find(function(o) { return o.col_name === col; });
          // Fallback: index-based match (split segments only have "name", not "col_name")
          if (!ov) {
            var ovIdx = S.overlayCols ? S.overlayCols.indexOf(col) : -1;
            if (ovIdx >= 0 && seg.overlays[ovIdx]) ov = seg.overlays[ovIdx];
          }
          if (ov) { vals = ov.values || []; _usedOverlay = true; }
        }
        return {
          key: seg.key || seg.label || seg.source_key || ('seg_' + (seg.index || 0)),
          source_key: seg.source_key || '',
          timestamps: seg.timestamps || seg.labels || [],
          values: vals
        };
      });
      console.log('[ClassifyAll] col=' + col + ', usedOverlay=' + _usedOverlay +
        ', first seg values[0..2]=' + JSON.stringify((colBody.inline_segments[0] || {}).values ? colBody.inline_segments[0].values.slice(0, 3) : []));
    }

    console.log('[ClassifyAll] Fetching col=' + col + ', inline=' + !!colBody.inline_segments);
    fetch('/api/segment_search/classify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(colBody),
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      doneCount++;
      var tabPanel = _findByDataValue(panel, '.cls-col-panel', 'col', col);
      if (tabPanel) {
        if (data.error) {
          tabPanel.innerHTML = '<div style="padding:20px;color:var(--red,#e11d48);">Error: ' + _escapeHtml(data.error) + '</div>';
        } else {
          tabPanel.innerHTML = '';
          _renderClassifyIntoPanel(tabPanel, data, col, colors[ci % colors.length]);
        }
      }
      // Store primary for badge updates
      if (col === S.primaryCol) {
        SS.classifyData = data;
        updatePresetBadges();
        combinedData = data;
      }
      if (doneCount >= allCols.length) {
        // Reset button
        var _btn = $('btnUnifiedSearch');
        if (_btn) { _btn.textContent = _t('js.search'); _btn.disabled = false; _btn.classList.remove('btn-loading'); }
      }
    })
    .catch(function(err) {
      doneCount++;
      var tabPanel = _findByDataValue(panel, '.cls-col-panel', 'col', col);
      if (tabPanel) tabPanel.innerHTML = '<div style="padding:20px;color:var(--red,#e11d48);">Error: ' + _escapeHtml(err.message) + '</div>';
    });
  });
}

/* ── Multi-column classify: render one column's results into a tab panel ──
   Uses the SAME cls-group accordion + Plotly chart pattern as renderClassifyResults.
   DO NOT simplify to text-only — charts are required for visual inspection.
   See CHART RENDERING PATTERN comment above doClassifyAll. ── */
function _renderClassifyIntoPanel(container, data, colName, color) {
  var summary = data.group_summary || {};
  var groups = data.groups || {};
  var presetKeys = Object.keys(summary);
  presetKeys.sort(function(a, b) {
    if (a === '_unclassified') return 1;
    if (b === '_unclassified') return -1;
    return (summary[b].count || 0) - (summary[a].count || 0);
  });
  var nonEmpty = presetKeys.filter(function(k) { return summary[k].count > 0 && k !== '_unclassified'; });
  var total = data.segments_total || 1;

  // Summary line
  var summaryDiv = document.createElement('div');
  summaryDiv.style.cssText = 'display:flex;gap:12px;align-items:center;margin-bottom:10px;font-size:.76rem;';
  summaryDiv.innerHTML = '<span><strong>' + (data.segments_total || 0) + '</strong> segments</span>'
    + '<span><strong>' + nonEmpty.length + '</strong> pattern types</span>'
    + '<span style="color:var(--text3);">Unclassified: ' + (data.unclassified || 0) + '</span>';
  container.appendChild(summaryDiv);

  // Distribution bar
  if (nonEmpty.length > 0) {
    var barColors = ['#059669','#2563eb','#7c3aed','#d97706','#e11d48','#0891b2','#6d28d9','#ea580c'];
    var barHtml = '<div class="cls-col-distbar">';
    nonEmpty.slice(0, 10).forEach(function(pk, idx) {
      var info = summary[pk];
      var pct = Math.round((info.count / total) * 100);
      if (pct < 3) pct = 3;
      var segColor = barColors[idx % barColors.length];
      var miniSvg = '';
      // Use SS.presets for actual shape (includes custom presets), fallback to PRESET_SHAPES
      var shape = (SS.presets[pk] && SS.presets[pk].preview)
        ? SS.presets[pk].preview
        : PRESET_SHAPES[pk];
      if (shape) {
        var mn = Math.min.apply(null, shape), mx = Math.max.apply(null, shape), rng = mx - mn || 1;
        var pts = shape.map(function(v, j) {
          return (2 + (j / (shape.length - 1)) * 16).toFixed(1) + ',' + (12 - (v - mn) / rng * 10).toFixed(1);
        }).join(' ');
        miniSvg = '<svg viewBox="0 0 20 14" style="width:18px;height:12px;"><polyline points="' + pts + '" fill="none" stroke="#fff" stroke-width="1.5" stroke-opacity=".8" stroke-linecap="round"/></svg>';
      }
      barHtml += '<div class="cls-col-distbar-seg" style="width:' + pct + '%;background:' + segColor + ';" title="'
        + _escapeHtml(info.label || pk) + ': ' + info.count + ' (' + Math.round((info.count / total) * 100) + '%)">'
        + miniSvg + (pct >= 10 ? '<span>' + _escapeHtml(info.label || pk) + '</span>' : '') + '</div>';
    });
    barHtml += '</div>';
    var barDiv = document.createElement('div');
    barDiv.innerHTML = barHtml;
    container.appendChild(barDiv.firstChild);
  }

  // Group accordion with charts (same pattern as renderClassifyResults)
  var chartIdx = window._mcChartIdx || 0;  // global counter to avoid ID collisions across columns

  presetKeys.forEach(function(pk) {
    var info = summary[pk];
    var segs = groups[pk] || [];
    if (segs.length === 0) return;
    var isUncls = pk === '_unclassified';
    var lineColor = isUncls ? '#9ca3af' : color;

    var group = document.createElement('div');
    group.className = 'cls-group' + (isUncls ? ' unclassified-group' : '');

    // Header with preset shape SVG
    var svgHtml = '';
    // Use SS.presets for actual shape (includes custom presets), fallback to PRESET_SHAPES
    var presetShape = (SS.presets[pk] && SS.presets[pk].preview)
      ? SS.presets[pk].preview
      : PRESET_SHAPES[pk];
    if (!isUncls && presetShape) {
      var shape = presetShape;
      var mn = Math.min.apply(null, shape), mx = Math.max.apply(null, shape);
      var pts = shape.map(function(v, i) {
        var x = 2 + (i / (shape.length - 1)) * 32;
        var y = 16 - 2 - ((v - mn) / (mx - mn + 0.001)) * 12;
        return x.toFixed(1) + ',' + y.toFixed(1);
      }).join(' ');
      svgHtml = '<svg class="cls-group-preset-icon" viewBox="0 0 36 18"><polyline points="' + pts + '" fill="none" stroke="' + lineColor + '" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    }

    var header = document.createElement('div');
    header.className = 'cls-group-header';
    var simText = info.avg_sim > 0 ? '<span class="cls-group-sim">' + Math.round(info.avg_sim * 100) + '% avg</span>' : '';
    header.innerHTML = svgHtml
      + '<span class="cls-group-name">' + _escapeHtml(info.label || pk) + '</span>'
      + '<span class="cls-group-count' + (isUncls ? ' unclassified' : '') + '">' + info.count + '</span>'
      + simText
      + '<button class="bk-addall-btn" title="' + _t('js.add_all_tip') + '">' + _t('js.add_all') + '</button>'
      + '<span class="cls-group-arrow">&#9656;</span>';
    group.appendChild(header);

    // Wire "Add All" toggle button
    (function(_segs, _pk, _colName) {
      var addAllBtn = header.querySelector('.bk-addall-btn');
      if (addAllBtn && _segs.length > 0) {
        var groupItems = _segs.map(function(seg) {
          return _makeBkInfo(seg, _colName, 'classify:' + _pk);
        });
        _bkAddAllBtnSetup(addAllBtn, groupItems);
      }
    })(segs, pk, colName);

    // Body with chart cards
    var body = document.createElement('div');
    body.className = 'cls-group-body';
    var grid = document.createElement('div');
    grid.className = 'cls-group-grid';

    segs.forEach(function(seg) {
      var cid = 'mcClsChart' + chartIdx;
      var card = document.createElement('div');
      card.className = 'cls-seg-card';
      var simPct = (seg.similarity * 100).toFixed(1);
      var simColor = seg.similarity > 0.7 ? 'var(--green)' : seg.similarity > 0.4 ? 'var(--amber)' : 'var(--red)';
      var _clsBkInfo = _makeBkInfo(seg, colName, 'classify:' + pk);
      var segLabel = seg.source_key || seg.key || seg.label || '';
      var segTimeRange = (seg.start_ts && seg.end_ts) ? (seg.start_ts + ' ~ ' + seg.end_ts) : (seg.start_ts || '');
      card.innerHTML = '<div class="cls-seg-meta">' + _escapeHtml(segLabel) + '</div>'
        + (segTimeRange ? '<div class="cls-seg-meta cls-seg-time">' + _escapeHtml(segTimeRange) + '</div>' : '')
        + '<canvas class="cls-seg-chart" id="' + cid + '"></canvas>'
        + '<div class="cls-seg-sim" style="color:' + simColor + ';">' + simPct + '%</div>'
        + _bkBtnHtml(_clsBkInfo);
      if (BasketManager.has(_clsBkInfo)) card.classList.add('in-basket');
      card.dataset.chartId = cid;
      card.dataset.chartValues = JSON.stringify(seg.values || []);
      var _clsBkBtn = card.querySelector('.bk-add-btn');
      if (_clsBkBtn) _bkWireBtn(_clsBkBtn, _clsBkInfo);
      grid.appendChild(card);
      chartIdx++;
    });

    body.appendChild(grid);
    group.appendChild(body);

    // Lazy chart rendering
    group._chartsRendered = false;
    group._renderCharts = function() {
      if (group._chartsRendered) return;
      group._chartsRendered = true;
      var cards = grid.querySelectorAll('.cls-seg-card[data-chart-values]');
      cards.forEach(function(c) {
        try {
          var vals = JSON.parse(c.dataset.chartValues);
          var el = document.getElementById(c.dataset.chartId);
          if (el && vals && vals.length > 0) {
            drawMiniCanvas(el, vals, { color: lineColor, lineWidth: 1.2, fill: true, fillColor: lineColor + '25' });
          }
        } catch (e) {}
      });
    };

    header.addEventListener('click', function() {
      group.classList.toggle('open');
      if (group.classList.contains('open')) group._renderCharts();
    });

    container.appendChild(group);
  });

  window._mcChartIdx = chartIdx;

  // Auto-open first group
  var first = container.querySelector('.cls-group:not(.unclassified-group)');
  if (first) {
    first.classList.add('open');
    first._renderCharts();
  }
}

// ── Save medoid as custom preset dialog ──
function _showSaveAsCustomDialog(basePreset, values) {
  var defaultName = basePreset.replace(/^custom_/, '') + '_sample';
  var name = prompt('Save as custom preset.\nEnter name:', defaultName);
  if (!name || !name.trim()) return;

  var body = {
    dataset_id: S.datasetKey,
    name: name.trim(),
    values: values,
    source: 'medoid',
    metadata: {
      from_preset: basePreset,
      column: S.primaryCol || ''
    }
  };

  fetch('/api/presets/custom', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
  .then(function(r) { return r.json(); })
  .then(function(res) {
    if (res.ok || res.key) {
      alert(_t('js.alert_saved') + ' ' + name);
      loadPresets();
    } else {
      alert(_t('js.alert_error') + ' ' + (res.error || 'Unknown'));
    }
  })
  .catch(function(err) {
    alert(_t('js.alert_network_error') + ' ' + err.message);
  });
}

/* ── Single-column classify renderer ──
   Builds cls-group accordion with Plotly charts into #clsGroupContainer.
   See CHART RENDERING PATTERN comment above doClassifyAll. ── */
function renderClassifyResults(data) {
  console.log('[RenderClassify] called. groups:', data.groups ? Object.keys(data.groups).length : 0);
  var panel = $('classifyResults');
  if (!panel) { console.warn('[RenderClassify] BLOCKED: panel not found'); return; }

  // CRITICAL: Restore panel structure (cluster mode destroys it)
  // Check if essential elements exist, if not, rebuild the structure
  if (!panel.querySelector('.ssr-header') || !$('clsGroupContainer')) {
    console.log('[RenderClassify] Rebuilding panel structure (destroyed by cluster)');
    panel.innerHTML =
      '<div class="ssr-header">' +
        '<h3 class="ssr-title">' + _t('finder.preset_classification') + '</h3>' +
        '<span class="chip chip-blue" id="clsTotal"></span>' +
        '<span class="chip chip-green" id="clsGroups"></span>' +
      '</div>' +
      '<div class="ssr-summary" id="clsSummary"></div>' +
      '<div id="clsGroupContainer"></div>';
  } else {
    // Reset panel title to Preset Classification (cluster mode changes it)
    var titleEl = panel.querySelector('.ssr-title');
    if (titleEl) titleEl.textContent = _t('js.preset_classification');
  }

  // Hide other result panels and collapse data panels
  if ($('segSearchResults')) $('segSearchResults').hidden = true;
  if ($('resultsSection')) $('resultsSection').hidden = true;
  collapseDataForResults();
  panel.hidden = false;

  // Multi-column: render column tabs if column_results exists
  var colResults = data.column_results;
  if (colResults && Object.keys(colResults).length > 1) {
    _renderMultiColClassify(panel, data, colResults);
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    return;
  }

  var groups = data.groups || {};
  var summary = data.group_summary || {};
  var presetKeys = Object.keys(summary);

  // Sort by count descending, _unclassified always last
  presetKeys.sort(function(a, b) {
    if (a === '_unclassified') return 1;
    if (b === '_unclassified') return -1;
    return (summary[b].count || 0) - (summary[a].count || 0);
  });

  var nonEmpty = presetKeys.filter(function(k) { return summary[k].count > 0 && k !== '_unclassified'; });

  if ($('clsTotal')) $('clsTotal').textContent = data.segments_total + ' segments';
  if ($('clsGroups')) $('clsGroups').textContent = nonEmpty.length + ' / ' + (presetKeys.length - (summary['_unclassified'] ? 1 : 0)) + ' groups';

  var summaryEl = $('clsSummary');
  if (summaryEl) {
    var minSimPct = data.min_similarity ? (data.min_similarity * 100).toFixed(0) + '%' : '40%';
    summaryEl.innerHTML = '<strong>Unit:</strong> ' + _escapeHtml(data.unit_label || data.unit)
      + ' &middot; <strong>Method:</strong> ' + _escapeHtml(data.method)
      + ' &middot; <strong>Unclassified:</strong> ' + (data.unclassified || 0)
      + ' (< ' + minSimPct + ')';
    if (data.missing_columns && data.missing_columns.length > 0) {
      summaryEl.innerHTML += '<br><span class="ssr-gt-summary">'
        + _t('js.missing_columns_warning') + ' ('
        + data.missing_columns.map(_escapeHtml).join(', ') + ')</span>';
    }

    // Distribution bar with sparklines
    if (nonEmpty.length > 0) {
      var total = data.segments_total || 1;
      var barColors = ['#059669','#2563eb','#7c3aed','#d97706','#e11d48','#0891b2','#6d28d9','#ea580c'];
      var barHtml = '<div class="cls-col-distbar" style="margin-top:10px;">';
      nonEmpty.slice(0, 10).forEach(function(pk, idx) {
        var info = summary[pk];
        var pct = Math.round((info.count / total) * 100);
        if (pct < 3) pct = 3;
        var segColor = barColors[idx % barColors.length];
        var miniSvg = '';
        // Use SS.presets for actual shape (includes custom presets), fallback to PRESET_SHAPES
        var shape = (SS.presets[pk] && SS.presets[pk].preview)
          ? SS.presets[pk].preview
          : PRESET_SHAPES[pk];
        if (shape) {
          var mn = Math.min.apply(null, shape), mx = Math.max.apply(null, shape), rng = mx - mn || 1;
          var pts = shape.map(function(v, j) {
            return (2 + (j / (shape.length - 1)) * 16).toFixed(1) + ',' + (12 - (v - mn) / rng * 10).toFixed(1);
          }).join(' ');
          miniSvg = '<svg viewBox="0 0 20 14" style="width:18px;height:12px;"><polyline points="' + pts + '" fill="none" stroke="#fff" stroke-width="1.5" stroke-opacity=".8" stroke-linecap="round"/></svg>';
        }
        barHtml += '<div class="cls-col-distbar-seg" style="width:' + pct + '%;background:' + segColor + ';" title="'
          + _escapeHtml(info.label || pk) + ': ' + info.count + ' (' + Math.round((info.count / total) * 100) + '%)">'
          + miniSvg + (pct >= 10 ? '<span>' + _escapeHtml(info.label || pk) + '</span>' : '') + '</div>';
      });
      barHtml += '</div>';
      summaryEl.innerHTML += barHtml;
    }
  }

  var container = $('clsGroupContainer');
  if (!container) return;
  container.innerHTML = '';

  var chartIdx = 0;

  presetKeys.forEach(function(presetName) {
    var info = summary[presetName];
    var segs = groups[presetName] || [];
    if (segs.length === 0) return;  // skip empty groups

    var isUnclassified = (presetName === '_unclassified');
    var group = document.createElement('div');
    group.className = 'cls-group' + (isUnclassified ? ' unclassified-group' : '');

    // SVG sparkline for preset shape (or "?" icon for unclassified)
    var svg;
    var svgW = 36, svgH = 18, pad = 2;
    if (isUnclassified) {
      svg = '<svg class="cls-group-preset-icon" viewBox="0 0 36 18">'
        + '<text x="18" y="14" text-anchor="middle" font-size="13" font-weight="700" fill="#9ca3af">?</text>'
        + '</svg>';
    } else {
      // Use SS.presets for actual shape (includes custom presets), fallback to PRESET_SHAPES
      var shape = (SS.presets[presetName] && SS.presets[presetName].preview)
        ? SS.presets[presetName].preview
        : (PRESET_SHAPES[presetName] || [0,0.5,1,0.5,0]);
      var mn = Math.min.apply(null, shape), mx = Math.max.apply(null, shape), rng = mx - mn || 0.001;
      var pts = shape.map(function(v, i) {
        var x = pad + (i / (shape.length - 1)) * (svgW - 2 * pad);
        var y = svgH - pad - ((v - mn) / rng) * (svgH - 2 * pad);
        return x + ',' + y;
      }).join(' ');
      svg = '<svg class="cls-group-preset-icon" viewBox="0 0 ' + svgW + ' ' + svgH + '">'
        + '<polyline points="' + pts + '" fill="none" stroke="#059669" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        + '</svg>';
    }

    // Medoid SVG (actual sample shape) - purple, clickable to save as custom
    var medoidSvg = '';
    var medoidData = data.group_medoids && data.group_medoids[presetName];
    if (!isUnclassified && medoidData && medoidData.values && medoidData.values.length > 0) {
      var mShape = medoidData.values;
      var mMn = Math.min.apply(null, mShape), mMx = Math.max.apply(null, mShape);
      var mRng = mMx - mMn || 0.001;
      var mPts = mShape.map(function(v, i) {
        var x = pad + (i / (mShape.length - 1)) * (svgW - 2 * pad);
        var y = svgH - pad - ((v - mMn) / mRng) * (svgH - 2 * pad);
        return x + ',' + y;
      }).join(' ');
      medoidSvg = '<svg class="cls-group-medoid-icon" viewBox="0 0 ' + svgW + ' ' + svgH + '" '
        + 'data-preset="' + presetName + '" '
        + 'data-medoid-values="' + encodeURIComponent(JSON.stringify(mShape)) + '" '
        + 'title="Click to save as custom preset">'
        + '<polyline points="' + mPts + '" fill="none" stroke="#6366f1" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        + '</svg>';
    }

    var header = document.createElement('div');
    header.className = 'cls-group-header';
    var simText = '';
    if (info.avg_sim > 0) {
      var bestPct = (info.best_sim * 100).toFixed(0);
      var avgPct = (info.avg_sim * 100).toFixed(0);
      var worstSim = segs.length > 0 ? segs[segs.length - 1].similarity : info.avg_sim;
      var worstPct = (worstSim * 100).toFixed(0);
      simText = '<span class="cls-group-sim">' + worstPct + '~' + bestPct + '%, avg ' + avgPct + '%</span>';
    }
    var countCls = isUnclassified ? ' unclassified' : '';
    var addAllClsBtn = (segs.length > 0)
      ? '<button class="bk-addall-btn" title="' + _t('js.add_all_tip') + '">' + _t('js.add_all') + '</button>'
      : '';
    header.innerHTML = '<div class="cls-shape-pair">' + svg + medoidSvg + '</div>'
      + '<span class="cls-group-name">' + (info.label || presetName) + '</span>'
      + '<span class="cls-group-count' + countCls + '">' + info.count + '</span>'
      + simText
      + addAllClsBtn
      + '<span class="cls-group-arrow">&#9656;</span>';

    // Wire "Add All" toggle button
    (function(_segs, _pk) {
      var addAllBtn = header.querySelector('.bk-addall-btn');
      if (addAllBtn && _segs.length > 0) {
        var groupItems = _segs.map(function(seg) {
          return _makeBkInfo(seg, S.primaryCol, 'classify:' + _pk);
        });
        _bkAddAllBtnSetup(addAllBtn, groupItems);
      }
    })(segs, presetName);

    // Wire medoid icon click to save as custom preset
    (function(_pk) {
      var medoidIcon = header.querySelector('.cls-group-medoid-icon');
      if (medoidIcon) {
        medoidIcon.addEventListener('click', function(e) {
          e.stopPropagation();
          var vals = JSON.parse(decodeURIComponent(this.dataset.medoidValues));
          _showSaveAsCustomDialog(_pk, vals);
        });
      }
    })(presetName);

    group.appendChild(header);

    var lineColor = isUnclassified ? '#9ca3af' : '#059669';

    var body = document.createElement('div');
    body.className = 'cls-group-body';

    var grid = document.createElement('div');
    grid.className = 'cls-group-grid';

    segs.forEach(function(seg) {
      var card = document.createElement('div');
      card.className = 'cls-seg-card';
      var cid = 'clsChart' + chartIdx;
      var simPct = (seg.similarity * 100).toFixed(1);
      var simColor = seg.similarity > 0.7 ? 'var(--green)' : seg.similarity > 0.4 ? 'var(--amber)' : 'var(--red)';
      var _clsBkInfo = _makeBkInfo(seg, S.primaryCol, 'classify:' + presetName);
      var segLabel = seg.source_key || seg.key || seg.label || '';
      var segTimeRange = (seg.start_ts && seg.end_ts) ? (seg.start_ts + ' ~ ' + seg.end_ts) : (seg.start_ts || '');
      card.innerHTML =
        '<div class="cls-seg-meta">' + segLabel + '</div>'
        + (segTimeRange ? '<div class="cls-seg-meta cls-seg-time">' + segTimeRange + '</div>' : '')
        + '<canvas class="cls-seg-chart" id="' + cid + '"></canvas>'
        + '<div class="cls-seg-sim" style="color:' + simColor + ';">' + simPct + '%</div>'
        + _bkBtnHtml(_clsBkInfo);
      if (BasketManager.has(_clsBkInfo)) card.classList.add('in-basket');
      card.dataset.chartId = cid;
      card.dataset.chartValues = JSON.stringify(seg.values || []);
      var _clsBkBtn = card.querySelector('.bk-add-btn');
      if (_clsBkBtn) _bkWireBtn(_clsBkBtn, _clsBkInfo);
      grid.appendChild(card);
      chartIdx++;
    });

    body.appendChild(grid);
    group.appendChild(body);

    group._chartsRendered = false;
    group._renderCharts = function(forceRender) {
      if (group._chartsRendered && !forceRender) return;
      group._chartsRendered = true;
      var cards = grid.querySelectorAll('.cls-seg-card[data-chart-values]');
      cards.forEach(function(c) {
        try {
          var vals = JSON.parse(c.dataset.chartValues);
          var el = document.getElementById(c.dataset.chartId);
          if (el && vals && vals.length > 0) {
            drawMiniCanvas(el, vals, { color: lineColor, lineWidth: 1.2, fill: true, fillColor: lineColor + '25' });
          }
        } catch (e) {}
      });
    };

    header.addEventListener('click', function() {
      group.classList.toggle('open');
      if (group.classList.contains('open')) group._renderCharts();
    });

    container.appendChild(group);
  });

  // Auto-open first non-empty group and render its charts (force render to ensure fresh display)
  var firstNonEmpty = container.querySelector('.cls-group:not(.empty)');
  console.log('[RenderClassify] Auto-open: firstNonEmpty=', !!firstNonEmpty, 'totalGroups=', container.querySelectorAll('.cls-group').length);
  if (firstNonEmpty) {
    firstNonEmpty.classList.add('open');
    if (firstNonEmpty._renderCharts) {
      // Force render even if flag says already rendered (fixes navigation bug)
      firstNonEmpty._chartsRendered = false;
      console.log('[RenderClassify] Calling _renderCharts(true) for first group');
      firstNonEmpty._renderCharts(true);
    }
  }

  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── Multi-column classify renderer ──
function _renderMultiColClassify(panel, data, colResults) {
  var colNames = Object.keys(colResults);
  var primaryCol = data.primary_col || colNames[0];
  var colors = ['#059669', '#2563eb', '#d97706', '#7c3aed', '#e11d48', '#0891b2'];

  // Summary header
  var headerHtml = '<div style="margin-bottom:12px;">';
  if ($('clsTotal')) $('clsTotal').textContent = data.segments_total + ' segments';
  headerHtml += '<div style="font-size:.78rem;color:var(--text2);margin-bottom:8px;">'
    + '<strong>' + colNames.length + '</strong> columns classified independently &middot; '
    + '<strong>Unit:</strong> ' + _escapeHtml(data.unit_label || data.unit)
    + ' &middot; <strong>Method:</strong> ' + _escapeHtml(data.method) + '</div>';
  if (data.missing_columns && data.missing_columns.length > 0) {
    headerHtml += '<div class="ssr-gt-summary" style="margin-bottom:8px;">'
      + _t('js.missing_columns_warning') + ' ('
      + data.missing_columns.map(_escapeHtml).join(', ') + ')</div>';
  }

  // Column tabs
  headerHtml += '<div class="cls-col-tabs">';
  colNames.forEach(function(col, ci) {
    var isPrimary = col === primaryCol;
    var color = colors[ci % colors.length];
    headerHtml += '<button class="cls-col-tab' + (ci === 0 ? ' active' : '') + '" data-col="' + _escapeHtml(col) + '" style="--tab-color:' + color + ';">'
      + _escapeHtml(col) + (isPrimary ? ' <span style="font-size:.6rem;opacity:.6;">(primary)</span>' : '')
      + '</button>';
  });
  headerHtml += '</div></div>';

  // Column content panels
  var contentHtml = '';
  colNames.forEach(function(col, ci) {
    var cr = colResults[col];
    var summary = cr.group_summary || {};
    var groups = cr.groups || {};
    var presetKeys = Object.keys(summary);
    presetKeys.sort(function(a, b) {
      if (a === '_unclassified') return 1;
      if (b === '_unclassified') return -1;
      return (summary[b].count || 0) - (summary[a].count || 0);
    });
    var nonEmpty = presetKeys.filter(function(k) { return summary[k].count > 0 && k !== '_unclassified'; });
    var color = colors[ci % colors.length];

    contentHtml += '<div class="cls-col-panel' + (ci === 0 ? ' active' : '') + '" data-col="' + _escapeHtml(col) + '">';

    // Per-column summary bar
    contentHtml += '<div style="display:flex;gap:12px;align-items:center;margin-bottom:10px;font-size:.76rem;">'
      + '<span><strong>' + (cr.segments_total || 0) + '</strong> segments</span>'
      + '<span><strong>' + nonEmpty.length + '</strong> pattern types found</span>'
      + '<span style="color:var(--text3);">Unclassified: ' + (cr.unclassified || 0) + '</span>'
      + '</div>';

    // Compact preset distribution bar with sparkline icons
    if (nonEmpty.length > 0) {
      var total = cr.segments_total || 1;
      var barColors = ['#059669','#2563eb','#7c3aed','#d97706','#e11d48','#0891b2','#6d28d9','#ea580c'];
      contentHtml += '<div class="cls-col-distbar">';
      nonEmpty.slice(0, 10).forEach(function(pk, idx) {
        var info = summary[pk];
        var pct = Math.round((info.count / total) * 100);
        if (pct < 3) pct = 3;
        var segColor = barColors[idx % barColors.length];

        // Mini sparkline SVG for preset shape
        var miniSvg = '';
        if (PRESET_SHAPES[pk]) {
          var shape = PRESET_SHAPES[pk];
          var mn = Math.min.apply(null, shape), mx = Math.max.apply(null, shape);
          var range = mx - mn || 1;
          var pts = shape.map(function(v, j) {
            return (2 + (j / (shape.length - 1)) * 16).toFixed(1) + ',' + (12 - (v - mn) / range * 10).toFixed(1);
          }).join(' ');
          miniSvg = '<svg viewBox="0 0 20 14" style="width:18px;height:12px;">'
            + '<polyline points="' + pts + '" fill="none" stroke="#fff" stroke-width="1.5" stroke-opacity=".8" stroke-linecap="round"/></svg>';
        }

        contentHtml += '<div class="cls-col-distbar-seg" style="width:' + pct + '%;background:' + segColor + ';" title="'
          + _escapeHtml(info.label || pk) + ': ' + info.count + ' (' + Math.round((info.count / total) * 100) + '%)">'
          + miniSvg
          + (pct >= 10 ? '<span>' + _escapeHtml(info.label || pk) + '</span>' : '')
          + '</div>';
      });
      contentHtml += '</div>';
    }

    // Group list (accordion)
    contentHtml += '<div class="cls-col-groups">';
    presetKeys.forEach(function(pk) {
      var info = summary[pk];
      var segs = groups[pk] || [];
      if (segs.length === 0) return;
      var isUncls = pk === '_unclassified';
      var lineColor = isUncls ? '#9ca3af' : color;

      // Mini sparkline for preset shape
      var svgHtml = '';
      if (!isUncls && PRESET_SHAPES[pk]) {
        var shape = PRESET_SHAPES[pk];
        var pts = shape.map(function(v, i) {
          var x = 2 + (i / (shape.length - 1)) * 32;
          var mn = Math.min.apply(null, shape), mx = Math.max.apply(null, shape);
          var y = 16 - 2 - ((v - mn) / (mx - mn + 0.001)) * 12;
          return x.toFixed(1) + ',' + y.toFixed(1);
        }).join(' ');
        svgHtml = '<svg viewBox="0 0 36 18" style="width:36px;height:18px;flex-shrink:0;">'
          + '<polyline points="' + pts + '" fill="none" stroke="' + lineColor + '" stroke-width="1.5" stroke-linecap="round"/></svg>';
      }

      var simText = '';
      if (info.avg_sim > 0) {
        simText = '<span style="font-size:.68rem;color:var(--text3);margin-left:auto;">'
          + Math.round(info.avg_sim * 100) + '% avg</span>';
      }

      contentHtml += '<div class="cls-col-group-header" onclick="this.nextElementSibling.hidden=!this.nextElementSibling.hidden;">'
        + svgHtml
        + '<span style="font-weight:600;font-size:.78rem;">' + _escapeHtml(info.label || pk) + '</span>'
        + '<span class="cls-col-group-count' + (isUncls ? ' uncls' : '') + '">' + info.count + '</span>'
        + simText
        + '<span style="font-size:.68rem;color:var(--text3);">&#9656;</span>'
        + '</div>';
      contentHtml += '<div class="cls-col-group-body" hidden>';
      segs.slice(0, 20).forEach(function(seg) {
        var simPct = (seg.similarity * 100).toFixed(1);
        var segTR = (seg.start_ts && seg.end_ts) ? (seg.start_ts + ' ~ ' + seg.end_ts) : (seg.start_ts || '');
        contentHtml += '<div class="cls-col-seg">'
          + '<span class="cls-col-seg-label">' + _escapeHtml(seg.source_key || '') + ' ' + _escapeHtml(segTR) + '</span>'
          + '<span class="cls-col-seg-sim" style="color:' + (seg.similarity > 0.7 ? 'var(--green)' : seg.similarity > 0.4 ? 'var(--amber,#d97706)' : 'var(--red,#e11d48)') + ';">' + simPct + '%</span>'
          + '</div>';
      });
      if (segs.length > 20) {
        contentHtml += '<div style="font-size:.68rem;color:var(--text3);padding:4px 8px;">+ ' + (segs.length - 20) + ' more</div>';
      }
      contentHtml += '</div>';
    });
    contentHtml += '</div></div>';
  });

  panel.innerHTML = headerHtml + contentHtml;

  // Tab switching
  panel.querySelectorAll('.cls-col-tab').forEach(function(tab) {
    tab.addEventListener('click', function() {
      panel.querySelectorAll('.cls-col-tab').forEach(function(t) { t.classList.remove('active'); });
      panel.querySelectorAll('.cls-col-panel').forEach(function(p) { p.classList.remove('active'); });
      tab.classList.add('active');
      var target = _findByDataValue(panel, '.cls-col-panel', 'col', tab.dataset.col);
      if (target) target.classList.add('active');
    });
  });
}

// Auto-classify is triggered by onSegSearchDataReady and time unit change

// Detail toggle (collapsible search settings)
