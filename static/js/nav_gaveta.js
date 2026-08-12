/* ==========================================================================
   GAVETA DE DEPARTAMENTOS (mobile) — arrastar o dedo da borda esquerda.

   Por que existe
   --------------
   Abaixo de 600px o `.nav-rail` era `display: none` e nada entrava no lugar:
   o celular ficava sem menu. Aqui o MESMO rail vira gaveta. Três formas de
   abrir, porque gesto sozinho não se descobre e nem sempre está disponível:
     1. arrastar o dedo da borda esquerda para a direita;
     2. tocar no botão de menu da barra verde;
     3. teclado (o botão é <button>, então Enter/Espaço já funcionam).

   Sobre o Safari
   --------------
   No Safari FORA do PWA, o arrasto começando na borda é o "voltar" do próprio
   navegador — e o navegador ganha. Por isso a zona de captura começa alguns
   pixels PARA DENTRO (ZONA_INICIO) e o botão existe como caminho garantido.
   Instalado como PWA (o projeto já tem manifest e apple-mobile-web-app-capable),
   não há gesto de voltar e o arrasto da borda funciona limpo.

   Nada aqui roda no desktop: o guard é a media query, consultada por
   matchMedia — a mesma fonte da verdade do CSS, para os dois não divergirem.
   ========================================================== */
(function () {
    'use strict';

    var LARGURA_MAX = 600;                 // igual ao @media do style.css
    var ZONA_INICIO = 28;                  // px da borda que capturam o arrasto
    var ABRIR_EM = 0.35;                   // fração da largura que "solta" aberto
    var VEL_MINIMA = 0.4;                  // px/ms — flick rápido abre/fecha

    var gaveta = document.getElementById('navRail');
    var veu = document.getElementById('navScrim');
    var botao = document.getElementById('navToggle');
    if (!gaveta || !veu) { return; }

    var mq = window.matchMedia('(max-width: ' + LARGURA_MAX + 'px)');
    var largura = 0;
    var aberta = false;
    var arrastando = false;
    var x0 = 0, y0 = 0, t0 = 0, dx = 0, decidiu = false;

    function ehMobile() { return mq.matches; }

    function medir() {
        largura = gaveta.getBoundingClientRect().width || 280;
    }

    function abrir() {
        medir();
        aberta = true;
        gaveta.style.transform = '';
        gaveta.classList.add('aberta');
        veu.classList.add('visivel');
        if (botao) { botao.setAttribute('aria-expanded', 'true'); }
        // Trava a rolagem do fundo: sem isto, arrastar dentro da gaveta rola a
        // página atrás dela.
        document.body.style.overflow = 'hidden';
    }

    function fechar() {
        aberta = false;
        gaveta.style.transform = '';
        gaveta.classList.remove('aberta');
        veu.classList.remove('visivel');
        if (botao) { botao.setAttribute('aria-expanded', 'false'); }
        document.body.style.overflow = '';
    }

    function alternar() { aberta ? fechar() : abrir(); }

    // ---- Abertura por botão, véu e teclado ----------------------------------
    if (botao) {
        botao.addEventListener('click', function (e) {
            e.preventDefault();
            alternar();
        });
    }
    veu.addEventListener('click', fechar);
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && aberta) { fechar(); }
    });

    // Clicou num departamento: a navegação já leva para outra página, mas
    // fechar aqui evita a gaveta "piscar" aberta durante o carregamento.
    gaveta.addEventListener('click', function (e) {
        if (e.target.closest('a')) { fechar(); }
    });

    // Voltou para o desktop (girou a tela, redimensionou): desfaz tudo, senão
    // o body fica travado e a gaveta com transform inline.
    function aoTrocarDeFaixa() {
        if (!ehMobile()) {
            gaveta.style.transform = '';
            gaveta.classList.remove('aberta', 'arrastando');
            veu.classList.remove('visivel');
            document.body.style.overflow = '';
            aberta = false;
        }
    }
    if (mq.addEventListener) {
        mq.addEventListener('change', aoTrocarDeFaixa);
    } else if (mq.addListener) {
        mq.addListener(aoTrocarDeFaixa);       // Safari antigo
    }

    // ---- O arrasto ----------------------------------------------------------
    document.addEventListener('touchstart', function (e) {
        if (!ehMobile() || e.touches.length !== 1) { return; }
        var t = e.touches[0];
        // Só começa perto da borda (fechada) ou em qualquer ponto (aberta,
        // para poder empurrar de volta).
        if (!aberta && t.clientX > ZONA_INICIO) { return; }
        medir();
        x0 = t.clientX;
        y0 = t.clientY;
        t0 = e.timeStamp;
        dx = 0;
        decidiu = false;
        arrastando = true;
    }, { passive: true });

    document.addEventListener('touchmove', function (e) {
        if (!arrastando || e.touches.length !== 1) { return; }
        var t = e.touches[0];
        var ax = t.clientX - x0;
        var ay = t.clientY - y0;

        // Primeiro movimento decide de quem é o gesto. Mais vertical que
        // horizontal = é rolagem da página; larga e não interfere.
        if (!decidiu) {
            if (Math.abs(ax) < 8 && Math.abs(ay) < 8) { return; }
            if (Math.abs(ay) > Math.abs(ax)) { arrastando = false; return; }
            decidiu = true;
            gaveta.classList.add('arrastando');
            veu.classList.add('visivel');
        }

        dx = ax;
        var base = aberta ? 0 : -largura;
        var pos = Math.max(-largura, Math.min(0, base + dx));
        gaveta.style.transform = 'translateX(' + pos + 'px)';
        veu.style.opacity = String((1 + pos / largura).toFixed(3));

        // O gesto é nosso: impede a rolagem/zoom do navegador por baixo.
        if (e.cancelable) { e.preventDefault(); }
    }, { passive: false });

    document.addEventListener('touchend', function (e) {
        if (!arrastando) { return; }
        arrastando = false;
        gaveta.classList.remove('arrastando');
        veu.style.opacity = '';
        if (!decidiu) { return; }

        var dt = Math.max(1, e.timeStamp - t0);
        var vel = dx / dt;                       // px/ms, positivo = para a direita
        var andou = Math.abs(dx) / (largura || 1);

        if (vel > VEL_MINIMA) { abrir(); }
        else if (vel < -VEL_MINIMA) { fechar(); }
        else if (aberta) { andou > ABRIR_EM ? fechar() : abrir(); }
        else { andou > ABRIR_EM ? abrir() : fechar(); }
    }, { passive: true });

    // Toque cancelado pelo sistema (chamada, notificação): volta ao estado
    // estável em vez de ficar no meio do caminho.
    document.addEventListener('touchcancel', function () {
        if (!arrastando) { return; }
        arrastando = false;
        gaveta.classList.remove('arrastando');
        veu.style.opacity = '';
        aberta ? abrir() : fechar();
    }, { passive: true });
})();
