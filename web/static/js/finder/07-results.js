// 07-results.js — 결과 카드 · 질의 정보 바 · 액션 바
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

function labelRange(s, e) {
  var a = S.labels[s] || s, b = S.labels[e] || e;
  a = String(a).replace(/:\d{2}$/, ''); b = String(b).replace(/:\d{2}$/, '');
  return a + ' ~ ' + b;
}

function interpolateNulls(vals) {
  var r = vals.slice();
  for (var i = 0; i < r.length; i++) {
    if (r[i] == null) {
      var p = i - 1; while (p >= 0 && r[p] == null) p--;
      var n = i + 1; while (n < r.length && r[n] == null) n++;
      if (p >= 0 && n < r.length) r[i] = r[p] + (r[n] - r[p]) * (i - p) / (n - p);
      else if (p >= 0) r[i] = r[p];
      else if (n < r.length) r[i] = r[n];
    }
  }
  return r;
}

function createCard(values, qValues, rank, label, simText, color, isQuery, overlayValues, overlayNames, nanIndices, bkInfo) {
  var card = document.createElement('div');
  card.className = 'result-card' + (isQuery ? ' is-query' : '');

  if (bkInfo && !isQuery && BasketManager.has(bkInfo)) {
    card.classList.add('in-basket');
  }

  var plotCanvas = document.createElement('canvas');
  plotCanvas.style.cssText = 'width:100%;height:80px;display:block;padding:4px;box-sizing:border-box;';
  card.appendChild(plotCanvas);

  var info = document.createElement('div');
  info.className = 'result-info';
  var colBadge = '';
  if (overlayValues && overlayValues.length > 0) {
    colBadge = '<span class="ri-cols" style="font-size:.65rem;color:var(--cyan);margin-left:4px;">' + (1 + overlayValues.length) + 'cols</span>';
  }
  var nanBadge = '';
  if (nanIndices && nanIndices.length > 0) {
    nanBadge = '<span class="ri-nan" style="font-size:.6rem;color:var(--red);margin-left:4px;">NaN ' + nanIndices.length + '</span>';
  }
  info.innerHTML =
    '<div class="ri-top">' +
      '<span class="ri-rank" style="color:' + color + '">' + rank + '</span>' +
      '<span class="ri-sim" style="color:' + color + '">' + simText + '</span>' +
      colBadge + nanBadge +
    '</div>' +
    '<div class="ri-label">' + label + '</div>';
  card.appendChild(info);

  if (bkInfo && !isQuery) {
    var bkKey = BasketManager._makeKey(bkInfo);
    var inBk = BasketManager.has(bkInfo);
    var bkBtn = document.createElement('button');
    bkBtn.className = 'bk-add-btn' + (inBk ? ' bk-added' : '');
    bkBtn.setAttribute('data-bk-key', bkKey);
    bkBtn.textContent = inBk ? '\u2713' : '+';
    bkBtn.title = inBk ? 'Remove from Basket' : 'Add to Basket';
    bkBtn.style.cssText = 'position:absolute;bottom:4px;right:4px;';
    bkBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      var isInBasket = bkBtn.classList.contains('bk-added') || BasketManager._keySet.has(bkKey);
      bkBtn.textContent = '...';
      bkBtn.disabled = true;
      if (isInBasket) {
        BasketManager.removeBulk([bkKey]).then(function() {
          bkBtn.textContent = '+';
          bkBtn.classList.remove('bk-added');
          bkBtn.disabled = false;
          bkBtn.title = 'Add to Basket';
          card.classList.remove('in-basket');
        }).catch(function() {
          bkBtn.textContent = '\u2713';
          bkBtn.disabled = false;
        });
      } else {
        BasketManager.add(bkInfo).then(function() {
          bkBtn.textContent = '\u2713';
          bkBtn.classList.add('bk-added');
          bkBtn.title = 'Remove from Basket';
          card.classList.add('in-basket');
        }).catch(function() {
          bkBtn.textContent = '+';
          bkBtn.disabled = false;
          bkBtn.classList.remove('bk-added');
        });
      }
    });
    card.style.position = 'relative';
    card.appendChild(bkBtn);
  }

  var ovColors = ['#E11D48', '#8B5CF6', '#0891B2', '#D97706'];
  var hasNan = nanIndices && nanIndices.length > 0;
  var plotValues = hasNan ? interpolateNulls(values) : values;

  requestAnimationFrame(function() {
    var overlays = [];
    if (overlayValues) {
      overlayValues.forEach(function(ovVals, oi) {
        overlays.push({ values: ovVals, color: ovColors[oi % ovColors.length], lineWidth: 1, dash: [3, 2], opacity: 0.5 });
      });
    }
    drawMiniCanvas(plotCanvas, plotValues, {
      color: color, lineWidth: 1.5,
      fill: true, fillColor: color + '0D',
      overlays: overlays,
      queryValues: (!isQuery && qValues) ? qValues : null,
      nanIndices: hasNan ? nanIndices : null,
    });
  });
  return card;
}

/* ── Column Stats Helper ───────────────────────────────────────── */
function buildColStatItem(name, stats) {
  var preview = stats.mean + ' \u00B1 ' + stats.std;
  return '<div class="col-stat-item">' +
    '<div class="col-stat-header">' +
      '<span class="col-stat-arrow">\u25B6</span>' +
      '<span class="col-stat-name">' + _escapeHtml(name) + '</span>' +
      '<span class="col-stat-preview">' + preview + '</span>' +
    '</div>' +
    '<div class="col-stat-body">' +
      '<div class="info-grid">' +
        '<div class="info-row"><span class="info-label">' + _t('js.lbl_points') + '</span><span class="info-val mono">' + stats.count.toLocaleString() + '</span></div>' +
        '<div class="info-row"><span class="info-label">' + _t('js.lbl_mean') + '</span><span class="info-val mono">' + stats.mean + '</span></div>' +
        '<div class="info-row"><span class="info-label">' + _t('js.lbl_std') + '</span><span class="info-val mono">' + stats.std + '</span></div>' +
        '<div class="info-row"><span class="info-label">' + _t('js.lbl_range') + '</span><span class="info-val mono">' + stats.min + ' ~ ' + stats.max + '</span></div>' +
      '</div>' +
    '</div>' +
  '</div>';
}

function bindColStatToggles() {
  document.querySelectorAll('.col-stat-header').forEach(function(hdr) {
    hdr.addEventListener('click', function() {
      hdr.parentElement.classList.toggle('open');
    });
  });
}

function updateQueryInfo() {
  var bar = $('chartQueryBar');
  if (!bar) return;
  bar.hidden = false;
  // hide generated pattern description when showing manual query info
  if ($('cqbGenDesc')) $('cqbGenDesc').hidden = true;
  var len = S.queryEnd - S.queryStart + 1;
  var range = labelRange(S.queryStart, S.queryEnd);
  $('cqbRange').textContent = range;
  $('cqbIdx').innerHTML = '#<span class="num-blue">' + S.queryStart + '</span> ~ <span class="num-blue">' + S.queryEnd + '</span>';
  $('cqbLen').innerHTML = '<span class="num">' + len + '</span> pts';
  var totalCols = 1 + (S.overlayCols ? S.overlayCols.length : 0);
  if (totalCols > 1) {
    $('cqbCols').hidden = false;
    $('cqbCols').textContent = totalCols + ' cols';
  } else {
    $('cqbCols').hidden = true;
  }
  $('chipQuery').hidden = false;
  $('chipQuery').innerHTML = 'Query: <span class="num">' + len + '</span>pts';
  // Render selection preview in sidebar
  renderSelectPreview();
}

/* ── Selection Preview (mini chart in sidebar) ─────────────────── */
function renderSelectPreview() {
  var hint = document.querySelector('.select-mode-hint');
  var preview = $('selectPreview');
  if (!preview) return;

  if (S.queryStart < 0 || S.queryEnd < 0 || S.queryEnd <= S.queryStart) {
    // No valid selection - show hint, hide preview
    if (hint) hint.hidden = false;
    preview.hidden = true;
    return;
  }

  // Hide hint, show preview
  if (hint) hint.hidden = true;
  preview.hidden = false;

  // Extract selected data
  var yVals = S.values.slice(S.queryStart, S.queryEnd + 1);
  var xVals = yVals.map(function(_, i) { return i; });

  // Render mini chart
  var trace = {
    x: xVals,
    y: yVals,
    type: 'scatter',
    mode: 'lines',
    line: { color: '#3b82f6', width: 1.5 },
    fill: 'tozeroy',
    fillcolor: 'rgba(59,130,246,0.1)'
  };
  var layout = {
    margin: { t: 8, r: 8, b: 20, l: 30 },
    xaxis: { showgrid: false, showticklabels: false, zeroline: false },
    yaxis: { showgrid: true, gridcolor: '#f1f5f9', tickfont: { size: 9, color: '#94a3b8' }, zeroline: false },
    showlegend: false,
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent'
  };
  var config = { displayModeBar: false, staticPlot: true };
  Plotly.react('selectPreviewChart', [trace], layout, config);

  // Update info text
  var range = labelRange(S.queryStart, S.queryEnd);
  var len = S.queryEnd - S.queryStart + 1;
  $('selectPreviewRange').textContent = range;
  $('selectPreviewPoints').textContent = len + ' pts';
}

function clearSelectPreview() {
  var hint = document.querySelector('.select-mode-hint');
  var preview = $('selectPreview');
  if (hint) hint.hidden = false;
  if (preview) preview.hidden = true;
}

/* ── No Results Notice ──────────────────────────────────────── */
function showNoResults(mode, debug) {
  var reasons = [];
  var actions = [];

  if (debug) {
    // segment mode: length mismatch
    if (debug.skip_length > 0 && S.method !== 'dtw') {
      reasons.push('<strong>' + debug.skip_length + '</strong> segments excluded due to length mismatch');
      actions.push({label: 'Switch to DTW', action: 'switch-dtw'});
    }
    // NaN ratio exceeded
    if (debug.skip_nan > 0) {
      var pct = debug.max_nan_ratio ? Math.round(debug.max_nan_ratio * 100) : 50;
      reasons.push('<strong>' + debug.skip_nan + '</strong> excluded due to NaN ratio exceeding ' + pct + '%');
      if (pct < 90) actions.push({label: 'Allow NaN 90%', action: 'raise-nan'});
    }
    // continuous mode: no candidates available
    if (mode === 'cont' && debug.total_windows === 0) {
      reasons.push(_t('js.no_windows'));
    }
    if (mode === 'cont' && debug.skip_nan > 0 && debug.total_windows > 0) {
      reasons.push(debug.skip_nan + ' of ' + debug.total_windows + ' windows excluded due to NaN');
    }
  }

  var target = mode === 'seg' ? $('segSummaryBar') : $('contSummaryBar');
  if (!target) return;

  var html = '<div class="no-results-notice">' +
    '<div class="nrn-title">' + _t('js.no_results_title') + '</div>';

  if (reasons.length > 0) {
    html += '<div class="nrn-reasons">';
    reasons.forEach(function(r) { html += '<div class="nrn-reason">' + r + '</div>'; });
    html += '</div>';
  }

  if (actions.length > 0) {
    html += '<div class="nrn-actions">';
    actions.forEach(function(a) {
      html += '<button class="btn btn-sm nrn-action-btn" data-nrn-action="' + a.action + '">' + a.label + '</button>';
    });
    html += '</div>';
  }
  html += '</div>';

  target.hidden = false;
  target.className = 'seg-summary-bar no-results';
  target.innerHTML = html;

  // show results area (continuous mode)
  if (mode === 'cont') {
    show('resultsSection');
    $('resultsGrid').innerHTML = '';
  }

  // action button events
  target.querySelectorAll('.nrn-action-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var act = this.dataset.nrnAction;
      if (act === 'switch-dtw') {
        // activate DTW pill
        document.querySelectorAll('[data-method]').forEach(function(p) { p.classList.remove('active'); });
        var dtwPill = document.querySelector('[data-method="dtw"]');
        if (dtwPill) { dtwPill.classList.add('active'); S.method = 'dtw'; }
        // re-search immediately
        target.hidden = true;
        $('btnSearch').click();
      } else if (act === 'raise-nan') {
        if ($('sliderNanRatio')) { $('sliderNanRatio').value = 90; }
        if ($('nanRatioVal')) { $('nanRatioVal').textContent = '90%'; }
        S.nanMaxRatio = 0.9;
        target.hidden = true;
        $('btnSearch').click();
      }
    });
  });
}

function resetResults() {
  hide('resultsSection');
  if ($('contSummaryBar')) { $('contSummaryBar').hidden = true; $('contSummaryBar').innerHTML = ''; }
  if ($('chartQueryBar')) $('chartQueryBar').hidden = true;
  $('chipQuery').hidden = true;
  $('btnSearch').disabled = true;
  updateActionBar();
}

/* ── Action Bar ──────────────────────────────────────────────── */
function updateActionBar() {
  var cqb = $('chartQueryBar');
  if (!cqb) return;

  if (S.mode === 'continuous' && S.generatedPattern) {
    // generated pattern mode — show query bar with pattern info
    cqb.hidden = false;
  } else if (S.mode === 'continuous' && S.queryStart >= 0) {
    // continuous with a selection — updateQueryInfo() owns the bar, leave it alone
  } else {
    cqb.hidden = true;
  }
}
// describe button — describe selected region with LLM
if ($('qabDescribeBtn')) {
  $('qabDescribeBtn').addEventListener('click', function() {
    describeSelectedRegion();
  });
}
