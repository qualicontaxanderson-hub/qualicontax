# 🔍 DEBUG: Problema na Extração de Dados do CNPJ

**Data:** 21 de Fevereiro de 2026 - 12:13  
**Status:** 🔧 DEBUG ADICIONADO - AGUARDANDO TESTE

---

## 📋 Problema Reportado

### Logs do Sistema
```
Email: None
DDD Telefone 1: 6296335566
DDD Telefone 2: 
Razão Social: POSTO NOVO HORIZONTE GOIATUBA LTDA
Inscrição Estadual extraída: ''
```

### O Que Funciona ✅
- ✅ Razão Social
- ✅ Nome Fantasia

### O Que NÃO Funciona ❌
- ❌ Telefone (API retorna mas não preenche campo)
- ❌ Email (API retorna `None` - empresa sem email)
- ❌ Inscrição Estadual (extraída como vazia)
- ❌ Endereço (desconhecido se funciona)

---

## 🔍 Análise Inicial

### 1. Telefone
**API retorna:** `6296335566` (10 dígitos)

**Análise:**
- ✅ Formato correto: DDD (62) + Número (96335566)
- ✅ JavaScript deveria formatar: `(62) 9633-5566`
- ❓ **Por que não está preenchendo?**
  - Possível: Tipo de dado (number vs string)
  - Possível: Campo DOM não encontrado
  - Possível: Erro na formatação

### 2. Email
**API retorna:** `None`

**Análise:**
- ✅ Comportamento CORRETO
- ℹ️ Muitas empresas não têm email cadastrado na Receita Federal
- ✅ Sistema já trata isso corretamente com warning no console

### 3. Inscrição Estadual
**API retorna:** Estrutura em `inscricoes_estaduais`

**Análise:**
- ❓ Estrutura pode ser:
  - Lista vazia: `[]`
  - Lista com strings: `["123456789"]`
  - Lista com dicts: `[{"inscricao_estadual": "123", "ativo": true}]`
- ❓ Código atual assume lista de dicts
- 🔧 Precisa verificar estrutura real

### 4. Endereço
**Dados esperados:**
- CEP
- Logradouro
- Número
- Bairro
- Município
- UF

**Análise:**
- ❓ Não sabemos se API está retornando
- ❓ Não sabemos se está sendo preenchido
- 🔧 Adicionei logs para verificar

---

## 🔧 Correções Implementadas

### 1. Backend (routes/clientes.py)

**Logs Adicionados:**
```python
print(f"Email: {data.get('email')}")
print(f"DDD Telefone 1: {data.get('ddd_telefone_1')}")
print(f"DDD Telefone 2: {data.get('ddd_telefone_2')}")
print(f"Inscrições Estaduais (raw): {data.get('inscricoes_estaduais')}")
print(f"CEP: {data.get('cep')}")
print(f"Logradouro: {data.get('logradouro')}")
print(f"Número: {data.get('numero')}")
print(f"UF: {data.get('uf')}")

# Para cada IE:
print(f"DEBUG: Total de IEs: {len(data['inscricoes_estaduais'])}")
print(f"DEBUG: IE[{idx}] = {ie_obj} (tipo: {type(ie_obj)})")
print(f"DEBUG: IE ativa encontrada: {inscricao_estadual}")
```

**Objetivo:** Entender EXATAMENTE o que a API retorna

### 2. Frontend (templates/clientes/form.html)

**Logs para Telefone:**
```javascript
console.log('DEBUG Telefones - ddd_telefone_1:', data.ddd_telefone_1, 'Tipo:', typeof data.ddd_telefone_1);
console.log('DEBUG - Telefone 1 limpo:', telefone1, 'Tamanho:', telefone1.length);
console.log('DEBUG - Telefone 1 formatado:', telFormatado);
console.log('✅ Telefone preenchido:', telFormatado);
// OU
console.warn('⚠️ Telefone 1 inválido - tamanho:', telefone1.length);
```

**Correção Aplicada:**
```javascript
// ANTES
const telefone1 = data.ddd_telefone_1.replace(/\D/g, '');

// DEPOIS
const telefone1 = String(data.ddd_telefone_1).replace(/\D/g, '');
```

**Por quê?** Se a API retornar número (não string), `.replace()` falharia!

**Logs para Endereço:**
```javascript
console.log('DEBUG Endereço - CEP:', data.cep);
console.log('DEBUG Endereço - Logradouro:', data.logradouro);
console.log('✅ CEP preenchido:', cepFormatado);
console.log('✅ Logradouro preenchido:', data.logradouro);
```

**Correção Aplicada:**
```javascript
// ANTES
const cepFormatado = data.cep.replace(/\D/g, '').replace(/(\d{5})(\d{3})/, '$1-$2');

// DEPOIS
const cepFormatado = String(data.cep).replace(/\D/g, '').replace(/(\d{5})(\d{3})/, '$1-$2');
```

---

## 🧪 Como Testar

### 1. Fazer Deploy
```bash
# Via GitHub
1. Fazer merge do PR
2. Aguardar deploy do Railway (5-10 min)
```

### 2. Testar Consulta CNPJ
```
1. Acessar: https://app.qualicontax.com.br/clientes/novo
2. Selecionar "Pessoa Jurídica"
3. Digitar CNPJ: (o mesmo que testou antes)
4. Clicar "Consultar CNPJ"
5. Abrir Console (F12)
```

### 3. Analisar Logs

**No Terminal do Railway (Backend):**
```
=== DADOS RETORNADOS PELA BRASIL API ===
Email: None
DDD Telefone 1: 6296335566
DDD Telefone 2: 
Inscrições Estaduais (raw): [...]  ← VER ESTRUTURA COMPLETA
CEP: 75680000
Logradouro: RUA ...
Número: 123
UF: GO
DEBUG: Total de IEs: 1
DEBUG: IE[0] = ... (tipo: ...)  ← VER ESTRUTURA
```

**No Console do Navegador (Frontend):**
```
DEBUG Telefones - ddd_telefone_1: 6296335566 Tipo: string  ← OU number?
DEBUG - Telefone 1 limpo: 6296335566 Tamanho: 10
DEBUG - Telefone 1 formatado: (62) 9633-5566
✅ Telefone preenchido: (62) 9633-5566  ← DEVE APARECER!

DEBUG Endereço - CEP: 75680000
DEBUG Endereço - Logradouro: RUA ...
✅ CEP preenchido: 75680-000
✅ Logradouro preenchido: RUA ...
```

---

## 🎯 O Que Esperar

### Cenário 1: Telefone Agora Funciona ✅
```
✅ Telefone preenchido: (62) 9633-5566
```

**Causa:** Era problema de tipo (number vs string)  
**Correção:** `String()` resolveu

### Cenário 2: Telefone Ainda Não Funciona ❌
```
⚠️ Telefone 1 inválido - tamanho: 0
```

**Possíveis causas:**
- `ddd_telefone_1` vazio/null
- Campo DOM não existe
- Erro não previsto

**Ação:** Ver logs detalhados e investigar

### Cenário 3: IE Agora Funciona ✅
```
DEBUG: IE ativa encontrada: 123456789
Inscrição Estadual extraída: '123456789'
```

**Causa:** Estrutura estava sendo lida corretamente  
**Resultado:** IE preenchida no campo

### Cenário 4: IE Ainda Não Funciona ❌
```
DEBUG: Total de IEs: 0
Inscrição Estadual extraída: ''
```

**Causa:** Empresa realmente não tem IE cadastrada  
**Resultado:** Normal para alguns tipos de empresa

---

## 📊 Possíveis Resultados

### Melhor Cenário ✅
```
✅ Telefone preenchido: (62) 9633-5566
✅ CEP preenchido: 75680-000
✅ Logradouro preenchido: RUA EXEMPLO
✅ Número preenchido: 123
✅ Bairro preenchido: CENTRO
✅ Cidade preenchida: GOIATUBA
✅ Estado preenchido: GO
✅ Inscrição Estadual preenchida: 123456789
```

**Causa:** Problema era o `String()` faltando  
**Ação:** Remover logs de debug

### Pior Cenário ❌
```
⚠️ Telefone 1 inválido - tamanho: 0
DEBUG Endereço - CEP: undefined
DEBUG Endereço - Logradouro: undefined
```

**Causa:** API não está retornando os dados  
**Ação:** Investigar resposta completa da API

### Cenário Misto ⚠️
```
✅ Telefone preenchido: (62) 9633-5566
✅ Endereço preenchido
❌ Inscrição Estadual: ''
```

**Causa:** Telefone e endereço OK, IE não cadastrada  
**Ação:** Normal, alguns CNPJs não têm IE

---

## 🔍 Análise dos Logs Atuais

### O Que Sabemos

**API Retorna:**
```
Email: None ← Correto, empresa sem email
DDD Telefone 1: 6296335566 ← DADO EXISTE!
Razão Social: POSTO NOVO HORIZONTE... ← Funciona
Inscrição Estadual: '' ← Pode ser normal
```

**Conclusão Preliminar:**
1. ✅ API está retornando dados
2. ✅ Telefone EXISTE (6296335566)
3. ❓ Telefone não está sendo preenchido no formulário
4. ❓ Endereço - não sabemos ainda
5. ❓ IE - pode ser que empresa não tenha mesmo

---

## 🚀 Próximos Passos

### Imediato
1. **Fazer merge deste PR**
2. **Aguardar deploy**
3. **Testar CNPJ novamente**
4. **Verificar logs detalhados**

### Com Base nos Logs
1. **Se telefone funcionar:** Remover logs de debug
2. **Se telefone não funcionar:** Investigar mais fundo
3. **Se endereço funcionar:** Ótimo!
4. **Se endereço não funcionar:** Ver o que API retorna

### Correção Final
1. Identificar problema exato
2. Implementar correção
3. Testar novamente
4. Limpar logs de debug
5. Documentar solução

---

## 📝 Checklist de Testes

Após deploy, testar e marcar:

**Campos Básicos:**
- [ ] Razão Social preenchida
- [ ] Nome Fantasia preenchido

**Telefones:**
- [ ] Telefone 1 preenchido e formatado
- [ ] Telefone 2 (se houver)

**Endereço:**
- [ ] CEP preenchido e formatado
- [ ] Logradouro preenchido
- [ ] Número preenchido
- [ ] Complemento (se houver)
- [ ] Bairro preenchido
- [ ] Cidade preenchida
- [ ] Estado preenchido

**Dados Adicionais:**
- [ ] Inscrição Estadual (se houver)
- [ ] CNAE Fiscal
- [ ] Descrição do CNAE
- [ ] Data de Início
- [ ] Porte da Empresa

**Console Logs:**
- [ ] Ver todos os logs DEBUG
- [ ] Verificar se há warnings
- [ ] Verificar se há erros

---

## 💡 Informações Importantes

### Email
**É NORMAL** que muitas empresas não tenham email:
```
Email: None
```

Isso não é um erro! Muitas empresas pequenas não cadastram email na Receita Federal.

### Inscrição Estadual
**É NORMAL** que algumas empresas não tenham IE:
- Empresas de fora do estado
- Alguns tipos de negócio
- MEI (em alguns casos)

```
Inscrição Estadual extraída: ''
```

Pode não ser erro!

### Telefone
**NÃO É NORMAL** que telefone não preencha:
```
DDD Telefone 1: 6296335566  ← DADO EXISTE
```

Se o dado existe na API mas não preenche, há um bug!

---

## 🎊 Conclusão

### O Que Foi Feito
✅ Adicionado debug completo no backend  
✅ Adicionado debug completo no frontend  
✅ Corrigido conversão de tipo (String())  
✅ Logs detalhados para cada campo  

### O Que Esperar
🎯 Com os logs detalhados, saberemos EXATAMENTE onde está o problema!

### Próximo Passo
📞 **Teste e envie os logs!**

Com os logs completos, poderei identificar e corrigir qualquer problema restante!

---

**Criado em:** 21/02/2026 - 12:13  
**Status:** 🔧 Debug implementado - Aguardando teste  
**Ação necessária:** Testar e verificar logs
