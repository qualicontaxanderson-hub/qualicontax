"""Preparado para integração com API de Nota Fiscal Eletrônica"""

# TODO: Implementar integração com API de NF-e
# Possíveis integrações: Focus NFe, eNotas, WebMania

class NFeAPI:
    """Classe para integração com API de Nota Fiscal"""
    
    def __init__(self, api_key=None):
        self.api_key = api_key
        
    def emitir_nfe(self, dados_nfe):
        """Emite uma NF-e"""
        raise NotImplementedError("Integração pendente")
        
    def consultar_nfe(self, chave_acesso):
        """Consulta uma NF-e pela chave de acesso"""
        raise NotImplementedError("Integração pendente")
        
    def cancelar_nfe(self, chave_acesso, justificativa):
        """Cancela uma NF-e"""
        raise NotImplementedError("Integração pendente")
