/* Caixa de escolha do SERVIÇO de uma linha do contrato de honorários (19/11/2026) — Anderson: "não tem que haver dois
   campos, um em que eu digito e outro que eu escolho... o filtro horroroso". No lugar do texto + <select> do aparelho:
   um botão-campo por linha que abre esta caixa (mesmo molde pk-* da caixa de Grupo): busca, cartões dos serviços (os
   mais usados primeiro), "Texto livre" para discriminação sem serviço, e um complemento opcional que entra na
   discriminação ("Honorários contábeis — Posto Marinheiro").

   CaixaServico.abrir({ servicos: [{id, nome, n, valor_padrao}], atual: {servico_id, complemento, texto}, z: 1300,
                        onUsar: function ({servico_id, nome, complemento, descricao}) {} })                          */
(function () {
  var PK_CORES = ['#0f766e', '#4f46e5', '#b45309', '#be123c', '#7c3aed', '#0369a1', '#15803d', '#c2410c'];
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function pkCor(s) { var h = 0; String(s).split('').forEach(function (c) { h = (h * 31 + c.charCodeAt(0)) >>> 0; }); return PK_CORES[h % 8]; }
  function sigla(nome) {
    var p = String(nome || '').replace(/—.*$/, '').trim().split(/\s+/).filter(function (x) { return x.length > 2 || /^[A-Z0-9-]+$/.test(x); });
    if (p.length === 1 && /^[A-Z0-9-]{2,5}$/.test(p[0])) return p[0].slice(0, 4);
    return (((p[0] || '')[0] || '') + ((p[1] || '')[0] || '')).toUpperCase();
  }
  function brl(v) { return 'R$ ' + Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function curto(nome) { return String(nome || '').split(' — ')[0]; }

  function abrir(o) {
    o = o || {};
    var lista = (o.servicos || []).map(function (s) { return { id: String(s.id), nome: s.nome, n: Number(s.n || 0), valor_padrao: s.valor_padrao }; });
    var atual = o.atual || {};
    var sel = atual.servico_id ? String(atual.servico_id) : '', livre = !atual.servico_id && atual.texto ? String(atual.texto) : '', livreAberto = false;
    var f = document.createElement('div'); f.className = 'pk-fundo pk-sobre';
    if (o.z) f.style.zIndex = o.z;
    f.innerHTML = '<div class="pk pk-verde" role="dialog" aria-modal="true" aria-label="Serviço contratado">'
      + '<header class="pk-cab"><span class="pk-av"><i class="fas fa-list-check"></i></span>'
      + '<div class="pk-quem"><b>' + esc(o.titulo || 'O que está contratado') + '</b><span>' + esc(o.empresa || 'escolha o serviço desta linha') + '</span></div>'
      + '<div class="pk-val"><span class="pk-tag">escolhido</span><b class="pk-val-b"></b><small class="pk-val-s"></small></div>'
      + '<button type="button" class="pk-x" title="Fechar (Esc)"><i class="fas fa-xmark"></i></button></header>'
      + '<div class="pk-barra"><div class="pk-busca"><i class="fas fa-search"></i><input type="search" name="q" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" inputmode="search" enterkeyhint="search" placeholder="Buscar o serviço… (honorários, LMC, folha, endereço)"></div></div>'
      + '<div class="pk-corpo"><div class="pk-grade"></div></div>'
      + '<footer class="pk-pe"><label class="pk-comp"><span>Complemento <small>opcional, entra na discriminação</small></span><input type="text" class="ns-in pk-comp-in" maxlength="120" placeholder="ex.: Posto Marinheiro"></label>'
      + '<button type="button" class="pk-canc">Cancelar</button>'
      + '<button type="button" class="pk-ok"><i class="fas fa-check"></i> Usar este serviço</button></footer></div>';
    document.body.appendChild(f);
    var trava = document.body.style.overflow; document.body.style.overflow = 'hidden';
    var $q = function (s) { return f.querySelector(s); };
    var busca = $q('.pk-busca input'), grade = $q('.pk-grade'), comp = $q('.pk-comp-in');
    comp.value = atual.complemento || '';
    function fechar() { document.removeEventListener('keydown', tecla, true); f.remove(); document.body.style.overflow = trava; }
    function tecla(e) { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); if (livreAberto) { livreAberto = false; desenhar(); } else fechar(); } }
    document.addEventListener('keydown', tecla, true);
    function porId(id) { return lista.filter(function (x) { return x.id === String(id); })[0]; }
    function escolhido() {
      if (sel) { var s = porId(sel), c = comp.value.trim().replace(/\s+/g, ' '); return { servico_id: sel, nome: s.nome, complemento: c, descricao: s.nome + (c ? ' — ' + c : '') }; }
      if (livre) return { servico_id: '', nome: '', complemento: '', descricao: livre };
      return null;
    }
    function cabecalho() {
      var e = escolhido(), vb = $q('.pk-val-b'), vs = $q('.pk-val-s');
      vb.textContent = e ? e.descricao : 'nada ainda';
      vs.textContent = e ? (e.servico_id ? 'serviço do cadastro' : 'texto livre, sem serviço') : 'toque num cartão';
      $q('.pk-ok').disabled = !e;
      $q('.pk-comp').style.display = sel ? '' : 'none';
    }
    function cartao(s) {
      var on = sel === s.id;
      return '<button type="button" class="pk-card pk-serv' + (on ? ' on' : '') + '" data-id="' + s.id + '">'
        + '<span class="pk-ck"><i class="fas fa-check"></i></span>'
        + '<div class="pk-card-h"><span class="pk-sig" style="background:' + pkCor(curto(s.nome)) + '">' + esc(sigla(s.nome)) + '</span>'
        + '<div><b>' + esc(curto(s.nome)) + '</b><small>' + (s.nome.indexOf(' — ') > 0 ? esc(s.nome.split(' — ').slice(1).join(' — ')) + ' · ' : '')
        + (s.n ? 'em ' + s.n + (s.n === 1 ? ' contrato' : ' contratos') : 'ainda sem uso') + (s.valor_padrao ? ' · ' + brl(s.valor_padrao) : '') + '</small></div></div>'
        + '</button>';
    }
    function cartaoLivre() {
      if (!livreAberto) return '<button type="button" class="pk-card pk-novo-c"><i class="fas fa-pen"></i><b>Texto livre</b><small>' + (livre ? esc(livre) : 'discriminação sem serviço do cadastro') + '</small></button>';
      return '<div class="pk-card pk-novo-f"><b><i class="fas fa-pen"></i> Texto livre</b>'
        + '<input type="text" class="pk-novo-nome" maxlength="200" placeholder="Como vai aparecer na cobrança" value="' + esc(livre || busca.value.trim()) + '">'
        + '<div class="pk-novo-bt"><button type="button" class="pk-canc pk-novo-x">Voltar</button><button type="button" class="pk-ok pk-novo-ok">Usar este texto</button></div><div class="pk-msg"></div></div>';
    }
    function desenhar() {
      var q = busca.value.trim().toLowerCase();
      var vis = lista.filter(function (x) { return !q || x.nome.toLowerCase().indexOf(q) >= 0 || sigla(x.nome).toLowerCase() === q; });
      vis.sort(function (a, b) {
        if ((a.id === sel) !== (b.id === sel)) return a.id === sel ? -1 : 1;
        if (a.n !== b.n) return b.n - a.n;
        return 0;
      });
      grade.innerHTML = vis.map(cartao).join('') + cartaoLivre() + (!vis.length ? '<div class="pk-carregando">Nenhum serviço com esse nome. Use "Texto livre".</div>' : '');
      grade.querySelectorAll('.pk-card[data-id]').forEach(function (c) {
        c.addEventListener('click', function () { sel = c.dataset.id; livre = ''; desenhar(); cabecalho(); comp.focus(); });
        c.addEventListener('dblclick', function () { sel = c.dataset.id; livre = ''; usar(); });
      });
      var nc = grade.querySelector('.pk-novo-c'); if (nc) nc.addEventListener('click', function () { livreAberto = true; desenhar(); });
      var nf = grade.querySelector('.pk-novo-f');
      if (nf) {
        var ni = nf.querySelector('.pk-novo-nome'); ni.focus(); ni.select();
        function okLivre() { var v = ni.value.trim().replace(/\s+/g, ' '); if (!v) { nf.querySelector('.pk-msg').textContent = 'Digite o texto.'; return; } livre = v; sel = ''; livreAberto = false; desenhar(); cabecalho(); }
        ni.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); okLivre(); } });
        nf.querySelector('.pk-novo-x').addEventListener('click', function () { livreAberto = false; desenhar(); });
        nf.querySelector('.pk-novo-ok').addEventListener('click', okLivre);
        nf.scrollIntoView({ block: 'nearest' });
      }
    }
    function usar() { var e = escolhido(); if (!e) return; fechar(); if (o.onUsar) o.onUsar(e); }
    busca.addEventListener('input', function () { livreAberto = false; desenhar(); });
    busca.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return; e.preventDefault();
      var p = grade.querySelectorAll('.pk-card[data-id]'); if (p.length === 1) p[0].click();
    });
    comp.addEventListener('input', cabecalho);
    comp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); usar(); } });
    $q('.pk-x').addEventListener('click', fechar);
    $q('.pk-pe > .pk-canc').addEventListener('click', fechar);
    $q('.pk-pe > .pk-ok').addEventListener('click', usar);
    f.addEventListener('mousedown', function (e) { if (e.target === f) fechar(); });
    desenhar(); cabecalho();
    if (!('ontouchstart' in window)) busca.focus();
  }
  window.CaixaServico = { abrir: abrir };
})();
