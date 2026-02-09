# Guia Rápido - Qualicontax

## 🚀 Início Rápido (5 minutos)

### 1. Clone e Configure
```bash
git clone https://github.com/qualicontaxanderson-hub/qualicontax.git
cd qualicontax
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure .env
```bash
cp .env.example .env
# Edite .env com suas credenciais do Railway
```

### 3. Inicialize o Banco
```bash
python init_db.py
```

### 4. Execute
```bash
python app.py
```

Acesse: http://localhost:5000
Login padrão: admin@qualicontax.com / admin123

## 📁 Estrutura Rápida

```
qualicontax/
├── app.py              # Aplicação principal
├── config.py           # Configurações
├── init_db.py          # Script de inicialização
├── models/             # Modelos de dados
├── routes/             # Rotas/Controllers
├── templates/          # Templates HTML
├── static/             # CSS, JS, imagens
└── utils/              # Utilitários
```

## 🎨 Cores do Tema

- Verde Principal: `#22C55E`
- Verde Escuro: `#16A34A`
- Laranja: `#FF6B35`
- Fundo Claro: `#F9FAFB`

## 📝 Rotas Principais

- `/` - Dashboard (requer login)
- `/login` - Página de login
- `/logout` - Fazer logout
- `/clientes` - Lista de clientes
- `/clientes/novo` - Novo cliente
- `/contratos` - Contratos
- `/processos` - Processos
- `/relatorios` - Relatórios

## 🔐 Autenticação

Todas as rotas exceto `/login` requerem autenticação.

Usar decorator `@login_required` para proteger novas rotas:

```python
from utils.auth_helper import login_required

@app.route('/rota-protegida')
@login_required
def minha_rota():
    return render_template('template.html')
```

## 💾 Banco de Dados

### Executar Query
```python
from utils.db_helper import execute_query

# SELECT
results = execute_query(
    "SELECT * FROM clientes WHERE id = %s",
    (cliente_id,),
    fetch=True
)

# INSERT/UPDATE
execute_query(
    "INSERT INTO clientes (nome, email) VALUES (%s, %s)",
    (nome, email)
)
```

### Usar Models
```python
from models.cliente import Cliente

# Buscar cliente
cliente = Cliente.get_by_id(1)

# Criar cliente
cliente_id = Cliente.create({
    'nome_razao_social': 'Cliente Teste',
    'tipo_pessoa': 'PF',
    'cpf_cnpj': '12345678901'
})

# Listar com filtros
clientes = Cliente.get_all(
    filters={'situacao': 'ATIVO'},
    page=1,
    per_page=10
)
```

## 🎨 Templates

Todos os templates estendem `base.html`:

```html
{% extends 'base.html' %}

{% block title %}Minha Página{% endblock %}

{% block content %}
<div class="container">
    <h1>Conteúdo da página</h1>
</div>
{% endblock %}
```

### Filtros Jinja2 Disponíveis

```html
{{ cliente.cpf_cnpj|format_cpf }}
{{ cliente.cpf_cnpj|format_cnpj }}
{{ cliente.telefone|format_phone }}
{{ valor|format_currency }}
{{ data|format_date }}
```

## ✅ Validações

```python
from utils.validators import validate_cpf, validate_cnpj, validate_email

if validate_cpf(cpf):
    # CPF válido
    pass

if validate_email(email):
    # Email válido
    pass
```

## 🎯 JavaScript

### Máscaras Automáticas

```html
<input type="text" data-mask="cpf" name="cpf">
<input type="text" data-mask="cnpj" name="cnpj">
<input type="text" data-mask="phone" name="telefone">
<input type="text" data-mask="cep" name="cep">
```

### Busca de Clientes

```html
<input type="text" onkeyup="searchClientes(this)">
<div id="search-results"></div>
```

### Criar Gráficos

```javascript
// No template HTML
<canvas id="meuGrafico" width="400" height="200"></canvas>

// No JavaScript
createFluxoCaixaChart('meuGrafico', {
    labels: ['Jan', 'Fev', 'Mar'],
    entradas: [1000, 1500, 1200],
    saidas: [800, 900, 850],
    saldo: [200, 600, 350]
});
```

## 📤 Upload de Arquivos

```python
from utils.file_handler import save_file

# No route
if 'arquivo' in request.files:
    file = request.files['arquivo']
    caminho = save_file(file, subfolder='clientes')
```

## 🔄 API Endpoints

### Buscar Clientes
```
GET /api/clientes/search?q=termo
Retorna: JSON com lista de clientes
```

### Dados Dashboard
```
GET /api/dashboard/charts
Retorna: JSON com dados para gráficos
```

## 🐛 Debug

Ativar modo debug em `.env`:
```
FLASK_ENV=development
```

Ver logs no console onde rodou `python app.py`

## 📦 Deploy

### Heroku
```bash
heroku create nome-app
heroku config:set DB_HOST=...
heroku config:set DB_USER=...
heroku config:set DB_PASSWORD=...
heroku config:set SECRET_KEY=...
git push heroku main
```

### Railway
1. Conecte o repositório
2. Configure variáveis de ambiente
3. Deploy automático

## 🆘 Problemas Comuns

### Erro de Conexão MySQL
- Verifique credenciais no `.env`
- Confirme que o banco está acessível
- Execute `python init_db.py` para criar tabelas

### Página não carrega CSS/JS
- Verifique se a pasta `static/` existe
- Limpe cache do navegador (Ctrl+Shift+R)

### Erro ao fazer login
- Verifique se executou `init_db.py`
- Confirme que a tabela `usuarios` existe
- Use credenciais corretas (admin@qualicontax.com / admin123)

## 📞 Suporte

Email: suporte@qualicontax.com
