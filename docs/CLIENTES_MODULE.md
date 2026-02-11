# Módulo de Gestão de Clientes - Qualicontax

Este módulo implementa um sistema completo de gestão de clientes com CRUD, relacionamentos, filtros avançados e visualização detalhada.

## 🚀 Funcionalidades Implementadas

### 1. Gestão de Clientes (CRUD Completo)
- ✅ Listagem com filtros avançados (situação, regime tributário, tipo de pessoa, busca)
- ✅ Criação de novos clientes (PF e PJ)
- ✅ Visualização detalhada em abas
- ✅ Edição de dados do cliente
- ✅ Inativação de clientes
- ✅ Exclusão de clientes
- ✅ Estatísticas no dashboard (total, ativos, inativos, PF, PJ)

### 2. Gestão de Endereços
- ✅ Múltiplos endereços por cliente
- ✅ Tipos: Comercial, Residencial, Correspondência
- ✅ Marcação de endereço principal
- ✅ Integração com API ViaCEP para busca automática
- ✅ Adicionar e remover endereços

### 3. Gestão de Contatos
- ✅ Múltiplos contatos por cliente
- ✅ Informações: nome, cargo, email, telefone, celular, departamento
- ✅ Marcação de contato principal
- ✅ Status ativo/inativo
- ✅ Adicionar e remover contatos

### 4. Gestão de Grupos
- ✅ Agrupamento de clientes por categorias
- ✅ Visualização de grupos vinculados
- ✅ Gerenciamento de grupos (criar, editar, deletar)

### 5. Relacionamentos
- ✅ Visualização de processos relacionados
- ✅ Visualização de tarefas relacionadas
- ✅ Visualização de obrigações fiscais

### 6. Interface Moderna
- ✅ Cards de estatísticas
- ✅ Sistema de abas na página de detalhes
- ✅ Modals para adicionar endereços e contatos
- ✅ Formulário com campos condicionais (PF/PJ)
- ✅ Máscaras de input (CPF, CNPJ, telefone)
- ✅ Badges de status coloridos
- ✅ Paginação
- ✅ Design responsivo

## 📦 Arquivos Criados/Modificados

### Models (Novos)
- `models/endereco_cliente.py` - CRUD de endereços
- `models/contato_cliente.py` - CRUD de contatos
- `models/grupo_cliente.py` - CRUD de grupos

### Models (Modificados)
- `models/cliente.py` - Métodos adicionados:
  - `get_stats()` - Estatísticas
  - `existe_cpf_cnpj()` - Validação de duplicidade
  - `update_situacao()` - Atualização de status
  - `get_grupos()` - Grupos do cliente
  - `get_processos()` - Processos do cliente
  - `get_tarefas()` - Tarefas do cliente
  - `get_obrigacoes()` - Obrigações do cliente

### Routes
- `routes/clientes.py` - Rotas completas:
  - `/clientes` - Listagem (GET)
  - `/clientes/novo` - Criar (GET/POST)
  - `/clientes/<id>` - Detalhes (GET)
  - `/clientes/<id>/editar` - Editar (GET/POST)
  - `/clientes/<id>/inativar` - Inativar (POST)
  - `/clientes/<id>/deletar` - Deletar (POST)
  - `/clientes/<id>/enderecos/novo` - Novo endereço (POST)
  - `/enderecos/<id>/excluir` - Excluir endereço (POST)
  - `/clientes/<id>/contatos/novo` - Novo contato (POST)
  - `/contatos/<id>/excluir` - Excluir contato (POST)
  - `/api/cep/<cep>` - Buscar CEP (GET)

### Templates
- `templates/clientes/index.html` - Listagem com cards de stats
- `templates/clientes/form.html` - Formulário unificado (criar/editar)
- `templates/clientes/detalhes.html` - Visualização com abas

### Database
- `init_db.py` - Atualizado com novos campos e tabelas
- `migrations/update_clientes_module.sql` - Script de migração

### Outros
- `requirements.txt` - Adicionado `requests` para API de CEP

## 🗄️ Estrutura do Banco de Dados

### Tabela: clientes
```sql
- id (PK)
- tipo_pessoa (PF/PJ)
- nome_razao_social
- nome_fantasia
- cpf_cnpj (UNIQUE)
- inscricao_estadual
- inscricao_municipal
- email
- telefone
- celular
- regime_tributario (SIMPLES, LUCRO_PRESUMIDO, LUCRO_REAL, MEI)
- porte_empresa (MEI, ME, EPP, MEDIO, GRANDE)
- data_inicio_contrato
- data_fim_contrato
- situacao (ATIVO, INATIVO, SUSPENSO, CANCELADO)
- observacoes
- criado_por (FK -> usuarios)
- criado_em
- atualizado_em
```

### Tabela: enderecos_clientes
```sql
- id (PK)
- cliente_id (FK)
- tipo (COMERCIAL, RESIDENCIAL, CORRESPONDENCIA)
- cep, logradouro, numero, complemento
- bairro, cidade, estado, pais
- principal (BOOLEAN)
```

### Tabela: contatos_clientes
```sql
- id (PK)
- cliente_id (FK)
- nome, cargo, email
- telefone, celular
- departamento
- principal (BOOLEAN)
- ativo (BOOLEAN)
```

### Tabela: grupos_clientes
```sql
- id (PK)
- nome
- descricao
- situacao (ATIVO, INATIVO)
```

### Tabela: cliente_grupo_relacao
```sql
- id (PK)
- cliente_id (FK)
- grupo_id (FK)
- UNIQUE(cliente_id, grupo_id)
```

## 🔧 Instalação e Configuração

### 1. Instalar Dependências
```bash
pip install -r requirements.txt
```

### 2. Atualizar Banco de Dados

**Para novo banco:**
```bash
python init_db.py
```

**Para banco existente:**
```sql
mysql -u usuario -p database < migrations/update_clientes_module.sql
```

Ou execute o conteúdo do arquivo SQL diretamente no seu gerenciador MySQL.

### 3. Verificar Configuração

Certifique-se de que as variáveis de ambiente estão configuradas no arquivo `.env`:
```
DB_HOST=localhost
DB_PORT=3306
DB_NAME=qualicontax
DB_USER=root
DB_PASSWORD=sua_senha
SECRET_KEY=sua_chave_secreta
```

### 4. Executar Aplicação
```bash
python app.py
```

## 📋 Como Usar

### Acessar Módulo de Clientes
1. Faça login no sistema
2. Clique em "Cliente" no menu lateral
3. Você verá o dashboard com estatísticas e listagem

### Criar Novo Cliente
1. Clique em "Novo Cliente"
2. Selecione o tipo (PF ou PJ)
3. Preencha os campos obrigatórios
4. Clique em "Cadastrar Cliente"

### Visualizar Detalhes
1. Na listagem, clique no ícone de olho
2. Navegue pelas abas para ver informações específicas
3. Use os botões para adicionar endereços e contatos

### Gerenciar Endereços
1. Na página de detalhes, aba "Endereços"
2. Clique em "Adicionar Endereço"
3. Preencha o CEP (busca automática via ViaCEP)
4. Complete as informações e salve

### Gerenciar Contatos
1. Na página de detalhes, aba "Contatos"
2. Clique em "Adicionar Contato"
3. Preencha as informações do contato
4. Marque como principal se necessário

### Filtrar Clientes
Na página de listagem, use os filtros:
- **Busca:** Nome, CPF/CNPJ ou email
- **Tipo:** Pessoa Física ou Jurídica
- **Situação:** Ativo, Inativo, Suspenso, Cancelado
- **Regime Tributário:** Simples, Lucro Presumido, Lucro Real, MEI

## 🎨 Interface

### Dashboard
- Cards com estatísticas (Total, Ativos, Inativos, PF, PJ)
- Filtros avançados
- Tabela com ações rápidas
- Paginação

### Formulário
- Campos condicionais por tipo de pessoa
- Máscaras automáticas (CPF, CNPJ, telefone)
- Validações de data
- Mensagens de erro claras

### Página de Detalhes
- 7 abas organizadas
- Modals para adicionar dados
- Badges de status coloridos
- Ações rápidas por item

## 🔒 Validações

- CPF/CNPJ único no sistema
- Formato de CPF/CNPJ (frontend e backend)
- Email válido
- Data fim > data início do contrato
- Campos obrigatórios por tipo de pessoa
- CEP válido (8 dígitos)

## 🚦 Status

✅ **Implementação completa e pronta para uso!**

Todos os requisitos do problema statement foram implementados com sucesso.

## 📞 Suporte

Para dúvidas ou problemas, consulte a documentação do código ou abra uma issue no repositório.
