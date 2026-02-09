// Qualicontax - Configurações de gráficos com Chart.js

// Configuração padrão dos gráficos
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif';
Chart.defaults.font.size = 13;
Chart.defaults.color = '#374151';

// Cores Qualicontax
const colors = {
    green: '#22C55E',
    darkGreen: '#16A34A',
    orange: '#FF6B35',
    blue: '#3B82F6',
    purple: '#8B5CF6',
    yellow: '#F59E0B',
    red: '#EF4444',
    gray: '#6B7280'
};

// Gráfico de Fluxo de Caixa
function createFluxoCaixaChart(canvasId, data) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    
    return new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.labels,
            datasets: [
                {
                    label: 'Entradas',
                    data: data.entradas,
                    backgroundColor: colors.green,
                    borderRadius: 6
                },
                {
                    label: 'Saídas',
                    data: data.saidas,
                    backgroundColor: colors.red,
                    borderRadius: 6
                },
                {
                    label: 'Saldo',
                    data: data.saldo,
                    backgroundColor: colors.blue,
                    borderRadius: 6
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        padding: 20,
                        usePointStyle: true
                    }
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            let label = context.dataset.label || '';
                            if (label) {
                                label += ': ';
                            }
                            label += 'R$ ' + context.parsed.y.toLocaleString('pt-BR', {
                                minimumFractionDigits: 2,
                                maximumFractionDigits: 2
                            });
                            return label;
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        callback: function(value) {
                            return 'R$ ' + value.toLocaleString('pt-BR');
                        }
                    }
                }
            }
        }
    });
}

// Gráfico de Pizza - Encerramentos
function createEncerramentosChart(canvasId, data) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    
    return new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: data.labels,
            datasets: [{
                data: data.values,
                backgroundColor: [
                    colors.green,
                    colors.orange,
                    colors.blue,
                    colors.purple,
                    colors.yellow
                ],
                borderWidth: 2,
                borderColor: '#fff'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'right',
                    labels: {
                        padding: 15,
                        usePointStyle: true
                    }
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const label = context.label || '';
                            const value = context.parsed;
                            const total = context.dataset.data.reduce((a, b) => a + b, 0);
                            const percentage = ((value / total) * 100).toFixed(1);
                            return `${label}: ${value} (${percentage}%)`;
                        }
                    }
                }
            }
        }
    });
}

// Gráfico de Barras - Clientes Potenciais por Usuário
function createClientesPorUsuarioChart(canvasId, data) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    
    return new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.labels,
            datasets: [{
                label: 'Clientes Potenciais',
                data: data.values,
                backgroundColor: colors.orange,
                borderRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                legend: {
                    display: false
                }
            },
            scales: {
                x: {
                    beginAtZero: true
                }
            }
        }
    });
}

// Gráfico de Barras - Novos Contratos x Encerrados
function createContratosChart(canvasId, data) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    
    return new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.labels,
            datasets: [
                {
                    label: 'Novos',
                    data: data.novos,
                    backgroundColor: colors.green,
                    borderRadius: 6
                },
                {
                    label: 'Encerrados',
                    data: data.encerrados,
                    backgroundColor: colors.red,
                    borderRadius: 6
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                }
            },
            scales: {
                y: {
                    beginAtZero: true
                }
            }
        }
    });
}

// Gráfico de Pizza - Vendas por Categoria
function createVendasPorCategoriaChart(canvasId, data) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    
    return new Chart(ctx, {
        type: 'pie',
        data: {
            labels: data.labels,
            datasets: [{
                data: data.values,
                backgroundColor: [
                    colors.green,
                    colors.blue,
                    colors.orange,
                    colors.purple,
                    colors.yellow,
                    colors.red
                ],
                borderWidth: 2,
                borderColor: '#fff'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        padding: 10,
                        usePointStyle: true,
                        font: {
                            size: 11
                        }
                    }
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const label = context.label || '';
                            const value = context.parsed;
                            const total = context.dataset.data.reduce((a, b) => a + b, 0);
                            const percentage = ((value / total) * 100).toFixed(1);
                            return `${label}: R$ ${value.toLocaleString('pt-BR')} (${percentage}%)`;
                        }
                    }
                }
            }
        }
    });
}

// Gráfico de Engajamento (circular)
function createEngajamentoChart(canvasId, percentage) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    
    return new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Engajamento', 'Restante'],
            datasets: [{
                data: [percentage, 100 - percentage],
                backgroundColor: [colors.green, '#E5E7EB'],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '75%',
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    enabled: false
                }
            }
        },
        plugins: [{
            id: 'centerText',
            beforeDraw: function(chart) {
                const width = chart.width;
                const height = chart.height;
                const ctx = chart.ctx;
                ctx.restore();
                
                const fontSize = (height / 114).toFixed(2);
                ctx.font = `bold ${fontSize}em sans-serif`;
                ctx.textBaseline = 'middle';
                ctx.fillStyle = colors.green;
                
                const text = percentage + '%';
                const textX = Math.round((width - ctx.measureText(text).width) / 2);
                const textY = height / 2;
                
                ctx.fillText(text, textX, textY);
                ctx.save();
            }
        }]
    });
}

// Carregar dados do dashboard via AJAX
function loadDashboardData() {
    fetch('/api/dashboard/charts')
        .then(response => response.json())
        .then(data => {
            // Criar gráficos com os dados recebidos
            if (data.fluxo_caixa) {
                createFluxoCaixaChart('fluxoCaixaChart', data.fluxo_caixa);
            }
            if (data.encerramentos) {
                createEncerramentosChart('encerramentosChart', data.encerramentos);
            }
            if (data.clientes_por_usuario) {
                createClientesPorUsuarioChart('clientesUsuarioChart', data.clientes_por_usuario);
            }
            if (data.contratos) {
                createContratosChart('contratosChart', data.contratos);
            }
            if (data.vendas_dezembro) {
                createVendasPorCategoriaChart('vendasDezChart', data.vendas_dezembro);
            }
            if (data.vendas_janeiro) {
                createVendasPorCategoriaChart('vendasJanChart', data.vendas_janeiro);
            }
            if (data.vendas_fevereiro) {
                createVendasPorCategoriaChart('vendasFevChart', data.vendas_fevereiro);
            }
            if (data.engajamento) {
                createEngajamentoChart('engajamentoChart', data.engajamento);
            }
        })
        .catch(error => {
            console.error('Erro ao carregar dados do dashboard:', error);
        });
}

// Inicializar gráficos quando a página carregar
document.addEventListener('DOMContentLoaded', function() {
    if (document.getElementById('fluxoCaixaChart')) {
        loadDashboardData();
    }
});
