// NIRE e IE organizados enquanto digita (11/11/2026, Anderson: "arrume para o usuário digitar e aparecer
// organizado ... São Paulo o formato é XXX.XXX.XXX.XXX, já em GO é XX.XXX.XXX-X").
// NIRE: XX.XXX.XXX.XXX. IE: a máscara do estado (as mesmas do servidor, models/inscricao_estadual.py);
// enquanto digita usa a menor máscara que ainda cabe; número maior que todas fica só com os dígitos.
(function () {
    function aplicar(m, d) {
        var out = '', i = 0;
        for (var k = 0; k < m.length && i < d.length; k++) {
            if (m[k] === '#') out += d[i++];
            else out += m[k];
        }
        return out;
    }
    function conta(m) { return m.split('#').length - 1; }
    function ie(uf, v, formatos) {
        v = String(v || '');
        if (/[a-z]/i.test(v)) return v;               // ISENTO, letra do produtor rural de SP etc.
        var d = v.replace(/\D/g, ''), ms = (formatos || {})[String(uf || '').toUpperCase()] || [];
        if (!d || !ms.length) return v;
        ms = ms.slice().sort(function (a, b) { return conta(a) - conta(b); });
        for (var i = 0; i < ms.length; i++) if (d.length <= conta(ms[i])) return aplicar(ms[i], d);
        return d;
    }
    function nire(v) {
        v = String(v || '');
        if (/[a-z]/i.test(v)) return v;
        var d = v.replace(/\D/g, '');
        return d.length <= 11 ? aplicar('##.###.###.###', d) : d;
    }
    // liga um campo: fmt(valor) → valor organizado; o cursor vai para o fim (campo curto, só números)
    function ligar(inp, fmt) {
        if (!inp || inp._qcMasc) return;
        inp._qcMasc = true;
        var f = function () { var n = fmt(inp.value); if (n !== inp.value) inp.value = n; };
        inp.addEventListener('input', function (e) { if (e.inputType && e.inputType.indexOf('delete') === 0) return; f(); });
        inp.addEventListener('blur', f);
        f();
    }
    window.QCMascara = { ie: ie, nire: nire, ligar: ligar };
})();
