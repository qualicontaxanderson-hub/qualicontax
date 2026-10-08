"""Rotas de Grupos de Clientes - CRUD completo"""
import json
import re
import unicodedata
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
from utils.auth_helper import login_required, permission_required
from utils.atividade import registrar
from utils.form_helpers import limpar_vazio
from utils.db_helper import execute_query
from utils.acesso import empresas_ocultas
from models.grupo_cliente import GrupoCliente
from models.cliente import Cliente

grupos = Blueprint('grupos', __name__)


_REGIME_ROTULO = {'SIMPLES': 'Simples Nacional', 'LUCRO_PRESUMIDO': 'Lucro Presumido',
                  'LUCRO_REAL': 'Lucro Real', 'MEI': 'MEI'}
# ordem e cor da etiqueta de cada regime no cartão (classe .gc-reg-*)
_REGIME_ORDEM = ['LUCRO_REAL', 'LUCRO_PRESUMIDO', 'SIMPLES', 'MEI']
_CORES_CIDADE = 8   # .gc-cid-0 … .gc-cid-7


def _cor_cidade(chave):
    """Cor fixa por cidade: a mesma cidade tem a mesma cor em todos os cartões."""
    return sum(ord(c) * (i + 1) for i, c in enumerate(chave)) % _CORES_CIDADE


def _cidade_titulo(cidade):
    return ' '.join(w.capitalize() if len(w) > 2 else w.lower() for w in (cidade or '').split())


def _sem_acento(s):
    return unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()


def _grupos_com_empresas():
    """Os grupos com as empresas de cada um, numa consulta só (antes era uma
    por grupo). Empresa reservada some para quem não pode vê-la."""
    linhas = execute_query("""
        SELECT g.id AS gid, g.nome AS gnome, g.situacao AS gsit,
               g.motivo_desativacao AS gmot, g.motivo_desativacao_obs AS gmot_obs,
               c.id, c.numero_cliente, c.nome_razao_social, c.tipo_pessoa, c.regime_tributario,
               c.cpf_cnpj, e.cidade, e.estado
          FROM grupos_clientes g
          LEFT JOIN cliente_grupo_relacao r ON r.grupo_id = g.id
          LEFT JOIN clientes c ON c.id = r.cliente_id
          LEFT JOIN enderecos_clientes e ON e.id = (
                SELECT e2.id FROM enderecos_clientes e2 WHERE e2.cliente_id = c.id
                 ORDER BY e2.principal DESC, e2.id LIMIT 1)
         ORDER BY g.nome, CAST(c.numero_cliente AS UNSIGNED), c.nome_razao_social
    """, fetch=True) or []
    ocultas = empresas_ocultas()
    grupos_d = {}
    for r in linhas:
        g = grupos_d.setdefault(r['gid'], {'id': r['gid'], 'nome': r['gnome'] or '',
                                           'situacao': r['gsit'] or 'ATIVO', 'empresas': [],
                                           'motivo': r['gmot'], 'motivo_obs': r['gmot_obs']})
        if r['id'] and r['id'] not in ocultas:
            g['empresas'].append({
                'id': r['id'], 'numero': r['numero_cliente'],
                'nome': (r['nome_razao_social'] or '').strip(), 'tipo': r['tipo_pessoa'],
                'regime': r['regime_tributario'], 'doc': r['cpf_cnpj'] or '',
                'reg': 'pf' if r['tipo_pessoa'] == 'PF' else (r['regime_tributario'] or '').lower(),
                'local': f"{_cidade_titulo(r['cidade'])}/{r['estado']}" if r['cidade'] else '',
            })
    for g in grupos_d.values():
        palavras = re.findall(r'\w+', g['nome'])
        g['sigla'] = (''.join(p[0] for p in palavras[:2]) if len(palavras) > 1 else g['nome'][:2]).upper()
        # a mesma cidade gravada com e sem acento ("SAO PAULO" / "São Paulo")
        # aparece uma vez só, na grafia com acento
        locais = {}
        for e in g['empresas']:
            if e['local']:
                k = _sem_acento(e['local'])
                if k not in locais or _sem_acento(locais[k]) == locais[k].lower():
                    locais[k] = e['local']
        g['locais'] = ', '.join(sorted(locais.values()))
        g['cidades'] = [{'nome': v, 'cor': _cor_cidade(k)}
                        for k, v in sorted(locais.items(), key=lambda x: x[0])]
        # regime é de empresa: a pessoa física conta à parte, na etiqueta PF
        regimes = {}
        for e in g['empresas']:
            if e['regime'] and e['tipo'] != 'PF':
                regimes[e['regime']] = regimes.get(e['regime'], 0) + 1
        ordem = {k: i for i, k in enumerate(_REGIME_ORDEM)}
        g['regimes'] = [{'qtd': n, 'rotulo': _REGIME_ROTULO.get(k, k), 'classe': k.lower()}
                        for k, n in sorted(regimes.items(), key=lambda x: ordem.get(x[0], 99))]
        n_pf = sum(1 for e in g['empresas'] if e['tipo'] == 'PF')
        if n_pf:
            g['regimes'].append({'qtd': n_pf, 'rotulo': 'Pessoa Física', 'classe': 'pf'})
        g['motivo'] = _motivo_rotulo(g['motivo'], g['motivo_obs']) if g['situacao'] != 'ATIVO' else ''
        g['busca'] = ' '.join([g['nome']] + [f"{e['numero'] or ''} {e['nome']}" for e in g['empresas']]).lower()
    return list(grupos_d.values())


@grupos.route('/grupos')
@permission_required('grupos.index')
def index():
    """Grupos de empresas em cartões (07/10/2026). Busca e Ativos/Inativos
    filtram na própria tela; ?situacao= só escolhe o botão que abre marcado."""
    try:
        lista = _grupos_com_empresas()
    except Exception as e:
        flash(f'Erro ao carregar grupos: {str(e)}', 'danger')
        lista = []
    return render_template('grupos/index.html', grupos=lista,
                           total_empresas=sum(len(g['empresas']) for g in lista),
                           filtro_situacao=request.args.get('situacao', ''))


@grupos.route('/grupos/novo', methods=['GET', 'POST'])
@login_required
def novo():
    """Criar novo grupo"""
    if request.method == 'POST':
        try:
            nome = limpar_vazio(request.form.get('nome'))          # E1: sem "None"
            descricao = limpar_vazio(request.form.get('descricao'))
            situacao = request.form.get('situacao', 'ATIVO')
            
            # Validação
            if not nome:
                flash('Nome do grupo é obrigatório!', 'danger')
                return render_template('grupos/form.html', grupo=None)
            
            # Criar grupo
            grupo_id = GrupoCliente.create(nome, descricao, situacao)

            if grupo_id:
                registrar('escrita.criou_grupo', 'cadastros', tabela='grupos_clientes',
                          registro_id=grupo_id,
                          depois={'nome': nome, 'descricao': descricao, 'situacao': situacao})
                flash('Grupo criado com sucesso!', 'success')
                return redirect(url_for('grupos.detalhes', id=grupo_id))
            else:
                flash('Erro ao criar grupo!', 'danger')
        
        except Exception as e:
            flash(f'Erro ao criar grupo: {str(e)}', 'danger')
    
    return render_template('grupos/form.html', grupo=None)


@grupos.route('/grupos/<int:id>')
@login_required
def detalhes(id):
    """Visualizar detalhes do grupo e seus clientes"""
    try:
        grupo = GrupoCliente.get_by_id(id)
        if not grupo:
            flash('Grupo não encontrado!', 'danger')
            return redirect(url_for('grupos.index'))
        
        # Buscar clientes do grupo
        clientes_grupo = GrupoCliente.get_clientes(id)
        
        # Buscar todos os clientes para poder adicionar
        todos_clientes = Cliente.get_all(page=1, per_page=1000)
        clientes_disponiveis = todos_clientes.get('clientes', [])
        
        # Filtrar clientes que já estão no grupo
        clientes_ids_no_grupo = [c['id'] for c in clientes_grupo]
        clientes_disponiveis = [c for c in clientes_disponiveis if c['id'] not in clientes_ids_no_grupo]
        
        return render_template('grupos/detalhes.html',
                             grupo=grupo,
                             clientes=clientes_grupo,
                             clientes_disponiveis=clientes_disponiveis)
    
    except Exception as e:
        flash(f'Erro ao carregar detalhes do grupo: {str(e)}', 'danger')
        return redirect(url_for('grupos.index'))


@grupos.route('/grupos/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar(id):
    """Editar grupo"""
    grupo = GrupoCliente.get_by_id(id)
    if not grupo:
        flash('Grupo não encontrado!', 'danger')
        return redirect(url_for('grupos.index'))
    
    if request.method == 'POST':
        try:
            nome = limpar_vazio(request.form.get('nome'))          # E1: sem "None"
            descricao = limpar_vazio(request.form.get('descricao'))
            situacao = request.form.get('situacao', 'ATIVO')
            
            # Validação
            if not nome:
                flash('Nome do grupo é obrigatório!', 'danger')
                return render_template('grupos/form.html', grupo=grupo)
            
            # Atualizar grupo
            sucesso = GrupoCliente.update(id, nome, descricao, situacao)

            if sucesso is not None:
                registrar('escrita.alterou_grupo', 'cadastros', tabela='grupos_clientes',
                          registro_id=id,
                          antes={'nome': grupo.get('nome'), 'descricao': grupo.get('descricao'),
                                 'situacao': grupo.get('situacao')},
                          depois={'nome': nome, 'descricao': descricao, 'situacao': situacao})
                flash('Grupo atualizado com sucesso!', 'success')
                return redirect(url_for('grupos.detalhes', id=id))
            else:
                flash('Erro ao atualizar grupo!', 'danger')
        
        except Exception as e:
            flash(f'Erro ao atualizar grupo: {str(e)}', 'danger')
    
    return render_template('grupos/form.html', grupo=grupo)


@grupos.route('/grupos/<int:id>/deletar', methods=['POST'])
@login_required
def deletar(id):
    """Deletar grupo"""
    try:
        sucesso = GrupoCliente.delete(id)
        if sucesso is not None:
            flash('Grupo removido com sucesso!', 'success')
        else:
            flash('Erro ao remover grupo!', 'danger')
    except Exception as e:
        flash(f'Erro ao remover grupo: {str(e)}', 'danger')
    
    return redirect(url_for('grupos.index'))


@grupos.route('/grupos/<int:grupo_id>/adicionar-cliente', methods=['POST'])
@login_required
def adicionar_cliente(grupo_id):
    """Adicionar cliente ao grupo"""
    try:
        cliente_id = request.form.get('cliente_id', type=int)
        
        if not cliente_id:
            flash('Cliente não selecionado!', 'danger')
            return redirect(url_for('grupos.detalhes', id=grupo_id))
        
        sucesso = GrupoCliente.add_cliente(grupo_id, cliente_id)
        
        if sucesso:
            flash('Cliente adicionado ao grupo com sucesso!', 'success')
        else:
            flash('Erro ao adicionar cliente ao grupo!', 'danger')
    
    except Exception as e:
        flash(f'Erro ao adicionar cliente: {str(e)}', 'danger')
    
    return redirect(url_for('grupos.detalhes', id=grupo_id))


@grupos.route('/grupos/<int:grupo_id>/remover-cliente/<int:cliente_id>', methods=['POST'])
@login_required
def remover_cliente(grupo_id, cliente_id):
    """Remover cliente do grupo"""
    try:
        sucesso = GrupoCliente.remove_cliente(grupo_id, cliente_id)
        
        if sucesso is not None:
            flash('Cliente removido do grupo com sucesso!', 'success')
        else:
            flash('Erro ao remover cliente do grupo!', 'danger')
    
    except Exception as e:
        flash(f'Erro ao remover cliente: {str(e)}', 'danger')
    
    return redirect(url_for('grupos.detalhes', id=grupo_id))


# ── Caixa do grupo (08/10/2026) ────────────────────────────────────────────
# O cartão abre o grupo numa caixa no meio da tela, sem trocar de página: lá
# se abre a ficha da empresa, muda a empresa de grupo, inclui empresas,
# renomeia e desativa (com motivo e quem desativou). Tudo em JSON.

MOTIVOS_DESATIVACAO = {
    'TROCA_CONTABILIDADE': 'Troca de Contabilidade',
    'ENCERRAMENTO': 'Encerramento das Atividades',
    'INADIMPLENCIA': 'Inadimplência',
    'OUTRO': 'Outro motivo',
}


def _motivo_rotulo(codigo, obs):
    if codigo == 'OUTRO' and obs:
        return obs
    return MOTIVOS_DESATIVACAO.get(codigo or '', codigo or '')


def _grupo_json(grupo_id):
    g = execute_query("""
        SELECT g.id, g.nome, g.situacao, g.desativado_em, g.motivo_desativacao,
               g.motivo_desativacao_obs, u.nome AS desativado_por_nome
          FROM grupos_clientes g
          LEFT JOIN usuarios u ON u.id = g.desativado_por
         WHERE g.id = %s""", (grupo_id,), fetch=True, fetch_one=True)
    if not g:
        return None
    return {'id': g['id'], 'nome': g['nome'] or '', 'ativo': (g['situacao'] or 'ATIVO') == 'ATIVO',
            'desativado_em': g['desativado_em'].strftime('%d/%m/%Y %H:%M') if g['desativado_em'] else '',
            'desativado_por': g['desativado_por_nome'] or '',
            'motivo': _motivo_rotulo(g['motivo_desativacao'], g['motivo_desativacao_obs'])}


@grupos.route('/grupos/<int:id>/painel')
@permission_required('grupos.index')
def painel(id):
    """Tudo o que a caixa do grupo mostra, numa chamada."""
    grupo = _grupo_json(id)
    if not grupo:
        return jsonify(success=False, message='Grupo não encontrado.'), 404
    ocultas = empresas_ocultas()
    empresas = execute_query("""
        SELECT c.id, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj, c.tipo_pessoa,
               c.regime_tributario, c.situacao, e.cidade, e.estado
          FROM cliente_grupo_relacao r
          JOIN clientes c ON c.id = r.cliente_id
          LEFT JOIN enderecos_clientes e ON e.id = (
                SELECT e2.id FROM enderecos_clientes e2 WHERE e2.cliente_id = c.id
                 ORDER BY e2.principal DESC, e2.id LIMIT 1)
         WHERE r.grupo_id = %s
         ORDER BY CAST(c.numero_cliente AS UNSIGNED), c.nome_razao_social""", (id,), fetch=True) or []
    grupo['empresas'] = [{
        'id': e['id'], 'numero': e['numero_cliente'] or '', 'nome': (e['nome_razao_social'] or '').strip(),
        'doc': e['cpf_cnpj'] or '', 'tipo': e['tipo_pessoa'],
        'regime': 'Pessoa Física' if e['tipo_pessoa'] == 'PF'
                  else _REGIME_ROTULO.get(e['regime_tributario'] or '', e['regime_tributario'] or ''),
        'local': f"{_cidade_titulo(e['cidade'])}/{e['estado']}" if e['cidade'] else '',
        # mesmas cores dos cartões: .gc-reg-* e .gc-cid-N
        'regime_classe': 'pf' if e['tipo_pessoa'] == 'PF' else (e['regime_tributario'] or '').lower(),
        'cid_cor': _cor_cidade(_sem_acento(f"{_cidade_titulo(e['cidade'])}/{e['estado']}")) if e['cidade'] else 0,
        'ativa': (e['situacao'] or 'ATIVO') == 'ATIVO',
        'ficha': url_for('clientes.detalhes', id=e['id']),
    } for e in empresas if e['id'] not in ocultas]
    grupo['outros_grupos'] = [{'id': o['id'], 'nome': o['nome']}
                              for o in GrupoCliente.get_all('ATIVO') if o['id'] != id]
    grupo['motivos'] = [{'codigo': k, 'rotulo': v} for k, v in MOTIVOS_DESATIVACAO.items()]
    grupo['n_ativas'] = sum(1 for e in grupo['empresas'] if e['ativa'])
    return jsonify(success=True, grupo=grupo)


@grupos.route('/grupos/api/empresas')
@permission_required('grupos.index')
def buscar_empresas():
    """Busca para "Incluir empresa": nome, número ou CPF/CNPJ. Diz em que grupo
    a empresa está hoje (incluir aqui tira de lá)."""
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify(success=True, empresas=[])
    dig = re.sub(r'\D', '', q)
    cond = ["c.nome_razao_social LIKE %s", "c.nome_fantasia LIKE %s", "c.numero_cliente = %s"]
    params = [f'%{q}%', f'%{q}%', q]
    if len(dig) >= 4:
        cond.append("REPLACE(REPLACE(REPLACE(c.cpf_cnpj,'.',''),'/',''),'-','') LIKE %s")
        params.append(f'%{dig}%')
    linhas = execute_query(f"""
        SELECT c.id, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj, c.tipo_pessoa,
               r.grupo_id AS grupo_atual_id, g.nome AS grupo_atual
          FROM clientes c
          LEFT JOIN cliente_grupo_relacao r ON r.cliente_id = c.id
          LEFT JOIN grupos_clientes g ON g.id = r.grupo_id
         WHERE c.situacao = 'ATIVO' AND ({' OR '.join(cond)})
         ORDER BY c.nome_razao_social LIMIT 30""", tuple(params), fetch=True) or []
    ocultas = empresas_ocultas()
    return jsonify(success=True, empresas=[{
        'id': r['id'], 'numero': r['numero_cliente'] or '', 'nome': (r['nome_razao_social'] or '').strip(),
        'doc': r['cpf_cnpj'] or '', 'tipo': r['tipo_pessoa'],
        'grupo': r['grupo_atual'] or '', 'grupo_id': r['grupo_atual_id'],
    } for r in linhas if r['id'] not in ocultas][:20])


def _empresa_visivel(cliente_id):
    try:
        cliente_id = int(cliente_id or 0)
    except (TypeError, ValueError):
        return None
    if not cliente_id or cliente_id in empresas_ocultas():
        return None
    return execute_query("SELECT id, nome_razao_social FROM clientes WHERE id = %s",
                         (cliente_id,), fetch=True, fetch_one=True)


def _mover(cliente_id, destino):
    """Empresa fica em UM grupo: sai dos atuais e entra no destino (mesma regra
    do formulário do cliente). Registra no log se mudou."""
    from routes.clientes import _salvar_grupo
    mud = _salvar_grupo(cliente_id, destino['id'])
    if mud:
        registrar('escrita.alterou_grupo_cliente', 'cadastros', tabela='clientes', registro_id=cliente_id,
                  antes={'grupos': mud[0]}, depois={'grupos': [destino['nome']], 'origem': 'caixa_do_grupo'})
    return bool(mud)


@grupos.route('/grupos/<int:id>/incluir-empresa', methods=['POST'])
@permission_required('grupos.index')
def incluir_empresa(id):
    emp = _empresa_visivel((request.get_json(silent=True) or {}).get('cliente_id'))
    grupo = GrupoCliente.get_by_id(id)
    if not emp or not grupo:
        return jsonify(success=False, message='Empresa ou grupo não encontrado.'), 404
    if not _mover(emp['id'], grupo):
        return jsonify(success=True, message='A empresa já está neste grupo.')
    return jsonify(success=True)


@grupos.route('/grupos/<int:id>/mover-empresa', methods=['POST'])
@permission_required('grupos.index')
def mover_empresa(id):
    dados = request.get_json(silent=True) or {}
    emp = _empresa_visivel(dados.get('cliente_id'))
    destino = GrupoCliente.get_by_id(dados.get('grupo_id') or 0)
    if not emp:
        return jsonify(success=False, message='Empresa não encontrada.'), 404
    if not destino or destino['id'] == id:
        return jsonify(success=False, message='Escolha o grupo de destino.'), 400
    _mover(emp['id'], destino)
    return jsonify(success=True)


@grupos.route('/grupos/<int:id>/renomear', methods=['POST'])
@permission_required('grupos.index')
def renomear(id):
    grupo = GrupoCliente.get_by_id(id)
    nome = re.sub(r'\s+', ' ', (request.get_json(silent=True) or {}).get('nome') or '').strip().upper()
    if not grupo:
        return jsonify(success=False, message='Grupo não encontrado.'), 404
    if not nome or len(nome) > 100:
        return jsonify(success=False, message='Digite um nome de até 100 letras.'), 400
    outro = execute_query("SELECT id FROM grupos_clientes WHERE UPPER(TRIM(nome)) = %s AND id <> %s LIMIT 1",
                          (nome, id), fetch=True, fetch_one=True)
    if outro:
        return jsonify(success=False, message=f'Já existe outro grupo chamado "{nome}".'), 400
    GrupoCliente.update(id, nome, grupo.get('descricao'), grupo.get('situacao') or 'ATIVO')
    registrar('escrita.alterou_grupo', 'cadastros', tabela='grupos_clientes', registro_id=id,
              antes={'nome': grupo.get('nome')}, depois={'nome': nome})
    return jsonify(success=True, nome=nome)


def _mudar_situacao_empresas(ids, situacao, porque):
    """Ativa/inativa as empresas do grupo, uma linha de log por empresa (fica
    no histórico da ficha de cada uma)."""
    for cid in ids:
        execute_query("UPDATE clientes SET situacao = %s, alterado_por = %s, alterado_em = NOW() WHERE id = %s",
                      (situacao, current_user.id, cid))
        registrar('escrita.alterou_cliente', 'cadastros', tabela='clientes', registro_id=cid,
                  antes={'situacao': 'ATIVO' if situacao != 'ATIVO' else 'INATIVO'},
                  depois={'situacao': situacao, 'motivo': porque})


@grupos.route('/grupos/<int:id>/desativar', methods=['POST'])
@permission_required('grupos.index')
def desativar(id):
    """Desativar exige o motivo; "Outro motivo" exige o texto. Grava quem e
    quando no grupo e deixa o histórico no log de atividades."""
    from models.grupo_cliente import _invalidate_cache
    grupo = GrupoCliente.get_by_id(id)
    dados = request.get_json(silent=True) or {}
    motivo = dados.get('motivo') or ''
    obs = re.sub(r'\s+', ' ', dados.get('obs') or '').strip()[:255]
    if not grupo:
        return jsonify(success=False, message='Grupo não encontrado.'), 404
    if motivo not in MOTIVOS_DESATIVACAO:
        return jsonify(success=False, message='Escolha o motivo da desativação.'), 400
    if motivo == 'OUTRO' and len(obs) < 3:
        return jsonify(success=False, message='Escreva qual é o motivo.'), 400
    if motivo != 'OUTRO':
        obs = ''
    # As empresas ATIVAS do grupo ficam inativas junto (somem das listas e das
    # obrigações; a captura segue enquanto o certificado valer). Guarda quais
    # foram, para a reativação devolver só essas.
    ativas = [e['id'] for e in (execute_query(
        "SELECT c.id FROM cliente_grupo_relacao r JOIN clientes c ON c.id = r.cliente_id "
        "WHERE r.grupo_id = %s AND c.situacao = 'ATIVO'", (id,), fetch=True) or [])]
    r = execute_query("""UPDATE grupos_clientes SET situacao = 'INATIVO', desativado_em = NOW(),
                                desativado_por = %s, motivo_desativacao = %s, motivo_desativacao_obs = %s,
                                empresas_desativadas = %s, alterado_por = %s, alterado_em = NOW()
                          WHERE id = %s""",
                      (current_user.id, motivo, obs or None, json.dumps(ativas), current_user.id, id))
    if r is None:
        return jsonify(success=False, message='Não consegui desativar agora. Tente de novo.'), 500
    _invalidate_cache()
    rotulo = _motivo_rotulo(motivo, obs)
    _mudar_situacao_empresas(ativas, 'INATIVO', f'grupo {grupo.get("nome")} desativado: {rotulo}')
    registrar('escrita.desativou_grupo', 'cadastros', tabela='grupos_clientes', registro_id=id,
              antes={'situacao': grupo.get('situacao')},
              depois={'situacao': 'INATIVO', 'motivo': rotulo, 'empresas_desativadas': ativas})
    return jsonify(success=True, empresas=len(ativas))


@grupos.route('/grupos/<int:id>/reativar', methods=['POST'])
@permission_required('grupos.index')
def reativar(id):
    """Reativa. A última desativação continua gravada no grupo (e no log)."""
    from models.grupo_cliente import _invalidate_cache
    grupo = GrupoCliente.get_by_id(id)
    if not grupo:
        return jsonify(success=False, message='Grupo não encontrado.'), 404
    linha = execute_query("SELECT empresas_desativadas FROM grupos_clientes WHERE id = %s",
                          (id,), fetch=True, fetch_one=True) or {}
    try:
        ids = [int(x) for x in json.loads(linha.get('empresas_desativadas') or '[]')]
    except (TypeError, ValueError):
        ids = []
    # só volta quem o grupo inativou E ainda está no grupo e inativo
    if ids:
        ids = [e['id'] for e in (execute_query(
            "SELECT c.id FROM cliente_grupo_relacao r JOIN clientes c ON c.id = r.cliente_id "
            f"WHERE r.grupo_id = %s AND c.situacao <> 'ATIVO' AND c.id IN ({','.join(['%s'] * len(ids))})",
            (id, *ids), fetch=True) or [])]
    r = execute_query("UPDATE grupos_clientes SET situacao = 'ATIVO', empresas_desativadas = NULL, "
                      "alterado_por = %s, alterado_em = NOW() WHERE id = %s", (current_user.id, id))
    if r is None:
        return jsonify(success=False, message='Não consegui reativar agora. Tente de novo.'), 500
    _invalidate_cache()
    _mudar_situacao_empresas(ids, 'ATIVO', f'grupo {grupo.get("nome")} reativado')
    registrar('escrita.reativou_grupo', 'cadastros', tabela='grupos_clientes', registro_id=id,
              antes={'situacao': grupo.get('situacao')},
              depois={'situacao': 'ATIVO', 'empresas_reativadas': ids})
    return jsonify(success=True, empresas=len(ids))
