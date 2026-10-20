/* Copiar dados do cadastro (20/10/2026) — pedido do Anderson: quem preenche
 * um documento copia o CNPJ, o nome ou a ficha inteira com um clique, em vez
 * de redigitar. Um arquivo só, carregado pelo base.html; as telas só marcam:
 *
 *   data-copiar[="valor"]     ícone de copiar no fim do elemento (sem valor:
 *                             copia o texto dele). data-copiar-rotulo="CNPJ"
 *                             dá o nome do campo no "copiar tudo".
 *   .fv-dl dd                 (ficha do cliente) ganha o ícone sozinho, com o
 *                             <dt> anterior como rótulo.
 *   data-copiar-bloco[="txt"] grupo de campos; "Copiar tudo" vai no
 *                             [data-copiar-cab] ou no .fv-seclabel de dentro
 *                             (com valor: copia esse texto pronto, ex. endereço).
 *   data-copiar-bloco-btn     botão que copia o bloco em volta dele.
 *   data-copiar-ficha         botão que copia todos os blocos da página.
 *
 * CPF, CNPJ, CEP, IE e telefone ganham também o "123": só os números, para
 * os sites que não aceitam ponto e barra.
 */
(function () {
    'use strict';

    var VAZIO = /^[\s—–-]*$/;

    function css() {
        if (document.getElementById('cpCss')) return;
        var s = document.createElement('style');
        s.id = 'cpCss';
        s.textContent = [
            '.cp-btn{display:inline-flex;align-items:center;justify-content:center;gap:3px;height:22px;min-width:22px;margin-left:6px;padding:0 4px;',
            '  border:1px solid transparent;border-radius:6px;background:transparent;color:#94a3b8;font:700 10px/1 inherit;cursor:pointer;',
            '  vertical-align:middle;opacity:.6;transition:opacity .12s,background .12s,color .12s;}',
            '.cp-btn i{font-size:11.5px;}',
            '.cp-btn:hover,.cp-btn:focus-visible{opacity:1;color:#15803d;background:#ecfdf3;border-color:#bbf7d0;outline:none;}',
            '.cp-btn.cp-dig{margin-left:2px;letter-spacing:.2px;}',
            '.cp-btn.cp-ok,.cp-tudo.cp-ok{opacity:1;color:#fff !important;background:#16a34a !important;border-color:#16a34a !important;}',
            '.cp-tudo{display:inline-flex;align-items:center;gap:5px;margin-left:auto;height:24px;padding:0 9px;border:1px solid #d1e7d9;',
            '  border-radius:999px;background:#fff;color:#15803d;font:700 11px/1 inherit;letter-spacing:0;text-transform:none;cursor:pointer;}',
            '.cp-tudo:hover{background:#ecfdf3;border-color:#86efac;}',
            '.cp-cab{display:flex !important;align-items:center;gap:8px;}',
            '.ef-hero .cp-btn{color:rgba(255,255,255,.75);}',
            '.ef-hero .cp-btn:hover{color:#fff;background:rgba(255,255,255,.16);border-color:rgba(255,255,255,.3);}',
            '.cp-linha{display:flex;align-items:center;min-width:0;}',
            '.cp-linha>:first-child{min-width:0;}',
            '.cp-linha>.cp-btn{flex:0 0 auto;}',
            '#cpAviso{position:fixed;left:50%;bottom:24px;transform:translate(-50%,20px);z-index:99999;max-width:min(92vw,520px);',
            '  padding:10px 16px;border-radius:10px;background:#0f172a;color:#fff;font-size:13px;box-shadow:0 8px 24px rgba(15,23,42,.25);',
            '  opacity:0;pointer-events:none;transition:opacity .15s,transform .15s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
            '#cpAviso.on{opacity:1;transform:translate(-50%,0);}',
            '#cpAviso i{color:#4ade80;margin-right:6px;}',
            '@media (hover:none){.cp-btn{opacity:.85;min-width:28px;height:28px;}}',
            '@media print{.cp-btn,.cp-tudo{display:none !important;}}'
        ].join('\n');
        document.head.appendChild(s);
    }

    // Texto do elemento como a pessoa vê: <br> vira quebra, espaços somem,
    // e os botões que este arquivo pôs não entram.
    function textoDe(el) {
        var c = el.cloneNode(true);
        c.querySelectorAll('.cp-btn, .cp-tudo, script, style').forEach(function (x) { x.remove(); });
        c.querySelectorAll('br').forEach(function (b) { b.replaceWith('\n'); });
        return c.textContent.split('\n').map(function (l) { return l.replace(/\s+/g, ' ').trim(); })
            .filter(Boolean).join('\n');
    }

    function soDigitos(v) { return String(v || '').replace(/\D/g, ''); }

    // CPF, CNPJ, CEP, IE, telefone: só número e pontuação, com 8+ dígitos.
    function ehNumero(v) {
        return /^[\d.\-\/\s()]+$/.test(v) && soDigitos(v).length >= 8 && soDigitos(v) !== v;
    }

    function copiar(texto) {
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(texto).catch(function () { return copiarVelho(texto); });
        }
        return copiarVelho(texto);
    }
    function copiarVelho(texto) {
        var t = document.createElement('textarea');
        t.value = texto;
        t.setAttribute('readonly', '');
        t.style.cssText = 'position:fixed;top:-1000px;opacity:0;';
        document.body.appendChild(t);
        t.select();
        var ok = false;
        try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
        t.remove();
        return ok ? Promise.resolve() : Promise.reject(new Error('copiar'));
    }

    var avisoTimer = null;
    function avisar(msg, erro) {
        var a = document.getElementById('cpAviso');
        if (!a) {
            a = document.createElement('div');
            a.id = 'cpAviso';
            a.setAttribute('role', 'status');
            document.body.appendChild(a);
        }
        a.innerHTML = '<i class="fas ' + (erro ? 'fa-circle-xmark' : 'fa-check') + '"></i>';
        a.appendChild(document.createTextNode(msg));
        a.classList.add('on');
        clearTimeout(avisoTimer);
        avisoTimer = setTimeout(function () { a.classList.remove('on'); }, 1800);
    }

    function feito(btn, texto, rotulo) {
        copiar(texto).then(function () {
            var linhas = texto.split('\n').length;
            avisar(linhas > 1 ? (rotulo || 'Dados') + ' copiados (' + linhas + ' linhas)'
                              : 'Copiado: ' + texto);
            if (btn) {
                btn.classList.add('cp-ok');
                setTimeout(function () { btn.classList.remove('cp-ok'); }, 1200);
            }
        }, function () {
            avisar('Não consegui copiar. Selecione o texto e use Ctrl+C.', true);
        });
    }

    function botao(valor, rotulo, digitos) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'cp-btn' + (digitos ? ' cp-dig' : '');
        b.dataset.cpValor = digitos ? soDigitos(valor) : valor;
        if (digitos) {
            b.textContent = '123';
            b.title = 'Copiar só os números' + (rotulo ? ' do ' + rotulo : '') + ' (' + soDigitos(valor) + ')';
        } else {
            b.innerHTML = '<i class="far fa-copy"></i>';
            b.title = 'Copiar ' + (rotulo ? rotulo + ': ' : '') + valor;
        }
        b.setAttribute('aria-label', b.title);
        return b;
    }

    // Prepara um campo: guarda valor/rótulo e põe o(s) botão(ões).
    function prepararCampo(el, valor, rotulo, depois) {
        if (el.dataset.cpPronto) return;
        el.dataset.cpPronto = '1';
        valor = (valor || '').trim();
        if (VAZIO.test(valor)) return;
        el.dataset.cpValor = valor;
        el.dataset.cpRotulo = rotulo || '';
        var bs = [botao(valor, rotulo, false)];
        if (valor.indexOf('\n') < 0 && ehNumero(valor)) bs.push(botao(valor, rotulo, true));
        if (depois) {
            // ao lado do elemento (ex.: título cortado com reticências)
            var linha = document.createElement('span');
            linha.className = 'cp-linha';
            el.parentNode.insertBefore(linha, el);
            linha.appendChild(el);
            bs.forEach(function (b) { linha.appendChild(b); });
        } else {
            bs.forEach(function (b) { el.appendChild(b); });
        }
    }

    function linhasDoBloco(bl) {
        if (bl.dataset.copiarBloco) return bl.dataset.copiarBloco;
        var out = [];
        bl.querySelectorAll('[data-cp-valor]:not(.cp-btn)').forEach(function (c) {
            var v = c.dataset.cpValor, r = c.dataset.cpRotulo;
            if (!v || VAZIO.test(v)) return;
            v = v.replace(/\n/g, ' · ');
            out.push(r ? r + ': ' + v : v);
        });
        return out.join('\n');
    }

    function tituloDoBloco(bl) {
        if (bl.dataset.copiarTitulo) return bl.dataset.copiarTitulo;
        var cab = bl.querySelector('.fv-seclabel, [data-copiar-cab]');
        return cab ? textoDe(cab) : '';
    }

    function preparar(raiz) {
        css();
        raiz = raiz || document;
        raiz.querySelectorAll('.fv-dl dd').forEach(function (dd) {
            if (dd.hasAttribute('data-nocopiar')) return;
            var dt = dd.previousElementSibling;
            prepararCampo(dd, textoDe(dd), dt && dt.tagName === 'DT' ? textoDe(dt) : '');
        });
        raiz.querySelectorAll('[data-copiar]').forEach(function (el) {
            prepararCampo(el, el.getAttribute('data-copiar') || textoDe(el),
                          el.getAttribute('data-copiar-rotulo') || '', el.hasAttribute('data-copiar-depois'));
        });
        raiz.querySelectorAll('[data-copiar-bloco]').forEach(function (bl) {
            if (bl.dataset.cpBlocoPronto) return;
            bl.dataset.cpBlocoPronto = '1';
            var cab = bl.querySelector('[data-copiar-cab]') || bl.querySelector('.fv-seclabel');
            if (!cab) return;
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'cp-tudo';
            b.dataset.copiarBlocoBtn = '';
            b.innerHTML = '<i class="far fa-copy"></i> Copiar tudo';
            b.title = 'Copiar todos os dados deste quadro';
            cab.classList.add('cp-cab');
            cab.appendChild(b);
        });
    }

    document.addEventListener('click', function (e) {
        var b = e.target.closest('.cp-btn, [data-copiar-bloco-btn], [data-copiar-ficha]');
        if (!b) return;
        e.preventDefault();
        e.stopPropagation();          // o cartão inteiro abre a ficha; o copiar não
        if (b.classList.contains('cp-btn')) {
            feito(b, b.dataset.cpValor);
        } else if (b.hasAttribute('data-copiar-ficha')) {
            var partes = [];
            if (b.dataset.copiarFicha) partes.push(b.dataset.copiarFicha);
            document.querySelectorAll('[data-copiar-bloco]').forEach(function (bl) {
                var t = linhasDoBloco(bl), tit = tituloDoBloco(bl);
                if (t) partes.push((tit ? tit.toUpperCase() + '\n' : '') + t);
            });
            feito(b, partes.join('\n\n'), 'Ficha');
        } else {
            var bl = b.closest('[data-copiar-bloco]');
            if (bl) feito(b, linhasDoBloco(bl), tituloDoBloco(bl) || 'Dados');
        }
    }, true);

    window.qcxCopiar = {preparar: preparar, copiar: function (t) { feito(null, t); }};
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { preparar(); });
    } else {
        preparar();
    }
})();
