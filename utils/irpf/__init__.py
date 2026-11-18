"""Imposto de Renda Pessoa Física: leitura dos arquivos .DEC/.REC do programa da Receita.

Pedido do Anderson em 18/11/2026: "colocar o sistema para fazer a leitura dos Imposto de
Renda pessoa física". O programa IRPF grava cada declaração entregue em
``C:\\Arquivos de Programas RFB\\IRPF{ano}\\transmitidas\\{CPF}-IRPF-A-{exercicio}-{ano-base}-ORIGI.DEC``
(a declaração) e ``.REC`` (o recibo). São texto puro, ISO-8859-1, um registro por linha,
largura fixa. O desenho das linhas está dentro do próprio programa
(``irpf.jar!/mapeamentoTxt.xml``) e foi copiado para ``utils/irpf/leiautes/irpf_{ano}.json``.

Módulos:
- ``leitor``  — abre o .DEC/.REC e devolve os registros em dicionários (sem banco).
- ``ingest``  — grava no banco, cruza pelo CPF com o cadastro, guarda o arquivo no Dropbox
                e varre a ``_ENTRADA`` (Q-Colabore) no cron do roteador.
"""
