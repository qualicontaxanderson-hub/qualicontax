"""Rotas de Grupos de Clientes - CRUD completo"""
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


def _cidade_titulo(cidade):
    return ' '.join(w.capitalize() if len(w) > 2 else w.lower() for w in (cidade or '').split())


def _sem_acento(s):
    return unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()


def _grupos_com_empresas():
    """Os grupos com as empresas de cada um, numa consulta só (antes era uma
    por grupo). Empresa reservada some para quem não pode vê-la."""
    linhas = execute_query("""
        SELECT g.id AS gid, g.nome AS gnome, g.situacao AS gsit,
               c.id, c.numero_cliente, c.nome_razao_social, c.tipo_pessoa, c.regime_tributario,
               e.cidade, e.estado
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
                                           'situacao': r['gsit'] or 'ATIVO', 'empresas': []})
        if r['id'] and r['id'] not in ocultas:
            g['empresas'].append({
                'id': r['id'], 'numero': r['numero_cliente'],
                'nome': (r['nome_razao_social'] or '').strip(), 'tipo': r['tipo_pessoa'],
                'regime': r['regime_tributario'],
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
        regimes = {}
        for e in g['empresas']:
            if e['regime']:
                rot = _REGIME_ROTULO.get(e['regime'], e['regime'])
                regimes[rot] = regimes.get(rot, 0) + 1
        g['regimes'] = [f'{n} {rot}' for rot, n in sorted(regimes.items(), key=lambda x: -x[1])]
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
