# -*- coding: utf-8 -*-
"""NFS-e da Prefeitura de São Paulo (Nota Fiscal Paulistana) — emissor municipal.

Segundo "motor" de NFS-e ao lado do nacional (``nfse_adn``). As duas gravam
nas mesmas tabelas (``nfse_emitidas`` / ``nfse_dps_controle``) e partem da
mesma declaração; o que muda é o empacotamento: aqui é SOAP com RPS assinado,
lá é REST com DPS.

Existe porque São Paulo não entrega a emissão ao Emissor Nacional para todo
mundo: em 31/10/2026 a Qualicontax (Simples) ainda só emitia pelo sistema da
prefeitura, e a obrigatoriedade do nacional (1º/11/2026) pode ser adiada.
"""
