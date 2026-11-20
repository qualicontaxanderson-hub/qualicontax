// Qualicontax - Scripts principais

document.addEventListener('DOMContentLoaded', function() {

    // ── Barra de abas do departamento (.nav-fiscal): setas nas pontas quando não cabe tudo + roda do mouse ──
    // 20/11/2026 — Anderson, num monitor menor: "vai até Fluxo de Caixa e não consigo ir para frente mais".
    document.querySelectorAll('.nav-fiscal').forEach(function (nav) {
        var tr = nav.querySelector('.nav-fiscal-track');
        if (!tr) return;
        function seta(lado, ico) {
            var b = document.createElement('button');
            b.type = 'button'; b.className = 'nff-seta ' + lado; b.setAttribute('aria-label', lado === 'esq' ? 'Abas anteriores' : 'Mais abas');
            b.innerHTML = '<i class="fas ' + ico + '"></i>';
            b.addEventListener('click', function () { tr.scrollBy({ left: (lado === 'esq' ? -1 : 1) * Math.max(160, tr.clientWidth * 0.6), behavior: 'smooth' }); });
            nav.appendChild(b); return b;
        }
        seta('esq', 'fa-chevron-left'); seta('dir', 'fa-chevron-right');
        function medir() {
            var sobra = tr.scrollWidth - tr.clientWidth;
            nav.classList.toggle('tem-esq', sobra > 4 && tr.scrollLeft > 4);
            nav.classList.toggle('tem-dir', sobra > 4 && tr.scrollLeft < sobra - 4);
        }
        tr.addEventListener('scroll', medir, { passive: true });
        window.addEventListener('resize', medir);
        if (window.ResizeObserver) new ResizeObserver(medir).observe(tr);
        tr.addEventListener('wheel', function (e) {
            if (tr.scrollWidth - tr.clientWidth <= 4 || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
            e.preventDefault(); tr.scrollLeft += e.deltaY;
        }, { passive: false });
        setTimeout(medir, 50);
    });

    // ── Nav panel toggle (hamburger button in top bar) ──
    const navPanel    = document.querySelector('.nav-panel');
    const mainArea    = document.querySelector('.main-area');
    const panelToggle = document.querySelector('#navPanelToggle');

    function isPanelVisible() {
        return navPanel && !navPanel.classList.contains('hidden') && !navPanel.classList.contains('mobile-open') === false;
    }

    if (panelToggle && navPanel && mainArea) {
        // Restore saved state
        const savedHidden = localStorage.getItem('navPanelHidden') === 'true';
        if (savedHidden) {
            navPanel.classList.add('hidden');
            mainArea.classList.add('panel-hidden');
        }

        panelToggle.addEventListener('click', function() {
            const isMobile = window.innerWidth <= 900;
            if (isMobile) {
                // Mobile: slide in/out as overlay
                navPanel.classList.toggle('mobile-open');
            } else {
                // Desktop: toggle panel + adjust main area margin
                navPanel.classList.toggle('hidden');
                mainArea.classList.toggle('panel-hidden');
                localStorage.setItem('navPanelHidden', navPanel.classList.contains('hidden'));
            }
        });

        // Close panel overlay when clicking outside on mobile
        document.addEventListener('click', function(e) {
            if (window.innerWidth <= 900 &&
                navPanel.classList.contains('mobile-open') &&
                !navPanel.contains(e.target) &&
                !panelToggle.contains(e.target)) {
                navPanel.classList.remove('mobile-open');
            }
        });
    }

    // ── Panel expandable sections ──
    document.querySelectorAll('.panel-section-toggle').forEach(function(btn) {
        // Open sections that contain the active link automatically
        const section = document.getElementById(btn.dataset.target);
        if (section && section.querySelector('.active')) {
            btn.classList.add('open');
        }

        btn.addEventListener('click', function() {
            this.classList.toggle('open');
        });
    });

    // ── Panel sub-section toggles (Conciliação Bancária) ──
    document.querySelectorAll('.panel-subsection-toggle').forEach(function(btn) {
        const section = document.getElementById(btn.dataset.target);
        if (section && section.querySelector('.active')) {
            btn.classList.add('open');
        }

        btn.addEventListener('click', function() {
            this.classList.toggle('open');
        });
    });

    // ── Profile dropdown ──
    const profileToggle = document.querySelector('#profileToggle');
    const profileMenu   = document.querySelector('#profileMenu');
    if (profileToggle && profileMenu) {
        profileToggle.addEventListener('click', function(e) {
            e.stopPropagation();
            profileMenu.classList.toggle('show');
        });

        document.addEventListener('click', function(e) {
            if (!profileToggle.contains(e.target)) {
                profileMenu.classList.remove('show');
            }
        });
    }

    // ── Avisos do sistema (19/11/2026): verde/azul somem em 4,5 s (param com o mouse em cima);
    //    vermelho/amarelo ficam até "Fechar", o × ou Esc. Quando o último sai, o fundo escurecido sai junto. ──
    const caixaAvisos = document.getElementById('qcAvisos');
    if (caixaAvisos) {
        function tirar(alert) {
            if (!alert || alert.classList.contains('saindo')) return;
            alert.classList.add('saindo');
            setTimeout(function () {
                alert.remove();
                if (!caixaAvisos.querySelector('.alert.fica')) caixaAvisos.classList.remove('com-fundo');
                if (!caixaAvisos.querySelector('.alert')) caixaAvisos.remove();
            }, 260);
        }
        caixaAvisos.querySelectorAll('.alert').forEach(function (alert) {
            alert.querySelectorAll('.alert-close, .alert-fechar').forEach(function (b) { b.addEventListener('click', function () { tirar(alert); }); });
            if (alert.classList.contains('some')) {
                let resta = 4500, inicio = Date.now(), t = setTimeout(function () { tirar(alert); }, resta);
                alert.addEventListener('mouseenter', function () { clearTimeout(t); resta -= Date.now() - inicio; });
                alert.addEventListener('mouseleave', function () { inicio = Date.now(); t = setTimeout(function () { tirar(alert); }, Math.max(resta, 800)); });
            }
        });
        caixaAvisos.addEventListener('click', function (e) { if (e.target === caixaAvisos) { /* clicou fora dos cartões: nada */ } });
        document.addEventListener('keydown', function (e) { if (e.key === 'Escape') caixaAvisos.querySelectorAll('.alert.fica').forEach(tirar); });
    }

});

// Global function for submenu toggle (can be called from onclick)
function toggleSubmenu(element) {
    const parent = element.parentElement;
    parent.classList.toggle('open');
}

// Máscaras de input
function maskCPF(input) {
    let value = input.value.replace(/\D/g, '');
    if (value.length <= 11) {
        value = value.replace(/(\d{3})(\d)/, '$1.$2');
        value = value.replace(/(\d{3})(\d)/, '$1.$2');
        value = value.replace(/(\d{3})(\d{1,2})$/, '$1-$2');
    }
    input.value = value;
}

function maskCNPJ(input) {
    let value = input.value.replace(/\D/g, '');
    if (value.length <= 14) {
        value = value.replace(/^(\d{2})(\d)/, '$1.$2');
        value = value.replace(/^(\d{2})\.(\d{3})(\d)/, '$1.$2.$3');
        value = value.replace(/\.(\d{3})(\d)/, '.$1/$2');
        value = value.replace(/(\d{4})(\d)/, '$1-$2');
    }
    input.value = value;
}

function maskPhone(input) {
    let value = input.value.replace(/\D/g, '');
    if (value.length <= 10) {
        // Telefone fixo: (XX) XXXX-XXXX
        value = value.replace(/^(\d{2})(\d)/, '($1) $2');
        value = value.replace(/(\d{4})(\d)/, '$1-$2');
    } else if (value.length <= 11) {
        // Celular: (XX) XXXXX-XXXX
        value = value.replace(/^(\d{2})(\d)/, '($1) $2');
        value = value.replace(/(\d{5})(\d)/, '$1-$2');
    }
    input.value = value;
}

function maskCelular(input) {
    let value = input.value.replace(/\D/g, '');
    if (value.length <= 11) {
        // Celular: (XX) XXXXX-XXXX (sempre 9 dígitos após DDD)
        value = value.replace(/^(\d{2})(\d)/, '($1) $2');
        value = value.replace(/(\d{5})(\d)/, '$1-$2');
    }
    input.value = value;
}

function maskCEP(input) {
    let value = input.value.replace(/\D/g, '');
    if (value.length <= 8) {
        value = value.replace(/^(\d{5})(\d)/, '$1-$2');
    }
    input.value = value;
}

// Aplicar máscaras automaticamente
document.addEventListener('DOMContentLoaded', function() {
    const cpfInputs = document.querySelectorAll('input[data-mask="cpf"]');
    cpfInputs.forEach(input => {
        input.addEventListener('input', function() {
            maskCPF(this);
        });
    });
    
    const cnpjInputs = document.querySelectorAll('input[data-mask="cnpj"]');
    cnpjInputs.forEach(input => {
        input.addEventListener('input', function() {
            maskCNPJ(this);
        });
    });
    
    const phoneInputs = document.querySelectorAll('input[data-mask="phone"]');
    phoneInputs.forEach(input => {
        input.addEventListener('input', function() {
            maskPhone(this);
        });
    });
    
    const celularInputs = document.querySelectorAll('input[data-mask="celular"]');
    celularInputs.forEach(input => {
        input.addEventListener('input', function() {
            maskCelular(this);
        });
    });
    
    const cepInputs = document.querySelectorAll('input[data-mask="cep"]');
    cepInputs.forEach(input => {
        input.addEventListener('input', function() {
            maskCEP(this);
        });
    });
});

// Confirmação de exclusão
function confirmDelete(message) {
    return confirm(message || 'Tem certeza que deseja excluir este registro?');
}

// Toggle dark mode
function toggleDarkMode() {
    document.body.classList.toggle('dark-mode');
    const isDark = document.body.classList.contains('dark-mode');
    localStorage.setItem('darkMode', isDark);
}

// Restaura dark mode
document.addEventListener('DOMContentLoaded', function() {
    const isDark = localStorage.getItem('darkMode') === 'true';
    if (isDark) {
        document.body.classList.add('dark-mode');
    }
});

// Busca de clientes (autocomplete)
let searchTimeout;
function searchClientes(input) {
    clearTimeout(searchTimeout);
    const query = input.value.trim();
    
    if (query.length < 3) {
        document.getElementById('search-results').innerHTML = '';
        return;
    }
    
    searchTimeout = setTimeout(() => {
        fetch(`/api/clientes/search?q=${encodeURIComponent(query)}`)
            .then(response => response.json())
            .then(data => {
                displaySearchResults(data);
            })
            .catch(error => {
                console.error('Erro na busca:', error);
            });
    }, 300);
}

function displaySearchResults(results) {
    const container = document.getElementById('search-results');
    
    if (results.length === 0) {
        container.innerHTML = '<div class="search-no-results">Nenhum cliente encontrado</div>';
        return;
    }
    
    let html = '<div class="search-results-list">';
    results.forEach(cliente => {
        html += `
            <a href="/clientes/${cliente.id}" class="search-result-item">
                <div class="result-name">${cliente.nome_razao_social}</div>
                <div class="result-doc">${cliente.cpf_cnpj}</div>
            </a>
        `;
    });
    html += '</div>';
    
    container.innerHTML = html;
}

// Validação de formulários
function validateForm(formId) {
    const form = document.getElementById(formId);
    const inputs = form.querySelectorAll('[required]');
    let isValid = true;
    
    inputs.forEach(input => {
        if (!input.value.trim()) {
            input.classList.add('is-invalid');
            isValid = false;
        } else {
            input.classList.remove('is-invalid');
        }
    });
    
    return isValid;
}

// Exportar tabela para Excel
function exportToExcel(tableId, filename) {
    const table = document.getElementById(tableId);
    const html = table.outerHTML;
    const blob = new Blob([html], { type: 'application/vnd.ms-excel' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename + '.xls';
    link.click();
    URL.revokeObjectURL(url);
}

// Imprimir página
function printPage() {
    window.print();
}

// Caixa CENTRALIZADA para digitar um nome (08/10/2026): substitui o prompt()
// do navegador, que abre colado no topo da tela. Devolve uma Promise com o
// texto digitado, ou null se cancelar (Esc, clique fora ou "Cancelar").
function qPedirTexto(opt) {
    opt = opt || {};
    return new Promise(function (resolve) {
        var fundo = document.createElement('div');
        fundo.className = 'qpt-fundo';
        fundo.innerHTML =
            '<form class="qpt-caixa" role="dialog" aria-modal="true">' +
            '<h3 class="qpt-tit"></h3><label class="qpt-lbl"></label>' +
            '<input class="qpt-in" type="text" maxlength="100" autocomplete="off">' +
            '<p class="qpt-ajuda"></p>' +
            '<div class="qpt-acoes"><button type="button" class="qpt-btn qpt-nao">Cancelar</button>' +
            '<button type="submit" class="qpt-btn qpt-sim"></button></div></form>';
        fundo.querySelector('.qpt-tit').textContent = opt.titulo || 'Novo cadastro';
        fundo.querySelector('.qpt-lbl').textContent = opt.rotulo || 'Nome';
        fundo.querySelector('.qpt-sim').textContent = opt.botao || 'Salvar';
        var ajuda = fundo.querySelector('.qpt-ajuda');
        if (opt.ajuda) ajuda.textContent = opt.ajuda; else ajuda.remove();
        var inp = fundo.querySelector('.qpt-in');
        inp.value = opt.valor || '';
        if (opt.placeholder) inp.placeholder = opt.placeholder;
        function fechar(v) { document.removeEventListener('keydown', esc); fundo.remove(); resolve(v); }
        function esc(e) { if (e.key === 'Escape') fechar(null); }
        document.addEventListener('keydown', esc);
        fundo.addEventListener('mousedown', function (e) { if (e.target === fundo) fechar(null); });
        fundo.querySelector('.qpt-nao').addEventListener('click', function () { fechar(null); });
        fundo.querySelector('form').addEventListener('submit', function (e) {
            e.preventDefault();
            var v = inp.value.trim();
            if (!v) { inp.focus(); inp.classList.add('qpt-erro'); return; }
            fechar(v);
        });
        document.body.appendChild(fundo);
        inp.focus(); inp.select();
    });
}
