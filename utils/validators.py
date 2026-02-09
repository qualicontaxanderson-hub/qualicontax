"""Validadores de dados"""
import re


def validate_cpf(cpf):
    """
    Valida CPF brasileiro.
    
    Args:
        cpf (str): CPF a ser validado
        
    Returns:
        bool: True se válido
    """
    # Remove caracteres não numéricos
    cpf = re.sub(r'[^0-9]', '', cpf)
    
    # Verifica se tem 11 dígitos
    if len(cpf) != 11:
        return False
    
    # Verifica se todos os dígitos são iguais
    if cpf == cpf[0] * 11:
        return False
    
    # Validação do primeiro dígito verificador
    sum_digits = sum(int(cpf[i]) * (10 - i) for i in range(9))
    digit1 = (sum_digits * 10 % 11) % 10
    
    if int(cpf[9]) != digit1:
        return False
    
    # Validação do segundo dígito verificador
    sum_digits = sum(int(cpf[i]) * (11 - i) for i in range(10))
    digit2 = (sum_digits * 10 % 11) % 10
    
    if int(cpf[10]) != digit2:
        return False
    
    return True


def validate_cnpj(cnpj):
    """
    Valida CNPJ brasileiro.
    
    Args:
        cnpj (str): CNPJ a ser validado
        
    Returns:
        bool: True se válido
    """
    # Remove caracteres não numéricos
    cnpj = re.sub(r'[^0-9]', '', cnpj)
    
    # Verifica se tem 14 dígitos
    if len(cnpj) != 14:
        return False
    
    # Verifica se todos os dígitos são iguais
    if cnpj == cnpj[0] * 14:
        return False
    
    # Validação do primeiro dígito verificador
    weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    sum_digits = sum(int(cnpj[i]) * weights1[i] for i in range(12))
    digit1 = (sum_digits % 11)
    digit1 = 0 if digit1 < 2 else 11 - digit1
    
    if int(cnpj[12]) != digit1:
        return False
    
    # Validação do segundo dígito verificador
    weights2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    sum_digits = sum(int(cnpj[i]) * weights2[i] for i in range(13))
    digit2 = (sum_digits % 11)
    digit2 = 0 if digit2 < 2 else 11 - digit2
    
    if int(cnpj[13]) != digit2:
        return False
    
    return True


def validate_email(email):
    """
    Valida formato de email.
    
    Args:
        email (str): Email a ser validado
        
    Returns:
        bool: True se válido
    """
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def validate_phone(phone):
    """
    Valida formato de telefone brasileiro.
    
    Args:
        phone (str): Telefone a ser validado
        
    Returns:
        bool: True se válido
    """
    # Remove caracteres não numéricos
    phone = re.sub(r'[^0-9]', '', phone)
    
    # Verifica se tem 10 ou 11 dígitos (com DDD)
    return len(phone) in [10, 11]
