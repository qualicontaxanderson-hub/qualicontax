"""
Script de inicialização do banco de dados
Cria as tabelas necessárias e um usuário admin padrão

AVISO: Este script cria uma estrutura de banco de dados que NÃO está compatível
com a estrutura atual em uso. A tabela real 'usuarios' possui:
- tipo_usuario ENUM('ADMIN','GERENTE','CONTADOR','ASSISTENTE','ESTAGIARIO') 
- situacao ENUM('ATIVO','INATIVO','FERIAS','LICENCA')
- Campos adicionais: cpf, telefone, departamento_id, cargo, capacidade_tarefas,
  data_admissao, foto_perfil, criado_em, atualizado_em, ultimo_acesso

Este script cria colunas 'tipo' e 'ativo' que não existem no banco real.
Recomenda-se atualizar este script antes de usar em produção.
"""
import sys
from utils.db_helper import execute_query
from utils.auth_helper import hash_password


def create_tables():
    """Cria as tabelas necessárias no banco de dados"""
    
    tables = [
        # Usuários
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(255) NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL,
            senha_hash VARCHAR(255) NOT NULL,
            tipo ENUM('admin', 'usuario') DEFAULT 'usuario',
            ativo BOOLEAN DEFAULT TRUE,
            data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Clientes
        """
        CREATE TABLE IF NOT EXISTS clientes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            tipo_pessoa ENUM('PF', 'PJ') NOT NULL,
            nome_razao_social VARCHAR(255) NOT NULL,
            nome_fantasia VARCHAR(255),
            cpf_cnpj VARCHAR(18) UNIQUE NOT NULL,
            inscricao_estadual VARCHAR(20),
            inscricao_municipal VARCHAR(20),
            email VARCHAR(255),
            telefone VARCHAR(20),
            celular VARCHAR(20),
            regime_tributario ENUM('SIMPLES', 'LUCRO_PRESUMIDO', 'LUCRO_REAL', 'MEI'),
            porte_empresa ENUM('MEI', 'ME', 'EPP', 'MEDIO', 'GRANDE'),
            data_inicio_contrato DATE,
            data_fim_contrato DATE,
            situacao ENUM('ATIVO', 'INATIVO', 'SUSPENSO', 'CANCELADO') DEFAULT 'ATIVO',
            observacoes TEXT,
            criado_por INT,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (criado_por) REFERENCES usuarios(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Endereços de Clientes
        """
        CREATE TABLE IF NOT EXISTS enderecos_clientes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT NOT NULL,
            tipo ENUM('COMERCIAL', 'RESIDENCIAL', 'CORRESPONDENCIA') DEFAULT 'COMERCIAL',
            cep VARCHAR(10),
            logradouro VARCHAR(255),
            numero VARCHAR(20),
            complemento VARCHAR(100),
            bairro VARCHAR(100),
            cidade VARCHAR(100),
            estado VARCHAR(2),
            pais VARCHAR(100) DEFAULT 'Brasil',
            principal BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Contatos de Clientes
        """
        CREATE TABLE IF NOT EXISTS contatos_clientes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT NOT NULL,
            nome VARCHAR(255),
            cargo VARCHAR(100),
            email VARCHAR(255),
            telefone VARCHAR(20),
            celular VARCHAR(20),
            departamento VARCHAR(100),
            principal BOOLEAN DEFAULT FALSE,
            ativo BOOLEAN DEFAULT TRUE,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Grupos de Clientes
        """
        CREATE TABLE IF NOT EXISTS grupos_clientes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(255) NOT NULL,
            descricao TEXT,
            situacao ENUM('ATIVO', 'INATIVO') DEFAULT 'ATIVO',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Relação Cliente-Grupo
        """
        CREATE TABLE IF NOT EXISTS cliente_grupo_relacao (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT NOT NULL,
            grupo_id INT NOT NULL,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
            FOREIGN KEY (grupo_id) REFERENCES grupos_clientes(id) ON DELETE CASCADE,
            UNIQUE KEY unique_cliente_grupo (cliente_id, grupo_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Processos
        """
        CREATE TABLE IF NOT EXISTS processos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT,
            numero_processo VARCHAR(100),
            tipo VARCHAR(100),
            status VARCHAR(50),
            data_abertura DATE,
            data_conclusao DATE,
            descricao TEXT,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Tipos de Obrigações
        """
        CREATE TABLE IF NOT EXISTS tipos_obrigacoes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(100) NOT NULL,
            descricao TEXT,
            periodicidade VARCHAR(50)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Obrigações
        """
        CREATE TABLE IF NOT EXISTS obrigacoes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT,
            tipo_obrigacao_id INT,
            descricao VARCHAR(255),
            vencimento DATE,
            valor DECIMAL(10,2),
            status VARCHAR(50),
            pago BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE SET NULL,
            FOREIGN KEY (tipo_obrigacao_id) REFERENCES tipos_obrigacoes(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Calendário de Obrigações
        """
        CREATE TABLE IF NOT EXISTS calendario_obrigacoes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT,
            obrigacao_id INT,
            data_vencimento DATE,
            status VARCHAR(50),
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
            FOREIGN KEY (obrigacao_id) REFERENCES obrigacoes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Documentos
        """
        CREATE TABLE IF NOT EXISTS documentos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT,
            processo_id INT,
            nome_arquivo VARCHAR(255),
            tipo VARCHAR(50),
            caminho_arquivo VARCHAR(500),
            data_upload TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE SET NULL,
            FOREIGN KEY (processo_id) REFERENCES processos(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Tarefas
        """
        CREATE TABLE IF NOT EXISTS tarefas (
            id INT AUTO_INCREMENT PRIMARY KEY,
            processo_id INT,
            usuario_id INT,
            titulo VARCHAR(255),
            descricao TEXT,
            prazo DATE,
            status VARCHAR(50),
            prioridade VARCHAR(20),
            FOREIGN KEY (processo_id) REFERENCES processos(id) ON DELETE CASCADE,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Templates de Processos
        """
        CREATE TABLE IF NOT EXISTS templates_processos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(255) NOT NULL,
            descricao TEXT,
            etapas TEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Notificações
        """
        CREATE TABLE IF NOT EXISTS notificacoes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            usuario_id INT,
            titulo VARCHAR(255),
            mensagem TEXT,
            lida BOOLEAN DEFAULT FALSE,
            data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        
        # Logs do Sistema
        """
        CREATE TABLE IF NOT EXISTS logs_sistema (
            id INT AUTO_INCREMENT PRIMARY KEY,
            usuario_id INT,
            acao VARCHAR(255),
            tabela VARCHAR(100),
            registro_id INT,
            detalhes TEXT,
            data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
    ]
    
    print("Criando tabelas...")
    for i, table_sql in enumerate(tables, 1):
        result = execute_query(table_sql)
        if result is not None:
            print(f"✓ Tabela {i}/{len(tables)} criada com sucesso")
        else:
            print(f"✗ Erro ao criar tabela {i}/{len(tables)}")
            return False
    
    print("\n✓ Todas as tabelas foram criadas com sucesso!")
    return True


def create_admin_user():
    """Cria um usuário admin padrão"""
    
    print("\nCriando usuário admin padrão...")
    
    # Verifica se já existe um admin
    check_query = "SELECT id FROM usuarios WHERE tipo = 'admin' LIMIT 1"
    existing_admin = execute_query(check_query, fetch=True, fetch_one=True)
    
    if existing_admin:
        print("✓ Já existe um usuário admin no sistema")
        return True
    
    # Cria admin padrão
    admin_email = "admin@qualicontax.com"
    admin_password = "admin123"
    admin_hash = hash_password(admin_password)
    
    insert_query = """
        INSERT INTO usuarios (nome, email, senha_hash, tipo, ativo)
        VALUES (%s, %s, %s, %s, TRUE)
    """
    result = execute_query(insert_query, ("Administrador", admin_email, admin_hash, "admin"))
    
    if result:
        print(f"\n✓ Usuário admin criado com sucesso!")
        print(f"  Email: {admin_email}")
        print(f"  Senha: {admin_password}")
        print(f"\n  ⚠️  IMPORTANTE: Altere a senha após o primeiro login!")
        return True
    else:
        print("✗ Erro ao criar usuário admin")
        return False


def main():
    """Função principal"""
    print("=" * 60)
    print("QUALICONTAX - Inicialização do Banco de Dados")
    print("=" * 60)
    print()
    
    # Cria tabelas
    if not create_tables():
        print("\n✗ Erro ao criar tabelas. Verifique as configurações do banco.")
        sys.exit(1)
    
    # Cria usuário admin
    if not create_admin_user():
        print("\n✗ Erro ao criar usuário admin.")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("✓ Inicialização concluída com sucesso!")
    print("=" * 60)
    print("\nVocê pode agora executar a aplicação com: python app.py")


if __name__ == '__main__':
    main()
