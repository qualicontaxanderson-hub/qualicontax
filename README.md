# Qualicontax - Sistema de Gestão Contábil

Sistema web responsivo para gestão de atividades contábeis desenvolvido com Flask.

## 🎯 Funcionalidades

- ✅ **Autenticação** - Login/logout com sessões seguras
- ✅ **Dashboard** - Visualização de KPIs, gráficos e métricas
- ✅ **CRUD de Clientes** - Cadastro completo de clientes PF/PJ
- ✅ **Gestão de Contratos** - Controle de contratos e serviços
- ✅ **Processos** - Gerenciamento de processos contábeis
- ✅ **Relatórios** - Relatórios de clientes, processos e obrigações
- ✅ **Documentos** - Upload e download de arquivos
- ✅ **API REST** - Endpoints para integração

## 🛠️ Tecnologias

### Backend
- **Flask 3.0.0** - Framework web Python
- **Flask-Login 0.6.3** - Gerenciamento de autenticação
- **MySQL Connector** - Conexão com Railway MySQL
- **Werkzeug** - Segurança e hash de senhas

### Frontend
- **HTML5 + Jinja2** - Templates dinâmicos
- **CSS3** - Design responsivo com Grid e Flexbox
- **JavaScript ES6+** - Interatividade
- **Chart.js 4.x** - Gráficos interativos
- **Font Awesome 6.x** - Ícones

### Banco de Dados
- **MySQL** - Railway Database

## 📋 Requisitos

- Python 3.8+
- MySQL 5.7+ ou Railway Database
- pip (gerenciador de pacotes Python)

## 🚀 Instalação

### 1. Clone o repositório
```bash
git clone https://github.com/qualicontaxanderson-hub/qualicontax.git
cd qualicontax
```

### 2. Crie um ambiente virtual
```bash
python3 -m venv venv
source venv/bin/activate  # No Windows: venv\Scripts\activate
```

### 3. Instale as dependências
```bash
pip install -r requirements.txt
```

### 4. Configure as variáveis de ambiente
Copie o arquivo `.env.example` para `.env` e configure:
```bash
cp .env.example .env
```

Edite o arquivo `.env` com suas credenciais:
```
DB_HOST=seu-host.railway.app
DB_PORT=3306
DB_NAME=railway
DB_USER=root
DB_PASSWORD=sua-senha
SECRET_KEY=sua-chave-secreta-muito-longa-e-segura
FLASK_ENV=development
```

### 5. Execute a aplicação
```bash
python app.py
```

A aplicação estará disponível em: `http://localhost:5000`

## 📊 Estrutura do Banco de Dados

O sistema espera as seguintes tabelas no MySQL:

```sql
-- Usuários
CREATE TABLE usuarios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nome VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    senha_hash VARCHAR(255) NOT NULL,
    tipo ENUM('admin', 'usuario') DEFAULT 'usuario',
    ativo BOOLEAN DEFAULT TRUE,
    data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Clientes
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

-- Processos
CREATE TABLE processos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id INT,
    numero_processo VARCHAR(100),
    tipo VARCHAR(100),
    status VARCHAR(50),
    data_abertura DATE,
    data_conclusao DATE,
    descricao TEXT,
    FOREIGN KEY (cliente_id) REFERENCES clientes(id)
);

-- Obrigações
CREATE TABLE obrigacoes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id INT,
    tipo_obrigacao_id INT,
    descricao VARCHAR(255),
    vencimento DATE,
    valor DECIMAL(10,2),
    status VARCHAR(50),
    pago BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (cliente_id) REFERENCES clientes(id)
);

-- Documentos
CREATE TABLE documentos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id INT,
    processo_id INT,
    nome_arquivo VARCHAR(255),
    tipo VARCHAR(50),
    caminho_arquivo VARCHAR(500),
    data_upload TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cliente_id) REFERENCES clientes(id),
    FOREIGN KEY (processo_id) REFERENCES processos(id)
);

-- Tarefas
CREATE TABLE tarefas (
    id INT AUTO_INCREMENT PRIMARY KEY,
    processo_id INT,
    usuario_id INT,
    titulo VARCHAR(255),
    descricao TEXT,
    prazo DATE,
    status VARCHAR(50),
    prioridade VARCHAR(20),
    FOREIGN KEY (processo_id) REFERENCES processos(id),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
);
```

## 🎨 Paleta de Cores

```css
--primary-green: #22C55E
--dark-green: #16A34A
--accent-orange: #FF6B35
--black: #000000
--white: #FFFFFF
```

## 📁 Estrutura do Projeto

```
qualicontax/
├── app.py                      # Aplicação principal
├── config.py                   # Configurações
├── requirements.txt            # Dependências
├── Procfile                    # Deploy Heroku
├── .env.example               # Exemplo de variáveis
├── models/                    # Models de dados
│   ├── usuario.py
│   ├── cliente.py
│   ├── processo.py
│   ├── tarefa.py
│   ├── documento.py
│   └── obrigacao.py
├── routes/                    # Rotas/Controllers
│   ├── auth.py
│   ├── dashboard.py
│   ├── clientes.py
│   ├── contratos.py
│   ├── processos.py
│   ├── relatorios.py
│   ├── documentos.py
│   └── api.py
├── templates/                 # Templates HTML
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── clientes/
│   ├── contratos/
│   ├── relatorios/
│   ├── includes/
│   └── errors/
├── static/                    # Arquivos estáticos
│   ├── css/
│   │   └── style.css
│   ├── js/
│   │   ├── main.js
│   │   └── charts.js
│   └── img/
└── utils/                     # Utilitários
    ├── db_helper.py
    ├── auth_helper.py
    ├── validators.py
    ├── formatters.py
    ├── file_handler.py
    └── integrations/
```

## 🔐 Segurança

- Senhas armazenadas com hash PBKDF2-SHA256
- Proteção CSRF em formulários
- Sessões seguras com cookies HTTP-only
- Validação de entrada em todos os formulários
- Sanitização de uploads de arquivos

## 🚀 Deploy

### Heroku

```bash
heroku create nome-do-app
heroku config:set DB_HOST=seu-host
heroku config:set DB_USER=seu-usuario
heroku config:set DB_PASSWORD=sua-senha
heroku config:set SECRET_KEY=sua-chave
git push heroku main
```

### Railway

1. Conecte o repositório ao Railway
2. Configure as variáveis de ambiente
3. Deploy automático a cada push

## 🧪 Testes

Para testar a aplicação localmente:

1. Configure um banco de dados de teste
2. Execute as migrations
3. Crie um usuário admin:

```python
from models.usuario import Usuario
from utils.auth_helper import hash_password

Usuario.create(
    nome='Admin',
    email='admin@qualicontax.com',
    senha_hash=hash_password('senha123'),
    tipo='admin'
)
```

## 📝 Licença

Este projeto é privado e confidencial. Todos os direitos reservados.

## 👥 Equipe

Desenvolvido por Qualicontax

## 📞 Suporte

Para suporte, entre em contato através do email: suporte@qualicontax.com