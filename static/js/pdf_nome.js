// Menu do ícone de PDF de UMA nota (Entradas, Saídas, CT-e, NFS-e): escolher o
// nome do arquivo antes de baixar.
//   a) pela chave de acesso (como sempre foi)          -> ?nome=chave
//   b) Empresa - Nº da nota - Data                      -> ?nome=empresa
// O servidor monta o nome (routes/escrita_fiscal.py, _nome_pdf_unico). Uso no
// link do PDF: onclick="return pdfNomeMenu(event, this)". O último escolhido
// fica marcado (localStorage, só conveniência deste navegador).
(function () {
    const CHAVE_LS = 'qc.pdfNome';
    let menu = null, alvo = null;

    function ultimo() {
        try { return localStorage.getItem(CHAVE_LS) || 'empresa'; } catch (e) { return 'empresa'; }
    }
    function guardar(v) {
        try { localStorage.setItem(CHAVE_LS, v); } catch (e) { /* sem storage: só não lembra */ }
    }

    function montar() {
        const css = document.createElement('style');
        css.textContent =
            '.pdfn-menu{position:fixed;z-index:3000;background:#fff;border:1px solid #e2e8f0;border-radius:8px;' +
            'box-shadow:0 6px 20px rgba(0,0,0,.14);min-width:250px;overflow:hidden;display:none;font-size:13px;}' +
            '.pdfn-menu.open{display:block;}' +
            '.pdfn-tit{padding:8px 14px 6px;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.03em;}' +
            '.pdfn-menu a{display:flex;align-items:flex-start;gap:10px;padding:8px 14px;color:#334155;cursor:pointer;text-decoration:none;}' +
            '.pdfn-menu a:hover{background:#f1f5f9;}' +
            '.pdfn-menu a i{color:#b91c1c;margin-top:2px;width:14px;text-align:center;}' +
            '.pdfn-menu a small{display:block;color:#94a3b8;font-size:11px;margin-top:1px;}' +
            '.pdfn-menu a.ult b::after{content:" \\2713";color:#16a34a;}';
        document.head.appendChild(css);
        menu = document.createElement('div');
        menu.className = 'pdfn-menu';
        menu.innerHTML =
            '<div class="pdfn-tit">Nome do arquivo</div>' +
            '<a data-nome="empresa"><i class="fas fa-building"></i><span><b>Empresa + nº + data</b>' +
            '<small>Ex.: FORNECEDOR LTDA - NF 1234 - 17.10.2026</small></span></a>' +
            '<a data-nome="chave"><i class="fas fa-key"></i><span><b>Chave de acesso</b>' +
            '<small>Os 44 números da nota</small></span></a>';
        menu.addEventListener('click', function (ev) {
            ev.stopPropagation();
            const a = ev.target.closest('a[data-nome]');
            if (!a || !alvo) return;
            const nome = a.getAttribute('data-nome');
            guardar(nome);
            const href = alvo.getAttribute('href');
            fechar();
            window.location.href = href + (href.indexOf('?') >= 0 ? '&' : '?') + 'nome=' + nome;
        });
        document.body.appendChild(menu);
        document.addEventListener('click', fechar);
        document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') fechar(); });
        window.addEventListener('scroll', fechar, true);
        window.addEventListener('resize', fechar);
    }

    function fechar() {
        if (menu) menu.classList.remove('open');
        alvo = null;
    }

    window.pdfNomeMenu = function (ev, link) {
        ev.preventDefault();
        ev.stopPropagation();
        if (!menu) montar();
        if (alvo === link && menu.classList.contains('open')) { fechar(); return false; }
        alvo = link;
        const ult = ultimo();
        menu.querySelectorAll('a[data-nome]').forEach(function (a) {
            a.classList.toggle('ult', a.getAttribute('data-nome') === ult);
        });
        menu.classList.add('open');
        // Abaixo do ícone, alinhado pela direita; sobe se não couber na tela.
        const r = link.getBoundingClientRect();
        const w = menu.offsetWidth, h = menu.offsetHeight;
        let left = Math.min(r.right - w, window.innerWidth - w - 8);
        let top = r.bottom + 4;
        if (top + h > window.innerHeight - 8) top = r.top - h - 4;
        menu.style.left = Math.max(8, left) + 'px';
        menu.style.top = Math.max(8, top) + 'px';
        return false;
    };
})();
