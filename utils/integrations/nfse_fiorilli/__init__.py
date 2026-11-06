# -*- coding: utf-8 -*-
"""NFS-e pelo ISSWeb da Fiorilli no LAYOUT NACIONAL — 06/11/2026.

Para municípios que NÃO aceitam o Emissor Nacional do governo (consulta em
``utils/integrations/nfse_adn/convenio.py``) e usam o ISSWeb da Fiorilli. Primeiro deles:
Senador Canedo/GO (Roma, Fresh Start, BEM — locação, emissão a partir de dez/2026).

A descoberta que encurtou tudo: o ISSWeb tem um webservice NACIONAL
(``IssWeb-ejb/IssWebWSNacional/IssWebWSNacionalPortType``) que recebe a MESMA DPS 1.01
(namespace http://www.sped.fazenda.gov.br/nfse) que o motor nacional monta, e devolve a
NFS-e no mesmo formato. O cancelamento é o MESMO pedRegEvento (e101101) mais a IM.
O webservice antigo ABRASF 2.01 responde "FI410 — em desativação em favor do novo
WebService no layout nacional". Ou seja: nenhuma montagem própria — só o transporte.

Provado na homologação pública da Fiorilli (http://fi1.fiorilli.com.br:5663, prestador de
teste CNPJ 01001001000113, IM 15000): NFS-e 38974 (DPS assinada) e 38975 autorizadas,
cStat 100; 38975 cancelada (status CANCELADA).

Produção: a URL é do município (a prefeitura informa) e a prefeitura precisa credenciar a
empresa; a Fiorilli também confere se a atividade está no cadastro municipal da empresa.
"""
