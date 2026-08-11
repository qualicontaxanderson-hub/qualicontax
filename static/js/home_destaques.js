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

    // Card tipo LISTA: ranking rolante. Cada linha = posição, quantidade (à
    // direita, tabular), rótulo (com reticências) e, atrás, barra proporcional
    // ao 1º colocado. NÃO desenha sparkline — a lista já é a visualização.
    function listCardHTML(c) {
        var itens = c.itens || [];
        var max = 0;
        itens.forEach(function (it) { if (typeof it.valor === 'number' && it.valor > max) max = it.valor; });
        var rows = itens.map(function (it, i) {
            var isNum = typeof it.valor === 'number';
            var val = isNum ? nfmt(it.valor) : esc(it.valor);
            var bar = (it.barra != null) ? (it.barra * 100)
                : (isNum && max > 0 ? (it.valor / max) * 100 : 0);
            return '<div class="ef-cc-lrow">' +
                '<span class="ef-cc-lbar" style="width:' + bar.toFixed(1) + '%"></span>' +
                '<span class="ef-cc-lpos">' + (i + 1) + 'º</span>' +
                '<span class="ef-cc-lval">' + val + '</span>' +
                '<span class="ef-cc-lrot">' + esc(it.rotulo) + '</span>' +
                '</div>';
        }).join('');
        return '<div class="ef-car-card is-lista">' +
            '<div class="ef-cc-top"><span class="ef-cc-titulo"><i class="fas ' + esc(c.icone) + '"></i>' +
                esc(c.titulo) + '</span>' + pill(c.trend) + '</div>' +
            '<div class="ef-cc-lviewport"><div class="ef-cc-lista">' + rows + '</div></div>' +
            '</div>';
    }

    function cardHTML(c) {
        if (c && c.tipo === 'lista') return listCardHTML(c);
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
    // Cards têm larguras diferentes (272px número, 300px lista): navego pelo
    // offsetLeft real de cada card, não por uma largura única.
    function nearestIdx() {
        if (!track) return 0;
        var sl = track.scrollLeft, best = 0, bd = Infinity, ch = track.children;
        for (var i = 0; i < ch.length; i++) {
            var d = Math.abs(ch[i].offsetLeft - sl);
            if (d < bd) { bd = d; best = i; }
        }
        return best;
    }
    function setDot(i) { if (dots) dots.forEach(function (d, ix) { d.classList.toggle('on', ix === i); }); }
    function go(i) { idx = i; var c = track.children[i]; track.scrollTo({ left: c ? c.offsetLeft : 0, behavior: 'smooth' }); setDot(i); }
    function syncDot() { if (!track) return; var i = nearestIdx(); if (i !== idx) { idx = i; setDot(i); } }
    function pauseAWhile() { paused = true; if (resumeT) clearTimeout(resumeT); resumeT = setTimeout(function () { paused = false; }, 8000); }
    function startAuto(n) { if (timer) clearInterval(timer); if (n <= 1) return; timer = setInterval(function () { if (!paused) go((idx + 1) % n); }, 4000); }

    // Auto-scroll VERTICAL dos cards LISTA: janela de 5 linhas, sobe de página em
    // página (~4s), fase escalonada por card; pausa no hover; parada quando
    // prefers-reduced-motion está ligado ou a lista tem <= 5 itens.
    function setupListas() {
        var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        var PER = 5;
        Array.prototype.forEach.call(carEl.querySelectorAll('.ef-car-card.is-lista'), function (card, ci) {
            var lista = card.querySelector('.ef-cc-lista');
            var vp = card.querySelector('.ef-cc-lviewport');
            if (!lista || !vp || !lista.children.length) return;
            var rowH = lista.children[0].offsetHeight;
            var n = lista.children.length, visN = Math.min(n, PER);
            vp.style.height = (rowH * visN) + 'px';   // janela de até 5 linhas
            if (reduce || n <= PER) return;           // parada
            var pages = Math.ceil(n / PER), maxOff = (n - PER) * rowH, pg = 0, hover = false;
            card.addEventListener('mouseenter', function () { hover = true; });
            card.addEventListener('mouseleave', function () { hover = false; });
            setTimeout(function () {
                setInterval(function () {
                    if (hover) return;
                    pg = (pg + 1) % pages;
                    lista.style.transform = 'translateY(-' + Math.min(pg * PER * rowH, maxOff) + 'px)';
                }, 4000);
            }, ci * 1300);   // fase diferente por card
        });
    }

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
        setupListas();
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
