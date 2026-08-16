# -*- coding: utf-8 -*-
"""
app.py
======

Ponto de entrada da aplicação SIGS (Sistema Integrado de Gerenciamento de
Senhas). Este módulo contém exclusivamente as rotas Flask (camada de
apresentação/API); toda a lógica de negócio está em ``database.py``, a
impressão física em ``printer.py`` e a configuração em ``config.py``.

Rotas principais:

    GET  /                      Tela principal (emissão/chamada de senhas) [login]
    GET  /painel                Painel público geral de chamadas (tela cheia) [público]
    GET  /painel/empresa/<id>   Painel público de UMA empresa (tela cheia) [público]
    GET  /painel/geral          Painel público resumo (emitidas/atendidas/canceladas) [público]
    GET  /configuracoes         Tela de configurações do sistema [admin]
    GET  /relatorios            Tela de geração de relatórios [admin]
    GET/POST /login             Autenticação de usuários
    POST /logout                Encerra sessão e libera o guichê/sala
    GET  /admin/usuarios        Gerenciamento de usuários [admin]
    GET  /admin/empresas        Gerenciamento de empresas do feirão [admin]

    POST /api/emitir            Emite uma nova senha (grava + imprime)
    POST /api/chamar            Chama a próxima senha da fila (FIFO; escopo automático por
                                 empresa quando o usuário logado é "recrutador")
    POST /api/repetir           Repete a última chamada realizada (mesmo escopo acima)
    POST /api/finalizar-atendimento  Finaliza o atendimento e já chama a próxima (idem)
    POST /api/reiniciar         Reinicia o contador de senhas
    GET  /api/painel/status     Dados consumidos pelo painel geral (polling)
    GET  /api/painel/empresa/<id>/status  Dados consumidos pelo painel de uma empresa
    GET  /api/painel/geral/status         Dados consumidos pelo painel-resumo público
    GET  /api/fila              Lista da fila atual (escopo automático por empresa p/ recrutador)
    POST /api/senha/<id>/finalizar
    POST /api/senha/<id>/cancelar

    GET  /api/config            Retorna as configurações atuais (JSON)
    POST /api/config            Atualiza as configurações do sistema
    GET  /api/impressoras       Lista as impressoras instaladas no Windows
    GET  /api/empresas          Lista as empresas ATIVAS (seletor de emissão)

    GET  /api/relatorios/csv        Exporta relatório em CSV
    GET  /api/relatorios/excel      Exporta relatório em Excel (.xlsx)
    GET  /api/relatorios/pdf        Exporta relatório em PDF
    GET  /api/relatorios/resumo     Retorna estatísticas resumidas (JSON)

Execução:
    Desenvolvimento -> python dev.py
    Produção (rede local) -> python wsgi.py
"""

import csv
import io
import re
from datetime import datetime, timedelta
from typing import Optional

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for

import auth
import database
from config import SIGS_VERSAO, STATIC_DIR, TEMPLATES_DIR, config_manager, logger, obter_secret_key
from models import PerfilUsuario
from printer import ErroImpressora, ImpressoraTermica

# ---------------------------------------------------------------------------
# Inicialização da aplicação Flask
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    static_folder=str(STATIC_DIR),
    template_folder=str(TEMPLATES_DIR),
)

# Chave secreta utilizada para assinar o cookie de sessão (login). É gerada
# automaticamente e persistida em disco por config.obter_secret_key().
app.secret_key = obter_secret_key()

# Sessões de login duram até 12 horas de inatividade (cobre um turno de
# atendimento inteiro sem exigir novo login no meio do expediente).
app.permanent_session_lifetime = timedelta(hours=12)

# Desativa o cache de arquivos estáticos (CSS/JS/imagens) no navegador.
# Sem isso, o navegador pode continuar usando uma cópia antiga de
# static/js/*.js mesmo após o arquivo ser atualizado no servidor, exigindo
# um "hard refresh" manual (Ctrl+F5) do usuário a cada atualização do
# sistema. Em produção de alto tráfego isso teria custo de performance,
# mas para um sistema interno de atendimento a atualização imediata é
# mais importante do que a economia de banda.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Garante que o banco de dados e as tabelas existam antes de qualquer
# requisição ser atendida.
database.inicializar_banco()


@app.context_processor
def injetar_usuario_logado():
    """
    Disponibiliza as variáveis ``usuario_logado``, ``eh_admin``,
    ``sigs_versao`` e ``identidade_empresa`` para TODOS os templates
    automaticamente, sem precisar repassá-las manualmente em cada chamada
    a ``render_template``. ``sigs_versao`` é usada pelo rodapé fixo (ver
    templates/layout.html).

    ``identidade_empresa`` é ``None`` para qualquer perfil que não seja
    "recrutador" (ou para um recrutador sem empresa vinculada/sem
    identidade visual definida). Quando presente, é um dicionário
    ``{"logo_path": ..., "cor": ...}`` com o logo/cor DA EMPRESA do
    recrutador logado — ``templates/layout.html`` usa ``cor`` para
    sobrescrever a variável CSS ``--cor-principal``, e ``index.html`` usa
    ``logo_path`` para trocar o logo do cabeçalho. Isso faz a tela
    principal do recrutador (e apenas ela — as demais telas são
    restritas a admin, que não tem empresa) "vestir" automaticamente a
    identidade visual da empresa em que ele está logado. O painel público
    de uma empresa (``/painel/empresa/<id>``) NÃO usa esta variável, pois
    é uma rota sem login: ele lê ``empresa.logo_path``/``empresa.cor_principal``
    diretamente do objeto ``empresa`` que a própria rota já passa ao
    template (ver ``painel_empresa`` abaixo).
    """
    usuario_sessao = auth.usuario_logado()

    identidade_empresa = None
    if usuario_sessao and usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR:
        empresa_id = usuario_sessao.get("empresa_id")
        if empresa_id:
            empresa = database.obter_empresa_por_id(empresa_id)
            if empresa and (empresa.logo_path or empresa.cor_principal):
                identidade_empresa = {"logo_path": empresa.logo_path, "cor": empresa.cor_principal}

    return {
        "usuario_logado": usuario_sessao,
        "eh_admin": auth.eh_admin(),
        "sigs_versao": SIGS_VERSAO,
        "identidade_empresa": identidade_empresa,
    }


# ---------------------------------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------------------------------

def resposta_erro(mensagem: str, codigo_http: int = 400):
    """Padroniza o formato de resposta de erro da API (JSON)."""
    logger.error(mensagem)
    return jsonify({"sucesso": False, "erro": mensagem}), codigo_http


def resposta_sucesso(dados: dict, codigo_http: int = 200):
    """Padroniza o formato de resposta de sucesso da API (JSON)."""
    payload = {"sucesso": True}
    payload.update(dados)
    return jsonify(payload), codigo_http


def _guiche_formatado(usuario_sessao: dict) -> Optional[str]:
    """
    Formata o guichê/sala do usuário logado como texto pronto para gravar
    em ``senhas.guiche`` e exibir no painel, ou ``None`` se o usuário não
    ocupa guichê/sala algum no momento.

    Dois formatos, conforme o perfil:
        - "atendente": ``"Guichê 01"`` (pool geral).
        - "recrutador": ``"Sala 01 — <Nome da Empresa>"`` (pool por
          empresa) — o nome da empresa é embutido no próprio texto para
          diferenciar visualmente das salas de outras empresas nos
          relatórios (a coluna "Empresa" também já cobre isso, mas o
          texto do guichê fica ambíguo sem essa distinção, já que a
          numeração 1..N se repete de forma independente em cada
          empresa).

    Centralizar essa formatação evita repetir a lógica em cada rota que
    precisa dela (``/api/chamar`` e ``/api/finalizar-atendimento``),
    reduzindo o risco de inconsistência.
    """
    guiche = usuario_sessao.get("guiche")
    if not guiche:
        return None
    if usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR:
        empresa_nome = usuario_sessao.get("empresa_nome") or "Empresa não identificada"
        return f"Sala {guiche:02d} — {empresa_nome}"
    return f"Guichê {guiche:02d}"


def _pode_gerenciar_senha(usuario_sessao: dict, senha_id: int) -> bool:
    """
    Verifica se o usuário logado pode finalizar/cancelar uma senha
    específica pelo id.

    Para todos os perfis exceto "recrutador", sempre retorna ``True``
    (comportamento inalterado desde antes da existência dos
    recrutadores — qualquer usuário logado podia gerenciar qualquer
    senha). Para "recrutador", só retorna ``True`` se a senha pertencer à
    empresa vinculada ao usuário, impedindo que um recrutador
    finalize/cancele senhas de outra empresa mesmo sabendo o id.

    Se a senha não existir, retorna ``True`` propositalmente: deixa a
    própria rota (``finalizar_senha``/``cancelar_senha``) responder com o
    404 apropriado, em vez desta função inventar uma resposta de
    permissão para um recurso inexistente.
    """
    if usuario_sessao.get("perfil") != PerfilUsuario.RECRUTADOR:
        return True

    senha = database.obter_senha_por_id(senha_id)
    if senha is None:
        return True

    return senha.empresa == usuario_sessao.get("empresa_nome")


# ---------------------------------------------------------------------------
# Rotas de páginas (HTML)
# ---------------------------------------------------------------------------

@app.route("/")
@auth.login_required
def index():
    """Tela principal, utilizada pelo atendente para emitir e chamar senhas.

    Exige login. O guichê exibido é o atribuído automaticamente ao usuário
    no momento em que ele autenticou (ver auth.iniciar_sessao)."""
    configuracoes = config_manager.obter_todas()
    return render_template("index.html", config=configuracoes)


@app.route("/favicon.ico")
def favicon():
    """
    Responde imediatamente com "sem conteúdo" para requisições automáticas
    de favicon feitas pelo navegador, evitando que elas caiam no
    tratamento de erro 404 (o que geraria uma página de erro completa, ou
    ruído desnecessário nos logs, para um recurso puramente cosmético).
    """
    return "", 204


@app.route("/health")
def health():
    """
    Endpoint de verificação de saúde ("health check"), útil para confirmar
    rapidamente se o servidor Flask está no ar E se o banco de dados
    SQLite está acessível — por exemplo, em scripts de monitoramento/deploy,
    ou para diagnosticar um problema de arquivo/permissão em
    "database/senhas.db".

    Sempre público (sem exigir login), pois seu único propósito é
    diagnóstico técnico e não expõe nenhum dado sensível do sistema.
    """
    try:
        total_usuarios = database.contar_usuarios()
        return resposta_sucesso(
            {
                "status": "ok",
                "banco_de_dados": "conectado",
                "total_usuarios_cadastrados": total_usuarios,
            }
        )
    except Exception as erro:  # pragma: no cover - falha de infraestrutura
        return resposta_erro(f"Falha na conexão com o banco de dados: {erro}", 503)


@app.route("/painel")
def painel():
    """
    Painel público de chamadas, projetado para exibição em TV/monitor.

    Esta tela é INTENCIONALMENTE pública (sem exigência de login): ela é
    voltada ao público que aguarda atendimento, e não a operadores do
    sistema. Apenas telas operacionais/administrativas exigem login.
    """
    configuracoes = config_manager.obter_todas()
    return render_template("painel.html", config=configuracoes)


@app.route("/painel/empresa/<int:empresa_id>")
def painel_empresa(empresa_id: int):
    """
    Painel público de UMA empresa do feirão, projetado para exibição em
    TV/monitor dentro da sala de entrevistas daquela empresa. Mostra
    apenas a fila e a chamada atual daquela empresa (ver
    ``/api/painel/empresa/<id>/status``).

    Assim como o painel geral (``/painel``), é INTENCIONALMENTE público
    (sem exigência de login): o candidato aguardando na sala não precisa
    de conta no sistema. Quem CHAMA a próxima senha é o recrutador,
    logado na tela principal (ver ``index``) — este painel é apenas o
    display, não tem controles de chamada.

    Retorna 404 se a empresa não existir (id inválido/removido).
    """
    empresa = database.obter_empresa_por_id(empresa_id)
    if empresa is None:
        abort(404)

    configuracoes = config_manager.obter_todas()
    return render_template("painel_empresa.html", config=configuracoes, empresa=empresa)


@app.route("/painel/geral")
def painel_geral():
    """
    Painel público resumo do feirão inteiro: total de senhas emitidas,
    aguardando, em atendimento, finalizadas e canceladas, com o detalhe
    por empresa (ver ``/api/painel/geral/status``). Também projetado para
    TV/monitor, sem exigência de login.
    """
    configuracoes = config_manager.obter_todas()
    return render_template("painel_geral.html", config=configuracoes)


@app.route("/configuracoes")
@auth.login_required
@auth.admin_required
def configuracoes_tela():
    """Tela de configurações gerais do sistema. Acesso restrito a administradores."""
    configuracoes = config_manager.obter_todas()
    impressoras = ImpressoraTermica.listar_impressoras_instaladas()
    return render_template("configuracoes.html", config=configuracoes, impressoras=impressoras)


@app.route("/relatorios")
@auth.login_required
@auth.admin_required
def relatorios_tela():
    """Tela de geração de relatórios (CSV, Excel, PDF). Acesso restrito a administradores."""
    configuracoes = config_manager.obter_todas()
    return render_template("relatorios.html", config=configuracoes)


# ---------------------------------------------------------------------------
# Rotas de autenticação (login / logout)
#
# Não existe autocadastro público: o primeiro administrador é criado pelo
# script de linha de comando "criar_admin.py" (ver README, seção 12.3), e
# todos os demais usuários são cadastrados exclusivamente por um
# administrador já logado, pela tela "Gerenciar Usuários"
# (/admin/usuarios). Isso evita que qualquer pessoa com acesso ao
# navegador crie sua própria conta no sistema.
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login_tela():
    """
    Tela de login. Todo acesso ao sistema (exceto o painel público) exige
    autenticação prévia. No POST, valida as credenciais e, em caso de
    sucesso, atribui automaticamente o próximo guichê disponível ao
    usuário (ver auth.iniciar_sessao).
    """
    if auth.usuario_logado():
        return redirect(url_for("index"))

    erro = None
    login_informado = ""
    if request.method == "POST":
        login_informado = request.form.get("login", "")
        senha_informada = request.form.get("senha", "")

        usuario, erro = auth.autenticar(login_informado, senha_informada)
        if usuario is not None:
            auth.iniciar_sessao(usuario)
            destino = request.args.get("proximo")
            if destino and destino.startswith("/"):
                return redirect(destino)
            return redirect(url_for("index"))

    # Em caso de erro, o login digitado é reenviado ao template para que o
    # campo não precise ser redigitado — apenas a senha é sempre limpa por
    # segurança (nunca reenviamos senha de volta ao HTML).
    return render_template("login.html", erro=erro, login_informado=login_informado)


@app.route("/logout", methods=["POST"])
@auth.login_required
def logout_tela():
    """Encerra a sessão do usuário e libera o guichê que ele ocupava."""
    auth.encerrar_sessao()
    return redirect(url_for("login_tela"))


# ---------------------------------------------------------------------------
# Administração de usuários (apenas administradores)
# ---------------------------------------------------------------------------

@app.route("/admin/usuarios")
@auth.login_required
@auth.admin_required
def usuarios_tela():
    """Tela de gerenciamento de usuários: criação, reset de senha,
    ativação/desativação, alteração de perfil e (para recrutadores) o
    vínculo com uma empresa do feirão. Acesso restrito a administradores."""
    usuarios = database.listar_usuarios()
    guiches_ocupados = database.listar_guiches_ocupados()
    guiches_empresa_ocupados = database.listar_guiches_empresa_ocupados()
    empresas = database.listar_empresas()
    return render_template(
        "usuarios.html",
        config=config_manager.obter_todas(),
        usuarios=usuarios,
        guiches_ocupados=guiches_ocupados,
        guiches_empresa_ocupados=guiches_empresa_ocupados,
        empresas=empresas,
    )


# ---------------------------------------------------------------------------
# Administração de empresas do feirão (apenas administradores)
# ---------------------------------------------------------------------------

@app.route("/admin/empresas")
@auth.login_required
@auth.admin_required
def empresas_tela():
    """Tela de gerenciamento das empresas participantes do feirão do
    emprego: cadastro, renomeação e ativação/desativação. Acesso restrito
    a administradores. Empresas ativas aparecem no seletor exibido ao
    emitir uma senha (ver index.html/index.js)."""
    empresas = database.listar_empresas()
    return render_template(
        "empresas.html",
        config=config_manager.obter_todas(),
        empresas=empresas,
    )


# ---------------------------------------------------------------------------
# API - Emissão e chamada de senhas
# ---------------------------------------------------------------------------

@app.route("/api/emitir", methods=["POST"])
@auth.login_required
def api_emitir():
    """
    Emite uma nova senha: grava no banco de dados e envia para impressão
    imediatamente. Caso a impressão falhe, a senha permanece gravada no
    banco (o atendimento não deve ser bloqueado por falha de impressora),
    mas o erro é reportado ao cliente para que o atendente seja avisado.

    O guichê e o nome do atendente são obtidos diretamente da sessão de
    login (nunca do corpo da requisição), evitando que um atendente emita
    senhas em nome de outro guichê/usuário.

    O corpo da requisição pode opcionalmente incluir ``{"impressora": "Nome"}``
    para imprimir este ticket em uma impressora específica, escolhida pelo
    usuário na janela de impressão exibida ao clicar em "Emitir Senha" (ver
    index.js). Se omitido ou vazio, usa a impressora padrão configurada em
    Configurações (ou a impressora padrão do Windows, se nenhuma estiver
    configurada).

    O corpo da requisição DEVE incluir ``{"empresa_id": <int>}``: a
    seleção da empresa do feirão é obrigatória para emitir uma senha (ver
    tela "Empresas", /admin/empresas). A empresa é validada no servidor
    (existe e está ativa) — nunca confiamos apenas na validação do
    formulário no navegador.

    A numeração da senha (``senha.numero``) é POR EMPRESA: cada empresa
    tem sua própria sequência independente 001, 002, 003... (ver
    ``database.criar_senha``) — duas empresas diferentes podem ter, ao
    mesmo tempo, uma senha de número 001.

    O ticket impresso usa o LOGO DA PRÓPRIA EMPRESA (``empresa.logo_path``),
    não mais o logo padrão do sistema configurado em Configurações. Se a
    empresa ainda não tiver um logo cadastrado, o ticket é impresso sem
    nenhum logo (ver ``_extrair_cor_predominante``/tela Empresas para
    fazer o upload).
    """
    try:
        dados = request.get_json(silent=True) or {}
        impressora_escolhida = str(dados.get("impressora") or "").strip()
        empresa_id_bruto = dados.get("empresa_id")

        if empresa_id_bruto in (None, ""):
            return resposta_erro("Selecione a empresa para emitir a senha.", 400)
        try:
            empresa_id = int(empresa_id_bruto)
        except (TypeError, ValueError):
            return resposta_erro("Empresa inválida.", 400)

        empresa = database.obter_empresa_por_id(empresa_id)
        if empresa is None or not empresa.ativa:
            return resposta_erro(
                "Empresa inválida ou inativa. Atualize a página e tente novamente.", 400
            )

        usuario_sessao = auth.usuario_logado()
        guiche = f"Guichê {usuario_sessao['guiche']:02d}" if usuario_sessao.get("guiche") else None
        usuario = usuario_sessao.get("nome_completo")

        senha = database.criar_senha(
            empresa_id=empresa.id, empresa=empresa.nome, guiche=guiche, usuario=usuario
        )

        erro_impressao = None
        try:
            configuracoes = config_manager.obter_todas()
            nome_impressora = impressora_escolhida or configuracoes.get("nome_impressora") or None
            impressora = ImpressoraTermica(nome_impressora)
            # Logo IMPRESSO no ticket: o logo do SISTEMA (config.logo_path)
            # não é mais usado aqui — agora imprimimos o logo DA PRÓPRIA
            # EMPRESA. "empresa.logo_path" é relativo à pasta "static/"
            # (ex.: "img/empresas/3.png"), enquanto o Pillow (usado por
            # printer.py) abre o arquivo relativo à raiz do projeto — daí
            # o prefixo "static/" abaixo. Empresa sem logo cadastrado
            # imprime sem nenhum logo (sem fallback para o logo do
            # sistema, propositalmente).
            caminho_logo_empresa = f"static/{empresa.logo_path}" if empresa.logo_path else None
            impressora.imprimir_senha(
                numero=senha.numero,
                nome_evento=configuracoes.get("nome_evento", ""),
                caminho_logo=caminho_logo_empresa,
                nome_empresa=empresa.nome,
            )
        except ErroImpressora as erro:
            erro_impressao = str(erro)

        resultado = {"senha": senha.to_dict()}
        if erro_impressao:
            resultado["aviso_impressao"] = erro_impressao

        return resposta_sucesso(resultado, 201)

    except Exception as erro:  # pragma: no cover - proteção contra falhas inesperadas
        return resposta_erro(f"Erro ao emitir senha: {erro}", 500)


@app.route("/api/chamar", methods=["POST"])
@auth.login_required
def api_chamar():
    """
    Chama a próxima senha da fila (FIFO), sempre em nome do guichê e do
    usuário atualmente logados (obtidos da sessão, nunca do corpo da
    requisição, para evitar chamadas em nome de outro guichê).

    Quando o perfil logado é "recrutador", a fila é automaticamente
    restrita à empresa vinculada ao usuário (``usuario_sessao['empresa_nome']``)
    — um recrutador nunca chama uma senha de outra empresa. Para o perfil
    "atendente", o comportamento é o de sempre: a fila GERAL (sem filtro
    de empresa).
    """
    try:
        usuario_sessao = auth.usuario_logado()
        eh_recrutador = usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR

        if not usuario_sessao.get("guiche"):
            mensagem = (
                "Você não possui uma sala atribuída no momento (todas ocupadas "
                "nesta empresa). Faça logout e login novamente ou contate um "
                "administrador."
                if eh_recrutador
                else "Você não possui um guichê atribuído no momento (todos "
                "ocupados). Faça logout e login novamente ou contate um "
                "administrador."
            )
            return resposta_erro(mensagem, 409)

        guiche = _guiche_formatado(usuario_sessao)
        usuario = usuario_sessao.get("nome_completo")
        empresa_filtro = usuario_sessao.get("empresa_nome") if eh_recrutador else None

        resultado = database.chamar_proxima(guiche=guiche, usuario=usuario, empresa=empresa_filtro)
        if resultado is None:
            return resposta_erro("Não há senhas aguardando chamada.", 404)

        return resposta_sucesso({"chamada": resultado})

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao chamar próxima senha: {erro}", 500)


@app.route("/api/repetir", methods=["POST"])
@auth.login_required
def api_repetir():
    """
    Repete a última chamada realizada (nova animação/bip no painel).

    Para o perfil "recrutador", repete apenas a última chamada DA SUA
    PRÓPRIA empresa (nunca a última chamada geral, que pode pertencer a
    outra empresa) — ver ``database.repetir_ultima_chamada``.
    """
    try:
        usuario_sessao = auth.usuario_logado()
        empresa_filtro = (
            usuario_sessao.get("empresa_nome")
            if usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR
            else None
        )

        resultado = database.repetir_ultima_chamada(empresa=empresa_filtro)
        if resultado is None:
            return resposta_erro("Nenhuma chamada foi realizada ainda.", 404)

        return resposta_sucesso({"chamada": resultado})

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao repetir chamada: {erro}", 500)


@app.route("/api/finalizar-atendimento", methods=["POST"])
@auth.login_required
def api_finalizar_atendimento():
    """
    Finaliza o atendimento em andamento no guichê do usuário logado e
    chama automaticamente a próxima senha da fila. Se não houver mais
    senhas aguardando, retorna sucesso com um aviso informando que o
    atendente deve aguardar a emissão de uma nova senha (isto NÃO é
    tratado como erro, pois é uma situação normal do dia a dia).
    """
    try:
        usuario_sessao = auth.usuario_logado()
        eh_recrutador = usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR

        if not usuario_sessao.get("guiche"):
            mensagem = (
                "Você não possui uma sala atribuída no momento. Apenas usuários "
                "com perfil recrutador e sala ativa podem finalizar atendimentos."
                if eh_recrutador
                else "Você não possui um guichê atribuído no momento. Apenas "
                "usuários com perfil atendente e guichê ativo podem finalizar "
                "atendimentos."
            )
            return resposta_erro(mensagem, 409)

        guiche = _guiche_formatado(usuario_sessao)
        usuario = usuario_sessao.get("nome_completo")
        empresa_filtro = usuario_sessao.get("empresa_nome") if eh_recrutador else None

        resultado = database.finalizar_atendimento_e_chamar_proxima(
            guiche=guiche, usuario=usuario, empresa=empresa_filtro
        )

        resposta = {
            "senha_finalizada": resultado["senha_finalizada"],
            "chamada": resultado["chamada"],
        }
        if resultado["chamada"] is None:
            resposta["aviso"] = "Atendimento finalizado. Aguardando nova senha ser emitida."

        return resposta_sucesso(resposta)

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao finalizar atendimento: {erro}", 500)


@app.route("/api/reiniciar", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_reiniciar():
    """
    Reinicia o contador de numeração de senhas de TODAS as empresas para
    zero (cada empresa tem sua própria sequência independente — ver
    ``database.criar_senha``). Restrito a administradores (é uma operação
    sensível que afeta todas as empresas de uma vez). Para reiniciar
    apenas UMA empresa, use
    ``/api/admin/empresas/<id>/reiniciar-contador`` (botão por linha na
    tela Empresas).
    """
    try:
        database.reiniciar_contador()
        return resposta_sucesso({"mensagem": "Contador reiniciado com sucesso (todas as empresas)."})
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao reiniciar contador: {erro}", 500)


@app.route("/api/fila")
@auth.login_required
def api_fila():
    """
    Retorna a fila atual de senhas aguardando chamada.

    Para o perfil "recrutador", a fila é automaticamente restrita à
    empresa vinculada ao usuário — ele só vê (e só pode cancelar) as
    senhas da sua própria empresa. Para os demais perfis, mantém o
    comportamento de sempre: a fila GERAL, sem filtro de empresa.
    """
    try:
        usuario_sessao = auth.usuario_logado()
        empresa_filtro = (
            usuario_sessao.get("empresa_nome")
            if usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR
            else None
        )

        fila = database.listar_fila_atual(empresa=empresa_filtro)
        total = database.contar_aguardando(empresa=empresa_filtro)
        return resposta_sucesso({"fila": fila, "total_aguardando": total})
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao consultar fila: {erro}", 500)


@app.route("/api/senha/<int:senha_id>/finalizar", methods=["POST"])
@auth.login_required
def api_finalizar(senha_id: int):
    """Marca uma senha como finalizada. Um recrutador só pode finalizar
    senhas da sua própria empresa (ver ``_pode_gerenciar_senha``)."""
    if not _pode_gerenciar_senha(auth.usuario_logado(), senha_id):
        return resposta_erro("Você não tem permissão para gerenciar esta senha.", 403)
    if database.finalizar_senha(senha_id):
        return resposta_sucesso({"mensagem": "Senha finalizada."})
    return resposta_erro("Senha não encontrada.", 404)


@app.route("/api/senha/<int:senha_id>/cancelar", methods=["POST"])
@auth.login_required
def api_cancelar(senha_id: int):
    """Marca uma senha como cancelada. Um recrutador só pode cancelar
    senhas da sua própria empresa (ver ``_pode_gerenciar_senha``)."""
    if not _pode_gerenciar_senha(auth.usuario_logado(), senha_id):
        return resposta_erro("Você não tem permissão para gerenciar esta senha.", 403)
    if database.cancelar_senha(senha_id):
        return resposta_sucesso({"mensagem": "Senha cancelada."})
    return resposta_erro("Senha não encontrada.", 404)


# ---------------------------------------------------------------------------
# API - Painel público (polling)
# ---------------------------------------------------------------------------

@app.route("/api/painel/status")
def api_painel_status():
    """
    Endpoint consultado periodicamente (a cada N segundos, conforme
    configuração) pelo painel público via Fetch/AJAX. Retorna apenas os
    dados necessários para atualização (sem recarregar a página inteira).
    """
    try:
        configuracoes = config_manager.obter_todas()
        qtd_exibidas = configuracoes.get("qtd_senhas_exibidas", 10)

        agora = datetime.now()

        return resposta_sucesso(
            {
                "chamada_atual": database.obter_chamada_atual(),
                "ultimas_emitidas": database.listar_ultimas_emitidas(qtd_exibidas),
                "data": agora.strftime("%d/%m/%Y"),
                "hora": agora.strftime("%H:%M:%S"),
                "config": configuracoes,
            }
        )
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao consultar status do painel: {erro}", 500)


@app.route("/api/painel/empresa/<int:empresa_id>/status")
def api_painel_empresa_status(empresa_id: int):
    """
    Endpoint consultado periodicamente pelo painel público de UMA empresa
    (``/painel/empresa/<id>``). Mesmo formato de ``/api/painel/status``,
    mas com a chamada atual e as últimas emitidas restritas àquela
    empresa (ver ``database.obter_chamada_atual``/``listar_ultimas_emitidas``
    com o parâmetro ``empresa``).
    """
    empresa = database.obter_empresa_por_id(empresa_id)
    if empresa is None:
        return resposta_erro("Empresa não encontrada.", 404)

    try:
        configuracoes = config_manager.obter_todas()
        qtd_exibidas = configuracoes.get("qtd_senhas_exibidas", 10)

        agora = datetime.now()

        return resposta_sucesso(
            {
                "empresa": empresa.to_dict(),
                "chamada_atual": database.obter_chamada_atual(empresa=empresa.nome),
                "ultimas_emitidas": database.listar_ultimas_emitidas(qtd_exibidas, empresa=empresa.nome),
                "total_aguardando": database.contar_aguardando(empresa=empresa.nome),
                "data": agora.strftime("%d/%m/%Y"),
                "hora": agora.strftime("%H:%M:%S"),
                "config": configuracoes,
            }
        )
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao consultar status do painel da empresa: {erro}", 500)


@app.route("/api/painel/geral/status")
def api_painel_geral_status():
    """
    Endpoint consultado periodicamente pelo painel-resumo público
    (``/painel/geral``). Retorna o resumo agregado de senhas por status
    (total geral e detalhado por empresa) — ver
    ``database.resumo_geral_senhas``.
    """
    try:
        agora = datetime.now()

        return resposta_sucesso(
            {
                "resumo": database.resumo_geral_senhas(),
                "data": agora.strftime("%d/%m/%Y"),
                "hora": agora.strftime("%H:%M:%S"),
                "config": config_manager.obter_todas(),
            }
        )
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao consultar o painel geral: {erro}", 500)


# ---------------------------------------------------------------------------
# API - Configurações
# ---------------------------------------------------------------------------

@app.route("/api/config", methods=["GET"])
@auth.login_required
@auth.admin_required
def api_config_obter():
    """Retorna todas as configurações atuais do sistema. Restrito a administradores."""
    return resposta_sucesso({"config": config_manager.obter_todas()})


@app.route("/api/config", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_config_salvar():
    """Atualiza uma ou mais configurações do sistema. Restrito a administradores."""
    try:
        dados = request.get_json(silent=True) or {}
        if not dados:
            return resposta_erro("Nenhum dado de configuração foi enviado.", 400)

        config_manager.salvar(dados)
        return resposta_sucesso({"config": config_manager.obter_todas()})

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao salvar configurações: {erro}", 500)


@app.route("/api/impressoras")
@auth.login_required
def api_impressoras():
    """
    Lista as impressoras instaladas no Windows.

    Diferente das demais rotas de configuração, esta é acessível a
    QUALQUER usuário logado (não apenas administradores): o emissor de
    senhas precisa desta lista para escolher a impressora na janela
    exibida ao clicar em "Emitir Senha" (ver index.js). Apenas ALTERAR a
    impressora padrão do sistema (tela Configurações) continua restrito a
    administradores.
    """
    return resposta_sucesso({"impressoras": ImpressoraTermica.listar_impressoras_instaladas()})


@app.route("/api/empresas")
@auth.login_required
def api_empresas():
    """
    Lista as empresas ATIVAS do feirão, usadas para popular o seletor de
    empresa exibido ao emitir uma senha (ver index.html/index.js).

    Assim como ``/api/impressoras``, é acessível a QUALQUER usuário
    logado (não apenas administradores), pois é o perfil "emissor" — não
    o admin — quem efetivamente emite senhas.
    """
    return resposta_sucesso({"empresas": database.listar_empresas(somente_ativas=True)})


# ---------------------------------------------------------------------------
# API - Relatórios
# ---------------------------------------------------------------------------

def _parametros_periodo():
    """Extrai e retorna os parâmetros de período (inicio/fim) e o filtro
    opcional de empresa (nome exato) da querystring."""
    inicio = request.args.get("inicio") or None
    fim = request.args.get("fim") or None
    empresa = request.args.get("empresa") or None
    return inicio, fim, empresa


@app.route("/api/relatorios/resumo")
@auth.login_required
@auth.admin_required
def api_relatorios_resumo():
    """Retorna um resumo estatístico (JSON) para exibição na tela de
    relatórios: total emitidas, total chamadas, tempo médio de espera e a
    contagem de senhas emitidas por empresa (``por_empresa``), opcional-
    mente filtrado por uma empresa específica (querystring ``empresa``)."""
    try:
        inicio, fim, empresa = _parametros_periodo()
        emitidas = database.listar_senhas_periodo(inicio, fim, empresa=empresa)
        chamadas = database.listar_chamadas_periodo(inicio, fim, empresa=empresa)
        tempo_medio = database.tempo_medio_atendimento(inicio, fim, empresa=empresa)
        por_empresa = database.listar_contagem_por_empresa(inicio, fim)

        return resposta_sucesso(
            {
                "total_emitidas": len(emitidas),
                "total_chamadas": len(chamadas),
                "tempo_medio": tempo_medio,
                "por_empresa": por_empresa,
            }
        )
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao gerar resumo: {erro}", 500)


@app.route("/api/relatorios/csv")
@auth.login_required
@auth.admin_required
def api_relatorios_csv():
    """Gera e retorna um relatório em formato CSV para download."""
    try:
        tipo = request.args.get("tipo", "emitidas")
        inicio, fim, empresa = _parametros_periodo()

        buffer_texto = io.StringIO()
        escritor = csv.writer(buffer_texto, delimiter=";")

        if tipo == "chamadas":
            escritor.writerow(["ID Evento", "ID Senha", "Número", "Empresa", "Guichê", "Usuário", "Data/Hora"])
            for item in database.listar_chamadas_periodo(inicio, fim, empresa=empresa):
                escritor.writerow(
                    [
                        item["id"], item["senha_id"], item["numero"], item.get("empresa") or "-",
                        item["guiche"], item["usuario"], item["data_hora"],
                    ]
                )
            nome_arquivo = "relatorio_chamadas.csv"
        else:
            escritor.writerow(["ID", "Número", "Status", "Empresa", "Data/Hora", "Guichê", "Usuário"])
            for item in database.listar_senhas_periodo(inicio, fim, empresa=empresa):
                escritor.writerow(
                    [
                        item["id"], item["numero"], item["status"], item.get("empresa") or "-",
                        item["data_hora"], item["guiche"], item["usuario"],
                    ]
                )
            nome_arquivo = "relatorio_emitidas.csv"

        # Codifica em UTF-8 com BOM para compatibilidade com Excel no Windows.
        buffer_bytes = io.BytesIO(buffer_texto.getvalue().encode("utf-8-sig"))
        buffer_bytes.seek(0)

        return send_file(
            buffer_bytes,
            mimetype="text/csv",
            as_attachment=True,
            download_name=nome_arquivo,
        )

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao gerar relatório CSV: {erro}", 500)


@app.route("/api/relatorios/excel")
@auth.login_required
@auth.admin_required
def api_relatorios_excel():
    """Gera e retorna um relatório em formato Excel (.xlsx) para download."""
    try:
        # Importação local para não exigir openpyxl caso o relatório em
        # Excel nunca seja utilizado (reduz acoplamento e tempo de boot).
        from openpyxl import Workbook
        from openpyxl.styles import Font

        tipo = request.args.get("tipo", "emitidas")
        inicio, fim, empresa = _parametros_periodo()

        pasta = Workbook()
        planilha = pasta.active

        if tipo == "chamadas":
            planilha.title = "Chamadas"
            cabecalho = ["ID Evento", "ID Senha", "Número", "Empresa", "Guichê", "Usuário", "Data/Hora"]
            planilha.append(cabecalho)
            for item in database.listar_chamadas_periodo(inicio, fim, empresa=empresa):
                planilha.append(
                    [
                        item["id"], item["senha_id"], item["numero"], item.get("empresa") or "-",
                        item["guiche"], item["usuario"], item["data_hora"],
                    ]
                )
            nome_arquivo = "relatorio_chamadas.xlsx"
        else:
            planilha.title = "Emitidas"
            cabecalho = ["ID", "Número", "Status", "Empresa", "Data/Hora", "Guichê", "Usuário"]
            planilha.append(cabecalho)
            for item in database.listar_senhas_periodo(inicio, fim, empresa=empresa):
                planilha.append(
                    [
                        item["id"], item["numero"], item["status"], item.get("empresa") or "-",
                        item["data_hora"], item["guiche"], item["usuario"],
                    ]
                )
            nome_arquivo = "relatorio_emitidas.xlsx"

        for celula in planilha[1]:
            celula.font = Font(bold=True)

        buffer_bytes = io.BytesIO()
        pasta.save(buffer_bytes)
        buffer_bytes.seek(0)

        return send_file(
            buffer_bytes,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=nome_arquivo,
        )

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao gerar relatório Excel: {erro}", 500)


@app.route("/api/relatorios/pdf")
@auth.login_required
@auth.admin_required
def api_relatorios_pdf():
    """Gera e retorna um relatório em formato PDF para download.

    Importante: este PDF é exclusivamente um RELATÓRIO GERENCIAL, e não
    deve ser confundido com o ticket de senha — o ticket impresso ao
    atendente NUNCA utiliza PDF, apenas impressão GDI direta (ver
    printer.py). O uso de PDF aqui é apenas para consulta/análise dos
    dados operacionais.
    """
    try:
        # Importações locais, mesmo racional de desempenho do relatório Excel.
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet

        tipo = request.args.get("tipo", "emitidas")
        inicio, fim, empresa = _parametros_periodo()

        buffer_bytes = io.BytesIO()
        documento = SimpleDocTemplate(buffer_bytes, pagesize=A4)
        estilos = getSampleStyleSheet()
        elementos = []

        titulo = "Relatório de Senhas Emitidas" if tipo != "chamadas" else "Relatório de Chamadas"
        elementos.append(Paragraph(titulo, estilos["Title"]))
        elementos.append(Spacer(1, 0.5 * cm))

        if tipo == "chamadas":
            dados_tabela = [["ID Evento", "ID Senha", "Número", "Empresa", "Guichê", "Usuário", "Data/Hora"]]
            for item in database.listar_chamadas_periodo(inicio, fim, empresa=empresa):
                dados_tabela.append(
                    [
                        str(item["id"]),
                        str(item["senha_id"]),
                        f"{item['numero']:03d}",
                        item.get("empresa") or "-",
                        item["guiche"] or "-",
                        item["usuario"] or "-",
                        item["data_hora"],
                    ]
                )
            nome_arquivo = "relatorio_chamadas.pdf"
        else:
            dados_tabela = [["ID", "Número", "Status", "Empresa", "Data/Hora", "Guichê", "Usuário"]]
            for item in database.listar_senhas_periodo(inicio, fim, empresa=empresa):
                dados_tabela.append(
                    [
                        str(item["id"]),
                        f"{item['numero']:03d}",
                        item["status"],
                        item.get("empresa") or "-",
                        item["data_hora"],
                        item["guiche"] or "-",
                        item["usuario"] or "-",
                    ]
                )
            nome_arquivo = "relatorio_emitidas.pdf"

        # Inclui o resumo de tempo médio de atendimento ao final do relatório.
        tempo_medio = database.tempo_medio_atendimento(inicio, fim, empresa=empresa)

        tabela = Table(dados_tabela, repeatRows=1)
        tabela.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003C71")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EAF1FA")]),
                ]
            )
        )
        elementos.append(tabela)
        elementos.append(Spacer(1, 0.7 * cm))
        elementos.append(
            Paragraph(
                f"Tempo médio de atendimento: {tempo_medio['tempo_medio_formatado']} "
                f"(baseado em {tempo_medio['total_amostras']} amostra(s)).",
                estilos["Normal"],
            )
        )

        documento.build(elementos)
        buffer_bytes.seek(0)

        return send_file(
            buffer_bytes,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=nome_arquivo,
        )

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao gerar relatório PDF: {erro}", 500)


# ---------------------------------------------------------------------------
# API - Administração de usuários (apenas administradores)
# ---------------------------------------------------------------------------

@app.route("/api/admin/usuarios", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_criar_usuario():
    """Cria um novo usuário diretamente pelo painel de administração,
    permitindo ao administrador definir o perfil (admin, atendente ou
    emissor) já na criação. Esta é a ÚNICA forma de cadastrar um usuário
    pelo navegador — não existe autocadastro público (ver
    "criar_admin.py" para criar o primeiro administrador via linha de
    comando)."""
    try:
        dados = request.get_json(silent=True) or {}
        nome_completo = str(dados.get("nome_completo") or "").strip()
        login_novo = str(dados.get("login") or "").strip()
        senha = str(dados.get("senha") or "")
        perfil = str(dados.get("perfil") or PerfilUsuario.ATENDENTE).strip()
        empresa_id_bruto = dados.get("empresa_id")

        if not nome_completo or not login_novo:
            return resposta_erro("Informe nome completo e login.", 400)

        erro_senha = auth.validar_forca_senha(senha)
        if erro_senha:
            return resposta_erro(erro_senha, 400)

        if perfil not in PerfilUsuario.TODOS:
            return resposta_erro("Perfil inválido.", 400)

        # Um recrutador PRECISA estar vinculado a uma empresa já na
        # criação (é assim que ele sabe qual fila atender ao logar — ver
        # auth.iniciar_sessao). Para os demais perfis, qualquer
        # empresa_id enviado é simplesmente ignorado.
        empresa_id = None
        if perfil == PerfilUsuario.RECRUTADOR:
            if empresa_id_bruto in (None, ""):
                return resposta_erro("Selecione a empresa do recrutador.", 400)
            try:
                empresa_id = int(empresa_id_bruto)
            except (TypeError, ValueError):
                return resposta_erro("Empresa inválida.", 400)
            if database.obter_empresa_por_id(empresa_id) is None:
                return resposta_erro("Empresa não encontrada.", 400)

        usuario = database.criar_usuario(
            nome_completo=nome_completo,
            login=login_novo,
            senha_hash=auth.gerar_hash_senha(senha),
            perfil=perfil,
            empresa_id=empresa_id,
        )
        return resposta_sucesso({"usuario": usuario.to_dict_publico()}, 201)

    except ValueError as erro:
        return resposta_erro(str(erro), 409)
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao criar usuário: {erro}", 500)


@app.route("/api/admin/usuarios/<int:usuario_id>/resetar-senha", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_resetar_senha(usuario_id: int):
    """Reseta (redefine) a senha de login de um usuário. Esta é a
    funcionalidade de 'reset de senha' exigida para o administrador do
    sistema — distinta do reinício do contador de senhas de atendimento."""
    try:
        dados = request.get_json(silent=True) or {}
        nova_senha = str(dados.get("nova_senha") or "")

        erro_senha = auth.validar_forca_senha(nova_senha)
        if erro_senha:
            return resposta_erro(erro_senha, 400)

        if database.resetar_senha_usuario(usuario_id, auth.gerar_hash_senha(nova_senha)):
            return resposta_sucesso({"mensagem": "Senha redefinida com sucesso."})
        return resposta_erro("Usuário não encontrado.", 404)

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao redefinir senha: {erro}", 500)


@app.route("/api/admin/usuarios/<int:usuario_id>/perfil", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_definir_perfil(usuario_id: int):
    """Altera o perfil (admin/atendente/emissor) de um usuário."""
    try:
        dados = request.get_json(silent=True) or {}
        perfil = str(dados.get("perfil") or "").strip()

        if database.definir_perfil_usuario(usuario_id, perfil):
            return resposta_sucesso({"mensagem": "Perfil atualizado."})
        return resposta_erro("Usuário não encontrado.", 404)

    except ValueError as erro:
        return resposta_erro(str(erro), 400)
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao atualizar perfil: {erro}", 500)


@app.route("/api/admin/usuarios/<int:usuario_id>/status", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_definir_status(usuario_id: int):
    """Ativa ou desativa o acesso de um usuário ao sistema."""
    try:
        dados = request.get_json(silent=True) or {}
        ativo = bool(dados.get("ativo", True))

        if database.definir_status_usuario(usuario_id, ativo):
            if not ativo:
                # Libera imediatamente o guichê/sala do usuário desativado
                # (um dos dois DELETE é sempre um no-op, dependendo do
                # perfil — ver auth.encerrar_sessao para o mesmo padrão).
                database.liberar_guiche(usuario_id)
                database.liberar_guiche_empresa(usuario_id)
            return resposta_sucesso({"mensagem": "Status do usuário atualizado."})
        return resposta_erro("Usuário não encontrado.", 404)

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao atualizar status: {erro}", 500)


@app.route("/api/admin/usuarios/<int:usuario_id>/empresa", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_definir_empresa_usuario(usuario_id: int):
    """
    Vincula (ou desvincula, enviando ``empresa_id: null``) um recrutador a
    uma empresa do feirão. Usado pelo seletor de empresa na tabela de
    usuários (``/admin/usuarios``) — separado da rota de perfil
    (``.../perfil``) porque o administrador pode querer trocar a empresa
    de um recrutador já existente sem alterar o perfil dele.
    """
    try:
        dados = request.get_json(silent=True) or {}
        empresa_id_bruto = dados.get("empresa_id")

        empresa_id = None
        if empresa_id_bruto not in (None, ""):
            try:
                empresa_id = int(empresa_id_bruto)
            except (TypeError, ValueError):
                return resposta_erro("Empresa inválida.", 400)
            if database.obter_empresa_por_id(empresa_id) is None:
                return resposta_erro("Empresa não encontrada.", 400)

        if database.definir_empresa_usuario(usuario_id, empresa_id):
            return resposta_sucesso({"mensagem": "Empresa do recrutador atualizada."})
        return resposta_erro("Usuário não encontrado.", 404)

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao atualizar empresa do usuário: {erro}", 500)


@app.route("/api/admin/reset-senhas-emitidas", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_reset_senhas_emitidas():
    """
    Apaga TODO o histórico de senhas emitidas e chamadas, reiniciando o
    contador para zero. Operação destrutiva e irreversível, restrita a
    administradores — exige confirmação explícita no corpo da requisição
    (``{"confirmar": true}``) para evitar acionamento acidental.
    """
    try:
        dados = request.get_json(silent=True) or {}
        if dados.get("confirmar") is not True:
            return resposta_erro("Confirmação obrigatória para esta operação destrutiva.", 400)

        database.resetar_senhas_emitidas()
        return resposta_sucesso({"mensagem": "Todas as senhas emitidas foram apagadas."})

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao resetar senhas emitidas: {erro}", 500)


@app.route("/api/admin/guiches")
@auth.login_required
@auth.admin_required
def api_admin_guiches():
    """Retorna a lista de guichês atualmente ocupados (monitoramento)."""
    return resposta_sucesso({"guiches": database.listar_guiches_ocupados()})


# ---------------------------------------------------------------------------
# API - Administração de empresas do feirão (apenas administradores)
# ---------------------------------------------------------------------------

@app.route("/api/admin/empresas", methods=["GET"])
@auth.login_required
@auth.admin_required
def api_admin_listar_empresas():
    """
    Lista TODAS as empresas (ativas e inativas).

    Usada pelo filtro de empresa da tela de Relatórios: diferente do
    seletor de emissão (``/api/empresas``, só ativas), o filtro de
    relatórios precisa incluir empresas já desativadas, já que o
    histórico de senhas emitidas para elas continua consultável.
    """
    return resposta_sucesso({"empresas": database.listar_empresas()})


@app.route("/api/admin/empresas", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_criar_empresa():
    """Cadastra uma nova empresa participante do feirão do emprego."""
    try:
        dados = request.get_json(silent=True) or {}
        nome = str(dados.get("nome") or "").strip()

        if not nome:
            return resposta_erro("Informe o nome da empresa.", 400)

        empresa = database.criar_empresa(nome)
        return resposta_sucesso({"empresa": empresa.to_dict()}, 201)

    except ValueError as erro:
        return resposta_erro(str(erro), 409)
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao cadastrar empresa: {erro}", 500)


@app.route("/api/admin/empresas/<int:empresa_id>/renomear", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_renomear_empresa(empresa_id: int):
    """Renomeia uma empresa já cadastrada. Senhas já emitidas para ela
    mantêm o nome antigo gravado (sem retroatividade)."""
    try:
        dados = request.get_json(silent=True) or {}
        novo_nome = str(dados.get("nome") or "").strip()

        if database.renomear_empresa(empresa_id, novo_nome):
            return resposta_sucesso({"mensagem": "Empresa renomeada com sucesso."})
        return resposta_erro("Empresa não encontrada.", 404)

    except ValueError as erro:
        return resposta_erro(str(erro), 409)
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao renomear empresa: {erro}", 500)


@app.route("/api/admin/empresas/<int:empresa_id>/status", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_status_empresa(empresa_id: int):
    """Ativa ou desativa uma empresa. Empresas inativas deixam de aparecer
    no seletor de emissão de senha, mas o histórico de senhas já emitidas
    para elas permanece intacto para fins de relatório."""
    try:
        dados = request.get_json(silent=True) or {}
        ativa = bool(dados.get("ativa", True))

        if database.definir_status_empresa(empresa_id, ativa):
            return resposta_sucesso({"mensagem": "Status da empresa atualizado."})
        return resposta_erro("Empresa não encontrada.", 404)

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao atualizar status da empresa: {erro}", 500)


@app.route("/api/admin/empresas/<int:empresa_id>/reiniciar-contador", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_reiniciar_contador_empresa(empresa_id: int):
    """
    Reinicia para zero o contador de numeração de senhas de UMA ÚNICA
    empresa (a próxima senha emitida para ela volta a ser 001), sem
    afetar a sequência das demais empresas nem apagar o histórico de
    senhas já emitidas.

    Cada empresa possui sua própria sequência independente de numeração
    (ver ``database.criar_senha``); este botão substitui, por empresa, o
    antigo botão único "Reiniciar Contador" de Configurações — aquele
    agora reinicia TODAS as empresas de uma vez (ver
    ``database.reiniciar_contador``).
    """
    try:
        if database.reiniciar_contador_empresa(empresa_id):
            return resposta_sucesso({"mensagem": "Contador de senhas da empresa reiniciado."})
        return resposta_erro("Empresa não encontrada.", 404)

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao reiniciar contador da empresa: {erro}", 500)


# ---------------------------------------------------------------------------
# API - Identidade visual da empresa (logo + cor)
# ---------------------------------------------------------------------------
#
# Cada empresa pode ter seu próprio logo e cor de destaque, aplicados
# automaticamente no painel público daquela empresa (/painel/empresa/<id>)
# e na tela principal de um recrutador vinculado a ela (ver
# injetar_usuario_logado). A cor é sempre extraída automaticamente do
# logo enviado (ver _extrair_cor_predominante), mas pode ser sobrescrita
# manualmente a qualquer momento pela rota .../cor abaixo.

EXTENSOES_LOGO_PERMITIDAS = {"png", "jpg", "jpeg", "gif", "webp"}
PASTA_LOGOS_EMPRESAS = STATIC_DIR / "img" / "empresas"


def _extrair_cor_predominante(caminho_imagem) -> str:
    """
    Calcula uma cor hexadecimal (``"#RRGGBB"``) representativa de uma
    imagem, usada como sugestão automática de "cor da empresa" ao enviar
    um logo.

    Técnica simples e barata: reduz a imagem inteira a 1x1 pixel com
    reamostragem suavizada (``Image.LANCZOS``), o que produz efetivamente
    a cor MÉDIA de toda a imagem — não é uma extração sofisticada de
    "cor dominante" (não identifica clusters de cor), mas é suficiente
    para gerar uma cor de destaque plausível sem dependências extras além
    do Pillow (já usado por ``printer.py``).

    Logos com fundo transparente (PNG/GIF com canal alfa) são compostos
    sobre um fundo BRANCO antes do cálculo — sem isso, pixels
    transparentes tendem a ser interpretados como preto puro pelo Pillow,
    o que enviesaria a média para tons escuros mesmo em logos claros.
    """
    from PIL import Image

    with Image.open(caminho_imagem) as imagem:
        if imagem.mode in ("RGBA", "LA") or (imagem.mode == "P" and "transparency" in imagem.info):
            imagem_rgba = imagem.convert("RGBA")
            fundo_branco = Image.new("RGB", imagem_rgba.size, (255, 255, 255))
            fundo_branco.paste(imagem_rgba, mask=imagem_rgba.split()[-1])
            imagem_rgb = fundo_branco
        else:
            imagem_rgb = imagem.convert("RGB")

        r, g, b = imagem_rgb.resize((1, 1), Image.LANCZOS).getpixel((0, 0))

    return f"#{r:02X}{g:02X}{b:02X}"


@app.route("/api/admin/empresas/<int:empresa_id>/logo", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_upload_logo_empresa(empresa_id: int):
    """
    Recebe o upload do logo de uma empresa (campo de formulário
    ``multipart/form-data`` chamado ``logo``), salva o arquivo em
    ``static/img/empresas/<id>.<extensão>`` e extrai automaticamente a
    cor predominante da imagem, gravando os dois valores de uma vez (ver
    ``database.definir_logo_empresa``).

    Se a empresa já tinha um logo com uma extensão DIFERENTE (por
    exemplo, trocou de .png para .jpg), o arquivo antigo é removido do
    disco para não acumular arquivos órfãos.
    """
    empresa = database.obter_empresa_por_id(empresa_id)
    if empresa is None:
        return resposta_erro("Empresa não encontrada.", 404)

    arquivo = request.files.get("logo")
    if arquivo is None or not arquivo.filename:
        return resposta_erro("Envie um arquivo de imagem no campo 'logo'.", 400)

    extensao = arquivo.filename.rsplit(".", 1)[-1].lower() if "." in arquivo.filename else ""
    if extensao not in EXTENSOES_LOGO_PERMITIDAS:
        return resposta_erro(
            f"Formato de imagem não suportado. Use um dos seguintes: {', '.join(sorted(EXTENSOES_LOGO_PERMITIDAS))}.",
            400,
        )

    PASTA_LOGOS_EMPRESAS.mkdir(parents=True, exist_ok=True)

    # Remove um logo anterior com extensão diferente (evita arquivo órfão
    # em disco quando a empresa troca, por exemplo, de .png para .jpg).
    for extensao_existente in EXTENSOES_LOGO_PERMITIDAS:
        if extensao_existente == extensao:
            continue
        caminho_antigo = PASTA_LOGOS_EMPRESAS / f"{empresa_id}.{extensao_existente}"
        if caminho_antigo.exists():
            try:
                caminho_antigo.unlink()
            except OSError as erro:  # pragma: no cover - falha de infraestrutura
                logger.warning("Não foi possível remover o logo antigo '%s': %s", caminho_antigo, erro)

    caminho_arquivo = PASTA_LOGOS_EMPRESAS / f"{empresa_id}.{extensao}"

    try:
        arquivo.save(str(caminho_arquivo))
        cor_extraida = _extrair_cor_predominante(caminho_arquivo)
    except Exception as erro:
        # Cobre tanto falha de escrita em disco quanto um arquivo que não
        # é realmente uma imagem válida (upload corrompido/malicioso) —
        # o Pillow levanta uma exceção ao tentar abrir esse último caso.
        if caminho_arquivo.exists():
            caminho_arquivo.unlink(missing_ok=True)
        return resposta_erro(f"Não foi possível processar o arquivo de imagem enviado: {erro}", 400)

    logo_path_relativo = f"img/empresas/{empresa_id}.{extensao}"
    database.definir_logo_empresa(empresa_id, logo_path_relativo, cor_extraida)

    return resposta_sucesso({"logo_path": logo_path_relativo, "cor_principal": cor_extraida})


@app.route("/api/admin/empresas/<int:empresa_id>/cor", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_definir_cor_empresa(empresa_id: int):
    """
    Sobrescreve manualmente a cor de identidade visual de uma empresa
    (sem alterar o logo), usada quando o administrador não gosta da cor
    extraída automaticamente do logo enviado.
    """
    try:
        dados = request.get_json(silent=True) or {}
        cor = str(dados.get("cor") or "").strip()

        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", cor):
            return resposta_erro("Informe uma cor no formato hexadecimal, ex.: #003C71.", 400)

        if database.definir_cor_empresa(empresa_id, cor.upper()):
            return resposta_sucesso({"cor_principal": cor.upper()})
        return resposta_erro("Empresa não encontrada.", 404)

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao atualizar cor da empresa: {erro}", 500)


# ---------------------------------------------------------------------------
# Tratamento global de erros
# ---------------------------------------------------------------------------

def _config_para_erro() -> dict:
    """
    Retorna as configurações do sistema para uso na página de erro amigável
    (erro.html), com uma proteção extra: se a própria leitura das
    configurações falhar (por exemplo, o erro 500 foi causado justamente
    por uma falha de acesso ao banco de dados), cai de volta em um valor
    padrão mínimo em vez de gerar um segundo erro dentro do tratamento do
    primeiro erro.
    """
    try:
        return config_manager.obter_todas()
    except Exception:  # pragma: no cover - proteção contra falha em cascata
        return {"cor_principal": "#003C71"}


def _requisicao_eh_api() -> bool:
    """Identifica se a requisição atual é para um endpoint de API (JSON) ou
    para uma página HTML. Usado pelos error handlers abaixo para decidir
    se devolvem JSON (para chamadas de tela feitas via JavaScript) ou uma
    página HTML amigável (para navegação direta do usuário, ex.: digitar
    uma URL errada ou clicar em um link quebrado)."""
    return request.path.startswith("/api/")


@app.errorhandler(404)
def erro_404(_erro):
    if _requisicao_eh_api():
        return jsonify({"sucesso": False, "erro": "Recurso não encontrado."}), 404
    return (
        render_template(
            "erro.html",
            config=_config_para_erro(),
            codigo=404,
            titulo="Página não encontrada",
            mensagem="O endereço acessado não existe ou foi movido.",
        ),
        404,
    )


@app.errorhandler(500)
def erro_500(erro):
    logger.error("Erro interno não tratado: %s", erro)
    if _requisicao_eh_api():
        return jsonify({"sucesso": False, "erro": "Erro interno do servidor."}), 500
    return (
        render_template(
            "erro.html",
            config=_config_para_erro(),
            codigo=500,
            titulo="Erro interno do servidor",
            mensagem=(
                "Algo deu errado ao processar sua solicitação. Tente novamente "
                "em instantes; se o problema persistir, procure um administrador."
            ),
        ),
        500,
    )


@app.errorhandler(403)
def erro_403(_erro):
    if _requisicao_eh_api():
        return jsonify({"sucesso": False, "erro": "Acesso negado."}), 403
    return (
        render_template(
            "erro.html",
            config=_config_para_erro(),
            codigo=403,
            titulo="Acesso negado",
            mensagem="Você não tem permissão para acessar esta página.",
        ),
        403,
    )


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------
#
# Este arquivo NÃO deve mais ser executado diretamente (`python app.py`).
# Use um dos dois pontos de entrada dedicados, na raiz do projeto:
#
#     python dev.py    -> desenvolvimento (debug + reload automático)
#     python wsgi.py   -> produção na rede local (servidor waitress)
#
# Eles apenas importam o `app` definido acima e o servem de formas
# diferentes; a aplicação em si continua inteira neste arquivo.
