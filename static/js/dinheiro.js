// Campo de dinheiro "de calculadora" (02/11/2026, pedido do Anderson): os dígitos entram
// pela direita — digitar 1000 mostra 10,00; digitar 10 mostra 0,10. Marque o input com
// class="dinheiro" (type="text"). O valor vai ao servidor como texto pt-BR ("1.800,00");
// quem lê usa _dec (nfse_emissor) ou equivalente, que aceita vírgula e ponto de milhar.
(function () {
    function fmt(centavos) {
        return (centavos / 100).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    function centavos(el) {
        return parseInt(String(el.value || '').replace(/\D/g, '') || '0', 10);
    }
    function ligar(el) {
        if (el.dataset.dinheiroOk) return;
        el.dataset.dinheiroOk = '1';
        el.setAttribute('inputmode', 'numeric');
        el.setAttribute('autocomplete', 'off');
        if (!el.placeholder) el.placeholder = '0,00';
        // valor vindo do servidor ("1800.00", "1800") vira 1.800,00; já formatado fica como está
        var v = String(el.value || '').trim();
        if (v && v.indexOf(',') < 0) {
            var n = parseFloat(v);
            el.value = isNaN(n) || n === 0 ? '' : fmt(Math.round(n * 100));
        }
        el.addEventListener('input', function () {
            var d = String(el.value || '').replace(/\D/g, '').replace(/^0+(?=\d)/, '');
            el.value = d ? fmt(parseInt(d, 10)) : '';
        });
        el.addEventListener('focus', function () { setTimeout(function () { el.setSelectionRange(el.value.length, el.value.length); }, 0); });
    }
    window.Dinheiro = {
        fmt: fmt, centavos: centavos,
        ligar: function (raiz) { (raiz || document).querySelectorAll('input.dinheiro').forEach(ligar); }
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { window.Dinheiro.ligar(); });
    else window.Dinheiro.ligar();
})();
