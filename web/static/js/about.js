/* ── 탭 전환 ── */
(function() {
  var tabs = document.querySelectorAll('.about-tab');
  var contents = document.querySelectorAll('.about-tab-content');
  tabs.forEach(function(tab) {
    tab.addEventListener('click', function() {
      var target = this.getAttribute('data-tab');
      tabs.forEach(function(t) { t.classList.remove('active'); });
      contents.forEach(function(c) { c.classList.remove('active'); });
      this.classList.add('active');
      var el = document.getElementById(target);
      if (el) el.classList.add('active');
      // Canvas 리드로우 (탭 전환 후 크기 계산)
      setTimeout(function() {
        if (target === 'tab-overview') {
          var activeSub = document.querySelector('.sub-tab-content.active');
          if (activeSub && activeSub.id === 'sub-why') { drawWhyCharts(); drawWhyPreprocess(); }
        }
        if (target === 'tab-reference') { drawPatternCards(); }
        if (target === 'tab-data-prep') { drawFeatCharts(); }
        if (target === 'tab-search') { drawNtsDemo(); }
      }, 80);
    });
  });
})();

/* ── 서브 탭 전환 (그룹별 독립 동작) ── */
(function() {
  document.querySelectorAll('.sub-tabs').forEach(function(group) {
    var tabs = group.querySelectorAll('.sub-tab');
    // 같은 부모 tab-content 안의 sub-tab-content만 대상
    var parent = group.closest('.about-tab-content');
    var contents = parent ? parent.querySelectorAll(':scope > .sub-tab-content, :scope > .sub-tabs-wrap ~ .sub-tab-content') : [];
    // fallback: 해당 그룹의 data-subtab 타겟들
    var targetIds = [];
    tabs.forEach(function(t) { targetIds.push(t.getAttribute('data-subtab')); });

    tabs.forEach(function(tab) {
      tab.addEventListener('click', function() {
        var target = this.getAttribute('data-subtab');
        // 같은 그룹 내 탭만 토글
        tabs.forEach(function(t) { t.classList.remove('active'); });
        this.classList.add('active');
        // 같은 그룹의 콘텐츠만 토글
        targetIds.forEach(function(id) {
          var el = document.getElementById(id);
          if (el) el.classList.remove('active');
        });
        var el = document.getElementById(target);
        if (el) el.classList.add('active');
        // 캔버스 리드로우 (서브탭 전환 시)
        setTimeout(function() {
          if (target === 'sub-why') { drawWhyCharts(); drawWhyPreprocess(); }
          if (target === 'sub-ref-example') { drawPatternCards(); }
          if (target === 'sub-dp-analysis') { drawFeatCharts(); }
          if (target === 'sub-search-modes') { drawNtsDemo(); }
        }, 80);
      });
    });
  });
})();

/* ── Canvas 공통 유틸 ── */
function getCanvasWidth(canvas, extraPad) {
  if (!canvas || !canvas.parentElement) return 0;
  // offsetWidth가 0이면 숨겨진 상태
  if (canvas.offsetWidth === 0 && canvas.offsetHeight === 0) return 0;
  var parent = canvas.parentElement;
  var style = getComputedStyle(parent);
  var w = parent.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - (extraPad || 2);
  return Math.max(0, w);
}

/* ── 기능 미니 차트 ── */
function drawFeatCharts() {
  function setup(id, H) {
    var c = document.getElementById(id);
    if (!c) return null;
    var W = getCanvasWidth(c);
    if (W < 50) return null;
    var dpr = window.devicePixelRatio || 1;
    c.width = W * dpr; c.height = H * dpr;
    c.style.width = W + 'px'; c.style.height = H + 'px';
    var ctx = c.getContext('2d');
    ctx.scale(dpr, dpr);
    return { ctx: ctx, W: W, H: H };
  }

  // 02: 멀티컬럼 — 3개 라인 겹침 + 가중치 바
  (function() {
    var s = setup('featMultiCol', 56);
    if (!s) return;
    var ctx = s.ctx, W = s.W, H = s.H;
    var N = 24, pad = 4;
    var power = [], irr = [], temp = [];
    for (var i = 0; i < N; i++) {
      var solar = i >= 6 && i <= 18 ? Math.sin((i - 6) * Math.PI / 12) : 0;
      power.push(solar * 80 + (Math.sin(i * 2.3) * 3));
      irr.push(solar * 900 + (Math.sin(i * 3.1) * 30));
      temp.push(15 + solar * 18 + (i > 12 ? 3 : 0)); // 온도 피크 살짝 늦음
    }
    var cw = W - pad * 2, ch = H - pad * 2 - 10;
    var toX = function(i) { return pad + (i / (N - 1)) * cw; };

    // 배경 밴드 (낮 시간)
    ctx.fillStyle = 'rgba(5,150,105,.04)';
    ctx.fillRect(toX(6), pad, toX(18) - toX(6), ch);

    // Line 1: power (green, 굵은)
    ctx.beginPath(); ctx.strokeStyle = '#059669'; ctx.lineWidth = 2.5;
    power.forEach(function(v, i) {
      var y = pad + ch * (1 - v / 90);
      i === 0 ? ctx.moveTo(toX(i), y) : ctx.lineTo(toX(i), y);
    }); ctx.stroke();

    // Line 2: irradiance (blue, 점선)
    ctx.beginPath(); ctx.strokeStyle = '#2563EB'; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
    irr.forEach(function(v, i) {
      var y = pad + ch * (1 - v / 1000);
      i === 0 ? ctx.moveTo(toX(i), y) : ctx.lineTo(toX(i), y);
    }); ctx.stroke(); ctx.setLineDash([]);

    // Line 3: temp (amber, 가는)
    ctx.beginPath(); ctx.strokeStyle = '#D97706'; ctx.lineWidth = 1;
    temp.forEach(function(v, i) {
      var y = pad + ch * (1 - (v - 10) / 30);
      i === 0 ? ctx.moveTo(toX(i), y) : ctx.lineTo(toX(i), y);
    }); ctx.stroke();

    // 하단 가중치 바
    var barY = H - 8, barH = 4;
    var weights = [{w:0.6,col:'#059669',lbl:'power 60%'},{w:0.3,col:'#2563EB',lbl:'irrad 30%'},{w:0.1,col:'#D97706',lbl:'temp 10%'}];
    var bx = pad;
    weights.forEach(function(wt) {
      var bw = cw * wt.w;
      ctx.fillStyle = wt.col + '40'; ctx.fillRect(bx, barY, bw, barH);
      ctx.fillStyle = wt.col; ctx.fillRect(bx, barY, bw, 1.5);
      ctx.font = '7px "DM Mono",monospace'; ctx.fillStyle = wt.col;
      ctx.fillText(wt.lbl, bx + 2, barY - 2);
      bx += bw;
    });
  })();

  // 01: 분할 데모 — 1주일 태양광 데이터를 여러 단위로 분할
  (function() {
    // 1주일(168pt) 태양광 데이터 생성: 일출~일몰 종 모양 + 날씨 변동
    var N = 168;
    var fullData = [];
    var dayWeather = [1.0, 0.95, 0.4, 0.7, 1.0, 0.85, 0.6]; // 7일 날씨 계수
    for (var i = 0; i < N; i++) {
      var day = Math.floor(i / 24), hour = i % 24;
      var base = hour >= 6 && hour <= 18 ? Math.sin((hour - 6) * Math.PI / 12) : 0;
      var noise = (Math.sin(i * 3.7) * 0.15 + Math.sin(i * 7.1) * 0.08); // 결정론적 노이즈
      fullData.push(base * dayWeather[day] + noise * dayWeather[day]);
    }
    var segColors = ['#059669', '#2563EB', '#D97706', '#7C3AED', '#0891B2', '#DC2626', '#6366F1'];

    // 원본 전체 시계열
    var sSource = setup('splitDemoSource', 50);
    if (sSource) {
      var ctx = sSource.ctx, W = sSource.W, H = sSource.H;
      var pad = 4, cw = W - pad * 2, ch = H - pad * 2;
      var mn = -0.2, mx = 1.1;
      var toX = function(i) { return pad + (i / (N - 1)) * cw; };
      var toY = function(v) { return pad + ch * (1 - (v - mn) / (mx - mn)); };
      // 일별 배경
      for (var d = 0; d < 7; d++) {
        ctx.fillStyle = segColors[d] + '08';
        var x0 = toX(d * 24), x1 = d < 6 ? toX((d + 1) * 24) : pad + cw;
        ctx.fillRect(x0, pad, x1 - x0, ch);
      }
      // 라인
      ctx.beginPath(); ctx.strokeStyle = '#374151'; ctx.lineWidth = 1.5;
      fullData.forEach(function(v, i) { i === 0 ? ctx.moveTo(toX(i), toY(v)) : ctx.lineTo(toX(i), toY(v)); });
      ctx.stroke();
      // 일별 경계선
      for (var d = 1; d < 7; d++) {
        ctx.beginPath(); ctx.strokeStyle = 'rgba(100,116,139,.2)'; ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]); ctx.moveTo(toX(d * 24), pad); ctx.lineTo(toX(d * 24), pad + ch); ctx.stroke(); ctx.setLineDash([]);
      }
      ctx.font = '7px "DM Mono",monospace'; ctx.fillStyle = 'rgba(100,116,139,.5)';
      for (var d = 0; d < 7; d++) ctx.fillText('D' + (d + 1), toX(d * 24) + 3, H - 2);
    }

    // 카드 그리기 헬퍼
    function drawSplitCard(canvasId, segLen, H) {
      var s = setup(canvasId, H);
      if (!s) return;
      var ctx = s.ctx, W = s.W, H2 = s.H;
      var pad = 3, cw = W - pad * 2, ch = H2 - pad * 2;
      var mn = -0.2, mx = 1.1;
      var numSegs = Math.ceil(N / segLen);
      var segW = cw / numSegs;
      var gapPx = Math.min(3, segW * 0.08);

      for (var si = 0; si < numSegs; si++) {
        var s0 = si * segLen, s1 = Math.min(s0 + segLen, N);
        var len = s1 - s0;
        var col = segColors[si % segColors.length];
        var areaX = pad + si * segW;
        var drawW = segW - gapPx;

        // 배경
        ctx.fillStyle = col + '0C';
        ctx.fillRect(areaX, pad, drawW, ch);

        // 세그먼트 라인
        ctx.beginPath(); ctx.strokeStyle = col; ctx.lineWidth = 1.5;
        for (var j = 0; j < len; j++) {
          var x = areaX + (j / Math.max(len - 1, 1)) * drawW;
          var y = pad + ch * (1 - (fullData[s0 + j] - mn) / (mx - mn));
          j === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
    }

    drawSplitCard('splitDemo6h', 6, 48);
    drawSplitCard('splitDemo1d', 24, 48);
    drawSplitCard('splitDemo2d', 48, 48);
    drawSplitCard('splitDemoFull', 168, 48);
  })();

  // 03: Z-score — 왼: 겨울50kW vs 여름100kW, 오른: 정규화 후 겹침
  (function() {
    var s = setup('featZscore', 56);
    if (!s) return;
    var ctx = s.ctx, W = s.W, H = s.H;
    var N = 24, pad = 4, cw = W - pad * 2, ch = H - pad * 2;
    var halfW = (cw - 50) / 2;
    // 겨울(낮은 발전) vs 여름(높은 발전) — 같은 종 모양이지만 스케일 다름
    function solarDay(peak, noise) {
      var d = [];
      for (var h = 0; h < N; h++) {
        var base = h >= 6 && h <= 18 ? peak * Math.sin((h - 6) * Math.PI / 12) : peak * 0.01;
        d.push(base + Math.sin(h * noise) * peak * 0.06);
      }
      return d;
    }
    var winter = solarDay(50, 2.3), summer = solarDay(100, 3.1);
    var toXL = function(i) { return pad + (i / (N - 1)) * halfW; };
    var toYL = function(v) { return pad + ch * (1 - v / 110); };

    // 왼쪽 배경
    ctx.fillStyle = 'rgba(100,116,139,.03)'; ctx.fillRect(pad, pad, halfW, ch);
    // 겨울 (amber)
    ctx.beginPath(); ctx.strokeStyle = '#D97706'; ctx.lineWidth = 2;
    winter.forEach(function(v, i) { i === 0 ? ctx.moveTo(toXL(i), toYL(v)) : ctx.lineTo(toXL(i), toYL(v)); }); ctx.stroke();
    // 여름 (green)
    ctx.beginPath(); ctx.strokeStyle = '#059669'; ctx.lineWidth = 2;
    summer.forEach(function(v, i) { i === 0 ? ctx.moveTo(toXL(i), toYL(v)) : ctx.lineTo(toXL(i), toYL(v)); }); ctx.stroke();
    // 라벨 + 거리 표시
    ctx.font = '8px "DM Mono",monospace';
    ctx.fillStyle = '#D97706'; ctx.fillText('50kW', pad + 2, pad + 9);
    ctx.fillStyle = '#059669'; ctx.fillText('100kW', pad + 2, pad + 18);
    // X 표시 (다른 패턴으로 판정)
    ctx.fillStyle = '#DC2626'; ctx.font = 'bold 11px "DM Sans",sans-serif';
    ctx.fillText('X', pad + halfW - 12, pad + 12);

    // 화살표 영역
    var arrowX = pad + halfW + 4;
    ctx.fillStyle = 'rgba(100,116,139,.4)'; ctx.font = '8px "DM Sans",sans-serif';
    ctx.fillText('z-score', arrowX, H / 2 - 2);
    ctx.fillText('  →', arrowX + 2, H / 2 + 10);

    // 오른쪽: 정규화 후 겹침
    var off = pad + halfW + 50;
    ctx.fillStyle = 'rgba(5,150,105,.04)'; ctx.fillRect(off, pad, halfW, ch);
    var toXR = function(i) { return off + (i / (N - 1)) * halfW; };
    var toYR = function(v) { return pad + ch * (0.5 - v / 5); };
    var norm = []; for (var h = 0; h < N; h++) norm.push(h >= 6 && h <= 18 ? 2 * Math.sin((h - 6) * Math.PI / 12) : -0.1);
    // 두 라인이 거의 완벽히 겹침
    ctx.beginPath(); ctx.strokeStyle = '#D97706'; ctx.lineWidth = 2.5; ctx.globalAlpha = 0.5;
    norm.forEach(function(v, i) { i === 0 ? ctx.moveTo(toXR(i), toYR(v + 0.05)) : ctx.lineTo(toXR(i), toYR(v + 0.05)); }); ctx.stroke();
    ctx.beginPath(); ctx.strokeStyle = '#059669'; ctx.lineWidth = 2; ctx.globalAlpha = 0.7;
    norm.forEach(function(v, i) { i === 0 ? ctx.moveTo(toXR(i), toYR(v)) : ctx.lineTo(toXR(i), toYR(v)); }); ctx.stroke();
    ctx.globalAlpha = 1;
    // O 표시 (같은 패턴)
    ctx.fillStyle = '#059669'; ctx.font = 'bold 11px "DM Sans",sans-serif';
    ctx.fillText('O', off + halfW - 10, pad + 12);
    ctx.font = '8px "DM Mono",monospace'; ctx.fillStyle = '#059669';
    ctx.fillText('same shape', off + 2, pad + 9);
  })();

  // 04: 결과 필터링 — 유사도 바 + threshold + Top-K 브래킷
  (function() {
    var s = setup('featFilter', 48);
    if (!s) return;
    var ctx = s.ctx, W = s.W, H = s.H;
    var pad = 4, cw = W - pad * 2, ch = H - pad * 2 - 4;
    var sims = [96, 93, 91, 87, 84, 80, 76, 72, 68, 63, 58, 51, 44, 35, 28];
    var barW = Math.min(20, (cw / sims.length) - 3);
    var gap = (cw - barW * sims.length) / (sims.length + 1);

    // threshold 선 (70%)
    var thY = pad + ch * (1 - 0.7);
    ctx.beginPath(); ctx.strokeStyle = '#DC2626'; ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]); ctx.moveTo(pad, thY); ctx.lineTo(pad + cw, thY); ctx.stroke(); ctx.setLineDash([]);
    ctx.font = '7px "DM Mono",monospace'; ctx.fillStyle = '#DC2626'; ctx.fillText('threshold 70%', pad + cw - 56, thY - 3);

    // Top-K 범위 (상위 5개) 배경
    var topK = 5;
    var topKEndX = pad + gap + topK * (barW + gap) - gap / 2;
    ctx.fillStyle = 'rgba(5,150,105,.06)';
    ctx.fillRect(pad, pad, topKEndX - pad, ch + 4);

    sims.forEach(function(sim, i) {
      var x = pad + gap + i * (barW + gap);
      var bh = ch * (sim / 100);
      var above = sim >= 70;
      var isTopK = i < topK;
      // 바 배경
      ctx.fillStyle = isTopK ? '#059669' + '35' : (above ? 'rgba(5,150,105,.12)' : 'rgba(100,116,139,.08)');
      ctx.fillRect(x, pad + ch - bh, barW, bh);
      // 바 상단 강조
      ctx.fillStyle = isTopK ? '#059669' : (above ? 'rgba(5,150,105,.4)' : 'rgba(100,116,139,.2)');
      ctx.fillRect(x, pad + ch - bh, barW, 2);
      // 유사도 숫자 (Top-K만)
      if (isTopK) {
        ctx.font = '6px "DM Mono",monospace'; ctx.fillStyle = '#059669'; ctx.textAlign = 'center';
        ctx.fillText(sim + '%', x + barW / 2, pad + ch - bh - 2);
        ctx.textAlign = 'left';
      }
    });
    // Top-K 라벨
    ctx.fillStyle = '#059669'; ctx.font = '7px "DM Mono",monospace';
    ctx.fillText('Top-5', pad + 2, H - 1);
  })();

  // 05: 캘린더 정렬 — 자정 경계 + 비정렬 vs 정렬 비교
  (function() {
    var s = setup('featCalendar', 48);
    if (!s) return;
    var ctx = s.ctx, W = s.W, H = s.H;
    var pad = 4, cw = W - pad * 2, ch = H - pad * 2;
    var N = 96; // 4일분 (24pt x 4)
    var weather = [1.0, 0.4, 0.9, 0.7];
    var data = [];
    for (var i = 0; i < N; i++) {
      var d = Math.floor(i / 24), h = i % 24;
      var base = h >= 6 && h <= 18 ? Math.sin((h - 6) * Math.PI / 12) : 0;
      data.push(base * weather[d] * 6 + Math.sin(i * 2.7) * 0.3);
    }
    var toX = function(i) { return pad + (i / (N - 1)) * cw; };
    var toY = function(v) { return pad + ch * (1 - (v + 0.5) / 7); };

    // 일별 교대 배경 + 경계선
    var dayColors = ['rgba(37,99,235,.06)', 'rgba(37,99,235,.02)'];
    var dayLabels = ['Mon 00:00', 'Tue 00:00', 'Wed 00:00', 'Thu 00:00'];
    for (var d = 0; d < 4; d++) {
      var x0 = toX(d * 24), x1 = d < 3 ? toX((d + 1) * 24) : pad + cw;
      ctx.fillStyle = dayColors[d % 2]; ctx.fillRect(x0, pad, x1 - x0, ch);
      // 경계선
      ctx.beginPath(); ctx.strokeStyle = '#2563EB'; ctx.lineWidth = 1;
      ctx.setLineDash([3, 2]); ctx.moveTo(x0, pad); ctx.lineTo(x0, pad + ch); ctx.stroke(); ctx.setLineDash([]);
      ctx.font = '7px "DM Mono",monospace'; ctx.fillStyle = '#2563EB';
      ctx.fillText(dayLabels[d], x0 + 2, H - 1);
    }
    // 데이터 라인
    ctx.beginPath(); ctx.strokeStyle = '#374151'; ctx.lineWidth = 1.5;
    data.forEach(function(v, i) { i === 0 ? ctx.moveTo(toX(i), toY(v)) : ctx.lineTo(toX(i), toY(v)); });
    ctx.stroke();
    // 쿼리 하이라이트 (Day 1 구간)
    ctx.fillStyle = 'rgba(5,150,105,.12)';
    ctx.fillRect(toX(0), pad, toX(24) - toX(0), ch);
    ctx.font = '7px "DM Mono",monospace'; ctx.fillStyle = '#059669';
    ctx.fillText('query', toX(2), pad + 9);
  })();

  // 08: FFT — 주기성 바 차트
  (function() {
    var s = setup('featFFT', 56);
    if (!s) return;
    var ctx = s.ctx, W = s.W, H = s.H;
    var pad = 6, cw = W - pad * 2, ch = H - pad * 2 - 14;
    var bars = [
      { label: '24h', val: 0.92, color: '#059669' },
      { label: '168h', val: 0.34, color: '#2563EB' },
      { label: '12h', val: 0.18, color: '#D97706' },
      { label: '8h', val: 0.08, color: 'rgba(100,116,139,.4)' },
    ];
    var barW = Math.min(40, (cw / bars.length) - 12);
    var gap = (cw - barW * bars.length) / (bars.length + 1);
    bars.forEach(function(b, i) {
      var x = pad + gap + i * (barW + gap);
      var bh = ch * b.val;
      ctx.fillStyle = b.color + '20'; ctx.fillRect(x, pad + ch - bh, barW, bh);
      ctx.fillStyle = b.color; ctx.fillRect(x, pad + ch - bh, barW, 3);
      ctx.font = '8px "DM Mono",monospace'; ctx.fillStyle = b.color; ctx.textAlign = 'center';
      ctx.fillText(b.label, x + barW / 2, H - 2);
      ctx.fillText(Math.round(b.val * 100) + '%', x + barW / 2, pad + ch - bh - 3);
    });
    ctx.textAlign = 'left';
  })();

  // 06: 불연속 세그먼트 — 3개 세그먼트 + gap
  (function() {
    var s = setup('featDiscont', 56);
    if (!s) return;
    var ctx = s.ctx, W = s.W, H = s.H;
    var pad = 4, cw = W - pad * 2, ch = H - pad * 2;
    var segs = [
      { start: 0, len: 18, color: '#059669' },
      { start: 24, len: 15, color: '#2563EB' },
      { start: 45, len: 20, color: '#D97706' }
    ];
    var total = 70;
    segs.forEach(function(seg) {
      var data = [];
      for (var i = 0; i < seg.len; i++) data.push(Math.sin(i * 0.4 + seg.start * 0.1) * 4 + (Math.random() - 0.5));
      var x0 = pad + (seg.start / total) * cw;
      var segW = (seg.len / total) * cw;
      // 배경
      ctx.fillStyle = seg.color + '08';
      ctx.fillRect(x0, pad, segW, ch);
      // 라인
      ctx.beginPath(); ctx.strokeStyle = seg.color; ctx.lineWidth = 2;
      data.forEach(function(v, i) {
        var x = x0 + (i / (seg.len - 1)) * segW;
        var y = pad + ch * (0.5 - v / 12);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }); ctx.stroke();
    });
    // gap 라벨
    ctx.font = '8px "DM Mono",monospace'; ctx.fillStyle = 'rgba(100,116,139,.4)';
    ctx.fillText('gap', pad + (20 / total) * cw, H / 2);
    ctx.fillText('gap', pad + (41 / total) * cw, H / 2);
  })();
}
drawFeatCharts();

/* ── WHY 카드 미니 차트 ── */
function drawWhyCharts() {
  // Card 1: 긴 시계열에서 매칭 구간 강조
  (function() {
    var c = document.getElementById('whyChart1');
    if (!c) return;
    var dpr = window.devicePixelRatio || 1;
    var W = getCanvasWidth(c, 44); // why-num 공간 확보
    if (W < 50) return;
    var H = 48;
    c.width = W * dpr; c.height = H * dpr;
    c.style.width = W + 'px'; c.style.height = H + 'px';
    var ctx = c.getContext('2d');
    ctx.scale(dpr, dpr);
    var N = 120;
    var data = [];
    for (var i = 0; i < N; i++) {
      data.push(Math.sin(i * 0.15) * 4 + Math.sin(i * 0.4) * 2 + (Math.random() - 0.5) * 1.5);
    }
    var pad = 4, cw = W - pad * 2, ch = H - pad * 2;
    var mn = -8, mx = 8;
    var toX = function(i) { return pad + (i / (N - 1)) * cw; };
    var toY = function(v) { return pad + ch * (1 - (v - mn) / (mx - mn)); };

    // 전체 데이터 (연한 회색)
    ctx.beginPath(); ctx.strokeStyle = 'rgba(100,116,139,.3)'; ctx.lineWidth = 1;
    data.forEach(function(v, i) { i === 0 ? ctx.moveTo(toX(i), toY(v)) : ctx.lineTo(toX(i), toY(v)); });
    ctx.stroke();

    // 매칭 구간 3곳 강조
    var matches = [[15, 28], [52, 65], [88, 101]];
    matches.forEach(function(m) {
      ctx.beginPath(); ctx.strokeStyle = '#059669'; ctx.lineWidth = 2.5;
      for (var j = m[0]; j <= m[1]; j++) {
        j === m[0] ? ctx.moveTo(toX(j), toY(data[j])) : ctx.lineTo(toX(j), toY(data[j]));
      }
      ctx.stroke();
      // 하이라이트 배경
      ctx.fillStyle = 'rgba(5,150,105,.08)';
      ctx.fillRect(toX(m[0]), pad, toX(m[1]) - toX(m[0]), ch);
    });
  })();

  // Card 2: 같은 형태 다른 스케일 → Z-score로 통일
  (function() {
    var c = document.getElementById('whyChart2');
    if (!c) return;
    var dpr = window.devicePixelRatio || 1;
    var W = getCanvasWidth(c, 44);
    if (W < 50) return;
    var H = 48;
    c.width = W * dpr; c.height = H * dpr;
    c.style.width = W + 'px'; c.style.height = H + 'px';
    var ctx = c.getContext('2d');
    ctx.scale(dpr, dpr);

    var N = 24;
    var pad = 4, cw = W - pad * 2, ch = H - pad * 2;
    var halfW = cw / 2 - 8;

    // 왼쪽: 원본 (50kW vs 100kW)
    function bellData(scale) {
      var d = [];
      for (var h = 0; h < N; h++) {
        d.push(h >= 6 && h <= 18 ? scale * Math.sin((h - 6) * Math.PI / 12) : 0);
      }
      return d;
    }
    var lo = bellData(50), hi = bellData(100);
    var toXL = function(i) { return pad + (i / (N - 1)) * halfW; };
    var toYL = function(v) { return pad + ch * (1 - v / 110); };

    ctx.beginPath(); ctx.strokeStyle = '#D97706'; ctx.lineWidth = 1.5;
    lo.forEach(function(v, i) { i === 0 ? ctx.moveTo(toXL(i), toYL(v)) : ctx.lineTo(toXL(i), toYL(v)); });
    ctx.stroke();
    ctx.beginPath(); ctx.strokeStyle = '#059669'; ctx.lineWidth = 1.5;
    hi.forEach(function(v, i) { i === 0 ? ctx.moveTo(toXL(i), toYL(v)) : ctx.lineTo(toXL(i), toYL(v)); });
    ctx.stroke();

    // 중앙 화살표
    var mid = pad + halfW + 8;
    ctx.fillStyle = 'var(--text3, #94a3b8)'; ctx.font = '10px "DM Sans",sans-serif';
    ctx.fillText('z-score →', mid - 2, H / 2 + 3);

    // 오른쪽: 정규화 후 (겹침)
    var off = pad + halfW + 36;
    var toXR = function(i) { return off + (i / (N - 1)) * halfW; };
    var toYR = function(v) { return pad + ch * (1 - (v + 0.2) / 2.4); };
    var norm = [];
    for (var h = 0; h < N; h++) {
      norm.push(h >= 6 && h <= 18 ? 2 * Math.sin((h - 6) * Math.PI / 12) : -0.2);
    }
    ctx.beginPath(); ctx.strokeStyle = '#D97706'; ctx.lineWidth = 1.5; ctx.setLineDash([3, 2]);
    norm.forEach(function(v, i) { i === 0 ? ctx.moveTo(toXR(i), toYR(v)) : ctx.lineTo(toXR(i), toYR(v)); });
    ctx.stroke(); ctx.setLineDash([]);
    ctx.beginPath(); ctx.strokeStyle = '#059669'; ctx.lineWidth = 1.5;
    norm.forEach(function(v, i) { i === 0 ? ctx.moveTo(toXR(i), toYR(v)) : ctx.lineTo(toXR(i), toYR(v)); });
    ctx.stroke();
  })();

  // Card 3a: Text → Shape ("서서히 올라가다 중간에서 뚝 떨어지는")
  function drawBidirCanvas(id, data, color, fillColor) {
    var c = document.getElementById(id);
    if (!c) return;
    var dpr = window.devicePixelRatio || 1;
    var W = 80, H = 36;
    c.width = W * dpr; c.height = H * dpr;
    c.style.width = W + 'px'; c.style.height = H + 'px';
    var ctx = c.getContext('2d');
    ctx.scale(dpr, dpr);
    var N = data.length;
    var mn = Math.min.apply(null, data), mx = Math.max.apply(null, data);
    var rng = mx - mn || 1;
    var pad = 3, cw = W - pad * 2, ch = H - pad * 2;
    var toX = function(i) { return pad + (i / (N - 1)) * cw; };
    var toY = function(v) { return pad + ch * (1 - (v - mn) / rng); };
    ctx.beginPath();
    ctx.moveTo(toX(0), toY(mn));
    data.forEach(function(v, i) { ctx.lineTo(toX(i), toY(v)); });
    ctx.lineTo(toX(N - 1), toY(mn));
    ctx.closePath();
    ctx.fillStyle = fillColor; ctx.fill();
    ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 2;
    data.forEach(function(v, i) { i === 0 ? ctx.moveTo(toX(i), toY(v)) : ctx.lineTo(toX(i), toY(v)); });
    ctx.stroke();
  }
  drawBidirCanvas('whyChart3a',
    [-1, -0.6, -0.1, 0.3, 0.7, 1.0, 1.1, 1.2, 0.2, -0.8, -1.4, -1.6],
    '#059669', 'rgba(5,150,105,.12)');
  // Card 3b: Shape → Text (일교차 주기 패턴)
  drawBidirCanvas('whyChart3b',
    [0.2, -0.3, -0.8, -1, -0.7, 0.1, 0.8, 1.2, 1.0, 0.4, -0.2, -0.6, -0.9, -0.5, 0.3, 1.0, 1.4, 1.1, 0.5],
    '#2563EB', 'rgba(37,99,235,.1)');

  // Card 6: 다양한 형태 — datetime vs step 기반 나란히
  (function() {
    var c = document.getElementById('whyChart6');
    if (!c) return;
    var dpr = window.devicePixelRatio || 1;
    var W = getCanvasWidth(c, 44);
    if (W < 50) return;
    var H = 48;
    c.width = W * dpr; c.height = H * dpr;
    c.style.width = W + 'px'; c.style.height = H + 'px';
    var ctx = c.getContext('2d');
    ctx.scale(dpr, dpr);
    var pad = 4, halfW = (W - pad * 2) / 2 - 6, ch = H - pad * 2;

    // 왼쪽: datetime 기반 (종 모양)
    var d1 = [];
    for (var i = 0; i < 24; i++) d1.push(i >= 6 && i <= 18 ? 8 * Math.sin((i - 6) * Math.PI / 12) : 0.2);
    ctx.beginPath(); ctx.strokeStyle = '#059669'; ctx.lineWidth = 1.5;
    d1.forEach(function(v, i) {
      var x = pad + (i / 23) * halfW;
      var y = pad + ch * (1 - v / 10);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }); ctx.stroke();
    ctx.font = '7px "DM Mono",monospace'; ctx.fillStyle = '#059669'; ctx.fillText('datetime', pad + 2, pad + 8);

    // 오른쪽: step 기반 (계단형)
    var off = pad + halfW + 12;
    var d2 = [];
    for (var i = 0; i < 20; i++) d2.push(3 + Math.floor(i / 5) * 2 + (Math.random() - 0.5) * 0.5);
    ctx.beginPath(); ctx.strokeStyle = '#2563EB'; ctx.lineWidth = 1.5;
    d2.forEach(function(v, i) {
      var x = off + (i / 19) * halfW;
      var y = pad + ch * (1 - v / 12);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }); ctx.stroke();
    ctx.fillStyle = '#2563EB'; ctx.fillText('step', off + 2, pad + 8);

    // 구분선
    var mx = pad + halfW + 6;
    ctx.beginPath(); ctx.strokeStyle = 'rgba(100,116,139,.2)'; ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]); ctx.moveTo(mx, pad); ctx.lineTo(mx, pad + ch); ctx.stroke(); ctx.setLineDash([]);
  })();
}
drawWhyCharts();

/* ── WHY 카드: 프리셋 미니 차트 ── */
(function() {
  var presets = {
    whyPreset1: function(n) { var d=[]; for(var i=0;i<n;i++) d.push(i/(n-1)); return d; },           // rise
    whyPreset2: function(n) { var d=[]; for(var i=0;i<n;i++) d.push(Math.sin(i*Math.PI/(n-1))); return d; }, // bell
    whyPreset3: function(n) { var d=[]; for(var i=0;i<n;i++) d.push(-Math.sin(i*Math.PI/(n-1))); return d; }, // dip
    whyPreset4: function(n) { var d=[]; for(var i=0;i<n;i++) d.push(Math.abs(2*i/(n-1)-1)); return d; }  // v_shape
  };
  Object.keys(presets).forEach(function(id) {
    var c = document.getElementById(id);
    if (!c) return;
    var dpr = window.devicePixelRatio||1;
    var W = c.parentElement ? c.parentElement.clientWidth / 4 - 4 : 40;
    W = Math.max(20, W);
    var H = 28;
    c.width = W*dpr; c.height = H*dpr;
    c.style.width = W+'px'; c.style.height = H+'px';
    c.style.borderRadius = '4px';
    c.style.background = 'var(--bg2)';
    c.style.border = '1px solid rgba(100,116,139,.1)';
    c.style.display = 'block';
    c.style.width = '100%';
    c.style.boxSizing = 'border-box';
    W = c.offsetWidth || W;
    c.width = W*dpr; c.height = H*dpr;
    c.style.width = W+'px';
    var ctx = c.getContext('2d');
    ctx.scale(dpr, dpr);
    var data = presets[id](16);
    var mn = Math.min.apply(null, data), mx = Math.max.apply(null, data);
    var rng = mx - mn || 1;
    var pad = 3;
    ctx.beginPath(); ctx.strokeStyle = '#059669'; ctx.lineWidth = 1.5;
    data.forEach(function(v, i) {
      var x = pad + (i/(data.length-1))*(W-pad*2);
      var y = pad + (H-pad*2)*(1-(v-mn)/rng);
      i===0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
    }); ctx.stroke();
  });
})();

/* ── WHY 카드: 컬럼별 shape 미니 차트 ── */
(function() {
  function drawColMini(id, dataFn, color) {
    var c = document.getElementById(id);
    if (!c) return;
    var dpr = window.devicePixelRatio||1;
    var W = c.offsetWidth || 60; var H = 32;
    c.width = W*dpr; c.height = H*dpr;
    c.style.width = W+'px'; c.style.height = H+'px';
    c.style.borderRadius = '4px';
    c.style.background = 'var(--bg2)';
    c.style.border = '1px solid rgba(100,116,139,.08)';
    c.style.display = 'block'; c.style.boxSizing = 'border-box';
    var ctx = c.getContext('2d');
    ctx.scale(dpr, dpr);
    var data = dataFn();
    var mn = Math.min.apply(null, data), mx = Math.max.apply(null, data);
    var rng = mx - mn || 1; var pad = 2;
    ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 1.5;
    data.forEach(function(v,i) {
      var x = pad+(i/(data.length-1))*(W-pad*2);
      var y = pad+(H-pad*2)*(1-(v-mn)/rng);
      i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
    }); ctx.stroke();
  }
  // 24pt 1일 패턴
  drawColMini('whyColTemp', function() {
    var d=[]; for(var h=0;h<24;h++) d.push(22+8*Math.sin((h-5)*Math.PI/12-Math.PI/2)); return d;
  }, '#059669');
  drawColMini('whyColPower', function() {
    var d=[]; for(var h=0;h<24;h++) d.push(h>=6&&h<=18?80*Math.sin((h-6)*Math.PI/12):0); return d;
  }, '#D97706');
  drawColMini('whyColHumid', function() {
    var d=[]; for(var h=0;h<24;h++) d.push(60+Math.sin(h*1.3)*1.5); return d;
  }, '#7C3AED');
})();

/* ── WHY 카드: 전처리 전/후 차트 — 탭이 열릴 때마다 다시 그린다(숨겨진 채 로드되면 폭 0) ── */
function drawWhyPreprocess() {
  var c = document.getElementById('whyChartPreprocess');
  if (!c) return;
  var dpr = window.devicePixelRatio||1;
  var W = getCanvasWidth(c, 44);
  if (W < 50) return;
  var H = 48;
  c.width = W*dpr; c.height = H*dpr;
  c.style.width = W+'px'; c.style.height = H+'px';
  var ctx = c.getContext('2d');
  ctx.scale(dpr, dpr);
  var N = 48, pad = 4, cw = W-pad*2, ch = H-pad*2;
  // 원본 (결측 + 이상치 포함)
  var raw = [], clean = [];
  for (var i = 0; i < N; i++) {
    var base = Math.sin(i*0.3)*3 + Math.sin(i*0.08)*4;
    raw.push(base);
    clean.push(base);
  }
  // NaN 구간 (10~13)
  raw[10] = NaN; raw[11] = NaN; raw[12] = NaN;
  // 이상치 (25, 38)
  raw[25] = 12; raw[38] = -10;
  // clean 보간
  clean[10] = (clean[9]+clean[13])/2; clean[11] = clean[10]; clean[12] = (clean[10]+clean[13])/2;
  clean[25] = (clean[24]+clean[26])/2; clean[38] = (clean[37]+clean[39])/2;

  var all = raw.concat(clean).filter(function(v){return v===v;});
  var mn = Math.min.apply(null, all)-1, mx = Math.max.apply(null, all)+1;
  var toX = function(i) { return pad+(i/(N-1))*cw; };
  var toY = function(v) { return pad+ch*(1-(v-mn)/(mx-mn)); };
  // 원본 (red, dotted)
  ctx.beginPath(); ctx.strokeStyle='rgba(220,38,38,.4)'; ctx.lineWidth=1.5; ctx.setLineDash([3,2]);
  raw.forEach(function(v,i){if(v!==v)return; i===0?ctx.moveTo(toX(i),toY(v)):ctx.lineTo(toX(i),toY(v));}); ctx.stroke(); ctx.setLineDash([]);
  // NaN 강조
  ctx.fillStyle='rgba(220,38,38,.08)'; ctx.fillRect(toX(9),pad,toX(14)-toX(9),ch);
  // 이상치 강조
  [25,38].forEach(function(idx){
    ctx.beginPath(); ctx.arc(toX(idx),toY(raw[idx]),3,0,Math.PI*2); ctx.fillStyle='#DC2626'; ctx.fill();
  });
  // clean (green)
  ctx.beginPath(); ctx.strokeStyle='#059669'; ctx.lineWidth=2;
  clean.forEach(function(v,i){i===0?ctx.moveTo(toX(i),toY(v)):ctx.lineTo(toX(i),toY(v));}); ctx.stroke();
  // 라벨
  ctx.font='7px "DM Mono",monospace';
  ctx.fillStyle='rgba(220,38,38,.5)'; ctx.fillText('NaN', toX(10), pad+9);
  ctx.fillStyle='#059669'; ctx.fillText('cleaned', pad+2, pad+9);
}
drawWhyPreprocess();

/* ── NL-to-Shape 데모 차트 ── */
function drawNtsDemo() {
  var canvas = document.getElementById('ntsShapeDemo');
  if (!canvas) return;
  var W = getCanvasWidth(canvas);
  if (W < 50) return;
  var dpr = window.devicePixelRatio || 1;
  var H = 70;
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  var ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  // "서서히 올라가다 중간에서 갑자기 떨어지는" shape
  var N = 24;
  var data = [];
  for (var h = 0; h < N; h++) {
    var v = 0;
    if (h >= 6 && h <= 18) v = 12 * Math.sin((h - 6) * Math.PI / 12);
    // 14시 이후 급락 → 회복 없이 낮게 유지
    if (h >= 14) v = Math.max(0, v * 0.08 + 0.3);
    data.push(Math.max(0, v));
  }

  var pad = { t: 6, r: 6, b: 6, l: 6 };
  var cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
  var mx = 14;
  var toX = function(i) { return pad.l + (i / (N - 1)) * cw; };
  var toY = function(v) { return pad.t + ch * (1 - v / mx); };

  // 영역
  ctx.beginPath();
  ctx.moveTo(toX(0), toY(0));
  data.forEach(function(v, i) { ctx.lineTo(toX(i), toY(v)); });
  ctx.lineTo(toX(N - 1), toY(0));
  ctx.closePath();
  ctx.fillStyle = 'rgba(5,150,105,.1)';
  ctx.fill();

  // 정상 구간 (green)
  ctx.beginPath(); ctx.strokeStyle = '#059669'; ctx.lineWidth = 2.5;
  for (var i = 0; i <= 13; i++) i === 0 ? ctx.moveTo(toX(i), toY(data[i])) : ctx.lineTo(toX(i), toY(data[i]));
  ctx.stroke();

  // 급락 구간 (red)
  ctx.beginPath(); ctx.strokeStyle = '#DC2626'; ctx.lineWidth = 2.5;
  ctx.moveTo(toX(13), toY(data[13]));
  for (var i = 14; i < N; i++) ctx.lineTo(toX(i), toY(data[i]));
  ctx.stroke();

  // 라벨
  ctx.font = '8px "DM Sans", sans-serif';
  ctx.fillStyle = '#059669'; ctx.fillText('rising', toX(9), toY(data[9]) - 5);
  ctx.fillStyle = '#DC2626'; ctx.fillText('drop', toX(15), toY(data[14]) - 5);
}
drawNtsDemo();

window.addEventListener('resize', function() {
  drawWhyCharts(); drawWhyPreprocess(); drawPatternCards();
  drawFeatCharts();
  drawNtsDemo();
});


/* ── 참고문헌 탭: 대표 패턴 카드(.pc-chart) — data-pattern 별 합성 파형 ── */
function drawPatternCards() {
  var gen = {
    clear:      function(i, n) { var x = i / (n - 1); return Math.max(0, Math.sin(Math.PI * x)); },                          // 맑은 날 종 모양
    cloudy:     function(i, n) { var x = i / (n - 1); return Math.max(0, Math.sin(Math.PI * x)) * (0.45 + 0.35 * Math.sin(i * 1.7) * Math.cos(i * 0.9)); },
    partial:    function(i, n) { var x = i / (n - 1); var b = Math.max(0, Math.sin(Math.PI * x)); return (i % 11 < 4) ? b * 0.35 : b; },   // 부분 구름 — 간헐 하락
    shadow:     function(i, n) { var x = i / (n - 1); var b = Math.max(0, Math.sin(Math.PI * x)); return (x > 0.55 && x < 0.7) ? b * 0.25 : b; }, // 그림자 — 한 번 깊은 골
    wind_rated: function(i, n) { var x = i / (n - 1); return Math.min(1, 0.2 + 1.6 * x) * (0.96 + 0.04 * Math.sin(i * 2.3)); },               // 정격 도달 뒤 평탄
    wind_ramp:  function(i, n) { var x = i / (n - 1); return x < 0.4 ? 0.15 + 0.05 * Math.sin(i) : Math.min(1, 0.15 + (x - 0.4) * 2.4); },     // 급상승
    wind_calm:  function(i, n) { return 0.08 + 0.06 * Math.abs(Math.sin(i * 1.3)); }                                                            // 무풍 — 낮게 깔림
  };
  document.querySelectorAll('canvas.pc-chart').forEach(function (c) {
    var f = gen[c.dataset.pattern]; if (!f) return;
    var W = getCanvasWidth(c), H = parseInt(c.getAttribute('height'), 10) || 60;
    if (W < 40) return;
    var dpr = window.devicePixelRatio || 1;
    c.width = W * dpr; c.height = H * dpr; c.style.width = W + 'px'; c.style.height = H + 'px';
    var ctx = c.getContext('2d'); ctx.scale(dpr, dpr);
    var n = 48, pad = 4, cw = W - pad * 2, ch = H - pad * 2;
    var color = /^wind/.test(c.dataset.pattern) ? '#0F766E' : '#D97706';
    ctx.beginPath();
    for (var i = 0; i < n; i++) {
      var x = pad + cw * i / (n - 1), y = pad + ch * (1 - f(i, n));
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.lineJoin = 'round'; ctx.stroke();
    ctx.lineTo(pad + cw, pad + ch); ctx.lineTo(pad, pad + ch); ctx.closePath();
    ctx.fillStyle = color + '22'; ctx.fill();
  });
}
drawPatternCards();
