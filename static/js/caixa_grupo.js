/* Caixa de escolha do Grupo (19/11/2026) — a mesma caixa da ficha do cliente (templates/clientes/detalhes.html,
   abrirCaixa, ramo "grupo"), agora reaproveitável: a janela do contrato de honorários a usa no lugar do <select>.
   CSS: templates/clientes/_pk_css.html. Dados: /clientes/api/caixa-opcoes?qual=grupo. Criar: /clientes/api/grupo-rapido.

   CaixaGrupo.abrir({ atual: '12' | '', empresa: 'NOME', pode_criar: true, url_opcoes, url_novo, z: 1300,
                      onUsar: function (id, nome) {} })                                                            */
(function () {
  var PK_CORES = ['#0f766e', '#4f46e5', '#b45309', '#be123c', '#7c3aed', '#0369a1', '#15803d', '#c2410c'];
  var CACHE = null;
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function pkCor(s) { var h = 0; String(s).split('').forEach(function (c) { h = (h * 31 + c.charCodeAt(0)) >>> 0; }); return PK_CORES[h % 8]; }

  function abrir(o) {
    o = o || {};
    var sel = String(o.atual || ''), orig = sel, dados = null, novoAberto = false, pode = !!o.pode_criar;
    var f = document.createElement('div'); f.className = 'pk-fundo pk-sobre';
    if (o.z) f.style.zIndex = o.z;
    f.innerHTML = '<div class="pk pk-verde" role="dialog" aria-modal="true" aria-label="Grupo da empresa">'
      + '<header class="pk-cab"><span class="pk-av"><i class="fas fa-layer-group"></i></span>'
      + '<div class="pk-quem"><b>' + esc(o.titulo || 'Grupo da empresa') + '</b><span>' + esc(o.empresa || '') + '</span></div>'
      + '<div class="pk-val"><span class="pk-tag">escolhido</span><b class="pk-val-b"></b><small class="pk-val-s"></small></div>'
      + '<button type="button" class="pk-x" title="Fechar (Esc)"><i class="fas fa-xmark"></i></button></header>'
      + '<div class="pk-kpis"></div>'
      + '<div class="pk-barra"><div class="pk-busca"><i class="fas fa-search"></i><input type="search" name="q" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" inputmode="search" enterkeyhint="search" placeholder="Buscar pelo nome do grupo, número ou nome de uma empresa…"></div></div>'
      + '<div class="pk-corpo"><div class="pk-grade"><div class="pk-carregando"><i class="fas fa-spinner fa-spin"></i> Carregando…</div></div></div>'
      + '<footer class="pk-pe"><div class="pk-pe-info"></div><button type="button" class="pk-canc">Cancelar</button>'
      + '<button type="button" class="pk-sem"><i class="fas fa-ban"></i> Sem grupo</button>'
      + '<button type="button" class="pk-ok"><i class="fas fa-check"></i> Usar este grupo</button></footer></div>';
    document.body.appendChild(f);
    var trava = document.body.style.overflow; document.body.style.overflow = 'hidden';
    var $q = function (s) { return f.querySelector(s); };
    var busca = $q('.pk-busca input'), grade = $q('.pk-grade');
    function fechar() { document.removeEventListener('keydown', tecla, true); f.remove(); document.body.style.overflow = trava; }
    function tecla(e) { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); if (novoAberto) { novoAberto = false; desenhar(); } else fechar(); } }
    document.addEventListener('keydown', tecla, true);
    function porId(id) { return (dados || []).filter(function (x) { return x.id === String(id); })[0]; }
    function usar(id) { var g = porId(id); fechar(); if (o.onUsar) o.onUsar(id ? String(id) : '', g ? g.nome : ''); }
    function cabecalho() {
      var g = porId(sel), vb = $q('.pk-val-b'), vs = $q('.pk-val-s'), info = $q('.pk-pe-info');
      vb.textContent = g ? g.nome : 'sem grupo';
      vs.textContent = g ? g.n + (g.n === 1 ? ' empresa' : ' empresas') : 'escolha um cartão';
      var mudou = sel !== orig;
      info.innerHTML = orig && !mudou ? '<i class="fas fa-check"></i> Este já é o grupo da empresa.' : (mudou && orig ? '<i class="fas fa-circle-info"></i> Trocar aqui troca também no cadastro da empresa.' : '');
      $q('.pk-ok').disabled = !sel || !mudou;
      $q('.pk-sem').style.display = orig ? '' : 'none';
    }
    function kpis() {
      var atual = porId(orig), tot = (dados || []).reduce(function (s, x) { return s + x.n; }, 0);
      $q('.pk-kpis').innerHTML = '<div><i class="fas fa-layer-group"></i><div><dt>Grupo atual</dt><dd>' + esc(atual ? atual.nome : 'sem grupo') + '</dd><small>' + (atual ? atual.n + ' empresas' : 'falta escolher') + '</small></div></div>'
        + '<div><i class="fas fa-object-group"></i><div><dt>Grupos ativos</dt><dd>' + (dados || []).length + '</dd><small>para escolher</small></div></div>'
        + '<div><i class="fas fa-building"></i><div><dt>Empresas em grupos</dt><dd>' + tot + '</dd><small>somando todos</small></div></div>';
    }
    function cartaoGrupo(g) {
      var on = sel === g.id, atual = orig === g.id;
      return '<button type="button" class="pk-card' + (on ? ' on' : '') + '" data-id="' + g.id + '">'
        + '<span class="pk-ck"><i class="fas fa-check"></i></span>'
        + '<div class="pk-card-h"><span class="pk-sig" style="background:' + pkCor(g.nome) + '">' + esc(g.sigla) + '</span>'
        + '<div><b>' + esc(g.nome) + '</b><small>' + g.n + (g.n === 1 ? ' empresa' : ' empresas') + (atual ? ' · <em>atual</em>' : '') + '</small></div></div>'
        + (g.cidades.length ? '<div class="pk-cids">' + g.cidades.map(function (c) { return '<span><i class="fas fa-location-dot"></i> ' + esc(c) + '</span>'; }).join('') + (g.mais_cid ? '<span>+' + g.mais_cid + '</span>' : '') + '</div>' : '')
        + (g.empresas.length ? '<ul class="pk-emps">' + g.empresas.map(function (e) { return '<li><span>' + esc(e.num || '—') + '</span>' + esc(e.nome) + '</li>'; }).join('') + (g.mais_emp ? '<li class="pk-mais">e mais ' + g.mais_emp + '</li>' : '') + '</ul>' : '<div class="pk-vazio-g">Grupo ainda sem empresas.</div>')
        + '</button>';
    }
    function cartaoNovo() {
      if (!pode) return '';
      if (!novoAberto) return '<button type="button" class="pk-card pk-novo-c"><i class="fas fa-plus"></i><b>Novo grupo</b><small>' + (busca.value.trim() ? 'criar "' + esc(busca.value.trim()) + '"' : 'não achou? crie aqui') + '</small></button>';
      return '<div class="pk-card pk-novo-f"><b><i class="fas fa-plus"></i> Novo grupo</b>'
        + '<input type="text" class="pk-novo-nome" maxlength="100" placeholder="Nome do grupo" value="' + esc(busca.value.trim()) + '">'
        + '<div class="pk-novo-bt"><button type="button" class="pk-canc pk-novo-x">Voltar</button><button type="button" class="pk-ok pk-novo-ok">Criar</button></div><div class="pk-msg"></div></div>';
    }
    function desenhar() {
      if (!dados) return;
      var q = busca.value.trim().toLowerCase();
      var vis = dados.filter(function (x) { return !q || String(x.busca || x.nome).toLowerCase().indexOf(q) >= 0; });
      vis.sort(function (a, b) {
        var pa = a.id === orig ? 0 : 1, pb = b.id === orig ? 0 : 1;
        if (pa !== pb) return pa - pb;
        return String(a.nome).localeCompare(String(b.nome), 'pt-BR');
      });
      grade.innerHTML = vis.map(cartaoGrupo).join('') + cartaoNovo() + (!vis.length && !pode ? '<div class="pk-carregando">Nada encontrado.</div>' : '');
      grade.querySelectorAll('.pk-card[data-id]').forEach(function (c) {
        c.addEventListener('click', function () { sel = c.dataset.id; desenhar(); cabecalho(); });
        c.addEventListener('dblclick', function () { sel = c.dataset.id; if (sel !== orig) usar(sel); });
      });
      var nc = grade.querySelector('.pk-novo-c'); if (nc) nc.addEventListener('click', function () { novoAberto = true; desenhar(); });
      var nf = grade.querySelector('.pk-novo-f');
      if (nf) {
        var ni = nf.querySelector('.pk-novo-nome'); ni.focus(); ni.select();
        ni.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); criar(nf); } });
        nf.querySelector('.pk-novo-x').addEventListener('click', function () { novoAberto = false; desenhar(); });
        nf.querySelector('.pk-novo-ok').addEventListener('click', function () { criar(nf); });
        nf.scrollIntoView({ block: 'nearest' });
      }
    }
    function criar(nf) {
      var nome = nf.querySelector('.pk-novo-nome').value.trim().replace(/\s+/g, ' '), msg = nf.querySelector('.pk-msg');
      if (!nome) { msg.textContent = 'Digite o nome.'; return; }
      var bt = nf.querySelector('.pk-novo-ok'); bt.disabled = true; bt.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
      fetch(o.url_novo, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nome: nome }) })
        .then(function (r) { return r.json(); }).then(function (j) {
          bt.disabled = false; bt.textContent = 'Criar';
          if (!j.success) { msg.textContent = j.message || 'Não foi criado.'; return; }
          var id = String(j.id);
          if (!porId(id)) { dados.push({ id: id, nome: j.nome, sigla: String(j.nome).slice(0, 2).toUpperCase(), n: 0, cidades: [], mais_cid: 0, empresas: [], mais_emp: 0, busca: j.nome }); CACHE = null; }
          novoAberto = false; busca.value = ''; sel = id;
          desenhar(); cabecalho(); kpis();
          $q('.pk-pe-info').innerHTML = '<i class="fas fa-check"></i> ' + (j.ja_existia ? 'Já existia e foi escolhido: ' : 'Criado e escolhido: ') + esc(j.nome) + '. Confirme em "Usar este grupo".';
        }).catch(function () { bt.disabled = false; bt.textContent = 'Criar'; msg.textContent = 'Sem conexão. Tente de novo.'; });
    }
    function comecar(lista) { dados = lista; CACHE = lista; kpis(); cabecalho(); desenhar(); if (!('ontouchstart' in window)) busca.focus(); }
    busca.addEventListener('input', function () { if (novoAberto) novoAberto = false; desenhar(); });
    busca.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return; e.preventDefault();
      var p = grade.querySelectorAll('.pk-card[data-id]'); if (p.length === 1) p[0].click();
    });
    $q('.pk-x').addEventListener('click', fechar);
    $q('.pk-canc').addEventListener('click', fechar);
    $q('.pk-sem').addEventListener('click', function () { usar(''); });
    $q('.pk-ok').addEventListener('click', function () { usar(sel); });
    f.addEventListener('mousedown', function (e) { if (e.target === f) fechar(); });
    kpis(); cabecalho();
    if (CACHE) { comecar(CACHE); return; }
    fetch(o.url_opcoes).then(function (r) { if (!r.ok) throw 0; return r.json(); }).then(comecar)
      .catch(function () { comecar((o.reserva || []).map(function (g) { return { id: String(g.id), nome: g.nome, sigla: String(g.nome).slice(0, 2).toUpperCase(), n: 0, cidades: [], mais_cid: 0, empresas: [], mais_emp: 0, busca: g.nome }; })); });
  }
  window.CaixaGrupo = { abrir: abrir, limparCache: function () { CACHE = null; } };
})();
