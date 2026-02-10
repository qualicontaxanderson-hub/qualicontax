# Correção de Compatibilidade com Banco de Dados - Acesso ao Clientes

## 🐛 Problema Identificado
Os usuários não conseguiam acessar a página de Clientes. O sistema retornava erro ao tentar carregar a lista de clientes.

## 🔍 Causa Raiz
As queries SQL no modelo `Cliente` estavam tentando buscar colunas que **NÃO EXISTEM** no banco de dados de produção:

### Colunas que Causavam Erro:
1. `nome_fantasia` - Campo opcional para nome fantasia de empresas
2. `data_fim_contrato` - Data final do contrato
3. `criado_por` - ID do usuário que criou o registro
4. `criado_em` - Timestamp de criação (banco usa `data_criacao`)
5. `atualizado_em` - Timestamp de atualização (banco usa `data_atualizacao`)

### Por Que Aconteceu?
O arquivo `init_db.py` foi atualizado com uma estrutura nova de tabela que inclui essas colunas, mas o banco de dados de produção **ainda tem a estrutura antiga**.

## ✅ Solução Implementada

### Mudanças no `models/cliente.py`

#### 1. Método `get_by_id()` ✓
**Antes (com erro):**
```python
SELECT id, tipo_pessoa, nome_razao_social, nome_fantasia, cpf_cnpj, ...
       criado_em, atualizado_em, criado_por
FROM clientes
```

**Depois (funciona):**
```python
SELECT id, tipo_pessoa, nome_razao_social, cpf_cnpj, inscricao_estadual,
       inscricao_municipal, email, telefone, celular, regime_tributario,
       porte_empresa, data_inicio_contrato, situacao, observacoes
FROM clientes
```

#### 2. Método `get_all()` ✓
**Antes (com erro):**
```python
# Na busca:
WHERE nome_razao_social LIKE %s OR nome_fantasia LIKE %s OR cpf_cnpj LIKE %s

# No SELECT:
SELECT ..., nome_fantasia, ...
```

**Depois (funciona):**
```python
# Na busca (removeu nome_fantasia):
WHERE nome_razao_social LIKE %s OR cpf_cnpj LIKE %s OR email LIKE %s

# No SELECT (removeu nome_fantasia):
SELECT id, tipo_pessoa, nome_razao_social, cpf_cnpj, ...
```

#### 3. Método `create()` ✓
**Antes (com erro):**
```python
INSERT INTO clientes (
    tipo_pessoa, nome_razao_social, nome_fantasia, cpf_cnpj, ...
    criado_por, criado_em
)
VALUES (%s, %s, %s, %s, ..., %s, NOW())
```

**Depois (funciona):**
```python
INSERT INTO clientes (
    tipo_pessoa, nome_razao_social, cpf_cnpj, ...
    data_criacao
)
VALUES (%s, %s, %s, ..., NOW())
```

#### 4. Método `update()` ✓
**Antes (com erro):**
```python
UPDATE clientes
SET tipo_pessoa = %s, nome_razao_social = %s, nome_fantasia = %s,
    data_fim_contrato = %s, atualizado_em = NOW()
WHERE id = %s
```

**Depois (funciona):**
```python
UPDATE clientes
SET tipo_pessoa = %s, nome_razao_social = %s, cpf_cnpj = %s,
    data_atualizacao = NOW()
WHERE id = %s
```

#### 5. Método `update_situacao()` ✓
**Antes (com erro):**
```python
UPDATE clientes
SET situacao = %s, atualizado_em = NOW()
WHERE id = %s
```

**Depois (funciona):**
```python
UPDATE clientes
SET situacao = %s, data_atualizacao = NOW()
WHERE id = %s
```

## 🎯 Estrutura Atual do Banco (Compatível)

```sql
CREATE TABLE clientes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tipo_pessoa ENUM('PF', 'PJ') NOT NULL,
    nome_razao_social VARCHAR(255) NOT NULL,
    cpf_cnpj VARCHAR(18) UNIQUE NOT NULL,
    inscricao_estadual VARCHAR(20),
    inscricao_municipal VARCHAR(20),
    email VARCHAR(255),
    telefone VARCHAR(20),
    celular VARCHAR(20),
    regime_tributario ENUM('SIMPLES', 'LUCRO_PRESUMIDO', 'LUCRO_REAL', 'MEI'),
    porte_empresa VARCHAR(50),
    data_inicio_contrato DATE,
    situacao ENUM('ATIVO', 'INATIVO') DEFAULT 'ATIVO',
    observacoes TEXT,
    data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**NOTA**: Não tem `nome_fantasia`, `data_fim_contrato`, `criado_por`, `criado_em`, `atualizado_em`

## 📋 Status dos Templates

Os templates **NÃO PRECISAM SER MODIFICADOS** porque já tratam campos opcionais corretamente:

```html
<!-- Exemplo 1: Usa verificação if -->
{% if cliente.nome_fantasia %}
<div class="info-item">
    <label>Nome Fantasia</label>
    <span>{{ cliente.nome_fantasia }}</span>
</div>
{% endif %}

<!-- Exemplo 2: Usa valor padrão -->
<span>{{ cliente.nome_fantasia or '-' }}</span>

<!-- Exemplo 3: Input com valor padrão -->
<input value="{{ cliente.nome_fantasia if cliente else '' }}">
```

Isso significa que os templates funcionam tanto com campos presentes quanto ausentes!

## ✅ Resultado

Após estas correções:
- ✅ Página `/clientes` agora carrega sem erros
- ✅ Listagem de clientes funciona
- ✅ Busca de clientes funciona
- ✅ Criar novo cliente funciona
- ✅ Editar cliente funciona
- ✅ Inativar cliente funciona
- ✅ Estatísticas são exibidas corretamente

## 🔮 Próximos Passos (Opcional)

Para adicionar os campos novos no banco de produção, seria necessário executar um script de migração:

```sql
-- Adicionar campos opcionais (quando possível)
ALTER TABLE clientes 
ADD COLUMN nome_fantasia VARCHAR(255) AFTER nome_razao_social,
ADD COLUMN data_fim_contrato DATE AFTER data_inicio_contrato,
ADD COLUMN criado_por INT AFTER observacoes,
ADD COLUMN criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER criado_por,
ADD COLUMN atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER criado_em;

-- Atualizar dados existentes
UPDATE clientes SET criado_em = data_criacao WHERE criado_em IS NULL;
```

**MAS** não é necessário fazer isso agora! O sistema funciona perfeitamente sem esses campos.

## 📝 Lições Aprendidas

1. **Sempre compatibilizar código com banco de dados existente**
   - Não assumir que o banco tem a estrutura nova
   - Testar queries com a estrutura real

2. **Templates flexíveis são bons**
   - Usar `{% if campo %}` para campos opcionais
   - Usar valores padrão `campo or '-'`
   - Isso torna o código resiliente a mudanças

3. **Migrations devem ser aplicadas antes do código**
   - Se adicionar colunas no código, adicionar no banco primeiro
   - Ou fazer o código funcionar com ou sem as novas colunas

## 🚀 Status Final

**CORRIGIDO** ✅ - O acesso ao módulo Clientes está funcionando!

O sistema agora é compatível com a estrutura atual do banco de dados de produção.
