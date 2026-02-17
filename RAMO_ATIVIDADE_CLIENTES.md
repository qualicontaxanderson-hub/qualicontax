# 🏭 Ramo de Atividade para Clientes

## ✅ IMPLEMENTADO COM SUCESSO!

Implementada funcionalidade completa de **Ramo de Atividade** para clientes, permitindo organizar os clientes por setor/atividade econômica.

## 📋 Solicitação Original

> "Na aba Cadastro temos que incluir um campo Ramo de Atividade, tenho clientes com diversas atividades como Posto de Gasolina, Distribuidora, Transportadoras, Lava Rápido e nos dados Gerais tem que aparecer para aparecer quando estivermos cadastrando..."
> 
> "Na na Aba https://app.qualicontax.com.br/ramodeatividade ficar igual https://app.qualicontax.com.br/grupos aparecendo quantos clientes estão vinculados a aquela atividade..."

## 🎯 Solução Implementada

Sistema completo de gestão de Ramos de Atividade com:

1. **Página de gerenciamento** (`/ramodeatividade`)
2. **Campo no cadastro** de cliente
3. **Exibição na página** de detalhes
4. **Contador de clientes** por ramo

## 📸 Funcionalidades

### Menu Atualizado
```
Cadastros ▼
  ├─ Clientes
  ├─ Grupos
  ├─ Ramo de Atividade    ← NOVO! ✨
  └─ Contratos
```

### Ramos Pré-cadastrados
- Posto de Gasolina
- Distribuidora
- Transportadoras
- Lava Rápido
- Comércio
- Indústria
- Serviços
- Tecnologia
- Consultoria
- Construção Civil

## 🔧 Estrutura Implementada

### 1. Banco de Dados

**Tabela `ramos_atividade`:**
```sql
CREATE TABLE ramos_atividade (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nome VARCHAR(255) NOT NULL,
    descricao TEXT,
    situacao ENUM('ATIVO', 'INATIVO') DEFAULT 'ATIVO',
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

**Tabela `cliente_ramo_atividade_relacao`:**
```sql
CREATE TABLE cliente_ramo_atividade_relacao (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id INT NOT NULL,
    ramo_atividade_id INT NOT NULL,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
    FOREIGN KEY (ramo_atividade_id) REFERENCES ramos_atividade(id) ON DELETE CASCADE,
    UNIQUE KEY unique_cliente_ramo (cliente_id, ramo_atividade_id)
)
```

### 2. Modelo (models/ramo_atividade.py)

**Métodos CRUD:**
- `get_all(situacao=None)` - Lista todos os ramos
- `get_by_id(ramo_id)` - Busca ramo por ID
- `create(nome, descricao, situacao)` - Cria novo ramo
- `update(ramo_id, nome, descricao, situacao)` - Atualiza ramo
- `delete(ramo_id)` - Remove ramo

**Métodos de Relacionamento:**
- `add_cliente(ramo_id, cliente_id)` - Adiciona cliente ao ramo
- `remove_cliente(ramo_id, cliente_id)` - Remove cliente do ramo
- `get_clientes(ramo_id)` - Lista clientes do ramo
- `get_by_cliente(cliente_id)` - Lista ramos do cliente

### 3. Rotas (routes/ramos_atividade.py)

**Rotas implementadas:**
```
GET  /ramodeatividade                              # Listar ramos
GET  /ramodeatividade/novo                         # Formulário novo
POST /ramodeatividade/novo                         # Criar ramo
GET  /ramodeatividade/<id>                         # Ver detalhes
GET  /ramodeatividade/<id>/editar                  # Formulário editar
POST /ramodeatividade/<id>/editar                  # Atualizar ramo
POST /ramodeatividade/<id>/deletar                 # Deletar ramo
POST /ramodeatividade/<id>/adicionar-cliente       # Adicionar cliente
POST /ramodeatividade/<id>/remover-cliente/<cid>   # Remover cliente
```

### 4. Templates

**index.html** - Listagem de ramos:
- Filtro por situação
- Tabela com: Nome, Descrição, **Total de Clientes** ⭐, Situação, Ações
- Ícone: 🏭 fas fa-industry

**form.html** - Formulário criar/editar:
- Nome (obrigatório)
- Descrição (opcional)
- Situação (Ativo/Inativo)

**detalhes.html** - Ver ramo e clientes:
- Cards estatísticos (total de clientes, situação)
- Formulário para adicionar clientes
- Tabela de clientes vinculados
- Botão para remover clientes

### 5. Integração com Cliente

**Formulário de Cliente:**
```html
<div class="form-group">
    <label for="ramo_atividade_id">Ramo de Atividade</label>
    <select id="ramo_atividade_id" name="ramo_atividade_id">
        <option value="">Selecione...</option>
        <option value="1">Posto de Gasolina</option>
        <option value="2">Distribuidora</option>
        <!-- ... -->
    </select>
    <small>Ex: Posto de Gasolina, Distribuidora, Transportadoras...</small>
</div>
```

**Detalhes do Cliente:**
```html
<div class="info-item">
    <label>Ramo de Atividade</label>
    <span>
        <span class="badge badge-secondary">Posto de Gasolina</span>
    </span>
</div>
```

## 💡 Como Usar

### 1. Gerenciar Ramos de Atividade

#### Acessar Página de Ramos
1. Menu lateral → **"Cadastros"**
2. Clique em **"Ramo de Atividade"**
3. Você verá a lista de ramos

#### Criar Novo Ramo
1. Na página de ramos, clique em **"Novo Ramo"**
2. Preencha:
   - **Nome**: Ex: "Atacado"
   - **Descrição**: Ex: "Comércio atacadista"
   - **Situação**: Ativo
3. Clique em **"Salvar Ramo"**

#### Editar Ramo
1. Na listagem, clique no ícone de **editar** (✏️)
2. Altere os dados
3. Salve

#### Ver Clientes do Ramo
1. Na listagem, clique no ícone de **visualizar** (👁️)
2. Veja:
   - Total de clientes
   - Lista completa de clientes
   - Opção de adicionar mais clientes
   - Opção de remover clientes

### 2. Cadastrar Cliente com Ramo

#### Novo Cliente
1. **"Cadastros"** → **"Clientes"** → **"Novo Cliente"**
2. Preencha dados básicos (Nome, CPF/CNPJ, etc.)
3. Na seção **"Dados da Empresa"**, selecione:
   - **Ramo de Atividade**: "Posto de Gasolina"
4. Clique em **"Salvar Cliente"**
5. ✅ Cliente vinculado ao ramo!

#### Editar Cliente
1. Edite cliente existente
2. Altere o campo **"Ramo de Atividade"**
3. Salve
4. ✅ Associação atualizada!

### 3. Visualizar Ramo do Cliente

1. Abra **detalhes do cliente**
2. Na seção **"Informações Cadastrais"**
3. Veja o campo **"Ramo de Atividade"** com badge

## 📊 Exemplo de Uso

### Cenário: Posto de Gasolina ABC

**1. Criar Ramo "Posto de Gasolina"** (se não existir)
- Nome: Posto de Gasolina
- Descrição: Postos de combustíveis e conveniências
- Situação: Ativo

**2. Cadastrar Cliente ABC LTDA**
- Nome: ABC LTDA
- CNPJ: 12.345.678/0001-99
- Ramo: **Posto de Gasolina** ⛽

**3. Ver no Ramo**
- Acesse /ramodeatividade
- Clique em "Posto de Gasolina"
- Veja: **Total: 1 cliente** (ABC LTDA)

**4. Ver no Cliente**
- Acesse detalhes de ABC LTDA
- Veja: **Ramo: Posto de Gasolina** 🏭

## 🔄 Diferença entre Grupos e Ramos

### Grupos
- **Propósito**: Segmentação livre (VIP, Campanhas, etc.)
- **Relação**: N:N (cliente pode ter vários grupos)
- **Uso**: Marketing, organização interna

### Ramos de Atividade
- **Propósito**: Classificação por setor econômico
- **Relação**: 1:N (cliente tem apenas 1 ramo)
- **Uso**: Contabilidade, relatórios fiscais, análises setoriais

## 📈 Benefícios

1. **Organização Setorial**: Clientes agrupados por atividade
2. **Relatórios**: Análises por setor econômico
3. **Contabilidade**: Facilita tratamento fiscal específico
4. **Gestão**: Visão clara da carteira de clientes
5. **Busca**: Encontrar clientes por ramo

## 🔐 Validações

- ✅ Nome do ramo é obrigatório
- ✅ Cliente não pode estar duas vezes no mesmo ramo
- ✅ Ramo pode ser deletado (clientes perdem associação)
- ✅ Cliente sem ramo é permitido (campo opcional)
- ✅ Ao editar cliente, troca de ramo é automática

## 📦 Arquivos Criados

1. ✅ `models/ramo_atividade.py` (5.6 KB)
2. ✅ `routes/ramos_atividade.py` (7.2 KB)
3. ✅ `templates/ramos_atividade/index.html` (5.4 KB)
4. ✅ `templates/ramos_atividade/form.html` (3.0 KB)
5. ✅ `templates/ramos_atividade/detalhes.html` (7.1 KB)
6. ✅ `migrations/add_ramo_atividade.py` (3.4 KB)

## 📝 Arquivos Modificados

1. ✅ `init_db.py` - Tabelas adicionadas
2. ✅ `app.py` - Blueprint registrado
3. ✅ `templates/base.html` - Link no menu
4. ✅ `routes/clientes.py` - Integração completa
5. ✅ `templates/clientes/form.html` - Campo adicionado
6. ✅ `templates/clientes/detalhes.html` - Exibição adicionada

## 🚀 Migração

Para aplicar as mudanças no banco de dados:

```bash
cd /home/runner/work/qualicontax/qualicontax
python migrations/add_ramo_atividade.py
```

Isso irá:
1. ✅ Criar tabela `ramos_atividade`
2. ✅ Criar tabela `cliente_ramo_atividade_relacao`
3. ✅ Inserir 10 ramos padrão

## ✅ Status

**TOTALMENTE IMPLEMENTADO E FUNCIONAL!**

Agora você pode:
- ✅ Gerenciar ramos de atividade (/ramodeatividade)
- ✅ Ver quantos clientes tem em cada ramo
- ✅ Cadastrar cliente com ramo de atividade
- ✅ Editar o ramo do cliente
- ✅ Ver o ramo na página de detalhes
- ✅ Adicionar/remover clientes de ramos
- ✅ Usar os ramos para organização e relatórios

## 🎊 Resultado Final

A solicitação foi **100% atendida**:

✅ **"incluir um campo Ramo de Atividade"** → Implementado no formulário  
✅ **"Posto de Gasolina, Distribuidora, etc"** → Ramos pré-cadastrados  
✅ **"nos dados Gerais tem que aparecer"** → Exibe nos detalhes  
✅ **"ficar igual /grupos"** → Página idêntica com contador de clientes  
✅ **"aparecendo quantos clientes"** → Badge mostra total de clientes  

---

**Data de Implementação:** 12/02/2026  
**Status:** ✅ Implementado e Testado  
**Versão:** copilot/replace-old-sidebar-menu  
**Tipo de Mudança:** Nova Funcionalidade
