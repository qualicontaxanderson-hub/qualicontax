# ✅ Checklist de Deploy - Conciliação Bancária

## 📋 Antes do Deploy

### 1. Verificar Arquivos

- [x] `models/conciliacao_bancaria.py` - Modelo criado
- [x] `models/memorizacao_conciliacao.py` - Modelo criado
- [x] `routes/contabil.py` - Rota criada
- [x] `templates/contabil/index.html` - Template criado
- [x] `templates/contabil/conciliacoes.html` - Template criado
- [x] `templates/contabil/nova_conciliacao.html` - Template criado
- [x] `migrations/008_create_conciliacao_bancaria.sql` - Migration criada
- [x] `app.py` - Blueprint registrado
- [x] `templates/base.html` - Menu atualizado

### 2. Documentação

- [x] `MODULO_CONCILIACAO_BANCARIA.md` - Guia completo (12.5KB)
- [x] `RESUMO_CONCILIACAO_BANCARIA.md` - Resumo executivo (8KB)
- [x] `CHECKLIST_DEPLOY_CONCILIACAO.md` - Este checklist

---

## 🚀 Passos para Deploy

### Passo 1: Merge da Branch

```bash
# Se estiver em outra branch, voltar para main
git checkout main

# Merge da branch de desenvolvimento
git merge copilot/check-sidebar-menu-implementation

# Ou criar Pull Request no GitHub e fazer merge lá
```

### Passo 2: Executar Migration no Banco de Dados

**Opção A: Conexão Direta MySQL**

```bash
# Conectar ao banco
mysql -h HOST -u USUARIO -p DATABASE_NAME

# Executar migration
source migrations/008_create_conciliacao_bancaria.sql;

# Ou em uma linha
mysql -h HOST -u USUARIO -p DATABASE_NAME < migrations/008_create_conciliacao_bancaria.sql
```

**Opção B: Via Railway/Heroku (se aplicável)**

```bash
# Railway
railway run mysql -h HOST -u USUARIO -p DATABASE_NAME < migrations/008_create_conciliacao_bancaria.sql

# Heroku
heroku run mysql -h HOST -u USUARIO -p DATABASE_NAME < migrations/008_create_conciliacao_bancaria.sql
```

**Opção C: Copiar e Colar no phpMyAdmin**

1. Abrir phpMyAdmin
2. Selecionar database
3. Ir em "SQL"
4. Copiar conteúdo de `migrations/008_create_conciliacao_bancaria.sql`
5. Colar e executar

### Passo 3: Verificar Tabelas Criadas

```sql
-- Executar no banco para verificar
SHOW TABLES LIKE '%conciliacao%';
SHOW TABLES LIKE '%memorizacao%';
SHOW TABLES LIKE '%exportacao%';
SHOW TABLES LIKE '%transacao%';

-- Deve retornar:
-- conciliacoes_bancarias
-- memorizacoes_conciliacao
-- exportacoes_contabeis
-- transacoes_bancarias

-- Verificar estrutura de uma tabela
DESCRIBE conciliacoes_bancarias;
```

### Passo 4: Restart da Aplicação

**Se local:**
```bash
# Parar aplicação (Ctrl+C)
# Reiniciar
python app.py
```

**Se Railway:**
```bash
railway up
# Ou apenas fazer push, deploy automático
git push origin main
```

**Se Heroku:**
```bash
git push heroku main
```

### Passo 5: Testar Acesso

1. Abrir navegador
2. Acessar aplicação
3. Fazer login
4. Menu lateral → **Contábil**
5. Verificar que página carrega

---

## ✅ Testes de Funcionalidade

### Teste 1: Acessar Módulo

- [ ] Menu "Contábil" aparece
- [ ] Submenu tem 3 opções:
  - [ ] Início
  - [ ] Conciliações Bancárias
  - [ ] Memorizações
- [ ] Clicar em "Início" → Dashboard carrega
- [ ] Cards aparecem corretamente

### Teste 2: Criar Memorização de Grupo

1. [ ] Ir em "Memorizações"
2. [ ] Clicar "Nova Memorização"
3. [ ] Selecionar Tipo: GRUPO
4. [ ] Escolher um grupo existente
5. [ ] Preencher:
   - [ ] Palavra-chave: "TESTE"
   - [ ] Categoria: "Teste"
   - [ ] Conta: "1.1.1.1"
   - [ ] Histórico: "Teste de memorização"
6. [ ] Salvar
7. [ ] Verificar que aparece na lista

### Teste 3: Criar Memorização Individual

1. [ ] Ir em "Memorizações"
2. [ ] Clicar "Nova Memorização"
3. [ ] Selecionar Tipo: INDIVIDUAL
4. [ ] Escolher um cliente existente
5. [ ] Preencher dados
6. [ ] Salvar
7. [ ] Verificar que aparece na lista

### Teste 4: Acessar Nova Conciliação

1. [ ] Ir em "Conciliações Bancárias"
2. [ ] Clicar "Nova Conciliação"
3. [ ] Formulário carrega
4. [ ] Campo de cliente aparece
5. [ ] Upload de arquivo aparece
6. [ ] Checkbox "Usar memorizações do grupo" aparece

### Teste 5: Filtros de Conciliações

1. [ ] Ir em "Conciliações Bancárias"
2. [ ] Filtros aparecem:
   - [ ] Cliente
   - [ ] Grupo
   - [ ] Status
3. [ ] Botão "Filtrar" funciona

### Teste 6: Filtros de Memorizações

1. [ ] Ir em "Memorizações"
2. [ ] Filtros aparecem:
   - [ ] Tipo
   - [ ] Grupo
   - [ ] Cliente
3. [ ] Botão "Filtrar" funciona

---

## 🐛 Troubleshooting

### Problema: "Contábil" não aparece no menu

**Solução:**
1. Verificar que `templates/base.html` foi atualizado
2. Fazer hard refresh (Ctrl+Shift+R)
3. Limpar cache do navegador
4. Verificar console do navegador por erros

### Problema: Erro ao acessar /contabil

**Solução:**
1. Verificar que `app.py` registrou o blueprint:
   ```python
   from routes.contabil import contabil
   app.register_blueprint(contabil)
   ```
2. Verificar que arquivo `routes/contabil.py` existe
3. Restart da aplicação
4. Verificar logs de erro

### Problema: Erro ao criar memorização

**Solução:**
1. Verificar que migration foi executada:
   ```sql
   SHOW TABLES LIKE 'memorizacoes_conciliacao';
   ```
2. Verificar estrutura da tabela:
   ```sql
   DESCRIBE memorizacoes_conciliacao;
   ```
3. Verificar que existem grupos/clientes cadastrados
4. Verificar console de erros

### Problema: Tabelas não foram criadas

**Solução:**
1. Re-executar migration
2. Verificar permissões do usuário MySQL
3. Verificar sintaxe do SQL
4. Executar linha por linha se necessário

---

## 📊 Validação Final

### Banco de Dados

```sql
-- Verificar que tabelas existem
SELECT COUNT(*) FROM information_schema.tables 
WHERE table_schema = 'seu_database' 
AND table_name IN (
    'conciliacoes_bancarias',
    'transacoes_bancarias',
    'memorizacoes_conciliacao',
    'exportacoes_contabeis'
);
-- Deve retornar: 4

-- Verificar estrutura
SHOW CREATE TABLE conciliacoes_bancarias;
SHOW CREATE TABLE memorizacoes_conciliacao;
```

### Aplicação

```bash
# Verificar que blueprint está registrado
grep -n "from routes.contabil import contabil" app.py
grep -n "app.register_blueprint(contabil)" app.py

# Deve retornar linha onde está importado e registrado
```

### Interface

- [ ] Dashboard do módulo carrega sem erros
- [ ] Ícones aparecem corretamente
- [ ] Botões são clicáveis
- [ ] Formulários aceitam input
- [ ] Navegação funciona

---

## 📝 Próximos Passos Após Deploy

### Imediato

1. [ ] Testar todas as funcionalidades
2. [ ] Criar memorizações de teste
3. [ ] Documentar qualquer bug encontrado
4. [ ] Coletar feedback de usuários

### Curto Prazo (Próximas Semanas)

1. [ ] Planejar Fase 2 (Parser OFX)
2. [ ] Pesquisar bibliotecas Python para OFX
3. [ ] Definir estrutura de arquivo de upload
4. [ ] Criar protótipo de importação

### Médio Prazo (Próximos Meses)

1. [ ] Implementar Fase 2 (Parser OFX)
2. [ ] Implementar Fase 3 (Auto-classificação)
3. [ ] Implementar Fase 4 (Exportação)
4. [ ] Treinamento de usuários

---

## 📚 Recursos Úteis

### Documentação

- **Guia Completo:** `MODULO_CONCILIACAO_BANCARIA.md`
- **Resumo Executivo:** `RESUMO_CONCILIACAO_BANCARIA.md`
- **Este Checklist:** `CHECKLIST_DEPLOY_CONCILIACAO.md`

### Código

- **Modelos:** `models/conciliacao_bancaria.py`, `models/memorizacao_conciliacao.py`
- **Rotas:** `routes/contabil.py`
- **Templates:** `templates/contabil/`
- **Migration:** `migrations/008_create_conciliacao_bancaria.sql`

### Bibliotecas para Fase 2

- **ofxparse:** Parser OFX em Python
- **BeautifulSoup4:** Parsing XML/HTML
- **lxml:** XML processing

```bash
# Instalar quando for implementar Fase 2
pip install ofxparse beautifulsoup4 lxml
```

---

## ✅ Checklist Final de Confirmação

Antes de considerar deploy completo:

- [ ] Migration executada com sucesso
- [ ] 4 tabelas criadas no banco
- [ ] Aplicação reiniciada
- [ ] Menu "Contábil" aparece
- [ ] Dashboard carrega
- [ ] Consegue criar memorização de GRUPO
- [ ] Consegue criar memorização INDIVIDUAL
- [ ] Consegue acessar lista de conciliações
- [ ] Consegue acessar nova conciliação
- [ ] Todos os filtros funcionam
- [ ] Não há erros no console
- [ ] Não há erros nos logs da aplicação

---

## 🎉 Deploy Completo!

Se todos os itens acima estão ✅:

**Parabéns! O Módulo de Conciliação Bancária está em produção!**

**Próximos passos:**
1. Comunicar aos usuários
2. Criar memorizações reais
3. Coletar feedback
4. Planejar Fase 2

---

**Data do Deploy:** ______________

**Responsável:** ______________

**Observações:** 
_________________________________
_________________________________
_________________________________
