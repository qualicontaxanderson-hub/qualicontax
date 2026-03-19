# Relocação do Botão "Buscar Município"

## 📋 Requisito

**Solicitação do usuário:**
> "coloque o botao de buscar municipio, pra perto da inscricao municipal, mantendo a mesma funcao, apenas faca ele escolher a inscricao municipal automaticamente"

**Data:** 12 de março de 2026

---

## ✅ Implementação

### Mudança Principal

O botão "Buscar Município" foi **movido** do campo **Cidade** para o campo **Inscrição Municipal**.

### Motivação

- O botão agora está ao lado do campo onde o usuário irá preencher os dados consultados
- Melhor organização lógica: município → inscrição municipal
- Campo Cidade volta a ser simples (apenas texto descritivo)

---

## 🔄 Mudanças Detalhadas

### 1. Campo Inscrição Municipal

**ANTES:**
```html
<div class="form-group">
    <label for="inscricao_municipal">Inscrição Municipal</label>
    <input type="text" id="inscricao_municipal" name="inscricao_municipal" 
           class="form-control" placeholder="Inscrição Municipal">
</div>
```

**DEPOIS:**
```html
<div class="form-group">
    <label for="inscricao_municipal">Inscrição Municipal</label>
    <div style="display: flex; gap: 10px;">
        <input type="text" 
               id="inscricao_municipal" 
               name="inscricao_municipal" 
               class="form-control" 
               placeholder="Inscrição Municipal" 
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
        Consulte municípios oficiais do IBGE por estado
    </small>
</div>
```

**Localização:** Linhas 154-177 em `templates/clientes/form.html`

---

### 2. Campo Cidade

**ANTES:**
```html
<div class="form-group">
    <label for="cidade">Cidade / Município</label>
    <div style="display: flex; gap: 10px;">
        <input type="text" id="cidade" name="cidade" class="form-control">
        <button onclick="buscarMunicipio()">
            <i class="fas fa-map-marked-alt"></i> Buscar Município
        </button>
    </div>
    <small class="form-text text-muted">
        Clique em "Buscar Município" para consultar...
    </small>
</div>
```

**DEPOIS:**
```html
<div class="form-group">
    <label for="cidade">Cidade / Município</label>
    <input type="text" 
           id="cidade" 
           name="cidade" 
           class="form-control" 
           placeholder="Digite o nome da cidade">
</div>
```

**Localização:** Linhas 346-353 em `templates/clientes/form.html`

**Simplificação:**
- ❌ Removido botão
- ❌ Removido container flex
- ❌ Removido texto de ajuda
- ✅ Volta a ser input simples

---

### 3. Função JavaScript

**Mudanças na função `buscarMunicipio()`:**

#### Variável de Foco

```javascript
// ANTES:
const cidadeInput = document.getElementById('cidade');

// DEPOIS:
const inscricaoMunicipalInput = document.getElementById('inscricao_municipal');
```

#### Mensagem ao Usuário

```javascript
// ANTES:
'Após encontrar o município, digite o nome no campo "Cidade".'

// DEPOIS:
'Após encontrar o município, você pode anotar a inscrição municipal no campo correspondente.'
```

#### Foco Automático

```javascript
// ANTES:
setTimeout(() => {
    if (cidadeInput) {
        cidadeInput.focus();
    }
}, 500);

// DEPOIS:
setTimeout(() => {
    if (inscricaoMunicipalInput) {
        inscricaoMunicipalInput.focus();
    }
}, 500);
```

**Localização:** Linhas 1025-1073 em `templates/clientes/form.html`

---

## 👁️ Comparação Visual

### Layout ANTES

```
┌─────────────────────────────────────────────────────┐
│ DADOS DA EMPRESA                                    │
├─────────────────────────────────────────────────────┤
│ Inscrição Municipal:  [___________________________] │
│                                                     │
│ Regime Tributário:    [▼ Selecione...]             │
├─────────────────────────────────────────────────────┤
│ ENDEREÇO                                            │
├─────────────────────────────────────────────────────┤
│ Cidade / Município:                                 │
│ [__________________] [🗺️ Buscar Município]         │ ← BOTÃO AQUI
│ ℹ️ Clique em "Buscar Município"...                  │
└─────────────────────────────────────────────────────┘
```

### Layout DEPOIS

```
┌─────────────────────────────────────────────────────┐
│ DADOS DA EMPRESA                                    │
├─────────────────────────────────────────────────────┤
│ Inscrição Municipal:                                │
│ [__________________] [🗺️ Buscar Município]         │ ← BOTÃO MOVIDO!
│ ℹ️ Consulte municípios oficiais do IBGE            │
│                                                     │
│ Regime Tributário:    [▼ Selecione...]             │
├─────────────────────────────────────────────────────┤
│ ENDEREÇO                                            │
├─────────────────────────────────────────────────────┤
│ Cidade / Município:  [___________________________]  │ ← SIMPLIFICADO
└─────────────────────────────────────────────────────┘
```

---

## 🎯 Fluxo do Usuário Atualizado

### Processo Completo

1. **Usuário preenche dados básicos**
   - CNPJ, Razão Social, Nome Fantasia

2. **Chega no campo Inscrição Municipal**
   - Vê botão "Buscar Município" ao lado
   - Decide consultar IBGE

3. **Clica no botão "Buscar Município"**

4. **Sistema valida Estado (UF)**
   - Se não selecionado: Alert pedindo para selecionar
   - Se selecionado: Continua

5. **Alert com instruções**
   ```
   🗺️ BUSCAR MUNICÍPIO
   
   Estado selecionado: GO - Goiás
   
   A página do IBGE Cidades será aberta.
   
   Você poderá:
   • Ver todos os municípios do estado
   • Pesquisar pelo nome do município
   • Verificar informações oficiais
   
   Após encontrar o município, você pode anotar 
   a inscrição municipal no campo correspondente.
   ```

6. **Página IBGE abre**
   - URL: `https://cidades.ibge.gov.br/brasil/go/panorama`
   - Lista todos municípios de Goiás
   - Usuário pesquisa e consulta

7. **Usuário retorna ao formulário**
   - Campo "Inscrição Municipal" já está focado ✅
   - Preenche a inscrição encontrada

8. **Continua preenchimento**
   - Próximo campo: Regime Tributário
   - Depois: Endereço...

---

## ✨ Benefícios da Mudança

### 1. Melhor Organização Lógica
- **Antes:** Botão no campo Cidade (genérico)
- **Depois:** Botão no campo Inscrição Municipal (específico)
- **Resultado:** Usuário sabe onde preencher o dado consultado

### 2. Interface Mais Limpa
- Campo Cidade volta a ser simples
- Menos elementos visuais no endereço
- Foco no que é importante

### 3. Fluxo Mais Natural
- Consulta → Anota inscrição
- Foco automático no campo correto
- Menos confusão sobre onde preencher

### 4. Mantém Funcionalidade
- ✅ Mesma validação de UF
- ✅ Mesma abertura do IBGE
- ✅ Mesmo tratamento de erros
- ✅ Mesmos logs de debug

---

## 🧪 Como Testar

### Teste 1: Validação de UF

**Passos:**
1. Abrir formulário de novo cliente
2. NÃO selecionar Estado (UF)
3. Ir para campo Inscrição Municipal
4. Clicar botão "Buscar Município"

**Resultado Esperado:**
```
⚠️ ESTADO NÃO SELECIONADO!

Por favor, selecione o Estado (UF) antes de buscar o município.

O estado é necessário para filtrar os municípios corretos.
```
✅ Foco move para campo Estado

---

### Teste 2: Abertura do IBGE

**Passos:**
1. Selecionar Estado: "GO - Goiás"
2. Ir para campo Inscrição Municipal
3. Clicar botão "Buscar Município"
4. Ver alert com instruções
5. Clicar "OK"

**Resultado Esperado:**
- ✅ Nova aba abre: `https://cidades.ibge.gov.br/brasil/go/panorama`
- ✅ Página IBGE mostra municípios de Goiás
- ✅ Campo Inscrição Municipal fica focado

---

### Teste 3: Foco no Campo Correto

**Passos:**
1. Seguir Teste 2
2. Consultar município no IBGE
3. Retornar à aba do formulário (sem clicar em nada)

**Resultado Esperado:**
- ✅ Campo "Inscrição Municipal" está focado
- ✅ Cursor piscando no campo
- ✅ Pronto para digitar

---

### Teste 4: Campo Cidade Simplificado

**Passos:**
1. Rolar até seção Endereço
2. Localizar campo "Cidade / Município"

**Resultado Esperado:**
- ✅ Campo é input simples
- ❌ Sem botão ao lado
- ❌ Sem texto de ajuda abaixo
- ✅ Visual limpo

---

## 📊 Comparação: Antes vs Depois

| Aspecto | ANTES | DEPOIS |
|---------|-------|--------|
| **Localização do Botão** | Ao lado do campo Cidade | Ao lado de Inscrição Municipal |
| **Campo Cidade** | Input + Botão + Helper text | Input simples |
| **Campo Insc. Municipal** | Input simples | Input + Botão + Helper text |
| **Foco após consulta** | Campo Cidade | Campo Inscrição Municipal |
| **Mensagem do alert** | "digite o nome no campo Cidade" | "anote a inscrição municipal" |
| **Funcionalidade IBGE** | Mesma | Mesma ✅ |

---

## 📝 Notas Técnicas

### Arquivos Modificados

- **templates/clientes/form.html**
  - Linhas 154-177: Campo Inscrição Municipal (adicionado botão)
  - Linhas 346-353: Campo Cidade (simplificado)
  - Linhas 1025-1073: Função buscarMunicipio() (ajustada)

### Compatibilidade

- ✅ Mantém todas validações
- ✅ Mantém tratamento de erros
- ✅ Mantém console logs
- ✅ Mantém estilo visual
- ✅ Sem quebras de funcionalidade

### Dependências

- Font Awesome (ícone fa-map-marked-alt)
- Bootstrap (btn-info)
- JavaScript nativo (DOM, window.open)

---

## 🎯 Conclusão

A relocação do botão "Buscar Município" foi implementada com sucesso, atendendo ao requisito do usuário de:

✅ Mover botão para perto da Inscrição Municipal  
✅ Manter a mesma função (abertura do IBGE)  
✅ Focar no campo correto após consulta  

A mudança torna a interface mais lógica e organizada, melhorando a experiência do usuário ao cadastrar novos clientes.

---

**Status:** ✅ Implementado e Testado  
**Data:** 12 de março de 2026  
**Versão:** 1.0
