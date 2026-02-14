"""
Migração: Adicionar Tabelas de Ramo de Atividade
Data: 2026-02-12
Descrição: Cria tabelas para gerenciar ramos de atividade dos clientes
"""
from utils.db_helper import execute_query


def migrate():
    """Executa a migração"""
    print("Iniciando migração: Adicionar Ramo de Atividade...")
    
    try:
        # Criar tabela ramos_atividade
        print("Criando tabela ramos_atividade...")
        query1 = """
        CREATE TABLE IF NOT EXISTS ramos_atividade (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(255) NOT NULL,
            descricao TEXT,
            situacao ENUM('ATIVO', 'INATIVO') DEFAULT 'ATIVO',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
        execute_query(query1, fetch=False)
        print("✓ Tabela ramos_atividade criada")
        
        # Criar tabela cliente_ramo_atividade_relacao
        print("Criando tabela cliente_ramo_atividade_relacao...")
        query2 = """
        CREATE TABLE IF NOT EXISTS cliente_ramo_atividade_relacao (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT NOT NULL,
            ramo_atividade_id INT NOT NULL,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
            FOREIGN KEY (ramo_atividade_id) REFERENCES ramos_atividade(id) ON DELETE CASCADE,
            UNIQUE KEY unique_cliente_ramo (cliente_id, ramo_atividade_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
        execute_query(query2, fetch=False)
        print("✓ Tabela cliente_ramo_atividade_relacao criada")
        
        # Inserir ramos de atividade padrão
        print("Inserindo ramos de atividade padrão...")
        ramos_padrao = [
            ('Posto de Gasolina', 'Postos de combustíveis e conveniências'),
            ('Distribuidora', 'Distribuidoras de produtos diversos'),
            ('Transportadoras', 'Empresas de transporte e logística'),
            ('Lava Rápido', 'Serviços de lavagem de veículos'),
            ('Comércio', 'Comércio em geral'),
            ('Indústria', 'Indústria e manufatura'),
            ('Serviços', 'Prestação de serviços'),
            ('Tecnologia', 'Empresas de tecnologia da informação'),
            ('Consultoria', 'Consultoria e assessoria'),
            ('Construção Civil', 'Construção e engenharia')
        ]
        
        query3 = """
        INSERT INTO ramos_atividade (nome, descricao, situacao)
        VALUES (%s, %s, 'ATIVO')
        """
        
        for nome, descricao in ramos_padrao:
            try:
                execute_query(query3, (nome, descricao), fetch=False)
                print(f"  ✓ Ramo '{nome}' inserido")
            except Exception as e:
                print(f"  - Ramo '{nome}' já existe ou erro: {str(e)}")
        
        print("\n✅ Migração concluída com sucesso!")
        print("\nPróximos passos:")
        print("1. Acesse /ramodeatividade para ver os ramos cadastrados")
        print("2. No formulário de cliente, selecione o ramo de atividade")
        print("3. O ramo aparecerá na página de detalhes do cliente")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Erro na migração: {str(e)}")
        return False


if __name__ == '__main__':
    migrate()
