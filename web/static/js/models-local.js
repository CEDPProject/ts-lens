(function () {
    var esc = function (v) { return String(v == null ? '' : v).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
    var $ = function (id) { return document.getElementById(id); };
    var localRows = [], localSort = { key: 'state', dir: 1 };
    function stateRank(m) { return m.provider === 'builtin' ? 1 : (m.reachable ? 0 : 2); }   // 연결됨 → 내장 → 연결 안 됨
    function deviceOf(m) {
      if (m.external_provider && (m.provider === 'anthropic' || m.provider === 'openai_compat' || m.provider === 'ollama')) return 'external';
      return (m.inference_devices || []).indexOf('cuda') >= 0 ? 'gpu' : 'cpu';
    }
    function providerName(p) { var k = 'data.provider_' + p; var v = _t(k); return v === k ? p : v; }
    var taskLabels = {text_to_ts: _t('data.task_text_to_ts'), ts_to_text: _t('data.task_ts_to_text')};
    var deviceLabels = {cpu: _t('data.device_cpu'), gpu: _t('data.device_gpu'), external: _t('data.device_external')};
    function renderLocal() {
      var k = localSort.key, d = localSort.dir;
      var rows = localRows.slice().sort(function (a, b) {
        var va, vb;
        if (k === 'state') { va = stateRank(a); vb = stateRank(b); }
        else if (k === 'task') { va = (a.activeTasks.length ? 0 : 1) * 10 + a.tasks.length; vb = (b.activeTasks.length ? 0 : 1) * 10 + b.tasks.length; }
        else if (k === 'provider') { va = providerName(a.provider); vb = providerName(b.provider); }
        else if (k === 'device') { va = deviceOf(a); vb = deviceOf(b); }
        else { va = a.key; vb = b.key; }
        if (va < vb) return -d; if (va > vb) return d;
        return a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
      });
      $('localModels').querySelector('tbody').innerHTML = rows.map(function (m) {
        var st = m.provider === 'builtin' ? _t('data.state_builtin') : (m.reachable ? _t('data.state_ok') : _t('data.state_down'));
        var dotColor = m.provider === 'builtin' ? 'var(--text4)' : (m.reachable ? 'var(--accent)' : 'var(--red)');
        var dev = deviceOf(m);
        var tasks = m.tasks.map(function (t) {
          var on = m.activeTasks.indexOf(t) >= 0;
          return '<span class="lm-task' + (on ? ' on' : '') + '">' + taskLabels[t] + (on ? ' · ' + _t('data.state_active_short') : '') + '</span>';
        }).join('');
        return '<tr>' +
          '<td><div class="lm-key">' + esc(m.key) + '</div><div class="lm-desc">' + esc(m.description || m.model_id || '') + '</div></td>' +
          '<td>' + esc(providerName(m.provider)) + '</td>' +
          '<td><span class="lm-dev lm-dev-' + dev + '">' + deviceLabels[dev] + '</span></td>' +
          '<td><div class="lm-tasks">' + tasks + '</div></td>' +
          '<td><span class="lm-state"><span class="lm-dot" style="background:' + dotColor + '"></span>' + st + '</span>' + (m.reason ? '<div class="lm-reason">' + esc(m.reason) + '</div>' : '') + '</td></tr>';
      }).join('') || '<tr><td colspan="5">—</td></tr>';
      [].forEach.call($('localModels').querySelectorAll('th[data-sort]'), function (th) {
        th.classList.toggle('asc', th.dataset.sort === k && d === 1); th.classList.toggle('desc', th.dataset.sort === k && d === -1);
      });
    }
    [].forEach.call($('localModels').querySelectorAll('th[data-sort]'), function (th) {
      th.addEventListener('click', function () {
        if (localSort.key === th.dataset.sort) localSort.dir = -localSort.dir; else localSort = { key: th.dataset.sort, dir: 1 };
        renderLocal();
      });
    });
    function loadLocal() {
      Promise.all(['text_to_ts', 'ts_to_text'].map(function (task) {
        return fetch('/api/llm_models?task=' + task).then(function (r) { return r.json(); }).then(function (j) { return (j.models || []).map(function (m) { m._task = task; return m; }); });
      })).then(function (lists) {
        var byKey = {};   // 같은 모델은 한 줄 — 작업은 배지로
        [].concat.apply([], lists).forEach(function (m) {
          if (m.key === 'external-llm') return;
          var row = byKey[m.key] || (byKey[m.key] = Object.assign({}, m, { tasks: [], activeTasks: [] }));
          row.tasks.push(m._task); if (m.active) row.activeTasks.push(m._task);
          if (m.reachable) row.reachable = true; if (m.reason && !row.reason) row.reason = m.reason;
        });
        localRows = Object.keys(byKey).map(function (k) { return byKey[k]; });
        renderLocal();
      });
    }
    loadLocal();
})();
