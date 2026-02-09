"""Preparado para integração com APIs bancárias"""

# TODO: Implementar integração bancária
# Possíveis integrações: Open Banking Brasil, APIs específicas de bancos

class BankingAPI:
    """Classe para integração bancária"""
    
    def __init__(self, bank_code=None, api_key=None):
        self.bank_code = bank_code
        self.api_key = api_key
        
    def get_account_balance(self, account_id):
        """Obtém saldo da conta"""
        raise NotImplementedError("Integração pendente")
        
    def get_transactions(self, account_id, start_date, end_date):
        """Obtém extrato de transações"""
        raise NotImplementedError("Integração pendente")
        
    def create_payment(self, payment_data):
        """Cria um pagamento"""
        raise NotImplementedError("Integração pendente")
