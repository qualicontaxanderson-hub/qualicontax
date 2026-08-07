/* ===========================================================================
   Carrossel de destaques + contadores da HOME DE DEPARTAMENTO.
   Extraído de escrita_fiscal/index.html (Bloco C3) para ser compartilhado com
   /cadastros/ em vez de copiado. Comportamento idêntico ao original.

   A home já está pintada quando isto roda; o script só busca os números e
   enfeita. Se falhar, ou se o dado vier velho (>5min), o carrossel some e a
   home fica exatamente como estava — melhor omitir que mentir.

   Uso no template:
     <div class="ef-car-skel" id="efCarSkel" aria-hidden="true">...</div>
     <div class="ef-carousel" id="efCarousel" hidden
          data-endpoint="{{ url_for('...api_home_destaques') }}"></div>
   O endpoint sai do data-endpoint; nada de URL fixa aqui dentro.
   =========================================================================== */
(function () {
    var carEl = document.getElementById('efCarousel');
    var skelEl = document.getElementById('efCarSkel');
    if (!carEl) return;
    var endpoint = carEl.getAttribute('data-endpoint');
    if (!endpoint) return;
    var STALE_MS = 5 * 60 * 1000;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
        });
    }
    function nfmt(n) { return Number(n || 0).toLocaleString('pt-BR'); }

    // Sparkline SVG desenhado no cliente a partir do array (sem lib externa).
    function sparkSVG(arr, tipo) {
        arr = (arr && arr.length) ? arr : [0];
        var W = 240, H = 34, n = arr.length;
        var max = Math.max.apply(null, arr), min = Math.min.apply(null, arr);
        var rng = (max - min) || 1;
        if (tipo === 'barra') {
            var gap = W / n, bw = gap * 0.6;
            var bars = arr.map(function (v, i) {
                var h = max > 0 ? (v / max) * (H - 2) : 0;
                var x = i * gap + (gap - bw) / 2;
                return '<rect x="' + x.toFixed(1) + '" y="' + (H - Math.max(h, 1)).toFixed(1) +
                       '" width="' + bw.toFixed(1) + '" height="' + Math.max(h, 1).toFixed(1) +
                       '" rx="1.5" fill="#22c55e"></rect>';
            }).join('');
            return '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">' + bars + '</svg>';
        }
        var pts = arr.map(function (v, i) {
            var x = n > 1 ? (i / (n - 1)) * W : 0;
            var y = H - ((v - min) / rng) * (H - 4) - 2;
            return x.toFixed(1) + ',' + y.toFixed(1);
        });
        var area = 'M0,' + H + ' L' + pts.join(' L') + ' L' + W + ',' + H + ' Z';
        return '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">' +
               '<path d="' + area + '" fill="rgba(34,197,94,.14)"></path>' +
               '<polyline points="' + pts.join(' ') + '" fill="none" stroke="#16a34a" ' +
               'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></polyline></svg>';
    }

    function pill(t) {
        t = t || { tipo: 'neutro' };
        var txt;
        if (t.tipo === 'alta') txt = '▲ ' + Math.abs(t.pct) + '%';
        else if (t.tipo === 'baixa') txt = '▼ ' + Math.abs(t.pct) + '%';
        else if (t.tipo === 'igual') txt = '0%';
        else txt = t.rotulo || 'mês';
        return '<span class="ef-cc-pill ef-pill-' + (t.tipo || 'neutro') + '">' + esc(txt) + '</span>';
    }

    function cardHTML(c) {
        var suf = c.valor_sufixo ? '<small>' + esc(c.valor_sufixo) + '</small>' : '';
        // Sem série real não desenha sparkline: uma linha reta inventada é
        // decoração que passa por dado. Card sem spark simplesmente não tem.
        var spark = (c.spark && c.spark.length)
            ? '<div class="ef-cc-spark">' + sparkSVG(c.spark, c.spark_tipo) + '</div>' : '';
        return '<div class="ef-car-card">' +
            '<div class="ef-cc-top"><span class="ef-cc-titulo"><i class="fas ' + esc(c.icone) + '"></i>' +
                esc(c.titulo) + '</span>' + pill(c.trend) + '</div>' +
            '<div class="ef-cc-valor">' + nfmt(c.valor) + suf + '</div>' +
            '<div class="ef-cc-apoio">' + esc(c.apoio) + '</div>' +
            spark +
            '</div>';
    }

    function fillCounters(counters) {
        if (!counters) return;
        document.querySelectorAll('[data-counter]').forEach(function (el) {
            var k = el.getAttribute('data-counter');
            if (counters[k] != null) {
                el.textContent = (typeof counters[k] === 'number') ? nfmt(counters[k]) : counters[k];
            }
        });
    }

    var track, dots, timer = null, idx = 0, paused = false, resumeT = null;
    function cardW() { var c = track && track.querySelector('.ef-car-card'); return c ? c.offsetWidth + 14 : 286; }
    function setDot(i) { if (dots) dots.forEach(function (d, ix) { d.classList.toggle('on', ix === i); }); }
    function go(i) { idx = i; track.scrollTo({ left: i * cardW(), behavior: 'smooth' }); setDot(i); }
    function syncDot() { if (!track) return; var i = Math.round(track.scrollLeft / cardW()); if (i !== idx) { idx = i; setDot(i); } }
    function pauseAWhile() { paused = true; if (resumeT) clearTimeout(resumeT); resumeT = setTimeout(function () { paused = false; }, 8000); }
    function startAuto(n) { if (timer) clearInterval(timer); if (n <= 1) return; timer = setInterval(function () { if (!paused) go((idx + 1) % n); }, 4000); }

    function render(cards) {
        carEl.innerHTML = '<div class="ef-car-track" id="efCarTrack">' + cards.map(cardHTML).join('') +
            '</div><div class="ef-car-dots" id="efCarDots"></div>';
        track = document.getElementById('efCarTrack');
        var dotsEl = document.getElementById('efCarDots');
        dotsEl.innerHTML = cards.map(function (_, ix) { return '<span class="ef-dot' + (ix === 0 ? ' on' : '') + '" data-ix="' + ix + '"></span>'; }).join('');
        dots = Array.prototype.slice.call(dotsEl.children);
        dots.forEach(function (d) { d.addEventListener('click', function () { go(parseInt(d.getAttribute('data-ix'), 10)); pauseAWhile(); }); });
        track.addEventListener('scroll', syncDot, { passive: true });
        // auto-avanço PAUSA quando o usuário toca/arrasta/rola a faixa
        ['pointerdown', 'touchstart', 'wheel'].forEach(function (ev) { track.addEventListener(ev, pauseAWhile, { passive: true }); });
        carEl.hidden = false;
        startAuto(cards.length);
    }

    fetch(endpoint, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
        .then(function (data) {
            if (skelEl) skelEl.remove();
            fillCounters(data.counters);
            var fresco = data.gerado_em_ms && (Date.now() - data.gerado_em_ms) <= STALE_MS;
            if (fresco && data.cards && data.cards.length) render(data.cards);
            // stale (>5min) ou sem cards → não mostra o carrossel; a home segue como está
        })
        .catch(function () { if (skelEl) skelEl.remove(); });
})();
