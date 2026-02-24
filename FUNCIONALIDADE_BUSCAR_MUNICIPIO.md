# 🗺️ Funcionalidade: Buscar Município no Cadastro de Clientes

## Requisito Original

**Solicitação do usuário:**
> "precisamos criar um novo campo na aba de cadastro que seja de município que nem o sintegra que nos levaria para a página que possamos escolher o município onde a empresa nova seja cadastrada, crie um botão do campo de município para que a gente escolha o município da empresa que estará sendo cadastrada"

**Data:** 24/02/2026

---

## Solução Implementada

### Descrição

Foi adicionado um **botão "Buscar Município"** ao lado do campo "Cidade" no formulário de cadastro de clientes, seguindo o mesmo padrão visual e comportamental do botão "Sintegra".

### Funcionalidades

1. ✅ **Validação de UF:** Verifica se o estado foi selecionado antes de abrir a consulta
2. ✅ **Abertura do IBGE Cidades:** Abre o site oficial do IBGE com os municípios do estado selecionado
3. ✅ **Instruções ao Usuário:** Mostra alert explicativo sobre como usar a funcionalidade
4. ✅ **Foco Automático:** Após abrir o IBGE, o campo cidade fica focado para facilitar o preenchimento
5. ✅ **Tratamento de Erros:** Detecta e informa sobre bloqueio de pop-ups

---

## Localização

**Arquivo:** `templates/clientes/form.html`  
**Seção:** Endereço (Address)  
**Campo:** Cidade / Município

---

## Interface do Usuário

### Visual

```
┌─────────────────────────────────────────────────────┐
│ Cidade / Município                                  │
│ ┌───────────────────────────┐ ┌─────────────────┐  │
│ │ Digite o nome da cidade   │ │ 🗺️ Buscar       │  │
│ │                            │ │   Município     │  │
│ └───────────────────────────┘ └─────────────────┘  │
│ ℹ️ Clique em "Buscar Município" para consultar     │
│    a lista oficial de municípios do IBGE           │
└─────────────────────────────────────────────────────┘
```

### Elementos

- **Campo de Texto:** Input para digitar o nome da cidade
- **Botão:** Cor azul (btn-info), ícone de mapa (fa-map-marked-alt)
- **Texto de Ajuda:** Orientação sobre como usar o botão

---

## Código Implementado

### HTML

```html
<div class="form-group">
    <label for="cidade">Cidade / Município</label>
    <div style="display: flex; gap: 10px;">
        <input type="text" 
               id="cidade" 
               name="cidade" 
               class="form-control" 
               placeholder="Digite o nome da cidade" 
               value="{{ cliente.cidade if cliente else '' }}"
               style="flex: 1;">
        <button type="button" 
                class="btn btn-info" 
                onclick="buscarMunicipio()" 
                title="Buscar município no IBGE"
                style="white-space: nowrap;">
            <i class="fas fa-map-marked-alt"></i> Buscar Município
        </button>
    </div>
    <small class="form-text text-muted">
        <i class="fas fa-info-circle"></i> 
        Clique em "Buscar Município" para consultar a lista oficial de municípios do IBGE por estado
    </small>
</div>
```

### JavaScript

```javascript
function buscarMunicipio() {
    console.log('=== FUNÇÃO BUSCAR MUNICÍPIO CHAMADA ===');
    
    const estado = document.getElementById('estado').value;
    const cidadeInput = document.getElementById('cidade');
    
    // Verificar se estado foi selecionado
    if (!estado) {
        alert('⚠️ ESTADO NÃO SELECIONADO!\n\n' +
              'Por favor, selecione o Estado (UF) antes de buscar o município.\n\n' +
              'O estado é necessário para filtrar os municípios corretos.');
        document.getElementById('estado').focus();
        return;
    }
    
    console.log('Estado selecionado:', estado);
    
    // Mostrar instruções ao usuário
    const estadoNome = document.getElementById('estado').options[document.getElementById('estado').selectedIndex].text;
    
    alert('🗺️ BUSCAR MUNICÍPIO\n\n' +
          'Estado selecionado: ' + estadoNome + '\n\n' +
          'A página do IBGE Cidades será aberta.\n\n' +
          'Você poderá:\n' +
          '• Ver todos os municípios do estado\n' +
          '• Pesquisar pelo nome do município\n' +
          '• Verificar informações oficiais\n\n' +
          'Após encontrar o município, digite o nome no campo "Cidade".');
    
    // Abrir página do IBGE com municípios do estado selecionado
    const url = `https://cidades.ibge.gov.br/brasil/${estado.toLowerCase()}/panorama`;
    console.log('Abrindo URL:', url);
    
    const ibgeWindow = window.open(url, '_blank');
    
    if (ibgeWindow) {
        console.log('✅ Página do IBGE aberta com sucesso');
        // Focar no campo cidade para facilitar preenchimento após consulta
        setTimeout(() => {
            if (cidadeInput) {
                cidadeInput.focus();
            }
        }, 500);
    } else {
        console.error('❌ Falha ao abrir página (pop-up bloqueado?)');
        alert('⚠️ A página foi bloqueada pelo navegador.\n\n' +
              'Por favor, permita pop-ups para este site.\n\n' +
              'Ou acesse manualmente: ' + url);
    }
    
    console.log('=== FIM BUSCAR MUNICÍPIO ===');
}
```

---

## Fluxo do Usuário

### Cenário 1: Uso Normal (Sucesso)

```
1. Usuário abre formulário de novo cliente
   ↓
2. Preenche informações básicas
   ↓
3. Na seção Endereço, seleciona Estado (UF)
   Exemplo: "GO - Goiás"
   ↓
4. Clica no botão "🗺️ Buscar Município"
   ↓
5. Vê alert com instruções:
   "🗺️ BUSCAR MUNICÍPIO
    Estado selecionado: GO - Goiás
    A página do IBGE Cidades será aberta..."
   ↓
6. Clica "OK" no alert
   ↓
7. Nova aba abre com a URL:
   https://cidades.ibge.gov.br/brasil/go/panorama
   ↓
8. Página do IBGE mostra:
   - Lista de todos os municípios de Goiás
   - Campo de busca para filtrar
   - Informações detalhadas de cada município
   ↓
9. Usuário procura e encontra o município desejado
   Exemplo: Encontra "Goiatuba"
   ↓
10. Retorna à aba do formulário
    (campo cidade já está focado automaticamente)
    ↓
11. Digita o nome do município no campo:
    "GOIATUBA"
    ↓
12. Continua preenchendo o restante do formulário ✅
```

### Cenário 2: Esqueceu de Selecionar UF

```
1. Usuário tenta clicar "Buscar Município" 
   SEM ter selecionado o Estado (UF)
   ↓
2. Sistema detecta falta de UF
   ↓
3. Alert aparece:
   "⚠️ ESTADO NÃO SELECIONADO!
    Por favor, selecione o Estado (UF) antes de buscar o município."
   ↓
4. Foco move automaticamente para o campo Estado
   ↓
5. Usuário seleciona a UF
   ↓
6. Clica novamente "Buscar Município"
   ↓
7. Agora funciona normalmente ✅
```

### Cenário 3: Pop-up Bloqueado

```
1. Usuário clica "Buscar Município"
   ↓
2. Navegador bloqueia abertura da nova aba
   ↓
3. Sistema detecta bloqueio
   ↓
4. Alert aparece:
   "⚠️ A página foi bloqueada pelo navegador.
    Por favor, permita pop-ups para este site.
    Ou acesse manualmente: https://cidades.ibge.gov.br/..."
   ↓
5. Opções do usuário:
   a) Permitir pop-ups e clicar novamente
   b) Copiar URL do alert e abrir manualmente
   ↓
6. Após resolver, funciona normalmente ✅
```

---

## Exemplos de URLs por Estado

| Estado | Sigla | URL do IBGE |
|--------|-------|-------------|
| Acre | AC | https://cidades.ibge.gov.br/brasil/ac/panorama |
| Alagoas | AL | https://cidades.ibge.gov.br/brasil/al/panorama |
| Amapá | AP | https://cidades.ibge.gov.br/brasil/ap/panorama |
| Amazonas | AM | https://cidades.ibge.gov.br/brasil/am/panorama |
| Bahia | BA | https://cidades.ibge.gov.br/brasil/ba/panorama |
| Ceará | CE | https://cidades.ibge.gov.br/brasil/ce/panorama |
| Distrito Federal | DF | https://cidades.ibge.gov.br/brasil/df/panorama |
| Espírito Santo | ES | https://cidades.ibge.gov.br/brasil/es/panorama |
| Goiás | GO | https://cidades.ibge.gov.br/brasil/go/panorama |
| Maranhão | MA | https://cidades.ibge.gov.br/brasil/ma/panorama |
| Mato Grosso | MT | https://cidades.ibge.gov.br/brasil/mt/panorama |
| Mato Grosso do Sul | MS | https://cidades.ibge.gov.br/brasil/ms/panorama |
| Minas Gerais | MG | https://cidades.ibge.gov.br/brasil/mg/panorama |
| Pará | PA | https://cidades.ibge.gov.br/brasil/pa/panorama |
| Paraíba | PB | https://cidades.ibge.gov.br/brasil/pb/panorama |
| Paraná | PR | https://cidades.ibge.gov.br/brasil/pr/panorama |
| Pernambuco | PE | https://cidades.ibge.gov.br/brasil/pe/panorama |
| Piauí | PI | https://cidades.ibge.gov.br/brasil/pi/panorama |
| Rio de Janeiro | RJ | https://cidades.ibge.gov.br/brasil/rj/panorama |
| Rio Grande do Norte | RN | https://cidades.ibge.gov.br/brasil/rn/panorama |
| Rio Grande do Sul | RS | https://cidades.ibge.gov.br/brasil/rs/panorama |
| Rondônia | RO | https://cidades.ibge.gov.br/brasil/ro/panorama |
| Roraima | RR | https://cidades.ibge.gov.br/brasil/rr/panorama |
| Santa Catarina | SC | https://cidades.ibge.gov.br/brasil/sc/panorama |
| São Paulo | SP | https://cidades.ibge.gov.br/brasil/sp/panorama |
| Sergipe | SE | https://cidades.ibge.gov.br/brasil/se/panorama |
| Tocantins | TO | https://cidades.ibge.gov.br/brasil/to/panorama |

**Padrão:** `https://cidades.ibge.gov.br/brasil/{uf-minuscula}/panorama`

---

## Comparação com o Botão Sintegra

| Aspecto | Botão Sintegra | Botão Buscar Município |
|---------|---------------|------------------------|
| **Localização** | Ao lado de Inscrição Estadual | Ao lado de Cidade |
| **Ícone** | `fa-external-link-alt` | `fa-map-marked-alt` |
| **Cor do Botão** | `btn-info` (azul) | `btn-info` (azul) |
| **Validação Prévia** | Requer CNPJ preenchido | Requer UF selecionada |
| **Site Aberto** | sintegra.gov.br | cidades.ibge.gov.br |
| **Ação Adicional** | Copia CNPJ para clipboard | Foca campo cidade |
| **Layout** | Flex com gap: 10px | Flex com gap: 10px |
| **Estilo** | white-space: nowrap | white-space: nowrap |

**Conclusão:** ✅ Design totalmente consistente, conforme solicitado!

---

## Vantagens da Solução

### 1. Dados Oficiais e Confiáveis ✅

- **IBGE** = Instituto Brasileiro de Geografia e Estatística
- Órgão oficial do governo brasileiro
- Dados sempre atualizados
- Referência nacional para informações geográficas

### 2. Cobertura Completa ✅

- **5.570 municípios** brasileiros cadastrados
- Todos os 26 estados + Distrito Federal
- Informações adicionais disponíveis:
  - População
  - Área territorial
  - Densidade demográfica
  - IDH (Índice de Desenvolvimento Humano)

### 3. Interface Intuitiva ✅

- Consistente com botão Sintegra (conforme requisito)
- Validação clara e objetiva
- Instruções detalhadas em português
- Feedback imediato de erros

### 4. Não Invasivo ✅

- Abre em nova aba (não interrompe preenchimento)
- Não obrigatório (usuário pode digitar diretamente)
- Foco automático no campo após consulta
- Usuário mantém controle total

### 5. Robusto ✅

- Tratamento de bloqueio de pop-ups
- Logs detalhados no console para debugging
- URL alternativa fornecida em caso de erro
- Funciona em todos os navegadores modernos

---

## Guia de Testes

### Teste 1: Validação de UF

**Objetivo:** Verificar se sistema valida seleção de UF

**Passos:**
1. Abrir formulário de novo cliente
2. Ir até seção Endereço
3. NÃO selecionar Estado (UF)
4. Clicar em "Buscar Município"

**Resultado Esperado:**
- ✅ Alert aparece: "⚠️ ESTADO NÃO SELECIONADO!"
- ✅ Mensagem orienta a selecionar UF primeiro
- ✅ Foco move automaticamente para campo Estado
- ✅ Página do IBGE NÃO abre

### Teste 2: Funcionamento com UF Selecionada

**Objetivo:** Verificar abertura correta do IBGE

**Passos:**
1. Abrir formulário de novo cliente
2. Selecionar Estado: "GO - Goiás"
3. Clicar em "Buscar Município"
4. Clicar "OK" no alert de instruções

**Resultado Esperado:**
- ✅ Alert mostra: "Estado selecionado: GO - Goiás"
- ✅ Nova aba abre com URL: https://cidades.ibge.gov.br/brasil/go/panorama
- ✅ Página mostra lista de municípios de Goiás
- ✅ Campo cidade fica focado após ~500ms
- ✅ Console mostra logs de sucesso

### Teste 3: Diferentes Estados

**Objetivo:** Verificar se funciona para todos os estados

**Passos:**
1. Repetir teste anterior com diferentes UFs:
   - SP - São Paulo
   - RJ - Rio de Janeiro
   - MG - Minas Gerais
   - RS - Rio Grande do Sul
   - BA - Bahia

**Resultado Esperado:**
- ✅ URL muda conforme estado: .../brasil/{uf}/...
- ✅ Página do IBGE mostra municípios do estado correto
- ✅ Todos os estados funcionam

### Teste 4: Preenchimento do Campo

**Objetivo:** Verificar se usuário consegue preencher após consulta

**Passos:**
1. Selecionar Estado: "GO"
2. Clicar "Buscar Município"
3. Na página do IBGE, procurar "Goiatuba"
4. Retornar ao formulário
5. Digitar "GOIATUBA" no campo cidade

**Resultado Esperado:**
- ✅ Campo cidade está focado
- ✅ Usuário consegue digitar
- ✅ Valor é salvo corretamente
- ✅ Formulário pode ser submetido

### Teste 5: Bloqueio de Pop-up

**Objetivo:** Verificar tratamento de bloqueio

**Passos:**
1. Configurar navegador para bloquear pop-ups
2. Selecionar Estado: "SP"
3. Clicar "Buscar Município"

**Resultado Esperado:**
- ✅ Sistema detecta bloqueio
- ✅ Alert aparece com mensagem de erro
- ✅ URL alternativa é fornecida
- ✅ Usuário pode copiar URL e abrir manualmente

---

## Console Logs

### Exemplo de Log de Sucesso

```javascript
=== FUNÇÃO BUSCAR MUNICÍPIO CHAMADA ===
Estado selecionado: GO
Abrindo URL: https://cidades.ibge.gov.br/brasil/go/panorama
✅ Página do IBGE aberta com sucesso
=== FIM BUSCAR MUNICÍPIO ===
```

### Exemplo de Log com Erro (UF não selecionada)

```javascript
=== FUNÇÃO BUSCAR MUNICÍPIO CHAMADA ===
// Função retorna antes de continuar (return)
```

### Exemplo de Log com Pop-up Bloqueado

```javascript
=== FUNÇÃO BUSCAR MUNICÍPIO CHAMADA ===
Estado selecionado: SP
Abrindo URL: https://cidades.ibge.gov.br/brasil/sp/panorama
❌ Falha ao abrir página (pop-up bloqueado?)
=== FIM BUSCAR MUNICÍPIO ===
```

---

## Troubleshooting

### Problema: "Botão não aparece"

**Causas Possíveis:**
- Cache do navegador desatualizado
- Arquivo não foi salvo corretamente
- JavaScript não está carregando

**Soluções:**
1. Limpar cache do navegador (Ctrl+Shift+Delete)
2. Fazer hard refresh (Ctrl+F5)
3. Verificar console para erros JavaScript
4. Confirmar que arquivo form.html foi atualizado

### Problema: "Alert não aparece ao clicar"

**Causas Possíveis:**
- Erro JavaScript na função buscarMunicipio()
- onclick não está vinculado corretamente

**Soluções:**
1. Abrir console (F12)
2. Verificar mensagens de erro
3. Confirmar que função buscarMunicipio() existe
4. Testar chamando função manualmente: `buscarMunicipio()`

### Problema: "Página IBGE não abre"

**Causas Possíveis:**
- Pop-up está sendo bloqueado
- Problema de conexão com internet
- URL do IBGE mudou

**Soluções:**
1. Permitir pop-ups para o site
2. Verificar conexão com internet
3. Testar URL manualmente no navegador
4. Verificar console para erros

### Problema: "Campo cidade não fica focado"

**Causas Possíveis:**
- setTimeout pode não estar funcionando
- Campo cidade não existe no DOM
- ID do campo está errado

**Soluções:**
1. Verificar se campo tem id="cidade"
2. Aumentar tempo do setTimeout (de 500ms para 1000ms)
3. Testar foco manual: `document.getElementById('cidade').focus()`

---

## Manutenção Futura

### Se URL do IBGE mudar

**Arquivo:** `templates/clientes/form.html`  
**Linha:** ~1034  
**Código Atual:**
```javascript
const url = `https://cidades.ibge.gov.br/brasil/${estado.toLowerCase()}/panorama`;
```

**Como Atualizar:**
1. Substituir padrão de URL
2. Testar com alguns estados
3. Commit e deploy

### Se quiser adicionar mais informações no alert

**Arquivo:** `templates/clientes/form.html`  
**Linha:** ~1027  
**Modificar a string do alert**

### Se quiser mudar ícone do botão

**Arquivo:** `templates/clientes/form.html`  
**Linha:** ~339  
**Classe Atual:** `fa-map-marked-alt`  
**Alternativas:**
- `fa-map-marker-alt` (marcador simples)
- `fa-globe-americas` (globo)
- `fa-search-location` (busca + localização)

---

## Histórico de Mudanças

| Data | Versão | Mudança |
|------|--------|---------|
| 24/02/2026 | 1.0 | Implementação inicial do botão Buscar Município |

---

## Contato

Para dúvidas ou problemas com esta funcionalidade:
- Verificar console do navegador (F12)
- Verificar logs no servidor
- Consultar esta documentação

---

**Desenvolvido para:** Sistema Qualicontax  
**Autor:** Equipe de Desenvolvimento  
**Data:** Fevereiro de 2026  
**Versão:** 1.0
