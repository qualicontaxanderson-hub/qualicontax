# 🚀 GUIA RÁPIDO DE DEPLOY: Data de Início e CNAE

**IMPORTANTE:** Siga esta ordem exata!

---

## ⚠️ Pré-requisitos

Você **DEVE** executar a migração do banco **ANTES** de fazer o deploy do código!

---

## 📋 Passo a Passo

### 1️⃣ Executar Migração no Banco (PRIMEIRO!)

#### Opção A: Via Railway CLI (Recomendado)
```bash
# Se você tem Railway CLI instalado
railway run mysql -u root < migrations/add_cnae_fields.sql
```

#### Opção B: Via MySQL Workbench / phpMyAdmin
1. Abrir o arquivo: `migrations/add_cnae_fields.sql`
2. Copiar todo o conteúdo
3. Colar na aba SQL do seu gerenciador
4. Executar

#### Opção C: Via Linha de Comando
```bash
# Se você tem acesso SSH ao servidor
mysql -u usuario -p nome_do_banco < migrations/add_cnae_fields.sql
```

**Verificar se funcionou:**
```sql
DESCRIBE clientes;
```

Deve mostrar:
```
...
| porte_empresa          | enum(...)        | YES  |     | NULL    |                |
| cnae_fiscal            | varchar(10)      | YES  |     | NULL    |                | ← NOVO
| cnae_fiscal_descricao  | varchar(500)     | YES  |     | NULL    |                | ← NOVO
| data_inicio_contrato   | date             | YES  |     | NULL    |                |
...
```

---

### 2️⃣ Fazer Merge do Código (DEPOIS!)

```bash
# Via GitHub UI (Recomendado)
1. Acessar: https://github.com/qualicontaxanderson-hub/qualicontax/pulls
2. Encontrar PR com título "Add CNAE fields..."
3. Clicar "Merge pull request"
4. Aguardar deploy automático do Railway (5-10 min)

# OU via linha de comando
git checkout main
git merge copilot/check-sidebar-menu-implementation
git push origin main
```

---

### 3️⃣ Limpar Cache (SEMPRE!)

```
Windows: Ctrl + F5
Mac: Cmd + Shift + R
Ou: Abrir aba anônima
```

---

### 4️⃣ Testar

1. Acessar: https://app.qualicontax.com.br/clientes/novo
2. Selecionar "Pessoa Jurídica"
3. Digitar CNPJ válido (ex: qualquer CNPJ real)
4. Clicar "Consultar CNPJ"
5. ✅ Verificar: Data de Início da Atividade preenchida
6. ✅ Verificar: CNAE Fiscal preenchido
7. ✅ Verificar: Descrição do CNAE preenchida
8. Salvar cliente
9. ✅ Verificar que salvou com sucesso

---

## ✅ Checklist Completo

- [ ] **Passo 1:** Migração executada no banco
- [ ] **Verificação:** Campos cnae_fiscal e cnae_fiscal_descricao existem
- [ ] **Passo 2:** Código mergeado para main
- [ ] **Verificação:** Railway fez deploy (ver logs)
- [ ] **Passo 3:** Cache limpo (Ctrl + F5)
- [ ] **Passo 4:** Testado consulta CNPJ
- [ ] **Verificação:** Data de Início preenchida ✓
- [ ] **Verificação:** CNAE Fiscal preenchido ✓
- [ ] **Verificação:** Descrição do CNAE preenchida ✓
- [ ] **Passo 5:** Criado cliente de teste
- [ ] **Verificação:** Cliente salvo com CNAE ✓

---

## ⚠️ Troubleshooting

### Erro: "Unknown column 'cnae_fiscal'"
**Causa:** Migração não foi executada  
**Solução:** Execute o Passo 1 primeiro!

### CNAE não preenche ao consultar CNPJ
**Causa 1:** Cache do navegador  
**Solução:** Ctrl + F5

**Causa 2:** Deploy não concluído  
**Solução:** Verificar Railway → Deployments

**Causa 3:** CNPJ não tem CNAE na Receita  
**Solução:** Normal, testar com outro CNPJ

### Data de Início não preenche
**Causa:** Provavelmente cache  
**Solução:** Ctrl + F5 ou aba anônima

**Nota:** Data de Início JÁ funcionava, não mudou!

---

## 📊 Resultado Esperado

### Antes da Consulta
```
Data de Início da Atividade: [________]
CNAE Fiscal: [________]
Descrição do CNAE: [________]
```

### Depois da Consulta (Exemplo Real)
```
Data de Início da Atividade: [2015-03-15] ⭐
CNAE Fiscal: [4711-3/02] ⭐
Descrição do CNAE: [Comércio varejista de mercadorias em geral, com predominância de produtos alimentícios - minimercados, mercearias e armazéns] ⭐
```

### Mensagem de Sucesso
```
✅ Dados preenchidos com sucesso!

Os seguintes dados foram obtidos da Receita Federal:
• Razão Social
• Nome Fantasia
• Inscrição Estadual
• Data de Início da Atividade  ← Já funcionava
• CNAE Fiscal  ← NOVO!
• Descrição do CNAE  ← NOVO!
• E-mail
• Telefone
• CEP
• Endereço
...
```

---

## 🎯 Próximos Passos

**Agora mesmo:**
1. Execute a migração (Passo 1)
2. Faça o merge (Passo 2)
3. Aguarde 10 minutos
4. Teste!

**Depois:**
- Documentar para equipe
- Treinar usuários
- Monitorar por 1-2 dias

---

## 💡 Dicas

1. **Sempre execute migração ANTES do deploy**
2. **Sempre limpe cache ao testar**
3. **Teste com CNPJ real** (não inventado)
4. **Veja console do navegador** (F12) para debug
5. **Campos CNAE são read-only** (correto!)

---

**Tempo estimado:** 15 minutos  
**Dificuldade:** Fácil  
**Risco:** Baixo (migration segura com IF NOT EXISTS)

---

**Criado em:** 21/02/2026  
**Versão:** 1.0  
**Status:** ✅ Pronto para executar
