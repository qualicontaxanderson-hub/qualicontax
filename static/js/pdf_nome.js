// Caixa do ícone de PDF de UMA nota (Entradas, Saídas, CT-e, NFS-e): escolher o
// nome do arquivo antes de baixar.
//   a) Empresa - Nº da nota - Data                      -> ?nome=empresa
//   b) pela chave de acesso (como sempre foi)          -> ?nome=chave
// O servidor monta o nome (routes/escrita_fiscal.py, _nome_pdf_unico). Uso no
// link do PDF: onclick="return pdfNomeMenu(event, this)". O último escolhido
// fica marcado (localStorage, só conveniência deste navegador).
//
// 27/11/2026 — Anderson (no iPhone): "quando abre a nota no celular não consigo compartilhar nem nada... fica uma tela
// inútil". No celular o download abria a pré-visualização do sistema, sem salvar nem mandar. Agora, em celular e tablet,
// o PDF é buscado e entregue pela FOLHA DE COMPARTILHAR do aparelho (WhatsApp, Salvar em Arquivos, e-mail, imprimir...).
// Sem suporte (ou se a folha falhar), cai no download de sempre. No computador nada muda.
// A caixa segue o molde das janelas do sistema (cabeçalho verde, opções em cartões); no celular sobe de baixo.
(function () {
    const CHAVE_LS = 'qc.pdfNome';
    let caixa = null, fundo = null, alvo = null;
    const CELULAR = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent)
        || (navigator.maxTouchPoints > 1 && /Mac/.test(navigator.platform));      // iPad se apresenta como Mac
    const COMPARTILHA = CELULAR && !!(navigator.share && navigator.canShare);

    function ultimo() {
        try { return localStorage.getItem(CHAVE_LS) || 'empresa'; } catch (e) { return 'empresa'; }
    }
    function guardar(v) {
        try { localStorage.setItem(CHAVE_LS, v); } catch (e) { /* sem storage: só não lembra */ }
    }

    function nomeDoCabecalho(cd) {
        // Content-Disposition: attachment; filename="x.pdf"; filename*=UTF-8''x.pdf
        if (!cd) return '';
        let m = /filename\*=(?:UTF-8|utf-8)''([^;]+)/.exec(cd);
        if (m) { try { return decodeURIComponent(m[1].trim()); } catch (e) { /* segue */ } }
        m = /filename="?([^";]+)"?/.exec(cd);
        return m ? m[1].trim() : '';
    }

    async function entregar(url) {
        if (COMPARTILHA) {
            try {
                const r = await fetch(url, { credentials: 'same-origin' });
                if (!r.ok) throw new Error('HTTP ' + r.status);
                const blob = await r.blob();
                const arq = new File([blob], nomeDoCabecalho(r.headers.get('content-disposition')) || 'nota.pdf', { type: 'application/pdf' });
                if (navigator.canShare({ files: [arq] })) {
                    await navigator.share({ files: [arq], title: arq.name });
                    return;
                }
            } catch (e) {
                if (e && e.name === 'AbortError') return;       // a pessoa fechou a folha: nada a fazer
                // sem permissão / sem suporte: cai no download comum
            }
        }
        window.location.href = url;
    }

    function montar() {
        const css = document.createElement('style');
        css.textContent =
            '.pdfn-fundo{position:fixed;inset:0;z-index:2999;background:rgba(15,61,46,.45);backdrop-filter:blur(2px);display:none;}' +
            '.pdfn-fundo.open{display:block;}' +
            '.pdfn-caixa{position:fixed;z-index:3000;left:50%;top:50%;transform:translate(-50%,-50%);width:min(440px,calc(100vw - 32px));' +
            'background:#fff;border-radius:18px;box-shadow:0 24px 60px rgba(15,61,46,.28);overflow:hidden;display:none;font-size:14px;color:#0f172a;}' +
            '.pdfn-caixa.open{display:block;}' +
            '.pdfn-cab{background:linear-gradient(120deg,#0f3d2e 0%,#166534 100%);color:#fff;padding:16px 56px 14px 20px;position:relative;}' +
            '.pdfn-cab b{display:block;font-size:17px;font-weight:800;letter-spacing:-.01em;}' +
            '.pdfn-cab small{display:block;font-size:12.5px;opacity:.85;margin-top:2px;}' +
            '.pdfn-x{position:absolute;right:12px;top:12px;width:34px;height:34px;border-radius:50%;border:0;background:rgba(255,255,255,.16);color:#fff;font-size:18px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;}' +
            '.pdfn-x:hover{background:rgba(255,255,255,.28);}' +
            '.pdfn-corpo{padding:14px 16px 6px;display:flex;flex-direction:column;gap:10px;}' +
            '.pdfn-op{display:flex;align-items:center;gap:12px;text-align:left;background:#fff;border:1.5px solid #e2e8f0;border-radius:14px;padding:12px 14px;font:inherit;cursor:pointer;color:#0f172a;width:100%;}' +
            '.pdfn-op:hover{border-color:#86efac;background:#f6fdf8;}' +
            '.pdfn-op.ult{border-color:#16a34a;background:#f0fdf4;}' +
            '.pdfn-op>i{width:38px;height:38px;border-radius:12px;background:#fee2e2;color:#b91c1c;display:inline-flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;}' +
            '.pdfn-op span{flex:1;min-width:0;}' +
            '.pdfn-op b{display:block;font-size:14.5px;font-weight:800;}' +
            '.pdfn-op small{display:block;color:#64748b;font-size:12px;margin-top:2px;}' +
            '.pdfn-op em{flex-shrink:0;font-style:normal;font-size:11px;font-weight:800;color:#15803d;background:#dcfce7;border-radius:99px;padding:3px 9px;}' +
            '.pdfn-dica{margin:8px 16px 14px;padding:10px 12px;border-radius:12px;font-size:12.5px;color:#166534;background:#f0fdf4;border:1px solid #dcfce7;line-height:1.4;display:flex;gap:8px;align-items:flex-start;}' +
            '.pdfn-dica i{margin-top:2px;}' +
            '.pdfn-pe{padding:0 16px 14px;display:flex;justify-content:flex-end;}' +
            '.pdfn-cancela{border:1.5px solid #e2e8f0;background:#fff;border-radius:11px;padding:9px 16px;font:inherit;font-weight:700;color:#334155;cursor:pointer;}' +
            '@media (max-width:640px){' +
            '.pdfn-caixa{left:0;right:0;top:auto;bottom:0;transform:none;width:100%;border-radius:20px 20px 0 0;padding-bottom:env(safe-area-inset-bottom);}' +
            '.pdfn-cab{padding:18px 56px 14px 18px;} .pdfn-op{padding:14px;} .pdfn-op b{font-size:15.5px;} .pdfn-cancela{width:100%;padding:13px;font-size:15px;}}';
        document.head.appendChild(css);
        fundo = document.createElement('div');
        fundo.className = 'pdfn-fundo';
        caixa = document.createElement('div');
        caixa.className = 'pdfn-caixa';
        caixa.setAttribute('role', 'dialog');
        caixa.setAttribute('aria-modal', 'true');
        caixa.setAttribute('aria-label', 'PDF da nota');
        caixa.innerHTML =
            '<div class="pdfn-cab"><b>PDF da nota</b><small>' +
            (COMPARTILHA ? 'escolha o nome do arquivo e mande ou salve pelo celular' : 'escolha como o arquivo vai se chamar') +
            '</small><button type="button" class="pdfn-x" aria-label="Fechar">&times;</button></div>' +
            '<div class="pdfn-corpo">' +
            '<button type="button" class="pdfn-op" data-nome="empresa"><i class="fas fa-building"></i><span><b>Empresa + nº + data</b>' +
            '<small>Ex.: FORNECEDOR LTDA - NF 1234 - 17.10.2026</small></span><em hidden>último</em></button>' +
            '<button type="button" class="pdfn-op" data-nome="chave"><i class="fas fa-key"></i><span><b>Chave de acesso</b>' +
            '<small>Os 44 números da nota</small></span><em hidden>último</em></button>' +
            '</div>' +
            (COMPARTILHA
                ? '<div class="pdfn-dica"><i class="fas fa-share-nodes"></i><span>Vai abrir a folha de compartilhar do celular: WhatsApp, Salvar em Arquivos, e-mail, imprimir.</span></div>'
                : '<div class="pdfn-dica"><i class="fas fa-download"></i><span>O arquivo vai para a pasta de downloads.</span></div>') +
            '<div class="pdfn-pe"><button type="button" class="pdfn-cancela">Cancelar</button></div>';
        caixa.addEventListener('click', function (ev) {
            ev.stopPropagation();
            if (ev.target.closest('.pdfn-x, .pdfn-cancela')) { fechar(); return; }
            const b = ev.target.closest('.pdfn-op');
            if (!b || !alvo) return;
            const nome = b.getAttribute('data-nome');
            guardar(nome);
            const href = alvo.getAttribute('href');
            fechar();
            entregar(href + (href.indexOf('?') >= 0 ? '&' : '?') + 'nome=' + nome);
        });
        fundo.addEventListener('click', fechar);
        document.body.appendChild(fundo);
        document.body.appendChild(caixa);
        document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') fechar(); });
    }

    function fechar() {
        if (caixa) { caixa.classList.remove('open'); fundo.classList.remove('open'); }
        alvo = null;
    }

    window.pdfNomeMenu = function (ev, link) {
        ev.preventDefault();
        ev.stopPropagation();
        if (!caixa) montar();
        alvo = link;
        const ult = ultimo();
        caixa.querySelectorAll('.pdfn-op').forEach(function (b) {
            const e = b.getAttribute('data-nome') === ult;
            b.classList.toggle('ult', e);
            b.querySelector('em').hidden = !e;
        });
        fundo.classList.add('open');
        caixa.classList.add('open');
        return false;
    };
})();
