/**
 * Label Basket Page
 * - Column multi-select for chart display
 * - Filter and sort by various fields
 * - Inline label editing
 */
(function() {
  'use strict';

  // State
  var items = [];
  var selectedDraft = {};
  var selectedConfirmed = {};
  var availableCols = [];
  var selectedCols = new Set();
  var draftSortKey = 'point_count';
  var draftFilterText = '';
  var confirmedSortKey = 'point_count';
  var confirmedFilterText = '';

  // 사용자가 표시 컬럼을 직접 건드렸는지. 건드린 뒤에는 자동 선택이 덮어쓰지 않는다.
  var userPickedCols = false;

  // Bulk fetch state
  var _bulkQueue = [];
  var _bulkActive = false;
  var _chartObserver = null;
  // render() 마다 증가. 이전 render 의 응답/드레인이 새 DOM 을 건드리지 못하게 막는다.
  var _renderGen = 0;

  // DOM refs
  var draftGroups = document.getElementById('draftGroups');
  var draftCount = document.getElementById('draftCount');
  var confirmedGroups = document.getElementById('confirmedGroups');
  var confirmedCount = document.getElementById('confirmedCount');
  var draftToolbar = document.getElementById('draftToolbar');
  var draftCheckAll = document.getElementById('draftCheckAll');
  var draftLabelInput = document.getElementById('draftLabelInput');
  var confirmedToolbar = document.getElementById('confirmedToolbar');
  var confirmedCheckAll = document.getElementById('confirmedCheckAll');

  var COLORS = ['#059669', '#2563EB', '#D97706', '#DC2626', '#7C3AED', '#0891B2', '#E11D48', '#4F46E5'];

  function colorForIndex(index) {
    if (index < COLORS.length) return COLORS[index];
    // Golden-angle hues remain distinguishable when datasets expose >8 cols.
    return 'hsl(' + Math.round((index * 137.508) % 360) + ', 68%, 43%)';
  }

  // Initialize
  syncAndLoad();
  setupColumnPicker();
  setupFilterSort();
  setupToolbarActions();

  function syncAndLoad() {
    var local = [];
    try {
      local = JSON.parse(localStorage.getItem('basket_items') || '[]');
    } catch (e) { /* ignore */ }

    if (local.length > 0) {
      fetch('/api/basket', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: local })
      })
      .then(function(r) { return r.json(); })
      .then(function() {
        localStorage.removeItem('basket_items');
        loadItems();
      })
      .catch(function() {
        loadItems();
      });
    } else {
      loadItems();
    }
  }

  function loadItems() {
    // Fetch metadata first. Chart waveforms are requested in compact form only
    // when their cards approach the viewport.
    fetch('/api/basket')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        items = data;
        selectedDraft = {};
        selectedConfirmed = {};
        discoverColumns();
      });
  }

  function discoverColumns() {
    if (items.length === 0) {
      availableCols = [];
      renderColPicker();
      render();
      updateNavBadge();
      return;
    }

    // Render immediately from the primary columns already included in the list
    // response. Discovering every overlay column can take several seconds and
    // must not leave a basket with visible counts looking empty meanwhile.
    var primaryCols = new Set();
    items.forEach(function(it) {
      if (it.col_name) primaryCols.add(it.col_name);
      (it.columns || []).forEach(function(col) { if (col) primaryCols.add(col); });
      (it.series || []).forEach(function(series) {
        if (series.col_name) primaryCols.add(series.col_name);
      });
    });
    availableCols = Array.from(primaryCols).sort();
    if (!userPickedCols) {
      selectedCols = new Set(availableCols);
    }
    renderColPicker();
    render();
    updateNavBadge();

    // Discover all dataset columns from metadata only. Waveforms are fetched
    // separately and lazily for visible cards, avoiding duplicate heavy reads.
    fetch('/api/basket/columns')
    .then(function(r) { return r.json(); })
    .then(function(result) {
      var colSet = new Set();
      (result.columns || []).forEach(function(col) {
        if (col) colSet.add(col);
      });
      primaryCols.forEach(function(c) { colSet.add(c); });
      availableCols = Array.from(colSet).sort();
      // Basket의 기본 계약은 담은 구간의 모든 컬럼을 보여주는 것이다.
      // 사용자가 컬럼 선택을 직접 바꾸지 않았다면 나중에 발견된 컬럼도 모두 선택한다.
      if (!userPickedCols) {
        selectedCols = new Set(availableCols);
      }
      renderColPicker();
      redrawAllCharts();
    })
    .catch(function() {
      // Primary-column UI is already rendered; keep it usable if optional
      // overlay-column discovery fails.
    });
  }

  function updateNavBadge() {
    var badge = document.getElementById('basketBadge');
    if (!badge) return;

    var draftN = items.filter(function(it) { return it.status === 'draft'; }).length;
    if (draftN > 0) {
      badge.textContent = draftN;
      badge.style.display = 'inline-flex';
    } else {
      badge.style.display = 'none';
    }
  }

  // Column Picker
  function setupColumnPicker() {
    var btn = document.getElementById('btnColPicker');
    var dropdown = document.getElementById('colDropdown');
    var selectAll = document.getElementById('colSelectAll');
    var selectNone = document.getElementById('colSelectNone');

    if (!btn || !dropdown) return;

    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      dropdown.hidden = !dropdown.hidden;
    });

    document.addEventListener('click', function(e) {
      if (!dropdown.contains(e.target) && e.target !== btn) {
        dropdown.hidden = true;
      }
    });

    if (selectAll) {
      selectAll.addEventListener('click', function(e) {
        e.stopPropagation();
        userPickedCols = true;
        availableCols.forEach(function(c) { selectedCols.add(c); });
        renderColPicker();
        redrawAllCharts();
      });
    }

    if (selectNone) {
      selectNone.addEventListener('click', function(e) {
        e.stopPropagation();
        userPickedCols = true;
        selectedCols.clear();
        renderColPicker();
        redrawAllCharts();
      });
    }
  }

  function renderColPicker() {
    var list = document.getElementById('colList');
    if (!list) return;

    list.innerHTML = '';
    availableCols.forEach(function(col, i) {
      var label = document.createElement('label');
      label.className = 'lb-col-item';

      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = col;
      cb.checked = selectedCols.has(col);
      cb.addEventListener('change', function() {
        userPickedCols = true;
        if (this.checked) {
          selectedCols.add(col);
        } else {
          selectedCols.delete(col);
        }
        updateColPickerCount();
        redrawAllCharts();
      });

      var dot = document.createElement('span');
      dot.className = 'lb-col-dot';
      dot.style.background = colorForIndex(i);

      var name = document.createElement('span');
      name.textContent = col;

      label.appendChild(cb);
      label.appendChild(dot);
      label.appendChild(name);
      list.appendChild(label);
    });

    updateColPickerCount();
  }

  function updateColPickerCount() {
    var countEl = document.getElementById('colPickerCount');
    if (countEl) {
      countEl.textContent = selectedCols.size + '/' + availableCols.length;
    }
  }

  function redrawAllCharts() {
    document.querySelectorAll('canvas[data-item-id]').forEach(function(cv) {
      cv._drawn = false;
    });
    document.querySelectorAll('.lb-group-grid').forEach(function(grid) {
      var group = grid.closest('.lb-group');
      if (group && group.classList.contains('open')) {
        drawChartsInContainer(grid);
      }
    });
  }

  // Filter & Sort
  function setupFilterSort() {
    var draftSort = document.getElementById('draftSort');
    var draftFilter = document.getElementById('draftFilter');
    var confirmedSort = document.getElementById('confirmedSort');
    var confirmedFilter = document.getElementById('confirmedFilter');

    if (draftSort) {
      draftSort.addEventListener('change', function() {
        draftSortKey = this.value;
        render();
      });
    }

    if (draftFilter) {
      var timeout;
      draftFilter.addEventListener('input', function() {
        clearTimeout(timeout);
        var val = this.value;
        timeout = setTimeout(function() {
          draftFilterText = val.toLowerCase();
          render();
        }, 200);
      });
    }

    if (confirmedSort) {
      confirmedSort.addEventListener('change', function() {
        confirmedSortKey = this.value;
        render();
      });
    }

    if (confirmedFilter) {
      var timeout2;
      confirmedFilter.addEventListener('input', function() {
        clearTimeout(timeout2);
        var val = this.value;
        timeout2 = setTimeout(function() {
          confirmedFilterText = val.toLowerCase();
          render();
        }, 200);
      });
    }
  }

  function applyFilter(arr, filterText) {
    if (!filterText) return arr;
    return arr.filter(function(it) {
      return (it.col_name || '').toLowerCase().includes(filterText) ||
             (it.label || '').toLowerCase().includes(filterText) ||
             (it.context || '').toLowerCase().includes(filterText) ||
             (it.source_key || '').toLowerCase().includes(filterText);
    });
  }

  function applySort(arr, sortKey) {
    return arr.slice().sort(function(a, b) {
      switch (sortKey) {
        case 'point_count':
          return (a.point_count || 0) - (b.point_count || 0);
        case 'start_ts':
          return (a.start_ts || '').localeCompare(b.start_ts || '');
        case 'col_name':
          return (a.col_name || '').localeCompare(b.col_name || '');
        case 'label':
          return (a.label || '').localeCompare(b.label || '');
        default:
          return 0;
      }
    });
  }

  // Render
  function render() {
    // 진행 중인 드레인 체인은 세대 번호로 무효화한다. _bulkActive 를 그냥 false 로
    // 되돌리면 아직 살아 있는 체인과 새 체인이 동시에 큐를 갉아먹는다.
    _renderGen++;
    if (_chartObserver) {
      _chartObserver.disconnect();
      _chartObserver = null;
    }
    _bulkQueue = [];
    _bulkActive = false;

    var drafts = items.filter(function(it) { return it.status === 'draft'; });
    var confirmed = items.filter(function(it) { return it.status === 'confirmed'; });

    drafts = applyFilter(drafts, draftFilterText);
    drafts = applySort(drafts, draftSortKey);
    confirmed = applyFilter(confirmed, confirmedFilterText);
    confirmed = applySort(confirmed, confirmedSortKey);

    draftCount.textContent = drafts.length;
    confirmedCount.textContent = confirmed.length;
    var workflowDraftCount = document.getElementById('workflowDraftCount');
    var workflowConfirmedCount = document.getElementById('workflowConfirmedCount');
    var workflowReady = document.getElementById('workflowReady');
    if (workflowDraftCount) workflowDraftCount.textContent = drafts.length;
    if (workflowConfirmedCount) workflowConfirmedCount.textContent = confirmed.length;
    if (workflowReady) {
      workflowReady.textContent = confirmed.length > 0
        ? window._t('basket.ready_count', confirmed.length)
        : window._t('basket.not_ready');
      workflowReady.classList.toggle('is-ready', confirmed.length > 0);
    }
    draftToolbar.hidden = drafts.length === 0;
    confirmedToolbar.hidden = confirmed.length === 0;

    renderGrouped(draftGroups, drafts, true);
    renderGrouped(confirmedGroups, confirmed, false);
  }

  function groupByKey(arr, sortKey, forceByLabel) {
    var groups = {};
    arr.forEach(function(it) {
      var key;
      if (forceByLabel) {
        key = it.label || '(unlabeled)';
      } else if (sortKey === 'col_name') {
        key = it.col_name || '(none)';
      } else if (sortKey === 'label') {
        key = it.label || '(none)';
      } else {
        key = (it.point_count || 0) + ' pts';
      }
      if (!groups[key]) groups[key] = [];
      groups[key].push(it);
    });

    // Sort items within each group
    Object.keys(groups).forEach(function(key) {
      groups[key] = applySort(groups[key], sortKey);
    });

    var keys = Object.keys(groups).sort();
    return keys.map(function(k) {
      return { label: k, items: groups[k] };
    });
  }

  function renderGrouped(container, arr, isDraft) {
    container.innerHTML = '';

    if (arr.length === 0) {
      var icon = isDraft ? '&#128203;' : '&#9989;';
      var text = isDraft ? window._t('basket.no_draft') : window._t('basket.no_confirmed');
      container.innerHTML =
        '<div class="lb-empty">' +
        '<div class="lb-empty-icon">' + icon + '</div>' +
        '<div class="lb-empty-text">' + text + '</div>' +
        '</div>';
      return;
    }

    var sortKey = isDraft ? draftSortKey : confirmedSortKey;
    var groups = groupByKey(arr, sortKey, !isDraft);

    groups.forEach(function(g) {
      var group = document.createElement('div');
      group.className = 'lb-group open';

      var head = document.createElement('div');
      head.className = 'lb-group-head';
      head.innerHTML =
        '<input type="checkbox" class="lb-group-check" title="' + window._t('basket.select_group') + '">' +
        '<span class="lb-group-label">' + escHtml(g.label) + '</span>' +
        '<span class="lb-group-count">' + g.items.length + '</span>' +
        '<span class="lb-group-arrow">&#9656;</span>';
      group.appendChild(head);

      var sel = isDraft ? selectedDraft : selectedConfirmed;
      var groupCheck = head.querySelector('.lb-group-check');
      groupCheck.addEventListener('click', function(e) { e.stopPropagation(); });
      (function(gc, groupItems) {
        gc.addEventListener('change', function() {
          var checked = gc.checked;
          groupItems.forEach(function(it) { sel[it.id] = checked; });
          group.querySelectorAll('.lb-card-check').forEach(function(chk) {
            chk.checked = checked;
            var card = chk.closest('.lb-card');
            if (card) card.classList.toggle('selected', checked);
          });
        });
      })(groupCheck, g.items);

      var body = document.createElement('div');
      body.className = 'lb-group-body';

      var grid = document.createElement('div');
      grid.className = 'lb-group-grid';
      g.items.forEach(function(it) {
        grid.appendChild(createCard(it, isDraft));
      });
      body.appendChild(grid);

      if (!isDraft) {
        var actions = document.createElement('div');
        actions.className = 'lb-group-actions';
        var exportBtn = document.createElement('button');
        exportBtn.className = 'lb-btn lb-btn-outline lb-btn-sm';
        exportBtn.textContent = window._t('js.export_group');
        exportBtn.addEventListener('click', function(e) {
          e.stopPropagation();
          window.location = '/api/basket/export?status=confirmed';
        });
        actions.appendChild(exportBtn);
        body.appendChild(actions);
      }

      group.appendChild(body);

      head.addEventListener('click', function() {
        group.classList.toggle('open');
        if (group.classList.contains('open')) {
          drawChartsInContainer(grid);
        }
      });

      container.appendChild(group);

      requestAnimationFrame(function() {
        setTimeout(function() {
          drawChartsInContainer(grid);
        }, 30);
      });
    });
  }

  function createCard(it, isDraft) {
    var card = document.createElement('div');
    var sel = isDraft ? selectedDraft : selectedConfirmed;
    card.className = 'lb-card' + (sel[it.id] ? ' selected' : '');
    card.dataset.id = it.id;

    var srcLabel = it.source_key || it.dataset_key || '';
    var startShort = (it.start_ts || '').replace(/^\d{4}-/, '');
    var endShort = (it.end_ts || '').replace(/^\d{4}-/, '');

    var html = '';

    html += '<input type="checkbox" class="lb-card-check" data-id="' + it.id + '"' +
            (sel[it.id] ? ' checked' : '') + '>';

    html += '<div class="lb-card-header">';
    html += '<div class="lb-card-meta">' + escHtml(srcLabel) + '</div>';
    html += '<div class="lb-card-time">' + escHtml(startShort) + ' ~ ' + escHtml(endShort) + '</div>';
    html += '</div>';

    html += '<div class="lb-chart-wrap">';
    html += '<canvas class="lb-card-canvas" data-item-id="' + it.id + '" data-col-name="' + escHtml(it.col_name || '') + '"></canvas>';
    html += '<div class="lb-chart-legend" data-item-id="' + it.id + '"></div>';
    html += '</div>';

    html += '<div class="lb-card-info">';
    html += '<span class="lb-card-pts">' + (it.point_count || 0) + 'pts</span>';
    if (it.similarity != null) {
      html += '<span class="lb-card-sim">' + window._t('basket.similarity') + ' ' + (Number(it.similarity) * 100).toFixed(1) + '%</span>';
    }
    if (it.search_method) {
      html += '<span class="lb-card-method">' + window._t('basket.method') + ' ' + escHtml(it.search_method) + '</span>';
    }
    if (it.col_name) {
      html += '<span class="lb-card-col">' + escHtml(it.col_name) + '</span>';
    }
    if (it.context) {
      html += '<span class="lb-card-context">' + escHtml(it.context) + '</span>';
    }
    if (it.label) {
      html += '<span class="lb-card-label-tag" data-label-id="' + it.id + '">' + escHtml(it.label) + '</span>';
    }
    html += '</div>';

    html += '<div class="lb-card-actions">';
    if (isDraft) {
      html += '<button class="lb-card-move to-confirmed" data-id="' + it.id + '">' + window._t('basket.move_confirmed') + ' &rarr;</button>';
    } else {
      html += '<button class="lb-card-move to-draft" data-id="' + it.id + '">&larr; ' + window._t('basket.move_draft') + '</button>';
    }
    if (!isDraft && it.label) {
      html += '<button class="lb-card-edit-label" data-id="' + it.id + '">' + window._t('basket.edit') + '</button>';
    }
    html += '<button class="lb-card-del" data-id="' + it.id + '">' + window._t('basket.delete') + '</button>';
    html += '</div>';

    card.innerHTML = html;

    var cv = card.querySelector('canvas[data-item-id]');
    if (cv && it.values && it.values.length > 0) {
      cv._embeddedValues = it.values;
    }

    wireCardEvents(card, it, isDraft);

    return card;
  }

  function wireCardEvents(card, it, isDraft) {
    var sel = isDraft ? selectedDraft : selectedConfirmed;
    var chk = card.querySelector('.lb-card-check');
    chk.addEventListener('click', function(e) { e.stopPropagation(); });
    chk.addEventListener('change', function() {
      sel[it.id] = this.checked;
      card.classList.toggle('selected', this.checked);
    });
    card.addEventListener('click', function(e) {
      if (e.target.closest('button') || e.target.tagName === 'INPUT') return;
      sel[it.id] = !sel[it.id];
      chk.checked = sel[it.id];
      card.classList.toggle('selected', sel[it.id]);
    });

    card.querySelector('.lb-card-del').addEventListener('click', function(e) {
      e.stopPropagation();
      deleteItem(it.id);
    });

    var moveBtn = card.querySelector('.lb-card-move');
    if (moveBtn) {
      moveBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        if (isDraft) {
          updateItem(it.id, { status: 'confirmed' });
        } else {
          updateItem(it.id, { status: 'draft', label: null });
        }
      });
    }

    var editBtn = card.querySelector('.lb-card-edit-label');
    if (editBtn) {
      editBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        startInlineEdit(card, it);
      });
    }
  }

  function startInlineEdit(card, it) {
    var labelTag = card.querySelector('.lb-card-label-tag');
    if (!labelTag) return;

    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'lb-inline-edit';
    input.value = it.label || '';
    labelTag.replaceWith(input);
    input.focus();
    input.select();

    var saved = false;

    function save() {
      if (saved) return;
      saved = true;
      var newLabel = input.value.trim();
      if (newLabel && newLabel !== it.label) {
        updateItem(it.id, { label: newLabel });
      } else {
        restoreLabelTag(input, it);
      }
    }

    input.addEventListener('blur', save);
    input.addEventListener('keydown', function(ev) {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        save();
      }
      if (ev.key === 'Escape') {
        saved = true;
        restoreLabelTag(input, it);
      }
    });
  }

  function restoreLabelTag(input, it) {
    var newTag = document.createElement('span');
    newTag.className = 'lb-card-label-tag';
    newTag.dataset.labelId = it.id;
    newTag.textContent = it.label || '';
    input.replaceWith(newTag);
  }

  // Chart Rendering
  function drawChartsInContainer(container) {
    var needBulk = [];

    container.querySelectorAll('canvas[data-item-id]').forEach(function(cv) {
      if (cv._drawn) return;
      drawItemChart(cv);
      // Metadata-first loading means even the primary series may not be in the
      // initial response. Fetch when the card has no embedded snapshot, or
      // when another selected column is needed.
      if (!cv._allColData && (!cv._embeddedValues || wantsOverlay(cv))) {
        var rect = cv.getBoundingClientRect();
        var nearViewport = rect.bottom >= -300 && rect.top <= (window.innerHeight + 300);
        if (nearViewport) {
          needBulk.push(cv);
        } else if (typeof IntersectionObserver !== 'undefined') {
          if (!_chartObserver) {
            _chartObserver = new IntersectionObserver(function(entries) {
              var visible = [];
              entries.forEach(function(entry) {
                if (!entry.isIntersecting) return;
                _chartObserver.unobserve(entry.target);
                if (!entry.target._allColData && !entry.target._loading) visible.push(entry.target);
              });
              if (visible.length) fetchBulkData(visible);
            }, { rootMargin: '300px 0px' });
          }
          _chartObserver.observe(cv);
        } else {
          needBulk.push(cv);
        }
      }
    });

    if (needBulk.length > 0) {
      fetchBulkData(needBulk);
    }
  }

  // 카드 제 컬럼 말고 다른 컬럼이 선택돼 있을 때만 구간 원본을 더 받아 온다.
  function wantsOverlay(canvas) {
    var own = canvas.dataset.colName;
    var other = false;
    selectedCols.forEach(function(c) { if (c !== own) other = true; });
    return other;
  }

  // 지금 가진 것으로 그릴 수 있는 최선을 그린다.
  // 선택 컬럼 오버레이 > 담을 때 저장해 둔 제 구간 값 > 빈 상태.
  function drawItemChart(canvas) {
    var colName = canvas.dataset.colName;
    if (canvas._allColData) {
      drawSelectedColsChart(canvas, canvas._allColData, colName);
    } else if (canvas._embeddedValues && canvas._embeddedValues.length > 1) {
      drawSingleChart(canvas, canvas._embeddedValues, colName);
    } else {
      drawEmptyState(canvas);
    }
    canvas._drawn = true;
  }

  function fetchBulkData(canvases) {
    canvases.forEach(function(cv) {
      if (cv._loading || cv._allColData) return;
      cv._loading = true;
      _bulkQueue.push(cv);
    });
    if (!_bulkActive) drainBulkQueue();
  }

  function drainBulkQueue() {
    if (_bulkQueue.length === 0) {
      _bulkActive = false;
      return;
    }
    _bulkActive = true;

    var gen = _renderGen;
    var batch = _bulkQueue.splice(0, 30);
    var ids = batch.map(function(cv) {
      return parseInt(cv.dataset.itemId, 10);
    });

    fetch('/api/basket/bulk-data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: ids, compact: true, max_points: 400 })
    })
    .then(function(r) { return r.json(); })
    .then(function(result) {
      if (gen !== _renderGen) return;
      batch.forEach(function(cv) {
        cv._loading = false;
        var data = result[cv.dataset.itemId] || {};
        if (data.series && data.series.length > 0) {
          cv._allColData = data;
        }
        // 오버레이가 없어도 카드가 담고 있는 제 구간 값은 그대로 그린다.
        drawItemChart(cv);
      });
    })
    .catch(function() {
      if (gen !== _renderGen) return;
      batch.forEach(function(cv) {
        cv._loading = false;
        // 실패는 최종 상태가 아니다. 다시 그릴 수 있게 표시를 풀어 둔다.
        cv._drawn = false;
        drawItemChart(cv);
      });
    })
    .finally(function() {
      if (gen !== _renderGen) {
        // 낡은 체인: 새 render 가 이미 자기 체인을 돌리고 있으므로 여기서 끝낸다.
        return;
      }
      drainBulkQueue();
    });
  }

  function drawEmptyState(canvas) {
    var w = canvas.offsetWidth || 200;
    var h = canvas.offsetHeight || 90;
    canvas.width = w * 2;
    canvas.height = h * 2;
    var ctx = canvas.getContext('2d');
    ctx.scale(2, 2);
    ctx.fillStyle = '#9CA3AF';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(window._t('basket.no_data'), w / 2, h / 2 + 4);
  }

  function drawSingleChart(canvas, values, colName) {
    if (!values || values.length < 2) {
      drawEmptyState(canvas);
      return;
    }

    var w = canvas.offsetWidth || 200;
    var h = canvas.offsetHeight || 90;
    canvas.width = w * 2;
    canvas.height = h * 2;
    var ctx = canvas.getContext('2d');
    ctx.scale(2, 2);

    var card = canvas.closest('.lb-card');
    var legendEl = card ? card.querySelector('.lb-chart-legend') : null;
    if (legendEl) {
      legendEl.innerHTML = '';
      legendEl.hidden = true;
    }

    var pad = { t: 4, r: 4, b: 4, l: 4 };
    var cw = w - pad.l - pad.r;
    var ch = h - pad.t - pad.b;

    var mn = Infinity, mx = -Infinity;
    for (var i = 0; i < values.length; i++) {
      var v = values[i];
      if (v != null && isFinite(v)) {
        if (v < mn) mn = v;
        if (v > mx) mx = v;
      }
    }
    var rng = mx - mn || 1;

    ctx.strokeStyle = COLORS[0];
    ctx.lineWidth = 1.5;
    ctx.beginPath();

    var first = true;
    for (var j = 0; j < values.length; j++) {
      var val = values[j];
      if (val == null || !isFinite(val)) continue;
      var x = pad.l + (j / (values.length - 1)) * cw;
      var y = pad.t + (1 - (val - mn) / rng) * ch;
      if (first) {
        ctx.moveTo(x, y);
        first = false;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
  }

  function drawSelectedColsChart(canvas, allData, primaryCol) {
    var w = canvas.offsetWidth || 200;
    var h = canvas.offsetHeight || 90;
    canvas.width = w * 2;
    canvas.height = h * 2;
    var ctx = canvas.getContext('2d');
    ctx.scale(2, 2);

    var colMap = {};
    if (allData && Array.isArray(allData.series)) {
      allData.series.forEach(function(series) {
        if (selectedCols.has(series.col_name)) {
          colMap[series.col_name] = series.values || [];
        }
      });
    } else {
      (allData || []).forEach(function(r) {
        if (selectedCols.has(r.col_name)) {
          if (!colMap[r.col_name]) colMap[r.col_name] = [];
          colMap[r.col_name].push(r.value);
        }
      });
    }

    // A selected column can be absent in a mixed-dataset Basket. In that case
    // retain the primary-series context, but honour an explicit "None" choice.
    if (selectedCols.size > 0 && Object.keys(colMap).length === 0 && primaryCol) {
      if (allData && Array.isArray(allData.series)) {
        var ownSeries = allData.series.find(function(series) { return series.col_name === primaryCol; });
        if (ownSeries) colMap[primaryCol] = ownSeries.values || [];
      } else {
        (allData || []).forEach(function(r) {
          if (r.col_name === primaryCol) {
            if (!colMap[primaryCol]) colMap[primaryCol] = [];
            colMap[primaryCol].push(r.value);
          }
        });
      }
    }

    var colNames = Object.keys(colMap);
    if (colNames.length === 0) {
      if (canvas._embeddedValues && canvas._embeddedValues.length > 1) {
        drawSingleChart(canvas, canvas._embeddedValues, primaryCol);
      } else {
        drawEmptyState(canvas);
      }
      return;
    }

    var drawCols = [];
    if (colMap[primaryCol]) drawCols.push(primaryCol);
    colNames.forEach(function(c) {
      if (c !== primaryCol) drawCols.push(c);
    });

    var card = canvas.closest('.lb-card');
    var legendEl = card ? card.querySelector('.lb-chart-legend') : null;
    if (legendEl) {
      if (drawCols.length > 1) {
        legendEl.innerHTML = drawCols.map(function(col, i) {
          var colIdx = availableCols.indexOf(col);
          var color = colorForIndex(colIdx >= 0 ? colIdx : i);
          return '<span class="lb-legend-item"><span class="lb-legend-dot" style="background:' + color + '"></span>' + escHtml(col) + '</span>';
        }).join('');
        legendEl.hidden = false;
      } else {
        legendEl.innerHTML = '';
        legendEl.hidden = true;
      }
    }

    var pad = { t: 4, r: 4, b: 4, l: 4 };
    var cw = w - pad.l - pad.r;
    var ch = h - pad.t - pad.b;

    drawCols.forEach(function(col, ci) {
      var vals = colMap[col];
      if (!vals || vals.length < 2) return;

      var mn = Infinity, mx = -Infinity;
      for (var i = 0; i < vals.length; i++) {
        if (vals[i] != null && isFinite(vals[i])) {
          if (vals[i] < mn) mn = vals[i];
          if (vals[i] > mx) mx = vals[i];
        }
      }
      var rng = mx - mn || 1;

      var colIdx = availableCols.indexOf(col);
      ctx.strokeStyle = colorForIndex(colIdx >= 0 ? colIdx : ci);
      ctx.lineWidth = ci === 0 ? 1.5 : 1;
      ctx.globalAlpha = ci === 0 ? 1 : 0.6;
      ctx.beginPath();

      var first = true;
      for (var j = 0; j < vals.length; j++) {
        if (vals[j] == null || !isFinite(vals[j])) continue;
        var x = pad.l + (j / (vals.length - 1)) * cw;
        var y = pad.t + (1 - (vals[j] - mn) / rng) * ch;
        if (first) {
          ctx.moveTo(x, y);
          first = false;
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.stroke();
    });

    ctx.globalAlpha = 1;
  }

  // CRUD Operations
  function deleteItem(id) {
    fetch('/api/basket/' + id, { method: 'DELETE' })
      .then(function(r) {
        if (!r.ok) throw new Error(_t('js.delete_failed'));
        return r.json();
      })
      .then(function() {
        loadItems();
      })
      .catch(function(err) {
        console.error('Delete error:', err);
      });
  }

  function updateItem(id, data) {
    fetch('/api/basket/' + id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    })
    .then(function(r) {
      if (!r.ok) throw new Error(_t('js.update_failed'));
      return r.json();
    })
    .then(function() {
      loadItems();
    })
    .catch(function(err) {
      console.error('Update error:', err);
    });
  }

  // Toolbar Actions
  function setupToolbarActions() {
    draftCheckAll.addEventListener('change', function() {
      var drafts = items.filter(function(it) { return it.status === 'draft'; });
      drafts.forEach(function(it) {
        selectedDraft[it.id] = draftCheckAll.checked;
      });
      render();
    });

    confirmedCheckAll.addEventListener('change', function() {
      var confirmed = items.filter(function(it) { return it.status === 'confirmed'; });
      confirmed.forEach(function(it) {
        selectedConfirmed[it.id] = confirmedCheckAll.checked;
      });
      render();
    });

    document.getElementById('btnConfirmSelected').addEventListener('click', function() {
      var ids = Object.keys(selectedDraft).filter(function(k) {
        return selectedDraft[k];
      }).map(Number);

      if (ids.length === 0) return;

      var label = draftLabelInput.value.trim();
      if (!label) {
        draftLabelInput.focus();
        return;
      }

      fetch('/api/basket/bulk', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ids, label: label, status: 'confirmed' })
      })
      .then(function() {
        draftLabelInput.value = '';
        loadItems();
      })
      .catch(function(err) {
        console.error('Confirm error:', err);
        loadItems();
      });
    });

    document.getElementById('btnDeleteDraft').addEventListener('click', function() {
      var ids = Object.keys(selectedDraft).filter(function(k) {
        return selectedDraft[k];
      }).map(Number);

      if (ids.length === 0) return;
      if (!confirm(window._t('basket.delete_selected', ids.length))) return;

      fetch('/api/basket/bulk', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ids })
      })
      .then(function() {
        loadItems();
      })
      .catch(function(err) {
        console.error('Bulk delete error:', err);
        loadItems();
      });
    });

    document.getElementById('btnDeleteConfirmed').addEventListener('click', function() {
      var ids = Object.keys(selectedConfirmed).filter(function(k) {
        return selectedConfirmed[k];
      }).map(Number);

      if (ids.length === 0) return;
      if (!confirm(window._t('basket.delete_confirmed', ids.length))) return;

      fetch('/api/basket/bulk', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ids })
      })
      .then(function() {
        loadItems();
      })
      .catch(function(err) {
        console.error('Bulk delete error:', err);
        loadItems();
      });
    });

    document.getElementById('btnSyncBasket').addEventListener('click', syncAndLoad);

    document.getElementById('btnExportAll').addEventListener('click', function() {
      window.location = '/api/basket/export?status=confirmed';
    });
  }

  // Utilities
  function escHtml(s) {
    var d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
})();
