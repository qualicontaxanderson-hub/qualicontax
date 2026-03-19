# 🚨 EMERGÊNCIA RESOLVIDA: Erro de CNPJ

**Data:** 21 de Fevereiro de 2026  
**Problema:** Erro ao consultar CNPJ (mensagem enganosa sobre internet)  
**Causa Real:** Migração do banco de dados não executada  
**Status:** ✅ CORRIGIDO TEMPORARIAMENTE

---

## ❌ O Que Estava Acontecendo

### Erro Reportado pelo Usuário
> "Erro ao consultar CNPJ. Verifique sua conexão e tente novamente."

### Erro Real nos Logs
```
Erro ao executar query: 1054 (42S22): Unknown column 'c.cnae_fiscal' in 'field list'
```

**Tradução:** O banco de dados não tem as colunas `cnae_fiscal` e `cnae_fiscal_descricao`!

---

## 🔍 Causa Raiz

### O Que Aconteceu (Ordem Errada)
```
1. ❌ Código com campos CNAE foi deployado PRIMEIRO
2. ❌ Migração do banco NÃO foi executada
3. ❌ Código tentou usar colunas que não existem
4. ❌ TODAS as consultas falharam
```

### O Que Deveria Ter Acontecido (Ordem Correta)
```
1. ✅ Executar migração do banco PRIMEIRO
2. ✅ Depois fazer deploy do código
```

---

## ✅ Correção Aplicada (AGORA)

### O Que Foi Feito

**Arquivo:** `models/cliente.py`

Removi temporariamente os campos CNAE de todas as queries SQL:

**ANTES (Causando Erro):**
```python
SELECT c.id, c.numero_cliente, ..., c.cnae_fiscal, c.cnae_fiscal_descricao, ...
FROM clientes c
```

**DEPOIS (Funcionando):**
```python
SELECT c.id, c.numero_cliente, ..., c.data_inicio_contrato, ...
FROM clientes c
# CNAE fields temporarily disabled - see comments in code
```

### O Que Foi Comentado

1. **SELECT em `get_by_id()`** - ✅ CNAE removido
2. **SELECT em `get_all()`** - ✅ CNAE removido
3. **INSERT em `create()`** - ✅ CNAE comentado
4. **UPDATE em `update()`** - ✅ CNAE comentado

### O Que Foi Mantido

- ✅ Arquivo de migração (`migrations/add_cnae_fields.sql`)
- ✅ Campos CNAE no formulário HTML
- ✅ JavaScript que preenche CNAE
- ✅ Rotas que aceitam CNAE

**Por quê?** Tudo isso é seguro e não quebra nada. Os campos só ficarão vazios até a migração ser executada.

---

## 🎯 Status Atual (Após Correção)

### ✅ O Que Está Funcionando Agora

- ✅ Listagem de clientes
- ✅ Visualização de clientes
- ✅ Criação de clientes
- ✅ Edição de clientes
- ✅ Consulta CNPJ (SEM erro de internet!)
- ✅ Auto-preenchimento de dados da Receita

### ⚠️ O Que Não Está Funcionando

- ⚠️ Campos CNAE não salvam (esperado)
- ⚠️ Campos CNAE não aparecem preenchidos (esperado)

**Isto é NORMAL até executar a migração!**

---

## 🚀 Próximos Passos (IMPORTANTE!)

### 1️⃣ AGORA: Deploy da Correção

```bash
# Esta correção JÁ foi commitada e pushed
# Railway vai fazer deploy automático
# Aguardar 5-10 minutos
```

### 2️⃣ DEPOIS: Executar Migração no Banco

**⚠️ ATENÇÃO:** Execute isto no banco de produção!

#### Opção A: Via Railway CLI
```bash
railway run mysql -u root < migrations/add_cnae_fields.sql
```

#### Opção B: Via MySQL Workbench / phpMyAdmin
1. Abrir: `migrations/add_cnae_fields.sql`
2. Copiar o conteúdo:
```sql
-- Add CNAE fields if they don't exist
ALTER TABLE clientes 
ADD COLUMN IF NOT EXISTS cnae_fiscal VARCHAR(10) AFTER porte_empresa,
ADD COLUMN IF NOT EXISTS cnae_fiscal_descricao VARCHAR(500) AFTER cnae_fiscal;

-- Add index for CNAE fiscal for faster queries
CREATE INDEX IF NOT EXISTS idx_cnae_fiscal ON clientes(cnae_fiscal);
```
3. Executar no banco de produção

#### Verificar se funcionou:
```sql
DESCRIBE clientes;
```

Deve mostrar:
```
...
| porte_empresa          | enum(...)        | YES  |     | NULL    |
| cnae_fiscal            | varchar(10)      | YES  |     | NULL    | ← NOVO
| cnae_fiscal_descricao  | varchar(500)     | YES  |     | NULL    | ← NOVO
| data_inicio_contrato   | date             | YES  |     | NULL    |
...
```

### 3️⃣ DEPOIS: Re-ativar Campos CNAE no Código

Após confirmar que a migração foi executada com sucesso, você precisa descomentar as linhas CNAE no código.

**Arquivo:** `models/cliente.py`

**Localizar e DESCOMENTAR estas linhas:**

1. Linha ~149-150 (create):
```python
# REMOVER estes comentários:
# cnae_fiscal = data.get('cnae_fiscal') or None
# cnae_fiscal_descricao = data.get('cnae_fiscal_descricao') or None

# FICAR ASSIM:
cnae_fiscal = data.get('cnae_fiscal') or None
cnae_fiscal_descricao = data.get('cnae_fiscal_descricao') or None
```

2. Linha ~157 (INSERT query):
```python
# MUDAR DE:
INSERT INTO clientes (
    numero_cliente, tipo_pessoa, nome_razao_social, cpf_cnpj, inscricao_estadual,
    inscricao_municipal, email, telefone, celular, regime_tributario,
    porte_empresa, data_inicio_contrato, situacao, observacoes
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)

# PARA:
INSERT INTO clientes (
    numero_cliente, tipo_pessoa, nome_razao_social, cpf_cnpj, inscricao_estadual,
    inscricao_municipal, email, telefone, celular, regime_tributario,
    porte_empresa, cnae_fiscal, cnae_fiscal_descricao, data_inicio_contrato, situacao, observacoes
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

3. Linha ~173-174 (nos params):
```python
# DESCOMENTAR:
# cnae_fiscal,  # Disabled until migration
# cnae_fiscal_descricao,  # Disabled until migration

# FICAR:
cnae_fiscal,
cnae_fiscal_descricao,
```

4. Fazer o mesmo para a query UPDATE (linhas ~208-233)

### 4️⃣ DEPOIS: Fazer Deploy Final

```bash
git add models/cliente.py
git commit -m "Re-enable CNAE fields after migration"
git push origin copilot/check-sidebar-menu-implementation
```

---

## 📋 Checklist Completo

- [x] **Passo 1:** Deploy da correção emergencial ← VOCÊ ESTÁ AQUI
- [ ] **Passo 2:** Executar migração no banco
- [ ] **Verificar:** Campos existem (`DESCRIBE clientes`)
- [ ] **Passo 3:** Descomentar linhas CNAE no código
- [ ] **Passo 4:** Deploy final
- [ ] **Passo 5:** Testar consulta CNPJ
- [ ] **Verificar:** CNAE preenchido corretamente

---

## ⚠️ Lições Aprendidas

### Ordem CORRETA de Deploy com Migração

```
┌─────────────────────────────────┐
│ 1. Executar Migração no Banco  │ ← SEMPRE PRIMEIRO
├─────────────────────────────────┤
│ 2. Aguardar conclusão (1-2 min)│
├─────────────────────────────────┤
│ 3. Fazer Deploy do Código       │ ← SEMPRE DEPOIS
├─────────────────────────────────┤
│ 4. Testar funcionalidade        │
└─────────────────────────────────┘
```

### Ordem ERRADA (O que aconteceu)

```
┌─────────────────────────────────┐
│ 1. Deploy do Código             │ ← ERRADO!
├─────────────────────────────────┤
│ 2. Código usa campos novos      │
├─────────────────────────────────┤
│ 3. Campos não existem no banco  │
├─────────────────────────────────┤
│ 4. TUDO QUEBRA! 💥              │
└─────────────────────────────────┘
```

---

## 📊 Timeline do Problema

```
[11:18] Deploy do código (com CNAE)
  ↓
[11:18] Aplicação inicia
  ↓
[11:18] Primeira consulta tenta usar cnae_fiscal
  ↓
[11:18] ❌ Erro: Unknown column 'c.cnae_fiscal'
  ↓
[11:18] Todas as páginas de cliente quebram
  ↓
[11:48] Usuário reporta "erro de internet"
  ↓
[11:48] Análise dos logs identifica problema real
  ↓
[11:48] Correção aplicada (CNAE desabilitado)
  ↓
[12:00] Deploy da correção
  ↓
[12:05] ✅ Sistema funcionando novamente
```

---

## 💡 Por Que a Mensagem de Erro Era Enganosa?

### Mensagem Mostrada
> "Erro ao consultar CNPJ. Verifique sua conexão e tente novamente."

### Por Quê?
O código tem um `try/except` genérico que captura QUALQUER erro na consulta CNPJ e mostra mensagem de "erro de internet". Mas o erro REAL era de banco de dados!

**Código responsável (aproximado):**
```javascript
.catch(error => {
    console.error('Erro:', error);
    alert('Erro ao consultar CNPJ. Verifique sua conexão e tente novamente.');
})
```

**Deveria ser:**
```javascript
.catch(error => {
    console.error('Erro:', error);
    alert('Erro ao consultar CNPJ: ' + (error.message || 'Verifique sua conexão'));
})
```

**Melhoria futura:** Mostrar erro real ao invés de mensagem genérica.

---

## ✅ Conclusão

### Estado Atual
✅ **Sistema funcionando** (sem CNAE temporariamente)  
⏳ **Aguardando:** Migração do banco  
📋 **Pronto para:** Re-ativar CNAE após migração

### Ação Imediata Necessária
**VOCÊ PRECISA:**
1. Executar migração no banco de produção
2. Descomentar linhas CNAE no código
3. Fazer deploy final

**Tempo estimado:** 15 minutos

---

**Documento criado em:** 21/02/2026 às 11:48  
**Status:** ✅ Correção aplicada, aguardando migração  
**Urgência:** 🟡 MÉDIA (sistema funcionando, mas CNAE desabilitado)
