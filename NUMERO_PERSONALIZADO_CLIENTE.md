# 🔢 Campo de Número Personalizado do Cliente

## ✅ IMPLEMENTADO COM SUCESSO!

Esta funcionalidade permite que você **defina manualmente um número personalizado** para cada cliente, mantendo consistência com seus cadastros atuais.

## 📋 Problema Original

> "eu não pedi uma coluna de ID eu pedi que eu possa digitar o numero da empresa para ficar igual aos projetos que eu tenho hoje, exemplo vou cadastro o Cliente ACB Ltda, esse cliente é numero 102 nos meus cadastros hoje então quero que no meu app também eu consiga cadastrar como 102 ou então alterar para outro numero se necessário."

## 🎯 Solução Implementada

Foi adicionado um campo **"Número do Cliente"** que permite:
- ✅ Digitar manualmente o número que você quiser (ex: 102)
- ✅ Manter o mesmo número dos seus cadastros atuais
- ✅ Alterar o número quando necessário
- ✅ Deixar em branco se preferir usar apenas o ID automático

## 📸 Screenshots da Funcionalidade

### 1. Formulário de Cadastro
![Formulário com campo de Número do Cliente](https://github.com/user-attachments/assets/e1e9ee90-1af5-4409-8f79-82958b14e7a2)

**Características do Campo:**
- 🎨 Destaque visual em verde
- 📝 Placeholder explicativo: "Digite o número do cliente (ex: 102)"
- ℹ️ Texto de ajuda completo
- ⭐ Opcional - não é obrigatório preencher

### 2. Listagem de Clientes
![Listagem mostrando números personalizados](https://github.com/user-attachments/assets/80a07a96-c326-4e3b-a869-ddf32da2268f)

**Como Aparece na Listagem:**
- 🟢 **Números Personalizados**: #102, #205, #450 (em verde e destaque)
- ⚪ **IDs Automáticos**: Auto: 1003, Auto: 1005 (em cinza)

## 🔧 Mudanças Técnicas Implementadas

### 1. Banco de Dados
```sql
ALTER TABLE clientes 
ADD COLUMN numero_cliente VARCHAR(20) UNIQUE 
AFTER id
```

- **Campo**: `numero_cliente`
- **Tipo**: VARCHAR(20)
- **Restrição**: UNIQUE (não permite duplicatas)
- **Opcional**: Pode ser NULL

### 2. Modelo (models/cliente.py)
- ✅ Campo incluído em todas as queries SELECT
- ✅ Método `existe_numero_cliente()` para validar duplicidade
- ✅ Campo adicionado nos métodos `create()` e `update()`
- ✅ Busca habilitada por número do cliente

### 3. Rotas (routes/clientes.py)
- ✅ Processamento do campo no POST (criar cliente)
- ✅ Processamento do campo no POST (editar cliente)
- ✅ Validação de unicidade ao criar
- ✅ Validação de unicidade ao editar (excluindo o próprio cliente)
- ✅ Mensagens de erro amigáveis

### 4. Formulário (templates/clientes/form.html)
- ✅ Campo em destaque no topo do formulário
- ✅ Texto explicativo claro
- ✅ Validação maxlength=20
- ✅ Opcional (não obrigatório)

### 5. Listagem (templates/clientes/index.html)
- ✅ Coluna renomeada para "Nº Cliente"
- ✅ Exibe número personalizado em verde se definido
- ✅ Exibe "Auto: id" em cinza se não definido
- ✅ Destaque visual para diferenciar

## 💡 Como Usar

### Cadastrar Cliente com Número Personalizado

1. Acesse **Clientes > Novo Cliente**
2. No campo **"Número do Cliente"**, digite o número desejado
   - Exemplo: `102` para manter o mesmo da sua empresa ABC Ltda
3. Preencha os demais campos normalmente
4. Clique em **"Salvar Cliente"**

### Editar Número de Cliente Existente

1. Na listagem de clientes, clique em **"Editar"**
2. Modifique o campo **"Número do Cliente"**
3. Clique em **"Salvar"**

### Deixar Sem Número Personalizado

Se você deixar o campo em branco, o sistema usará o ID automático do banco de dados.

## 📊 Exemplos Práticos

### Exemplo 1: Cliente com Número Personalizado
```
Número do Cliente: 102
Nome: ABC LTDA
CNPJ: 12.345.678/0001-90

Aparece na listagem como: #102 (verde, em destaque)
```

### Exemplo 2: Cliente sem Número Personalizado
```
Número do Cliente: (deixado em branco)
Nome: JOÃO DA SILVA
CPF: 123.456.789-00

Aparece na listagem como: Auto: 1003 (cinza)
```

## 🎨 Diferenciação Visual

### Números Personalizados
- **Cor**: Verde (#22C55E)
- **Formato**: #102, #205, #450
- **Destaque**: Negrito, tamanho maior
- **Ícone**: ⭐ Estrela
- **Fundo**: Verde claro nas linhas

### IDs Automáticos
- **Cor**: Cinza (#9CA3AF)
- **Formato**: Auto: 1003, Auto: 1005
- **Tamanho**: Menor, discreto
- **Ícone**: 🤖 Robô
- **Fundo**: Branco normal

## ⚠️ Validações

### Unicidade
O sistema não permite que dois clientes tenham o mesmo número personalizado:
```
Erro: Número do cliente "102" já está em uso!
```

### Ao Editar
Ao editar um cliente, o sistema permite manter o número atual ou alterá-lo para outro número disponível.

### Busca
Você pode buscar clientes pelo número personalizado na barra de busca.

## 🗄️ Migração de Dados

Para bancos de dados existentes, execute o script de migração:

```bash
cd /home/runner/work/qualicontax/qualicontax
python -c "import sys; sys.path.insert(0, '.'); from migrations.add_numero_cliente import migrate_add_numero_cliente; migrate_add_numero_cliente()"
```

Isso adiciona a coluna `numero_cliente` à tabela `clientes` sem afetar os dados existentes.

## 📝 Campos no Banco de Dados

### Tabela: `clientes`
```sql
CREATE TABLE clientes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    numero_cliente VARCHAR(20) UNIQUE,  -- ← NOVO CAMPO
    tipo_pessoa ENUM('PF', 'PJ') NOT NULL,
    nome_razao_social VARCHAR(255) NOT NULL,
    -- ... outros campos ...
);
```

## 🎯 Benefícios da Funcionalidade

1. **Consistência**: Mantenha os mesmos números dos seus cadastros atuais
2. **Flexibilidade**: Escolha quando usar número personalizado ou ID automático
3. **Controle**: Você decide qual número cada cliente terá
4. **Editável**: Pode alterar o número depois se necessário
5. **Único**: Sistema garante que não haverá duplicatas
6. **Busca**: Pode buscar clientes pelo número personalizado

## ✅ Checklist de Funcionalidades

- [x] Campo opcional no formulário
- [x] Validação de unicidade
- [x] Exibição na listagem com destaque visual
- [x] Busca por número do cliente
- [x] Edição de número existente
- [x] Migração para bancos existentes
- [x] Mensagens de erro claras
- [x] Documentação completa
- [x] Screenshots da interface

## 🚀 Próximos Passos

Para usar a funcionalidade:

1. ✅ Execute a migração no banco de dados (se já tiver dados)
2. ✅ Acesse o formulário de novo cliente
3. ✅ Digite o número personalizado desejado
4. ✅ Salve e veja o resultado na listagem

## 💬 Comunicação com Clientes

Agora você pode se referir aos clientes pelo número personalizado:

- "Verificar o cliente **#102**"
- "Contrato do cliente **#205** precisa ser renovado"
- "Cliente **#450** solicitou alteração"

Isso facilita a comunicação e mantém consistência com seus sistemas atuais!

---

**Data de Implementação:** 12/02/2026  
**Status:** ✅ Implementado e Testado  
**Versão:** copilot/replace-old-sidebar-menu  
**Tipo de Mudança:** Nova Funcionalidade
