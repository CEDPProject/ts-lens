// 21-ss-presets.js — 세그먼트 검색 — 프리셋 그리드
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

var PRESET_SHAPES = {
  rise:          [0,0.2,0.4,0.6,0.8,1.0],
  fall:          [1.0,0.8,0.6,0.4,0.2,0],
  flat:          [0.5,0.5,0.5,0.5,0.5,0.5],
  bell:          [0,0.3,0.8,1.0,0.8,0.3,0],
  v_shape:       [1.0,0.6,0.2,0,0.2,0.6,1.0],
  spike:         [0,0,0.2,1.0,0.2,0,0],
  dip:           [1.0,1.0,0.8,0,0.8,1.0,1.0],
};

function renderPresetGrid() {
  var container = $('segPresetGrid');
  if (!container) return;
  container.innerHTML = '';

  // Render column chips for multi-column preset selection
  renderColChips();

  // ── "All" button as first item ──
  var allBtn = document.createElement('button');
  allBtn.className = 'seg-preset-btn seg-preset-all';
  allBtn.dataset.preset = '__all__';
  allBtn.innerHTML =
    '<svg class="seg-preset-icon" viewBox="0 0 40 18">'
    + '<rect x="2" y="2" width="8" height="6" rx="1" fill="#059669" opacity=".5"/>'
    + '<rect x="12" y="4" width="8" height="6" rx="1" fill="#059669" opacity=".35"/>'
    + '<rect x="22" y="1" width="8" height="6" rx="1" fill="#059669" opacity=".65"/>'
    + '<rect x="7" y="10" width="8" height="6" rx="1" fill="#059669" opacity=".45"/>'
    + '<rect x="17" y="11" width="8" height="6" rx="1" fill="#059669" opacity=".55"/>'
    + '<rect x="30" y="9" width="8" height="6" rx="1" fill="#059669" opacity=".4"/>'
    + '</svg>';
  var allLabel = document.createElement('span');
  allLabel.className = 'seg-preset-label';
  allLabel.textContent = _t('js.all');
  allBtn.appendChild(allLabel);

  allBtn.addEventListener('click', function() {
    var wasActive = allBtn.classList.contains('active');
    container.querySelectorAll('.seg-preset-btn').forEach(function(b) {
      b.classList.remove('active');
    });
    if (!wasActive) {
      allBtn.classList.add('active');
      SS.selectedPreset = '__all__';
    } else {
      SS.selectedPreset = null;
    }
    window._ssSelectedPreset = SS.selectedPreset;

    // Hide previous result panels when selecting All preset
    console.log('[Preset All] Hiding result panels');
    if ($('segSearchResults')) $('segSearchResults').hidden = true;
    if ($('classifyResults')) $('classifyResults').hidden = true;

    updateUnifiedSearchBtn();
  });
  container.appendChild(allBtn);

  // ── Split presets into built-in and custom ──
  var keys = Object.keys(SS.presets);
  var builtinKeys = keys.filter(function(k) { return !SS.presets[k].custom && !SS.presets[k].hidden; });
  var hiddenKeys = keys.filter(function(k) { return !SS.presets[k].custom && SS.presets[k].hidden; });

  // Custom presets: in single-column mode, show only presets for current column
  // In multi-column mode, show all custom presets
  var isMultiCol = S.overlayCols && S.overlayCols.length > 0;
  var customKeys = keys.filter(function(k) {
    if (!SS.presets[k].custom) return false;
    if (isMultiCol) return true;  // Multi-column: show all
    // Single-column: filter by column metadata.
    // 저장 경로마다 키가 달랐다 — sketch/medoid 는 column(문자열),
    // Discovery 는 columns(배열). 둘 다 읽어야 등록한 도형이 사라지지 않는다.
    var meta = SS.presets[k].metadata || {};
    var cols = [];
    if (meta.column) cols = [meta.column];
    else if (Array.isArray(meta.columns)) cols = meta.columns;
    if (!cols.length) return true;  // No column info: show anyway
    return cols.indexOf(S.primaryCol) !== -1;
  });

  // ── Custom presets first (placed right next to "All") ──
  customKeys.forEach(function(k) {
    container.appendChild(_createPresetBtn(k, SS.presets[k], container, true));
  });

  // 사용자가 등록한 도형과 내장 도형 사이를 가로줄 하나로만 가른다
  if (customKeys.length > 0 && builtinKeys.length > 0) {
    var sep = document.createElement('span');
    sep.className = 'seg-preset-sep';
    sep.setAttribute('aria-hidden', 'true');
    container.appendChild(sep);
  }

  // ── Activated (added) built-in presets ──
  builtinKeys.forEach(function(k) {
    container.appendChild(_createPresetBtn(k, SS.presets[k], container));
  });

  // ── "+ Add preset": standardized built-in candidates, inactive until added ──
  if (hiddenKeys.length > 0) {
    var addBtn = document.createElement('button');
    addBtn.className = 'seg-preset-btn seg-preset-hidden';
    addBtn.innerHTML =
      '<svg class="seg-preset-icon" viewBox="0 0 40 18">'
      + '<path d="M20 4 L20 14 M15 9 L25 9" stroke="#6d28d9" stroke-width="2" stroke-linecap="round"/>'
      + '</svg>'
      + '<span class="seg-preset-label">Add preset (' + hiddenKeys.length + ')</span>';
    addBtn.title = 'Add a standardized preset shape (inactive until added)';
    addBtn.addEventListener('click', function() {
      _showHiddenPresetsDialog(hiddenKeys);
    });
    container.appendChild(addBtn);
  }

  // ── Reset: clear custom + activated built-ins back to default (none) ──
  if (customKeys.length > 0 || builtinKeys.length > 0) {
    var resetBtn = document.createElement('button');
    resetBtn.className = 'seg-preset-btn seg-preset-reset';
    resetBtn.innerHTML =
      '<svg class="seg-preset-icon" viewBox="0 0 40 18">'
      + '<path d="M20 4 L20 14 M15 9 L25 9" stroke="#ef4444" stroke-width="2" stroke-linecap="round"/>'
      + '</svg>'
      + '<span class="seg-preset-label">' + _t('js.reset') + '</span>';
    resetBtn.title = 'Delete custom presets & restore hidden';
    resetBtn.addEventListener('click', function() {
      _resetAllPresets(customKeys.length, hiddenKeys.length);
    });
    container.appendChild(resetBtn);
  }
}

function _showHiddenPresetsDialog(hiddenKeys) {
  var msg = 'Add a preset shape:\n\n';
  hiddenKeys.forEach(function(k, i) {
    msg += (i + 1) + '. ' + (SS.presets[k].label || k) + '\n';
  });
  msg += '\nEnter number to add (or cancel):';
  var input = prompt(msg);
  if (!input) return;
  var idx = parseInt(input, 10) - 1;
  if (idx >= 0 && idx < hiddenKeys.length) {
    var key = hiddenKeys[idx];
    fetch('/api/presets/builtin/' + key + '/unhide?dataset_id=' + encodeURIComponent(S.datasetKey || ''), { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.ok) {
          loadPresets();
        } else {
          alert(data.error || _t('js.restore_failed'));
        }
      });
  }
}

function _resetAllPresets(customCount, hiddenCount) {
  var msg = 'Reset presets to default?\n\n';
  if (customCount > 0) msg += '• Delete ' + customCount + ' custom preset(s)\n';
  msg += '• Restore all built-in preset shapes\n';
  msg += '\nDeleting custom presets cannot be undone.';

  if (!confirm(msg)) return;

  fetch('/api/presets/reset?dataset_id=' + encodeURIComponent(S.datasetKey || ''), { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.ok) {
        loadPresets();
      } else {
        alert(data.error || _t('js.reset_failed'));
      }
    })
    .catch(function(err) {
      alert(_t('js.alert_network_error') + ' ' + err.message);
    });
}

/* Discovery/LLM 이 붙인 이름은 daily_cycle 처럼 snake_case 로 온다.
   저장된 값은 건드리지 않고 화면에 나갈 때만 사람이 읽는 형태로 바꾼다. */
function _prettyPresetName(name) {
  if (!name) return '';
  var s = String(name).replace(/^custom_/, '').replace(/[_-]+/g, ' ').trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function _createPresetBtn(k, info, container, isCustom) {
  var btn = document.createElement('button');
  btn.className = 'seg-preset-btn' + (isCustom ? ' seg-preset-custom' : '');
  btn.dataset.preset = k;

  // Use preview values from API, fallback to PRESET_SHAPES
  var shape = info.preview || PRESET_SHAPES[k] || [0,0.5,1,0.5,0];
  // Normalize shape for display
  var mn = Math.min.apply(null, shape);
  var mx = Math.max.apply(null, shape);
  var range = mx - mn || 1;
  shape = shape.map(function(v) { return (v - mn) / range; });

  var svgW = 40, svgH = 18, pad = 2;
  var pts = shape.map(function(v, i) {
    var x = pad + (i / (shape.length - 1)) * (svgW - 2 * pad);
    var y = svgH - pad - v * (svgH - 2 * pad);
    return x + ',' + y;
  }).join(' ');
  var strokeColor = isCustom ? '#6366f1' : '#059669';
  var svg = '<svg class="seg-preset-icon" viewBox="0 0 ' + svgW + ' ' + svgH + '">'
    + '<polyline points="' + pts + '" fill="none" stroke="' + strokeColor + '" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
    + '</svg>';

  var label = document.createElement('span');
  label.className = 'seg-preset-label';
  label.textContent = _prettyPresetName(info.label || k);

  btn.innerHTML = svg;
  btn.appendChild(label);

  // Delete button for deletable presets (custom=delete, builtin=hide)
  if (info.deletable) {
    var delBtn = document.createElement('span');
    delBtn.className = 'preset-delete-btn';
    delBtn.innerHTML = '&times;';
    delBtn.title = isCustom ? 'Delete' : 'Hide';
    delBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (!S.datasetKey) {
        alert(_t('js.alert_select_dataset'));
        return;
      }
      if (isCustom) {
        var realKey = k.replace(/^custom_/, '');
        if (confirm('Delete preset "' + info.label + '"?')) {
          fetch('/api/presets/custom/' + realKey + '?dataset_id=' + encodeURIComponent(S.datasetKey), { method: 'DELETE' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
              if (data.ok) {
                loadPresets();
              } else {
                alert(data.error || _t('js.delete_failed'));
              }
            })
            .catch(function(err) { alert(_t('js.alert_network_error') + ' ' + err.message); });
        }
      } else {
        if (confirm('Hide preset "' + info.label + '"? You can restore it later.')) {
          fetch('/api/presets/builtin/' + k + '/hide?dataset_id=' + encodeURIComponent(S.datasetKey), { method: 'POST' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
              if (data.ok) {
                loadPresets();
              } else {
                alert(data.error || _t('js.hide_failed'));
              }
            })
            .catch(function(err) { alert(_t('js.alert_network_error') + ' ' + err.message); });
        }
      }
    });
    btn.appendChild(delBtn);
  }

  btn.addEventListener('click', function() {
    var wasActive = btn.classList.contains('active');
    container.querySelectorAll('.seg-preset-btn').forEach(function(b) {
      if (b.dataset.preset !== '__all__') b.classList.remove('active');
    });

    var ci = SS.activeColIdx || 0;
    var isMulti = S.overlayCols && S.overlayCols.length > 0;

    if (ci === 0 || !isMulti) {
      // Primary column
      if (!wasActive) {
        btn.classList.add('active');
        SS.selectedPreset = k;
      } else {
        SS.selectedPreset = null;
      }
      window._ssSelectedPreset = SS.selectedPreset;
      _updateChipPresetLabel(0, SS.selectedPreset);
    } else {
      // Overlay column
      var allCols = [S.primaryCol].concat(S.overlayCols);
      var col = allCols[ci];
      if (!wasActive) {
        btn.classList.add('active');
        SS.overlayPresets[col] = k;
      } else {
        delete SS.overlayPresets[col];
      }
      _updateChipPresetLabel(ci, SS.overlayPresets[col] || null);
    }
    updateUnifiedSearchBtn();

    // Hide previous result panels when selecting a new preset
    console.log('[Preset] Hiding result panels, preset=' + k);
    if ($('segSearchResults')) $('segSearchResults').hidden = true;
    if ($('classifyResults')) $('classifyResults').hidden = true;

    // Show cached matches from classifyData if available (single-col only)
    if (!isMulti && SS.selectedPreset && SS.classifyData) {
      console.log('[Preset] Showing cached matches for ' + SS.selectedPreset);
      showPresetMatches(SS.selectedPreset);
    }
  });

  return btn;
}

/* ── Column chip tabs (multi-column) ── */
