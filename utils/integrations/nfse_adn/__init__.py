# -*- coding: utf-8 -*-
"""Captura de NFS-e pelo ADN (Ambiente de Dados Nacional).

Escopo: SOMENTE LEITURA. O ADN aceita eventos de manifestação (Confirmação,
Rejeição, Confirmação Tácita, Anulação de Rejeição) e este módulo NÃO ENVIA
NENHUM — nem em teste, nem em produção. Mesmo princípio já valendo na captura
de DFe: manifestar tem efeito fiscal e é irreversível.

Onde este pacote mora e por quê
-------------------------------
A especificação pedia ``integracoes/nfse_adn/`` na raiz. Ficou em
``utils/integrations/`` porque é onde vivem ``dfe_captura``, ``cte_captura`` e
``dfe_sefaz`` — criar uma segunda árvore de integrações na raiz deixaria dois
lugares para procurar a mesma coisa. O nome do pacote é o da spec.
"""
