/**
 * DrawQuery — Canvas-based time series pattern drawing component
 * Allows users to sketch a pattern shape that becomes the search query.
 *
 * Usage:
 *   const dq = new DrawQuery('#containerId', { onDraw: (values) => {...} });
 *   dq.getValues();  // returns normalized y-values array
 *   dq.clear();
 *   dq.setLength(48);
 */
(function(global) {
  'use strict';

  var DEFAULTS = {
    width: 320,
    height: 140,
    lineColor: '#059669',
    lineWidth: 2.5,
    gridColor: '#e5e7eb',
    bgColor: '#fafafa',
    guideColor: '#d1d5db',
    outputResolution: 48,
    minPoints: 5,
    smoothing: 0.15,
  };

  function DrawQuery(container, options) {
    this.opts = Object.assign({}, DEFAULTS, options || {});
    this.container = typeof container === 'string'
      ? document.querySelector(container)
      : container;

    if (!this.container) {
      console.error('DrawQuery: container not found');
      return;
    }

    this.points = [];
    this.isDrawing = false;
    this.hasDrawn = false;
    this._outputLength = this.opts.outputResolution;

    this._init();
  }

  DrawQuery.prototype._init = function() {
    this.container.innerHTML = '';
    this.container.classList.add('dq-container');

    // Wrapper
    this.wrapper = document.createElement('div');
    this.wrapper.className = 'dq-wrapper';
    this.container.appendChild(this.wrapper);

    // Canvas
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'dq-canvas';
    this.wrapper.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d');

    // Preview mini-chart (normalized output)
    this.previewWrap = document.createElement('div');
    this.previewWrap.className = 'dq-preview-wrap';
    this.previewWrap.innerHTML = '<div class="dq-preview-label">' + _t('js.query_shape') + '</div>';
    this.previewCanvas = document.createElement('canvas');
    this.previewCanvas.className = 'dq-preview-canvas';
    this.previewWrap.appendChild(this.previewCanvas);
    this.wrapper.appendChild(this.previewWrap);
    this.previewCtx = this.previewCanvas.getContext('2d');

    // Toolbar
    this.toolbar = document.createElement('div');
    this.toolbar.className = 'dq-toolbar';
    this.toolbar.innerHTML =
      '<button type="button" class="dq-btn dq-btn-clear" title="Clear">' +
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14"/></svg>' +
      '</button>' +
      '<button type="button" class="dq-btn dq-btn-save" title="Save to Preset" disabled>' +
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><polyline points="17,21 17,13 7,13 7,21"/><polyline points="7,3 7,8 15,8"/></svg>' +
      '</button>' +
      '<span class="dq-info"></span>';
    this.container.appendChild(this.toolbar);

    // Hint overlay
    this.hint = document.createElement('div');
    this.hint.className = 'dq-hint';
    this.hint.innerHTML = '<span class="dq-hint-icon">✎</span><span class="dq-hint-text">' + _t('js.draw_hint') + '</span>';
    this.wrapper.appendChild(this.hint);

    this._resize();
    this._bindEvents();
    this._drawGrid();
    this._updateInfo();
  };

  DrawQuery.prototype._resize = function() {
    var rect = this.wrapper.getBoundingClientRect();
    var w = rect.width || this.opts.width;
    var h = this.opts.height;
    var dpr = window.devicePixelRatio || 1;

    this.canvas.style.width = w + 'px';
    this.canvas.style.height = h + 'px';
    this.canvas.width = w * dpr;
    this.canvas.height = h * dpr;
    this.ctx.scale(dpr, dpr);
    this.w = w;
    this.h = h;

    // Preview canvas sizing
    var pw = 80, ph = 40;
    this.previewCanvas.style.width = pw + 'px';
    this.previewCanvas.style.height = ph + 'px';
    this.previewCanvas.width = pw * dpr;
    this.previewCanvas.height = ph * dpr;
    this.previewCtx.scale(dpr, dpr);
    this.pw = pw;
    this.ph = ph;
  };

  DrawQuery.prototype._bindEvents = function() {
    var self = this;

    // Mouse events
    this.canvas.addEventListener('mousedown', function(e) { self._startDraw(e); });
    this.canvas.addEventListener('mousemove', function(e) { self._continueDraw(e); });
    this.canvas.addEventListener('mouseup', function(e) { self._endDraw(e); });
    this.canvas.addEventListener('mouseleave', function(e) { self._endDraw(e); });

    // Touch events
    this.canvas.addEventListener('touchstart', function(e) {
      e.preventDefault();
      self._startDraw(e.touches[0]);
    }, { passive: false });
    this.canvas.addEventListener('touchmove', function(e) {
      e.preventDefault();
      self._continueDraw(e.touches[0]);
    }, { passive: false });
    this.canvas.addEventListener('touchend', function(e) { self._endDraw(e); });

    // Toolbar buttons
    this.toolbar.querySelector('.dq-btn-clear').addEventListener('click', function() {
      self.clear();
    });
    this.toolbar.querySelector('.dq-btn-save').addEventListener('click', function() {
      if (self.isValid() && typeof self.opts.onSave === 'function') {
        self.opts.onSave(self.getValues());
      }
    });

    // Resize handler
    window.addEventListener('resize', function() {
      self._resize();
      self._redraw();
    });
  };

  DrawQuery.prototype._getPos = function(e) {
    var rect = this.canvas.getBoundingClientRect();
    return {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top
    };
  };

  DrawQuery.prototype._startDraw = function(e) {
    this.isDrawing = true;
    this.points = [];
    var pos = this._getPos(e);
    this.points.push(pos);
    this.hint.classList.add('dq-hint-hidden');
  };

  DrawQuery.prototype._continueDraw = function(e) {
    if (!this.isDrawing) return;
    var pos = this._getPos(e);
    this.points.push(pos);
    this._redraw();
  };

  DrawQuery.prototype._endDraw = function(e) {
    if (!this.isDrawing) return;
    this.isDrawing = false;
    if (this.points.length >= this.opts.minPoints) {
      this.hasDrawn = true;
      this._lastPoints = this.points.slice();
      this._smoothPoints();
      this._redraw();
      this._updatePreview();
      this._updateInfo();
      this._fireCallback();
    }
  };

  DrawQuery.prototype._smoothPoints = function() {
    if (this.points.length < 3) return;
    var alpha = this.opts.smoothing;
    var smoothed = [this.points[0]];
    for (var i = 1; i < this.points.length - 1; i++) {
      var prev = this.points[i - 1];
      var curr = this.points[i];
      var next = this.points[i + 1];
      smoothed.push({
        x: curr.x * (1 - alpha) + (prev.x + next.x) / 2 * alpha,
        y: curr.y * (1 - alpha) + (prev.y + next.y) / 2 * alpha
      });
    }
    smoothed.push(this.points[this.points.length - 1]);
    this.points = smoothed;
  };

  DrawQuery.prototype._drawGrid = function() {
    var ctx = this.ctx;
    var w = this.w, h = this.h;

    // Background
    ctx.fillStyle = this.opts.bgColor;
    ctx.fillRect(0, 0, w, h);

    // Grid lines
    ctx.strokeStyle = this.opts.gridColor;
    ctx.lineWidth = 0.5;

    // Horizontal lines
    for (var y = 0; y <= h; y += h / 4) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }

    // Vertical lines
    for (var x = 0; x <= w; x += w / 6) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }

    // Center guide line
    ctx.strokeStyle = this.opts.guideColor;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();
    ctx.setLineDash([]);
  };

  DrawQuery.prototype._redraw = function() {
    this._drawGrid();

    if (this.points.length < 2) return;

    var ctx = this.ctx;
    ctx.strokeStyle = this.opts.lineColor;
    ctx.lineWidth = this.opts.lineWidth;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    ctx.beginPath();
    ctx.moveTo(this.points[0].x, this.points[0].y);
    for (var i = 1; i < this.points.length; i++) {
      ctx.lineTo(this.points[i].x, this.points[i].y);
    }
    ctx.stroke();
  };

  DrawQuery.prototype._updatePreview = function() {
    var ctx = this.previewCtx;
    var w = this.pw, h = this.ph;
    var values = this.getValues();

    // Clear
    ctx.fillStyle = '#f9fafb';
    ctx.fillRect(0, 0, w, h);

    if (!values || values.length < 2) {
      this.previewWrap.classList.remove('dq-preview-active');
      return;
    }

    this.previewWrap.classList.add('dq-preview-active');

    // Normalize for display
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var range = max - min || 1;

    ctx.strokeStyle = this.opts.lineColor;
    ctx.lineWidth = 1.5;
    ctx.beginPath();

    for (var i = 0; i < values.length; i++) {
      var x = (i / (values.length - 1)) * w;
      var y = h - ((values[i] - min) / range) * (h - 4) - 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  };

  DrawQuery.prototype._updateInfo = function() {
    var info = this.toolbar.querySelector('.dq-info');
    var saveBtn = this.toolbar.querySelector('.dq-btn-save');
    if (this.hasDrawn) {
      var vals = this.getValues();
      info.textContent = vals.length + ' points';
      info.classList.add('dq-info-active');
      if (saveBtn) saveBtn.disabled = false;
    } else {
      info.textContent = '';
      info.classList.remove('dq-info-active');
      if (saveBtn) saveBtn.disabled = true;
    }
  };

  DrawQuery.prototype._fireCallback = function() {
    if (typeof this.opts.onDraw === 'function') {
      this.opts.onDraw(this.getValues());
    }
    // Dispatch event for external listeners (e.g., Save to Preset button)
    document.dispatchEvent(new CustomEvent('sketchChanged', { detail: { valid: this.isValid() } }));
  };

  /**
   * Get the drawn pattern as normalized y-values array.
   * Resamples to outputResolution points, inverts Y (canvas Y is top-down).
   */
  DrawQuery.prototype.getValues = function() {
    if (!this.hasDrawn || this.points.length < this.opts.minPoints) {
      return null;
    }

    var pts = this.points;
    var n = this._outputLength;
    var h = this.h;
    var result = [];

    // Sort points by x coordinate
    var sorted = pts.slice().sort(function(a, b) { return a.x - b.x; });

    // Resample to n points using linear interpolation
    var minX = sorted[0].x;
    var maxX = sorted[sorted.length - 1].x;
    var rangeX = maxX - minX || 1;

    for (var i = 0; i < n; i++) {
      var targetX = minX + (i / (n - 1)) * rangeX;
      // Find surrounding points
      var j = 0;
      while (j < sorted.length - 1 && sorted[j + 1].x < targetX) j++;

      var p0 = sorted[j];
      var p1 = sorted[Math.min(j + 1, sorted.length - 1)];

      var t = (p1.x === p0.x) ? 0 : (targetX - p0.x) / (p1.x - p0.x);
      var y = p0.y + t * (p1.y - p0.y);

      // Invert Y (canvas Y=0 is top, we want high values at top)
      result.push(h - y);
    }

    // Z-normalize
    var mean = result.reduce(function(a, b) { return a + b; }, 0) / result.length;
    var std = Math.sqrt(result.reduce(function(a, b) { return a + (b - mean) * (b - mean); }, 0) / result.length);
    if (std < 1e-8) std = 1;

    return result.map(function(v) { return (v - mean) / std; });
  };

  /**
   * Get raw y-values (not normalized)
   */
  DrawQuery.prototype.getRawValues = function() {
    if (!this.hasDrawn || this.points.length < this.opts.minPoints) {
      return null;
    }

    var pts = this.points;
    var n = this._outputLength;
    var h = this.h;
    var result = [];

    var sorted = pts.slice().sort(function(a, b) { return a.x - b.x; });
    var minX = sorted[0].x;
    var maxX = sorted[sorted.length - 1].x;
    var rangeX = maxX - minX || 1;

    for (var i = 0; i < n; i++) {
      var targetX = minX + (i / (n - 1)) * rangeX;
      var j = 0;
      while (j < sorted.length - 1 && sorted[j + 1].x < targetX) j++;

      var p0 = sorted[j];
      var p1 = sorted[Math.min(j + 1, sorted.length - 1)];

      var t = (p1.x === p0.x) ? 0 : (targetX - p0.x) / (p1.x - p0.x);
      var y = p0.y + t * (p1.y - p0.y);

      result.push(h - y);
    }

    return result;
  };

  /**
   * Check if user has drawn a valid pattern
   */
  DrawQuery.prototype.isValid = function() {
    return this.hasDrawn && this.points.length >= this.opts.minPoints;
  };

  /**
   * Clear the canvas
   */
  DrawQuery.prototype.clear = function() {
    this.points = [];
    this.hasDrawn = false;
    this._lastPoints = null;
    this._drawGrid();
    this._updatePreview();
    this._updateInfo();
    this.hint.classList.remove('dq-hint-hidden');
    if (typeof this.opts.onClear === 'function') {
      this.opts.onClear();
    }
    // Dispatch event for external listeners
    document.dispatchEvent(new CustomEvent('sketchChanged', { detail: { valid: false } }));
  };

  /**
   * Set the output resolution (number of points in result)
   */
  DrawQuery.prototype.setLength = function(len) {
    this._outputLength = Math.max(3, Math.min(9999, len || (window.acfg ? window.acfg('preset_shapes.generation_resolution') : 48)));
    if (this.hasDrawn) {
      this._updatePreview();
      this._updateInfo();
      this._fireCallback();
    }
  };

  /**
   * Load a preset shape into the canvas
   */
  DrawQuery.prototype.loadShape = function(values) {
    if (!values || values.length < 2) return;

    var w = this.w, h = this.h;
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var range = max - min || 1;
    var pad = 10;

    this.points = [];
    for (var i = 0; i < values.length; i++) {
      var x = pad + (i / (values.length - 1)) * (w - 2 * pad);
      var y = h - pad - ((values[i] - min) / range) * (h - 2 * pad);
      this.points.push({ x: x, y: y });
    }

    this.hasDrawn = true;
    this._lastPoints = this.points.slice();
    this.hint.classList.add('dq-hint-hidden');
    this._redraw();
    this._updatePreview();
    this._updateInfo();
    this._fireCallback();
  };

  // Export
  global.DrawQuery = DrawQuery;

})(window);


/**
 * DrawQuery Integration with TS-Lens App
 * Auto-initializes when DOM is ready and provides global interface.
 */
(function() {
  'use strict';

  var _instance = null;
  var _searchBtn = null;

  function initDrawQuery() {
    var container = document.getElementById('drawQueryContainer');
    if (!container) return;

    _searchBtn = document.getElementById('btnUnifiedSearch');

    _instance = new DrawQuery(container, {
      height: 140,
      outputResolution: 48,
      onDraw: function(values) {
        updateSearchButton();
        console.log('[DrawQuery] Pattern drawn:', values ? values.length + ' points' : 'null');
      },
      onClear: function() {
        updateSearchButton();
      },
      onSave: function(values) {
        if (!values || !window.S || !window.S.datasetKey) {
          alert(window._t('js.alert_cannot_save'));
          return;
        }
        var name = prompt('Save sketch as preset.\nEnter name:');
        if (!name || !name.trim()) return;

        var body = {
          dataset_id: window.S.datasetKey,
          name: name.trim(),
          values: values,
          source: 'sketch',
          metadata: {
            length: values.length,
            column: window.S.primaryCol || ''
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
            alert(window._t('js.alert_saved') + ' ' + name);
            if (typeof window.loadPresets === 'function') window.loadPresets();
          } else {
            alert(window._t('js.alert_error') + ' ' + (res.error || 'Unknown'));
          }
        })
        .catch(function(err) {
          alert(window._t('js.alert_network_error') + ' ' + err.message);
        });
      }
    });

    // Update output length when window size changes
    var fixedLenInput = document.getElementById('inputFixedLen');
    if (fixedLenInput) {
      fixedLenInput.addEventListener('change', function() {
        var len = parseInt(this.value) || (window.acfg ? window.acfg('preset_shapes.generation_resolution') : 48);
        if (_instance) _instance.setLength(len);
      });
    }

    // Listen to fixed length preset changes
    document.querySelectorAll('.method-pill.fixed-preset').forEach(function(pill) {
      pill.addEventListener('click', function() {
        var minutes = parseInt(this.dataset.minutes);
        if (minutes > 0 && _instance) {
          // Estimate points based on sampling interval (default 10min)
          var samplingMin = window._samplingMinutes || (window.acfg ? window.acfg('detection.default_sampling_minutes') : 60);
          var len = Math.round(minutes / samplingMin);
          _instance.setLength(Math.max(3, len));
        }
      });
    });

    console.log('[DrawQuery] Initialized');
  }

  function updateSearchButton() {
    if (!_searchBtn) return;
    // Only manage button state when in sketch mode
    var queryMode = window._queryMode;
    if (queryMode !== 'sketch') return;

    var hasDrawn = _instance && _instance.isValid();
    // Enable search if either drawn pattern or chart selection exists
    var hasChartSelection = window.S && window.S.queryStart >= 0 && window.S.queryEnd > window.S.queryStart;

    _searchBtn.disabled = !(hasDrawn || hasChartSelection);
  }

  /**
   * Global interface for app.js integration
   */
  window.DrawQueryAPI = {
    getInstance: function() {
      return _instance;
    },

    hasValidPattern: function() {
      return _instance && _instance.isValid();
    },

    getQueryValues: function() {
      if (!_instance) return null;
      return _instance.getValues();
    },

    getRawQueryValues: function() {
      if (!_instance) return null;
      return _instance.getRawValues();
    },

    clear: function() {
      if (_instance) _instance.clear();
    },

    setLength: function(len) {
      if (_instance) _instance.setLength(len);
    },

    loadPreset: function(values) {
      if (_instance) _instance.loadShape(values);
    }
  };

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initDrawQuery);
  } else {
    initDrawQuery();
  }

})();

