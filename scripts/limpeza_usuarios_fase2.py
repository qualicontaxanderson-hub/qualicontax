# -*- coding: utf-8 -*-
"""FASE 2 — limpeza de usuários. DRY-RUN por padrão; --executar para valer.

Guard do id 1: os alvos são recomputados como (todos os usuários exceto 1) e
conferidos contra o esperado {3,6} da Fase 1. Se aparecer qualquer id fora disso,
ou se 1 entrar nos alvos, ABORTA sem tocar em nada.
"""
import os
import sys

# Rodar de scripts/ não põe a raiz do projeto no path; bootstrap igual ao das
# migrations, para 'utils' resolver quando chamado como scripts/limpeza_....py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv; load_dotenv()
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from utils.db_helper import execute_query, transacao

EXECUTAR = '--executar' in sys.argv
ESPERADOS = {3, 6}          # o que a Fase 1 levantou
INTOCAVEL = 1

def cnt(sql, params=None):
    return (execute_query(sql, params, fetch=True, fetch_one=True) or {}).get('c', 0)

# --- Guard: quem são os alvos, e nada de tocar no id 1 --------------------
existentes = [r['id'] for r in (execute_query('SELECT id FROM usuarios ORDER BY id', fetch=True) or [])]
alvos = [i for i in existentes if i != INTOCAVEL]
print(f"Usuários no banco: {existentes} | INTOCÁVEL: {INTOCAVEL} | Alvos (≠1): {alvos}")
if INTOCAVEL in alvos:
    sys.exit("ABORT: id 1 nos alvos — impossível, guard falhou.")
if set(alvos) != ESPERADOS:
    sys.exit(f"ABORT: alvos {set(alvos)} != esperado {ESPERADOS} da Fase 1. "
             "O banco mudou desde o levantamento — reveja antes de apagar.")
if 1 not in existentes:
    sys.exit("ABORT: id 1 não existe no banco — algo muito errado.")

alvos_csv = ",".join(str(i) for i in alvos)

# --- Tabelas Q-Colabore a limpar POR INTEIRO (conv, sem FK/cascade) --------
CONV = ['usuario_dados_bancarios', 'cadastro_pendente_departamentos',
        'cadastro_pendente', 'cadastro_link']

print("\n" + "="*70)
print("PLANO DE DELETE" + ("  [MODO EXECUTAR]" if EXECUTAR else "  [DRY-RUN — nada será apagado]"))
print("="*70)
print("\nA) Limpeza total das tabelas Q-Colabore (conv, ficariam órfãs):")
plano_conv = {t: cnt(f"SELECT COUNT(*) c FROM `{t}`") for t in CONV}
for t, n in plano_conv.items():
    print(f"     DELETE FROM {t:<34} -> {n:>3} linha(s)")

print(f"\nB) Usuários (WHERE id IN ({alvos_csv}) AND id <> {INTOCAVEL}):")
print(f"     DELETE FROM usuarios{'':<27} -> {len(alvos):>3} linha(s)  (ids {alvos})")

# --- Efeitos automáticos das FKs (informativo) -----------------------------
fks = execute_query("""SELECT k.TABLE_NAME t, k.COLUMN_NAME c, r.DELETE_RULE del
    FROM information_schema.KEY_COLUMN_USAGE k
    JOIN information_schema.REFERENTIAL_CONSTRAINTS r
      ON r.CONSTRAINT_NAME=k.CONSTRAINT_NAME AND r.CONSTRAINT_SCHEMA=k.TABLE_SCHEMA
    WHERE k.TABLE_SCHEMA=DATABASE() AND k.REFERENCED_TABLE_NAME='usuarios'
    ORDER BY r.DELETE_RULE, k.TABLE_NAME""", fetch=True) or []
restritivas = []
print("\nC) FKs CASCADE — linhas que somem junto (automático):")
achou_c = False
for f in fks:
    if (f['del'] or '').upper() == 'CASCADE':
        n = cnt(f"SELECT COUNT(*) c FROM `{f['t']}` WHERE `{f['c']}` IN ({alvos_csv})")
        if n: print(f"     {f['t']}.{f['c']:<24} -> {n} linha(s) deletadas em cascata"); achou_c=True
if not achou_c: print("     (nenhuma)")

print("\nD) FKs SET NULL — vira NULL (automático, linha preservada):")
achou_d = False
for f in fks:
    if (f['del'] or '').upper() == 'SET NULL':
        n = cnt(f"SELECT COUNT(*) c FROM `{f['t']}` WHERE `{f['c']}` IN ({alvos_csv})")
        if n: print(f"     {f['t']}.{f['c']:<28} -> {n} linha(s) viram NULL"); achou_d=True
    elif (f['del'] or '').upper() in ('RESTRICT', 'NO ACTION'):
        restritivas.append(f)
if not achou_d: print("     (nenhuma)")

print("\nE) FKs RESTRICT/NO ACTION que bloqueariam:")
bloq = False
for f in restritivas:
    n = cnt(f"SELECT COUNT(*) c FROM `{f['t']}` WHERE `{f['c']}` IN ({alvos_csv})")
    if n: print(f"     BLOQUEIO: {f['t']}.{f['c']} = {n}"); bloq=True
if not bloq: print("     (nenhuma — nenhum DELETE é bloqueado)")

if not EXECUTAR:
    print("\n>>> DRY-RUN. Nada foi alterado. Rode com --executar após confirmação.")
    sys.exit(0)

# --- EXECUÇÃO REAL: tudo numa transação única ------------------------------
print("\n" + "="*70)
print("EXECUTANDO (transação única)…")
print("="*70)
afetadas = {}
with transacao() as cur:
    for t in CONV:
        cur.execute(f"DELETE FROM `{t}`")
        afetadas[t] = cur.rowcount
    # Guard redundante no próprio SQL: nunca o id 1.
    cur.execute(f"DELETE FROM usuarios WHERE id IN ({alvos_csv}) AND id <> {INTOCAVEL}")
    afetadas['usuarios'] = cur.rowcount
    # Trava de segurança DENTRO da transação: se por algum bug o admin sumiu,
    # levanta e reverte tudo.
    cur.execute("SELECT COUNT(*) AS c FROM usuarios WHERE id = 1")
    if (cur.fetchone() or {}).get('c', 0) != 1:
        raise RuntimeError("id 1 sumiu durante a transação — revertendo TUDO.")
for t, n in afetadas.items():
    print(f"     {t:<34} {n} linha(s) apagada(s)")
print("\n>>> COMMIT ok.")
