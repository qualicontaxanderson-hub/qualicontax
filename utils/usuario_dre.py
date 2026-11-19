"""Em que conta do DRE cai cada pagamento de cada pessoa (19/11/2026).

Anderson: "No nosso DRE temos Gabriel e ele tem Salário, Ajuda de Custo, Comissões. Preciso disso tudo separado mas
quando for as comissões quero que já vá sozinho... no cadastro de funcionário usuário no Config ter essas configurações
em qual parte do DRE ficaria cada coisa de cada funcionário. Exemplo a Grazi que não tem a comissão vinculada no DRE
quando fazer a primeira vez obrigar a gente a vincular no usuário e após isso não precisa mais."

Tabela fin_usuario_dre (usuario_id, papel, categoria_id): uma linha por papel. A COMISSÃO é usada sozinha: o pedido de
comissão quitado pelo extrato classifica a saída nessa categoria e concilia (vai para o DRE). Salário e ajuda de custo
ficam guardados para o mesmo uso quando esses pagamentos passarem pelo sistema.
"""
from utils.db_helper import execute_query

PAPEIS = (('SALARIO', 'Salário'), ('AJUDA', 'Ajuda de custo'), ('COMISSAO', 'Comissão'))
_VALIDOS = {p for p, _ in PAPEIS}


def de(usuario_id):
    """{'SALARIO': categoria_id, ...} só com o que está vinculado."""
    rows = execute_query('SELECT papel, categoria_id FROM fin_usuario_dre WHERE usuario_id = %s',
                         (usuario_id,), fetch=True) or []
    return {r['papel']: r['categoria_id'] for r in rows if r['categoria_id']}


def categoria(usuario_id, papel):
    return de(usuario_id).get(papel)


def sem_comissao(usuario_ids):
    """Dos usuários dados, os que ainda não têm a comissão vinculada."""
    ids = sorted({int(u) for u in usuario_ids if u})
    if not ids:
        return set()
    marks = ','.join(['%s'] * len(ids))
    tem = execute_query(f"SELECT usuario_id FROM fin_usuario_dre WHERE papel = 'COMISSAO' AND categoria_id IS NOT NULL "
                        f"AND usuario_id IN ({marks})", tuple(ids), fetch=True) or []
    return set(ids) - {r['usuario_id'] for r in tem}


def categorias_despesa():
    """Categorias de despesa ativas para escolher, na ordem do plano (grupo → nome)."""
    return execute_query("SELECT id, grupo, nome FROM fin_categorias WHERE tipo = 'P' AND COALESCE(ativo, 1) = 1 "
                         "ORDER BY ordem, nome", fetch=True) or []


def salvar(usuario_id, papel, categoria_id, por=None):
    """Grava (ou apaga, com categoria vazia) o vínculo. Devolve True se mudou."""
    if papel not in _VALIDOS:
        raise ValueError('Papel inválido.')
    cid = int(categoria_id) if str(categoria_id or '').isdigit() else None
    if cid:
        ok = execute_query("SELECT 1 FROM fin_categorias WHERE id = %s AND tipo = 'P'", (cid,), fetch=True, fetch_one=True)
        if not ok:
            raise ValueError('Escolha uma categoria de despesa.')
    atual = categoria(usuario_id, papel)
    if atual == cid:
        return False
    if cid:
        execute_query('INSERT INTO fin_usuario_dre (usuario_id, papel, categoria_id, atualizado_por) VALUES (%s, %s, %s, %s) '
                      'ON DUPLICATE KEY UPDATE categoria_id = VALUES(categoria_id), atualizado_por = VALUES(atualizado_por)',
                      (usuario_id, papel, cid, por), fetch=False)
    else:
        execute_query('DELETE FROM fin_usuario_dre WHERE usuario_id = %s AND papel = %s', (usuario_id, papel), fetch=False)
    return True


def comissoes_pelo_extrato():
    """Roda logo depois de cada importação do extrato (e ao abrir Lançamentos): quita o pedido de comissão cuja saída
    chegou e manda essa saída para o DRE. Antes das regras de memorização, que só pegam o que sobrar sem categoria."""
    import logging
    from utils.atividade import registrar
    from models.fin_lancamento import FinComissaoFechamento
    log = logging.getLogger(__name__)
    try:
        for fid, eid in FinComissaoFechamento.quitar_pelo_extrato():
            registrar('escrita.quitou_comissao_pelo_extrato', 'financeiro', tabela='fin_comissao_fechamentos',
                      registro_id=fid, depois={'extrato_id': eid})
    except Exception:
        log.exception('[usuario_dre] quitação de comissão pelo extrato')
    try:
        for eid, cid, res in classificar_comissoes_pendentes():
            registrar('escrita.classificou_comissao_no_dre', 'financeiro', tabela='extrato_lancamentos', registro_id=eid,
                      depois={'categoria_id': cid, 'conciliacao': res})
    except Exception:
        log.exception('[usuario_dre] comissão paga → DRE')


def classificar_comissoes_pendentes(usuario_id=None):
    """Saídas do extrato que pagaram pedidos de comissão e ainda estão SEM CATEGORIA: classifica na comissão da pessoa
    e concilia (cria a despesa já paga, que é o que entra no DRE). Linha já classificada não é tocada. Devolve
    [(extrato_id, categoria_id, resultado da conciliação)]."""
    from models.extrato_lancamento import ExtratoLancamento
    cond, params = '', ()
    if usuario_id:
        cond, params = ' AND f.usuario_id = %s', (usuario_id,)
    rows = execute_query(
        f"""SELECT e.id, d.categoria_id FROM fin_comissao_pagamentos p
              JOIN fin_comissao_fechamentos f ON f.id = p.fechamento_id
              JOIN extrato_lancamentos e ON e.id = p.extrato_id
              JOIN fin_usuario_dre d ON d.usuario_id = f.usuario_id AND d.papel = 'COMISSAO' AND d.categoria_id IS NOT NULL
             WHERE e.categoria_id IS NULL{cond}""", params, fetch=True) or []
    feitos = []
    for r in rows:
        execute_query('UPDATE extrato_lancamentos SET categoria_id = %s WHERE id = %s AND categoria_id IS NULL',
                      (r['categoria_id'], r['id']), fetch=False)
        lanc = ExtratoLancamento.get(r['id'])
        if not lanc or lanc.get('categoria_id') != r['categoria_id']:
            continue
        try:
            res = ExtratoLancamento.conciliar_automatico(lanc)
        except Exception:
            res = 'erro'
        feitos.append((r['id'], r['categoria_id'], res))
    return feitos
