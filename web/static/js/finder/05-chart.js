// 05-chart.js — 본 차트 렌더링
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

/* ── Main Chart ────────────────────────────────────────────── */
function drawMainChart() {
  var values = S.values, labels = S.labels, N = values.length;
  var idx = []; for (var i = 0; i < N; i++) idx.push(i);

  // tick labels — format varies by index type
  var step = Math.max(1, Math.floor(N / 10));
  var tv = [], tt = [];
  var isNumericIndex = S.indexType === 'numeric';
  for (var k = 0; k < N; k += step) {
    tv.push(k);
    // show blank for gap markers or null positions
    var isGap = labels[k] === '—' || labels[k] === '-'
                || values[k] === null || values[k] === undefined
                || (typeof values[k] === 'number' && isNaN(values[k]));
    if (isGap) {
      tt.push('');
    } else if (isNumericIndex) {
      var lbl = labels[k] || String(k);
      tt.push(lbl.replace(/^0+(\d)/, '$1'));
    } else {
      tt.push(shortLabel(labels[k]));
    }
  }

  var hasOverlay = S.overlays.length > 0;
  var ovColors = ['#E11D48', '#8B5CF6', '#0891B2', '#D97706'];

  var hoverTpl = isNumericIndex
    ? '#%{text}<br>%{y:.2f}<extra></extra>'
    : '%{text}<br>%{y:.2f}<extra></extra>';

  var traces = [{
    x: idx, y: values, type: 'scatter', mode: 'lines',
    name: $('chartTitleText').textContent || 'data', line: { color: '#3B82F6', width: 1.2 },
    text: labels, hovertemplate: hoverTpl,
    yaxis: 'y', connectgaps: false,
  }];

  // overlay traces (secondary y-axis)
  if (hasOverlay) {
    S.overlays.forEach(function(ov, oi) {
      var ovIdx = []; for (var j = 0; j < ov.values.length; j++) ovIdx.push(j);
      traces.push({
        x: ovIdx, y: ov.values, type: 'scatter', mode: 'lines',
        name: ov.name,
        line: { color: ovColors[oi % ovColors.length], width: 1, dash: 'dot' },
        opacity: 0.6,
        text: ov.labels, hovertemplate: ov.name + '<br>%{text}<br>%{y:.2f}<extra></extra>',
        yaxis: 'y2', connectgaps: false,
      });
    });
  }

  if (S.queryStart >= 0 && S.queryEnd >= 0) {
    traces.push({
      x: idx.slice(S.queryStart, S.queryEnd + 1),
      y: values.slice(S.queryStart, S.queryEnd + 1),
      type: 'scatter', mode: 'lines', name: 'Query',
      line: { color: '#059669', width: 3 }, hoverinfo: 'skip',
      yaxis: 'y',
      showlegend: false,
    });
  }

  S.results.forEach(function(r, i) {
    traces.push({
      x: idx.slice(r.start, r.end + 1),
      y: values.slice(r.start, r.end + 1),
      type: 'scatter', mode: 'lines',
      name: '#' + (i+1), line: { color: '#F59E0B', width: 2.5 },
      text: labels.slice(r.start, r.end + 1),
      hovertemplate: '#' + (i+1) + ' | %{text}<br>%{y:.2f}<extra></extra>',
      yaxis: 'y',
      showlegend: false,
    });
  });

  var shapes = [];
  if (S.queryStart >= 0 && S.queryEnd >= 0) {
    shapes.push({ type:'rect', xref:'x', yref:'paper',
      x0:S.queryStart, x1:S.queryEnd, y0:0, y1:1,
      fillcolor:'rgba(5,150,105,.08)', line:{color:'#059669',width:1,dash:'dot'} });
  }
  S.results.forEach(function(r) {
    shapes.push({ type:'rect', xref:'x', yref:'paper',
      x0:r.start, x1:r.end, y0:0, y1:1,
      fillcolor:'rgba(245,158,11,.06)', line:{color:'#F59E0B',width:1,dash:'dot'} });
  });

  var layout = {
    margin: { t:8, r: hasOverlay ? 60 : 16, b:44, l:52 },
    xaxis: { type:'linear', tickvals:tv, ticktext:tt, tickangle:-25,
             showgrid:true, gridcolor:'#F0F0F0', zeroline:false,
             tickfont:{size:10} },
    yaxis: { showgrid:true, gridcolor:'#F0F0F0', zeroline:false,
             tickfont:{size:10} },
    shapes: shapes, showlegend: hasOverlay,
    legend: { x:0, y:1.02, orientation:'h', font:{size:10} },
    dragmode: 'select', selectdirection: 'h',
    paper_bgcolor:'#FAFBFC', plot_bgcolor:'#FAFBFC',
    font: { family:'Pretendard, sans-serif', size:11 },
    hovermode: 'x unified',
  };
  if (hasOverlay) {
    layout.yaxis2 = {
      overlaying: 'y', side: 'right',
      showgrid: false, zeroline: false,
      tickfont: { size: 9, color: ovColors[0] },
    };
  }

  Plotly.newPlot('plotMain', traces, layout, {
    responsive:true, scrollZoom:true,
    displayModeBar:false, displaylogo:false,
  });

  var plotEl = $('plotMain');
  if (plotEl.removeAllListeners) plotEl.removeAllListeners('plotly_selected');
  plotEl.on('plotly_selected', function(ev) {
    if (_queryMode !== 'select') return;  // Block chart selection except in select mode
    if (!ev || !ev.range) return;
    var i0 = Math.max(0, Math.round(ev.range.x[0]));
    var i1 = Math.min(N-1, Math.round(ev.range.x[1]));

    if ($('chkFixed').checked) {
      var fl = parseInt($('inputFixedLen').value) || DEFAULT_SEG_LEN;
      if (S.alignMode === 'calendar') {
        i0 = snapToNearest(i0);
        /* Use next calendar boundary as endpoint (accurate range even with data gaps) */
        var nextB = findNextBoundary(i0);
        i1 = nextB > i0 ? nextB - 1 : Math.min(N-1, i0 + fl - 1);
      } else {
        i1 = Math.min(N-1, i0 + fl - 1);
      }
    }

    if (i1 - i0 < 2) return;
    // reset generated pattern on manual selection
    if (S.generatedPattern) {
      S.generatedPattern = null;
      if ($('cqbGenDesc')) $('cqbGenDesc').hidden = true;
    }
    S.queryStart = i0; S.queryEnd = i1; S.results = [];
    $('btnSearch').disabled = false;
    updateUnifiedSearchBtn();
    updateQueryInfo();
    drawMainChart();
    updateActionBar();
  });
  plotEl.on('plotly_selecting', function() {});
}

function shortLabel(s) {
  if (!s) return '';
  var m = String(s).match(/\d{4}-(\d{2}-\d{2})/);
  return m ? m[1] : String(s).substring(0,8);
}

/* ── Search ──────────────────────────────────────────────────── */
