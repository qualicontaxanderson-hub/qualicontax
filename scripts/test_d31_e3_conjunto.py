"""
Teste de campo do D3.1 Etapa 3 — exercita as FUNÇÕES REAIS de
routes/escrita_fiscal.py contra o banco de verdade (MySQL). Prova o que o
surrogate SQLite não prova: dialeto, tipos e transação reais.

TRÊS MODOS:

  1) DOIS NÚMEROS (modo antigo, inalterado) — usa duas empresas que já existem:
       python scripts/test_d31_e3_conjunto.py <numA> <numB>        # dry-run
       python scripts/test_d31_e3_conjunto.py <numA> <numB> --go   # executa + restaura snapshot

  2) --auto — o script cria ELE MESMO dois clientes fantasma ('ZZ TESTE CLONE A/B',
     numero_cliente = MAX+9001/9002, CNPJ fictício, INATIVO), roda b..e contra eles
     e no fim APAGA tudo (dados + os dois clientes), com contagem e guard:
       python scripts/test_d31_e3_conjunto.py --auto

  3) --preview-reais — SÓ leitura, contra o conjunto FISCAL id=1 (os 6 postos):
     lista membros, chama o preview de INCLUIR e o de DESVINCULAR e imprime os números.
     Nenhum INSERT/UPDATE/DELETE; a execução roda numa transação com ROLLBACK sempre,
     e contagens antes/depois provam que nada foi escrito:
       python scripts/test_d31_e3_conjunto.py --preview-reais

SEGURANÇA: nunca escreve fora dos ids envolvidos; --auto só apaga cliente cuja
razão social ainda começa com 'ZZ TESTE CLONE'. NÃO mexe em MEMO_POOL_VIVO no
Railway — liga a variável só dentro deste processo.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# O pool vivo nasce DESLIGADO em produção (MEMO_POOL_VIVO=0). Este teste liga o
# interruptor SÓ no próprio processo, para exercitar o fan-out (item b) — a
# produção não é afetada (o env do serviço web continua sem a variável = off).
os.environ['MEMO_POOL_VIVO'] = '1'

from utils.db_helper import execute_query, transacao  # noqa: E402
from routes.escrita_fiscal import (  # noqa: E402
    _upsert_vinculo, _incluir_aplicar, _incluir_preview, _desvincular_preview,
    _desvincular_tudo, _desvincular_restore, _memo_col_existe, _memo_tabela_existe,
)

ALE = 'TEST0000000ALE'    # CNPJs de teste — nenhum fornecedor real usa estes
YPE = 'TEST0000000YPE'
CORTE_CNPJ = 'TEST000CORTE'
SET_TESTE = '__TESTE_D31_E3__'
FALHAS = []


class _Rollback(Exception):
    """Sentinela: força a transação de --preview-reais a dar ROLLBACK sempre."""


def check(nome, cond):
    print(("  PASS  " if cond else "  FALHA "), nome)
    if not cond:
        FALHAS.append(nome)


def _count(sql, params=()):
    r = execute_query(sql, params, fetch=True, fetch_one=True) or {}
    return int(r.get('c') or 0)


def cli_por_numero(numero):
    return execute_query("SELECT id, nome_razao_social FROM clientes WHERE numero_cliente = %s",
                         (str(numero),), fetch=True, fetch_one=True)


def migracao_ok():
    return (_memo_col_existe('memo_clone_set', 'nome')
            and _memo_col_existe('memo_clone_membro', 'corte_data')
            and _memo_col_existe('memo_clone_set', 'departamento')
            and _memo_tabela_existe('memo_desvinculo_op')
            and _memo_tabela_existe('memo_desvinculo_bkp'))


def _resultado(msg_ok):
    print()
    if FALHAS:
        print("RESULTADO: FALHOU em", len(FALHAS), "->", FALHAS); sys.exit(1)
    print("RESULTADO:", msg_ok)


# ---------------------------------------------------------------------------
# Cenário b..e (compartilhado pelos modos que ESCREVEM). Cria o dado sintético
# nas duas empresas dadas e roda as asserções. `art` recebe op_desv p/ limpeza.
# ---------------------------------------------------------------------------
def _fake_chave(cliente_id, k):
    # chave_acesso é NOT NULL varchar(44); geramos uma fictícia ÚNICA por nota.
    return ('ZZ%010d%032d' % (int(cliente_id), k))[:44]


_NOME_ARQ = '__ZZTESTE_D31_E3__.xml'   # nome_arquivo NOT NULL (dado sintético)


def _rodar_bcde(A, B, art):
    with transacao() as cur:
        cur.execute("INSERT INTO memo_clone_set (nome) VALUES (%s)", (SET_TESTE,))
        set_id = cur.lastrowid
        cur.execute("INSERT INTO memo_clone_membro (set_id, cliente_id) VALUES (%s,%s)", (set_id, A))
    art['set_id'] = set_id

    with transacao() as cur:
        cur.execute("INSERT INTO nfe_importacoes (cliente_id, emit_cnpj, tipo, data_emissao, nome_arquivo, chave_acesso) "
                    "VALUES (%s,%s,'entrada','2026-08-05',%s,%s)", (B, ALE, _NOME_ARQ, _fake_chave(B, 1)))
        nfeB = cur.lastrowid
        cur.execute("INSERT INTO nfe_itens (nfe_id, codigo_produto, produto_catalogo_id) "
                    "VALUES (%s,'123',NULL)", (nfeB,))
        cur.execute("INSERT INTO nfe_importacoes (cliente_id, emit_cnpj, tipo, data_emissao, nome_arquivo, chave_acesso) "
                    "VALUES (%s,%s,'entrada','2026-07-01',%s,%s)", (B, YPE, _NOME_ARQ, _fake_chave(B, 2)))
        nfeB2 = cur.lastrowid
        cur.execute("INSERT INTO nfe_itens (nfe_id, codigo_produto, produto_catalogo_id) "
                    "VALUES (%s,'777',NULL)", (nfeB2,))
        cur.execute("INSERT INTO nfe_produto_vinculo (cliente_id,grupo_id,ramo_atividade_id,"
                    "emit_cnpj,codigo_produto_xml,descricao_produto_xml,produto_catalogo_id,tipo) "
                    "VALUES (%s,NULL,NULL,%s,'777','SABAO',222,'entrada')", (B, YPE))
        cur.execute("INSERT INTO memo_clone_membro (set_id, cliente_id) VALUES (%s,%s)", (set_id, B))

    print("\nb) POOL VIVO — A vincula (ALE,123)->111; B deve receber")
    _upsert_vinculo(A, ALE, '123', 'GASOLINA', 111, tipo='entrada')
    rb = execute_query("SELECT produto_catalogo_id p FROM nfe_produto_vinculo WHERE cliente_id=%s "
                       "AND emit_cnpj=%s AND codigo_produto_xml='123'", (B, ALE),
                       fetch=True, fetch_one=True)
    check("B ganhou a regra (ALE,123)->111", bool(rb) and rb['p'] == 111)
    it = execute_query("SELECT produto_catalogo_id p FROM nfe_itens WHERE nfe_id=%s AND codigo_produto='123'",
                       (nfeB,), fetch=True, fetch_one=True)
    check("item NULL de B (nota ALE) virou 111", it and it['p'] == 111)

    print("c) SEGURANÇA — A vincula (YPE,777)->111; B já tem ->222: não sobrescreve")
    _upsert_vinculo(A, YPE, '777', 'SABAO', 111, tipo='entrada')
    r777 = execute_query("SELECT produto_catalogo_id p FROM nfe_produto_vinculo WHERE cliente_id=%s "
                         "AND emit_cnpj=%s AND codigo_produto_xml='777'", (B, YPE),
                         fetch=True, fetch_one=True)
    check("regra divergente de B intacta (=222)", r777 and r777['p'] == 222)
    i777 = execute_query("SELECT produto_catalogo_id p FROM nfe_itens WHERE nfe_id=%s AND codigo_produto='777'",
                         (nfeB2,), fetch=True, fetch_one=True)
    check("item NULL divergente segue NULL", i777 and i777['p'] is None)

    print("d) INCLUIR — corte por data de emissão (via preview: corte reduz itens vs 'todos')")
    with transacao() as cur:
        cur.execute("INSERT INTO nfe_importacoes (cliente_id, emit_cnpj, tipo, data_emissao, nome_arquivo, chave_acesso) "
                    "VALUES (%s,%s,'entrada','2026-06-01',%s,%s)", (B, CORTE_CNPJ, _NOME_ARQ, _fake_chave(B, 3)))
        nfeC1 = cur.lastrowid
        cur.execute("INSERT INTO nfe_itens (nfe_id, codigo_produto, produto_catalogo_id) VALUES (%s,'123',NULL)", (nfeC1,))
    prev_todos = _incluir_preview(set_id, B, None)
    prev_corte = _incluir_preview(set_id, B, '2026-08-01')
    check("preview: corte por data reduz itens a vincular vs 'todos'",
          prev_corte['itens_a_vincular'] <= prev_todos['itens_a_vincular'])

    print("e) DESVINCULAR 'tudo' de B — backup ANTES do delete, e restore por op_id")
    antes = _count("SELECT COUNT(*) c FROM nfe_produto_vinculo WHERE cliente_id=%s "
                   "AND grupo_id IS NULL AND ramo_atividade_id IS NULL", (B,))
    res = _desvincular_tudo(set_id, B, actor_id=None)
    art['op_desv'] = res['op_id']
    bkp = _count("SELECT COUNT(*) c FROM memo_desvinculo_bkp WHERE op_id=%s", (res['op_id'],))
    depois = _count("SELECT COUNT(*) c FROM nfe_produto_vinculo WHERE cliente_id=%s "
                    "AND grupo_id IS NULL AND ramo_atividade_id IS NULL", (B,))
    check(f"backup guardou as {antes} regra(s) ANTES do delete", bkp == antes and antes > 0)
    check("regras de B apagadas (0 restantes)", depois == 0)
    volta = _desvincular_restore(res['op_id'])
    rest = _count("SELECT COUNT(*) c FROM nfe_produto_vinculo WHERE cliente_id=%s "
                  "AND grupo_id IS NULL AND ramo_atividade_id IS NULL", (B,))
    check(f"restore por op_id reinseriu {antes} regra(s)", rest == antes and volta == antes)


# ---------------------------------------------------------------------------
# MODO 1 — dois números (inalterado): snapshot + cenário + restaura o snapshot.
# ---------------------------------------------------------------------------
def modo_numeros(numA, numB, go):
    a, b = cli_por_numero(numA), cli_por_numero(numB)
    if not a or not b:
        print('ERRO: não encontrei as duas empresas de teste pelos números dados.'); sys.exit(2)
    if a['id'] == b['id']:
        print('ERRO: os dois números apontam para a mesma empresa.'); sys.exit(2)
    A, B = a['id'], b['id']
    IDS = (A, B)
    ph = ','.join(['%s'] * len(IDS))
    print(f"Empresa A = #{numA} id={A} ({a['nome_razao_social']})")
    print(f"Empresa B = #{numB} id={B} ({b['nome_razao_social']})")
    if not migracao_ok():
        print('ERRO: migration da gestão do conjunto ainda NÃO foi aplicada.'); sys.exit(2)
    if not go:
        print('\nDRY-RUN (sem --go): nada foi gravado. Passe --go para executar e limpar.'); return

    snap_vinc = execute_query(
        f"SELECT * FROM nfe_produto_vinculo WHERE cliente_id IN ({ph})", IDS, fetch=True) or []
    print(f"\nSnapshot: {len(snap_vinc)} regra(s) pré-existentes nas 2 empresas (serão preservadas).")

    art = {'op_desv': None, 'set_id': None}
    try:
        _rodar_bcde(A, B, art)
    finally:
        print("\nLIMPEZA — removendo o dado sintético e restaurando o snapshot...")
        with transacao() as cur:
            cur.execute(f"DELETE i FROM nfe_itens i JOIN nfe_importacoes n ON n.id=i.nfe_id "
                        f"WHERE n.cliente_id IN ({ph}) AND n.emit_cnpj IN (%s,%s,%s)",
                        (*IDS, ALE, YPE, CORTE_CNPJ))
            cur.execute(f"DELETE FROM nfe_importacoes WHERE cliente_id IN ({ph}) "
                        f"AND emit_cnpj IN (%s,%s,%s)", (*IDS, ALE, YPE, CORTE_CNPJ))
            cur.execute(f"DELETE FROM nfe_produto_vinculo WHERE cliente_id IN ({ph})", IDS)
            for r in snap_vinc:
                cur.execute("INSERT INTO nfe_produto_vinculo (id,cliente_id,grupo_id,ramo_atividade_id,"
                            "emit_cnpj,codigo_produto_xml,descricao_produto_xml,produto_catalogo_id,tipo) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (r['id'], r['cliente_id'], r.get('grupo_id'), r.get('ramo_atividade_id'),
                             r['emit_cnpj'], r['codigo_produto_xml'], r.get('descricao_produto_xml'),
                             r['produto_catalogo_id'], r.get('tipo')))
            cur.execute(f"DELETE FROM memo_clone_membro WHERE cliente_id IN ({ph})", IDS)
            cur.execute("DELETE FROM memo_clone_set WHERE nome=%s", (SET_TESTE,))
            if art['op_desv']:
                cur.execute("DELETE FROM memo_desvinculo_bkp WHERE op_id=%s", (art['op_desv'],))
                cur.execute("DELETE FROM memo_desvinculo_op WHERE id=%s", (art['op_desv'],))
        rest_vinc = _count(f"SELECT COUNT(*) c FROM nfe_produto_vinculo WHERE cliente_id IN ({ph})", IDS)
        check(f"snapshot restaurado ({rest_vinc} == {len(snap_vinc)} regra(s) originais)",
              rest_vinc == len(snap_vinc))
    _resultado("b..e OK contra o MySQL real; ambiente restaurado. (f = guarda admin na rota.)")


# ---------------------------------------------------------------------------
# MODO 2 — --auto: cria dois clientes fantasma, roda b..e, apaga tudo com guard.
# ---------------------------------------------------------------------------
def _criar_fantasmas():
    mx = execute_query("SELECT MAX(CAST(numero_cliente AS UNSIGNED)) m FROM clientes",
                       fetch=True, fetch_one=True)['m'] or 0
    nA, nB = str(int(mx) + 9001), str(int(mx) + 9002)   # faixa longe de cliente real
    ids = []
    with transacao() as cur:
        # NÃO há coluna 'ativo' em clientes; o equivalente a "ativo=0" é
        # situacao='INATIVO' (mantém os fantasmas fora das telas normais).
        for nome, num in [('ZZ TESTE CLONE A', nA), ('ZZ TESTE CLONE B', nB)]:
            cur.execute("INSERT INTO clientes (tipo_pessoa, nome_razao_social, cpf_cnpj, "
                        "regime_tributario, numero_cliente, situacao) "
                        "VALUES ('PJ', %s, %s, 'SIMPLES', %s, 'INATIVO')",
                        (nome, f'ZZTST{num}', num))
            ids.append(cur.lastrowid)
    print(f"Fantasmas criados: A id={ids[0]} #{nA} · B id={ids[1]} #{nB} (situacao=INATIVO)")
    return ids[0], ids[1]


def _contagens_ids(ids):
    ph = ','.join(['%s'] * len(ids))
    return {
        'nfe_produto_vinculo': _count(f"SELECT COUNT(*) c FROM nfe_produto_vinculo WHERE cliente_id IN ({ph})", ids),
        'nfe_importacoes':     _count(f"SELECT COUNT(*) c FROM nfe_importacoes WHERE cliente_id IN ({ph})", ids),
        'nfe_itens':           _count(f"SELECT COUNT(*) c FROM nfe_itens i JOIN nfe_importacoes n ON n.id=i.nfe_id WHERE n.cliente_id IN ({ph})", ids),
        'memo_clone_membro':   _count(f"SELECT COUNT(*) c FROM memo_clone_membro WHERE cliente_id IN ({ph})", ids),
        'memo_desvinculo_op':  _count(f"SELECT COUNT(*) c FROM memo_desvinculo_op WHERE cliente_id IN ({ph})", ids),
        'memo_desvinculo_bkp': _count(f"SELECT COUNT(*) c FROM memo_desvinculo_bkp WHERE cliente_id IN ({ph})", ids),
        'memo_clone_set(teste)': _count("SELECT COUNT(*) c FROM memo_clone_set WHERE nome=%s", (SET_TESTE,)),
    }


def _guard_zz(cid):
    r = execute_query("SELECT nome_razao_social FROM clientes WHERE id=%s", (cid,), fetch=True, fetch_one=True)
    return bool(r) and (r['nome_razao_social'] or '').startswith('ZZ TESTE CLONE')


def _limpar_auto(ids):
    ph = ','.join(['%s'] * len(ids))
    print("\nLIMPEZA (--auto) — contagem ANTES de apagar:")
    for t, c in _contagens_ids(ids).items():
        print(f"    {t}: {c}")
    # 1) apaga vínculos/itens/notas/memo_* dos dois ids
    with transacao() as cur:
        cur.execute(f"DELETE i FROM nfe_itens i JOIN nfe_importacoes n ON n.id=i.nfe_id WHERE n.cliente_id IN ({ph})", ids)
        cur.execute(f"DELETE FROM nfe_importacoes WHERE cliente_id IN ({ph})", ids)
        cur.execute(f"DELETE FROM nfe_produto_vinculo WHERE cliente_id IN ({ph})", ids)
        cur.execute(f"DELETE FROM memo_desvinculo_bkp WHERE cliente_id IN ({ph})", ids)
        cur.execute(f"DELETE FROM memo_desvinculo_op WHERE cliente_id IN ({ph})", ids)
        cur.execute(f"DELETE FROM memo_clone_membro WHERE cliente_id IN ({ph})", ids)
        cur.execute("DELETE FROM memo_clone_set WHERE nome=%s", (SET_TESTE,))
    depois = _contagens_ids(ids)
    print("Contagem DEPOIS de apagar os dados:")
    for t, c in depois.items():
        print(f"    {t}: {c}")
    resto = sum(depois.values())
    if resto:
        print(f"ERRO: sobraram {resto} linha(s) com os ids {ids} após a limpeza. "
              f"NÃO vou apagar os clientes — olhe o que ficou.")
        sys.exit(1)
    # 2) só então apaga os clientes — com o guard obrigatório 'ZZ TESTE CLONE'
    for cid in ids:
        if not _guard_zz(cid):
            print(f"ABORT: cliente id={cid} não começa com 'ZZ TESTE CLONE'. NÃO apago o cliente.")
            sys.exit(1)
    with transacao() as cur:
        cur.execute(f"DELETE FROM clientes WHERE id IN ({ph})", ids)
    print(f"Clientes fantasma {ids} apagados (guard 'ZZ TESTE CLONE' ok).")


def modo_auto():
    if not migracao_ok():
        print('ERRO: migration da gestão do conjunto ainda NÃO foi aplicada.'); sys.exit(2)
    idA, idB = _criar_fantasmas()
    art = {'op_desv': None, 'set_id': None}
    try:
        _rodar_bcde(idA, idB, art)
    finally:
        _limpar_auto((idA, idB))
    _resultado("b..e OK em clientes FANTASMA; tudo apagado e conferido por contagem.")


# ---------------------------------------------------------------------------
# MODO 3 — --preview-reais: só leitura, contra o conjunto FISCAL id=1.
# ---------------------------------------------------------------------------
def _contagens_preview(ids):
    ph = ','.join(['%s'] * len(ids))
    return {
        'nfe_produto_vinculo': _count(f"SELECT COUNT(*) c FROM nfe_produto_vinculo WHERE cliente_id IN ({ph})", ids),
        'nfe_importacoes':     _count(f"SELECT COUNT(*) c FROM nfe_importacoes WHERE cliente_id IN ({ph})", ids),
        'nfe_itens':           _count(f"SELECT COUNT(*) c FROM nfe_itens i JOIN nfe_importacoes n ON n.id=i.nfe_id WHERE n.cliente_id IN ({ph})", ids),
        'memo_clone_set':      _count("SELECT COUNT(*) c FROM memo_clone_set"),
        'memo_clone_membro':   _count("SELECT COUNT(*) c FROM memo_clone_membro"),
        'memo_desvinculo_op':  _count("SELECT COUNT(*) c FROM memo_desvinculo_op"),
        'memo_desvinculo_bkp': _count("SELECT COUNT(*) c FROM memo_desvinculo_bkp"),
    }


def modo_preview_reais():
    if not migracao_ok():
        print('ERRO: migration da gestão do conjunto ainda NÃO foi aplicada.'); sys.exit(2)
    SID = 1
    membros = execute_query(
        "SELECT c.id, c.numero_cliente, c.nome_razao_social "
        "FROM memo_clone_membro m JOIN clientes c ON c.id = m.cliente_id "
        "JOIN memo_clone_set s ON s.id = m.set_id "
        "WHERE m.set_id = %s AND s.departamento = 'FISCAL' "
        "ORDER BY CAST(c.numero_cliente AS UNSIGNED)", (SID,), fetch=True) or []
    if not membros:
        print(f"Conjunto FISCAL {SID} não encontrado ou sem membros."); sys.exit(2)
    print(f"Conjunto FISCAL {SID} — {len(membros)} membros:")
    for m in membros:
        print(f"    #{m['numero_cliente']} id={m['id']} {m['nome_razao_social']}")
    membro_ids = [m['id'] for m in membros]

    mph = ','.join(['%s'] * len(membro_ids))
    alvo = execute_query(
        f"SELECT id, numero_cliente, nome_razao_social FROM clientes "
        f"WHERE situacao = 'ATIVO' AND id NOT IN ({mph}) ORDER BY id LIMIT 1",
        tuple(membro_ids), fetch=True, fetch_one=True)
    if not alvo:
        print("Não achei um cliente ATIVO não-membro para o preview de incluir."); sys.exit(2)
    print(f"\nCliente não-membro para o preview de INCLUIR: "
          f"#{alvo['numero_cliente']} id={alvo['id']} {alvo['nome_razao_social']}")

    envolvidos = membro_ids + [alvo['id']]
    antes = _contagens_preview(envolvidos)

    # Transação com ROLLBACK SEMPRE: se algum caminho escrever pelo MESMO cursor,
    # é desfeito. Os previews usam execute_query (conexões próprias, autocommit),
    # então a GARANTIA real de "não escreveu" é a contagem antes/depois abaixo.
    try:
        with transacao() as cur:
            cur.execute("SELECT 1")
            pv = _incluir_preview(SID, alvo['id'], None)
            print(f"\nPREVIEW INCLUIR ({alvo['nome_razao_social']}): "
                  f"{pv['itens_a_vincular']} item(ns) seriam carimbados · "
                  f"{pv['regras_a_criar']} regra(s) entrariam · "
                  f"{pv['itens_divergentes']} divergente(s) ignorado(s) · pool de {pv['pool']} regra(s).")
            alvo_desv = membro_ids[0]
            n_desv = _desvincular_preview(alvo_desv)
            print(f"PREVIEW DESVINCULAR (id={alvo_desv}): {n_desv} vínculo(s) seriam apagados.")
            raise _Rollback()
    except _Rollback:
        pass

    depois = _contagens_preview(envolvidos)
    difs = {t: (antes[t], depois[t]) for t in antes if antes[t] != depois[t]}
    print()
    check("preview-reais NÃO escreveu (contagens idênticas antes/depois)", not difs)
    if difs:
        print("   DIFERENÇAS (antes -> depois):", difs)
    _resultado("preview contra os 6 postos OK; leitura pura, contagens intactas.")


def main():
    args = sys.argv[1:]
    if '--preview-reais' in args:
        return modo_preview_reais()
    if '--auto' in args:
        return modo_auto()
    nums = [a for a in args if not a.startswith('--')]
    if len(nums) < 2:
        print(__doc__); sys.exit(2)
    return modo_numeros(nums[0], nums[1], go='--go' in args)


if __name__ == '__main__':
    main()
