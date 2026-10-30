(function () {
    var form = document.getElementById('extForm');
    var esc = function (v) { return String(v == null ? '' : v).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
    var $ = function (id) { return document.getElementById(id); };
    var KEEP = '__keep__', state = { keySet: false, hint: '' };
    function msg(text, bad) { var m = $('extMsg'); m.textContent = text || ''; m.style.color = bad ? 'var(--red)' : 'var(--text3)'; }
    function syncProvider() {
      var p = $('extProvider').value;
      $('extBaseUrlField').style.display = p === 'openai_compat' ? '' : 'none';
      $('extModel').placeholder = p === 'openai_compat' ? _t('data.ext_model_ph_openai') : _t('data.ext_model_ph_anthropic');
    }
    function render(s) {
      state.keySet = !!s.api_key_set; state.hint = s.api_key_hint || '';
      $('extEnabled').checked = !!s.enabled; $('extProvider').value = s.provider || 'anthropic';
      $('extModel').value = s.model || ''; $('extBaseUrl').value = s.base_url || ''; $('extLabel').value = s.label || '';
      $('extApiKey').value = ''; $('extApiKey').placeholder = state.keySet ? _t('data.ext_api_key_keep', state.hint) : _t('data.ext_api_key_ph');
      $('extUseTextToTs').checked = !!(s.use_for && s.use_for.text_to_ts);
      $('extUseTsToText').checked = !!(s.use_for && s.use_for.ts_to_text);
      $('extUseMeta').checked = !!(s.use_for && s.use_for.meta_inference);
      $('extWarnUsd').value = s.monthly_warn_usd != null ? s.monthly_warn_usd : ''; $('extLimitUsd').value = s.monthly_limit_usd != null ? s.monthly_limit_usd : '';
      var u = s.usage || {}; var line = _t('data.ext_usage_line', u.month || '', u.calls || 0, (u.input_tokens || 0).toLocaleString(), (u.output_tokens || 0).toLocaleString(), (u.usd || 0).toFixed(2));
      if (u.blocked) line += '<div style="color:var(--red);font-weight:600">' + _t('data.ext_usage_blocked', s.monthly_limit_usd) + '</div>';
      else if (u.warn) line += '<div style="color:var(--amber);font-weight:600">' + _t('data.ext_usage_warn', s.monthly_warn_usd) + '</div>';
      $('extUsage').innerHTML = line;
      $('extState').textContent = s.enabled ? _t('data.state_active') : _t('data.ext_off_note');
      msg(s.updated_at ? _t('data.ext_updated', s.updated_at) : '');
      syncProvider();
    }
    function payload() {
      return { enabled: $('extEnabled').checked, provider: $('extProvider').value, model: $('extModel').value.trim(),
        base_url: $('extBaseUrl').value.trim(), label: $('extLabel').value.trim(),
        api_key: $('extApiKey').value.trim() || (state.keySet ? KEEP : ''),
        monthly_warn_usd: $('extWarnUsd').value, monthly_limit_usd: $('extLimitUsd').value,
        use_for: { text_to_ts: $('extUseTextToTs').checked, ts_to_text: $('extUseTsToText').checked, meta_inference: $('extUseMeta').checked } };
    }
    function load() {
      fetch('/api/external_llm').then(function (r) { return r.json(); }).then(function (j) { if (j.settings) render(j.settings); });
    }
    $('extProvider').addEventListener('change', syncProvider);
    form.addEventListener('submit', function (e) {
      e.preventDefault(); msg('');
      fetch('/api/external_llm', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload()) })
        .then(function (r) { return r.json(); })
        .then(function (j) { if (j.error) { msg(j.error, true); return; } render(j.settings); msg(_t('data.ext_saved')); });
    });
    $('extTest').addEventListener('click', function () {
      msg(_t('data.ext_testing'));
      fetch('/api/external_llm/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload()) })
        .then(function (r) { return r.json(); })
        .then(function (j) { if (j.ok) msg(_t('data.ext_test_ok', j.reply, j.seconds)); else msg(_t('data.ext_test_fail', j.error || ''), true); });
    });
    $('extRemove').addEventListener('click', function () {
      if (!confirm(_t('data.ext_remove_confirm'))) return;
      fetch('/api/external_llm', { method: 'DELETE' }).then(function (r) { return r.json(); }).then(function (j) { render(j.settings); msg(_t('data.ext_removed')); });
    });
    load();
})();
