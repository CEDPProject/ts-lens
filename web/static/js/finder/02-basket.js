// 02-basket.js — 라벨 바스켓 — 담기 버튼 · 저장소 · 하단 패널
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

var BasketManager = {
  _keySet: new Set(),
  _draftCount: 0,
  _confirmedCount: 0,
  _initialized: false,
  _listeners: [],

  _makeKey: function(info) {
    return (info.source_key || '') + '|' + (info.start_ts || '') + '|' + (info.end_ts || '') + '|' + (info.col_name || '');
  },

  _notify: function() {
    var counts = { draft: this._draftCount, confirmed: this._confirmedCount };
    this._listeners.forEach(function(cb) { try { cb(counts); } catch(e) {} });
  },

  _syncCount: function() {
    var self = this;
    return Promise.all([
      fetch('/api/basket/count', { cache: 'no-store' }).then(function(r) { return r.json(); }),
      fetch('/api/basket/keys', { cache: 'no-store' }).then(function(r) { return r.json(); }),
    ]).then(function(results) {
      self._draftCount = results[0].draft || 0;
      self._confirmedCount = results[0].confirmed || 0;
      self._keySet = new Set(results[1].keys || []);
      console.log('[_syncCount] draft:', self._draftCount, 'confirmed:', self._confirmedCount, 'keySet size:', self._keySet.size);
      if (self._keySet.size > 0) {
        console.log('[_syncCount] first key from DB:', Array.from(self._keySet)[0]);
      }
      self._notify();
    }).catch(function(err) {
      console.log('[_syncCount] error:', err);
      self._notify();
    });
  },

  onChange: function(cb) { this._listeners.push(cb); },

  has: function(info) {
    return this._keySet.has(this._makeKey(info));
  },

  init: function() {
    var self = this;
    var localItems = [];
    try { localItems = JSON.parse(localStorage.getItem('basket_items') || '[]'); } catch(e) {}
    var migrateThen = localItems.length > 0
      ? fetch('/api/basket', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items: localItems }),
        }).then(function() { localStorage.removeItem('basket_items'); }).catch(function() {})
      : Promise.resolve();

    return migrateThen.then(function() {
      return Promise.all([
        fetch('/api/basket/keys', { cache: 'no-store' }).then(function(r) { return r.json(); }),
        fetch('/api/basket/count', { cache: 'no-store' }).then(function(r) { return r.json(); }),
      ]);
    }).then(function(results) {
      self._keySet = new Set(results[0].keys || []);
      self._draftCount = results[1].draft || 0;
      self._confirmedCount = results[1].confirmed || 0;
      self._initialized = true;
      self._notify();
    }).catch(function() { self._initialized = true; });
  },

  add: function(info) {
    var self = this;
    var key = this._makeKey(info);
    if (this._keySet.has(key)) return Promise.resolve({ count: 0, skipped: 1 });
    var item = {
      dataset_key: info.dataset_key || S.datasetKey || '',
      source_key: info.source_key || '',
      col_name: info.col_name || S.primaryCol || '',
      start_ts: info.start_ts || '',
      end_ts: info.end_ts || '',
      point_count: info.point_count || 0,
      context: info.context || '',
      similarity: info.similarity != null ? info.similarity : null,
      distance: info.distance != null ? info.distance : null,
      search_method: info.search_method || S.method || '',
      normalize: info.normalize != null ? info.normalize : null,
      columns: info.columns || [],
      series: info.series || [],
    };
    if (info.values && info.values.length) item.values = info.values;
    return fetch('/api/basket', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: [item] }),
    }).then(function(r) { return r.json(); })
    .then(function(res) {
      return self._syncCount().then(function() { return res; });
    });
  },

  addBulk: function(items) {
    var self = this;
    var filtered = [];
    items.forEach(function(info) {
      var key = self._makeKey(info);
      if (self._keySet.has(key)) return;
      var bkItem = {
        dataset_key: info.dataset_key || S.datasetKey || '',
        source_key: info.source_key || '',
        col_name: info.col_name || S.primaryCol || '',
        start_ts: info.start_ts || '',
        end_ts: info.end_ts || '',
        point_count: info.point_count || 0,
        context: info.context || '',
        similarity: info.similarity != null ? info.similarity : null,
        distance: info.distance != null ? info.distance : null,
        search_method: info.search_method || S.method || '',
        normalize: info.normalize != null ? info.normalize : null,
        columns: info.columns || [],
        series: info.series || [],
      };
      if (info.values && info.values.length) bkItem.values = info.values;
      filtered.push(bkItem);
    });
    console.log('[addBulk] items:', items.length, 'filtered:', filtered.length);
    if (filtered.length > 0) {
      console.log('[addBulk] first filtered item source_key:', filtered[0].source_key, 'start_ts:', filtered[0].start_ts, 'end_ts:', filtered[0].end_ts, 'col_name:', filtered[0].col_name);
    }
    if (filtered.length === 0) return Promise.resolve({ count: 0, skipped: items.length });
    return fetch('/api/basket', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: filtered }),
    }).then(function(r) { return r.json(); })
    .then(function(res) {
      console.log('[addBulk] POST response:', res);
      return self._syncCount().then(function() { return res; });
    });
  },

  removeBulk: function(keys) {
    var self = this;
    var toRemove = keys.filter(function(k) { return self._keySet.has(k); });
    console.log('[removeBulk] keys:', keys.length, 'toRemove:', toRemove.length);
    if (toRemove.length > 0) {
      console.log('[removeBulk] first key to remove:', toRemove[0]);
    }
    if (toRemove.length === 0) return Promise.resolve({ deleted: 0 });
    return fetch('/api/basket/bulk', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keys: toRemove }),
    }).then(function(r) { return r.json(); })
    .then(function(res) {
      console.log('[removeBulk] DELETE response:', res);
      return self._syncCount().then(function() { return res; });
    });
  },

  removeById: function(id, key) {
    var self = this;
    return fetch('/api/basket/' + id, { method: 'DELETE' })
      .then(function(r) { return r.json(); })
      .then(function(res) {
        return self._syncCount().then(function() { return res; });
      });
  },
};

// Helper: create basket info with correct key format
// Must match the key format in DB: source_key|start_ts|end_ts|col_name
function _makeBkInfo(seg, colName, context) {
  var timestamps = seg.timestamps || seg.labels || [];
  var explicitStart = seg.start_ts || seg.start || (timestamps.length ? timestamps[0] : '');
  var explicitEnd = seg.end_ts || seg.end || (timestamps.length ? timestamps[timestamps.length - 1] : '');
  var hasRealStartTs = explicitStart !== '' && explicitStart != null;
  var segLabel = seg.source_key || seg.key || seg.label || '';

  // 구간이 실제로 나온 소스를 먼저 쓴다. 관측소별로 소스가 갈리는 데이터셋에서
  // S.sourceKey 는 비어 있어 datasetKey 로 떨어지는데, 그러면 소스를 특정할 수 없어
  // 나중에 구간 원본을 못 찾는다.
  var sourceKey = seg.source_key || S.sourceKey || S.datasetKey || '';

  // For start_ts: use seg.start_ts if available, otherwise use segLabel
  var startTs = hasRealStartTs ? String(explicitStart) : segLabel;

  // For end_ts: use seg.end_ts if available, otherwise compute from segment length
  var endTs = '';
  if (hasRealStartTs && explicitEnd !== '' && explicitEnd != null) {
    endTs = String(explicitEnd);
  } else if (seg.values && seg.values.length > 0 && startTs) {
    // Compute end_ts: start + (points - 1) * interval
    // Assume hourly data (60 min interval) as default
    var intervalMin = (typeof SS !== 'undefined' && SS.samplingMinutes) || S.samplingMinutes || 60;
    var durationMin = (seg.values.length - 1) * intervalMin;
    try {
      var startDate = new Date(startTs.replace(' ', 'T'));
      if (isNaN(startDate.getTime())) throw new Error('non-date segment key');
      var endDate = new Date(startDate.getTime() + durationMin * 60000);
      var pad = function(n) { return n < 10 ? '0' + n : n; };
      endTs = endDate.getFullYear() + '-' + pad(endDate.getMonth() + 1) + '-' + pad(endDate.getDate()) + ' ' +
              pad(endDate.getHours()) + ':' + pad(endDate.getMinutes()) + ':' + pad(endDate.getSeconds());
    } catch(e) {
      endTs = startTs; // fallback
    }
  } else {
    endTs = startTs; // fallback
  }

  var info = {
    source_key: sourceKey,
    col_name: colName || S.primaryCol || '',
    start_ts: startTs,
    end_ts: endTs,
    point_count: seg.values ? seg.values.length : 0,
    context: context || '',
    values: seg.values || [],
    similarity: seg.similarity != null ? seg.similarity : null,
    distance: seg.distance != null ? seg.distance : null,
    search_method: seg.search_method || S.method || '',
    normalize: seg.normalize != null ? seg.normalize : null,
  };
  var allCols = [info.col_name];
  info.series = [{ col_name: info.col_name, values: seg.values || [], timestamps: timestamps }];
  (seg.overlays || []).forEach(function(ov, idx) {
    var overlayName = ov.col_name || ov.name || (S.overlayCols || [])[idx] || ('column_' + (idx + 2));
    allCols.push(overlayName);
    info.series.push({
      col_name: overlayName,
      values: ov.values || [],
      timestamps: ov.timestamps || ov.labels || timestamps,
    });
  });
  info.columns = allCols;
  return info;
}

// Attach the exact single/multi-column waveform snapshot used by a search
// result. This makes Basket independent of whether the source can be queried
// again later (uploaded, generated, and inline segments included).
function _withBkSeries(info, primaryValues, overlayValues, overlayNames, timestamps) {
  var names = [info.col_name || S.primaryCol || ''].concat(overlayNames || []);
  var series = [{
    col_name: names[0], values: primaryValues || [], timestamps: timestamps || [],
  }];
  (overlayValues || []).forEach(function(values, idx) {
    series.push({
      col_name: names[idx + 1] || ('column_' + (idx + 2)),
      values: values || [], timestamps: timestamps || [],
    });
  });
  info.columns = names;
  info.series = series;
  return info;
}

function _bkBtnHtml(info) {
  var key = BasketManager._makeKey(info);
  var inBk = BasketManager._keySet.has(key);
  return '<button class="bk-add-btn' + (inBk ? ' bk-added' : '') + '" data-bk-key="'
    + key.replace(/"/g, '&quot;') + '" title="' + (inBk ? 'Remove from Basket' : 'Add to Basket') + '"'
    + '>' + (inBk ? '\u2713' : '+') + '</button>';
}
function _bkWireBtn(btn, info) {
  btn.addEventListener('click', function(e) {
    e.stopPropagation();
    var key = BasketManager._makeKey(info);
    var isInBasket = btn.classList.contains('bk-added') || BasketManager._keySet.has(key);
    btn.textContent = '...';
    btn.disabled = true;

    if (isInBasket) {
      // Remove from basket
      BasketManager.removeBulk([key]).then(function() {
        btn.textContent = '+';
        btn.classList.remove('bk-added');
        btn.disabled = false;
        var card = btn.closest('.seg-card, .cls-seg-card, .ssr-card, .clu-seg-card');
        if (card) card.classList.remove('in-basket');
      }).catch(function() {
        btn.textContent = '\u2713';
        btn.disabled = false;
      });
    } else {
      // Add to basket
      BasketManager.add(info).then(function() {
        btn.textContent = '\u2713';
        btn.classList.add('bk-added');
        btn.disabled = false;
        var card = btn.closest('.seg-card, .cls-seg-card, .ssr-card, .clu-seg-card');
        if (card) card.classList.add('in-basket');
      }).catch(function() {
        btn.textContent = '+';
        btn.disabled = false;
      });
    }
  });
}

function _bkAddAllBtnSetup(btn, items, opts) {
  opts = opts || {};
  var keys = items.map(function(it) { return BasketManager._makeKey(it); });
  console.log('[_bkAddAllBtnSetup] items.length:', items.length, 'keys[0]:', keys[0]);
  btn.dataset.bkGroupKeys = JSON.stringify(keys);
  btn._bkItems = items;
  btn._bkShowCount = !!opts.showCount;
  btn._bkCount = items.length;
  _bkAddAllBtnSync(btn, keys);

  // Remove old listener if exists
  if (btn._bkClickHandler) {
    btn.removeEventListener('click', btn._bkClickHandler);
  }

  btn._bkClickHandler = function(e) {
    e.stopPropagation();
    var allIn = keys.every(function(k) { return BasketManager._keySet.has(k); });
    console.log('[_bkClickHandler] clicked! allIn:', allIn, 'keys.length:', keys.length);
    console.log('[_bkClickHandler] _keySet size:', BasketManager._keySet.size, 'first key in set:', Array.from(BasketManager._keySet)[0]);
    btn.textContent = '...';
    btn.disabled = true;
    if (allIn) {
      console.log('[_bkClickHandler] calling removeBulk');
      BasketManager.removeBulk(keys).then(function(res) {
        console.log('[_bkClickHandler] removeBulk done, res:', res);
        console.log('[_bkClickHandler] _keySet size after:', BasketManager._keySet.size);
        _bkAddAllBtnSync(btn, keys);
      }).catch(function(err) {
        console.log('[_bkClickHandler] removeBulk error:', err);
        _bkAddAllBtnSync(btn, keys);
      });
    } else {
      console.log('[_bkClickHandler] calling addBulk');
      BasketManager.addBulk(items).then(function(res) {
        console.log('[_bkClickHandler] addBulk done, res:', res);
        console.log('[_bkClickHandler] _keySet size after:', BasketManager._keySet.size);
        _bkAddAllBtnSync(btn, keys);
      }).catch(function(err) {
        console.log('[_bkClickHandler] addBulk error:', err);
        _bkAddAllBtnSync(btn, keys);
      });
    }
  };
  btn.addEventListener('click', btn._bkClickHandler);
}

function _bkAddAllBtnSync(btn, keys) {
  var allIn = keys.length > 0 && keys.every(function(k) { return BasketManager._keySet.has(k); });
  console.log('[_bkAddAllBtnSync] allIn:', allIn, 'keys.length:', keys.length);
  if (!allIn && keys.length > 0) {
    var missing = keys.filter(function(k) { return !BasketManager._keySet.has(k); });
    console.log('[_bkAddAllBtnSync] missing keys count:', missing.length, 'first missing:', missing[0]);
    if (BasketManager._keySet.size > 0) {
      var dbKeys = Array.from(BasketManager._keySet);
      var similar = dbKeys.filter(function(dk) { return dk.indexOf(missing[0].split('|')[1]) >= 0; });
      if (similar.length > 0) console.log('[_bkAddAllBtnSync] similar DB key:', similar[0]);
    }
  }
  btn.disabled = false;
  if (allIn) {
    btn.textContent = btn._bkShowCount ? '- Remove All (' + btn._bkCount + ')' : '- All';
    btn.classList.add('bk-removeall');
    btn.classList.remove('bk-added');
  } else {
    btn.textContent = btn._bkShowCount ? '+ Add All (' + btn._bkCount + ')' : '+ All';
    btn.classList.remove('bk-removeall', 'bk-added');
  }
}

/* ── Basket Slide-out Panel ─────────────────────────────────── */
var BasketPanel = {
  _open: false,
  _el: null,
  init: function() {
    var self = this;
    self._el = document.getElementById('basketPanel');
    if (!self._el) return;
    var closeBtn = document.getElementById('bpClose');
    if (closeBtn) closeBtn.addEventListener('click', function() { self.close(); });

    var badge = document.getElementById('basketBadge');
    if (badge) {
      badge.style.cursor = 'pointer';
      badge.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        self.toggle();
      });
    }

    self._el.addEventListener('click', function(e) { e.stopPropagation(); });

    BasketManager.onChange(function(counts) {
      var dc = self._el.querySelector('#bpDraftCount');
      var cc = self._el.querySelector('#bpConfirmedCount');
      if (dc) dc.textContent = counts.draft;
      if (cc) cc.textContent = counts.confirmed;
      if (self._open) self._renderItems();
    });
  },
  toggle: function() { this._open ? this.close() : this.open(); },
  open: function() {
    if (!this._el) return;
    this._open = true;
    this._el.hidden = false;
    var self = this;
    requestAnimationFrame(function() {
      requestAnimationFrame(function() { self._el.classList.add('open'); });
    });
    this._renderItems();
    BasketManager._syncCount();
  },
  close: function() {
    if (!this._el) return;
    this._open = false;
    this._el.classList.remove('open');
    var self = this;
    setTimeout(function() { if (!self._open) self._el.hidden = true; }, 280);
  },
  _renderItems: function() {
    var body = document.getElementById('bpBody');
    if (!body) return;
    body.innerHTML = '<div style="text-align:center;padding:20px;color:var(--muted);">' + _t('js.loading') + '</div>';
    fetch('/api/basket?status=draft')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var items = Array.isArray(data) ? data : (data.items || []);
        if (items.length === 0) {
          body.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted);">' + _t('js.no_draft') + '</div>';
          return;
        }

        // Group by point_count
        var groups = {};
        items.forEach(function(item) {
          var pc = item.point_count || 0;
          if (!groups[pc]) groups[pc] = [];
          groups[pc].push(item);
        });
        var sortedKeys = Object.keys(groups).map(Number).sort(function(a, b) { return a - b; });

        var html = '';
        sortedKeys.forEach(function(pc) {
          var grp = groups[pc];
          var label = pc > 0 ? pc + ' pts' : 'unknown length';
          html += '<div class="bp-length-group">'
            + '<div class="bp-length-header">'
            + '<span class="bp-length-label">' + label + '</span>'
            + '<span class="bp-length-count">' + grp.length + '</span>'
            + '</div>';
          grp.forEach(function(item) {
            var key = (item.source_key || '') + '|' + (item.start_ts || '') + '|' + (item.end_ts || '') + '|' + (item.col_name || '');
            var srcName = item.source_key || '';
            if (srcName.indexOf('/') >= 0) srcName = srcName.split('/').pop();
            html += '<div class="bp-item" data-id="' + item.id + '" data-bk-key="' + key.replace(/"/g, '&quot;') + '">'
              + '<div class="bp-item-info">'
              + '<div class="bp-item-col">' + (item.col_name || '') + '</div>'
              + '<div class="bp-item-src">' + srcName + '</div>'
              + '<div class="bp-item-range">' + (item.start_ts || '') + ' ~ ' + (item.end_ts || '') + '</div>'
              + (item.similarity != null ? '<div class="bp-item-range">' + _t('basket.similarity') + ' ' + (Number(item.similarity) * 100).toFixed(1) + '%'
                + (item.search_method ? ' · ' + _t('basket.method') + ' ' + item.search_method : '') + '</div>' : '')
              + '</div>'
              + '<button class="bp-item-del" title="Remove">&times;</button>'
              + '</div>';
          });
          html += '</div>';
        });
        body.innerHTML = html;
        body.querySelectorAll('.bp-item-del').forEach(function(btn) {
          btn.addEventListener('click', function(e) {
            e.stopPropagation();
            var itemEl = btn.closest('.bp-item');
            var id = itemEl.dataset.id;
            var bkKey = itemEl.dataset.bkKey;
            btn.disabled = true;
            BasketManager.removeById(id, bkKey).then(function() {
              itemEl.remove();
              var grpEl = itemEl.closest('.bp-length-group');
              if (grpEl && grpEl.querySelectorAll('.bp-item').length === 0) grpEl.remove();
              if (body.querySelectorAll('.bp-item').length === 0) {
                body.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted);">' + _t('js.no_draft') + '</div>';
              }
            }).catch(function() { btn.disabled = false; });
          });
        });
      })
      .catch(function() {
        body.innerHTML = '<div style="text-align:center;padding:20px;color:var(--red);">' + _t('js.basket_load_failed') + '</div>';
      });
  },
};

/* ── Collapse data panels for results (segment-aware) ── */
function collapseDataForResults() {
  // In segment mode: collapse segmentPanel; in continuous: collapse mainChartPanel
  var targets = [];
  var segPanel = $('segmentPanel');
  var mainChart = $('mainChartPanel');

  if (segPanel && !segPanel.hidden) targets.push(segPanel);
  if (mainChart && !mainChart.hidden) targets.push(mainChart);

  targets.forEach(function(el) {
    el.classList.add('collapsed-for-results');
    if (!el.querySelector('.chart-restore-btn')) {
      var label = el.id === 'segmentPanel' ? 'Segments' : 'Original Chart';
      var restoreBtn = document.createElement('button');
      restoreBtn.className = 'chart-restore-btn';
      restoreBtn.innerHTML = '&#9656; ' + label;
      restoreBtn.onclick = function() {
        el.classList.toggle('collapsed-for-results');
        restoreBtn.innerHTML = el.classList.contains('collapsed-for-results')
          ? '&#9656; ' + label : '&#9662; ' + label;
      };
      el.insertBefore(restoreBtn, el.firstChild);
    }
  });
}

/* ── Toast Notification System ─────────────────────────── */
