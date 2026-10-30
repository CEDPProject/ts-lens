// 01-core.js — 앱 전역 상태 · 설정 · DOM/i18n 헬퍼
// static/js/app.js 를 분할한 파일. 로드 순서는 templates/index.html 참조.

/**
 * TS Pattern Finder — Frontend Application
 * CEDP (Climate & Energy Data Platform)
 */

// Search defaults come from config/search.yaml via base.html (window.SEARCH_CONFIG).
// 길이·간격 기본값은 config/app.yaml 에서 온다 (window.APP_CONFIG).
// 화면 코드에 24 · 48 · 144 같은 숫자를 직접 쓰지 않는다 — yaml 이 단일 소스다.
var ACFG = window.APP_CONFIG || {};
function acfg(dotpath) {
  var cur = ACFG, parts = String(dotpath).split('.');
  for (var i = 0; i < parts.length; i++) {
    if (cur == null || typeof cur !== 'object' || !(parts[i] in cur)) {
      console.warn('[config] app.yaml 에서 값을 찾지 못했습니다:', dotpath);
      return undefined;
    }
    cur = cur[parts[i]];
  }
  return cur;
}
window.acfg = acfg;
var DEFAULT_SEG_LEN = acfg('segment.default_length');
var PRESET_RESOLUTION = acfg('preset_shapes.generation_resolution');
var DEFAULT_SAMPLING_MIN = acfg('detection.default_sampling_minutes');
// Literals here are last-resort fallbacks only — change the YAML, not this file.
var SCFG = window.SEARCH_CONFIG || {};
var SCFG_SLIDER = SCFG.top_k_slider || {};
var DEFAULT_METHOD = SCFG.default_method || 'euclidean';
var DEFAULT_TOP_K = SCFG.segment_top_k != null ? SCFG.segment_top_k : 20;
var UNLIMITED_TOP_K = SCFG_SLIDER.unlimited != null ? SCFG_SLIDER.unlimited : 9999;

var S = {
  sid: null, values: [], labels: [], stats: null,
  queryStart: -1, queryEnd: -1,
  results: [], queryData: null, method: DEFAULT_METHOD,
  boundaries: [],
  boundaryMode: null,
  alignMode: 'calendar',
  overlays: [],
  showOverlay: true,
  // dataset + column based
  datasetKey: null,
  primaryCol: null,
  overlayCols: [],
  selectedCols: [],  // Columns selected for clustering
  numericColumns: [],   // List of all numeric column names for the current dataset
  mode: 'continuous',  // 'continuous' | 'segment'
  datasetType: null,    // 'single' | 'collection'
  indexType: 'datetime', // 'datetime' | 'numeric' | 'text'
  // segment mode
  segments: [],
  segQueryIndex: -1,
  segResults: [],
  segSort: 'date',
  generatedPattern: null,  // { values: [...], description: "...", length: N } or null
  groundTruthLabels: [],   // [{start_ts, end_ts, label_type, source_key, ...}]
  hasGroundTruth: false,
};

// If user manually toggles Data Info panel, do not auto-collapse it again.
var _dataInfoUserInteracted = false;

var $ = function(id) { return document.getElementById(id); };

/* ── i18n Helper ── */
function _t(key, ...args) {
  var keys = key.split('.');
  var val = window.T;
  for (var k of keys) {
    if (val && typeof val === 'object') val = val[k];
    else { val = key; break; }
  }
  if (typeof val !== 'string') val = key;
  // Replace {0}, {1}, etc.
  args.forEach(function(arg, i) { val = val.replace('{' + i + '}', arg); });
  return val;
}

function _patternProvenance(data) {
  data = data || {};
  if (data.engine === 'verbalts') return _t('js.pattern_engine_ai', data.model || 'VerbalTS');
  if (data.fallback_reason === 'llm_call_failed') return _t('js.pattern_fallback_call');
  if (data.fallback_reason === 'llm_parse_failed') return _t('js.pattern_fallback_parse');
  if (data.engine === 'deterministic' || data.llm_used === false) return _t('js.pattern_engine_rule');
  var model = data.model || data.model_key || data.engine || 'AI';
  return _t('js.pattern_engine_ai', model);
}

/* ── Basket (장바구니) — DB-first ── */
