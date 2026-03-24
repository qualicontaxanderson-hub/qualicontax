// Qualicontax - Scripts principais

document.addEventListener('DOMContentLoaded', function() {

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

    // ── Auto-hide alerts after 5 seconds ──
    document.querySelectorAll('.alert').forEach(function(alert) {
        const closeBtn = alert.querySelector('.alert-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', function() {
                alert.style.opacity = '0';
                setTimeout(() => alert.remove(), 300);
            });
        }
        setTimeout(function() {
            alert.style.opacity = '0';
            setTimeout(() => alert.remove(), 300);
        }, 5000);
    });

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
