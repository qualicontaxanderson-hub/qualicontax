/* Número e complemento do endereço (10/11/2026) — pedido do Anderson:
 *   "se for SEM NÚMERO, ter um botão para clicar Sem Número até para termos controle futuro e organização"
 *   "Complemento, ter a opção de ir incluindo exemplo: + Quadra 79 + Lote 17 + Apartamento 408-B ...
 *    a turma vai digitar de tudo, então podemos criar os complementos mais usados e ter o botão de
 *    incluir novos, e tudo isso com registro de quem está criando"
 *
 * Um arquivo só, carregado pelo base.html; as telas só marcam:
 *   <input data-end-numero>        ganha o botão S/N (grava sempre "S/N")
 *   <input data-end-complemento>   vira peças: [QUADRA 79] [LOTE 17] [APARTAMENTO 408-B] + botões
 *                                  "+ QUADRA", "+ LOTE"... O campo original continua no formulário
 *                                  (escondido) com o texto pronto: "QUADRA 79 - LOTE 17 - APARTAMENTO 408-B".
 * Quem preenche o campo por código (CEP, CNPJ, editar) continua usando input.value = '...': o
 * campo avisa a peça sozinho. O texto antigo ("QUADRA12 LOTE 149", "QD 79 - APTO 408-B") é
 * separado em peças quando a tela abre.
 */
(function () {
    'use strict';

    var URL_TIPOS = '/api/enderecos/complemento-tipos';
    var URL_NOVO = '/api/enderecos/complemento-tipos/novo';
    var MAX = 100;           // enderecos_clientes.complemento VARCHAR(100)
    var A_VISTA = 7;         // quantos "+ TIPO" ficam à mostra; o resto vai em "+ outros"
    var SN = 'S/N';
    var SN_VARIANTES = ['SN', 'S/N', 'S N', 'S.N', 'S.N.', 'S/N.', 'SNR', 'S/NR', 'S/NO', 'S/Nº', 'SN°', 'SNº', 'S/N°',
                        'SEM NUMERO', 'SEM NÚMERO', 'SEM NUM', 'SEM N', 'S-N'];

    // lista inicial (a mesma da migração) — a tela já abre com ela e completa com a do banco
    var TIPOS = ['QUADRA', 'LOTE', 'SALA', 'APARTAMENTO', 'BLOCO', 'ANDAR', 'CASA', 'LOJA', 'KM', 'CONJUNTO',
                 'EDIFÍCIO', 'CONDOMÍNIO', 'TORRE', 'BOX', 'GALPÃO', 'MÓDULO', 'SETOR', 'ÁREA', 'CHÁCARA',
                 'FAZENDA', 'ANEXO', 'FUNDOS', 'TÉRREO', 'SOBRELOJA'];
    // abreviações que aparecem no texto antigo -> tipo
    var APELIDOS = {
        QD: 'QUADRA', QDA: 'QUADRA', QUAD: 'QUADRA', LT: 'LOTE', LTS: 'LOTE', LOTES: 'LOTE', SL: 'SALA', SALAS: 'SALA',
        APTO: 'APARTAMENTO', APT: 'APARTAMENTO', AP: 'APARTAMENTO', BL: 'BLOCO', BLC: 'BLOCO', CONJ: 'CONJUNTO',
        CJ: 'CONJUNTO', EDIF: 'EDIFÍCIO', ED: 'EDIFÍCIO', COND: 'CONDOMÍNIO', MOD: 'MÓDULO', GALP: 'GALPÃO',
        SLJ: 'SOBRELOJA', AND: 'ANDAR', CS: 'CASA', LJ: 'LOJA', FAZ: 'FAZENDA', Q: 'QUADRA'
    };
    var carregou = null;

    function semAcento(s) { return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toUpperCase(); }
    function mai(s) { return String(s || '').replace(/\s+/g, ' ').trim().toUpperCase(); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function css() {
        if (document.getElementById('qeCss')) return;
        var s = document.createElement('style');
        s.id = 'qeCss';
        s.textContent = [
            '.qe-num{position:relative;display:block;}',
            '.qe-num input{padding-right:52px !important;}',
            '.qe-sn{position:absolute;right:6px;top:50%;transform:translateY(-50%);height:26px;padding:0 9px;border:1px solid #cbd5e1;',
            '  border-radius:99px;background:#fff;color:#475569;font:700 11.5px/1 inherit;letter-spacing:.3px;cursor:pointer;}',
            '.qe-sn:hover{border-color:#16a34a;color:#15803d;}',
            '.qe-num.on .qe-sn{background:#16a34a;border-color:#16a34a;color:#fff;}',
            '.qe-num.on input{background:#f0fdf4 !important;color:#15803d !important;font-weight:700;letter-spacing:.5px;}',
            '.qe-comp{border:1px solid #e2e8f0;border-radius:12px;padding:8px 10px;background:#fff;}',
            '.qe-comp:focus-within{border-color:#86efac;box-shadow:0 0 0 3px #dcfce7;}',
            '.qe-linha{display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-height:30px;}',
            '.qe-vazio{color:#94a3b8;font-size:13px;}',
            '.qe-chip{display:inline-flex;align-items:center;gap:5px;height:28px;padding:0 4px 0 10px;border-radius:8px;background:#ecfdf3;',
            '  border:1px solid #bbf7d0;color:#0f172a;font-size:13px;cursor:pointer;}',
            '.qe-chip b{color:#15803d;font-weight:700;}',
            '.qe-chip.velho{background:#f8fafc;border-color:#e2e8f0;border-style:dashed;}',
            '.qe-chip.velho b{color:#64748b;font-weight:600;font-size:11px;}',
            '.qe-chip button{border:0;background:none;color:#94a3b8;width:20px;height:20px;border-radius:5px;cursor:pointer;font-size:14px;line-height:1;}',
            '.qe-chip button:hover{color:#b91c1c;background:#fee2e2;}',
            '.qe-ed{display:inline-flex;align-items:center;gap:5px;height:30px;padding:0 4px 0 10px;border:1px solid #16a34a;border-radius:8px;background:#fff;}',
            '.qe-ed b{color:#15803d;font-size:13px;}',
            '.qe-ed input{border:0 !important;outline:0 !important;box-shadow:none !important;width:110px;height:26px !important;padding:0 4px !important;',
            '  font-size:13px;text-transform:uppercase;background:transparent !important;}',
            '.qe-ed button{border:0;height:24px;min-width:24px;border-radius:6px;cursor:pointer;font-size:12px;font-weight:700;}',
            '.qe-ed .qe-ok{background:#16a34a;color:#fff;padding:0 8px;}',
            '.qe-ed .qe-x{background:#f1f5f9;color:#64748b;}',
            '.qe-mais{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-top:7px;padding-top:7px;border-top:1px dashed #e2e8f0;}',
            '.qe-add{height:26px;padding:0 9px;border:1px solid #e2e8f0;border-radius:99px;background:#fff;color:#1f6f43;',
            '  font-size:12px;font-weight:600;cursor:pointer;}',
            '.qe-add:hover{border-color:#16a34a;background:#f0fdf4;}',
            '.qe-outros{height:26px !important;padding:0 6px !important;border:1px dashed #cbd5e1 !important;border-radius:99px !important;',
            '  background:#fff !important;color:#475569;font-size:12px !important;font-weight:600;cursor:pointer;width:auto !important;min-width:0 !important;}',
            '.qe-novo{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:7px;}',
            '.qe-novo input{height:30px !important;max-width:260px;padding:0 10px !important;border:1px solid #cbd5e1 !important;border-radius:8px !important;',
            '  font-size:13px !important;text-transform:uppercase;}',
            '.qe-novo button{height:30px;padding:0 12px;border-radius:8px;border:1px solid #e2e8f0;background:#fff;font-size:12.5px;font-weight:600;cursor:pointer;}',
            '.qe-novo .qe-criar{background:#16a34a;border-color:#16a34a;color:#fff;}',
            '.qe-novo small{width:100%;color:#64748b;font-size:11.5px;}',
            '.qe-prev{display:block;margin-top:6px;color:#64748b;font-size:11.5px;}',
            '.qe-prev b{color:#0f172a;font-weight:600;}',
            '.qe-prev.longo,.qe-prev.longo b{color:#b91c1c;}',
            '.qe-msg{display:block;margin-top:4px;color:#b91c1c;font-size:11.5px;}',
            '.qe-novo[hidden],.qe-msg[hidden]{display:none !important;}',
            '.qe-tv{position:relative;display:block;}',
            '.qe-tv.on input,.qe-tv.on select{background:#f8fafc !important;color:#0f172a !important;padding-right:84px !important;}',
            '.qe-tv.on select{pointer-events:none;-webkit-appearance:none;appearance:none;}',
            '.qe-alt{display:none;position:absolute;right:6px;top:50%;transform:translateY(-50%);height:24px;padding:0 8px;border:1px solid #e2e8f0;',
            '  border-radius:7px;background:#fff;color:#64748b;font:600 11.5px/1 inherit;cursor:pointer;align-items:center;gap:4px;}',
            '.qe-tv.on .qe-alt{display:inline-flex;}',
            '.qe-alt i{font-size:10px;}',
            '.qe-alt:hover{border-color:#f59e0b;color:#b45309;background:#fffbeb;}',
            '.qe-tv-dif{display:block;margin-top:3px;font-size:11px;color:#b45309;}',
            '.qe-log{display:flex;gap:6px;align-items:stretch;}',
            '.qe-log select{flex:0 0 128px;width:128px !important;min-width:0 !important;}',
            '.qe-log input{flex:1;min-width:0;}',
            '.qe-log.falta select{border-color:#f87171 !important;box-shadow:0 0 0 3px #fee2e2 !important;}',
            '.qe-log-msg{display:block;margin-top:3px;font-size:11px;color:#b91c1c;}',
            '.qe-log-msg[hidden]{display:none;}',
            '.qe-cep-aviso{display:block;margin-top:4px;font-size:11.5px;line-height:1.35;}',
            '.qe-cep-aviso.ok{color:#15803d;} .qe-cep-aviso.geral{color:#b45309;} .qe-cep-aviso.erro{color:#b91c1c;} .qe-cep-aviso.busca{color:#64748b;}'
        ].join('\n');
        document.head.appendChild(s);
    }

    // ---- input.value = '...' feito por outro código também avisa a peça ----
    var nativo = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    function vigiar(inp, aoMudar) {
        Object.defineProperty(inp, 'value', {
            configurable: true,
            get: function () { return nativo.get.call(this); },
            set: function (v) { nativo.set.call(this, v); if (!inp._qeInterno) aoMudar(); }
        });
        var f = inp.form;
        if (f) f.addEventListener('reset', function () { setTimeout(aoMudar, 0); });
    }
    function gravar(inp, v) {
        inp._qeInterno = true;
        nativo.set.call(inp, v);
        inp._qeInterno = false;
        inp.dispatchEvent(new Event('input', { bubbles: true }));
        inp.dispatchEvent(new Event('change', { bubbles: true }));
    }

    // =========================== NÚMERO: botão S/N ===========================
    function ehSN(v) {
        var t = mai(v);
        return !!t && (SN_VARIANTES.indexOf(t) >= 0 || SN_VARIANTES.indexOf(t.replace(/ /g, '')) >= 0);
    }
    function montarNumero(inp) {
        if (inp._qe) return;
        inp._qe = true;
        var box = document.createElement('span');
        box.className = 'qe-num';
        inp.parentNode.insertBefore(box, inp);
        box.appendChild(inp);
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'qe-sn';
        b.textContent = 'S/N';
        b.title = 'Sem número';
        box.appendChild(b);
        function estado() {
            var on = ehSN(nativo.get.call(inp));
            if (on && nativo.get.call(inp) !== SN) { inp._qeInterno = true; nativo.set.call(inp, SN); inp._qeInterno = false; }
            box.classList.toggle('on', on);
            inp.readOnly = on;
            b.title = on ? 'Tem número: clique para digitar' : 'Sem número';
        }
        b.addEventListener('click', function () {
            if (box.classList.contains('on')) { gravar(inp, ''); estado(); inp.focus(); }
            else { gravar(inp, SN); estado(); }
        });
        inp.addEventListener('blur', estado);
        vigiar(inp, estado);
        estado();
    }

    // ======================= COMPLEMENTO: peças =======================
    function tiposCarregar() {
        if (carregou) return carregou;
        carregou = fetch(URL_TIPOS, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : []; })
            .then(function (lista) {
                if (Array.isArray(lista) && lista.length) TIPOS = lista.map(function (t) { return t.nome; });
                return TIPOS;
            }).catch(function () { return TIPOS; });
        return carregou;
    }

    // "QUADRA12 LOTE 149", "QD 79 - APTO 408-B", "QUADRA: 3-B; LOTE: 24;" -> [{tipo, valor}]
    function separar(texto) {
        var s = semAcento(texto).replace(/[;:,]/g, ' ').replace(/\s+/g, ' ').trim();
        if (!s) return [];
        var nomes = {};
        TIPOS.forEach(function (t) { nomes[semAcento(t)] = t; });
        Object.keys(APELIDOS).forEach(function (a) { if (!nomes[a]) nomes[a] = APELIDOS[a]; });
        var chaves = Object.keys(nomes).sort(function (a, b) { return b.length - a.length; });
        function letra(c) { return !!c && /[A-Z]/.test(c); }
        function tipoEm(i) {
            for (var k = 0; k < chaves.length; k++) {
                var c = chaves[k];
                if (s.substr(i, c.length) !== c) continue;
                var dep = s.charAt(i + c.length);
                if (c.length === 1) { if (/[0-9]/.test(dep)) return c; continue; }   // Q79 = QUADRA 79
                // QUADRA12 (grudado no número) vale; QUADRAMODULO (dois tipos grudados) vale; CASAL não
                if (!letra(dep) || tipoEm2(i + c.length)) return c;
                // QUADRAG 11, QUADRAC16 (a letra da quadra grudada, como vem da Receita)
                if (c.length >= 5 && /^[A-Z]{1,2}(?![A-Z])/.test(s.substr(i + c.length))) return c;
            }
            return null;
        }
        function tipoEm2(i) {
            for (var k = 0; k < chaves.length; k++) {
                var c = chaves[k];
                if (c.length >= 3 && s.substr(i, c.length) === c && !letra(s.charAt(i + c.length))) return true;
            }
            return false;
        }
        var out = [], cur = null, buf = '', i = 0, colado = false;
        function fechar() {
            var v = buf.replace(/^[\s\-–\/.]+|[\s\-–\/]+$/g, '');
            if (cur) out.push({ tipo: nomes[cur], valor: v });
            else if (v) out.push({ tipo: '', valor: v });
            buf = '';
        }
        while (i < s.length) {
            if (i === 0 || !letra(s.charAt(i - 1)) || colado) {
                var c = tipoEm(i);
                if (c) { fechar(); cur = c; i += c.length; colado = true; continue; }
            }
            colado = false;
            buf += s.charAt(i); i++;
        }
        fechar();
        // "1º ANDAR": o número veio antes do tipo
        for (var k = 0; k < out.length - 1; k++) {
            if (!out[k].tipo && out[k + 1].tipo && !out[k + 1].valor && out[k].valor.length <= 4) {
                out[k + 1].valor = out[k].valor; out.splice(k, 1); k--;
            }
        }
        return out;
    }
    function juntar(lista) {
        return lista.map(function (p) { return p.tipo ? (p.tipo + (p.valor ? ' ' + p.valor : '')) : p.valor; })
                    .filter(Boolean).join(' - ');
    }

    function montarComplemento(inp) {
        if (inp._qe) return;
        inp._qe = true;
        inp.style.display = 'none';
        var box = document.createElement('div');
        box.className = 'qe-comp';
        box.innerHTML = '<div class="qe-linha"></div><div class="qe-mais"></div>'
            + '<div class="qe-novo" hidden><input type="text" maxlength="30" placeholder="Nome do tipo novo (ex.: GLEBA)">'
            + '<button type="button" class="qe-criar">Criar tipo</button><button type="button" class="qe-cancelar">Cancelar</button>'
            + '<small>O tipo novo fica disponível para toda a equipe e registrado em seu nome.</small></div>'
            + '<small class="qe-prev"></small><small class="qe-msg" hidden></small>';
        inp.parentNode.insertBefore(box, inp.nextSibling);
        var linha = box.querySelector('.qe-linha'), mais = box.querySelector('.qe-mais'),
            novo = box.querySelector('.qe-novo'), prev = box.querySelector('.qe-prev'), msg = box.querySelector('.qe-msg');
        var lista = [], editando = null;   // editando = {i (índice ou -1 = nova), tipo}

        function aviso(t) { msg.textContent = t || ''; msg.hidden = !t; }
        function ler() { lista = separar(nativo.get.call(inp)); editando = null; desenhar(); }
        function salvar() { gravar(inp, juntar(lista)); }

        function desenhar() {
            var h = '';
            lista.forEach(function (p, i) {
                if (editando && editando.i === i) { h += editor(); return; }
                h += '<span class="qe-chip' + (p.tipo ? '' : ' velho') + '" data-i="' + i + '" title="Clique para alterar">'
                   + (p.tipo ? '<b>' + esc(p.tipo) + '</b>' + (p.valor ? ' ' + esc(p.valor) : '') : '<b>como estava</b> ' + esc(p.valor))
                   + '<button type="button" data-tirar="' + i + '" aria-label="Tirar">×</button></span>';
            });
            if (editando && editando.i === -1) h += editor();
            if (!lista.length && !editando) h += '<span class="qe-vazio">Sem complemento — inclua pelas opções abaixo</span>';
            linha.innerHTML = h;
            var mh = '';
            TIPOS.slice(0, A_VISTA).forEach(function (t) {
                mh += '<button type="button" class="qe-add" data-tipo="' + esc(t) + '">+ ' + esc(t.charAt(0) + t.slice(1).toLowerCase()) + '</button>';
            });
            mh += '<select class="qe-outros" aria-label="Outros tipos"><option value="">+ outros…</option>';
            TIPOS.slice(A_VISTA).forEach(function (t) { mh += '<option value="' + esc(t) + '">' + esc(t) + '</option>'; });
            mh += '<option value="__novo">✚ Criar tipo novo…</option></select>';
            mais.innerHTML = mh;
            var txt = juntar(lista);
            prev.classList.toggle('longo', txt.length > MAX);
            prev.innerHTML = txt ? 'Vai gravar: <b>' + esc(txt) + '</b>' + (txt.length > MAX ? ' — passou de ' + MAX + ' letras, abrevie' : '') : '';
            var ev = linha.querySelector('.qe-ed input');
            if (ev) { ev.focus(); ev.select(); }
        }
        function editor() {
            var v = editando.i >= 0 ? lista[editando.i].valor : '';
            return '<span class="qe-ed"><b>' + esc(editando.tipo || 'TEXTO') + '</b>'
                 + '<input type="text" maxlength="40" value="' + esc(v) + '" placeholder="' + (editando.tipo ? 'nº ou letra' : 'texto') + '">'
                 + '<button type="button" class="qe-ok">OK</button><button type="button" class="qe-x" aria-label="Cancelar">×</button></span>';
        }
        function confirmar() {
            var ev = linha.querySelector('.qe-ed input');
            if (!ev || !editando) return;
            var v = mai(ev.value).replace(/^[\-–\/.]+|[\-–\/]+$/g, '').trim();
            if (!editando.tipo && !v) { cancelar(); return; }
            var nova = lista.slice(), p = { tipo: editando.tipo, valor: v };
            if (editando.i >= 0) nova[editando.i] = p; else nova.push(p);
            if (juntar(nova).length > MAX) { aviso('Não cabe: o complemento passaria de ' + MAX + ' letras. Abrevie ou tire uma parte.'); return; }
            aviso('');
            lista = nova; editando = null; salvar(); desenhar();
        }
        function cancelar() { editando = null; aviso(''); desenhar(); }
        function abrir(tipo) { editando = { i: -1, tipo: tipo }; novo.hidden = true; desenhar(); }

        box.addEventListener('click', function (e) {
            var t = e.target;
            if (t.closest('[data-tirar]')) {
                lista.splice(+t.closest('[data-tirar]').getAttribute('data-tirar'), 1);
                editando = null; salvar(); desenhar(); return;
            }
            if (t.closest('.qe-ok')) { confirmar(); return; }
            if (t.closest('.qe-x')) { cancelar(); return; }
            var add = t.closest('.qe-add');
            if (add) { abrir(add.getAttribute('data-tipo')); return; }
            var chip = t.closest('.qe-chip');
            if (chip) { var i = +chip.getAttribute('data-i'); editando = { i: i, tipo: lista[i].tipo }; desenhar(); return; }
            if (t.closest('.qe-cancelar')) { novo.hidden = true; return; }
            if (t.closest('.qe-criar')) { criarTipo(); }
        });
        box.addEventListener('change', function (e) {
            if (!e.target.classList.contains('qe-outros')) return;
            var v = e.target.value;
            e.target.value = '';
            if (v === '__novo') { editando = null; desenhar(); novo.hidden = false; novo.querySelector('input').focus(); return; }
            if (v) abrir(v);
        });
        box.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && e.target.closest('.qe-ed')) { e.preventDefault(); confirmar(); }
            else if (e.key === 'Escape' && e.target.closest('.qe-ed')) { e.preventDefault(); cancelar(); }
            else if (e.key === 'Enter' && e.target.closest('.qe-novo')) { e.preventDefault(); criarTipo(); }
        });

        function criarTipo() {
            var campo = novo.querySelector('input');
            var nome = mai(campo.value);
            if (nome.length < 2) { campo.focus(); return; }
            var ja = TIPOS.filter(function (t) { return semAcento(t) === semAcento(nome); })[0];
            if (ja) { novo.hidden = true; campo.value = ''; abrir(ja); return; }
            if (!confirm('Criar o tipo de complemento "' + nome + '"?\n\nEle passa a aparecer para toda a equipe, e fica registrado que foi você quem criou.')) return;
            var tk = document.querySelector('meta[name=csrf-token]');
            fetch(URL_NOVO, {
                method: 'POST', credentials: 'same-origin',
                headers: Object.assign({ 'Content-Type': 'application/json', Accept: 'application/json' },
                                       tk ? { 'X-CSRFToken': tk.content } : {}),
                body: JSON.stringify({ nome: nome })
            }).then(function (r) { return r.json().catch(function () { return { ok: false }; }); })
              .then(function (d) {
                  if (!d.ok) { aviso(d.erro || 'Não consegui criar o tipo. Tente de novo.'); return; }
                  if (TIPOS.indexOf(d.nome) < 0) TIPOS.push(d.nome);
                  campo.value = ''; novo.hidden = true; aviso('');
                  document.querySelectorAll('input[data-end-complemento]').forEach(function (x) { if (x._qeDesenhar) x._qeDesenhar(); });
                  abrir(d.nome);
              }).catch(function () { aviso('Não consegui criar o tipo. Tente de novo.'); });
        }

        inp._qeDesenhar = desenhar;
        vigiar(inp, ler);
        ler();
        tiposCarregar().then(function () { if (!editando) ler(); else desenhar(); });
    }


    // ============ CEP: o que os Correios preenchem fica travado; logradouro = tipo + nome ============
    // Anderson (10/11/2026): "Se digitamos um CEP que tem cadastro tudo certinho o sistema puxa... mas vamos
    // supor que está incorreto alguma coisa, ai a pessoa digitaria, mas teria que ter um botão de certeza de
    // fazer essa alteração"; "tem casos que são CEP de Cidade, ai não puxa nome de rua avenida viela travessa
    // rodovia... teríamos que ter a opção de escolher o logradouro, digitar o nome da RUA/avenida".
    var TIPOS_LOG = ['RUA', 'AVENIDA', 'RODOVIA', 'ESTRADA', 'ALAMEDA', 'TRAVESSA', 'VIELA', 'PRAÇA', 'VIA', 'CAMINHO',
                     'LADEIRA', 'BECO', 'LARGO', 'MARGINAL', 'ACESSO', 'ANEL VIÁRIO', 'QUADRA', 'SETOR', 'CONJUNTO',
                     'ÁREA', 'VILA', 'CONDOMÍNIO', 'LOTEAMENTO', 'CHÁCARA', 'SÍTIO', 'FAZENDA', 'COMUNIDADE'];
    var APELIDOS_LOG = { R: 'RUA', AV: 'AVENIDA', AVN: 'AVENIDA', AL: 'ALAMEDA', TV: 'TRAVESSA', TRAV: 'TRAVESSA',
                         ROD: 'RODOVIA', EST: 'ESTRADA', ESTR: 'ESTRADA', PC: 'PRAÇA', PCA: 'PRAÇA',
                         LGO: 'LARGO', LD: 'LADEIRA', COND: 'CONDOMÍNIO', LOT: 'LOTEAMENTO', CONJ: 'CONJUNTO' };
    var CAMPOS_CEP = [['logradouro', 'logradouro', 'Logradouro'], ['bairro', 'bairro', 'Bairro'],
                      ['cidade', 'localidade', 'Cidade'], ['uf', 'uf', 'UF']];

    function chave(v) { return semAcento(v).replace(/[^A-Z0-9]/g, ''); }
    function separarLog(v) {
        var t = mai(v);
        if (!t) return { tipo: '', nome: '' };
        var p = t.split(' '), w = semAcento(p[0]).replace(/\.$/, '');
        var dois = p.length > 1 ? semAcento(p[0] + ' ' + p[1]) : '';
        for (var i = 0; i < TIPOS_LOG.length; i++) {
            var tt = semAcento(TIPOS_LOG[i]);
            if (tt.indexOf(' ') > 0 && dois === tt) return { tipo: TIPOS_LOG[i], nome: p.slice(2).join(' ') };
            if (w === tt) return { tipo: TIPOS_LOG[i], nome: p.slice(1).join(' ') };
        }
        var ap = APELIDOS_LOG[w];
        if (ap && p.length > 1) return { tipo: ap, nome: p.slice(1).join(' ') };
        return { tipo: '', nome: t };
    }
    function valorDe(el) { return el.tagName === 'SELECT' ? el.value : nativo.get.call(el); }

    function montarCep(cepInp) {
        if (cepInp._qe) return;
        cepInp._qe = true;
        var raiz = cepInp.form || document;
        function campo(k) { return raiz.querySelector('[data-end-' + k + ']'); }
        var aviso = document.createElement('small');
        aviso.className = 'qe-cep-aviso';
        cepInp.insertAdjacentElement('afterend', aviso);
        var velho = document.getElementById('cepAviso');
        if (velho && raiz.contains(velho)) velho.style.display = 'none';
        var ultimo = '';

        function dizer(cls, t) { aviso.className = 'qe-cep-aviso ' + (cls || ''); aviso.textContent = t || ''; }

        function caixa(el) {
            if (el._qeTv) return el._qeTv;
            var alvo = el._qeVisivel || el;          // logradouro: a caixa vai em volta do tipo + nome
            var box = document.createElement('span');
            box.className = 'qe-tv';
            alvo.parentNode.insertBefore(box, alvo);
            box.appendChild(alvo);
            var b = document.createElement('button');
            b.type = 'button'; b.className = 'qe-alt';
            b.innerHTML = '<i class="fas fa-pen"></i> alterar';
            box.appendChild(b);
            var nota = document.createElement('small');
            box.insertAdjacentElement('afterend', nota);
            b.addEventListener('click', function () {
                var rot = el.getAttribute('data-end-rotulo') || 'Campo';
                if (!confirm('O CEP ' + mai(cepInp.value) + ' (Correios) diz:\n\n' + rot + ': ' + valorDe(el) +
                             '\n\nTem certeza que quer alterar? Altere só se o endereço dos Correios estiver errado.')) return;
                destravar(el, true);
                var foco = el._qeFoco || el; foco.focus(); if (foco.select) foco.select();
            });
            el._qeTv = { box: box, nota: nota };
            return el._qeTv;
        }
        function travar(el) {
            var c = caixa(el);
            c.box.classList.add('on');
            if (el._qeTravar) el._qeTravar(true);
            else if (el.tagName === 'SELECT') el.setAttribute('tabindex', '-1');
            else el.readOnly = true;
            el._qeTravado = true;
            c.nota.className = ''; c.nota.textContent = '';
        }
        function destravar(el, porMao) {
            if (!el._qeTv) return;
            el._qeTv.box.classList.remove('on');
            if (el._qeTravar) el._qeTravar(false);
            else if (el.tagName === 'SELECT') el.removeAttribute('tabindex');
            else el.readOnly = false;
            el._qeTravado = false;
            el._qeTv.nota.className = 'qe-tv-dif';
            el._qeTv.nota.textContent = porMao ? 'Alterado à mão — os Correios dizem: ' + (el._qeCep || '') : '';
        }
        function por(el, v) {
            if (el.tagName === 'SELECT') { el.value = v; el.dispatchEvent(new Event('change', { bubbles: true })); }
            else { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }
        }
        function soltarTudo() {
            CAMPOS_CEP.forEach(function (c) { var el = campo(c[0]); if (el && el._qeTravado) destravar(el, false); });
        }

        function aplicar(j, soConferir) {
            CAMPOS_CEP.forEach(function (c) {
                var el = campo(c[0]);
                if (!el) return;
                el.setAttribute('data-end-rotulo', c[2]);
                var v = mai(j[c[1]]);
                if (c[0] === 'uf') v = v.slice(0, 2);
                var antes = el._qeCep;
                el._qeCep = v;
                if (!v) {                                   // o CEP não diz (CEP geral da cidade)
                    // o que veio do CEP anterior sai junto (não fica a rua de outro CEP)
                    var doAnterior = antes && chave(valorDe(el)) === chave(antes);
                    if (el._qeTravado || doAnterior) { destravar(el, false); if (!soConferir) por(el, ''); }
                    return;
                }
                var atual = valorDe(el);
                if (soConferir && atual && chave(atual) !== chave(v)) {   // gravado diferente do CEP: só avisa
                    caixa(el); destravar(el, false);
                    el._qeTv.nota.textContent = 'Diferente do CEP — os Correios dizem: ' + v;
                    return;
                }
                if (!soConferir || !atual) por(el, v);
                travar(el);
            });
            var log = campo('logradouro');
            if (j.logradouro) dizer('ok', soConferir ? '' : 'Endereço dos Correios. Para mudar, use "alterar".');
            else {
                dizer('geral', 'CEP geral da cidade: escolha o tipo (Rua, Avenida, Rodovia…), digite o nome e o bairro.');
                if (log && !soConferir && log._qeFoco) log._qeFoco.focus();
            }
        }
        function buscar(soConferir) {
            var d = (cepInp.value || '').replace(/\D/g, '');
            if (d.length !== 8) {
                if (!soConferir && ultimo) { soltarTudo(); ultimo = ''; dizer('', ''); }
                return;
            }
            if (d === ultimo) return;
            ultimo = d;
            if (!soConferir) dizer('busca', 'Buscando o endereço do CEP…');
            fetch('/api/cep/' + d, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
                .then(function (r) { return r.json().catch(function () { return { erro: true }; }); })
                .then(function (j) {
                    if (d !== (cepInp.value || '').replace(/\D/g, '')) return;
                    if (j && j.indisponivel) { dizer('erro', 'Não consegui consultar o CEP agora — preencha à mão.'); ultimo = ''; return; }
                    if (!j || j.erro || !(j.logradouro || j.localidade)) {
                        dizer('erro', 'CEP não encontrado nos Correios — confira o número. Se estiver certo, preencha à mão.');
                        soltarTudo();
                        return;
                    }
                    aplicar(j, soConferir);
                }).catch(function () { dizer('erro', 'Não consegui consultar o CEP agora — preencha à mão.'); ultimo = ''; });
        }
        cepInp.addEventListener('input', function () { buscar(false); });
        cepInp.addEventListener('blur', function () { buscar(false); });
        vigiar(cepInp, function () { setTimeout(function () { buscar(false); }, 0); });
        // a tela abriu com CEP (editar): confere em silêncio e trava o que bate com os Correios
        if ((cepInp.value || '').replace(/\D/g, '').length === 8) buscar(true);
    }

    function montarLogradouro(inp) {
        if (inp._qe) return;
        inp._qe = true;
        inp.style.display = 'none';
        var box = document.createElement('div');
        box.className = 'qe-log';
        var sel = document.createElement('select');
        sel.className = inp.className;
        sel.setAttribute('aria-label', 'Tipo de logradouro');
        sel.innerHTML = '<option value="">Tipo…</option>' + TIPOS_LOG.map(function (t) {
            return '<option value="' + esc(t) + '">' + esc(t.charAt(0) + t.slice(1).toLowerCase()) + '</option>'; }).join('');
        var nome = document.createElement('input');
        nome.type = 'text';
        nome.className = inp.className;
        nome.placeholder = 'Nome (ex.: 137, GOIÁS, GO-020)';
        nome.style.textTransform = 'uppercase';
        if (inp.required) { inp.required = false; nome.required = true; }
        if (inp.id) {
            var lb = document.querySelector('label[for="' + inp.id + '"]');
            nome.id = inp.id + '_nome';
            if (lb) lb.setAttribute('for', nome.id);
        }
        box.appendChild(sel); box.appendChild(nome);
        inp.parentNode.insertBefore(box, inp.nextSibling);
        var msg = document.createElement('small');
        msg.className = 'qe-log-msg'; msg.hidden = true;
        box.insertAdjacentElement('afterend', msg);
        inp._qeVisivel = box; inp._qeFoco = nome;
        var travado = false;

        function ler() {
            var v = nativo.get.call(inp);
            if (travado) { sel.value = ''; nome.value = mai(v); conferir(); return; }
            var p = separarLog(v);
            sel.value = p.tipo; nome.value = p.nome;
            conferir();
        }
        function conferir() {
            var falta = !travado && !sel.value && !!mai(nome.value);
            box.classList.toggle('falta', falta);
            msg.hidden = !falta;
            msg.textContent = falta ? 'Escolha o tipo: Rua, Avenida, Rodovia…' : '';
        }
        function juntarLog() {
            if (travado) return;
            var n = mai(nome.value);
            if (!sel.value) {                         // digitou "RUA 137" no nome: o tipo sai sozinho
                var p = separarLog(n);
                if (p.tipo) { sel.value = p.tipo; n = p.nome; nome.value = n; }
            }
            gravar(inp, sel.value ? sel.value + (n ? ' ' + n : '') : n);
            conferir();
        }
        sel.addEventListener('change', juntarLog);
        nome.addEventListener('input', function () {
            if (travado) return;
            gravar(inp, (sel.value ? sel.value + ' ' : '') + mai(nome.value));
            conferir();
        });
        nome.addEventListener('blur', juntarLog);
        inp._qeTravar = function (on) {
            travado = on;
            sel.style.display = on ? 'none' : '';
            nome.readOnly = on;
            ler();
        };
        vigiar(inp, ler);
        ler();
    }

    function iniciar(raiz) {
        css();
        (raiz || document).querySelectorAll('input[data-end-numero]').forEach(montarNumero);
        (raiz || document).querySelectorAll('input[data-end-complemento]').forEach(montarComplemento);
        (raiz || document).querySelectorAll('input[data-end-logradouro]').forEach(montarLogradouro);
        (raiz || document).querySelectorAll('input[data-end-cep]').forEach(montarCep);
    }
    window.QEndereco = { iniciar: iniciar, separar: separar, juntar: juntar };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { iniciar(); });
    else iniciar();
})();
