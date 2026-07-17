# -*- coding: utf-8 -*-
"""
auth.py
=======

Camada de autenticação e autorização do SIGS.

Este módulo concentra TODA a lógica de login/logout, hashing de senha,
controle de sessão (Flask ``session``) e os decorators utilizados pelas
rotas de ``app.py`` para exigir login (``login_required``) ou perfil de
administrador (``admin_required``).

Regras de negócio implementadas aqui:

    - Toda senha é armazenada como hash (nunca em texto puro), usando
      ``werkzeug.security`` (PBKDF2), já uma dependência do Flask.
    - O acesso a QUALQUER tela do sistema exige login prévio.
    - O primeiro usuário cadastrado no sistema se torna administrador
      automaticamente (ver ``database.criar_usuario``); os demais
      cadastros recebem o perfil "atendente" (acesso restrito).
    - Ao fazer login, o usuário assume automaticamente o próximo guichê
      disponível (sem necessidade de digitar/selecionar manualmente).
    - Ao fazer logout, o guichê ocupado pelo usuário é liberado, ficando
      disponível para o próximo login.
    - Usuários desativados por um administrador têm a sessão invalidada
      na primeira requisição seguinte à desativação.
"""

from functools import wraps
from typing import Optional

from flask import jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import database
from config import config_manager, logger
from models import PerfilUsuario

# Chaves utilizadas dentro da sessão Flask (cookie assinado).
CHAVE_SESSAO_USUARIO_ID = "usuario_id"
CHAVE_SESSAO_NOME = "usuario_nome"
CHAVE_SESSAO_LOGIN = "usuario_login"
CHAVE_SESSAO_PERFIL = "usuario_perfil"
CHAVE_SESSAO_GUICHE = "guiche"

TAMANHO_MINIMO_SENHA = 6


# ---------------------------------------------------------------------------
# Hashing de senha
# ---------------------------------------------------------------------------

def gerar_hash_senha(senha: str) -> str:
    """Gera o hash seguro (PBKDF2/SHA-256) de uma senha em texto puro."""
    return generate_password_hash(senha)


def verificar_senha(senha_hash: str, senha_texto_puro: str) -> bool:
    """Verifica se a senha informada corresponde ao hash armazenado."""
    return check_password_hash(senha_hash, senha_texto_puro)


def validar_forca_senha(senha: str) -> Optional[str]:
    """
    Valida requisitos mínimos de senha. Retorna uma mensagem de erro (str)
    caso a senha seja inválida, ou ``None`` se estiver tudo certo.
    """
    if not senha or len(senha) < TAMANHO_MINIMO_SENHA:
        return f"A senha deve ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres."
    return None


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

def autenticar(login: str, senha: str):
    """
    Valida as credenciais informadas.

    Retorna uma tupla ``(usuario, mensagem_erro)``: em caso de sucesso,
    ``usuario`` é a instância de ``models.Usuario`` e ``mensagem_erro`` é
    ``None``; em caso de falha, ``usuario`` é ``None`` e ``mensagem_erro``
    contém o motivo (credenciais inválidas ou usuário desativado).
    """
    usuario = database.obter_usuario_por_login((login or "").strip())

    if usuario is None or not verificar_senha(usuario.senha_hash, senha or ""):
        return None, "Login ou senha inválidos."

    if not usuario.ativo:
        return None, "Este usuário está desativado. Procure um administrador do sistema."

    return usuario, None


def iniciar_sessao(usuario) -> None:
    """
    Grava os dados do usuário autenticado na sessão Flask.

    Apenas usuários com perfil "atendente" assumem automaticamente um
    guichê de atendimento disponível — administradores e emissores de
    senha NÃO ocupam guichê, pois não realizam chamadas de atendimento
    (o administrador gerencia o sistema; o emissor apenas emite senhas em
    um totem).
    """
    session.clear()
    session[CHAVE_SESSAO_USUARIO_ID] = usuario.id
    session[CHAVE_SESSAO_NOME] = usuario.nome_completo
    session[CHAVE_SESSAO_LOGIN] = usuario.login
    session[CHAVE_SESSAO_PERFIL] = usuario.perfil
    session.permanent = True

    database.atualizar_ultimo_login(usuario.id)

    guiche = None
    if usuario.perfil == PerfilUsuario.ATENDENTE:
        qtd_guiches = config_manager.obter("qtd_guiches", 5)
        guiche = database.ocupar_proximo_guiche_disponivel(usuario.id, usuario.nome_completo, qtd_guiches)

        if guiche is None:
            logger.warning(
                "Usuário '%s' logou, mas não há guichês disponíveis (limite: %s).",
                usuario.login,
                qtd_guiches,
            )

    session[CHAVE_SESSAO_GUICHE] = guiche

    database.registrar_log("INFO", f"Login realizado: '{usuario.login}' (perfil {usuario.perfil}, guichê {guiche}).")


def encerrar_sessao() -> None:
    """Libera o guichê ocupado e remove todos os dados da sessão atual."""
    usuario_id = session.get(CHAVE_SESSAO_USUARIO_ID)
    login = session.get(CHAVE_SESSAO_LOGIN)

    if usuario_id is not None:
        database.liberar_guiche(usuario_id)
        database.registrar_log("INFO", f"Logout realizado: '{login}'.")

    session.clear()


def usuario_logado() -> Optional[dict]:
    """
    Retorna um dicionário com os dados do usuário atualmente logado (lidos
    da sessão), ou ``None`` se não houver sessão ativa.

    Não consulta o banco a cada chamada (para não onerar toda requisição);
    a verificação de que o usuário continua ativo é feita separadamente
    pelo decorator ``login_required``.
    """
    usuario_id = session.get(CHAVE_SESSAO_USUARIO_ID)
    if usuario_id is None:
        return None

    return {
        "id": usuario_id,
        "nome_completo": session.get(CHAVE_SESSAO_NOME),
        "login": session.get(CHAVE_SESSAO_LOGIN),
        "perfil": session.get(CHAVE_SESSAO_PERFIL),
        "guiche": session.get(CHAVE_SESSAO_GUICHE),
    }


def eh_admin() -> bool:
    """Retorna ``True`` se o usuário logado possui perfil de administrador."""
    return session.get(CHAVE_SESSAO_PERFIL) == PerfilUsuario.ADMIN


# ---------------------------------------------------------------------------
# Decorators de proteção de rotas
# ---------------------------------------------------------------------------

def _requisicao_eh_api() -> bool:
    """Identifica se a requisição atual é para um endpoint de API (JSON)
    ou para uma página HTML, de modo a retornar o tipo de resposta
    apropriado quando o acesso é negado."""
    return request.path.startswith("/api/")


def login_required(funcao_view):
    """
    Decorator que exige uma sessão de login válida para acessar a rota.

    Também revalida, a cada requisição, se o usuário continua cadastrado
    e ativo no banco de dados — garantindo que uma desativação feita por
    um administrador tenha efeito imediato, mesmo que a sessão/cookie do
    usuário desativado ainda seja tecnicamente válida.
    """

    @wraps(funcao_view)
    def wrapper(*args, **kwargs):
        usuario_id = session.get(CHAVE_SESSAO_USUARIO_ID)

        if usuario_id is None:
            if _requisicao_eh_api():
                return jsonify({"sucesso": False, "erro": "Sessão expirada. Faça login novamente."}), 401
            return redirect(url_for("login_tela", proximo=request.path))

        usuario = database.obter_usuario_por_id(usuario_id)
        if usuario is None or not usuario.ativo:
            encerrar_sessao()
            if _requisicao_eh_api():
                return jsonify({"sucesso": False, "erro": "Usuário desativado ou removido."}), 403
            return redirect(url_for("login_tela"))

        return funcao_view(*args, **kwargs)

    return wrapper


def admin_required(funcao_view):
    """
    Decorator que exige, além de login válido, que o usuário possua o
    perfil de administrador. Deve ser combinado com ``login_required``
    (aplicado primeiro) nas rotas correspondentes.
    """

    @wraps(funcao_view)
    def wrapper(*args, **kwargs):
        if not eh_admin():
            if _requisicao_eh_api():
                return jsonify({"sucesso": False, "erro": "Acesso restrito a administradores."}), 403
            return redirect(url_for("index"))

        return funcao_view(*args, **kwargs)

    return wrapper
