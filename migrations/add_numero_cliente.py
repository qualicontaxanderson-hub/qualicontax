"""
Script de migração: Adicionar campo numero_cliente à tabela clientes

Este script adiciona o campo numero_cliente que permite ao usuário
definir manualmente um código personalizado para cada cliente.
"""

from utils.db_helper import execute_query


def migrate_add_numero_cliente():
    """Adiciona campo numero_cliente na tabela clientes"""
    
    print("Iniciando migração: Adicionar campo numero_cliente...")
    
    # Verificar se a coluna já existe
    check_query = """
        SELECT COUNT(*) as count
        FROM information_schema.COLUMNS 
        WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME = 'clientes' 
        AND COLUMN_NAME = 'numero_cliente'
    """
    
    result = execute_query(check_query, fetch=True, fetch_one=True)
    
    if result and result['count'] > 0:
        print("✓ Campo numero_cliente já existe na tabela clientes")
        return True
    
    # Adicionar a coluna
    alter_query = """
        ALTER TABLE clientes 
        ADD COLUMN numero_cliente VARCHAR(20) UNIQUE 
        AFTER id
    """
    
    try:
        execute_query(alter_query)
        print("✓ Campo numero_cliente adicionado com sucesso!")
        return True
    except Exception as e:
        print(f"✗ Erro ao adicionar campo numero_cliente: {str(e)}")
        return False


if __name__ == '__main__':
    success = migrate_add_numero_cliente()
    if success:
        print("\n✓ Migração concluída com sucesso!")
    else:
        print("\n✗ Migração falhou. Verifique os erros acima.")
