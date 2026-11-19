/* Campo de data do sistema (19/11/2026) — no lugar do <input type="date"> do aparelho (no iPhone abre o calendário
   do sistema, fora do padrão da tela; Anderson: "está feio… datas internas também").
   O <input type="date"> continua no formulário (é ele que vai no POST e que o JS da tela lê e escreve); fica
   escondido atrás de um botão que mostra dd/mm/aaaa e abre o nosso calendário: mês e ano em listas, setas,
   atalhos (Hoje, 1º do mês, 1º do mês que vem, Limpar), respeita min/max. Ao escolher, dispara input+change.

   QCData.ligar(input) · QCData.ligarTodos(raiz) · QCData.pintarTodos() (depois de mudar .value por código)        */
(function () {
  var MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];
  var MES3 = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
  var DIAS = ['D', 'S', 'T', 'Q', 'Q', 'S', 'S'], DIA_L = ['domingo', 'segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado'];
  var CSS = '.qcd { position:relative; display:inline-block; width:100%; }'
    + '.qcd > input[type=date] { position:absolute; left:0; top:0; width:1px; height:1px; opacity:0; pointer-events:none; }'
    + '.qcd-bt { display:flex; align-items:center; gap:9px; width:100%; min-height:40px; border:1px solid #d7e3dc; border-radius:10px; background:#fff; padding:0 8px 0 10px; font:inherit; font-size:13.5px; color:#0f172a; cursor:pointer; text-align:left; }'
    + '.qcd-bt:hover, .qcd.aberto .qcd-bt { border-color:#22c55e; } .qcd.aberto .qcd-bt { box-shadow:0 0 0 3px rgba(34,197,94,.14); }'
    + '.qcd-bt > i.fa-calendar-days { color:#16a34a; font-size:14px; } .qcd-bt .qcd-v { flex:1; min-width:0; display:flex; flex-direction:column; line-height:1.15; }'
    + '.qcd-bt .qcd-v b { font-weight:700; font-size:14px; } .qcd-bt .qcd-v small { color:#64748b; font-size:11px; } .qcd-bt.vazio .qcd-v b { color:#94a3b8; font-weight:500; }'
    + '.qcd-bt .qcd-x { border:0; background:#f1f5f9; color:#64748b; width:24px; height:24px; border-radius:7px; cursor:pointer; font-size:11px; display:inline-flex; align-items:center; justify-content:center; } .qcd-bt .qcd-x:hover { background:#fee2e2; color:#b91c1c; }'
    + '.qcd-fundo { position:fixed; inset:0; z-index:2000; }'
    + '.qcd-pop { position:fixed; z-index:2001; width:320px; background:#fff; border-radius:16px; box-shadow:0 24px 60px rgba(2,44,22,.28); border:1px solid #e6eeea; padding:12px; font-size:13.5px; color:#0f172a; animation:qcdPop .16s ease-out; }'
    + '@keyframes qcdPop { from { opacity:0; transform:translateY(6px); } }'
    + '.qcd-cab { display:flex; align-items:center; gap:4px; margin-bottom:8px; } .qcd-cab b { flex:1; text-align:center; font-size:14px; font-weight:800; text-transform:capitalize; } .qcd-cab b.qcd-ano { flex:0 0 52px; } .qcd-sep { width:1px; height:22px; background:#e6eeea; margin:0 4px; }'
    + '.qcd-cab button { width:34px; height:36px; border-radius:9px; border:1px solid #e2e8f0; background:#fff; color:#334155; cursor:pointer; flex:0 0 auto; }'
    + '.qcd-g { display:grid; grid-template-columns:repeat(7, 1fr); gap:2px; } .qcd-g span { text-align:center; font-size:10px; font-weight:800; color:#94a3b8; padding:2px 0 4px; }'
    + '.qcd-g button { height:36px; border-radius:9px; border:0; background:transparent; font:inherit; font-size:13.5px; font-weight:700; color:#0f172a; cursor:pointer; }'
    + '.qcd-g button:hover { background:#f0fdf4; } .qcd-g button.fds { color:#94a3b8; font-weight:600; } .qcd-g button.hoje { box-shadow:inset 0 0 0 1.5px #86efac; }'
    + '.qcd-g button:disabled { color:#cbd5e1; cursor:default; background:transparent; } .qcd-g button.on { background:#16a34a; color:#fff; box-shadow:0 4px 12px rgba(22,163,74,.35); }'
    + '.qcd-pe { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; padding-top:10px; border-top:1px dashed #e6eeea; }'
    + '.qcd-pe button { border:1px solid #e2e8f0; background:#f8fafc; color:#334155; border-radius:9px; padding:7px 10px; font:inherit; font-size:12.5px; font-weight:700; cursor:pointer; } .qcd-pe button:hover { border-color:#86efac; background:#f0fdf4; }'
    + '.qcd-pe button.limpar { color:#b91c1c; } .qcd-pe button.fechar { margin-left:auto; background:#fff; }'
    + '@media (max-width:640px) { .qcd-fundo { background:rgba(15,23,42,.45); } .qcd-pop { left:0 !important; right:0 !important; top:auto !important; bottom:0 !important; width:100%; border-radius:18px 18px 0 0; padding:14px 14px 18px; animation:qcdSobe .22s cubic-bezier(.2,.8,.2,1); }'
    + '  .qcd-g button { height:42px; font-size:15px; } .qcd-cab b { font-size:16px; } .qcd-cab button { height:42px; width:40px; } .qcd-pe button { padding:10px 12px; font-size:13.5px; } }'
    + '@keyframes qcdSobe { from { transform:translateY(24px); opacity:0; } }';
  var cssOk = false;
  function css() { if (cssOk) return; cssOk = true; var s = document.createElement('style'); s.textContent = CSS; document.head.appendChild(s); }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function iso(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function de(s) { if (!s || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return null; var p = s.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function br(s) { var d = de(s); return d ? pad(d.getDate()) + '/' + pad(d.getMonth() + 1) + '/' + d.getFullYear() : ''; }
  function hojeIso() { return iso(new Date()); }
  var aberto = null;

  function pintar(inp) {
    var w = inp._qcd; if (!w) return;
    var bt = w.querySelector('.qcd-bt'), v = inp.value, d = de(v);
    bt.classList.toggle('vazio', !d);
    bt.querySelector('.qcd-v b').textContent = d ? br(v) : (inp.dataset.vazio || (inp.required ? 'escolher a data' : 'em branco'));
    bt.querySelector('.qcd-v small').textContent = d ? DIA_L[d.getDay()] + ', ' + d.getDate() + ' de ' + MES3[d.getMonth()] + ' de ' + d.getFullYear() : (inp.dataset.dica || 'toque para escolher');
    var x = bt.querySelector('.qcd-x'); x.style.display = (d && !inp.required && !inp.disabled) ? '' : 'none';
    bt.disabled = inp.disabled;
  }
  function setar(inp, v) {
    if (inp.value === v) return;
    inp.value = v; pintar(inp);
    inp.dispatchEvent(new Event('input', { bubbles: true })); inp.dispatchEvent(new Event('change', { bubbles: true }));
  }
  function fechar() { if (!aberto) return; aberto.fundo.remove(); aberto.pop.remove(); aberto.wrap.classList.remove('aberto'); document.removeEventListener('keydown', tecla, true); aberto = null; }
  function tecla(e) { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); fechar(); } }

  function abrir(inp) {
    fechar();
    var w = inp._qcd, bt = w.querySelector('.qcd-bt');
    var atual = de(inp.value), hoje = new Date(), min = de(inp.min), max = de(inp.max);
    var vis = new Date((atual || (min && min > hoje ? min : hoje)).getFullYear(), (atual || (min && min > hoje ? min : hoje)).getMonth(), 1);
    var fundo = document.createElement('div'); fundo.className = 'qcd-fundo';
    var pop = document.createElement('div'); pop.className = 'qcd-pop'; pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-modal', 'true');
    document.body.appendChild(fundo); document.body.appendChild(pop);
    aberto = { fundo: fundo, pop: pop, wrap: w }; w.classList.add('aberto');
    fundo.addEventListener('mousedown', fechar); fundo.addEventListener('touchstart', fechar, { passive: true });
    document.addEventListener('keydown', tecla, true);
    function dentro(d) { return (!min || d >= min) && (!max || d <= max); }
    function montar() {
      var ano = vis.getFullYear(), mes = vis.getMonth();
      var h = '<div class="qcd-cab"><button type="button" data-m="-1" aria-label="Mês anterior"><i class="fas fa-chevron-left"></i></button><b class="qcd-mes">' + MESES[mes] + '</b><button type="button" data-m="1" aria-label="Mês seguinte"><i class="fas fa-chevron-right"></i></button>'
        + '<span class="qcd-sep"></span><button type="button" data-a="-1" aria-label="Ano anterior"><i class="fas fa-angles-left"></i></button><b class="qcd-ano">' + ano + '</b><button type="button" data-a="1" aria-label="Ano seguinte"><i class="fas fa-angles-right"></i></button></div><div class="qcd-g">';
      DIAS.forEach(function (x) { h += '<span>' + x + '</span>'; });
      var prim = new Date(ano, mes, 1).getDay(), n = new Date(ano, mes + 1, 0).getDate(), hj = hojeIso(), v = inp.value;
      for (var i = 0; i < prim; i++) h += '<button type="button" disabled></button>';
      for (var d = 1; d <= n; d++) {
        var dt = new Date(ano, mes, d), s = iso(dt), fds = dt.getDay() === 0 || dt.getDay() === 6;
        h += '<button type="button" data-d="' + s + '" class="' + (s === v ? 'on ' : '') + (fds ? 'fds ' : '') + (s === hj ? 'hoje' : '') + '"' + (dentro(dt) ? '' : ' disabled') + '>' + d + '</button>';
      }
      h += '</div><div class="qcd-pe">';
      var pm = new Date(hoje.getFullYear(), hoje.getMonth(), 1), pp = new Date(hoje.getFullYear(), hoje.getMonth() + 1, 1);
      if (dentro(hoje)) h += '<button type="button" data-d="' + hojeIso() + '">Hoje</button>';
      if (dentro(pm)) h += '<button type="button" data-d="' + iso(pm) + '">1º do mês</button>';
      if (dentro(pp)) h += '<button type="button" data-d="' + iso(pp) + '">1º do mês que vem</button>';
      if (inp.value && !inp.required) h += '<button type="button" class="limpar" data-d="">Limpar</button>';
      h += '<button type="button" class="fechar">Fechar</button></div>';
      pop.innerHTML = h;
      pop.querySelectorAll('.qcd-cab button').forEach(function (b) { b.addEventListener('click', function () { vis = new Date(vis.getFullYear() + (+b.dataset.a || 0), vis.getMonth() + (+b.dataset.m || 0), 1); montar(); }); });
      pop.querySelectorAll('button[data-d]').forEach(function (b) { b.addEventListener('click', function () { setar(inp, b.dataset.d); fechar(); }); });
      pop.querySelector('.fechar').addEventListener('click', fechar);
      posicionar();
    }
    function posicionar() {
      if (window.innerWidth <= 640) return;
      var r = bt.getBoundingClientRect(), ph = pop.offsetHeight || 380, pw = pop.offsetWidth || 320;
      var top = r.bottom + 6; if (top + ph > window.innerHeight - 8) top = Math.max(8, r.top - ph - 6);
      var left = Math.min(r.left, window.innerWidth - pw - 8);
      pop.style.top = top + 'px'; pop.style.left = Math.max(8, left) + 'px';
    }
    montar();
  }

  function ligar(inp) {
    if (!inp || inp._qcd || inp.type !== 'date') return;
    css();
    var w = document.createElement('span'); w.className = 'qcd';
    if (inp.style.width) { w.style.width = inp.style.width; w.style.display = 'inline-block'; inp.style.width = ''; }
    inp.parentNode.insertBefore(w, inp); w.appendChild(inp);
    var bt = document.createElement('button'); bt.type = 'button'; bt.className = 'qcd-bt ns-in-data';
    bt.innerHTML = '<i class="fas fa-calendar-days"></i><span class="qcd-v"><b></b><small></small></span><span class="qcd-x" role="button" title="Limpar"><i class="fas fa-xmark"></i></span>';
    w.appendChild(bt); inp._qcd = w;
    bt.addEventListener('click', function (e) { if (e.target.closest('.qcd-x')) { setar(inp, ''); return; } if (aberto && aberto.wrap === w) fechar(); else abrir(inp); });
    inp.addEventListener('change', function () { pintar(inp); });
    pintar(inp);
  }
  function ligarTodos(raiz) { (raiz || document).querySelectorAll('input[type=date]:not([data-nativo])').forEach(ligar); }
  function pintarTodos(raiz) { (raiz || document).querySelectorAll('input[type=date]').forEach(function (i) { if (i._qcd) pintar(i); }); }
  window.addEventListener('resize', function () { if (aberto) fechar(); });
  window.QCData = { ligar: ligar, ligarTodos: ligarTodos, pintarTodos: pintarTodos, fechar: fechar, br: br };
})();
