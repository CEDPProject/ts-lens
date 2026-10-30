/**
 * TS-Lens Discovery Studio
 * Builds a compact pattern portfolio from periodicity, k-sweep clustering,
 * optional MOMENT/LLM interpretation, and user curation.
 */
(function () {
  'use strict';

  // Search defaults from config/search.yaml (window.SEARCH_CONFIG, set in base.html).
  var SCFG = window.SEARCH_CONFIG || {};
  var DEFAULT_TOP_K = SCFG.segment_top_k != null ? SCFG.segment_top_k : 20;

  var $ = function (id) { return document.getElementById(id); };
  var $$ = function (sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); };
  // Resolve at CALL time — window._t is defined by base.html in a script that
  // runs AFTER this file, so capturing it now would freeze the key-returning
  // fallback and labels would show raw keys.
  var _t = function () {
    return (window._t || function (k) { return k; }).apply(null, arguments);
  };

  var S = { datasetKey: null, selectedCols: [] };
  var cfg = { unit: 'auto', kMin: 3, kMax: 12, rank: 'distinct', normalize: true };
  var state = freshState();

  function freshState() {
    return {
      running: false,
      unit: null,
      totalSegments: 0,
      trials: [],
      bestTrial: null,
      clusters: [],
      evaluations: [],
      recommendations: [],
      selectedIdx: null,
      excludedCentroids: [],
      hilHistory: [],
      cacheKey: null,
      dateStart: null,
      dateEnd: null,
      segmentEstimate: 0,
      unitRefined: false,
      interpretDegraded: false,
    };
  }

  // Segment units from coarsest to finest. Used to step down to a finer unit
  // when an auto-inferred coarse unit would yield too few segments to cluster.
  var UNIT_LADDER = ['month', 'week', 'day', '6hour', 'hour'];

  function estimateSegments(unit, startTs, endTs) {
    if (!startTs || !endTs) return 0;
    var a = Date.parse(startTs), b = Date.parse(endTs);
    if (!isFinite(a) || !isFinite(b) || b <= a) return 0;
    var days = (b - a) / 86400000;
    switch (unit) {
      case 'hour': return Math.floor(days * 24) + 1;
      case '6hour': return Math.floor(days * 4) + 1;
      case 'day': return Math.floor(days) + 1;
      case 'week': return Math.floor(days / 7) + 1;
      case 'month': return Math.floor(days / 30) + 1;
      default: return Math.floor(days) + 1;
    }
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function show(id) { var e = $(id); if (e) e.hidden = false; }
  function hide(id) { var e = $(id); if (e) e.hidden = true; }

  function loadSelection() {
    try {
      // One-shot hand-off: a selection is only valid when the user just arrived
      // via "Go to Discovery" in the Finder. Consume it immediately so a reload,
      // a direct visit, or leftover session state never silently reuses an old
      // selection. We deliberately do NOT fall back to finder_current_selection
      // (it lingers in sessionStorage and would resurrect stale selections).
      var raw = sessionStorage.getItem('discovery_selection');
      sessionStorage.removeItem('discovery_selection');
      if (!raw) return false;
      var sel = JSON.parse(raw);
      // Finder hands off the first selected column as primaryCol for backward
      // compatibility, but Discovery treats all handed-off columns as peers.
      if (!sel || !sel.datasetKey || !sel.primaryCol) return false;
      S.datasetKey = sel.datasetKey;
      S.selectedCols = [sel.primaryCol].concat(sel.overlayCols || []).filter(Boolean)
        .filter(function (c, i, arr) { return arr.indexOf(c) === i; });
      console.log('[Discovery] Loaded selection:', S.datasetKey, S.selectedCols);
      return true;
    } catch (e) {
      console.error('[Discovery] loadSelection error:', e);
      return false;
    }
  }

  function primaryCol() {
    return (S.selectedCols && S.selectedCols[0]) || null;
  }

  function siblingCols() {
    return (S.selectedCols || []).slice(1);
  }

  function renderSelection() {
    if ($('discoveryDatasetName')) $('discoveryDatasetName').textContent = S.datasetKey || '-';
    if ($('discoveryColumns')) $('discoveryColumns').textContent = (S.selectedCols || []).join(', ') || '-';
  }

  function readConfig() {
    cfg.unit = ($('cfgUnit') && $('cfgUnit').value) || 'auto';
    cfg.rank = ($('cfgRank') && $('cfgRank').value) || 'balanced';
    cfg.normalize = !($('cfgNormalize') && $('cfgNormalize').value === 'off');
    var kMin = parseInt($('cfgKMin') && $('cfgKMin').value, 10);
    var kMax = parseInt($('cfgKMax') && $('cfgKMax').value, 10);
    cfg.kMin = isNaN(kMin) ? 3 : Math.max(2, Math.min(20, kMin));
    cfg.kMax = isNaN(kMax) ? 10 : Math.max(cfg.kMin, Math.min(20, kMax));
  }

  function setProgress(pct, label) {
    var fill = $('discoveryProgressFill');
    var lbl = $('discoveryProgressLabel');
    var chip = $('agentPhaseChip');
    if (fill) fill.style.width = Math.max(0, Math.min(100, pct)) + '%';
    if (lbl) lbl.textContent = label || '';
    if (chip) chip.textContent = label || _t('finder.discovery_status_running');
  }

  function setStep(key, status, detail) {
    var el = document.querySelector('.discovery-step[data-step="' + key + '"]');
    if (!el) return;
    el.classList.remove('is-active', 'is-done', 'is-warn');
    if (status === 'active') el.classList.add('is-active');
    if (status === 'done') el.classList.add('is-done');
    if (status === 'warn') el.classList.add('is-warn');
    var small = el.querySelector('small');
    if (small) small.textContent = detail || status || _t('finder.discovery_status_waiting');
  }

  function resetUi() {
    state = freshState();
    ['periodicity', 'cluster', 'interpret', 'rank'].forEach(function (k) { setStep(k, '', _t('finder.discovery_status_waiting')); });
    if ($('trialList')) $('trialList').innerHTML = '<div class="discovery-muted">' + escapeHtml(_t('finder.discovery_sweep_running')) + '</div>';
    if ($('recommendedClusters')) $('recommendedClusters').innerHTML = '';
    if ($('agentResultPlaceholder')) {
      $('agentResultPlaceholder').textContent = _t('finder.discovery_running');
      $('agentResultPlaceholder').hidden = false;
    }
    if ($('agentResultCount')) $('agentResultCount').hidden = true;
    if ($('inspectorEmpty')) $('inspectorEmpty').hidden = false;
    if ($('inspectorBody')) $('inspectorBody').hidden = true;
    if ($('discoveryNotice')) $('discoveryNotice').hidden = true;
    if ($('btnMatrix')) $('btnMatrix').hidden = true;
    if ($('matrixModal')) $('matrixModal').hidden = true;
    collapseRunPanels(false);
    renderSummary();
    setProgress(0, _t('finder.discovery_ready'));
  }

  function fetchJson(url, options) {
    return fetch(url, options).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok || data.error) throw new Error(data.error || ('HTTP ' + r.status));
        return data;
      });
    });
  }

  // The backend API still accepts one base column plus sibling columns, but
  // Discovery treats them as one selected column set with equal clustering
  // weight. The first column is only the cache/API anchor.
  async function prepareData() {
    try {
      var info = await fetchJson('/api/segment_search/prepare_cluster', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dataset_key: S.datasetKey,
          col_name: primaryCol(),
          overlay_cols: siblingCols().length ? siblingCols() : undefined,
        }),
      });
      state.cacheKey = info.cache_key || null;
      state.dateStart = info.date_start || null;
      state.dateEnd = info.date_end || null;
    } catch (e) {
      // Non-fatal: cluster calls will fall back to a fresh DB load.
    }
    // Fetch the full column list so we can profile every column per discovered group.
    try {
      var cols = await fetchJson('/api/column_limits/' + encodeURIComponent(S.datasetKey));
      state.profileCols = (cols.columns || []).map(function (c) { return c.col_name; }).filter(Boolean);
    } catch (e2) {
      state.profileCols = S.selectedCols.slice();
    }
  }

  // Re-run clustering at the chosen k WITH profile columns so the backend returns
  // per-cluster average shapes of each profiled column (column_profiles) and how
  // strongly each column separates the clusters (column_discriminative).
  // Same data + fixed seed + same k → identical clustering, so keys align.
  async function enrichWithProfiles(unit) {
    state.columnProfiles = {};
    state.columnDiscriminative = {};
    if (!state.bestTrial) return;
    var anchor = primaryCol();
    var profCols = (state.profileCols && state.profileCols.length) ? state.profileCols : S.selectedCols.slice();
    // Keep the API anchor first so the backend can reuse cached data, while the
    // resulting column profile matrix still presents all selected columns evenly.
    profCols = [anchor].concat(profCols.filter(function (c) { return c !== anchor; }));
    try {
      var body = {
        dataset_key: S.datasetKey,
        col_name: anchor,
        unit: unit,
        k: state.bestTrial.k,
        method: 'shape',
        normalize: cfg.normalize,
        profile_cols: profCols,
      };
      if (unit) body.profile_unit = unit;
      if (state.cacheKey) body.cache_key = state.cacheKey;
      if (siblingCols().length) body.overlay_cols = siblingCols();
      var result = await fetchJson('/api/segment_search/cluster', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      state.clusters = toClusterArray(result);
      state.columnProfiles = result.column_profiles || {};
      state.columnDiscriminative = result.column_discriminative || {};
    } catch (e) {
      // Non-fatal: keep the sweep's best clusters, just without column profiles.
      state.clusters = state.bestTrial.clusters;
    }
  }

  // When auto unit inference picks a coarse unit (e.g. month) over a short span,
  // each calendar bucket becomes a segment and there are too few of them — the
  // backend then caps cluster count at (segments - 1), so the K sweep collapses
  // to k=kMin. Step down to a finer unit until there are enough segments.
  function refineUnit(unit) {
    state.segmentEstimate = estimateSegments(unit, state.dateStart, state.dateEnd);
    if (cfg.unit !== 'auto' || !state.dateStart || !state.dateEnd) return unit;
    var need = cfg.kMax + 1;
    var idx = UNIT_LADDER.indexOf(unit);
    if (idx < 0) return unit;
    var chosen = unit;
    for (var i = idx; i < UNIT_LADDER.length; i++) {
      var est = estimateSegments(UNIT_LADDER[i], state.dateStart, state.dateEnd);
      chosen = UNIT_LADDER[i];
      state.segmentEstimate = est;
      if (est >= need) break;
    }
    if (chosen !== unit) {
      state.unit = chosen;
      state.unitRefined = true;
      setStep('periodicity', 'done', _t('finder.discovery_unit_refined_short', chosen));
    }
    return chosen;
  }

  async function inferUnit() {
    setStep('periodicity', 'active', _t('finder.discovery_status_scanning'));
    setProgress(8, _t('finder.discovery_step_periodicity'));

    var unit = 'day';
    if (cfg.unit !== 'auto') {
      state.unit = cfg.unit;
      setStep('periodicity', 'done', _t('finder.discovery_unit_manual', cfg.unit));
      return cfg.unit;
    }

    try {
      var data = await fetchJson('/api/periodicity_all/' + encodeURIComponent(S.datasetKey));
      var best = null;
      if (data.columns) {
        (S.selectedCols || []).forEach(function (col) {
          var info = data.columns[col];
          if (!info || !info.has) return;
          if (!best || (info.strength || 0) > (best.strength || 0)) best = info;
        });
        if (!best) {
          Object.keys(data.columns).forEach(function (col) {
            var info = data.columns[col];
            if (!info || !info.has) return;
            if (!best || (info.strength || 0) > (best.strength || 0)) best = info;
          });
        }
      }
      if (best && best.period) {
        var p = String(best.period).toLowerCase();
        if (p.indexOf('hour') >= 0) unit = 'hour';
        else if (p.indexOf('week') >= 0) unit = 'week';
        else if (p.indexOf('month') >= 0) unit = 'month';
        else if (p.indexOf('day') >= 0) unit = 'day';
      }
      state.unit = unit;
      setStep('periodicity', 'done', unit);
      return unit;
    } catch (e) {
      state.unit = unit;
      setStep('periodicity', 'warn', _t('finder.discovery_unit_fallback', 'day'));
      return unit;
    }
  }

  function toClusterArray(result) {
    var groups = result.groups || {};
    var groupSummary = result.group_summary || {};
    var centroids = result.centroids || result.centroids_display || {};
    return Object.keys(groups).map(function (key) {
      var segs = groups[key] || [];
      return {
        key: key,
        segments: segs,
        centroid: centroids[key] || [],
        summary: groupSummary[key] || {},
        count: (groupSummary[key] || {}).count || segs.length,
      };
    });
  }

  function normalizeMetric(values, value, invert) {
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    if (!isFinite(min) || !isFinite(max) || max === min) return 0.5;
    var n = (value - min) / (max - min);
    return invert ? 1 - n : n;
  }

  async function runClusterSweep(unit) {
    setStep('cluster', 'active', cfg.kMin + '-' + cfg.kMax);
    setProgress(18, _t('finder.discovery_step_cluster'));

    var kValues = [];
    for (var k = cfg.kMin; k <= cfg.kMax; k++) kValues.push(k);

    for (var i = 0; i < kValues.length; i++) {
      var kk = kValues[i];
      setProgress(18 + Math.round(44 * i / Math.max(1, kValues.length)), _t('finder.discovery_cluster_k', kk));
      try {
        var body = {
          dataset_key: S.datasetKey,
          col_name: primaryCol(),
          unit: unit,
          k: kk,
          method: 'shape',
          normalize: cfg.normalize,
        };
        if (state.cacheKey) body.cache_key = state.cacheKey;
        // Joint clustering over the selected columns with equal column weights.
        if (siblingCols().length) body.overlay_cols = siblingCols();
        var result = await fetchJson('/api/segment_search/cluster', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        // 구간이 너무 짧아 군집이 아예 안 되는 경우를 그대로 알린다.
        // (예전에는 빈 결과가 조용히 흘러가 클러스터마다 "데이터 부족"만 떴다)
        if (result.reason === 'segment_too_short') {
          throw new Error(_t('finder.discovery_seg_too_short',
            String(result.segment_length), String(result.min_segment_length)));
        }
        state.totalSegments = Math.max(state.totalSegments, result.segments_total || 0);
        state.trials.push({
          k: kk,
          silhouette: result.silhouette_score || 0,
          calinski: result.calinski_harabasz_score || 0,
          davies: result.davies_bouldin_score || 999,
          clusters: toClusterArray(result),
        });
        renderTrials();
      } catch (e) {
        state.trials.push({ k: kk, error: e.message, silhouette: -1, calinski: 0, davies: 999, clusters: [] });
        renderTrials();
      }
    }

    var valid = state.trials.filter(function (t) { return !t.error && t.clusters.length; });
    if (!valid.length) throw new Error(_t('js.no_cluster'));

    var sils = valid.map(function (t) { return t.silhouette; });
    var cals = valid.map(function (t) { return t.calinski; });
    var dbs = valid.map(function (t) { return t.davies; });
    valid.forEach(function (t) {
      t.score = 0.42 * normalizeMetric(sils, t.silhouette, false) +
        0.28 * normalizeMetric(cals, t.calinski, false) +
        0.30 * normalizeMetric(dbs, t.davies, true);
    });
    // Internal validity metrics (silhouette/CH/DB) on smooth time-series shapes
    // tend to fall monotonically with k, so taking the raw max score always
    // lands on kMin. Pick the "knee" instead — the point of diminishing returns.
    state.bestTrial = pickKneeTrial(valid);
    state.clusters = state.bestTrial.clusters;
    renderTrials();
    updateNotice();
    setStep('cluster', 'done', _t('finder.discovery_best_k', state.bestTrial.k));
  }

  // Kneedle-style elbow selection on the (k, score) curve. Normalizes both axes
  // to [0,1], then picks the trial farthest from the straight line connecting
  // the first and last point. Endpoints (kMin/kMax) are zero-distance by
  // construction, so this cannot trivially collapse to kMin. Falls back to the
  // max-score trial when there is no real elbow (flat/too few points).
  function pickKneeTrial(trials) {
    var maxByScore = function (arr) {
      return arr.slice().sort(function (a, b) { return b.score - a.score; })[0];
    };
    var pts = trials.slice().sort(function (a, b) { return a.k - b.k; });
    var n = pts.length;
    if (n <= 2) return maxByScore(pts);
    var ks = pts.map(function (p) { return p.k; });
    var ss = pts.map(function (p) { return p.score; });
    var kMin = Math.min.apply(null, ks), kMax = Math.max.apply(null, ks);
    var sMin = Math.min.apply(null, ss), sMax = Math.max.apply(null, ss);
    var kr = (kMax - kMin) || 1, sr = (sMax - sMin) || 1;
    var y0 = (ss[0] - sMin) / sr, y1 = (ss[n - 1] - sMin) / sr;
    var dx = 1, dy = y1 - y0;
    var denom = Math.sqrt(dx * dx + dy * dy) || 1;
    var bestIdx = 0, bestDist = -Infinity;
    for (var i = 0; i < n; i++) {
      var x = (ks[i] - kMin) / kr;
      var y = (ss[i] - sMin) / sr;
      var dist = Math.abs(dy * (x - 0) - dx * (y - y0)) / denom;
      if (dist > bestDist) { bestDist = dist; bestIdx = i; }
    }
    if (bestDist < 1e-6) return maxByScore(pts);
    return pts[bestIdx];
  }

  // Surface why the cluster count may be limited: when there are only a few
  // segments, the backend caps clusters at (segments - 1), so the K sweep can't
  // reach high k regardless of the requested range.
  function updateNotice() {
    var el = $('discoveryNotice');
    if (!el) return;
    var segs = state.totalSegments || 0;
    var maxK = Math.max(2, segs - 1);
    var msgs = [];
    if (state.unitRefined) {
      msgs.push(_t('finder.discovery_notice_unit_refined', '<strong>' + escapeHtml(state.unit) + '</strong>'));
    }
    if (segs && segs <= cfg.kMax + 1) {
      msgs.push(_t('finder.discovery_notice_seg_cap', '<strong>' + segs + '</strong>', '<strong>' + maxK + '</strong>'));
    }
    if (state.interpretDegraded) {
      msgs.push(_t('finder.discovery_notice_interpret_degraded'));
    }
    if (msgs.length) {
      el.innerHTML = msgs.join('<br>');
      el.hidden = false;
    } else {
      el.hidden = true;
    }
  }

  function analyzeShape(values) {
    if (!values || values.length < 3) return { shape: _t('js.shape_short'), summary: _t('js.shape_short_sum'), confidence: 35 };
    var n = values.length;
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var range = max - min || 1;
    var start = values[0];
    var end = values[n - 1];
    var mid = values[Math.floor(n / 2)];
    var mean = values.reduce(function (a, b) { return a + b; }, 0) / n;
    var variance = values.reduce(function (sum, v) { return sum + Math.pow(v - mean, 2); }, 0) / n;
    var volatility = Math.sqrt(variance) / range;
    var trend = (end - start) / range;

    if (Math.abs(trend) < 0.15 && volatility < 0.18) return { shape: _t('js.shape_stable'), summary: _t('js.shape_stable_sum'), confidence: 68 };
    if (trend > 0.45) return { shape: _t('js.shape_rising'), summary: _t('js.shape_rising_sum'), confidence: 72 };
    if (trend < -0.45) return { shape: _t('js.shape_falling'), summary: _t('js.shape_falling_sum'), confidence: 72 };
    if (mid > start && mid > end) return { shape: _t('js.shape_peak'), summary: _t('js.shape_peak_sum'), confidence: 74 };
    if (mid < start && mid < end) return { shape: _t('js.shape_dip'), summary: _t('js.shape_dip_sum'), confidence: 74 };
    if (volatility > 0.34) return { shape: _t('js.shape_volatile'), summary: _t('js.shape_volatile_sum'), confidence: 65 };
    return { shape: _t('js.shape_mixed'), summary: _t('js.shape_mixed_sum'), confidence: 58 };
  }

  async function interpretClusters() {
    setStep('interpret', 'active', _t('finder.discovery_status_profiling'));
    setProgress(70, _t('finder.discovery_step_interpret'));

    var centroids = state.clusters.map(function (c) { return c.centroid || []; });
    var sizes = state.clusters.map(function (c) { return c.count || 0; });
    var moment = null;

    try {
      moment = await fetchJson('/api/moment/analyze_clusters', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ centroids: centroids, sizes: sizes, total: state.totalSegments }),
      });
    } catch (e) {
      moment = null;
    }

    var llm = null;
    if (moment && moment.results) {
      try {
        llm = await fetchJson('/api/evaluate_clusters_llm', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            moment_results: moment.results,
            dataset_info: { columns: S.selectedCols || [], unit: state.unit, domain: 'general' },
          }),
        });
      } catch (e2) {
        llm = null;
      }
    }

    state.evaluations = state.clusters.map(function (cluster, idx) {
      var centroid = cluster.centroid || [];
      var fallback = analyzeShape(centroid);
      var mr = moment && moment.results && moment.results[idx] ? moment.results[idx] : {};
      var le = llm && llm.evaluations && llm.evaluations[idx] ? llm.evaluations[idx] : {};
      var count = cluster.count || 0;
      var ratio = state.totalSegments ? Math.round(count / state.totalSegments * 100) : 0;
      var confidence = le.llm_confidence || mr.confidence || fallback.confidence;
      var shape = mr.shape || fallback.shape;
      var label = le.domain_label || mr.domain_label || shape;
      var summary = le.reason || mr.description || fallback.summary;
      return {
        idx: idx,
        key: cluster.key,
        centroid: centroid,
        count: count,
        ratio: ratio,
        shape: shape,
        label: label,
        summary: summary,
        confidence: confidence,
        meaningful: le.meaningful !== false,
        cluster: cluster,
        profiles: (state.columnProfiles && cluster.key) ? state.columnProfiles[cluster.key] : null,
      };
    });

    setStep('interpret', 'done', llm && llm.llm_used ?
      _t('finder.discovery_model_profile') : _t('finder.discovery_stat_profile'));
  }

  // Compute cosine similarity between two centroid arrays
  function centroidSimilarity(a, b) {
    if (!a || !b || !a.length || !b.length) return 0;
    var minLen = Math.min(a.length, b.length);
    var dotProduct = 0, normA = 0, normB = 0;
    for (var i = 0; i < minLen; i++) {
      dotProduct += a[i] * b[i];
      normA += a[i] * a[i];
      normB += b[i] * b[i];
    }
    if (normA === 0 || normB === 0) return 0;
    return dotProduct / (Math.sqrt(normA) * Math.sqrt(normB));
  }

  // Greedy diverse selection: pick patterns that are dissimilar to already selected ones
  function selectDiverse(candidates, maxCount) {
    if (!candidates.length) return [];
    var selected = [candidates[0]]; // Start with highest scored
    var remaining = candidates.slice(1);

    while (selected.length < maxCount && remaining.length > 0) {
      var bestIdx = -1;
      var bestDiversity = -Infinity;

      for (var i = 0; i < remaining.length; i++) {
        // Compute min similarity to any already selected pattern
        var maxSim = 0;
        for (var j = 0; j < selected.length; j++) {
          var sim = centroidSimilarity(remaining[i].centroid, selected[j].centroid);
          if (sim > maxSim) maxSim = sim;
        }
        // Diversity score = base score * (1 - max_similarity_to_selected)
        var diversityScore = remaining[i].score * (1 - maxSim * 0.5);
        if (diversityScore > bestDiversity) {
          bestDiversity = diversityScore;
          bestIdx = i;
        }
      }

      if (bestIdx >= 0) {
        selected.push(remaining[bestIdx]);
        remaining.splice(bestIdx, 1);
      } else {
        break;
      }
    }
    return selected;
  }

  function rankRecommendations() {
    setStep('rank', 'active', _t('finder.discovery_status_scoring'));
    setProgress(88, _t('finder.discovery_step_rank'));

    var scored = state.evaluations.map(function (e) {
      var range = e.centroid && e.centroid.length ? Math.max.apply(null, e.centroid) - Math.min.apply(null, e.centroid) : 0;
      var coverage = Math.min(1, e.ratio / 35);
      var clarity = Math.min(1, Math.abs(range) / 2.2);
      var conf = Math.min(1, (e.confidence || 50) / 100);
      var score;
      if (cfg.rank === 'frequent') score = 0.55 * coverage + 0.25 * conf + 0.20 * clarity;
      else if (cfg.rank === 'distinct') score = 0.55 * clarity + 0.25 * conf + 0.20 * coverage;
      else score = 0.36 * coverage + 0.34 * clarity + 0.30 * conf;
      e.score = Math.round(score * 100);
      return e;
    })
      .filter(function (e) { return e.meaningful && e.count > 0; })
      .sort(function (a, b) { return b.score - a.score; });

    // Safety net: the interpreter marks patterns as not-meaningful, so a bad
    // interpretation pass could discard every cluster and render an empty
    // portfolio even though clustering succeeded. Clusters that exist are always
    // worth showing; keep them, ranked, rather than showing nothing.
    if (!scored.length) {
      scored = state.evaluations
        .filter(function (e) { return e.count > 0; })
        .sort(function (a, b) { return b.score - a.score; });
      if (scored.length) state.interpretDegraded = true;
    }

    // Apply diversity-aware selection (top 6 diverse patterns from candidates)
    state.recommendations = selectDiverse(scored, 6);

    setStep('rank', 'done', _t('finder.discovery_pattern_count', state.recommendations.length));
    setProgress(100, _t('finder.discovery_status_done'));
  }

  // Reflect the left column's collapsed state onto the grid so its width is
  // reclaimed by the main results area when every left panel is folded.
  function syncLeftWidth() {
    var grid = document.querySelector('.discovery-grid');
    var panels = $$('#discoveryLeft .discovery-collapsible');
    if (!grid || !panels.length) return;
    var allCollapsed = panels.every(function (p) { return p.classList.contains('collapsed'); });
    grid.classList.toggle('left-collapsed', allCollapsed);
  }

  // Bring the inspector into view — collapsing the portfolio is only useful if
  // the user actually sees the detail panel that sits below it.
  function focusInspector() {
    var insp = document.querySelector('.discovery-inspector');
    if (insp && insp.scrollIntoView) insp.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function collapseRunPanels(collapsed) {
    $$('#discoveryLeft .discovery-collapsible').forEach(function (p) {
      p.classList.toggle('collapsed', !!collapsed);
    });
    syncLeftWidth();
  }

  async function runDiscovery() {
    if (state.running || !S.datasetKey) return;
    readConfig();
    // Keep the settings bar visible: re-running with a different segment unit or
    // normalization is the normal loop, and hiding it stranded the user (the only
    // way back was leaving for the Finder).
    show('discoverPanel');
    resetUi();
    state.running = true;

    try {
      await prepareData();
      var unit = await inferUnit();
      unit = refineUnit(unit);
      await runClusterSweep(unit);
      await enrichWithProfiles(unit);
      await interpretClusters();
      rankRecommendations();
      renderSummary();
      renderRecommendations();
      // The Run Map / K Sweep panels are only useful while running — fold them
      // away once results are on screen (user can click to expand again).
      collapseRunPanels(true);
    } catch (e) {
      setProgress(100, _t('finder.discovery_status_failed'));
      if ($('agentResultPlaceholder')) {
        $('agentResultPlaceholder').hidden = false;
        $('agentResultPlaceholder').textContent = e.message || _t('finder.discovery_failed');
      }
      setStep('rank', 'warn', _t('finder.discovery_status_failed'));
    } finally {
      state.running = false;
    }
  }

  function renderTrials() {
    var el = $('trialList');
    if (!el) return;
    if (!state.trials.length) {
      el.innerHTML = '<div class="discovery-muted">' + escapeHtml(_t('finder.discovery_no_trials')) + '</div>';
      return;
    }
    var bestK = state.bestTrial && state.bestTrial.k;
    el.innerHTML = state.trials.map(function (t) {
      var cls = t.k === bestK ? ' is-best' : '';
      var score = t.score == null ? '-' : Math.round(t.score * 100);
      var meta = t.error ? escapeHtml(t.error) : ('sil ' + Number(t.silhouette || 0).toFixed(2) + ' / score ' + score);
      // Compact chip: full metrics live in the tooltip so the panel wraps
      // horizontally and keeps a stable height as the k range grows.
      return '<div class="discovery-k-item' + cls + '" title="k=' + t.k + ' · ' + meta + '">' +
        '<strong>k=' + t.k + '</strong>' +
        '<span>' + (t.error ? '!' : score) + '</span>' +
        '</div>';
    }).join('');
  }

  function renderSummary() {
    var el = $('discoverySummary');
    if (!el) return;
    var coverage = state.recommendations.reduce(function (sum, r) { return sum + (r.ratio || 0); }, 0);
    var items = [
      [_t('finder.discovery_sum_unit'), state.unit || '-'],
      [_t('finder.discovery_sum_segments'), state.totalSegments || '-'],
      [_t('finder.discovery_sum_bestk'), state.bestTrial ? state.bestTrial.k : '-'],
      [_t('finder.discovery_sum_patterns'), state.recommendations.length || '-'],
      [_t('finder.discovery_sum_coverage'), coverage ? Math.min(100, coverage) + '%' : '-'],
    ];
    el.innerHTML = items.map(function (it) {
      return '<div><span>' + it[0] + '</span><strong>' + it[1] + '</strong></div>';
    }).join('');
  }

  function fmtTs(ts) {
    if (!ts) return '';
    return String(ts).replace('T', ' ').slice(0, 16);
  }

  function renderRecommendations() {
    var container = $('recommendedClusters');
    var placeholder = $('agentResultPlaceholder');
    var count = $('agentResultCount');
    if (!container) return;
    if (!state.recommendations.length) {
      if (placeholder) {
        placeholder.hidden = false;
        placeholder.textContent = _t('finder.discovery_no_portfolio');
      }
      container.innerHTML = '';
      return;
    }
    if (placeholder) placeholder.hidden = true;
    // Always show the cards when a fresh portfolio renders (un-collapse).
    var ppExpand = $('portfolioPanel');
    if (ppExpand) ppExpand.classList.remove('collapsed');
    if ($('btnMatrix')) $('btnMatrix').hidden = !hasProfiles();
    if (count) {
      count.textContent = _t('finder.discovery_pattern_count', state.recommendations.length);
      count.hidden = false;
    }
    container.innerHTML = state.recommendations.map(function (rec, pos) {
      var sample = rec.cluster && rec.cluster.segments && rec.cluster.segments[0];
      var range = sample && sample.start_ts ? fmtTs(sample.start_ts) + ' - ' + fmtTs(sample.end_ts) : '';
      return '<article class="discovery-card' + (pos === 0 ? ' is-selected' : '') + '" data-rec="' + rec.idx + '">' +
        '<div class="discovery-card-top">' +
        '<div class="discovery-card-actions">' +
        '<button type="button" class="discovery-card-save" data-rec="' + rec.idx + '">' + escapeHtml(_t('finder.discovery_save_preset')) + '</button>' +
        '<button type="button" class="discovery-card-basket" data-rec="' + rec.idx + '">' + escapeHtml(_t('finder.discovery_add_basket')) + '</button>' +
        '<button type="button" class="discovery-card-tofinder" data-rec="' + rec.idx + '">' + escapeHtml(_t('finder.discovery_search_in_finder')) + '</button>' +
        '</div>' +
        '<span>' + rec.score + '</span>' +
        '</div>' +
        '<input class="discovery-card-label" data-rec="' + rec.idx + '" value="' + escapeHtml(rec.label || rec.shape) + '">' +
        '<div class="discovery-card-chart" id="discoveryCardChart_' + rec.idx + '"></div>' +
        '<div class="discovery-card-meta">' +
        '<span>' + escapeHtml(_t('finder.discovery_card_coverage', rec.ratio)) + '</span>' +
        '<span>' + escapeHtml(_t('finder.discovery_card_segments', rec.count)) + '</span>' +
        (range ? '<span>' + escapeHtml(range) + '</span>' : '') +
        '</div>' +
        '<p>' + escapeHtml(rec.summary || '') + '</p>' +
        '</article>';
    }).join('');

    // Wire interactions FIRST — a failing chart draw must never leave the cards
    // unclickable (which previously broke collapse + selection entirely).
    $$('.discovery-card').forEach(function (card) {
      card.addEventListener('click', function (event) {
        var tag = event.target && event.target.tagName;
        if (tag === 'INPUT' || tag === 'LABEL' || tag === 'BUTTON') return;
        // Selecting a card must not make the other cards disappear: folding the
        // whole portfolio (and scrolling away) on every pick reads as the UI
        // vanishing. The inspector updates in place; collapsing stays manual
        // through the portfolio header.
        selectRecommendation(parseInt(card.dataset.rec, 10));
      });
    });
    $$('.discovery-card-save').forEach(function (btn) {
      btn.addEventListener('click', function (event) {
        event.stopPropagation();
        savePresetForRec(findRec(parseInt(btn.dataset.rec, 10)), btn);
      });
    });
    $$('.discovery-card-tofinder').forEach(function (btn) {
      btn.addEventListener('click', function (event) {
        event.stopPropagation();
        sendPatternToFinder(findRec(parseInt(btn.dataset.rec, 10)), btn);
      });
    });
    $$('.discovery-card-basket').forEach(function (btn) {
      btn.addEventListener('click', function (event) {
        event.stopPropagation();
        addPatternToBasket(findRec(parseInt(btn.dataset.rec, 10)), btn);
      });
    });
    $$('.discovery-card-label').forEach(function (input) {
      input.addEventListener('input', function () {
        var rec = findRec(parseInt(input.dataset.rec, 10));
        if (rec) rec.label = input.value.trim() || rec.shape;
        if (state.selectedIdx === rec.idx && $('inspectorLabel')) $('inspectorLabel').value = rec.label;
      });
    });

    // Charts are best-effort: guard each so one bad chart can't abort the rest.
    state.recommendations.forEach(function (rec) {
      try { drawMiniChart('discoveryCardChart_' + rec.idx, rec); }
      catch (e) { console.error('[Discovery] card chart failed for', rec.idx, e); }
    });
    try { selectRecommendation(state.recommendations[0].idx); }
    catch (e) { console.error('[Discovery] initial select failed', e); }
  }

  // z-normalize a series so charts show SHAPE, not absolute level. The mini-charts
  // must match the clustering space: z-normalized when cfg.normalize is on, raw
  // values when off (raw keeps level/bias visible, e.g. for error-series regimes).
  function zNorm(arr) {
    if (!arr || arr.length < 2) return arr || [];
    var n = arr.length, mean = 0, i;
    for (i = 0; i < n; i++) mean += arr[i];
    mean /= n;
    var v = 0;
    for (i = 0; i < n; i++) { var d = arr[i] - mean; v += d * d; }
    var sd = Math.sqrt(v / n) || 1;
    var out = new Array(n);
    for (i = 0; i < n; i++) out[i] = (arr[i] - mean) / sd;
    return out;
  }

  // Shared y-range for the mini-charts. z-normalized data always sits within ±3,
  // but raw values (normalization off) have no such bound — on the fixed ±3 axis
  // an error series of -8..+2 falls outside the window and the centroid line is
  // simply not drawn. Derive one range from every centroid on screen so the cards
  // stay comparable and every centroid is visible.
  function chartYRange() {
    if (cfg.normalize) return [-3, 3];
    var lo = Infinity, hi = -Infinity;
    (state.recommendations || []).forEach(function (r) {
      (r.centroid || []).forEach(function (v) {
        if (typeof v === 'number' && isFinite(v)) {
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      });
    });
    if (!isFinite(lo) || !isFinite(hi)) return [-3, 3];
    var pad = (hi - lo) * 0.18 || Math.abs(hi || 1) * 0.2 || 1;
    return [lo - pad, hi + pad];
  }

  function drawMiniChart(id, rec, highlightSeg) {
    var el = $(id);
    if (!el || !window.Plotly) return;
    var traces = [];
    var segs = rec.cluster && rec.cluster.segments ? rec.cluster.segments : [];
    // When a specific member is highlighted, show only that segment (bold) over
    // the faint centroid; otherwise show up to 3 sample segments + centroid.
    var hasHighlight = typeof highlightSeg === 'number' && segs[highlightSeg];
    var sampled = hasHighlight ? [segs[highlightSeg]] : segs.slice(0, 3);
    sampled.forEach(function (seg) {
      if (!seg.values || !seg.values.length) return;
      traces.push({
        y: cfg.normalize ? zNorm(seg.values) : seg.values,
        type: 'scatter',
        mode: 'lines',
        line: {
          color: hasHighlight ? '#DC2626' : 'rgba(13,148,136,.22)',
          width: hasHighlight ? 2 : 1,
        },
        hoverinfo: 'skip',
      });
    });
    traces.push({
      y: cfg.normalize ? zNorm(rec.centroid || []) : (rec.centroid || []),
      type: 'scatter',
      mode: 'lines',
      line: {
        color: hasHighlight ? 'rgba(15,118,110,.35)' : '#0F766E',
        width: hasHighlight ? 1.5 : 2.5,
      },
      hoverinfo: 'skip',
    });
    // Shared y-range so every card uses the full height on the same scale and
    // patterns stay directly comparable (see chartYRange).
    var yRange = chartYRange();
    if (hasHighlight) {
      // Keep the highlighted member on screen even if it runs past the centroid
      // band; without this the red line disappears on outlier segments.
      var hv = (segs[highlightSeg] || {}).values || [];
      var vals = cfg.normalize ? zNorm(hv) : hv;
      vals.forEach(function (v) {
        if (typeof v === 'number' && isFinite(v)) {
          if (v < yRange[0]) yRange[0] = v;
          if (v > yRange[1]) yRange[1] = v;
        }
      });
    }
    Plotly.newPlot(el, traces, {
      margin: { l: 8, r: 8, t: 4, b: 4 },
      height: el.classList.contains('discovery-inspector-chart') ? 180 : 110,
      showlegend: false,
      xaxis: { visible: false },
      yaxis: { visible: false, range: yRange, fixedrange: true },
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'transparent',
    }, { displayModeBar: false, responsive: true });
  }

  function findRec(idx) {
    return state.recommendations.find(function (r) { return r.idx === idx; });
  }

  function selectRecommendation(idx) {
    var rec = findRec(idx);
    if (!rec) return;
    state.selectedIdx = idx;
    $$('.discovery-card').forEach(function (card) {
      card.classList.toggle('is-selected', parseInt(card.dataset.rec, 10) === idx);
    });
    if ($('inspectorEmpty')) $('inspectorEmpty').hidden = true;
    if ($('inspectorBody')) $('inspectorBody').hidden = false;
    if ($('inspectorLabel')) $('inspectorLabel').value = rec.label || rec.shape;
    if ($('inspectorMeta')) {
      $('inspectorMeta').innerHTML =
        '<span>score ' + rec.score + '</span>' +
        '<span>' + rec.ratio + '% coverage</span>' +
        '<span>' + rec.confidence + '% confidence</span>';
    }
    try { drawMiniChart('inspectorChart', rec); } catch (e) { console.error('[Discovery] inspector chart failed', e); }
    try { renderMembers(rec); } catch (e) { console.error('[Discovery] member render failed', e); }
    try { renderColumnProfiles(rec); } catch (e) { console.error('[Discovery] column profiles failed', e); }
  }

  // Lightweight inline SVG sparkline so we can render many member thumbnails
  // cheaply (Plotly per member would be far too heavy for hundreds of segments).
  // Min-max scaled within its own box → shows shape regardless of level.
  function sparklineSvg(values, w, h) {
    var v = values || [];
    if (v.length < 2) return '';
    var min = Math.min.apply(null, v), max = Math.max.apply(null, v);
    var range = (max - min) || 1;
    var n = v.length, pad = 2, iw = w - pad * 2, ih = h - pad * 2;
    var pts = [];
    for (var i = 0; i < n; i++) {
      var x = pad + (i / (n - 1)) * iw;
      var y = pad + (1 - (v[i] - min) / range) * ih;
      pts.push(x.toFixed(1) + ',' + y.toFixed(1));
    }
    return '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" ' +
      'width="100%" height="100%">' +
      '<polyline points="' + pts.join(' ') + '" fill="none" stroke="#0F766E" ' +
      'stroke-width="1.2" vector-effect="non-scaling-stroke" ' +
      'stroke-linejoin="round" stroke-linecap="round"/></svg>';
  }

  // Show the cluster's member segments as a compact grid of shape thumbnails
  // (many per row). Clicking a thumbnail highlights that segment on the big
  // inspector chart. Capped for DOM size; segments arrive sorted best-first.
  var _MEMBER_THUMB_CAP = 120;
  function renderMembers(rec, limit) {
    var list = $('inspectorMembers');
    var countEl = $('inspectorMemberCount');
    var titleEl = $('inspectorMembersTitle');
    var segs = (rec.cluster && rec.cluster.segments) ? rec.cluster.segments : [];
    if (titleEl) titleEl.textContent = _t('finder.discovery_members_of', (rec.label || rec.shape || ''));
    if (countEl) countEl.textContent = _t('finder.discovery_seg_count', segs.length);
    if (!list) return;
    if (!segs.length) {
      list.innerHTML = '<div class="discovery-muted">' + escapeHtml(_t('finder.discovery_no_member_info')) + '</div>';
      return;
    }
    var cap = limit || _MEMBER_THUMB_CAP;
    var shown = segs.slice(0, cap);
    var html = shown.map(function (seg, i) {
      var range = seg.start_ts
        ? (fmtTs(seg.start_ts) + (seg.end_ts ? ' → ' + fmtTs(seg.end_ts) : ''))
        : (seg.label || ('segment ' + (i + 1)));
      return '<button type="button" class="discovery-member" data-seg="' + i +
        '" title="' + escapeHtml(range) + '">' +
        '<span class="discovery-member-spark">' + sparklineSvg(seg.values, 60, 30) + '</span>' +
        '<span class="discovery-member-idx">' + (i + 1) + '</span>' +
        '</button>';
    }).join('');
    if (segs.length > cap) {
      html += '<button type="button" class="discovery-member-more" data-next="' + (cap + _MEMBER_THUMB_CAP) + '">' +
        escapeHtml(_t('finder.discovery_more_members', (segs.length - cap))) + '</button>';
    }
    list.innerHTML = html;
    $$('#inspectorMembers .discovery-member').forEach(function (row) {
      row.addEventListener('click', function () {
        var si = parseInt(row.dataset.seg, 10);
        $$('#inspectorMembers .discovery-member').forEach(function (r) {
          r.classList.toggle('is-active', r === row);
        });
        drawMiniChart('inspectorChart', rec, si);
      });
    });
    var moreBtn = list.querySelector('.discovery-member-more[data-next]');
    if (moreBtn) moreBtn.addEventListener('click', function () {
      renderMembers(rec, parseInt(moreBtn.dataset.next, 10));
    });
  }

  // Consistency badge: how tightly a column's shape holds across the cluster's
  // segments (avg cosine similarity to the mean shape).
  function consBadge(cons) {
    if (cons == null) return { label: '', color: '#6b7280' };
    if (cons >= 0.85) return { label: _t('finder.discovery_cons_strong'), color: '#059669' };
    if (cons >= 0.70) return { label: _t('finder.discovery_cons_moderate'), color: '#7c3aed' };
    if (cons >= 0.55) return { label: _t('finder.discovery_cons_weak'), color: '#d97706' };
    return { label: _t('finder.discovery_cons_noisy'), color: '#6b7280' };
  }

  function discMeta(level) {
    if (level === 'high') return { label: _t('finder.discovery_disc_high'), color: '#059669' };
    if (level === 'medium') return { label: _t('finder.discovery_disc_medium'), color: '#7c3aed' };
    return { label: _t('finder.discovery_disc_low'), color: '#9ca3af' };
  }

  // "Columns" tab: for the selected pattern, show the average shape of each
  // profiled column over this pattern's segments, with consistency, plus a
  // global note on which column best separates the patterns.
  function renderColumnProfiles(rec) {
    var insightEl = $('inspectorColInsight');
    var listEl = $('inspectorColProfiles');
    if (!listEl) return;
    var profiles = rec.profiles || (state.columnProfiles && rec.cluster ? state.columnProfiles[rec.cluster.key] : null);
    var cols = (state.profileCols || []).filter(Boolean);
    var avail = cols.filter(function (c) { return profiles && profiles[c]; });

    // Global insight: most discriminative column across all patterns.
    if (insightEl) {
      var disc = state.columnDiscriminative || {};
      var best = null, bestRank = -1;
      var rankOf = { high: 2, medium: 1, low: 0 };
      Object.keys(disc).forEach(function (c) {
        var r = rankOf[disc[c].level] != null ? rankOf[disc[c].level] : 0;
        if (r > bestRank) { bestRank = r; best = c; }
      });
      if (best && bestRank >= 1) {
        insightEl.innerHTML = '★ ' + _t('finder.discovery_col_insight', escapeHtml(best), discMeta(disc[best].level).label);
        insightEl.hidden = false;
      } else {
        insightEl.hidden = true;
      }
    }

    if (!avail.length) {
      listEl.innerHTML = '<div class="discovery-muted">' + escapeHtml(_t('finder.discovery_col_none')) + '</div>';
      return;
    }
    var disc2 = state.columnDiscriminative || {};
    listEl.innerHTML = avail.map(function (col) {
      var p = profiles[col];
      var cb = consBadge(p.consistency);
      var consPct = p.consistency != null ? Math.round(p.consistency * 100) : null;
      var dBadge = '';
      if (disc2[col]) {
        var dm = discMeta(disc2[col].level);
        dBadge = '<span class="discovery-disc-badge" style="color:' + dm.color +
          ';border-color:' + dm.color + '55;background:' + dm.color + '14;">' + escapeHtml(dm.label) + '</span>';
      }
      var consHtml = consPct != null
        ? '<span class="discovery-cons-badge" style="color:' + cb.color + ';">' +
          escapeHtml(cb.label) + ' · ' + escapeHtml(_t('finder.discovery_col_consistency', consPct)) + '</span>'
        : '';
      return '<div class="discovery-col-row">' +
        '<div class="discovery-col-row-head">' +
        '<span class="discovery-col-name" title="' + escapeHtml(col) + '">' + escapeHtml(col) + '</span>' +
        dBadge +
        '</div>' +
        '<div class="discovery-col-spark">' + sparklineSvg(p.shape, 130, 34) + '</div>' +
        '<div class="discovery-col-meta">' + consHtml +
        (p.n != null ? '<span class="discovery-col-n">n=' + p.n + '</span>' : '') +
        '</div>' +
        '</div>';
    }).join('');
  }

  // Full matrix view: all columns (rows) × all recommended patterns (columns),
  // each cell = that column's average shape for that pattern + consistency.
  // Lets the user compare every column across every pattern at a glance.
  function hasProfiles() {
    return state.columnProfiles && Object.keys(state.columnProfiles).length > 0;
  }

  function renderMatrix() {
    var body = $('matrixBody');
    if (!body) return;
    var recs = (state.recommendations || []).filter(function (r) {
      return r.cluster && state.columnProfiles && state.columnProfiles[r.cluster.key];
    });
    var cols = (state.profileCols || []);
    if (!recs.length || !cols.length || !hasProfiles()) {
      body.innerHTML = '<div class="discovery-muted">' + escapeHtml(_t('finder.discovery_matrix_none')) + '</div>';
      return;
    }
    var disc = state.columnDiscriminative || {};
    var head = '<thead><tr><th class="discovery-matrix-corner">' + escapeHtml(_t('finder.discovery_matrix_col')) + '</th>';
    recs.forEach(function (rec) {
      head += '<th title="' + escapeHtml(rec.label || rec.shape) + '">' + escapeHtml(rec.label || rec.shape) +
        '<span class="discovery-matrix-th-n">' + (rec.count || 0) + '</span></th>';
    });
    head += '</tr></thead>';

    var rows = cols.map(function (col) {
      var dBadge = '';
      if (disc[col]) {
        var dm = discMeta(disc[col].level);
        dBadge = ' <span class="discovery-disc-badge" style="color:' + dm.color + ';border-color:' + dm.color +
          '55;background:' + dm.color + '14;">' + escapeHtml(dm.label) + '</span>';
      }
      var primTag = '';
      var cells = recs.map(function (rec) {
        var p = state.columnProfiles[rec.cluster.key] && state.columnProfiles[rec.cluster.key][col];
        if (!p) return '<td class="discovery-matrix-cell is-empty">·</td>';
        var cb = consBadge(p.consistency);
        var consPct = p.consistency != null ? Math.round(p.consistency * 100) : null;
        var tip = col + ' · ' + (rec.label || rec.shape) + (consPct != null ? ' · ' + consPct + '%' : '') +
          (p.n != null ? ' · n=' + p.n : '');
        return '<td class="discovery-matrix-cell" style="background:' + cb.color + '12;" title="' + escapeHtml(tip) + '">' +
          '<div class="discovery-matrix-spark">' + sparklineSvg(p.shape, 96, 28) + '</div>' +
          (consPct != null ? '<span class="discovery-matrix-cons" style="color:' + cb.color + ';">' + consPct + '%</span>' : '') +
          '</td>';
      }).join('');
      return '<tr><td class="discovery-matrix-rowname">' + escapeHtml(col) + primTag + dBadge + '</td>' + cells + '</tr>';
    }).join('');

    body.innerHTML = '<table class="discovery-matrix">' + head + '<tbody>' + rows + '</tbody></table>';
  }

  function openMatrix() {
    renderMatrix();
    if ($('matrixModal')) $('matrixModal').hidden = false;
  }
  function closeMatrix() {
    if ($('matrixModal')) $('matrixModal').hidden = true;
  }

  function syncInspectorLabel() {
    var rec = findRec(state.selectedIdx);
    if (!rec || !$('inspectorLabel')) return;
    rec.label = $('inspectorLabel').value.trim() || rec.shape;
    var cardInput = document.querySelector('.discovery-card-label[data-rec="' + rec.idx + '"]');
    if (cardInput) cardInput.value = rec.label;
  }

  // Save a single recommendation as a custom preset. Used by the per-card
  // "프리셋 저장" buttons and the inspector save button.
  function savePresetForRec(rec, btn) {
    if (!rec) {
      alert(_t('finder.discovery_select_pattern_first'));
      return Promise.resolve();
    }
    syncInspectorLabel();
    var labelInput = document.querySelector('.discovery-card-label[data-rec="' + rec.idx + '"]');
    var name = (labelInput && labelInput.value.trim())
      ? labelInput.value.trim()
      : (rec.label || rec.shape || ('Pattern ' + rec.idx));
    var orig = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = _t('finder.discovery_saving'); }
    return fetchJson('/api/presets/custom', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        dataset_id: S.datasetKey,
        name: name,
        values: rec.centroid,
        source: 'discovery_studio',
        metadata: {
          from_discovery: true,
          cluster_idx: rec.idx,
          columns: S.selectedCols || [],
          unit: state.unit || '',
          score: rec.score,
          confidence: rec.confidence,
          coverage: rec.ratio,
        },
      }),
    }).then(function (data) {
      if (btn) { btn.disabled = false; btn.textContent = _t('finder.discovery_saved'); btn.classList.add('is-saved'); }
      return (data && data.key) || null;   // Finder 로 넘길 때 필요한 프리셋 키
    }).catch(function (e) {
      alert(_t('finder.discovery_save_failed', e.message));
      if (btn) { btn.disabled = false; btn.textContent = orig || _t('finder.discovery_save_preset'); }
    });
  }

  /* 군집 대표 파형을 그대로 Finder 의 질의로 넘긴다.
     프리셋으로 저장 → 데이터셋·컬럼·세그먼트 단위·프리셋 키를 sessionStorage 에
     실어 보내면, Finder 쪽 restoreFinderSelection() 이 이어받아
     세그먼트 뷰 + 모양 사전 모드까지 세팅한다. 이 경로가 없으면
     "군집 대표를 다시 질의로 쓴다"는 순환이 화면에서 끊긴다. */
  function sendPatternToFinder(rec, btn) {
    if (!rec) { alert(_t('finder.discovery_select_pattern_first')); return; }
    savePresetForRec(rec, btn).then(function (key) {
      if (!key) { alert(_t('finder.discovery_handoff_failed')); return; }
      try {
        sessionStorage.setItem('finder_restore', JSON.stringify({
          datasetKey: S.datasetKey,
          primaryCol: primaryCol(),
          overlayCols: siblingCols(),
          preset: key,
          unit: state.unit || null,
        }));
      } catch (e) {}
      window.location.href = '/finder';
    });
  }

  function saveSelectedPatterns() {
    savePresetForRec(findRec(state.selectedIdx), $('btnSaveRecommended'));
  }

  // Refresh the nav basket badge (base.html exposes the count fallback; we just
  // re-query and update the element directly so Discovery stays in sync).
  function updateBasketBadge() {
    var b = document.getElementById('basketBadge');
    if (!b) return;
    fetch('/api/basket/count').then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.draft > 0) { b.textContent = d.draft; b.style.display = 'inline-flex'; }
      else { b.style.display = 'none'; }
    }).catch(function () {});
  }

  // Add every member segment of a pattern to the label basket (per-pattern bulk
  // add, mirroring the Finder cluster "Add All").
  function addPatternToBasket(rec, btn) {
    var segs = (rec && rec.cluster && rec.cluster.segments) ? rec.cluster.segments : [];
    var items = segs.map(function (seg) {
      return {
        dataset_key: S.datasetKey,
        source_key: seg.source_key || '',
        col_name: primaryCol(),
        start_ts: seg.start_ts,
        end_ts: seg.end_ts,
        point_count: seg.point_count || (seg.values ? seg.values.length : 0),
        context: 'discovery:' + (rec.label || rec.shape || ('pattern_' + rec.idx)),
        values: seg.values,
      };
    }).filter(function (it) { return it.start_ts && it.end_ts; });
    if (!items.length) { alert(_t('finder.discovery_no_segments')); return Promise.resolve(); }
    var orig = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = _t('finder.discovery_adding'); }
    return fetchJson('/api/basket', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: items }),
    }).then(function (res) {
      var n = ((res && res.count) || 0) + ((res && res.updated) || 0);
      if (btn) { btn.disabled = false; btn.textContent = _t('finder.discovery_added', n); btn.classList.add('is-added'); }
      updateBasketBadge();
    }).catch(function (e) {
      alert(_t('finder.discovery_basket_failed', e.message));
      if (btn) { btn.disabled = false; btn.textContent = orig || _t('finder.discovery_add_basket'); }
    });
  }

  // HIL Action: Find more segments similar to selected pattern's centroid
  async function hilFindMore() {
    var rec = findRec(state.selectedIdx);
    if (!rec || !rec.centroid || !rec.centroid.length) {
      alert(_t('finder.discovery_select_pattern_first'));
      return;
    }
    setProgress(10, _t('finder.discovery_finding_more'));
    try {
      var result = await fetchJson('/api/segment_search/similar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dataset_key: S.datasetKey,
          col_name: primaryCol(),
          unit: state.unit,
          query_values: rec.centroid,
          top_k: DEFAULT_TOP_K,
        }),
      });
      state.hilHistory.push({ action: 'find_more', pattern: rec.label, results: result.segments ? result.segments.length : 0 });
      if (result.segments && result.segments.length) {
        rec.cluster.segments = result.segments;
        rec.count = result.segments.length;
        rec.ratio = state.totalSegments ? Math.round(result.segments.length / state.totalSegments * 100) : rec.ratio;
        renderRecommendations();
        selectRecommendation(rec.idx);
        alert(_t('finder.discovery_found_more', result.segments.length));
      } else {
        alert(_t('finder.discovery_found_none'));
      }
    } catch (e) {
      alert(_t('finder.discovery_find_failed', e.message));
    } finally {
      setProgress(100, _t('finder.discovery_status_done'));
    }
  }

  // HIL Action: Subdivide selected pattern into finer clusters
  async function hilSubdivide() {
    var rec = findRec(state.selectedIdx);
    if (!rec || !rec.cluster || !rec.cluster.segments || rec.cluster.segments.length < 6) {
      alert(_t('finder.discovery_subdivide_min'));
      return;
    }
    setProgress(15, _t('finder.discovery_subdividing'));
    try {
      var segmentValues = rec.cluster.segments.map(function (s) { return s.values || []; });
      var result = await fetchJson('/api/segment_search/subdivide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dataset_key: S.datasetKey,
          col_name: primaryCol(),
          segments: rec.cluster.segments,
          k: Math.min(4, Math.max(2, Math.floor(rec.cluster.segments.length / 3))),
        }),
      });
      state.hilHistory.push({ action: 'subdivide', pattern: rec.label, sub_k: result.groups ? Object.keys(result.groups).length : 0 });
      var subClusters = toClusterArray(result);
      if (subClusters.length > 1) {
        var recIdx = state.recommendations.findIndex(function (r) { return r.idx === rec.idx; });
        var newRecs = subClusters.map(function (sc, i) {
          var fallback = analyzeShape(sc.centroid);
          return {
            idx: 100 + rec.idx * 10 + i,
            key: sc.key,
            centroid: sc.centroid,
            count: sc.count,
            ratio: state.totalSegments ? Math.round(sc.count / state.totalSegments * 100) : 0,
            shape: fallback.shape,
            label: rec.label + ' - sub' + (i + 1),
            summary: fallback.summary,
            confidence: fallback.confidence,
            meaningful: true,
            cluster: sc,
            score: Math.round(rec.score * 0.9),
          };
        });
        state.recommendations.splice(recIdx, 1, ...newRecs);
        renderRecommendations();
        selectRecommendation(newRecs[0].idx);
        alert(_t('finder.discovery_subdivided', newRecs.length));
      } else {
        alert(_t('finder.discovery_subdivide_none'));
      }
    } catch (e) {
      alert(_t('finder.discovery_subdivide_failed', e.message));
    } finally {
      setProgress(100, _t('finder.discovery_status_done'));
    }
  }

  // HIL Action: Exclude selected pattern and re-cluster remaining
  async function hilExclude() {
    var rec = findRec(state.selectedIdx);
    if (!rec || !rec.centroid) {
      alert(_t('finder.discovery_select_pattern_first'));
      return;
    }
    state.excludedCentroids.push(rec.centroid);
    state.hilHistory.push({ action: 'exclude', pattern: rec.label });
    var recIdx = state.recommendations.findIndex(function (r) { return r.idx === rec.idx; });
    state.recommendations.splice(recIdx, 1);
    if (state.recommendations.length === 0) {
      setProgress(10, _t('finder.discovery_reclustering'));
      try {
        var body = {
          dataset_key: S.datasetKey,
          col_name: primaryCol(),
          unit: state.unit,
          k: state.bestTrial ? state.bestTrial.k : cfg.kMin,
          method: 'shape',
          exclude_centroids: state.excludedCentroids,
        };
        var result = await fetchJson('/api/segment_search/cluster', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        state.clusters = toClusterArray(result);
        await interpretClusters();
        rankRecommendations();
        renderSummary();
        renderRecommendations();
        alert(_t('finder.discovery_reclustered', state.excludedCentroids.length));
      } catch (e) {
        alert(_t('finder.discovery_recluster_failed', e.message));
      } finally {
        setProgress(100, _t('finder.discovery_status_done'));
      }
    } else {
      renderRecommendations();
      if (state.recommendations.length) selectRecommendation(state.recommendations[0].idx);
      alert(_t('finder.discovery_excluded', state.recommendations.length));
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    var hasSelection = loadSelection();

    if (!hasSelection) {
      // No dataset/column chosen — hide the context line and the run UI,
      // warn the user, and steer them back to the Finder to make a selection.
      var ctx = $('discoveryContextBlock');
      if (ctx) ctx.hidden = true;
      hide('discoveryConfig');
      hide('discoverPanel');
      show('discoveryEmptyState');
      return;
    }

    renderSelection();
    show('discoveryConfig');
    hide('discoverPanel');

    if ($('btnDiscoverStart')) $('btnDiscoverStart').addEventListener('click', runDiscovery);
    if ($('btnAgentRetry')) $('btnAgentRetry').addEventListener('click', runDiscovery);
    if ($('btnDiscoverBack')) $('btnDiscoverBack').addEventListener('click', function () {
      if (S.datasetKey && primaryCol()) {
        try {
          sessionStorage.setItem('finder_restore', JSON.stringify({
            datasetKey: S.datasetKey,
            primaryCol: primaryCol(),
            overlayCols: siblingCols(),
          }));
        } catch (e) {}
      }
      window.location.href = '/finder';
    });
    if ($('btnSaveRecommended')) $('btnSaveRecommended').addEventListener('click', saveSelectedPatterns);
    if ($('btnAddToBasket')) $('btnAddToBasket').addEventListener('click', function () {
      addPatternToBasket(findRec(state.selectedIdx), $('btnAddToBasket'));
    });
    updateBasketBadge();
    if ($('inspectorLabel')) $('inspectorLabel').addEventListener('input', syncInspectorLabel);
    if ($('btnFindMore')) $('btnFindMore').addEventListener('click', hilFindMore);
    if ($('btnSubdivide')) $('btnSubdivide').addEventListener('click', hilSubdivide);
    if ($('btnExclude')) $('btnExclude').addEventListener('click', hilExclude);
    // Inspector sub-tabs (Pattern / Columns)
    $$('#inspectorTabs .discovery-tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        var key = tab.dataset.tab;
        $$('#inspectorTabs .discovery-tab').forEach(function (t) {
          t.classList.toggle('is-active', t === tab);
        });
        $$('.discovery-tab-panel').forEach(function (p) {
          p.hidden = (p.getAttribute('data-tabpanel') !== key);
        });
      });
    });
    // Collapsible Run Map / K Sweep panels
    $$('.discovery-collapse-toggle').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var panel = btn.closest('.discovery-collapsible');
        if (panel) panel.classList.toggle('collapsed');
        syncLeftWidth();
      });
    });
    // Full cross-column matrix modal
    if ($('btnMatrix')) $('btnMatrix').addEventListener('click', function (event) {
      event.stopPropagation(); // don't toggle the portfolio collapse
      openMatrix();
    });
    if ($('matrixModal')) $('matrixModal').addEventListener('click', function (event) {
      if (event.target && event.target.getAttribute('data-close')) closeMatrix();
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeMatrix();
    });

    // Pattern Portfolio collapses on pick; click its header to expand again.
    if ($('portfolioHead')) $('portfolioHead').addEventListener('click', function () {
      var pp = $('portfolioPanel');
      if (!pp) return;
      pp.classList.toggle('collapsed');
      // When collapsing, make sure a pattern is shown and scroll to the
      // inspector — otherwise the cards just vanish into an empty gap.
      if (pp.classList.contains('collapsed')) {
        if (state.selectedIdx == null && state.recommendations.length) {
          selectRecommendation(state.recommendations[0].idx);
        }
        focusInspector();
      }
    });
  });
})();
