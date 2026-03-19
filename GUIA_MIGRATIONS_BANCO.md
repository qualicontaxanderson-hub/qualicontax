# 🗄️ GUIA: O QUE ALTERAR NO BANCO DE DADOS

## 📋 Resposta Rápida (30 segundos)

### ⚠️ APENAS 1 MIGRATION PRECISA SER EXECUTADA:

```bash
mysql -u root -p railway < migrations/add_data_inicio_atividade.sql
```

**Status:**
- ✅ Migration CNAE: **JÁ EXECUTADA** (nada a fazer)
- ⚠️ Migration Data Início: **PENDENTE** (precisa executar)

---

## 📊 Status de Todas as Migrations Deste PR

| # | Migration | Status | Descrição | Ação Necessária |
|---|-----------|--------|-----------|-----------------|
| 1 | `add_cnae_fields.sql` | ✅ **EXECUTADA** | Adiciona campos CNAE fiscal | ❌ Nenhuma (já feito) |
| 2 | `add_data_inicio_atividade.sql` | ⚠️ **PENDENTE** | Separa data de fundação da empresa | ✅ **EXECUTAR AGORA** |

---

## 🎯 O Que Você PRECISA Fazer

### Migration Pendente: `add_data_inicio_atividade.sql`

**O que faz:**
- Adiciona coluna `data_inicio_atividade` na tabela `clientes`
- Separa conceito de "data de fundação da empresa" vs "data de início do contrato"
- Campo será auto-preenchido ao consultar CNPJ

**Por que é necessário:**
- Antes: 1 campo usado para 2 propósitos diferentes (confuso!)
- Depois: 2 campos separados e claros

---

## 🛠️ Como Executar (3 Opções)

### Opção 1: Railway CLI (Recomendado) ⭐

```bash
# Passo 1: Login no Railway
railway login

# Passo 2: Selecionar projeto
railway link

# Passo 3: Executar migration
railway run mysql -u root -p$DATABASE_PASSWORD railway < migrations/add_data_inicio_atividade.sql
```

### Opção 2: Railway Dashboard (Visual)

1. Acesse: https://dashboard.railway.app
2. Selecione seu projeto "qualicontax"
3. Clique no serviço "MySQL"
4. Aba "Query"
5. Cole o conteúdo de `migrations/add_data_inicio_atividade.sql`
6. Clique "Run Query"

### Opção 3: Conexão Direta MySQL

```bash
# Usando suas credenciais do Railway
mysql -h hopper.proxy.rlwy.net -P 54114 -u root -p railway < migrations/add_data_inicio_atividade.sql

# Quando pedir senha, digite sua senha do MySQL Railway
```

---

## 📝 Conteúdo da Migration (O Que Será Executado)

```sql
-- Migration: Add data_inicio_atividade field
-- Separa data de fundação da empresa da data de início do contrato

-- Adiciona nova coluna para data de fundação da empresa
ALTER TABLE clientes 
ADD COLUMN IF NOT EXISTS data_inicio_atividade DATE 
COMMENT 'Data de início das atividades da empresa (obtida do CNPJ)';

-- Verificação
SELECT 
    COLUMN_NAME, 
    DATA_TYPE, 
    IS_NULLABLE, 
    COLUMN_COMMENT
FROM INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_NAME = 'clientes' 
AND COLUMN_NAME IN ('data_inicio_atividade', 'data_inicio_contrato')
ORDER BY COLUMN_NAME;
```

**O que acontece:**
1. Adiciona coluna `data_inicio_atividade` (tipo DATE)
2. Permite valores NULL (opcional)
3. Adiciona comentário explicativo
4. Mostra resultado da verificação

---

## ✅ Como Verificar Se Funcionou

### Verificação 1: Ver estrutura da tabela

```sql
DESCRIBE clientes;
```

**Procure por esta linha:**
```
data_inicio_atividade | date | YES | | NULL |
```

### Verificação 2: Contar colunas

```sql
SELECT COUNT(*) as total
FROM INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_NAME = 'clientes' 
AND COLUMN_NAME = 'data_inicio_atividade';
```

**Resultado esperado:**
```
+-------+
| total |
+-------+
|     1 |
+-------+
```

### Verificação 3: Ver ambas as datas

```sql
SELECT 
    COLUMN_NAME, 
    DATA_TYPE, 
    IS_NULLABLE, 
    COLUMN_COMMENT
FROM INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_NAME = 'clientes' 
AND COLUMN_NAME IN ('data_inicio_atividade', 'data_inicio_contrato')
ORDER BY COLUMN_NAME;
```

**Resultado esperado:**
```
+-------------------------+-----------+-------------+--------------------------------------------------------+
| COLUMN_NAME             | DATA_TYPE | IS_NULLABLE | COLUMN_COMMENT                                         |
+-------------------------+-----------+-------------+--------------------------------------------------------+
| data_inicio_atividade   | date      | YES         | Data de início das atividades da empresa (obtida...)   |
| data_inicio_contrato    | date      | YES         |                                                        |
+-------------------------+-----------+-------------+--------------------------------------------------------+
```

---

## 🔍 Detalhes de Cada Migration

### Migration 1: CNAE Fields ✅ (JÁ EXECUTADA)

**Arquivo:** `migrations/add_cnae_fields.sql`

**O que adiciona:**
- `cnae_fiscal` VARCHAR(10) - Código CNAE principal
- `cnae_fiscal_descricao` VARCHAR(500) - Descrição da atividade
- Índice `idx_cnae_fiscal` para performance

**Status:** ✅ **JÁ EXECUTADA** em 21/02/2026
- Você confirmou: "Coluna cnae_fiscal já existe."
- Você confirmou: "Coluna cnae_fiscal_descricao já existe."
- Você confirmou: "Índice idx_cnae_fiscal já existe."

**Ação:** ❌ Nenhuma (já está no banco)

---

### Migration 2: Data Início Atividade ⚠️ (PENDENTE)

**Arquivo:** `migrations/add_data_inicio_atividade.sql`

**O que adiciona:**
- `data_inicio_atividade` DATE - Data de fundação da empresa

**Por que é necessário:**

**ANTES (Errado):**
- Campo `data_inicio_contrato` usado para 2 coisas:
  - Data de fundação da empresa (do CNPJ) ❌
  - Data de início do contrato de serviço ❌
- **Problema:** Confusão conceitual!

**DEPOIS (Correto):**
- `data_inicio_atividade` → Data de fundação da empresa (auto-fill CNPJ) ✅
- `data_inicio_contrato` → Data de início do contrato (manual) ✅
- **Vantagem:** Campos separados e claros!

**Status:** ⚠️ **PENDENTE** - Precisa ser executada!

**Ação:** ✅ **EXECUTAR AGORA**

---

## 🚨 Troubleshooting

### Erro: "Column 'data_inicio_atividade' already exists"

**Significa:** Migration já foi executada!

**Ação:** 
```
✅ Tudo OK! Não precisa fazer nada.
```

### Erro: "Access denied for user"

**Causa:** Senha incorreta ou usuário sem permissão

**Solução:**
1. Verifique credenciais no Railway Dashboard
2. Use usuário `root` com senha correta
3. Verifique se IP está autorizado

### Erro: "Can't connect to MySQL server"

**Causa:** Host ou porta incorretos

**Solução:**
1. Verifique host: `hopper.proxy.rlwy.net`
2. Verifique porta: `54114`
3. Teste conexão básica:
   ```bash
   mysql -h hopper.proxy.rlwy.net -P 54114 -u root -p -e "SELECT 1"
   ```

### Erro: "No such file or directory"

**Causa:** Executando do diretório errado

**Solução:**
```bash
# Navegue para raiz do projeto
cd /caminho/para/qualicontax

# Verifique que arquivo existe
ls -la migrations/add_data_inicio_atividade.sql

# Execute novamente
mysql ... < migrations/add_data_inicio_atividade.sql
```

---

## 📖 Resumo Executivo

### O Que Fazer (Checklist)

- [ ] 1. **Conectar no banco Railway**
  - Host: hopper.proxy.rlwy.net
  - Port: 54114
  - User: root
  - Database: railway

- [ ] 2. **Executar migration pendente**
  ```bash
  mysql -h hopper.proxy.rlwy.net -P 54114 -u root -p railway < migrations/add_data_inicio_atividade.sql
  ```

- [ ] 3. **Verificar se funcionou**
  ```sql
  DESCRIBE clientes;
  ```

- [ ] 4. **Confirmar coluna existe**
  - Deve aparecer: `data_inicio_atividade | date | YES`

- [ ] 5. **Próximo passo**
  - Merge do PR
  - Aguardar deploy
  - Testar formulário

### O Que NÃO Fazer

❌ **NÃO** executar `add_cnae_fields.sql` novamente (já foi executada)  
❌ **NÃO** alterar manualmente outras colunas  
❌ **NÃO** deletar dados existentes  
❌ **NÃO** modificar estrutura de outras tabelas  

---

## 🎯 Por Que Essas Mudanças?

### Migration CNAE (✅ já feita)
**Problema anterior:** Sistema não salvava CNAE das empresas

**Solução:** 
- Adicionar campos `cnae_fiscal` e `cnae_fiscal_descricao`
- Auto-preencher ao consultar CNPJ
- Salvar no banco para referência futura

**Benefício:** 
- Saber qual a atividade principal da empresa
- Facilitar classificação e relatórios

### Migration Data Início (⚠️ pendente)
**Problema anterior:** Um campo para dois propósitos

**Solução:**
- Separar `data_inicio_atividade` (fundação) de `data_inicio_contrato` (serviço)
- Posicionar corretamente no formulário
- Auto-preencher fundação via CNPJ

**Benefício:**
- Clareza conceitual
- Dados mais precisos
- Melhor organização

---

## 📅 Próximos Passos (Após Migration)

### 1. ✅ Executar migration (você está aqui)

```bash
mysql ... < migrations/add_data_inicio_atividade.sql
```

### 2. ✅ Merge do PR

- Aprovar PR #6
- Merge para branch `main`

### 3. ✅ Deploy automático

- Railway detecta mudança em `main`
- Deploy automático (5-10 minutos)
- Aguardar conclusão

### 4. ✅ Testar formulário

- Acessar: https://app.qualicontax.com.br/clientes/novo
- Consultar CNPJ de teste
- Verificar campos preenchem
- Conferir CNAE aparece
- Verificar data de início da atividade preenche

### 5. ✅ Confirmar funcionamento

- Criar cliente teste
- Salvar com dados
- Verificar campos salvaram no banco
- Editar cliente e ver dados corretos

---

## 📞 Precisa de Ajuda?

Se algo der errado:

1. **Anote a mensagem de erro completa**
2. **Tire screenshot do erro**
3. **Verifique:**
   - Qual comando executou
   - Qual erro recebeu
   - Em que passo está

4. **Informe:**
   - Mensagem de erro exata
   - Método usado (CLI, Dashboard, Direto)
   - Se conseguiu conectar no banco

---

## ✅ Conclusão

**APENAS 1 AÇÃO NECESSÁRIA:**

```bash
mysql -u root -p railway < migrations/add_data_inicio_atividade.sql
```

Depois disso:
- ✅ Banco estará atualizado
- ✅ Código funcionará corretamente
- ✅ Formulário terá todos os campos
- ✅ Auto-fill do CNPJ completo

**Simples assim!** 🎉
