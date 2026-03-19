# Módulo de Conciliação Bancária - Guia Completo

## 📋 Visão Geral

O módulo de Conciliação Bancária permite importar extratos bancários (formato OFX), classificar transações automaticamente usando memorizações (regras), e exportar para sistemas contábeis.

### Diferenciais

- ✅ **Memorizações por GRUPO**: Regras compartilhadas entre clientes do mesmo grupo
- ✅ **Memorizações INDIVIDUAIS**: Regras específicas de cada cliente
- ✅ **Sistema de Prioridade**: Individual tem prioridade sobre Grupo
- ✅ **Importação OFX**: Padrão universal de extratos bancários
- ✅ **Exportação Multi-formato**: Domínio, Alterdata, Sage, CSV, etc.

---

## 🗂️ Estrutura de Dados

### Tabelas Criadas

#### 1. conciliacoes_bancarias
Armazena cada importação de extrato OFX.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INT | ID único |
| cliente_id | INT | Cliente da conciliação |
| grupo_id | INT | Grupo (opcional) |
| arquivo_ofx | VARCHAR | Nome do arquivo importado |
| data_importacao | DATETIME | Data/hora da importação |
| periodo_inicial | DATE | Início do período |
| periodo_final | DATE | Fim do período |
| saldo_inicial | DECIMAL | Saldo inicial |
| saldo_final | DECIMAL | Saldo final |
| status | VARCHAR | PENDENTE, CONCILIADA, EXPORTADA |

#### 2. transacoes_bancarias
Armazena as transações de cada conciliação.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INT | ID único |
| conciliacao_id | INT | ID da conciliação |
| data_transacao | DATE | Data da transação |
| descricao | VARCHAR | Descrição da transação |
| valor | DECIMAL | Valor (+ ou -) |
| tipo | VARCHAR | CREDITO ou DEBITO |
| categoria_contabil | VARCHAR | Categoria classificada |
| conta_contabil | VARCHAR | Conta contábil |
| historico_padrao | VARCHAR | Histórico para sistema contábil |
| status | VARCHAR | PENDENTE, CLASSIFICADA, EXPORTADA |

#### 3. memorizacoes_conciliacao
Regras de classificação automática.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INT | ID único |
| tipo | VARCHAR | GRUPO ou INDIVIDUAL |
| grupo_id | INT | ID do grupo (se tipo GRUPO) |
| cliente_id | INT | ID do cliente (se tipo INDIVIDUAL) |
| palavra_chave | VARCHAR | Palavra para buscar na descrição |
| categoria_contabil | VARCHAR | Categoria a aplicar |
| conta_contabil | VARCHAR | Conta contábil a aplicar |
| historico_padrao | VARCHAR | Histórico padrão |
| ativo | BOOLEAN | Se está ativo |

#### 4. exportacoes_contabeis
Registra exportações realizadas.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| id | INT | ID único |
| conciliacao_id | INT | ID da conciliação exportada |
| data_exportacao | DATETIME | Data/hora da exportação |
| formato | VARCHAR | Formato do arquivo |
| arquivo_exportado | VARCHAR | Nome do arquivo gerado |
| total_registros | INT | Quantidade de registros |

---

## 🎯 Como Funciona

### Sistema de Memorizações

#### Memorizações de GRUPO

São regras que se aplicam a **todos os clientes do grupo**.

**Exemplo - Grupo "Postos de Combustível":**

| Palavra-chave | Categoria | Conta | Histórico |
|---------------|-----------|-------|-----------|
| COMBUSTIVEL | Receita de Vendas | 3.1.01.001 | Venda de combustível |
| IPIRANGA | Fornecedor | 2.1.01.010 | Compra Ipiranga |
| ENERGIA | Despesas Operacionais | 3.1.02.001 | Conta de energia |

Todos os postos do grupo usarão essas mesmas regras.

#### Memorizações INDIVIDUAIS

São regras específicas de **um cliente**.

**Exemplo - Cliente "Posto Novo Horizonte Goiatuba":**

| Palavra-chave | Categoria | Conta | Histórico |
|---------------|-----------|-------|-----------|
| ALUGUEL MATRIZ | Despesa com Aluguel | 3.1.02.005 | Aluguel matriz Goiatuba |
| FUNCIONARIO JOÃO | Folha de Pagamento | 3.2.01.010 | Salário João Silva |
| IPVA CAMINHAO | Impostos Veículos | 3.3.01.020 | IPVA caminhão ABC-1234 |

Essas regras só valem para este cliente específico.

### Sistema de Prioridade

Quando uma transação é processada:

```
1. Busca nas Memorizações INDIVIDUAIS do cliente
   ↓
   ✅ Encontrou? → USA ESSA
   ❌ Não encontrou? → Continua
   ↓
2. Busca nas Memorizações do GRUPO (se cliente está em grupo)
   ↓
   ✅ Encontrou? → USA ESSA
   ❌ Não encontrou? → Classificação manual necessária
```

**Exemplo Prático:**

```
Transação: "PAGAMENTO FUNCIONARIO JOÃO - R$ 2.500,00"

Cliente: Posto Novo Horizonte (pertence ao grupo "Postos de Combustível")

Busca:
1º. Memorizações INDIVIDUAIS do Posto Novo Horizonte
    ✅ ENCONTROU: "FUNCIONARIO JOÃO"
    → Categoria: Folha de Pagamento
    → Conta: 3.2.01.010
    → Histórico: Salário João Silva

Resultado: Classificada automaticamente!
```

```
Transação: "COMPRA IPIRANGA COMBUSTIVEL - R$ 15.000,00"

Cliente: Posto Novo Horizonte (pertence ao grupo "Postos de Combustível")

Busca:
1º. Memorizações INDIVIDUAIS do Posto Novo Horizonte
    ❌ NÃO ENCONTROU "IPIRANGA"
    
2º. Memorizações do GRUPO "Postos de Combustível"
    ✅ ENCONTROU: "IPIRANGA"
    → Categoria: Fornecedor
    → Conta: 2.1.01.010
    → Histórico: Compra Ipiranga

Resultado: Classificada automaticamente!
```

---

## 📥 Importação OFX

### O que é OFX?

OFX (Open Financial Exchange) é um formato padrão de arquivo usado por bancos para exportar extratos e movimentações.

### Como Obter

1. Acesse o Internet Banking
2. Vá em "Extratos" ou "Movimentações"
3. Selecione o período
4. Escolha formato "OFX" ou "Money"
5. Faça o download

### Bancos que Suportam

- ✅ Banco do Brasil
- ✅ Caixa Econômica
- ✅ Itaú
- ✅ Bradesco
- ✅ Santander
- ✅ Sicoob
- ✅ Sicredi
- ✅ Banrisul
- ✅ E a maioria dos bancos

### Fluxo de Importação

```
1. Usuário seleciona cliente
   ↓
2. Faz upload do arquivo .ofx
   ↓
3. Escolhe se quer usar memorizações do grupo
   ↓
4. Sistema processa arquivo
   ↓
5. Extrai transações
   ↓
6. Aplica memorizações automaticamente
   ↓
7. Apresenta para revisão
```

---

## 🔄 Fluxo Completo de Trabalho

### Passo 1: Configurar Memorizações

#### Criar Memorizações de Grupo

1. Ir em **Contábil** → **Memorizações**
2. Clicar **Nova Memorização**
3. Selecionar **Tipo: GRUPO**
4. Escolher o grupo (ex: "Postos de Combustível")
5. Preencher:
   - Palavra-chave: "ENERGIA"
   - Categoria: "Despesas Operacionais"
   - Conta: "3.1.02.001"
   - Histórico: "Conta de energia elétrica"
6. **Salvar**

#### Criar Memorizações Individuais

1. Ir em **Contábil** → **Memorizações**
2. Clicar **Nova Memorização**
3. Selecionar **Tipo: INDIVIDUAL**
4. Escolher o cliente
5. Preencher dados específicos
6. **Salvar**

### Passo 2: Importar Extrato OFX

1. Ir em **Contábil** → **Conciliações Bancárias**
2. Clicar **Nova Conciliação**
3. Selecionar **Cliente**
4. Fazer **Upload do arquivo .ofx**
5. ☑️ Marcar "Usar memorizações do grupo" (se quiser)
6. Clicar **Importar e Processar**

### Passo 3: Revisar Classificações

1. Sistema apresenta transações
2. Transações já classificadas aparecem em verde
3. Transações sem classificação em amarelo
4. Usuário pode:
   - ✅ Aceitar classificação automática
   - ✏️ Ajustar manualmente
   - 💾 Criar nova memorização a partir da transação

### Passo 4: Exportar

1. Revisar todas as transações
2. Confirmar classificações
3. Escolher formato de exportação
4. Gerar arquivo para sistema contábil

---

## 💻 Rotas Disponíveis

### Visualização

| Rota | Método | Descrição |
|------|--------|-----------|
| `/contabil/` | GET | Página principal do módulo |
| `/contabil/conciliacoes` | GET | Lista de conciliações |
| `/contabil/conciliacoes/<id>` | GET | Detalhes de uma conciliação |
| `/contabil/nova_conciliacao` | GET | Formulário de importação |
| `/contabil/memorizacoes` | GET | Lista de memorizações |

### Ações

| Rota | Método | Descrição |
|------|--------|-----------|
| `/contabil/nova_conciliacao` | POST | Processa upload OFX |
| `/contabil/memorizacoes/nova` | POST | Cria memorização |
| `/contabil/memorizacoes/<id>/editar` | POST | Atualiza memorização |
| `/contabil/memorizacoes/<id>/excluir` | POST | Remove memorização |

### API

| Rota | Método | Descrição |
|------|--------|-----------|
| `/contabil/api/cliente/<id>/grupos` | GET | Grupos do cliente |

---

## 🎨 Interface

### Dashboard Principal

Ao acessar **Contábil** → **Início**, o usuário vê:

- **Card Conciliações Bancárias**
  - Descrição do recurso
  - Botão "Ver Conciliações"
  - Botão "Nova Conciliação"

- **Card Memorizações**
  - Descrição do recurso
  - Botão "Ver Memorizações"
  - Botão "Nova Memorização"

- **Informações do Sistema**
  - Importação OFX
  - Memorizações por Grupo
  - Memorizações Individuais
  - Exportação Contábil

- **Fluxo de Trabalho**
  - Passo 1: Importar Extrato
  - Passo 2: Escolher Memorizações
  - Passo 3: Classificar
  - Passo 4: Exportar

### Lista de Conciliações

Filtros disponíveis:
- Cliente
- Grupo
- Status (Pendente, Conciliada, Exportada)

Colunas exibidas:
- ID
- Cliente
- Grupo
- Período
- Saldo Inicial/Final
- Status
- Data Importação
- Ações

### Formulário de Importação

- Seleção de cliente
- Upload de arquivo .ofx (drag & drop)
- Checkbox "Usar memorizações do grupo"
- Instruções de como obter OFX

---

## 📊 Exemplos de Uso

### Caso 1: Escritório Contábil com Postos

**Cenário:**
- Escritório tem 15 postos de combustível
- Todos têm movimentações similares
- Alguns têm particularidades

**Solução:**

1. Criar grupo "Postos de Combustível"
2. Adicionar os 15 postos ao grupo
3. Criar memorizações de GRUPO:
   - "COMBUSTIVEL" → Receita
   - "ENERGIA" → Despesa
   - "IPIRANGA" → Fornecedor
   - etc.

4. Para postos com particularidades, criar memorizações INDIVIDUAIS:
   - Posto A: "FUNCIONARIO MARIA" → Folha
   - Posto B: "ALUGUEL FILIAL 2" → Despesa Aluguel

5. Ao importar OFX:
   - Marcar "Usar memorizações do grupo"
   - 80% das transações classificadas automaticamente
   - Apenas particularidades precisam revisão

### Caso 2: Cliente Único Complexo

**Cenário:**
- Cliente grande com muitos tipos de transação
- Não faz parte de nenhum grupo

**Solução:**

1. Criar muitas memorizações INDIVIDUAIS
2. Ao longo do tempo, criar novas regras
3. Sistema aprende os padrões
4. Classificação cada vez mais automática

---

## 🔜 Próximas Fases

### Fase 2: Parser OFX (A implementar)

**Funcionalidades:**
- Leitura de arquivos OFX
- Extração de transações
- Identificação de banco/agência/conta
- Validação de dados
- Salvamento no banco

**Bibliotecas sugeridas:**
- `ofxparse` (Python)
- `beautifulsoup4` (parsing XML)

### Fase 3: Auto-Classificação (A implementar)

**Funcionalidades:**
- Aplicação automática de memorizações
- Interface de revisão rápida
- Criação rápida de memorizações
- Sugestões de classificação
- Estatísticas de acerto

### Fase 4: Exportação (A implementar)

**Formatos a suportar:**
- Domínio Sistemas
- Alterdata
- Sage
- CSV genérico
- Outros sob demanda

---

## 🛠️ Instalação e Configuração

### 1. Executar Migration

```bash
mysql -u usuario -p database_name < migrations/008_create_conciliacao_bancaria.sql
```

### 2. Verificar Permissões

Certifique-se de que o diretório de uploads existe e tem permissões:

```bash
mkdir -p static/uploads/ofx
chmod 755 static/uploads/ofx
```

### 3. Acessar o Módulo

1. Login no sistema
2. Menu lateral → **Contábil**
3. Explorar funcionalidades

---

## ❓ FAQ

**P: Posso ter memorizações de grupo E individuais para o mesmo cliente?**

R: Sim! As individuais têm prioridade. Se não encontrar individual, usa a do grupo.

**P: O que acontece se nenhuma memorização corresponder?**

R: A transação fica como "PENDENTE" e precisa ser classificada manualmente.

**P: Posso desativar uma memorização sem excluir?**

R: Sim! Há um campo "ativo" que pode ser desmarcado.

**P: Como funciona a busca por palavra-chave?**

R: O sistema procura a palavra-chave em qualquer parte da descrição da transação (case-insensitive).

**P: Posso importar múltiplos OFX do mesmo cliente?**

R: Sim! Cada importação cria uma conciliação separada.

**P: As exportações ficam registradas?**

R: Sim! Há uma tabela que guarda histórico de todas as exportações.

---

## 🎉 Conclusão

O módulo de Conciliação Bancária está pronto para uso em sua estrutura base!

**Principais benefícios:**
- ✅ Automação de classificação contábil
- ✅ Redução de trabalho manual
- ✅ Padronização entre clientes do mesmo grupo
- ✅ Flexibilidade para casos específicos
- ✅ Rastreabilidade completa

**Próximos passos:**
1. Executar migration
2. Criar memorizações de teste
3. Importar OFX de teste
4. Aguardar implementação do parser (Fase 2)

---

**Desenvolvido com ❤️ para Qualicontax**
