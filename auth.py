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

import hmac
import secrets
import threading
import time
from functools import wraps
from typing import Dict, Optional

from flask import flash, jsonify, redirect, request, session, url_for
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
# As duas chaves abaixo só são preenchidas para o perfil "recrutador" (ver
# iniciar_sessao): identificam a empresa à qual o recrutador está
# vinculado, usada para filtrar a fila/chamadas às senhas daquela empresa.
CHAVE_SESSAO_EMPRESA_ID = "empresa_id"
CHAVE_SESSAO_EMPRESA_NOME = "empresa_nome"

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


# Hash "fantasma" (de uma senha que não existe) usado em ``autenticar``
# quando o login informado não corresponde a nenhum usuário. Sem isso, um
# login inexistente responderia mais rápido que um login existente com
# senha errada (pois puluar a chamada de verificar_senha economiza o
# tempo de CPU do PBKDF2), permitindo a um atacante descobrir quais
# logins existem no sistema só cronometrando as respostas ("timing
# attack" de enumeração de usuário). Comparar sempre contra um hash —
# real ou fantasma — mantém o tempo de resposta consistente nos dois
# casos.
_HASH_FANTASMA = generate_password_hash("sigs-hash-fantasma-login-inexistente")


# ---------------------------------------------------------------------------
# Limite de tentativas de login (proteção contra força bruta)
# ---------------------------------------------------------------------------
#
# Estado guardado em memória (não no banco de dados): é informação
# puramente transitória, reiniciada a cada vez que o servidor reinicia —
# o suficiente para o objetivo aqui, que é atrapalhar um script tentando
# adivinhar senhas por tentativa e erro, não uma auditoria permanente.
# Protegido por um lock porque o waitress atende requisições em várias
# threads simultâneas (ver wsgi.py).

LIMITE_TENTATIVAS_LOGIN = 5
JANELA_TENTATIVAS_SEGUNDOS = 5 * 60   # tentativas fora desta janela "prescrevem"
BLOQUEIO_SEGUNDOS = 5 * 60            # tempo bloqueado após atingir o limite

_lock_tentativas_login = threading.Lock()
# login normalizado -> {"falhas": int, "ultima_falha": float (time.time())}
_tentativas_login: Dict[str, dict] = {}


def segundos_login_bloqueado(login: str) -> Optional[int]:
    """
    Retorna quantos segundos ainda faltam para este login poder tentar
    novamente, ou ``None`` se ele não está bloqueado no momento.
    """
    chave = (login or "").strip().lower()
    if not chave:
        return None

    agora = time.time()
    with _lock_tentativas_login:
        registro = _tentativas_login.get(chave)
        if registro is None:
            return None

        # Tentativas antigas (fora da janela) não contam mais — o login
        # não está mais "sob suspeita".
        if agora - registro["ultima_falha"] > JANELA_TENTATIVAS_SEGUNDOS:
            del _tentativas_login[chave]
            return None

        if registro["falhas"] < LIMITE_TENTATIVAS_LOGIN:
            return None

        restante = BLOQUEIO_SEGUNDOS - (agora - registro["ultima_falha"])
        return int(restante) if restante > 0 else None


def registrar_tentativa_falha(login: str) -> None:
    """Contabiliza mais uma tentativa de login malsucedida para este login."""
    chave = (login or "").strip().lower()
    if not chave:
        return

    agora = time.time()
    with _lock_tentativas_login:
        registro = _tentativas_login.get(chave)
        if registro is None or agora - registro["ultima_falha"] > JANELA_TENTATIVAS_SEGUNDOS:
            _tentativas_login[chave] = {"falhas": 1, "ultima_falha": agora}
        else:
            registro["falhas"] += 1
            registro["ultima_falha"] = agora


def limpar_tentativas_login(login: str) -> None:
    """Zera o contador de tentativas malsucedidas (chamado após um login
    bem-sucedido)."""
    chave = (login or "").strip().lower()
    with _lock_tentativas_login:
        _tentativas_login.pop(chave, None)


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
    contém o motivo (credenciais inválidas, usuário desativado, ou login
    temporariamente bloqueado por excesso de tentativas — ver
    ``segundos_login_bloqueado``).
    """
    login_normalizado = (login or "").strip()

    restante = segundos_login_bloqueado(login_normalizado)
    if restante is not None:
        minutos = max(1, restante // 60 + (1 if restante % 60 else 0))
        return None, (
            f"Muitas tentativas de login incorretas para este usuário. "
            f"Tente novamente em cerca de {minutos} minuto(s)."
        )

    usuario = database.obter_usuario_por_login(login_normalizado)

    # Comparamos SEMPRE contra um hash (real ou "fantasma"), mesmo quando
    # o login não existe, para não vazar essa informação por timing (ver
    # _HASH_FANTASMA).
    hash_para_comparar = usuario.senha_hash if usuario is not None else _HASH_FANTASMA
    senha_confere = verificar_senha(hash_para_comparar, senha or "")

    if usuario is None or not senha_confere:
        registrar_tentativa_falha(login_normalizado)
        return None, "Login ou senha inválidos."

    if not usuario.ativo:
        # Senha estava correta — não é uma tentativa de "adivinhação",
        # então não soma ao contador de força bruta.
        return None, "Este usuário está desativado. Procure um administrador do sistema."

    # Recrutador não usa mais login/senha (ver autenticar_por_chave_empresa
    # mais abaixo) — mesmo que uma conta antiga ainda tenha uma senha
    # válida cadastrada, o acesso por aqui é recusado, direcionando para o
    # novo fluxo. Senha estava correta, então (assim como no caso "ativo"
    # acima) isto não conta como tentativa malsucedida.
    if usuario.perfil == PerfilUsuario.RECRUTADOR:
        return None, (
            "Recrutadores agora entram pela página de acesso das empresas, "
            "usando a chave de 8 dígitos da empresa — não usam mais login e "
            "senha aqui."
        )

    limpar_tentativas_login(login_normalizado)
    return usuario, None


def autenticar_por_chave_empresa(empresa_id: int, chave: str, nome_completo: str):
    """
    Valida a chave de acesso de 8 dígitos informada para a empresa
    ``empresa_id`` — o novo login do recrutador (ver
    app.py:api_empresa_entrar), no lugar de login/senha individuais.

    Retorna uma tupla ``(usuario, mensagem_erro)``, no mesmo formato de
    ``autenticar()``. Em caso de sucesso, ``usuario`` é uma instância
    RECÉM-PROVISIONADA de ``models.Usuario`` — diferente do login
    tradicional, aqui NÃO existe uma conta pré-cadastrada para "encontrar":
    cada entrada pela chave cria uma conta de recrutador efêmera nova, com
    o nome informado agora (ver ``database.provisionar_usuario_recrutador``
    e ``encerrar_sessao``, que a remove ao deslogar).

    Reaproveita a MESMA proteção de força bruta do login tradicional
    (``segundos_login_bloqueado``/``registrar_tentativa_falha``), só que
    "escopada" por empresa em vez de por login — importante porque uma
    chave de 8 dígitos (100 milhões de combinações) é bem mais fraca que
    uma senha, então merece a mesma proteção contra tentativa e erro.
    """
    chave_de_bloqueio = f"chave-empresa-{empresa_id}"

    restante = segundos_login_bloqueado(chave_de_bloqueio)
    if restante is not None:
        minutos = max(1, restante // 60 + (1 if restante % 60 else 0))
        return None, (
            f"Muitas tentativas incorretas para esta empresa. "
            f"Tente novamente em cerca de {minutos} minuto(s)."
        )

    nome_normalizado = (nome_completo or "").strip()
    if not nome_normalizado:
        return None, "Informe seu nome para entrar."

    empresa = database.obter_empresa_por_id(empresa_id)
    if empresa is None:
        return None, "Empresa não encontrada."

    # Comparação em tempo constante (hmac.compare_digest) para não vazar,
    # por cronometragem, quantos dígitos da chave informada já "acertaram"
    # o prefixo da chave real — mesmo cuidado já tomado com senha (ver
    # _HASH_FANTASMA acima).
    chave_informada = (chave or "").strip()
    chave_confere = hmac.compare_digest(chave_informada, empresa.chave_acesso or "")

    if not chave_confere:
        registrar_tentativa_falha(chave_de_bloqueio)
        return None, "Chave de acesso inválida."

    if not empresa.ativa:
        # Chave estava correta — não é uma tentativa de "adivinhação".
        return None, "Esta empresa está desativada. Procure um administrador do sistema."

    limpar_tentativas_login(chave_de_bloqueio)

    # Hash de um valor aleatório descartável: esta conta nunca é
    # autenticada por login/senha (ver checagem de perfil recrutador em
    # ``autenticar`` acima), então o hash em si nunca precisa ser
    # verificado — só precisa existir, pois a coluna é NOT NULL.
    senha_hash_descartavel = gerar_hash_senha(secrets.token_urlsafe(32))
    usuario = database.provisionar_usuario_recrutador(empresa.id, nome_normalizado, senha_hash_descartavel)
    return usuario, None


def iniciar_sessao(usuario) -> None:
    """
    Grava os dados do usuário autenticado na sessão Flask.

    Usuários com perfil "atendente" assumem automaticamente um guichê da
    fila GERAL de atendimento. Usuários com perfil "recrutador" assumem
    automaticamente uma mesa/guichê dentro do pool DA SUA PRÓPRIA empresa
    (``usuario.empresa_id``, definido pelo administrador em "Gerenciar
    Usuários") — os dois pools são independentes entre si (ver
    ``database.ocupar_proximo_guiche_disponivel`` vs.
    ``database.ocupar_proximo_guiche_empresa_disponivel``). Administradores
    e emissores de senha NÃO ocupam guichê/mesa, pois não realizam
    chamadas de atendimento (o administrador gerencia o sistema; o emissor
    apenas emite senhas em um totem).
    """
    session.clear()
    session[CHAVE_SESSAO_USUARIO_ID] = usuario.id
    session[CHAVE_SESSAO_NOME] = usuario.nome_completo
    session[CHAVE_SESSAO_LOGIN] = usuario.login
    session[CHAVE_SESSAO_PERFIL] = usuario.perfil
    session.permanent = True

    database.atualizar_ultimo_login(usuario.id)

    guiche = None
    empresa_id = None
    empresa_nome = None

    if usuario.perfil == PerfilUsuario.ATENDENTE:
        qtd_guiches = config_manager.obter("qtd_guiches", 5)
        guiche = database.ocupar_proximo_guiche_disponivel(usuario.id, usuario.nome_completo, qtd_guiches)

        if guiche is None:
            logger.warning(
                "Usuário '%s' logou, mas não há guichês disponíveis (limite: %s).",
                usuario.login,
                qtd_guiches,
            )

    elif usuario.perfil == PerfilUsuario.RECRUTADOR:
        if usuario.empresa_id is None:
            logger.warning(
                "Usuário '%s' tem perfil recrutador, mas não está vinculado a "
                "nenhuma empresa. Peça a um administrador para vincular uma "
                "empresa em Gerenciar Usuários.",
                usuario.login,
            )
        else:
            empresa = database.obter_empresa_por_id(usuario.empresa_id)
            if empresa is None:
                logger.warning(
                    "Usuário '%s' está vinculado a uma empresa (id=%s) que não "
                    "existe mais.",
                    usuario.login,
                    usuario.empresa_id,
                )
            else:
                empresa_id = empresa.id
                empresa_nome = empresa.nome
                qtd_guiches_empresa = config_manager.obter("qtd_guiches_por_empresa", 3)
                guiche = database.ocupar_proximo_guiche_empresa_disponivel(
                    empresa_id, usuario.id, usuario.nome_completo, qtd_guiches_empresa
                )

                if guiche is None:
                    logger.warning(
                        "Recrutador '%s' logou, mas não há mesas disponíveis para "
                        "a empresa '%s' (limite: %s).",
                        usuario.login,
                        empresa_nome,
                        qtd_guiches_empresa,
                    )

    session[CHAVE_SESSAO_GUICHE] = guiche
    session[CHAVE_SESSAO_EMPRESA_ID] = empresa_id
    session[CHAVE_SESSAO_EMPRESA_NOME] = empresa_nome

    database.registrar_log("INFO", f"Login realizado: '{usuario.login}' (perfil {usuario.perfil}, guichê {guiche}).")


def encerrar_sessao() -> None:
    """Libera o guichê/mesa ocupado (em qualquer um dos dois pools — geral
    ou por empresa, um dos DELETE é sempre um no-op) e remove todos os
    dados da sessão atual."""
    usuario_id = session.get(CHAVE_SESSAO_USUARIO_ID)
    login = session.get(CHAVE_SESSAO_LOGIN)

    if usuario_id is not None:
        database.liberar_guiche(usuario_id)
        database.liberar_guiche_empresa(usuario_id)
        database.registrar_log("INFO", f"Logout realizado: '{login}'.")
        # Contas de recrutador provisionadas automaticamente pelo login por
        # chave da empresa (ver database.provisionar_usuario_recrutador)
        # são efêmeras — removidas aqui para não acumular uma linha nova em
        # "usuarios" a cada entrada. Sem efeito (no-op) para qualquer outra
        # conta: o DELETE só afeta linhas com provisionado_por_chave=1 (ver
        # database.excluir_usuario_provisionado).
        database.excluir_usuario_provisionado(usuario_id)

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
        "empresa_id": session.get(CHAVE_SESSAO_EMPRESA_ID),
        "empresa_nome": session.get(CHAVE_SESSAO_EMPRESA_NOME),
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

    Também revalida, a CADA requisição, três coisas contra o banco de
    dados (nunca confiando apenas no que foi gravado na sessão no momento
    do login):

        1. O usuário ainda existe e continua ativo — garante que uma
           desativação feita por um administrador tenha efeito imediato.
        2. O PERFIL do usuário não mudou — sem isso, um admin rebaixado
           para atendente (ou vice-versa) continuaria com o acesso
           antigo até o cookie de sessão expirar (até 12h, ver
           app.permanent_session_lifetime), mesmo já sem permissão no
           banco.
        3. Para o perfil "recrutador", a EMPRESA vinculada não mudou —
           sem isso, um recrutador reatribuído a outra empresa por um
           administrador continuaria enxergando/gerenciando a fila da
           empresa ANTIGA (guardada na sessão) até deslogar.

    Qualquer uma dessas três checagens falhando encerra a sessão
    imediatamente e exige novo login — o mesmo tratamento já dado à
    desativação de usuário, só que agora cobrindo também mudança de
    perfil/empresa, não apenas o campo "ativo".
    """

    @wraps(funcao_view)
    def wrapper(*args, **kwargs):
        usuario_id = session.get(CHAVE_SESSAO_USUARIO_ID)

        if usuario_id is None:
            if _requisicao_eh_api():
                return jsonify({"sucesso": False, "erro": "Sessão expirada. Faça login novamente."}), 401
            flash("Sua sessão expirou. Faça login novamente para continuar.", "erro")
            return redirect(url_for("login_tela", proximo=request.path))

        usuario = database.obter_usuario_por_id(usuario_id)
        if usuario is None or not usuario.ativo:
            encerrar_sessao()
            if _requisicao_eh_api():
                return jsonify({"sucesso": False, "erro": "Usuário desativado ou removido."}), 403
            flash("Seu usuário foi desativado ou removido. Procure um administrador.", "erro")
            return redirect(url_for("login_tela"))

        perfil_mudou = usuario.perfil != session.get(CHAVE_SESSAO_PERFIL)
        empresa_mudou = (
            usuario.perfil == PerfilUsuario.RECRUTADOR
            and usuario.empresa_id != session.get(CHAVE_SESSAO_EMPRESA_ID)
        )
        if perfil_mudou or empresa_mudou:
            encerrar_sessao()
            mensagem = "Seu acesso foi atualizado por um administrador. Faça login novamente."
            if _requisicao_eh_api():
                return jsonify({"sucesso": False, "erro": mensagem}), 401
            flash(mensagem, "erro")
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
            flash("Esta área é restrita a administradores.", "erro")
            return redirect(url_for("index"))

        return funcao_view(*args, **kwargs)

    return wrapper


def admin_ou_recrutador_required(funcao_view):
    """
    Decorator que exige, além de login válido, que o usuário possua o
    perfil de administrador OU recrutador. Usado pela tela de Relatórios
    (``/relatorios`` e ``/api/relatorios/*``), que agora também é
    acessível a um recrutador — restrita, na prática, aos dados da
    PRÓPRIA empresa dele (a própria rota, em ``app.py``, é responsável
    por forçar esse recorte; este decorator só garante que o perfil é um
    dos dois permitidos). Deve ser combinado com ``login_required``
    (aplicado primeiro) nas rotas correspondentes.
    """

    @wraps(funcao_view)
    def wrapper(*args, **kwargs):
        perfil = session.get(CHAVE_SESSAO_PERFIL)
        if perfil not in (PerfilUsuario.ADMIN, PerfilUsuario.RECRUTADOR):
            if _requisicao_eh_api():
                return (
                    jsonify({"sucesso": False, "erro": "Acesso restrito a administradores e recrutadores."}),
                    403,
                )
            flash("Esta área é restrita a administradores e recrutadores.", "erro")
            return redirect(url_for("index"))

        return funcao_view(*args, **kwargs)

    return wrapper
