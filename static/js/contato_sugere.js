// Janela de contato (11/11/2026, Anderson): "se eu tentar digitar o nome já aparecer os outros cadastros de
// contatos para ficar mais fácil para o usuário". Lista embaixo do campo Nome com as pessoas que já são contato
// em alguma empresa; escolher uma chama aoEscolher(pessoa) — quem chama decide o que preencher.
// Uso: QCContatoSugere.ligar(inputNome, { url: '/api/contatos/sugerir', clienteId: 12, aoEscolher: fn })
(function () {
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function ligar(inp, op) {
        if (!inp || inp._qcSug) return;
        inp._qcSug = true;
        inp.setAttribute('autocomplete', 'off');
        var caixa = document.createElement('div');
        caixa.className = 'ct-sug';
        caixa.hidden = true;
        var pai = inp.parentNode;
        if (getComputedStyle(pai).position === 'static') pai.style.position = 'relative';
        pai.appendChild(caixa);
        var lista = [], ativo = -1, timer = null, ultimo = '', calado = false;

        function fechar() { caixa.hidden = true; ativo = -1; }
        function marcar(i) {
            ativo = i;
            caixa.querySelectorAll('.ct-sug-i').forEach(function (el, k) { el.classList.toggle('on', k === i); });
        }
        function escolher(i) {
            var p = lista[i];
            if (!p) return;
            fechar();
            calado = true;
            inp.value = p.nome;
            inp.dispatchEvent(new Event('input', { bubbles: true }));
            calado = false;
            if (op.aoEscolher) op.aoEscolher(p);
        }
        function desenhar() {
            if (!lista.length) { fechar(); return; }
            caixa.innerHTML = '<div class="ct-sug-t">Já é contato em outra empresa — clique para usar</div>' +
                lista.map(function (p, i) {
                    var emp = p.empresas.slice(0, 2).join(' · ') + (p.empresas.length > 2 ? ' · e mais ' + (p.empresas.length - 2) : '');
                    return '<div class="ct-sug-i" data-i="' + i + '"><b>' + esc(p.nome) + '</b>' +
                        (p.celular ? '<span class="ct-sug-cel">' + esc(p.celular) + '</span>' : '') +
                        '<small>' + (p.cargo ? esc(p.cargo) + ' · ' : '') + 'contato em ' + esc(emp) + '</small></div>';
                }).join('');
            caixa.hidden = false;
            ativo = -1;
        }
        function buscar() {
            var q = (inp.value || '').trim();
            if (q.length < 3) { lista = []; fechar(); return; }
            if (q === ultimo) { if (lista.length) caixa.hidden = false; return; }
            ultimo = q;
            var u = op.url + '?q=' + encodeURIComponent(q) + (op.clienteId ? '&cliente_id=' + op.clienteId : '');
            fetch(u, { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : []; })
                .then(function (l) {
                    if ((inp.value || '').trim() !== q) return;   // já digitou outra coisa
                    lista = Array.isArray(l) ? l : [];
                    if (document.activeElement === inp) desenhar();
                }).catch(function () {});
        }
        inp.addEventListener('input', function () {
            if (calado) return;
            clearTimeout(timer);
            timer = setTimeout(buscar, 250);
        });
        inp.addEventListener('focus', function () { if (lista.length && (inp.value || '').trim().length >= 3) desenhar(); });
        inp.addEventListener('keydown', function (e) {
            if (caixa.hidden) return;
            if (e.key === 'ArrowDown') { e.preventDefault(); marcar(Math.min(ativo + 1, lista.length - 1)); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); marcar(Math.max(ativo - 1, 0)); }
            else if (e.key === 'Enter' && ativo >= 0) { e.preventDefault(); escolher(ativo); }
            else if (e.key === 'Escape') { e.stopPropagation(); fechar(); }
        });
        caixa.addEventListener('mousedown', function (e) {
            var it = e.target.closest('.ct-sug-i');
            if (!it) return;
            e.preventDefault();
            escolher(parseInt(it.getAttribute('data-i'), 10));
        });
        inp.addEventListener('blur', function () { setTimeout(fechar, 150); });
        // ao abrir a janela de novo, a lista antiga não volta
        inp._qcSugLimpar = function () { lista = []; ultimo = ''; fechar(); };
    }
    window.QCContatoSugere = { ligar: ligar };
})();
