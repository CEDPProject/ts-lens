// 20-ss-core.js — 세그먼트 검색 — 모듈 상태 · LLM 선택기
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

/* ═══════════════════════════════════════════════════════════════
   Segment Search Module
   - Global LLM selector
   - Preset shapes + text query
   - Segment search API calls
   - TS-to-Text description
   ═══════════════════════════════════════════════════════════════ */
var SS = {
  presets: {},
  timeUnits: {},
  selectedTimeUnit: 'day',
  selectedPreset: null,
  queryMode: 'preset',   // 'preset' | 'text'
  distMethod: 'dtw',
  results: [],
  samplingMinutes: null,
  llmModels: [],
  queryModel: '',
  describeModel: '',
  classifyData: null,     // cached classify-all result
  overlayPresets: {},     // per-column preset: { col_name: preset_key }
  activeColIdx: 0,        // which column the preset grid applies to (0=primary)
};
window._ssSelectedPreset = null;  // expose for unified btn

function _populateModelSelect(sel, models, selectedKey) {
  if (!sel) return;
  sel.innerHTML = '';
  models.forEach(function(m) {
    var opt = document.createElement('option');
    opt.value = m.key;
    var desc = m.description || '';
    var statusNote = m.available === false ? ' [' + _t('finder.model_listed_unavailable') + ']' : '';
    opt.textContent = (desc ? m.key + ' — ' + desc : m.key) + statusNote;
    if (statusNote) opt.title = m.reachability_reason || _t('finder.model_listed_unavailable');
    if (m.key === selectedKey) opt.selected = true;
    sel.appendChild(opt);
  });
}

function _renderTextModelStatus(status) {
  window._llmAvailable = !!status.available;
  var el = $('llmTextRuntimeStatus');
  if (!el) return;
  var label = status.model_key || status.provider || 'AI';
  el.className = 'model-runtime-status ' + (status.available ? 'status-ready' : (status.configured ? 'status-limited' : 'status-error'));
  var text = el.querySelector('span:last-child');
  if (text) text.textContent = status.available
    ? _t(status.provider === 'builtin' ? 'finder.cpu_ready' : (status.provider === 'verbalts' ? 'finder.verbalts_ready' : 'finder.model_ready'), label)
    : (status.configured ? _t('finder.model_limited', label) : _t('finder.model_unavailable'));
  el.title = status.reason || '';
}

function _updateTextModelHints() {
  var isVerbal = SS.queryModel === 'verbalts-synth';
  var examples = $('verbaltsExamples');
  if (examples) examples.hidden = !isVerbal;
  var hints = $('textHintChips');
  if (hints) hints.hidden = isVerbal;
  var note = $('textModelNote');
  if (note) note.textContent = _t(SS.queryModel === 'cpu-basic' ? 'finder.cpu_note' : (isVerbal ? 'finder.verbalts_note' : 'finder.text_to_ts_note'));
  var input = $('segSearchInput');
  if (input) input.placeholder = isVerbal
    ? 'The trend type is linear. The trend direction is up. There is 0 season cycle.'
    : _t('finder.text_placeholder');
}

document.addEventListener('click', function(event) {
  var button = event.target.closest('[data-verbalts]');
  if (!button) return;
  var input = $('segSearchInput');
  if (!input) return;
  // Keep the example and the request's model in sync, including restored pages.
  var changed = SS.queryModel !== 'verbalts-synth';
  SS.queryModel = 'verbalts-synth';
  window._llmQueryModel = SS.queryModel;
  var select = $('llmTextQuerySelect');
  if (select) select.value = SS.queryModel;
  if (changed) _activateTaskModel('text_to_ts', SS.queryModel);
  _updateTextModelHints();
  input.value = button.dataset.verbalts;
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.focus();
});

function _getActiveModelKey(models) {
  var activeKey = '';
  models.forEach(function(m) {
    if (m.active && !activeKey) activeKey = m.key;
  });
  return activeKey || (models[0] && models[0].key) || '';
}

function _activateTaskModel(task, modelKey) {
  if (!modelKey) return Promise.resolve();
  return fetch('/api/llm_models/activate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model_key: modelKey, task: task })
  }).then(function(r) { return r.json(); }).then(function(data) {
    if (task === 'text_to_ts' && data && data.status) _renderTextModelStatus(data.status);
    return data;
  }).catch(function() {});
}

/* ── Scoped LLM Selectors (Query / Describe) ── */
function loadScopedLLMSelectors() {
  Promise.all([
    fetch('/api/llm_status?task=text_to_ts').then(function(r) { return r.json(); }),
    fetch('/api/llm_models?task=text_to_ts').then(function(r) { return r.json(); }),
    fetch('/api/llm_models?task=ts_to_text').then(function(r) { return r.json(); }),
  ])
    .then(function(responses) {
      var llmStatus = responses[0] || {};
      var qModels = (responses[1] && responses[1].models) || [];
      var dModels = (responses[2] && responses[2].models) || [];
      if (qModels.length === 0 && dModels.length === 0) return;

      window._llmAvailable = !!llmStatus.available;
      _renderTextModelStatus(llmStatus);

      SS.llmModels = qModels.length ? qModels : dModels;

      var qActive = _getActiveModelKey(qModels);
      var dActive = _getActiveModelKey(dModels);

      SS.queryModel = qActive || dActive || '';
      SS.describeModel = dActive || qActive || '';
      window._llmQueryModel = SS.queryModel;
      window._llmDescribeModel = SS.describeModel;
      _updateTextModelHints();

      _populateModelSelect($('llmTextQuerySelect'), qModels.length ? qModels : dModels, SS.queryModel);
      _populateModelSelect($('llmDescribeSelect'), dModels.length ? dModels : qModels, SS.describeModel);
    })
    .catch(function(e) { console.warn('[llm] Model list fetch failed:', e); });
}

if ($('llmTextQuerySelect')) {
  $('llmTextQuerySelect').addEventListener('change', function() {
    SS.queryModel = this.value || '';
    window._llmQueryModel = SS.queryModel;
    _updateTextModelHints();
    _activateTaskModel('text_to_ts', SS.queryModel);
  });
}

if ($('llmDescribeSelect')) {
  $('llmDescribeSelect').addEventListener('change', function() {
    SS.describeModel = this.value || '';
    window._llmDescribeModel = SS.describeModel;
    _activateTaskModel('ts_to_text', SS.describeModel);
  });
}

/* ── Load Presets & Time Units ── */
/* 프리셋 목록을 다시 읽어 그린다.
   호출한 쪽이 "다 그려진 뒤"에 이어서 할 일이 있으므로 프라미스를 돌려준다. */
function loadPresets() {
  var url = '/api/segment_search/presets';
  if (S.datasetKey) {
    url += '?dataset_id=' + encodeURIComponent(S.datasetKey);
  }
  return fetch(url)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      // Convert lists to dicts for easier access
      var presetList = data.presets || [];
      SS.presets = {};
      presetList.forEach(function(p) { SS.presets[p.key] = p; });

      var tuList = data.time_units || [];
      SS.timeUnits = {};
      tuList.forEach(function(t) { SS.timeUnits[t.key] = t; });

      // Time unit selection now handled in split modal
      renderPresetGrid();
    })
    .catch(function(err) { console.error('Presets load failed:', err); });
}
window.loadPresets = loadPresets;  // Expose for save button refresh
