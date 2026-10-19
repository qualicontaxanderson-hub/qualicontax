// Paginação das conferências (Entradas, Saídas, CT-e, NFS-e): primeira/última,
// os números em volta da página atual (1 … 4 5 [6] 7 8 … 20) e "Ir para" com
// o número digitado (Enter). Cada tela chama montarPaginacao(id, total,
// porPagina, pagina, irPara) — irPara(p) é quem recarrega a lista dela.
(function () {
    const css = document.createElement('style');
    css.textContent =
        '.cc-pagination{flex-wrap:wrap;row-gap:6px;}' +
        '.cc-pagination .pg-n{padding:5px 0;min-width:32px;text-align:center;}' +
        '.cc-pagination .pg-n.atual,.cc-pagination .pg-n.atual:disabled{background:#16a34a;border-color:#16a34a;color:#fff;cursor:default;opacity:1;}' +
        '.cc-pagination .pg-ret{color:#94a3b8;padding:0 2px;}' +
        '.cc-pagination .pg-info{color:#64748b;margin-left:6px;}' +
        '.cc-pagination .pg-ir{display:inline-flex;align-items:center;gap:6px;margin-left:12px;color:#64748b;}' +
        '.cc-pagination .pg-ir input{width:64px;padding:5px 8px;border:1.5px solid #e2e8f0;border-radius:7px;font-size:12px;}' +
        '.cc-pagination .pg-ir button{padding:5px 12px;}';
    document.head.appendChild(css);

    // Quais números aparecem: sempre a 1ª e a última, e 2 de cada lado da atual.
    function numeros(pagina, paginas) {
        const set = new Set([1, paginas]);
        for (let i = pagina - 2; i <= pagina + 2; i++) if (i >= 1 && i <= paginas) set.add(i);
        const lista = Array.from(set).sort(function (a, b) { return a - b; });
        const out = [];
        lista.forEach(function (n, i) {
            if (i && n - lista[i - 1] > 1) out.push(n - lista[i - 1] === 2 ? n - 1 : null);
            out.push(n);
        });
        return out;
    }

    window.montarPaginacao = function (id, total, porPagina, pagina, irPara) {
        const el = document.getElementById(id);
        if (!el) return;
        total = Number(total) || 0;
        pagina = Number(pagina) || 1;
        const paginas = Math.ceil(total / (Number(porPagina) || 1));
        if (paginas <= 1) { el.innerHTML = ''; return; }
        const b = function (p, html, extra, titulo) {
            const off = (p < 1 || p > paginas || p === pagina) ? ' disabled' : '';
            return '<button type="button" data-p="' + p + '" class="' + (extra || '') + '"' + off +
                   (titulo ? ' title="' + titulo + '"' : '') + '>' + html + '</button>';
        };
        let h = b(1, '<i class="fas fa-angles-left"></i>', '', 'Primeira página') +
                b(pagina - 1, '<i class="fas fa-chevron-left"></i>', '', 'Página anterior');
        numeros(pagina, paginas).forEach(function (n) {
            if (n === null) { h += '<span class="pg-ret">…</span>'; return; }
            h += n === pagina
                ? '<button type="button" class="pg-n atual" disabled>' + n + '</button>'
                : b(n, String(n), 'pg-n');
        });
        h += b(pagina + 1, '<i class="fas fa-chevron-right"></i>', '', 'Próxima página') +
             b(paginas, '<i class="fas fa-angles-right"></i>', '', 'Última página') +
             '<span class="pg-info">Página ' + pagina + ' de ' + paginas + ' · ' +
             total.toLocaleString('pt-BR') + ' registro(s)</span>' +
             '<span class="pg-ir">Ir para <input type="number" min="1" max="' + paginas + '" ' +
             'placeholder="' + pagina + '" aria-label="Número da página">' +
             '<button type="button" class="pg-go">Ir</button></span>';
        el.innerHTML = h;

        el.querySelectorAll('button[data-p]').forEach(function (bt) {
            bt.onclick = function () { irPara(Number(bt.getAttribute('data-p'))); };
        });
        const inp = el.querySelector('.pg-ir input');
        const ir = function () {
            const p = parseInt(inp.value, 10);
            if (!p) return;
            irPara(Math.min(Math.max(p, 1), paginas));
        };
        el.querySelector('.pg-go').onclick = ir;
        inp.onkeydown = function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); ir(); } };
    };
})();
