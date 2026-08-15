# NFS-e / ADN — Especificação consolidada da CAPTURA

**Módulo:** `utils/integrations/nfse_adn/`
**Substitui:** Documento D + adendo de eventos + patches 1, 2, 3, 4, 6 e 7.
Aqueles documentos deixam de valer como referência — o que estava certo neles está aqui.
**Consolidado em:** 14/08/2026

---

## 0. Proveniência — leia antes de confiar em qualquer linha

Este documento nasceu de sete arquivos que se corrigiam entre si. Ao consolidar,
**cada afirmação foi reverificada contra a fonte normativa**, não copiada. O que
não pôde ser verificado está marcado.

### 0.1 Fontes normativas usadas

| Fonte | Versão / data | Verificada |
|---|---|---|
| Swagger `API NFS-e - ADN Contribuinte` | v1, produção restrita | ✅ lido |
| `ANEXO_I-SEFIN_ADN-DPS_NFSe-SNNFSe` | **v1.01, 12/02/2026** — 417 linhas | ✅ lido |
| `ANEXO_II-SEFIN_ADN-PEDREGEVT_EVT-SNNFSe` | **v1.01, 17/01/2026** | ✅ lido |
| `NFSe-ESQUEMAS_XSD` | **v1.01, 09/02/2026** | ✅ lido |
| Manual dos Contribuintes — APIs do ADN | v1.0, 12/02/2026 | ✅ lido |

> **A lição que custou meio dia:** o Anexo I que circulava era de **09/09/2025**,
> com 281 linhas e **zero** ocorrências de IBS/CBS. O vigente tem 417 linhas e
> **219**. A URL antiga trazia `-1` no nome — sufixo de duplicata do Plone, não
> número de versão. **Confira data interna e contagem de linhas de todo artefato
> normativo antes de derivar spec. Anexo velho não avisa que é velho.**

### 0.2 Lacuna conhecida: o "Patch 5" não existe

Os patches 6 e 7 citam um Patch 5 em três pontos. **O arquivo nunca foi
localizado.** As três decisões que dependiam dele foram reconstruídas a partir
das citações e estão marcadas com 🔶 no corpo do documento:

- 🔶 **quarentena** — documento que não parseia vai para quarentena e o cursor
  avança (citado no patch 6 §7);
- 🔶 **bloqueio em eixo separado** — nunca entra em `situacao` (citado e
  refinado no patch 7 §8);
- 🔶 **validação de `TipoAmbiente`** — já implementada em `client.py`.

Se algo mais existia no Patch 5, não está aqui e ninguém sabe o quê.

### 0.3 Regras inegociáveis

> **SOMENTE LEITURA. NUNCA MANIFESTAR.**

O ADN aceita eventos de manifestação (Confirmação, Rejeição, Confirmação Tácita,
Anulação de Rejeição). **O app não envia nenhum** — nem em teste, nem em
produção, nem "só pra ver". Mesmo princípio da captura de DFe: manifestar tem
efeito fiscal e é irreversível.

Começar sempre em **produção restrita**. Migrações por Colab contra o MySQL do
Railway; nada roda na máquina do Anderson.

---

## 1. O que o módulo faz

Captura as NFS-e em que cada empresa da carteira figure como **prestador,
tomador ou intermediário**, gravando no banco e arquivando o XML no Dropbox.

Emitidas e tomadas vêm **na mesma chamada, no mesmo cursor** — não existem dois
fluxos. É mais simples que o `dfe_captura`, onde NF-e e CT-e exigem serviços e
cursores separados.

**Universo real, medido em 14/08/2026:** 54 clientes ativos, 46 com certificado
próprio válido, mais 3 alcançáveis pela regra de raiz (§6) = **49 empresas**.
As 5 restantes precisam de certificado e devem aparecer em relatório, não sumir.

---

## 2. FASE 0 — aferição de cobertura (BLOQUEANTE)

**Não implementar as fases seguintes antes de rodar isto e o Anderson aprovar.**

Nem toda nota emitida pelos municípios chega ao ADN: município com sistema
próprio não integrado mantém o documento só na base local. O agregado nacional
é bom, mas **o que decide é a cobertura desta carteira**.

Script em Colab, **fora do app**, sem criar tabela e sem gravar nada.

### CNPJs escolhidos — variedade de perfil, não de geografia

| Empresa | CNPJ | Onde | Por quê |
|---|---|---|---|
| MEGA TERCEIRIZAÇÃO DE SERVIÇOS | `40486724000101` | Santo André/SP, Simples | única que **emite** NFS-e; mede a cobertura das emitidas |
| POSTO NOVO HORIZONTE GOIATUBA | `33503987000116` | Goiatuba/GO, Lucro Real | só **toma**; município pequeno, o caso duvidoso |
| CLIRA TRANSPORTES RODOVIÁRIOS | `55244401000260` | Curitiba/PR, Simples | terceiro estado; é filial `/0002`, testa o certificado por raiz |

A carteira é 27 SP, 18 GO e 1 PR — a amostra respeita a proporção sem virar só
Goiás. Nas **tomadas**, quem emite é o prestador, que pode estar em qualquer
município do Brasil: a cobertura das tomadas depende de onde estão os
prestadores, não de onde está o cliente.

### O que medir

| Métrica | Por quê |
|---|---|
| % das **tomadas** que vieram | dor principal do Fiscal |
| % das **emitidas** que vieram | conferência de faturamento |
| profundidade do histórico | dimensiona o backfill |
| o certificado da raiz funciona para a filial? | confirma §6 na prática |

Comparar com o que o escritório lançou manualmente no último trimestre.

### Critério de decisão — do Anderson, não do código

| Cobertura | Decisão |
|---|---|
| **> 80%** | construir; vira produto |
| **50–80%** | construir, mas o lançamento manual segue oficial. **Não anunciar como "automático"** |
| **< 50%** | engavetar; reavaliar em 6 meses |

---

## 3. Diferenças críticas em relação ao `dfe_captura`

Portar o código do DFe sem observar estes pontos gera bug silencioso.

### 3.1 A parada do loop tem STATUS PRÓPRIO — não é lista vazia

O ADN não devolve `maxNSU`. Mas também **não se para por lista vazia**: o campo
`StatusProcessamento` diz explicitamente o que aconteceu.

```
DOCUMENTOS_LOCALIZADOS        -> processa e continua
NENHUM_DOCUMENTO_LOCALIZADO   -> FIM DA FILA
REJEICAO                      -> ERRO. O cursor NÃO avança
```

`REJEICAO` **também vem com lista vazia**. Tratar os dois como "acabou" faz o
cursor parar cedo e em silêncio. Status desconhecido também é erro — nunca lido
como fim.

### 3.2 NÃO existe limite de 90 dias

A SEFAZ descarta documentos 90 dias após a recepção. O ADN não estipulou limite
de data de emissão: **a partir do NSU 0 vem o histórico inteiro**. A primeira
carga pode ter anos × 49 empresas. O `DFE_CAPTURA_PRAZO_SEG=960` não segura —
ver §5.

### 3.3 REST/JSON, não SOAP/XML — mas o certificado é o mesmo

mTLS com certificado ICP-Brasil A1/A3, exatamente como a SEFAZ. Metade do
cliente já existia (§8).

### 3.4 Um cursor só, cobrindo emitidas e tomadas

### 3.5 O JSON é ENVELOPE — o documento fiscal está comprimido dentro

```
DistribuicaoNSU: NSU · ChaveAcesso · TipoDocumento · TipoEvento
                 · ArquivoXml · DataHoraGeracao
```

**Nenhum campo fiscal aparece no JSON.** Número, série, competência, prestador,
tomador, valores, ISS: tudo está no `ArquivoXml`, em **GZip + base64**. O
`parser` descompacta e lê XML.

---

## 4. Regra de ouro do cursor

> **O NSU só avança após gravação confirmada no banco.**

```python
for doc in lote.documentos:
    try:
        salvar_documento(doc)          # INSERT ... ON DUPLICATE KEY
        db.commit()                    # commit ANTES de avançar
        atualizar_cursor(empresa_id, doc['NSU'])
        db.commit()
    except Exception as e:
        db.rollback()
        registrar_erro(empresa_id, doc['NSU'], e)
        raise                          # NÃO avança. Reprocessa depois.
```

**Nunca** avançar em `finally` ou fora do `try`. **Nunca** avançar em lote antes
de gravar. `LoteDFe.ultimo_nsu` existe **só para log** — avançar por lote
esconderia um buraco numa queda no meio.

---

## 5. Dois modos de operação

```python
MODO_BACKFILL = {
    "empresas_por_execucao": 1,
    "deadline_seg": None,
    "trigger": "manual / sob demanda",
    "lotes_max_por_execucao": 200,     # ~10.000 docs; evita execução infinita
}
MODO_INCREMENTAL = {
    "empresas_por_execucao": "todas ativas",
    "deadline_seg": 960,               # mesmo padrão do DFe
    "trigger": "Railway Cron a cada 3h",
}
```

Ciclo de vida: `cadastrada → backfill → (várias execuções) → lote vazio →
incremental → cron de 3h`. Backfill em várias execuções é o esperado, não falha.

---

## 6. Certificado — a regra é RAIZ DE CNPJ

**Confirmado no manual oficial:** *"As consultas da API de Distribuição dos
Contribuintes podem ser realizadas utilizando um certificado cujo CNPJ tenha o
mesmo CNPJ Raiz do contribuinte para o qual se deseja consultar"*, e há o
parâmetro `cnpjConsulta` para consultar CNPJ diferente do certificado — com
validação de raiz entre os dois.

Isso resolve a dúvida antiga: **não é preciso um e-CNPJ por estabelecimento.**

```python
def resolver_certificado(cliente_id):
    # 1) certificado PRÓPRIO do cliente
    # 2) certificado de qualquer empresa ativa da MESMA RAIZ
    #    OS DOIS PASSOS COM O MESMO FILTRO: ativo=1, validade >= hoje
```

> ⚠️ **Filtro assimétrico é bug.** A primeira implementação filtrava validade só
> no passo 2: empresa com certificado próprio **vencido** devolvia o vencido e
> nunca chegava ao passo da raiz, falhando no mTLS com um válido disponível ao
> lado. Só apareceria no primeiro vencimento — parecendo problema do ADN.

**Não** cai para certificado de contador (regra do DFe). Aqui o critério é raiz,
que o ADN valida do lado dele.

---

## 7. Schema

Migrações **idempotentes**, aplicadas por Colab no MySQL do Railway.

### 7.1 Cursor

```sql
CREATE TABLE IF NOT EXISTS dfe_nsu_nfse (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  empresa_id    INT NOT NULL,
  cnpj          VARCHAR(14) NOT NULL,
  ult_nsu       BIGINT NOT NULL DEFAULT 0,
  modo          VARCHAR(15) NOT NULL DEFAULT 'backfill',
  ultima_exec   DATETIME NULL,
  ultimo_sucesso DATETIME NULL,
  tentativas_falha INT NOT NULL DEFAULT 0,
  ultimo_erro   TEXT NULL,
  ativo         TINYINT(1) NOT NULL DEFAULT 1,
  criado_em     DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_cnpj (cnpj),
  INDEX idx_modo_ativo (modo, ativo),
  INDEX idx_ultimo_sucesso (ultimo_sucesso)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 7.2 Documentos

Chave única **`(chave_acesso, papel)`**, não só a chave: se duas empresas da
carteira forem partes da mesma nota — uma presta, outra toma —, o documento
aparece nos dois cursores com papéis diferentes e **ambos os registros são
legítimos**. Chave simples apagaria um deles em silêncio.

```sql
CREATE TABLE IF NOT EXISTS nfse_capturadas (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  empresa_id     INT NOT NULL,
  cnpj_interessado VARCHAR(14) NOT NULL,
  nsu            BIGINT NOT NULL,
  chave_acesso   VARCHAR(60) NOT NULL,
  papel          VARCHAR(15) NOT NULL,   -- emitente|tomador|intermediario
  tipo_doc       VARCHAR(15) NOT NULL,   -- nfse|evento

  numero         VARCHAR(30) NULL,
  serie          VARCHAR(10) NULL,
  data_emissao   DATETIME NULL,
  data_processamento DATETIME NULL,
  competencia    DATE NULL,
  municipio_ibge VARCHAR(7) NULL,
  municipio_emissao VARCHAR(7) NULL,

  prestador_doc  VARCHAR(20) NULL,
  prestador_nome VARCHAR(255) NULL,
  tomador_doc    VARCHAR(20) NULL,
  tomador_nome   VARCHAR(255) NULL,
  intermediario_doc VARCHAR(20) NULL,
  destinatario_doc  VARCHAR(20) NULL,    -- §9.4: dado, NUNCA papel
  destinatario_nome VARCHAR(255) NULL,

  codigo_servico VARCHAR(20) NULL,
  codigo_servico_mun VARCHAR(10) NULL,
  codigo_nbs     VARCHAR(20) NULL,
  discriminacao  TEXT NULL,

  valor_servicos DECIMAL(15,2) NULL,
  valor_desc_incond DECIMAL(15,2) NULL,
  valor_desc_cond   DECIMAL(15,2) NULL,
  base_calculo   DECIMAL(15,2) NULL,
  aliquota_iss   DECIMAL(7,4) NULL,
  valor_iss      DECIMAL(15,2) NULL,
  total_retencoes DECIMAL(15,2) NULL,
  valor_liquido  DECIMAL(15,2) NULL,
  iss_retido     TINYINT(1) NULL,
  opt_simples    TINYINT NULL,

  cstat          SMALLINT NULL
    COMMENT 'Geracao da NFS-e (100/101/102/103/107). NAO indica cancelamento.',
  situacao       VARCHAR(20) NOT NULL DEFAULT 'ativa',
  chave_substituta VARCHAR(60) NULL,
  substitui_chave  VARCHAR(60) NULL
    COMMENT 'subst/chSubstda: qual nota ESTA substituiu. Verificacao cruzada.',

  restricao_eventos TINYINT(1) NOT NULL DEFAULT 0
    COMMENT 'Municipio impediu o CANCELAMENTO desta NFS-e. Ela segue ATIVA e VALIDA.',
  restricao_codigos VARCHAR(200) NULL,
  restricao_em   DATETIME NULL,

  xml_path       VARCHAR(500) NULL,
  raw_json       JSON NULL,               -- SEMPRE gravar
  criado_em      DATETIME DEFAULT CURRENT_TIMESTAMP,

  UNIQUE KEY uk_chave_papel (chave_acesso, papel),
  INDEX idx_empresa_comp (empresa_id, competencia),
  INDEX idx_empresa_nsu (empresa_id, nsu),
  INDEX idx_papel (empresa_id, papel, competencia),
  INDEX idx_situacao (situacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 7.3 IBS/CBS — três eixos, não campos achatados

O anexo vigente traz **dois** grupos `IBSCBS`: um calculado pela autoridade
(`NFSe/infNFSe/IBSCBS/`) e um declarado (`.../DPS/infDPS/IBSCBS/`).

**IBS-UF e IBS-Município são entes diferentes**, com alíquotas e diferimentos
diferentes. Somar destrói a apuração.

```sql
ALTER TABLE nfse_capturadas
  -- declarado
  ADD COLUMN ibscbs_cst        VARCHAR(3)  NULL,
  ADD COLUMN ibscbs_cclasstrib VARCHAR(10) NULL,
  ADD COLUMN ibscbs_fin_nfse   TINYINT     NULL,
  ADD COLUMN ibscbs_cind_op    VARCHAR(10) NULL,
  ADD COLUMN ibscbs_ind_dest   TINYINT     NULL,
  -- calculado
  ADD COLUMN ibscbs_bc         DECIMAL(15,2) NULL,
  ADD COLUMN ibs_uf_aliq_efet  DECIMAL(7,4)  NULL,
  ADD COLUMN ibs_uf_valor      DECIMAL(15,2) NULL,
  ADD COLUMN ibs_uf_dif        DECIMAL(15,2) NULL,
  ADD COLUMN ibs_mun_aliq_efet DECIMAL(7,4)  NULL,
  ADD COLUMN ibs_mun_valor     DECIMAL(15,2) NULL,
  ADD COLUMN ibs_mun_dif       DECIMAL(15,2) NULL,
  ADD COLUMN ibs_total         DECIMAL(15,2) NULL,
  ADD COLUMN cbs_aliq_efet     DECIMAL(7,4)  NULL,
  ADD COLUMN cbs_valor         DECIMAL(15,2) NULL,
  ADD COLUMN cbs_dif           DECIMAL(15,2) NULL,
  ADD COLUMN ibs_cred_pres     DECIMAL(15,2) NULL,
  ADD COLUMN cbs_cred_pres     DECIMAL(15,2) NULL,
  ADD COLUMN valor_total_nf    DECIMAL(15,2) NULL;
```

Grupos raros (`gTribCompraGov`, `gReeRepRes`, `imovel`, `gRefNFSe`) ficam no
`raw_json`/XML. Normalizar depois, se aparecerem no volume real.

### 7.4 Eventos — tabela separada, gravado SEMPRE

Eventos chegam com **NSU próprio**, independente do documento. Três cenários,
todos precisam funcionar:

| Cenário | O que acontece |
|---|---|
| evento **depois** do documento | caso normal, aplica na hora |
| evento **antes** do documento | backfill que começou no meio — **evento órfão** |
| documento **nunca** capturado | município fora do ADN — órfão para sempre |

**O bug clássico:** aplicar evento só em linha existente e descartar o resto. O
órfão desaparece e, quando o documento chega, entra como `ativa` — permanentemente
errado e sem sinal nenhum. **Por isso o evento é gravado exista ou não o documento.**

```sql
CREATE TABLE IF NOT EXISTS nfse_eventos (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  empresa_id_origem INT NOT NULL
    COMMENT 'PROVENIENCIA: qual cursor entregou primeiro. NAO filtrar por aqui.',
  cnpj_origem     VARCHAR(14) NOT NULL COMMENT 'idem',
  nsu_origem      BIGINT NOT NULL
    COMMENT 'NSU no cursor de origem. Outros cursores tem NSU diferente.',

  chave_referenciada VARCHAR(60) NOT NULL,
  tipo_evento     VARCHAR(40) NOT NULL,
  sequencia       INT NOT NULL,          -- nSeqEvento, 3 digitos, obrigatorio
  data_evento     DATETIME NULL,
  motivo          TEXT NULL,
  chave_substituta VARCHAR(60) NULL,

  aplicado        TINYINT(1) NOT NULL DEFAULT 0,
  orfao           TINYINT(1) NOT NULL DEFAULT 0,
  revisar         TINYINT(1) NOT NULL DEFAULT 0
    COMMENT 'tipo fora do MAPA_EVENTO_SITUACAO. Requer analise humana.',

  raw_json        JSON NULL,
  criado_em       DATETIME DEFAULT CURRENT_TIMESTAMP,

  UNIQUE KEY uk_evento (chave_referenciada, tipo_evento, sequencia),
  INDEX idx_chave (chave_referenciada),
  INDEX idx_orfao (orfao, aplicado),
  INDEX idx_revisar (revisar)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

> **O evento COLAPSA entre cursores, o documento não.** Um cancelamento é **um
> fato do documento**, não dois — se duas empresas da carteira são partes da
> nota, o evento chega nos dois cursores e vira **uma linha**. Por isso os campos
> de origem se chamam `*_origem`: são proveniência, **nunca filtro**. Para os
> eventos de uma empresa, faça `nfse_capturadas` (filtrada por `empresa_id`) →
> JOIN por `chave_acesso = chave_referenciada`.

### 7.5 Log de execução

```sql
CREATE TABLE IF NOT EXISTS nfse_consulta_log (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  empresa_id INT NULL, cnpj VARCHAR(14) NULL, modo VARCHAR(15) NULL,
  nsu_inicial BIGINT NULL, nsu_final BIGINT NULL,
  qtd_docs INT NOT NULL DEFAULT 0, qtd_salvos INT NOT NULL DEFAULT 0,
  qtd_duplicados INT NOT NULL DEFAULT 0,
  http_status INT NULL, duracao_ms INT NULL, erro TEXT NULL,
  criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_empresa_data (empresa_id, criado_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 7.6 Arquivamento do XML

```
EMPRESAS/{nº} - {razão social}/FISCAL/{ano}/{mês}/NFSE/{chave_acesso}.xml
```

Mesma convenção do DFe.

---

## 8. Estrutura do módulo

```
utils/integrations/nfse_adn/
├── __init__.py
├── client.py         ✅ JÁ ESCRITO (commit 6dea581)
├── parser.py         ✅ JÁ ESCRITO (commit 53c72c9) — o único que conhece campo
├── repositorio.py    ⬜ persistência e o escritor único de situacao
└── captura.py        ⬜ orquestração, backfill vs incremental
```

> **Onde mora e por quê:** a spec original pedia `integracoes/` na raiz. Ficou em
> `utils/integrations/` porque é onde já vivem `dfe_captura`, `cte_captura` e
> `dfe_sefaz`.

### 8.1 O que o `client.py` já resolve

Certificado (§6), mTLS, retry com backoff, tradução da resposta, guarda de
ambiente e desempacotamento gzip+base64. **Ele é fino porque metade já existia**
em `utils/certificado_digital` e `dfe_sefaz.montar_sessao_mtls` — o certificado
é o mesmo e a autenticação mútua é a mesma; só a camada de cima mudou.

Decisões embutidas nele que valem para o resto do módulo:

- **401/403 não entram no retry.** Recusa de autorização não muda na segunda
  tentativa, e três tentativas inúteis por empresa consomem o deadline das outras.
- **`SemCertificado` deve ser capturada ANTES de `ADNError`** — é problema de
  cadastro (desativa a empresa), não de transporte (tenta depois).
- 🔶 **`AmbienteDivergente`** confere o `TipoAmbiente` da resposta contra o
  ambiente pedido. É a guarda contra rodar a aferição em produção achando que é
  restrita.

**NÃO testado contra o ADN real** — o handshake só se prova na Fase 0.

---

## 9. `parser.py` — leiaute e mapeamento

### 9.1 A NFS-e CONTÉM a DPS — e há QUATRO nomes duplicados

```
NFSe/
├── infNFSe/                    ← GERADO PELA AUTORIDADE
│   ├── nNFSe, cStat, dhProc, cLocIncid, xLocEmi, xLocPrestacao
│   ├── emit/                   ← quem EMITIU a DPS (ver 9.3)
│   ├── valores/                ← CALCULADOS: vBC, pAliqAplic, vISSQN, vLiq
│   ├── IBSCBS/                 ← CALCULADO: aliquotas, totais, diferimentos
│   └── DPS/infDPS/             ← DECLARADO PELO CONTRIBUINTE
│       ├── dhEmi, serie, nDPS, dCompet, tpEmit, cLocEmi
│       ├── subst/ chSubstda
│       ├── prest/ · toma/ · interm/
│       ├── serv/ cServ/
│       ├── valores/            ← DECLARADOS
│       │   └── trib/ tribMun, tribFed(piscofins/CST), totTrib
│       └── IBSCBS/             ← DECLARADO: CST, cClassTrib, finNFSe, dest/
└── Signature
```

> ### ⚠️ TODO XPATH DEVE SER ABSOLUTO
> **Cinco** nomes existem em níveis diferentes — a spec dizia quatro até a
> extração dos XPaths para o `parser.py` revelar o quinto:
>
> | Nome | Onde |
> |---|---|
> | `valores` | `infNFSe/valores/` × `.../DPS/infDPS/valores/` |
> | `IBSCBS` | `infNFSe/IBSCBS/` × `.../DPS/infDPS/IBSCBS/` |
> | `trib` | `.../valores/trib/` × `.../IBSCBS/valores/trib/` |
> | `CST` | `tribFed/piscofins/CST` (PIS/COFINS) × `gIBSCBS/CST` (IBS/CBS) |
> | **`vBC`** | `infNFSe/valores/vBC` (base do ISS) × `infNFSe/IBSCBS/valores/vBC` (base do IBS/CBS) |
>
> E ainda `vIBSUF`, `vIBSMun` e `vCBS`, que existem no totalizador normal **e de
> novo** em `totCIBS/gTribCompraGov/`.
>
> O pior continua sendo o `CST`: tributos completamente diferentes, mesmo nome —
> e ler o errado não quebra nada, só fica errado em silêncio.
>
> **Nenhuma busca por nome de elemento. Nunca.** O `parser._no()` desce filho a
> filho por esse motivo: busca larga pega o primeiro que encontrar, e "o
> primeiro" não é regra nenhuma.

### 9.2 `cStat` é de GERAÇÃO, não de cancelamento

| Código | Significado |
|---|---|
| 100 | NFS-e Gerada |
| **101** | **NFS-e de Substituição Gerada** |
| 102 | Decisão Judicial ou Administrativa |
| 103 | Avulsa |
| 107 | MEI |

> ⚠️ **Armadilha de direção no 101:** significa que **esta nota É a substituta**.
> **Não** significa que ela foi substituída. Inverter marcaria a nota nova como
> `substituida` e deixaria a antiga ativa — exatamente ao contrário.

Nenhum valor indica cancelamento. `cStat` é **coluna informativa** e nunca
alimenta `situacao`.

### 9.3 `emit` ≠ `prest` — e isso decide o `papel`

O leiaute traz `tpEmit`: **a DPS pode ser emitida por quem não prestou o serviço.**

```python
def extrair_papel(xml, cnpj_consulta) -> str | None:
    base = "NFSe/infNFSe/DPS/infDPS"
    for papel, no in (("emitente", "prest"), ("tomador", "toma"),
                      ("intermediario", "interm")):
        if doc_de(xml, f"{base}/{no}") == cnpj_consulta:
            return papel
    return None      # -> quarentena (§12)
```

"emitente" no nosso schema = **prestador**. A origem é `prest/`, **nunca** `emit/`.

**`CNPJ` · `CPF` · `NIF` são alternativos** (coluna `ELE` = `CE`, elemento de
escolha): exatamente um aparece. Vale para `prest`, `toma`, `interm`, `fornec` e
`dest`.

### 9.4 `dest` é um QUARTO ator — e não entra no papel

`DPS/infDPS/IBSCBS/dest/` traz documento, nome e endereço, e o `indDest` diz que
o destinatário **pode não ser o tomador**.

**Mas o ADN distribui apenas a prestador, tomador e intermediário.** Uma empresa
que apareça só como destinatário **não recebe a nota no próprio cursor**. Criar
papel ou cursor por `dest` geraria empresa que nunca recebe nada.

Grave como **dado** (`destinatario_doc`, `destinatario_nome`, `ibscbs_ind_dest`),
nunca como papel.

### 9.5 Mapeamento campo a campo

| Coluna | XPath absoluto |
|---|---|
| `chave_acesso` | envelope `ChaveAcesso` (50 díg.; `infNFSe/@Id` tem 53 = prefixo + chave) |
| `numero` | `NFSe/infNFSe/nNFSe` |
| `serie` | `NFSe/infNFSe/DPS/infDPS/serie` |
| `data_emissao` | `NFSe/infNFSe/DPS/infDPS/dhEmi` |
| `data_processamento` | `NFSe/infNFSe/dhProc` |
| `competencia` | `NFSe/infNFSe/DPS/infDPS/dCompet` — **usar na apuração**, não `dhEmi` |
| `municipio_ibge` | `NFSe/infNFSe/cLocIncid` (`0-1`) |
| `municipio_emissao` | `NFSe/infNFSe/DPS/infDPS/cLocEmi` |
| `prestador_doc` / `_nome` | `.../DPS/infDPS/prest/{CNPJ\|CPF\|NIF}` · `/xNome` (`0-1`) |
| `tomador_doc` / `_nome` | `.../DPS/infDPS/toma/...` (grupo `0-1`) |
| `intermediario_doc` | `.../DPS/infDPS/interm/...` (`0-1`) |
| `codigo_servico` | `.../serv/cServ/cTribNac` |
| `codigo_servico_mun` | `.../serv/cServ/cTribMun` (`0-1`) |
| `codigo_nbs` | `.../serv/cServ/cNBS` (`0-1`) |
| `discriminacao` | `.../serv/cServ/xDescServ` |
| `valor_servicos` | `.../DPS/infDPS/valores/vServPrest/vServ` |
| `base_calculo` | `NFSe/infNFSe/valores/vBC` — **calculado** |
| `aliquota_iss` | `NFSe/infNFSe/valores/pAliqAplic` |
| `valor_iss` | `NFSe/infNFSe/valores/vISSQN` |
| `total_retencoes` | `NFSe/infNFSe/valores/vTotalRet` |
| `valor_liquido` | `NFSe/infNFSe/valores/vLiq` |
| `iss_retido` | derivar de `.../valores/trib/tribMun/tpRetISSQN` |
| `opt_simples` | `.../prest/regTrib/opSimpNac` (`3` = ME/EPP) |
| `substitui_chave` | `.../DPS/infDPS/subst/chSubstda` (§9.6) |

**Monetários em `Decimal`, nunca `float`** (formato `1-15V2`).
**Campos `0-1` ausentes → `None`**, nunca `""` nem `0`.

### 9.6 `subst/chSubstda` — verificação cruzada de graça

Uma nota que traz `subst/chSubstda` declara **qual nota ela substituiu**. É a
mesma informação do evento de substituição, por outro caminho.

**Não usar para escrever `situacao`** — quebraria o escritor único (§10.3).
**Usar como alerta:** se X declara substituir Y e Y não tem evento de
substituição, alguma coisa se perdeu. Detecção de falha silenciosa aproveitando
redundância que o leiaute já oferece.

> ⚠️ **DIREÇÕES OPOSTAS, NOMES QUASE IGUAIS.** No Anexo I, `subst/chSubstda` fica
> na nota **nova** e aponta para a **substituída**. No Anexo II,
> `e105102/chSubstituta` fica no evento da nota **velha** e aponta para a
> **substituta**. Trocar um pelo outro cancela a nota errada, e as duas ficam
> plausíveis no banco. **Documente o sentido em cada uso.**

---

## 10. Eventos e `situacao`

### 10.1 O tipo do evento é o NOME DO ELEMENTO

**Verificado no XSD:** `tpEvento` **não existe**. O tipo é o elemento dentro de
`infPedReg`, num grupo de escolha. Os 16 declarados:

| Elemento | Evento | Efeito em `situacao` |
|---|---|---|
| `e101101` | Cancelamento | `cancelada` |
| `e105102` | Cancelamento por Substituição | `substituida` + `chave_substituta` |
| `e101103` | **Solicitação** de Análise Fiscal | **nenhum** — é pedido, não decisão |
| `e105104` | Cancelamento **Deferido** | `cancelada` |
| `e105105` | Cancelamento **Indeferido** | **nenhum** — a nota segue válida |
| `e202201`, `e203202`, `e204203` | Confirmações | nenhum |
| `e205204` | Confirmação Tácita | nenhum |
| `e202205`, `e203206`, `e204207` | Rejeições | nenhum |
| `e205208` | Anulação de Rejeição | nenhum |
| `e305101` | Cancelamento por Ofício | `cancelada` |
| `e305102` | Bloqueio por Ofício | **nenhum** — vai para `restricao_eventos` (§10.5) |
| `e305103` | Desbloqueio por Ofício | **nenhum** — idem |

> ⚠️ **O trio `e101103` / `e105104` / `e105105` é a armadilha do `cStat` outra
> vez:** pedido, deferimento e indeferimento. Quem mapear a **solicitação** como
> cancelamento vai cancelar nota que a prefeitura **recusou** cancelar.

O envelope JSON traz `TipoEvento` (enum do Swagger). **O envelope é oficial; o
nome do elemento é conferência cruzada. Divergência → quarentena.**

### 10.2 O mapa tem TRÊS estados, não dois

```python
MAPA_EVENTO_SITUACAO = {
    'CANCELAMENTO': 'cancelada',
    'CANCELAMENTO_POR_SUBSTITUICAO': 'substituida',
    'CANCELAMENTO_DEFERIDO_ANALISE_FISCAL': 'cancelada',
    'CANCELAMENTO_POR_OFICIO': 'cancelada',
    'SOLICITACAO_CANCELAMENTO_ANALISE_FISCAL': None,   # conhecido, sem efeito
    'CANCELAMENTO_INDEFERIDO_ANALISE_FISCAL': None,
    'CONFIRMACAO_PRESTADOR': None, ...                  # manifestações
    # tipo AUSENTE do mapa -> revisar=1
}
```

- chave com **valor** → altera `situacao`
- chave com **`None`** → conhecido e sem efeito, `revisar=0`
- chave **ausente** → desconhecido, `revisar=1`

Sem o estado `None`, toda Confirmação cairia em revisão e o painel viraria ruído
— o jeito mais rápido de fazer um alerta ser ignorado.

### 10.3 `recalcular_situacao()` — ESCRITOR ÚNICO

```python
PRECEDENCIA = {"ativa": 0, "cancelada": 1, "substituida": 2}

def recalcular_situacao(chave_acesso: str) -> str:
    """UNICA funcao autorizada a escrever nfse_capturadas.situacao.

    ESCOPO DO UPDATE — intencional: atualiza TODAS as linhas desta chave, sem
    filtrar por papel. Nota com 2 papeis na carteira recebe a mesma situacao
    nas duas: o cancelamento e fato do DOCUMENTO, nao da parte.

    NAO USE rowcount PARA NADA:
      - 2 linhas afetadas e normal;
      - 0 linhas e normal (situacao ja era a calculada — o MySQL nao conta
        linha inalterada como afetada);
      - 0 linhas tambem ocorre quando o documento nao foi capturado.
    Para saber se existe, consulte antes com existe_documento().

    Deterministica e idempotente.
    """
```

**Precedência `substituida > cancelada > ativa`. Uma vez fora de `ativa`, não
volta** — a precedência garante isso sem guardar estado.

Nenhum outro trecho escreve `situacao`. **Verificar por busca no código.**

### 10.4 Fluxo — os dois sentidos cobertos

```python
def salvar_documento(doc, empresa_id, cnpj):
    upsert_nfse_capturadas(parser.para_registro(...))   # entra SEMPRE 'ativa'
    if existem_eventos(chave):                          # órfão que já esperava
        recalcular_situacao(chave)
        marcar_eventos_aplicados(chave, apenas_tipos=MAPA_EVENTO_SITUACAO.keys())
    db.commit()                                         # ANTES de avançar NSU

def salvar_evento(ev, empresa_id, cnpj):
    registro['revisar'] = 0 if ev.tipo in MAPA_EVENTO_SITUACAO else 1
    registro['orfao'] = 0 if existe_documento(chave) else 1
    upsert_nfse_eventos(registro)                       # grava SEMPRE
    if not registro['orfao']:
        recalcular_situacao(chave)
        marcar_evento_aplicado(registro['id'])
    db.commit()
```

`marcar_eventos_aplicados` **restrito aos tipos do mapa** — senão os
`revisar=1` seriam achatados como aplicados e o sinal de revisão morreria calado.

### 10.5 Bloqueio NÃO é estado da nota

**Verificado no XSD:** `TSCodigoEventoNFSe` é enumeração de exatamente cinco
valores — `e101101`, `e105102`, `e105104`, `e105105`, `e305101`. Ou seja, o
bloqueio de ofício só restringe **cancelamentos**.

Não é "nota bloqueada": é o município **impedindo que a nota seja cancelada**. A
nota segue ativa, válida e vale como documento fiscal.

Por isso vive em eixo próprio (`restricao_eventos`, `restricao_codigos`,
`restricao_em`), **nunca em `situacao`** 🔶. E `restricao_codigos` tem domínio
fechado de cinco valores — validar na gravação, não aceitar texto livre.

**Na tela: "restrição de eventos", nunca "bloqueada".** Rotular de bloqueada faria
o escritório tratar como problema o que é procedimento do município.

### 10.6 Detalhes que o modelo não expressa

- `e305103/idBloqOfic` referencia o bloqueio que anula — par reversível.
- `e205208/idEvManifRej` referencia a manifestação anulada — **existe evento que
  desfaz outro evento**. Não afeta o cálculo hoje (manifestação não altera
  `situacao`), mas o modelo "eventos só somam" não expressa isso. Registrado
  antes que alguém mapeie manifestação para algum estado.

---

## 11. Manutenção do mapa

O mapa é **código**; os eventos são **dados**. Mexer no mapa não reavalia nada do
que já foi gravado.

### 11.1 Guarda de mapa vazio — falhar alto

```python
class MapaVazioError(Exception):
    """MAPA_EVENTO_SITUACAO vazio. Provavel falha de import."""

def _guarda_mapa() -> tuple:
    tipos = tuple(MAPA_EVENTO_SITUACAO.keys())
    if not tipos:
        raise MapaVazioError("MAPA vazio. Abortado sem alterar nada.")
    return tipos
```

Com o mapa vazio, `NOT IN (NULL)` **nunca é verdadeiro** — a consulta volta zero
linhas exatamente quando tudo deveria ser rebaixado, e o retorno pareceria
sucesso. **Exceção, não dict de erro:** dict pode ser ignorado por quem chama.

### 11.2 `sincronizar_revisar()` — as duas direções, commit por chave

- **promover:** `revisar=1` e o tipo **agora** está no mapa → transição completa
  (`revisar=0` + `orfao`/`aplicado` conforme o documento exista) + recalcula.
- **rebaixar:** `revisar=0` e o tipo **não está mais** no mapa → `revisar=1`,
  `aplicado=0`.

**Commit por chave**, com `rollback + continue` — uma chave problemática não
derruba o lote, mesmo espírito do isolamento por empresa.

Não precisa de lógica de "desfazer": `recalcular_situacao()` recalcula do zero e
ignora tipos fora do mapa, então o efeito do tipo removido some sozinho.

### 11.3 `recalcular_lote(tipos_alterados)`

Para quando o **valor** de um tipo já conhecido é corrigido — essas linhas têm
`revisar=0, aplicado=1` e são invisíveis para a função anterior. Rejeita lista
vazia pelo mesmo motivo da guarda.

### 11.4 Truncamento explícito

Buscar `limite + 1` para **detectar** o truncamento e devolver `truncado`.

> **A interface precisa mostrar `truncado` e `falhas` em destaque**, não enterrar
> no JSON de retorno. Retorno que parece sucesso quando parou no limite é a
> mesma falha silenciosa que este documento inteiro vem eliminando.

### 11.5 Procedimento operacional

| Mudança no mapa | Ação |
|---|---|
| Adicionou tipo (com valor ou `None`) | `sincronizar_revisar()` |
| **Removeu** tipo | `sincronizar_revisar()` |
| Corrigiu o valor de um tipo existente | `recalcular_lote([tipos])` |

Rodar **até `truncado=False`**. Ambas idempotentes; na dúvida, rode as duas.
Depois conferir no painel: **Revisão** deve cair, **Órfãos** pode subir.

### 11.6 Métricas de saúde — duas, separadas

| Métrica | Query | Significa |
|---|---|---|
| **Órfãos** | `orfao=1 AND revisar=0` | evento conhecido cujo documento não chegou. Esperado se o município está fora do ADN; crescimento persistente → investigar o mapeamento de `chave_referenciada` |
| **Revisão** | `revisar=1` | tipo não mapeado. **Sempre requer ação humana** |

---

## 12. Erros e quarentena 🔶

| Situação | Ação |
|---|---|
| Certificado vencido / não abre | marca empresa `ativo=0`, grava erro, **continua as demais** |
| 401/403 | **não repete**; grava erro. Provável: raiz de CNPJ diferente |
| 5xx / timeout | `client.py` já faz 3 tentativas com backoff; esgotou, próxima empresa |
| base64 / gzip / UTF-8 / schema inválido | **quarentena + cursor AVANÇA**; grava `raw_json` mesmo assim |
| `papel` não identificado | quarentena |
| Falha ao gravar no banco | rollback, **cursor NÃO avança**, aborta a empresa |
| Falha ao salvar XML no Dropbox | grava o registro, `xml_path=NULL`, job separado reprocessa |

**Princípio:** erro de uma empresa nunca derruba a execução das outras.
Documento malformado não trava a fila — mas fica registrado, nunca descartado.

**Validar contra o XSD antes de parsear.** Com quatro nomes duplicados, o schema
elimina uma classe inteira de erro; a planilha descreve, o XSD valida.

---

## 13. Cron

```
Railway Cron Service — a cada 3 horas
python -m utils.integrations.nfse_adn.captura --modo incremental
```

**Serviço separado do DFe**, de preferência: falha do NFS-e não pode derrubar a
captura de NF-e, que já está estável em produção.

---

## 14. Tela — fase posterior

Não implementar junto com a captura; spec própria depois da captura estável.

> ### A TELA NASCE IGUAL ÀS DE ENTRADAS E SAÍDAS (decidido pelo Anderson, 15/08/2026)
>
> Não é tela nova inventada — é o mesmo molde que o escritório já usa todo dia:
> cartões de indicadores no topo, bloco de **Filtros**, abas **Notas Fiscais /
> Por Emissor / Por Produto / Chaves XML**, e os botões **Baixar XMLs**,
> **Gerar PDF**, **Exportar** e **Excluir em Lote**. Cada nota como cartão, com
> os mesmos selos.
>
> O que muda é só o conteúdo, e cabe no molde sem forçar:
> * no lugar de ICMS/PIS/COFINS, os indicadores são **ISS, retenções e valor
>   líquido**;
> * o **papel** (emitida × tomada) vira filtro, como o tipo hoje;
> * o período filtra por **competência** (`dCompet`), não por data de emissão —
>   serviço de dezembro emitido em janeiro pertence a dezembro.
>
> Motivo: quem opera a Escrita Fiscal já sabe usar aquela tela. Uma tela com
> outra lógica obrigaria a reaprender o que já é sabido, e o NFS-e não tem nada
> de tão diferente que justifique isso.

Escopo previsto: aba **NFS-e à direita do CT-e**, listagem por competência e
papel, filtro por empresa, download de XML, e **conciliação com os lançamentos
manuais** — que é onde o valor aparece para o escritório.

**Permissão `escrita_fiscal.nfse` criada desde já e NÃO vinculada a perfil
nenhum.** Admin enxerga por `has_permission`; liberar depois é um clique na tela
de Perfis. Nada de `@admin_required` — é dívida que se paga com juros, lição da
Caixa de entrada.

As ações administrativas (`sincronizar_revisar`, `recalcular_lote`,
`reconciliar_situacoes`) ficam sob a mesma permissão.

---

## 15. Checklist de aceitação

**Fase 0**
- [ ] Executada e resultado aprovado pelo Anderson
- [ ] Certificado da raiz testado com matriz e filial reais

**Captura**
- [ ] Backfill completo de 1 empresa em produção restrita
- [ ] Queda simulada no meio → retoma do NSU correto, sem duplicata
- [ ] Documento já capturado reprocessa sem duplicar (`ON DUPLICATE KEY`)
- [ ] Empresa com certificado vencido não interrompe as demais
- [ ] `resolver_certificado` usa o mesmo filtro nos dois passos
- [ ] Parada por `NENHUM_DOCUMENTO_LOCALIZADO`; `REJEICAO` não avança cursor
- [ ] `TipoAmbiente` conferido em toda resposta

**Parser**
- [ ] Todo XPath absoluto — nenhuma busca por nome de elemento
- [ ] `CST` do PIS/COFINS nunca confundido com o do IBS/CBS
- [ ] Os dois grupos `IBSCBS` mapeados separadamente
- [ ] IBS-UF e IBS-Município em colunas separadas
- [ ] `papel` de `prest`/`toma`/`interm`, **nunca** de `emit`
- [ ] `dest` gravado como dado, **nunca** como papel
- [ ] `CNPJ|CPF|NIF` tratados como escolha nos cinco grupos
- [ ] Monetários em `Decimal`; `0-1` ausente → `None`
- [ ] `dCompet` na apuração, não `dhEmi`
- [ ] `cStat` informativo; `101` = esta nota É a substituta
- [ ] `chSubstda` e `chSubstituta` com sentidos documentados no código

**Eventos**
- [ ] Evento antes do documento → órfão, aplicado quando o documento chega
- [ ] Evento depois → aplicado na hora
- [ ] Nota com 2 papéis → cancelamento atualiza **as duas** linhas
- [ ] Documento cancelado **nunca** volta a `ativa`
- [ ] Reprocessar o mesmo NSU não duplica (`uk_evento`)
- [ ] `SOLICITACAO_...` não cancela; `INDEFERIDO` não cancela
- [ ] Manifestação → `revisar=0`, `situacao` inalterada
- [ ] Tipo ausente do mapa → `revisar=1`, não marcado como aplicado
- [ ] `recalcular_situacao()` é o **único** escritor — verificar por busca
- [ ] Nenhum ponto usa `rowcount` dela
- [ ] Bloqueio em `restricao_eventos`; tela não diz "bloqueada"

**Manutenção do mapa**
- [ ] Mapa vazio → **estoura**, não retorna zeros
- [ ] `sincronizar_revisar()` trata promover e rebaixar
- [ ] Commit por chave; falha numa não derruba as demais
- [ ] Retorno traz `truncado` e `falhas`; a tela exibe os dois
- [ ] Ambas rodam 2× sem alterar o resultado

**Geral**
- [ ] `nfse_consulta_log` preenchido em toda execução, inclusive nas que falham
- [ ] **Nenhum evento de manifestação enviado** — confirmar por inspeção
- [ ] Migração roda 2× sem erro
- [ ] Painel expõe Órfãos e Revisão separados

---

## 16. O que este documento NÃO cobre

- ❌ **Emissão de NFS-e** → Documento B. Fica registrado que a emissão exige
  `CST`, `cClassTrib`, `finNFSe`, `cIndOp` e `indDest` obrigatórios no grupo
  declarado, e que o prazo do Emissor Nacional para ME/EPP do Simples é
  **01/09/2026**. O Anderson priorizou a captura sobre a emissão em 14/08/2026 —
  a reavaliação do cronograma do B precisa considerar que ele não está mais na
  frente da fila.
- ❌ **Manifestação** → proibida, §0.3.
- ❌ **Notas de municípios não integrados ao ADN** → limitação da fonte, sem
  solução técnica do nosso lado.
- ❌ **Tela do Fiscal** → §14, spec própria.
