// Botão "Hora" da faixa QUANDO (Entradas, Saídas, CT-e, NFS-e): abre uma janela
// à parte para recortar pelo horário de emissão, além da data. É para casos
// específicos — por isso não ocupa espaço no painel de filtros.
// A hora vale como data+hora: das 08:00 do dia de Início até as 12:00 do dia
// de Fim (o servidor monta em routes/escrita_fiscal.py, _filtro_hora). Sem
// hora aplicada nada é enviado e o filtro é só por data, como sempre.
(function () {
    let hora = {hora_ini: '', hora_fim: ''};
    let jan = null;

    function el(id) { return document.getElementById(id); }
    function br(iso) { return iso ? iso.split('-').reverse().join('/') : '—'; }

    function marcar() {
        const b = el('btnHora'), t = el('btnHoraTxt');
        if (!b) return;
        const ativo = !!(hora.hora_ini || hora.hora_fim);
        b.classList.toggle('verde', ativo);
        b.classList.toggle('azul', !ativo);
        if (t) t.textContent = ativo ? (hora.hora_ini || '00:00') + '–' + (hora.hora_fim || '23:59') : 'Hora';
    }

    function montar() {
        const css = document.createElement('style');
        css.textContent =
            '.fh-ov{position:fixed;inset:0;background:rgba(15,23,42,.45);z-index:3000;display:none;align-items:center;justify-content:center;padding:16px;}' +
            '.fh-ov.open{display:flex;}' +
            '.fh-box{background:#fff;border-radius:12px;box-shadow:0 20px 50px rgba(0,0,0,.25);width:100%;max-width:440px;font-size:13px;color:#334155;overflow:hidden;}' +
            '.fh-cab{background:#0f3d2e;color:#fff;padding:12px 16px;font-weight:700;display:flex;align-items:center;gap:8px;}' +
            '.fh-cab button{margin-left:auto;background:none;border:none;color:#fff;font-size:16px;cursor:pointer;}' +
            '.fh-corpo{padding:14px 16px;display:grid;gap:12px;}' +
            '.fh-per{background:#f8fafc;border:1px solid #e8eef3;border-radius:8px;padding:8px 10px;}' +
            '.fh-g{display:grid;grid-template-columns:1fr 1fr;gap:10px;}' +
            '.fh-g label{display:block;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;margin-bottom:4px;}' +
            '.fh-g input{width:100%;box-sizing:border-box;padding:7px 9px;border:1.5px solid #e2e8f0;border-radius:7px;font-size:13px;}' +
            '.fh-dica{font-size:12px;color:#64748b;line-height:1.45;}' +
            '.fh-erro{color:#b91c1c;font-size:12px;display:none;}' +
            '.fh-rod{display:flex;gap:8px;justify-content:flex-end;padding:0 16px 14px;flex-wrap:wrap;}' +
            '.fh-rod button{padding:7px 14px;border-radius:7px;font-weight:700;font-size:12.5px;cursor:pointer;border:1.5px solid #e2e8f0;background:#fff;color:#334155;}' +
            '.fh-rod .fh-ok{background:#16a34a;border-color:#16a34a;color:#fff;}' +
            '.fh-rod .fh-tirar{color:#b91c1c;margin-right:auto;}';
        document.head.appendChild(css);
        jan = document.createElement('div');
        jan.className = 'fh-ov';
        jan.innerHTML =
            '<div class="fh-box" role="dialog" aria-modal="true" aria-labelledby="fhTit">' +
            '<div class="fh-cab"><i class="fas fa-clock"></i><span id="fhTit">Filtrar por horário de emissão</span>' +
            '<button type="button" class="fh-x" title="Fechar">&times;</button></div>' +
            '<div class="fh-corpo">' +
            '<div class="fh-per">Período da busca: <b id="fhPer"></b></div>' +
            '<div class="fh-g"><div><label for="f_hora_ini">Hora início</label><input type="time" id="f_hora_ini"></div>' +
            '<div><label for="f_hora_fim">Hora fim</label><input type="time" id="f_hora_fim"></div></div>' +
            '<div class="fh-dica">A hora início vale para o <b>primeiro dia</b> do período e a hora fim para o ' +
            '<b>último dia</b>; os dias do meio entram inteiros. Para ver só um horário de um dia, ' +
            'coloque o mesmo dia em Início e Fim. Ex.: 10/10 a 10/10, das 08:00 às 12:00.</div>' +
            '<div class="fh-erro" id="fhErro"></div>' +
            '</div>' +
            '<div class="fh-rod"><button type="button" class="fh-tirar">Tirar hora</button>' +
            '<button type="button" class="fh-cancel">Cancelar</button>' +
            '<button type="button" class="fh-ok"><i class="fas fa-search"></i> Aplicar e buscar</button></div>' +
            '</div>';
        document.body.appendChild(jan);
        jan.addEventListener('click', function (ev) { if (ev.target === jan) fechar(); });
        jan.querySelector('.fh-x').onclick = fechar;
        jan.querySelector('.fh-cancel').onclick = fechar;
        jan.querySelector('.fh-tirar').onclick = function () { fechar(); window.limparHora(true); };
        jan.querySelector('.fh-ok').onclick = aplicar;
        jan.addEventListener('keydown', function (ev) {
            if (ev.key === 'Escape') fechar();
            if (ev.key === 'Enter') { ev.preventDefault(); aplicar(); }
        });
    }

    function fechar() { if (jan) jan.classList.remove('open'); }

    function aplicar() {
        const hi = el('f_hora_ini').value, hf = el('f_hora_fim').value;
        const di = (el('f_data_ini') || {}).value, df = (el('f_data_fim') || {}).value;
        const erro = el('fhErro');
        if (!hi && !hf) { erro.textContent = 'Preencha a hora início, a hora fim ou as duas.'; erro.style.display = 'block'; return; }
        if (hi && hf && di && di === df && hi > hf) {
            erro.textContent = 'No mesmo dia, a hora início precisa ser antes da hora fim.';
            erro.style.display = 'block';
            return;
        }
        hora = {hora_ini: hi, hora_fim: hf};
        fechar();
        marcar();
        if (typeof applyFilters === 'function') applyFilters();
    }

    window.abrirHora = function () {
        if (!jan) montar();
        el('f_hora_ini').value = hora.hora_ini;
        el('f_hora_fim').value = hora.hora_fim;
        el('fhErro').style.display = 'none';
        const di = (el('f_data_ini') || {}).value, df = (el('f_data_fim') || {}).value;
        el('fhPer').textContent = (di || df) ? br(di) + ' a ' + br(df) : 'sem data — a hora vale para todos os dias';
        jan.classList.add('open');
        el('f_hora_ini').focus();
    };

    // buscar=true refaz a busca só se havia hora aplicada (tirar o filtro muda
    // o resultado); o Limpar passa false porque já busca logo em seguida.
    window.limparHora = function (buscar) {
        const tinha = !!(hora.hora_ini || hora.hora_fim);
        hora = {hora_ini: '', hora_fim: ''};
        marcar();
        if (buscar && tinha && typeof applyFilters === 'function') applyFilters();
    };

    window.horaFiltros = function () { return {hora_ini: hora.hora_ini, hora_fim: hora.hora_fim}; };
})();
