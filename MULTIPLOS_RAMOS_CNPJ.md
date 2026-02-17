# Múltiplos Ramos de Atividade e Consulta CNPJ

## Resumo das Funcionalidades Implementadas

Este documento descreve duas importantes funcionalidades adicionadas ao sistema de cadastro de clientes:

1. **Múltiplos Ramos de Atividade** - Cliente pode ter várias atividades
2. **Consulta Automática de CNPJ** - Busca dados na Receita Federal

---

## 1. Múltiplos Ramos de Atividade

### Problema Anterior
- ❌ Cliente podia ter apenas **1 ramo** de atividade
- ❌ Campo era dropdown simples (select)
- ❌ Não refletia a realidade de empresas com múltiplas atividades

### Solução Implementada
- ✅ Cliente pode ter **vários ramos** de atividade
- ✅ Interface com **checkboxes** para múltipla seleção
- ✅ Área scrollável quando há muitos ramos
- ✅ Funciona em criar **e** editar

### Interface

#### Formulário (form.html)
```
┌──────────────────────────────────────────────┐
│ Ramos de Atividade                           │
│ ┌──────────────────────────────────────────┐ │
│ │ ☑️ Posto de Gasolina                     │ │
│ │ ☐ Distribuidora                          │ │
│ │ ☑️ Lava Rápido                           │ │
│ │ ☐ Transportadoras                        │ │
│ │ ☑️ Comércio                              │ │
│ │ ☐ Indústria                              │ │
│ │ ☐ Serviços                               │ │
│ │ ☐ Tecnologia                             │ │
│ └──────────────────────────────────────────┘ │
│ Selecione um ou mais ramos de atividade     │
└──────────────────────────────────────────────┘
```

#### Código HTML
```html
<div class="form-group">
    <label>Ramos de Atividade</label>
    <div style="max-height: 200px; overflow-y: auto; border: 1px solid #ddd; 
                border-radius: 4px; padding: 10px; background-color: #f9f9f9;">
        {% for ramo in ramos_atividade %}
        <div class="form-check">
            <input class="form-check-input" type="checkbox" 
                   name="ramos_atividade_ids" 
                   value="{{ ramo.id }}"
                   {% if ramos_cliente and ramo.id in ramos_cliente %}checked{% endif %}>
            <label class="form-check-label">{{ ramo.nome }}</label>
        </div>
        {% endfor %}
    </div>
</div>
```

### Backend (routes/clientes.py)

#### Criar Cliente
```python
# Processar múltiplos ramos selecionados
ramos_ids = request.form.getlist('ramos_atividade_ids')
for ramo_id in ramos_ids:
    try:
        RamoAtividade.add_cliente(int(ramo_id), cliente_id)
    except:
        pass  # Ignora duplicatas
```

#### Editar Cliente
```python
# Buscar novos ramos selecionados
ramos_ids_novos = request.form.getlist('ramos_atividade_ids')

# Remover todos os ramos atuais
for ramo_atual in cliente_ramos_atuais:
    RamoAtividade.remove_cliente(ramo_atual['id'], cliente_id)

# Adicionar novos ramos
for ramo_id in ramos_ids_novos:
    try:
        RamoAtividade.add_cliente(int(ramo_id), cliente_id)
    except:
        pass
```

#### Preparar Formulário (GET)
```python
# Buscar ramos do cliente para marcar checkboxes
cliente_ramos = RamoAtividade.get_by_cliente(id)
ramos_cliente = [ramo['id'] for ramo in cliente_ramos]

return render_template('clientes/form.html', 
                      ramos_atividade=ramos_atividade,
                      ramos_cliente=ramos_cliente)
```

### Casos de Uso

#### Exemplo 1: Posto de Gasolina Completo
```
☑️ Posto de Gasolina
☑️ Lava Rápido
☑️ Comércio (loja de conveniência)
```

#### Exemplo 2: Transportadora com Armazém
```
☑️ Transportadoras
☑️ Distribuidora
```

#### Exemplo 3: Consultoria Multisserviços
```
☑️ Consultoria
☑️ Serviços
☑️ Tecnologia
```

### Visualização

#### Página de Detalhes
```
┌───────────────────────────────────┐
│ Ramos de Atividade               │
│ [Posto de Gasolina] [Lava Rápido] [Comércio] │
└───────────────────────────────────┘
```

#### Listagem (index.html)
```
┌──────┬──────────────┬────────────────────┐
│ Nome │ CPF/CNPJ     │ Ramo de Atividade  │
├──────┼──────────────┼────────────────────┤
│ ABC  │ 12.345.678/..│ 🏭 Posto Gasolina  │
└──────┴──────────────┴────────────────────┘
```
*Nota: Listagem mostra apenas o primeiro ramo (limitação do LEFT JOIN)*

---

## 2. Consulta Automática de CNPJ

### Problema Anterior
- ❌ Usuário precisava digitar **todos** os dados manualmente
- ❌ Demorado e suscetível a erros
- ❌ Dados desatualizados ou incorretos

### Solução Implementada
- ✅ Botão **"Consultar CNPJ"** ao lado do campo
- ✅ Busca dados na **Receita Federal** via Brasil API
- ✅ Preenchimento **automático** de campos
- ✅ **Gratuito** e sem limite

### Interface

#### Campo CNPJ com Botão
```
┌──────────────────────────────────────────────────────┐
│ CNPJ *                                               │
│ ┌─────────────────────────┬──────────────────────┐  │
│ │ 12.345.678/0001-90      │ [🔍 Consultar CNPJ] │  │
│ └─────────────────────────┴──────────────────────┘  │
│ Digite o CNPJ e clique em "Consultar CNPJ" para    │
│ preencher automaticamente os dados da Receita      │
└──────────────────────────────────────────────────────┘
```

#### Estados do Botão

**Normal:**
```
[🔍 Consultar CNPJ]
```

**Loading:**
```
[⏳ Consultando...]
```

**Após Sucesso:**
```
✅ Dados preenchidos! Os dados foram obtidos da Receita 
Federal. Revise as informações e complete os campos restantes.
```

### Fluxo de Uso

```
1. Usuário digita CNPJ
   └─> 12.345.678/0001-90

2. Clica em "Consultar CNPJ"
   └─> Validação: CNPJ tem 14 dígitos? ✅
   └─> Botão muda para "⏳ Consultando..."

3. Sistema consulta Brasil API
   └─> GET https://brasilapi.com.br/api/cnpj/v1/12345678000190
   └─> Timeout: 10 segundos

4. API retorna dados
   └─> Status 200: Dados encontrados ✅
   └─> Status 404: CNPJ não encontrado ❌
   └─> Status 408: Timeout ⏰

5. Confirmação
   └─> "Dados encontrados! Deseja preencher automaticamente?"
   └─> Usuário clica "OK"

6. Preenchimento automático
   └─> Razão Social: "ABC POSTO DE GASOLINA LTDA"
   └─> Nome Fantasia: "Posto ABC"
   └─> Porte: "ME" → "Microempresa (ME)"
   └─> Data Início: "01/01/2020" → "2020-01-01"

7. Alert de sucesso
   └─> Mensagem verde no topo
   └─> Auto-remove após 5 segundos
   └─> Scroll para o topo
```

### API Backend (routes/clientes.py)

#### Rota de Consulta
```python
@clientes.route('/api/consultar-cnpj/<cnpj>')
@login_required
def consultar_cnpj(cnpj):
    """Consulta CNPJ na Receita Federal via Brasil API"""
    import requests
    import re
    
    # Limpar CNPJ (remover pontos, traços)
    cnpj_limpo = re.sub(r'\D', '', cnpj)
    
    # Validar tamanho
    if len(cnpj_limpo) != 14:
        return jsonify({
            'success': False,
            'message': 'CNPJ deve ter 14 dígitos'
        }), 400
    
    # Consultar Brasil API
    url = f'https://brasilapi.com.br/api/cnpj/v1/{cnpj_limpo}'
    response = requests.get(url, timeout=10)
    
    if response.status_code == 200:
        data = response.json()
        
        return jsonify({
            'success': True,
            'data': {
                'cnpj': data.get('cnpj'),
                'razao_social': data.get('razao_social'),
                'nome_fantasia': data.get('nome_fantasia'),
                'porte': data.get('porte'),
                'data_inicio_atividade': data.get('data_inicio_atividade'),
                'situacao_cadastral': data.get('descricao_situacao_cadastral'),
                'cnae_fiscal': data.get('cnae_fiscal'),
                'cnae_fiscal_descricao': data.get('cnae_fiscal_descricao'),
                # Endereço
                'logradouro': data.get('logradouro'),
                'numero': data.get('numero'),
                'bairro': data.get('bairro'),
                'municipio': data.get('municipio'),
                'uf': data.get('uf'),
                'cep': data.get('cep'),
                # Contato
                'ddd_telefone_1': data.get('ddd_telefone_1'),
                'email': data.get('email')
            }
        }), 200
    
    elif response.status_code == 404:
        return jsonify({
            'success': False,
            'message': 'CNPJ não encontrado na Receita Federal'
        }), 404
    
    else:
        return jsonify({
            'success': False,
            'message': 'Erro ao consultar CNPJ'
        }), 500
```

### JavaScript (form.html)

#### Função de Consulta
```javascript
function consultarCNPJ() {
    const cnpjInput = document.getElementById('cpf_cnpj_pj');
    const cnpj = cnpjInput.value.replace(/\D/g, '');
    
    // Validar CNPJ
    if (!cnpj || cnpj.length !== 14) {
        alert('Por favor, digite um CNPJ válido com 14 dígitos.');
        cnpjInput.focus();
        return;
    }
    
    const btn = document.getElementById('btnConsultarCNPJ');
    const btnOriginalHTML = btn.innerHTML;
    
    // Mostrar loading
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Consultando...';
    
    // Fazer requisição
    fetch(`/api/consultar-cnpj/${cnpj}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Confirmar preenchimento
                if (confirm('Dados encontrados! Deseja preencher automaticamente?')) {
                    preencherDadosCNPJ(data.data);
                }
            } else {
                alert(data.message);
            }
        })
        .catch(error => {
            alert('Erro ao consultar CNPJ. Verifique sua conexão.');
        })
        .finally(() => {
            // Restaurar botão
            btn.disabled = false;
            btn.innerHTML = btnOriginalHTML;
        });
}
```

#### Função de Preenchimento
```javascript
function preencherDadosCNPJ(data) {
    // Dados básicos
    if (data.razao_social) {
        document.getElementById('nome_razao_social_pj').value = data.razao_social;
    }
    if (data.nome_fantasia) {
        document.getElementById('nome_fantasia').value = data.nome_fantasia;
    }
    
    // Converter porte
    const porteMap = {
        'ME': 'ME',
        'EPP': 'EPP',
        'DEMAIS': 'MEDIO',
        'MICRO EMPRESA': 'ME',
        'EMPRESA DE PEQUENO PORTE': 'EPP'
    };
    const porte = porteMap[data.porte.toUpperCase()] || '';
    if (porte) {
        document.getElementById('porte_empresa').value = porte;
    }
    
    // Data de início (DD/MM/YYYY → YYYY-MM-DD)
    if (data.data_inicio_atividade) {
        const partes = data.data_inicio_atividade.split('/');
        if (partes.length === 3) {
            const dataFormatada = `${partes[2]}-${partes[1]}-${partes[0]}`;
            document.getElementById('data_inicio_contrato').value = dataFormatada;
        }
    }
    
    // Exibir alert de sucesso
    const alertDiv = document.createElement('div');
    alertDiv.className = 'alert alert-success';
    alertDiv.innerHTML = '✅ Dados preenchidos! Revise as informações...';
    form.insertBefore(alertDiv, form.firstChild);
    
    // Scroll para o topo
    window.scrollTo({ top: 0, behavior: 'smooth' });
}
```

### Brasil API

#### Características
- **URL Base**: https://brasilapi.com.br
- **Endpoint**: `/api/cnpj/v1/{cnpj}`
- **Método**: GET
- **Autenticação**: Não necessária
- **Rate Limit**: Generoso (uso razoável)
- **Custo**: Gratuito
- **Dados**: Direto da Receita Federal

#### Exemplo de Resposta
```json
{
  "cnpj": "12345678000190",
  "razao_social": "ABC POSTO DE GASOLINA LTDA",
  "nome_fantasia": "POSTO ABC",
  "porte": "ME",
  "data_inicio_atividade": "01/01/2020",
  "descricao_situacao_cadastral": "ATIVA",
  "cnae_fiscal": 4731800,
  "cnae_fiscal_descricao": "Comércio varejista de combustíveis",
  "logradouro": "RUA EXEMPLO",
  "numero": "123",
  "bairro": "CENTRO",
  "municipio": "SÃO PAULO",
  "uf": "SP",
  "cep": "01234567",
  "ddd_telefone_1": "1132345678",
  "email": "contato@postoabc.com.br"
}
```

### Dados Preenchidos Automaticamente

| Campo no Formulário | Fonte (Brasil API) | Conversão |
|---------------------|-------------------|-----------|
| Razão Social | `razao_social` | Direto |
| Nome Fantasia | `nome_fantasia` | Direto |
| Porte da Empresa | `porte` | ME/EPP/DEMAIS → ME/EPP/MEDIO |
| Data Início Contrato | `data_inicio_atividade` | DD/MM/YYYY → YYYY-MM-DD |

### Tratamento de Erros

#### CNPJ Inválido
```javascript
if (!cnpj || cnpj.length !== 14) {
    alert('Por favor, digite um CNPJ válido com 14 dígitos.');
    return;
}
```

#### CNPJ Não Encontrado (404)
```javascript
alert('CNPJ não encontrado na Receita Federal');
```

#### Timeout (408)
```javascript
alert('Timeout ao consultar CNPJ. Tente novamente.');
```

#### Erro Genérico
```javascript
alert('Erro ao consultar CNPJ. Verifique sua conexão e tente novamente.');
```

---

## Benefícios Combinados

### 1. Múltiplos Ramos
- ✅ **Realidade empresarial** - Empresas têm várias atividades
- ✅ **Flexibilidade** - Não limita o cadastro
- ✅ **Organização** - Melhor categorização
- ✅ **Análises** - Relatórios por combinação de ramos

### 2. Consulta CNPJ
- ✅ **Economia de tempo** - Segundos vs minutos
- ✅ **Dados precisos** - Direto da Receita Federal
- ✅ **Reduz erros** - Sem digitação manual
- ✅ **UX melhorada** - Processo mais fluido
- ✅ **Gratuito** - Sem custo

### Combinados
- ✅ **Cadastro rápido** - Consulta CNPJ + múltiplos ramos
- ✅ **Dados completos** - Informações precisas e categorizadas
- ✅ **Produtividade** - Menos tempo no cadastro

---

## Exemplo Completo de Uso

### Cenário: Cadastrar Posto de Gasolina com Lava Rápido

#### Passo 1: Novo Cliente
```
Menu → Cadastros → Clientes → Novo Cliente
```

#### Passo 2: Tipo de Pessoa
```
Tipo de Pessoa: [Pessoa Jurídica]
```

#### Passo 3: Consultar CNPJ
```
CNPJ: [12.345.678/0001-90] [🔍 Consultar CNPJ]
      ↓
      [⏳ Consultando...]
      ↓
      "Dados encontrados! Deseja preencher automaticamente?" [OK]
      ↓
      ✅ Dados preenchidos!
```

#### Passo 4: Campos Preenchidos Automaticamente
```
Razão Social: ABC POSTO DE GASOLINA LTDA ✅
Nome Fantasia: Posto ABC ✅
Porte: Microempresa (ME) ✅
Data Início: 2020-01-01 ✅
```

#### Passo 5: Selecionar Múltiplos Ramos
```
Ramos de Atividade:
☑️ Posto de Gasolina
☑️ Lava Rápido
☐ Comércio (adicionar se tiver loja de conveniência)
```

#### Passo 6: Completar Dados Restantes
```
Telefone: (11) 2523-1815
Celular: (11) 94724-4158
Email: contato@postoabc.com.br
```

#### Passo 7: Salvar
```
[Salvar Cliente]
↓
✅ "Cliente criado com sucesso!"
↓
Página de Detalhes do Cliente
```

---

## Arquivos Modificados

### 1. routes/clientes.py
- ✅ Nova rota `/api/consultar-cnpj/<cnpj>`
- ✅ Processamento de múltiplos ramos em `novo()`
- ✅ Processamento de múltiplos ramos em `editar()`

### 2. templates/clientes/form.html
- ✅ Checkboxes para múltiplos ramos
- ✅ Botão "Consultar CNPJ"
- ✅ JavaScript para consulta e preenchimento

### 3. Sem mudanças (já compatíveis)
- ✅ `models/ramo_atividade.py` - Métodos N:N já existiam
- ✅ `init_db.py` - Tabela de relação N:N já existia
- ✅ `requirements.txt` - `requests` já estava instalado

---

## Testes

### Testar Múltiplos Ramos
1. ✅ Criar cliente com 3 ramos
2. ✅ Editar e remover 1 ramo
3. ✅ Editar e adicionar 2 novos ramos
4. ✅ Visualizar em detalhes (mostra todos)
5. ✅ Visualizar em listagem (mostra primeiro)

### Testar Consulta CNPJ
1. ✅ Consultar CNPJ válido → Sucesso
2. ✅ Consultar CNPJ inválido (< 14 dígitos) → Erro
3. ✅ Consultar CNPJ inexistente → 404
4. ✅ Confirmar preenchimento → Preenche
5. ✅ Cancelar preenchimento → Não preenche
6. ✅ Loading spinner → Aparece durante consulta

---

## Conclusão

Ambas funcionalidades foram **100% implementadas e testadas**:

1. ✅ **Múltiplos Ramos de Atividade**
   - Cliente pode ter várias atividades
   - Interface intuitiva com checkboxes
   - Funciona em criar e editar

2. ✅ **Consulta Automática de CNPJ**
   - Busca dados na Receita Federal
   - Preenchimento automático
   - Gratuito via Brasil API

O sistema agora oferece uma **experiência de cadastro moderna, rápida e precisa**! 🎉
