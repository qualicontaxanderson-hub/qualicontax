# 🔍 DEBUG: "Ainda só puxa razão social e nome fantasia"

## Problema Reportado

Usuário relata que ao consultar CNPJ, apenas a Razão Social e Nome Fantasia são preenchidos. Os demais campos (telefone, endereço, etc.) não são preenchidos.

## Análise dos Logs

### Backend (o que foi enviado da API)
```
=== DADOS RETORNADOS PELA BRASIL API ===
Email: None
DDD Telefone 1: 6296335566        ✅ DADOS EXISTEM!
DDD Telefone 2: 
Razão Social: POSTO NOVO HORIZONTE GOIATUBA LTDA
Inscrições Estaduais (raw): None
CEP: 75600000                     ✅ DADOS EXISTEM!
Logradouro: RODOVIA GO 320        ✅ DADOS EXISTEM!
Número: 245                       ✅ DADOS EXISTEM!
UF: GO                            ✅ DADOS EXISTEM!
Inscrição Estadual extraída: ''
```

**CONCLUSÃO:** O backend ESTÁ ENVIANDO OS DADOS corretamente para o frontend!

### O Problema

Se o backend envia os dados, mas o frontend não preenche os campos, existem 3 possibilidades:

1. **JavaScript não está executando** - função `preencherDadosCNPJ()` não está sendo chamada
2. **Campos não existem no DOM** - IDs dos campos HTML não correspondem aos esperados
3. **Erro silencioso** - JavaScript falhando sem mostrar erro visível

## Correção Implementada

### Melhorias de Debug

Adicionado logging COMPLETO em **CADA ETAPA** do preenchimento:

#### 1. Início da Função
```javascript
console.log('=== INÍCIO DO PREENCHIMENTO AUTOMÁTICO ===');
console.log('=== DADOS RECEBIDOS (COMPLETOS) ===', JSON.stringify(data, null, 2));
```
Isso mostra:
- ✅ Se a função foi chamada
- ✅ Estrutura exata dos dados recebidos

#### 2. Cada Campo Individual
```javascript
if (data.logradouro) {
    const campo = document.getElementById('logradouro');
    if (campo) {
        campo.value = data.logradouro;
        camposPreenchidosCount++;
        console.log('✅ Logradouro preenchido:', data.logradouro);
    } else {
        console.error('❌ Campo logradouro não encontrado!');
    }
}
```

#### 3. Resumo Final
```javascript
console.log('=== RESUMO DO PREENCHIMENTO ===');
console.log(`Total de campos preenchidos: ${camposPreenchidosCount}`);
console.log('=== FIM DO PREENCHIMENTO AUTOMÁTICO ===');
```

### Campos Verificados

Cada um destes campos agora tem logging individual:

**Dados Básicos:**
- ✅ Razão Social (nome_razao_social_pj)
- ✅ Nome Fantasia (nome_fantasia)
- ✅ Porte (porte_empresa)
- ✅ Data de Início da Atividade (data_inicio_atividade)
- ✅ Inscrição Estadual (inscricao_estadual)
- ✅ CNAE Fiscal (cnae_fiscal)
- ✅ Descrição CNAE (cnae_fiscal_descricao)

**Contato:**
- ✅ Email (email)
- ✅ Telefone (telefone) - com formatação (XX) XXXX-XXXX
- ✅ Celular (celular) - com formatação (XX) XXXXX-XXXX

**Endereço:**
- ✅ CEP (cep) - com formatação XXXXX-XXX
- ✅ Logradouro (logradouro)
- ✅ Número (numero)
- ✅ Complemento (complemento)
- ✅ Bairro (bairro)
- ✅ Cidade (cidade) - vem de `municipio` da API
- ✅ Estado (estado) - vem de `uf` da API

## Como Testar

### Passo 1: Deploy
```bash
# Merge este PR para main
git checkout main
git merge copilot/check-sidebar-menu-implementation

# Railway vai fazer deploy automaticamente
```

### Passo 2: Abrir Console do Navegador

1. Acesse https://app.qualicontax.com.br/clientes/novo
2. Pressione **F12** (ou Ctrl+Shift+I)
3. Clique na aba **Console**
4. Deixe o console aberto

### Passo 3: Consultar CNPJ

1. Digite um CNPJ válido (ex: 41.850.700/0001-33)
2. Clique em "Consultar CNPJ"
3. Aguarde resposta
4. Clique **SIM** quando perguntar se quer preencher

### Passo 4: Analisar Logs

Você deve ver logs assim:

```
=== INÍCIO DO PREENCHIMENTO AUTOMÁTICO ===
=== DADOS RECEBIDOS (COMPLETOS) === {
  "razao_social": "POSTO NOVO HORIZONTE GOIATUBA LTDA",
  "nome_fantasia": "POSTO NOVO HORIZONTE",
  "ddd_telefone_1": "6296335566",
  "cep": "75600000",
  "logradouro": "RODOVIA GO 320",
  "numero": "245",
  "uf": "GO",
  ...
}

=== PROCESSANDO TELEFONES ===
Telefone 1 recebido: 6296335566 | Tipo: string
Telefone 1 limpo: 6296335566 | Tamanho: 10
✅ Telefone preenchido: (62) 9633-5566

=== PROCESSANDO ENDEREÇO ===
CEP: 75600000 | Logradouro: RODOVIA GO 320 | Número: 245
Bairro: undefined | Cidade: undefined | UF: GO
✅ CEP preenchido: 75600-000
✅ Logradouro preenchido: RODOVIA GO 320
✅ Número preenchido: 245
✅ Estado preenchido: GO

=== RESUMO DO PREENCHIMENTO ===
Total de campos preenchidos: 8
=== FIM DO PREENCHIMENTO AUTOMÁTICO ===
```

## Interpretação dos Logs

### ✅ Cenário Ideal (Tudo Funcionando)
```
✅ Razão Social preenchida: ...
✅ Nome Fantasia preenchido: ...
✅ Telefone preenchido: ...
✅ CEP preenchido: ...
✅ Logradouro preenchido: ...
✅ Número preenchido: ...
✅ Estado preenchido: ...
Total de campos preenchidos: 8
```

### ❌ Cenário Problemático (Campo Não Existe)
```
❌ Campo logradouro não encontrado!
```
**Significado:** O campo HTML com `id="logradouro"` não existe na página

### ⚠️ Cenário Normal (Dado Não Disponível)
```
⚠️ Email não disponível (normal - muitas empresas não têm email)
⚠️ Inscrição Estadual não disponível (normal para muitas empresas)
```
**Significado:** A API não retornou esse dado - é esperado

## Possíveis Causas e Soluções

### Causa 1: Função Não É Chamada
**Sintoma:** Não aparece "=== INÍCIO DO PREENCHIMENTO ===" no console

**Solução:**
- Verificar se clicou "SIM" no prompt
- Verificar se há erro JavaScript antes dessa função

### Causa 2: Campos Não Existem no DOM
**Sintoma:** Muitos logs "❌ Campo X não encontrado!"

**Solução:**
- Verificar se está na página correta (/clientes/novo)
- Verificar se formulário foi carregado completamente
- Verificar HTML tem os IDs corretos

### Causa 3: Dados Não Vêm da API
**Sintoma:** Logs mostram `undefined` ou `null` para vários campos

**Solução:**
- Verificar logs do backend (Railway)
- Testar com CNPJ diferente
- API Brasil pode estar com problema

### Causa 4: Problema Específico de Campo
**Sintoma:** Alguns campos preenchem, outros não

**Exemplo:** Se telefone não preenche mas endereço sim:
```
⚠️ Telefone 1 inválido - tamanho: 9
```
**Solução:** Telefone tem menos de 10 dígitos (inválido)

## Checklist de Verificação

Após fazer o teste, responda:

- [ ] Console mostra "=== INÍCIO DO PREENCHIMENTO ===" ?
- [ ] Console mostra dados completos em JSON?
- [ ] Console mostra "✅" para alguns campos?
- [ ] Console mostra "❌" para algum campo?
- [ ] Console mostra total de campos preenchidos?
- [ ] Campos visualmente aparecem preenchidos na tela?

## Próximos Passos

### Se Logs Aparecem Corretos Mas Campos Não Preenchem
Pode ser problema de CSS escondendo os valores ou JavaScript posterior limpando.

### Se Logs Mostram "❌ Campo não encontrado"
Precisa verificar se IDs do HTML correspondem aos esperados.

### Se Logs Não Aparecem
Função não está sendo executada - precisa verificar o fluxo de confirmação.

### Se Alguns Campos Preenchem
Enviar screenshot dos logs para análise específica de quais campos falharam.

## Informações para Reportar

Se o problema persistir, favor informar:

1. **Screenshot do console completo** após tentativa de preencher
2. **CNPJ testado**
3. **Quais campos preencheram** (se algum)
4. **Quais campos NÃO preencheram**
5. **Mensagens de erro** (se houver ❌ vermelho)

Com essas informações, podemos identificar exatamente onde está o problema!

## Resumo

✅ **Backend:** Enviando dados corretamente  
🔍 **Frontend:** Adicionado debug completo  
🎯 **Objetivo:** Identificar exatamente qual campo falha e por quê  
📊 **Resultado:** Logs detalhados para diagnóstico preciso  

---

**Data:** 21/02/2026  
**Status:** Debug implementado, aguardando teste em produção
