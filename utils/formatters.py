"""Formatadores de dados"""
import re
from datetime import datetime


def format_cpf(cpf):
    """
    Formata CPF para exibição (000.000.000-00).
    
    Args:
        cpf (str): CPF sem formatação
        
    Returns:
        str: CPF formatado
    """
    cpf = re.sub(r'[^0-9]', '', cpf)
    if len(cpf) == 11:
        return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
    return cpf


def format_cnpj(cnpj):
    """
    Formata CNPJ para exibição (00.000.000/0000-00).
    
    Args:
        cnpj (str): CNPJ sem formatação
        
    Returns:
        str: CNPJ formatado
    """
    cnpj = re.sub(r'[^0-9]', '', cnpj)
    if len(cnpj) == 14:
        return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
    return cnpj


def format_phone(phone):
    """
    Formata telefone para exibição (00) 00000-0000.
    
    Args:
        phone (str): Telefone sem formatação
        
    Returns:
        str: Telefone formatado
    """
    phone = re.sub(r'[^0-9]', '', phone)
    if len(phone) == 11:
        return f"({phone[:2]}) {phone[2:7]}-{phone[7:]}"
    elif len(phone) == 10:
        return f"({phone[:2]}) {phone[2:6]}-{phone[6:]}"
    return phone


def format_currency(value):
    """
    Formata valor para moeda brasileira (R$ 0.000,00).
    
    Args:
        value (float): Valor numérico
        
    Returns:
        str: Valor formatado
    """
    if value is None:
        return "R$ 0,00"
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_date(date, format_str='%d/%m/%Y'):
    """
    Formata data para exibição.
    
    Args:
        date: Data a ser formatada (datetime, date ou str)
        format_str: Formato desejado
        
    Returns:
        str: Data formatada
    """
    if date is None:
        return ""
    
    if isinstance(date, str):
        try:
            date = datetime.strptime(date, '%Y-%m-%d')
        except (ValueError, TypeError):
            return date
    
    return date.strftime(format_str)


def remove_mask(value):
    """
    Remove máscaras de CPF, CNPJ, telefone, etc.
    
    Args:
        value (str): Valor com máscara
        
    Returns:
        str: Valor sem máscara (apenas números)
    """
    if value is None:
        return ""
    return re.sub(r'[^0-9]', '', value)
