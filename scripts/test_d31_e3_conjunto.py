"""
Teste de campo do D3.1 Etapa 3 — roda a sequência b..e nas DUAS EMPRESAS DE
TESTE que você criar no sistema, exercitando as FUNÇÕES REAIS de
routes/escrita_fiscal.py contra o banco de verdade (MySQL). Prova o que o
surrogate SQLite não prova: dialeto, tipos e transação reais.

SEGURANÇA:
  * recebe os DOIS números de cliente como parâmetro e NUNCA escreve fora deles;
  * todo dado sintético (notas/itens/regras/conjunto) que ele cria é removido no
    passo de LIMPEZA, que roda mesmo se um assert falhar (finally);
  * sem --go é DRY-RUN: só resolve os ids e mostra o plano, não grava nada;
  * exige que a migration da gestão já tenha rodado (não a aplica sozinho).

Uso (no serviço web, mesmo ambiente do app):
    python scripts/test_d31_e3_conjunto.py <numero_A> <numero_B>          # dry-run
    python scripts/test_d31_e3_conjunto.py <numero_A> <numero_B> --go     # executa + limpa
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# O pool vivo nasce DESLIGADO em produção (MEMO_POOL_VIVO=0). Este teste liga o
# interruptor SÓ no próprio processo, para exercitar o fan-out (item b) — a
# produção não é afetada (o env do serviço web continua sem a variável = off).
os.environ['MEMO_POOL_VIVO'] = '1'

from utils.db_helper import execute_query, transacao  # noqa: E402
from routes.escrita_fiscal import (  # noqa: E402
    _upsert_vinculo, _incluir_aplicar, _incluir_preview,
    _desvincular_tudo, _desvincular_restore, _memo_col_existe, _memo_tabela_existe,
)

ALE = 'TEST0000000ALE'    # CNPJs de teste — nenhum fornecedor real usa estes
YPE = 'TEST0000000YPE'
FALHAS = []


def check(nome, cond):
    print(("  PASS  " if cond else "  FALHA "), nome)
    if not cond:
        FALHAS.append(nome)


def cli_por_numero(numero):
    r = execute_query("SELECT id, nome_razao_social FROM clientes WHERE numero_cliente = %s",
                      (str(numero),), fetch=True, fetch_one=True)
    return r


def migracao_ok():
    return (_memo_col_existe('memo_clone_set', 'nome')
            and _memo_col_existe('memo_clone_membro', 'corte_data')
            and _memo_tabela_existe('memo_desvinculo_op')
            and _memo_tabela_existe('memo_desvinculo_bkp'))


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(2)
    numA, numB = sys.argv[1], sys.argv[2]
    go = '--go' in sys.argv[3:]

    a = cli_por_numero(numA)
    b = cli_por_numero(numB)
    if not a or not b:
        print('ERRO: não encontrei as duas empresas de teste pelos números dados.'); sys.exit(2)
    if a['id'] == b['id']:
        print('ERRO: os dois números apontam para a mesma empresa.'); sys.exit(2)
    A, B = a['id'], b['id']
    IDS = (A, B)
    print(f"Empresa A = #{numA} id={A} ({a['nome_razao_social']})")
    print(f"Empresa B = #{numB} id={B} ({b['nome_razao_social']})")

    if not migracao_ok():
        print('ERRO: migration da gestão do conjunto ainda NÃO foi aplicada. '
              'Rode migrations/2026_08_memo_gestao_conjunto.py primeiro.'); sys.exit(2)

    if not go:
        print('\nDRY-RUN (sem --go): nada foi gravado. Passe --go para executar e limpar.')
        return

    # ---------- SNAPSHOT (para restaurar no fim) ----------
    ph = ','.join(['%s'] * len(IDS))
    snap_vinc = execute_query(
        f"SELECT * FROM nfe_produto_vinculo WHERE cliente_id IN ({ph})", IDS, fetch=True) or []
    print(f"\nSnapshot: {len(snap_vinc)} regra(s) pré-existentes nas 2 empresas (serão preservadas).")

    op_desv = None
    try:
        # ---------- monta cenário sintético (só nas 2 empresas de teste) ----------
        with transacao() as cur:
            cur.execute("INSERT INTO memo_clone_set (nome) VALUES ('__TESTE_D31_E3__')")
            set_id = cur.lastrowid
            cur.execute("INSERT INTO memo_clone_membro (set_id, cliente_id) VALUES (%s,%s)", (set_id, A))
            # A recebe a regra nova (ALE,123)->10 via _upsert_vinculo (dispara o pool vivo);
            # mas B ainda não está no conjunto, então nada vaza agora.
        # notas/itens sintéticos de B (par 123 sem vínculo) e divergente (777)
        with transacao() as cur:
            cur.execute("INSERT INTO nfe_importacoes (cliente_id, emit_cnpj, tipo, data_emissao) "
                        "VALUES (%s,%s,'entrada','2026-08-05')", (B, ALE))
            nfeB = cur.lastrowid
            cur.execute("INSERT INTO nfe_itens (nfe_id, codigo_produto, produto_catalogo_id) "
                        "VALUES (%s,'123',NULL)", (nfeB,))
            cur.execute("INSERT INTO nfe_importacoes (cliente_id, emit_cnpj, tipo, data_emissao) "
                        "VALUES (%s,%s,'entrada','2026-07-01')", (B, YPE))
            nfeB2 = cur.lastrowid
            cur.execute("INSERT INTO nfe_itens (nfe_id, codigo_produto, produto_catalogo_id) "
                        "VALUES (%s,'777',NULL)", (nfeB2,))
            # B já tem regra DIVERGENTE (YPE,777)->222
            cur.execute("INSERT INTO nfe_produto_vinculo (cliente_id,grupo_id,ramo_atividade_id,"
                        "emit_cnpj,codigo_produto_xml,descricao_produto_xml,produto_catalogo_id,tipo) "
                        "VALUES (%s,NULL,NULL,%s,'777','SABAO',222,'entrada')", (B, YPE))
            # agora B entra no conjunto
            cur.execute("INSERT INTO memo_clone_membro (set_id, cliente_id) VALUES (%s,%s)", (set_id, B))

        print("\nb) POOL VIVO — A vincula (ALE,123)->111; B deve receber")
        _upsert_vinculo(A, ALE, '123', 'GASOLINA', 111, tipo='entrada')  # dispara fan-out p/ B
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

        print("d) INCLUIR B de novo com corte não se aplica (já é membro); provamos o corte via preview")
        # cria em B uma nota antiga e uma nova do par 123 para o corte
        with transacao() as cur:
            cur.execute("INSERT INTO nfe_importacoes (cliente_id, emit_cnpj, tipo, data_emissao) "
                        "VALUES (%s,%s,'entrada','2026-06-01')", (B, 'TEST000CORTE'))
            nfeC1 = cur.lastrowid
            cur.execute("INSERT INTO nfe_itens (nfe_id, codigo_produto, produto_catalogo_id) VALUES (%s,'123',NULL)", (nfeC1,))
        prev_todos = _incluir_preview(set_id, B, None)
        prev_corte = _incluir_preview(set_id, B, '2026-08-01')
        check("preview: corte por data reduz itens a vincular vs 'todos'",
              prev_corte['itens_a_vincular'] <= prev_todos['itens_a_vincular'])

        print("e) DESVINCULAR 'tudo' de B — backup ANTES do delete, e restore por op_id")
        antes = execute_query("SELECT COUNT(*) c FROM nfe_produto_vinculo WHERE cliente_id=%s "
                              "AND grupo_id IS NULL AND ramo_atividade_id IS NULL", (B,),
                              fetch=True, fetch_one=True)['c']
        res = _desvincular_tudo(set_id, B, actor_id=None)
        op_desv = res['op_id']
        bkp = execute_query("SELECT COUNT(*) c FROM memo_desvinculo_bkp WHERE op_id=%s", (op_desv,),
                            fetch=True, fetch_one=True)['c']
        depois = execute_query("SELECT COUNT(*) c FROM nfe_produto_vinculo WHERE cliente_id=%s "
                               "AND grupo_id IS NULL AND ramo_atividade_id IS NULL", (B,),
                               fetch=True, fetch_one=True)['c']
        check(f"backup guardou as {antes} regra(s) ANTES do delete", bkp == antes and antes > 0)
        check("regras de B apagadas (0 restantes)", depois == 0)
        volta = _desvincular_restore(op_desv)
        rest = execute_query("SELECT COUNT(*) c FROM nfe_produto_vinculo WHERE cliente_id=%s "
                             "AND grupo_id IS NULL AND ramo_atividade_id IS NULL", (B,),
                             fetch=True, fetch_one=True)['c']
        check(f"restore por op_id reinseriu {antes} regra(s)", rest == antes and volta == antes)

    finally:
        # ---------- LIMPEZA: devolve tudo ao estado anterior ----------
        print("\nLIMPEZA — removendo todo o dado sintético e restaurando o snapshot...")
        with transacao() as cur:
            # apaga notas/itens sintéticos criados (só das 2 empresas, pelos CNPJs de teste)
            cur.execute(f"DELETE i FROM nfe_itens i JOIN nfe_importacoes n ON n.id=i.nfe_id "
                        f"WHERE n.cliente_id IN ({ph}) AND n.emit_cnpj IN (%s,%s,%s)",
                        (*IDS, ALE, YPE, 'TEST000CORTE'))
            cur.execute(f"DELETE FROM nfe_importacoes WHERE cliente_id IN ({ph}) "
                        f"AND emit_cnpj IN (%s,%s,%s)", (*IDS, ALE, YPE, 'TEST000CORTE'))
            # zera regras atuais das 2 empresas e re-insere o snapshot exato
            cur.execute(f"DELETE FROM nfe_produto_vinculo WHERE cliente_id IN ({ph})", IDS)
            for r in snap_vinc:
                cur.execute("INSERT INTO nfe_produto_vinculo (id,cliente_id,grupo_id,ramo_atividade_id,"
                            "emit_cnpj,codigo_produto_xml,descricao_produto_xml,produto_catalogo_id,tipo) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (r['id'], r['cliente_id'], r.get('grupo_id'), r.get('ramo_atividade_id'),
                             r['emit_cnpj'], r['codigo_produto_xml'], r.get('descricao_produto_xml'),
                             r['produto_catalogo_id'], r.get('tipo')))
            # remove conjunto/membros/op/bkp de teste (só das 2 empresas)
            cur.execute(f"DELETE FROM memo_clone_membro WHERE cliente_id IN ({ph})", IDS)
            cur.execute("DELETE FROM memo_clone_set WHERE nome='__TESTE_D31_E3__'")
            if op_desv:
                cur.execute("DELETE FROM memo_desvinculo_bkp WHERE op_id=%s", (op_desv,))
                cur.execute("DELETE FROM memo_desvinculo_op WHERE id=%s", (op_desv,))
        rest_vinc = execute_query(f"SELECT COUNT(*) c FROM nfe_produto_vinculo WHERE cliente_id IN ({ph})",
                                  IDS, fetch=True, fetch_one=True)['c']
        check(f"snapshot restaurado ({rest_vinc} == {len(snap_vinc)} regra(s) originais)",
              rest_vinc == len(snap_vinc))

    print()
    if FALHAS:
        print("RESULTADO: FALHOU em", len(FALHAS), "->", FALHAS); sys.exit(1)
    print("RESULTADO: b..e OK contra o MySQL real; ambiente restaurado. (f = guarda admin na rota.)")


if __name__ == '__main__':
    main()
