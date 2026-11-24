/* Caixa de escolha da EMPRESA (cliente) — 19/11/2026, Anderson: "feio para escolher o cliente". Mesmo molde pk-* das
   caixas de Grupo e de Serviço: busca por nome, número ou CNPJ/CPF, cartões com a sigla colorida, número, documento e a
   mensalidade (quando tem contrato), "Usar esta empresa". A lista vem pronta da tela (D.clientes), sem ir ao servidor.

   24/11/2026 (Anderson: "dê uma melhorada nesse campo de pesquisa"): a busca casa o COMEÇO das palavras ("AR" acha
   AR TRANSPORTES e ARAGARÇAS, não MARCIANO nem APARECIDA); cada palavra digitada tem que começar alguma palavra do nome,
   da fantasia, da cidade, do número ou do documento; números casam em qualquer posição do CNPJ/CPF. Quem começa pelo que
   foi digitado vem primeiro. E a tela pode mandar `fora`: empresas que NÃO podem ser escolhidas (ex.: em A CADASTRAR),
   mostradas apagadas, com o motivo e o link para resolver, só quando a busca bate com elas — responde na hora
   "por que a empresa não aparece?".

   CaixaCliente.abrir({ clientes: [{id, num, nome, doc, av, mensal, fant?, cid?}], atual: '149', titulo, sub, permite_vazio: true,
                        rotulo_vazio: 'A própria empresa', fora: [{id, num, nome, doc, av, motivo, url?}], rotulo_fora: 'em A CADASTRAR',
                        onUsar: function (cliente|null) {} })                                                                 */
(function () {
  var PK_CORES = ['#0f766e', '#4f46e5', '#b45309', '#be123c', '#7c3aed', '#0369a1', '#15803d', '#c2410c'];
  var LIMITE = 80, LIMITE_FORA = 8;
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function cor(s) { var h = 0; String(s).split('').forEach(function (c) { h = (h * 31 + c.charCodeAt(0)) >>> 0; }); return PK_CORES[h % 8]; }
  function sigla(nome) { var p = String(nome || '').replace(/["'`´]+/g, ' ').trim().split(/\s+/).filter(function (x) { return x.length > 2 && !/\d/.test(x) && !/^(LTDA|EIRELI|ME|EPP|SA|S\/A|DE|DA|DO|DOS|DAS|E)$/i.test(x); }); return (((p[0] || '')[0] || '') + ((p[1] || '')[0] || '')).toUpperCase() || '?'; }
  function docf(d) { d = String(d || '').replace(/\D/g, ''); return d.length === 14 ? d.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, '$1.$2.$3/$4-$5') : d.length === 11 ? d.replace(/^(\d{3})(\d{3})(\d{3})(\d{2})$/, '$1.$2.$3-$4') : d; }
  function brl(v) { return 'R$ ' + Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function norm(s) { return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); }
  function palavras(s) { return norm(s).split(/[^a-z0-9]+/).filter(Boolean); }
  function prep(c) {
    return { id: String(c.id), num: String(c.num || ''), nome: c.nome || '', doc: String(c.doc || ''), av: !!c.av, mensal: Number(c.mensal || 0),
      motivo: c.motivo || '', url: c.url || '', nomeN: norm(c.nome || ''), digitos: String(c.num || '') + ' ' + String(c.doc || '').replace(/\D/g, ''),
      pal: palavras((c.num || '') + ' ' + (c.nome || '') + ' ' + (c.fant || '') + ' ' + (c.cid || '') + ' ' + (c.doc || '')) };
  }
  // cada palavra digitada começa alguma palavra da empresa; só números casam em qualquer posição do número/documento
  function casa(x, toks, qd, soNumero) {
    if (soNumero) return x.digitos.indexOf(qd) >= 0;
    if (!toks.length) return true;
    if (qd.length >= 3 && x.digitos.indexOf(qd) >= 0) return true;
    return toks.every(function (t) { return x.pal.some(function (p) { return p.indexOf(t) === 0; }); });
  }
  function ordem(sel, q) {
    return function (a, b) {
      if ((a.id === sel) !== (b.id === sel)) return a.id === sel ? -1 : 1;
      if (q) { var pa = a.nomeN.indexOf(q) === 0, pb = b.nomeN.indexOf(q) === 0; if (pa !== pb) return pa ? -1 : 1; }
      return a.nome.localeCompare(b.nome, 'pt-BR');
    };
  }

  function abrir(o) {
    o = o || {};
    var lista = (o.clientes || []).map(prep), fora = (o.fora || []).map(prep);
    var sel = o.atual ? String(o.atual) : '', orig = sel;
    var f = document.createElement('div'); f.className = 'pk-fundo pk-sobre';
    if (o.z) f.style.zIndex = o.z;
    f.innerHTML = '<div class="pk pk-verde" role="dialog" aria-modal="true" aria-label="Empresa">'
      + '<header class="pk-cab"><span class="pk-av"><i class="fas fa-building"></i></span>'
      + '<div class="pk-quem"><b>' + esc(o.titulo || 'Empresa') + '</b><span>' + esc(o.sub || 'nome, número ou CNPJ/CPF') + '</span></div>'
      + '<div class="pk-val"><span class="pk-tag">escolhida</span><b class="pk-val-b"></b><small class="pk-val-s"></small></div>'
      + '<button type="button" class="pk-x" title="Fechar (Esc)"><i class="fas fa-xmark"></i></button></header>'
      + '<div class="pk-barra"><div class="pk-busca"><i class="fas fa-search"></i><input type="search" name="q" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" inputmode="search" enterkeyhint="search" placeholder="Buscar pelo nome, fantasia, cidade, número ou CNPJ/CPF…"></div><div class="pk-conta"></div></div>'
      + '<div class="pk-corpo"><div class="pk-grade pk-lista"></div><div class="pk-fora-bloco" hidden><div class="pk-fora-tit"></div><div class="pk-grade pk-grade-fora"></div></div></div>'
      + '<footer class="pk-pe"><div class="pk-pe-info"></div><button type="button" class="pk-canc">Cancelar</button>'
      + (o.permite_vazio ? '<button type="button" class="pk-sem"><i class="fas fa-ban"></i> ' + esc(o.rotulo_vazio || 'Sem empresa') + '</button>' : '')
      + '<button type="button" class="pk-ok"><i class="fas fa-check"></i> Usar esta empresa</button></footer></div>';
    document.body.appendChild(f);
    var trava = document.body.style.overflow; document.body.style.overflow = 'hidden';
    var $q = function (s) { return f.querySelector(s); };
    var busca = $q('.pk-busca input'), grade = $q('.pk-grade'), conta = $q('.pk-conta'), bloco = $q('.pk-fora-bloco'), gradeFora = $q('.pk-grade-fora'), titFora = $q('.pk-fora-tit');
    function fechar() { document.removeEventListener('keydown', tecla, true); f.remove(); document.body.style.overflow = trava; }
    function tecla(e) { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); fechar(); } }
    document.addEventListener('keydown', tecla, true);
    function porId(id) { return lista.filter(function (x) { return x.id === String(id); })[0]; }
    function usar(id) { var c = porId(id); fechar(); if (o.onUsar) o.onUsar(c || null); }
    function cabecalho() {
      var c = porId(sel), vb = $q('.pk-val-b'), vs = $q('.pk-val-s');
      vb.textContent = c ? c.nome : (o.permite_vazio ? (o.rotulo_vazio || 'nenhuma') : 'nenhuma');
      vs.textContent = c ? ((c.num ? 'nº ' + c.num + ' · ' : '') + docf(c.doc)) : 'toque num cartão';
      $q('.pk-ok').disabled = !sel;
      $q('.pk-pe-info').innerHTML = c && c.id === orig ? '<i class="fas fa-check"></i> Já é a empresa escolhida.' : '';
    }
    function cartao(c) {
      // uma linha por empresa (24/11/2026, Anderson: "só o nome da empresa, organizado e elegante"): sigla, nome inteiro numa linha, dados à direita
      var on = sel === c.id;
      return '<button type="button" class="pk-row' + (on ? ' on' : '') + '" data-id="' + c.id + '">'
        + '<span class="pk-sig" style="background:' + cor(c.nome) + '">' + esc(sigla(c.nome)) + '</span>'
        + '<span class="pk-row-nome">' + esc(c.nome) + '</span>'
        + '<small>' + (c.num ? 'nº ' + esc(c.num) + ' · ' : '') + esc(docf(c.doc) || 'sem CNPJ') + (c.av ? ' · <em>avulso</em>' : '') + (c.mensal ? ' · ' + brl(c.mensal) + '/mês' : '') + '</small>'
        + '<span class="pk-ck"><i class="fas fa-check"></i></span></button>';
    }
    function cartaoFora(c) {
      // uma linha limpa por empresa: só o nome; o que falta abre ao tocar na linha (24/11/2026, Anderson: "só o nome, organizado e elegante")
      return '<div class="pk-fora-row" data-fid="' + c.id + '"><span class="pk-fora-nome">' + esc(c.nome) + '</span>'
        + '<small>' + (c.num ? 'nº ' + esc(c.num) : esc(docf(c.doc))) + '</small><span class="pk-fora-tag">a cadastrar</span>'
        + '<div class="pk-fora-pq" hidden>' + esc(c.motivo || 'não pode ser escolhida') + (c.url ? ' · <a href="' + esc(c.url) + '" target="_blank" rel="noopener">abrir o cadastro</a>' : '') + '</div></div>';
    }
    function desenhar() {
      var q = norm(busca.value.trim()), toks = palavras(q), qd = q.replace(/\D/g, '');
      var soNumero = qd.length >= 3 && qd === q.replace(/[.\-\/\s]/g, '');      // digitou só número: casa pelo documento/número
      var vis = lista.filter(function (x) { return casa(x, toks, qd, soNumero); }).sort(ordem(sel, q));
      var mostra = vis.slice(0, LIMITE);
      var visFora = (q.length >= 2 || soNumero) ? fora.filter(function (x) { return casa(x, toks, qd, soNumero); }).sort(ordem('', q)) : [];
      grade.innerHTML = mostra.map(cartao).join('') + (!vis.length ? '<div class="pk-carregando">' + (visFora.length ? 'Nenhuma empresa disponível com esse nome.' : 'Nenhuma empresa com esse nome.') + '</div>' : '');
      conta.textContent = (vis.length > mostra.length ? 'mostrando ' + mostra.length + ' de ' + vis.length + ' · digite para filtrar' : (vis.length + (vis.length === 1 ? ' empresa' : ' empresas')))
        + (visFora.length ? ' · ' + visFora.length + ' ' + (o.rotulo_fora || 'fora') : '');
      bloco.hidden = !visFora.length;
      if (visFora.length) {
        titFora.textContent = (o.rotulo_fora || 'Não podem ser escolhidas');
        gradeFora.innerHTML = visFora.slice(0, LIMITE_FORA).map(cartaoFora).join('') + (visFora.length > LIMITE_FORA ? '<div class="pk-fora-mais">e mais ' + (visFora.length - LIMITE_FORA) + ' · digite mais letras</div>' : '');
        gradeFora.querySelectorAll('.pk-fora-row').forEach(function (r) { r.addEventListener('click', function (e) { if (e.target.tagName === 'A') return; var d = r.querySelector('.pk-fora-pq'); d.hidden = !d.hidden; r.classList.toggle('aberta', !d.hidden); }); });
      }
      grade.querySelectorAll('.pk-row[data-id]').forEach(function (c) {
        c.addEventListener('click', function () { sel = c.dataset.id; desenhar(); cabecalho(); });
        c.addEventListener('dblclick', function () { sel = c.dataset.id; usar(sel); });
      });
    }
    busca.addEventListener('input', desenhar);
    busca.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return; e.preventDefault();
      var p = grade.querySelectorAll('.pk-row[data-id]'); if (p.length === 1) { sel = p[0].dataset.id; usar(sel); }
    });
    $q('.pk-x').addEventListener('click', fechar);
    $q('.pk-canc').addEventListener('click', fechar);
    if ($q('.pk-sem')) $q('.pk-sem').addEventListener('click', function () { fechar(); if (o.onUsar) o.onUsar(null); });
    $q('.pk-ok').addEventListener('click', function () { if (sel) usar(sel); });
    f.addEventListener('mousedown', function (e) { if (e.target === f) fechar(); });
    desenhar(); cabecalho();
    if (!('ontouchstart' in window)) busca.focus();
  }
  window.CaixaCliente = { abrir: abrir };
})();
