"""
Script de inicialização do banco de dados.
Garante que o usuário admin padrão exista no banco.

As tabelas são criadas pelo app.py na inicialização do servidor.
Este script apenas cria o usuário admin padrão se ainda não existir.
"""
import sys
from utils.db_helper import execute_query
from utils.auth_helper import hash_password


def create_admin_user():
    """Cria o usuário admin padrão se ainda não existir."""

    print("\nCriando usuário admin padrão...")

    # Verifica se já existe um admin usando o schema real (tipo_usuario)
    check_query = "SELECT id FROM usuarios WHERE tipo_usuario = 'ADMIN' LIMIT 1"
    existing_admin = execute_query(check_query, fetch=True, fetch_one=True)

    if existing_admin:
        print("✓ Já existe um usuário admin no sistema")
        return True

    # Cria admin padrão com o schema atual da tabela usuarios
    admin_email = "admin@qualicontax.com"
    admin_password = "admin123"
    admin_hash = hash_password(admin_password)

    insert_query = """
        INSERT INTO usuarios (nome, login, email, senha_hash, tipo_usuario, situacao)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    result = execute_query(insert_query, ("Administrador", "admin", admin_email, admin_hash, "ADMIN", "ATIVO"))

    if result:
        print("\n✓ Usuário admin criado com sucesso!")
        print(f"  Login: admin")
        print(f"  Email: {admin_email}")
        print(f"  Senha: {admin_password}")
        print("\n  ⚠️  IMPORTANTE: Altere a senha após o primeiro login!")
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

    # As tabelas são criadas automaticamente pelo app.py na inicialização.
    # Este script apenas garante que o usuário admin padrão exista.
    if not create_admin_user():
        print("\n✗ Erro ao criar usuário admin.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("✓ Inicialização concluída com sucesso!")
    print("=" * 60)
    print("\nVocê pode agora executar a aplicação com: gunicorn app:app --timeout 300")


if __name__ == '__main__':
    main()
