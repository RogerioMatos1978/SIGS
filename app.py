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
    GET  /painel/geral          Painel público resumo (aguardando/em atendimento) [público]
    GET  /configuracoes         Tela de configurações do sistema [admin]
    GET  /relatorios            Tela de geração de relatórios [admin]
    GET/POST /login             Autenticação de usuários
    POST /logout                Encerra sessão e libera o guichê/mesa
    GET  /admin/usuarios        Gerenciamento de usuários [admin]
    GET  /admin/empresas        Gerenciamento de empresas do feirão [admin]

    POST /api/emitir            Emite uma nova senha (grava + imprime)
    POST /api/chamar            Chama a próxima senha da fila (FIFO; escopo automático por
                                 empresa quando o usuário logado é "recrutador")
    POST /api/chamar-varias     Chama um CONJUNTO de senhas escolhidas manualmente de uma vez
                                 (corpo: {"senha_ids": [...]}, mesmo escopo por empresa acima)
    POST /api/repetir           Repete a última chamada realizada (mesmo escopo acima)
    POST /api/finalizar-atendimento  Finaliza o atendimento e já chama a próxima (idem)
    POST /api/reiniciar         Reinicia o contador de senhas
    GET  /api/painel/status     Dados consumidos pelo painel geral (polling)
    GET  /api/painel/empresa/<id>/status  Dados consumidos pelo painel de uma empresa
    GET  /api/painel/geral/status         Dados consumidos pelo painel-resumo público
    GET  /api/fila              Lista da fila atual (escopo automático por empresa p/ recrutador)
    POST /api/senha/<id>/finalizar
    POST /api/senha/<id>/cancelar
    POST /api/senha/<id>/reimprimir  Reimprime o ticket (só se status == 'Emitida')

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

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, url_for

import auth
import database
from config import SIGS_VERSAO, STATIC_DIR, TEMPLATES_DIR, config_manager, logger, obter_secret_key
from models import PerfilUsuario, StatusSenha
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

# Reforço de segurança do cookie de sessão (defesa em profundidade contra
# CSRF, já que o sistema não usa tokens CSRF separados):
#   - SESSION_COOKIE_SAMESITE = "Lax": o navegador só envia o cookie de
#     sessão em navegações de nível superior (clicar num link) ou nas
#     próprias requisições do site; formulários/scripts de OUTRO site não
#     conseguem "andar" com a sessão do usuário. Os navegadores modernos
#     já aplicam "Lax" como padrão quando o atributo não é definido, mas
#     declarar explicitamente evita depender desse comportamento padrão
#     (nem todo navegador/webview usado em totens segue o mesmo padrão).
#   - SESSION_COOKIE_HTTPONLY = True: impede que o cookie de sessão seja
#     lido via JavaScript (document.cookie), mitigando o impacto de um
#     eventual XSS. Já é o padrão do Flask, mas fica explícito aqui.
#   - SESSION_COOKIE_SECURE não é ativado: o SIGS roda em HTTP simples na
#     rede local (sem HTTPS/certificado) — marcar o cookie como "Secure"
#     faria o navegador simplesmente parar de enviá-lo, quebrando o login.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

# Limite de tamanho do corpo de uma requisição (protege contra upload
# excessivamente grande no campo de logo da empresa — ver
# api_admin_upload_logo_empresa — que carrega o arquivo inteiro na
# memória via Pillow para extrair a cor predominante).
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB

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
    """
    Padroniza o formato de resposta de erro da API (JSON).

    A mensagem completa (que em vários pontos do código embute o texto
    cru de uma exceção Python, ex.: ``f"Erro ao emitir senha: {erro}"``)
    é SEMPRE registrada no log para diagnóstico. Mas para códigos 5xx
    (falhas internas inesperadas — banco de dados, impressora, etc.), o
    que volta para o CLIENTE é uma mensagem genérica: o texto de uma
    exceção pode revelar detalhes internos (caminhos de arquivo, nomes de
    tabela/coluna, driver do banco) que não servem para o usuário e não
    deveriam ser expostos por uma API, mesmo numa rede local. Para
    códigos 4xx (validação de entrada, permissão, "não encontrado"), a
    mensagem é escrita deliberadamente pelo próprio código da rota (não é
    o texto de uma exceção) e continua sendo devolvida normalmente — é
    exatamente o que orienta o usuário a corrigir o problema.
    """
    logger.error(mensagem)
    mensagem_publica = "Ocorreu um erro interno. Tente novamente; se persistir, procure um administrador." if codigo_http >= 500 else mensagem
    return jsonify({"sucesso": False, "erro": mensagem_publica}), codigo_http


def resposta_sucesso(dados: dict, codigo_http: int = 200):
    """Padroniza o formato de resposta de sucesso da API (JSON)."""
    payload = {"sucesso": True}
    payload.update(dados)
    return jsonify(payload), codigo_http


def _guiche_formatado(usuario_sessao: dict) -> Optional[str]:
    """
    Formata o guichê/mesa do usuário logado como texto pronto para gravar
    em ``senhas.guiche`` e exibir no painel, ou ``None`` se o usuário não
    ocupa guichê/mesa algum no momento.

    Dois formatos, conforme o perfil:
        - "atendente": ``"Guichê 01"`` (pool geral).
        - "recrutador": ``"Mesa 01 — <Nome da Empresa>"`` (pool por
          empresa — vários recrutadores da MESMA empresa atendem em uma
          única sala, cada um em sua própria mesa numerada) — o nome da
          empresa é embutido no próprio texto para diferenciar
          visualmente das mesas de outras empresas nos relatórios (a
          coluna "Empresa" também já cobre isso, mas o texto do guichê
          fica ambíguo sem essa distinção, já que a numeração 1..N se
          repete de forma independente em cada empresa).

    Centralizar essa formatação evita repetir a lógica em cada rota que
    precisa dela (``/api/chamar`` e ``/api/finalizar-atendimento``),
    reduzindo o risco de inconsistência.
    """
    guiche = usuario_sessao.get("guiche")
    if not guiche:
        return None
    if usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR:
        empresa_nome = usuario_sessao.get("empresa_nome") or "Empresa não identificada"
        return f"Mesa {guiche:02d} — {empresa_nome}"
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

    A comparação é feita por ``empresa_id`` (referência estável), NUNCA
    pelo nome da empresa: comparar por nome permitiria que, após uma
    empresa ser renomeada, um cadastro diferente que reaproveitasse esse
    nome antigo passasse a "herdar" permissão sobre senhas que nunca
    foram dele — ver ``database._migrar_tabela_senhas_adicionar_empresa_id``.
    """
    if usuario_sessao.get("perfil") != PerfilUsuario.RECRUTADOR:
        return True

    senha = database.obter_senha_por_id(senha_id)
    if senha is None:
        return True

    return senha.empresa_id == usuario_sessao.get("empresa_id")


# ---------------------------------------------------------------------------
# Rotas de páginas (HTML)
# ---------------------------------------------------------------------------

@app.route("/")
@auth.login_required
def index():
    """Tela principal, utilizada pelo atendente para emitir e chamar senhas.

    Exige login. O guichê exibido é o atribuído automaticamente ao usuário
    no momento em que ele autenticou (ver auth.iniciar_sessao).

    Para o perfil "recrutador", também busca a própria empresa
    (``empresa_recrutador``) para que o template saiba se a emissão de
    senhas já está bloqueada (ver ``database.bloquear_emissao_empresa``)
    e ajuste os botões exibidos de acordo."""
    configuracoes = config_manager.obter_todas()

    usuario_sessao = auth.usuario_logado()
    empresa_recrutador = None
    if usuario_sessao and usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR:
        empresa_id = usuario_sessao.get("empresa_id")
        if empresa_id:
            empresa_recrutador = database.obter_empresa_por_id(empresa_id)

    return render_template("index.html", config=configuracoes, empresa_recrutador=empresa_recrutador)


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
    Painel público resumo do feirão inteiro: senhas aguardando e em
    atendimento, com o detalhe por empresa (ver
    ``/api/painel/geral/status``). Também projetado para TV/monitor, sem
    exigência de login.

    Propositalmente não mostra senhas finalizadas nem canceladas — mesmo
    critério aplicado a todos os painéis públicos (ver
    ``database.listar_ultimas_emitidas``).
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
@auth.admin_ou_recrutador_required
def relatorios_tela():
    """
    Tela de geração de relatórios (CSV, Excel, PDF). Acessível a
    administradores (veem TODAS as empresas, com filtro) e a
    recrutadores (veem SOMENTE a própria empresa, sem filtro — ver
    ``eh_recrutador``/``empresa_recrutador`` usados pelo template para
    esconder o seletor "Empresa" nesse caso).
    """
    configuracoes = config_manager.obter_todas()
    usuario_sessao = auth.usuario_logado()
    eh_recrutador = usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR
    empresa_recrutador = None
    if eh_recrutador and usuario_sessao.get("empresa_id"):
        empresa_recrutador = database.obter_empresa_por_id(usuario_sessao["empresa_id"])
    return render_template(
        "relatorios.html", config=configuracoes, eh_recrutador=eh_recrutador, empresa_recrutador=empresa_recrutador
    )


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

def _listar_empresas_publicas() -> list:
    """
    Lista as empresas que podem aparecer em telas PÚBLICAS (sem login):
    só as ATIVAS e que não sejam uma das duas opções fixas do sistema
    ("Criar Currículos"/"Imprimir Currículos" — ver
    ``database.NOMES_EMPRESAS_FIXAS``, que não têm recrutador nem login
    por chave). Remove sempre ``chave_acesso`` de cada empresa antes de
    devolver: esse dado nunca deve chegar ao HTML/JS de uma página
    pública, mesmo que o template atual não o exiba.

    Compartilhada por ``login_tela`` (painel de acesso das empresas ao
    lado do formulário de login) e ``empresas_entrar_tela`` (a mesma
    lista, em tela cheia) — evita duplicar o filtro em dois lugares.
    """
    return [
        {chave: valor for chave, valor in empresa.items() if chave != "chave_acesso"}
        for empresa in database.listar_empresas(somente_ativas=True)
        if not empresa.get("fixa")
    ]


@app.route("/login", methods=["GET", "POST"])
def login_tela():
    """
    Tela de login. Todo acesso ao sistema (exceto o painel público) exige
    autenticação prévia. No POST, valida as credenciais e, em caso de
    sucesso, atribui automaticamente o próximo guichê disponível ao
    usuário (ver auth.iniciar_sessao).

    Também exibe, ao lado do formulário, um painel com as empresas
    cadastradas (``_listar_empresas_publicas``) e um link de acesso
    direto para o login de cada uma (``/empresas/<id>/entrar``) — evita
    que o recrutador precise navegar até ``/empresas/entrar`` só para
    encontrar sua empresa.
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
    #
    # IMPORTANTE: é preciso passar "config" explicitamente aqui, como toda
    # outra rota do sistema faz. Sem isso, o Flask injeta automaticamente
    # sua PRÓPRIA variável global "config" (app.config, as configurações
    # internas do Flask) no lugar — que não possui "cor_principal", fazendo
    # layout.html calcular "--cor-principal: ;" (vazio). Isso invalidava a
    # propriedade CSS "background" do botão "Entrar" (voltando ao branco
    # padrão do navegador), enquanto o texto branco (--cor-branco) permanecia
    # aplicado — resultando em texto branco sobre fundo branco, ou seja,
    # botão "sem texto" visível. Bug real corrigido em 2026-08-16.
    return render_template(
        "login.html",
        erro=erro,
        login_informado=login_informado,
        config=config_manager.obter_todas(),
        empresas=_listar_empresas_publicas(),
    )


@app.route("/logout", methods=["POST"])
@auth.login_required
def logout_tela():
    """Encerra a sessão do usuário e libera o guichê que ele ocupava."""
    auth.encerrar_sessao()
    return redirect(url_for("login_tela"))


# ---------------------------------------------------------------------------
# Acesso das empresas (recrutador) — login por chave, sem senha individual
# ---------------------------------------------------------------------------

@app.route("/empresas/entrar")
def empresas_entrar_tela():
    """
    Página PÚBLICA (sem login) com um card para cada empresa ATIVA do
    feirão. Clicar em um card leva ao formulário de acesso daquela empresa
    (``/empresas/<id>/entrar``), onde o recrutador informa seu nome e a
    chave de 8 dígitos da empresa — substitui o antigo cadastro individual
    de login/senha (ver auth.autenticar_por_chave_empresa).

    As duas opções fixas do sistema ("Criar Currículos"/"Imprimir
    Currículos" — ver ``database.NOMES_EMPRESAS_FIXAS``) NUNCA aparecem
    aqui: elas não são empresas reais participantes do feirão, não têm
    recrutador, e um login por chave para elas nunca faria sentido (ver
    também ``empresa_login_tela``, que bloqueia o acesso direto pela URL).

    Essa mesma tela cheia continua existindo (link "Entre por aqui" em
    ``login.html``) para quem prefere/precisa dela — ex.: no layout
    empilhado de telas estreitas (celular), onde o painel ao lado do
    login vira uma segunda seção mais para baixo na página. A lista de
    empresas em si vem de ``_listar_empresas_publicas``, compartilhada
    com ``login_tela``.
    """
    if auth.usuario_logado():
        return redirect(url_for("index"))

    return render_template(
        "empresas_publico.html",
        config=config_manager.obter_todas(),
        empresas=_listar_empresas_publicas(),
    )


@app.route("/empresas/<int:empresa_id>/entrar", methods=["GET", "POST"])
def empresa_login_tela(empresa_id: int):
    """
    Formulário de acesso de UMA empresa: nome do recrutador + chave de 8
    dígitos da empresa (ver auth.autenticar_por_chave_empresa). Em caso de
    sucesso, uma conta de recrutador efêmera é provisionada
    automaticamente (ver database.provisionar_usuario_recrutador) e a
    sessão é iniciada normalmente — o recrutador assume uma mesa da
    empresa exatamente como já acontecia com o login tradicional.

    Bloqueado (mesma mensagem de "não encontrada") para as duas opções
    fixas do sistema (``empresa.fixa`` — ver
    ``database.NOMES_EMPRESAS_FIXAS``): mesmo que não apareçam na lista
    de cards (ver ``empresas_entrar_tela``), alguém poderia tentar acessar
    esta URL diretamente pelo id — elas não têm recrutador, então o login
    por chave nunca deve funcionar para elas.
    """
    if auth.usuario_logado():
        return redirect(url_for("index"))

    empresa = database.obter_empresa_por_id(empresa_id)
    if empresa is None or not empresa.ativa or empresa.fixa:
        flash("Empresa não encontrada ou desativada.", "erro")
        return redirect(url_for("empresas_entrar_tela"))

    erro = None
    nome_informado = ""
    if request.method == "POST":
        nome_informado = request.form.get("nome_completo", "")
        chave_informada = request.form.get("chave", "")

        usuario, erro = auth.autenticar_por_chave_empresa(empresa_id, chave_informada, nome_informado)
        if usuario is not None:
            auth.iniciar_sessao(usuario)
            return redirect(url_for("index"))

    # Assim como em login.html, o nome digitado é reenviado ao template em
    # caso de erro (não precisa ser redigitado) — a chave, por segurança,
    # nunca é reenviada ao HTML.
    return render_template(
        "empresa_login.html",
        erro=erro,
        nome_informado=nome_informado,
        empresa=empresa.to_dict_publico(),
        config=config_manager.obter_todas(),
    )


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

    O corpo da requisição pode incluir ``{"impressora": "Nome"}`` para
    imprimir este ticket em uma impressora específica desta máquina,
    escolhida pelo usuário na janela de impressão exibida ao clicar em
    "Emitir Senha" (ver index.js) — a lista de impressoras vem sempre ao
    vivo de ``/api/impressoras`` (win32print.EnumPrinters), nunca de um
    nome digitado à mão, evitando o erro "StartDoc failed" causado por um
    nome configurado que não bate exatamente com o nome real da
    impressora no Windows. Se omitido ou vazio, usa a impressora padrão
    configurada em Configurações (ou a impressora padrão do Windows, se
    nenhuma estiver configurada).

    O corpo da requisição DEVE incluir ``{"empresa_id": <int>}``: a
    seleção da empresa do feirão é obrigatória para emitir uma senha (ver
    tela "Empresas", /admin/empresas). A empresa é validada no servidor
    (existe e está ativa) — nunca confiamos apenas na validação do
    formulário no navegador.

    O corpo da requisição pode incluir opcionalmente ``{"nome_pessoa":
    "Texto"}`` — o "Primeiro Nome" digitado livremente pelo Emissor (ver
    index.html), gravado na própria senha e impresso no ticket como
    "Nome: {texto}" quando preenchido (ver printer.py:imprimir_senha).
    Diferente de ``empresa_id``, NUNCA é obrigatório.

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
        # "Primeiro Nome" é OPCIONAL — diferente de empresa_id, ausência ou
        # texto vazio não é erro, só significa que a linha "Nome:" não sai
        # no ticket (ver printer.py:imprimir_senha). Truncado em 60
        # caracteres (mesmo limite do campo no formulário, ver
        # index.html) só como proteção extra contra um valor absurdamente
        # longo enviado direto à API, ignorando o maxlength do navegador.
        nome_pessoa = str(dados.get("nome_pessoa") or "").strip()[:60] or None

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
        if empresa.emissao_bloqueada_em:
            return resposta_erro(
                "A emissão de senhas para esta empresa está bloqueada no momento. "
                "Não é possível emitir novas senhas (o próprio recrutador da empresa "
                "ou um administrador pode reativar a emissão).",
                409,
            )

        usuario_sessao = auth.usuario_logado()
        guiche = f"Guichê {usuario_sessao['guiche']:02d}" if usuario_sessao.get("guiche") else None
        usuario = usuario_sessao.get("nome_completo")

        senha = database.criar_senha(
            empresa_id=empresa.id,
            empresa=empresa.nome,
            guiche=guiche,
            usuario=usuario,
            nome_pessoa=nome_pessoa,
            # As duas opções fixas do sistema ("Criar Currículos"/
            # "Imprimir Currículos" — ver database.NOMES_EMPRESAS_FIXAS)
            # não têm fila nem chamada: a senha já nasce "Finalizada" (ver
            # database.criar_senha para o motivo completo).
            finalizar_imediatamente=empresa.fixa,
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
                nome_pessoa=senha.nome_pessoa,
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
    restrita à empresa vinculada ao usuário (``usuario_sessao['empresa_id']``)
    — um recrutador nunca chama uma senha de outra empresa. Para o perfil
    "atendente", o comportamento é o de sempre: a fila GERAL (sem filtro
    de empresa).

    O "Bloqueio de Emissão de Senhas" de uma empresa (ver
    ``/api/bloquear-emissao``) NÃO afeta esta rota: bloquear a emissão
    impede apenas que NOVAS senhas sejam criadas (ver ``api_emitir``) —
    chamar/atender a fila já existente continua funcionando normalmente,
    mesmo com a emissão bloqueada.
    """
    try:
        usuario_sessao = auth.usuario_logado()
        eh_recrutador = usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR

        if not usuario_sessao.get("guiche"):
            mensagem = (
                "Você não possui uma mesa atribuída no momento (todas ocupadas "
                "nesta empresa). Faça logout e login novamente ou contate um "
                "administrador."
                if eh_recrutador
                else "Você não possui um guichê atribuído no momento (todos "
                "ocupados). Faça logout e login novamente ou contate um "
                "administrador."
            )
            return resposta_erro(mensagem, 409)

        empresa_id_filtro = usuario_sessao.get("empresa_id") if eh_recrutador else None

        guiche = _guiche_formatado(usuario_sessao)
        usuario = usuario_sessao.get("nome_completo")

        resultado = database.chamar_proxima(guiche=guiche, usuario=usuario, empresa_id=empresa_id_filtro)
        if resultado is None:
            return resposta_erro("Não há senhas aguardando chamada.", 404)

        return resposta_sucesso({"chamada": resultado})

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao chamar próxima senha: {erro}", 500)


@app.route("/api/chamar-varias", methods=["POST"])
@auth.login_required
def api_chamar_varias():
    """
    Chama, de uma vez, um CONJUNTO específico de senhas selecionadas
    manualmente pelo recrutador na Fila de Espera (checkbox por linha —
    ver templates/index.html/static/js/index.js), diferente de
    ``/api/chamar`` (sempre a próxima em ordem FIFO). Usada pelo botão
    "Chamar Selecionadas".

    Corpo esperado (JSON): ``{"senha_ids": [1, 2, 3]}``.

    Sempre em nome do guichê e do usuário atualmente logados (obtidos da
    sessão, nunca do corpo da requisição — mesmo princípio de
    ``/api/chamar``). Quando o perfil logado é "recrutador", cada id é
    validado contra a empresa vinculada ao usuário (dupla checagem: uma
    vez aqui via ``_pode_gerenciar_senha``, por id, e de novo dentro de
    ``database.chamar_varias`` via ``empresa_id`` — defesa em
    profundidade contra um recrutador tentar chamar a senha de outra
    empresa manipulando os ids enviados).

    Todas as senhas chamadas nesta operação compartilham um mesmo
    "lote" (ver ``database.chamar_varias``/``obter_chamada_atual``), que
    é o que o Painel Público usa para exibir a sequência chamada — sem
    misturar com o lote de OUTRA empresa chamando ao mesmo tempo, já
    que a busca do lote mais recente no painel sempre é escopada por
    empresa.
    """
    try:
        usuario_sessao = auth.usuario_logado()
        eh_recrutador = usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR

        if not usuario_sessao.get("guiche"):
            mensagem = (
                "Você não possui uma mesa atribuída no momento (todas ocupadas "
                "nesta empresa). Faça logout e login novamente ou contate um "
                "administrador."
                if eh_recrutador
                else "Você não possui um guichê atribuído no momento (todos "
                "ocupados). Faça logout e login novamente ou contate um "
                "administrador."
            )
            return resposta_erro(mensagem, 409)

        dados = request.get_json(silent=True) or {}
        senha_ids_brutos = dados.get("senha_ids")
        if not isinstance(senha_ids_brutos, list) or not senha_ids_brutos:
            return resposta_erro("Selecione ao menos uma senha para chamar.", 400)

        try:
            senha_ids = [int(item) for item in senha_ids_brutos]
        except (TypeError, ValueError):
            return resposta_erro("Lista de senhas inválida.", 400)

        # Checagem de permissão POR ID, antes de chegar em database.chamar_varias
        # (que também valida por empresa_id) — mesmo padrão já usado por
        # api_cancelar/api_reimprimir, garantindo uma mensagem de erro
        # consistente (403, não 400/500) quando um recrutador tenta
        # chamar a senha de outra empresa.
        for senha_id in senha_ids:
            if not _pode_gerenciar_senha(usuario_sessao, senha_id):
                return resposta_erro("Você não tem permissão para chamar uma das senhas selecionadas.", 403)

        empresa_id_filtro = usuario_sessao.get("empresa_id") if eh_recrutador else None

        guiche = _guiche_formatado(usuario_sessao)
        usuario = usuario_sessao.get("nome_completo")

        resultado = database.chamar_varias(
            senha_ids=senha_ids, guiche=guiche, usuario=usuario, empresa_id=empresa_id_filtro
        )
        return resposta_sucesso({"chamada": resultado})

    # database.chamar_varias levanta ValueError para qualquer senha
    # inválida (não encontrada, já chamada/finalizada/cancelada, ou de
    # outra empresa) — 409 (conflito de estado), igual ao já usado por
    # /api/repetir para o mesmo tipo de situação.
    except ValueError as erro:
        return resposta_erro(str(erro), 409)

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao chamar senhas selecionadas: {erro}", 500)


@app.route("/api/repetir", methods=["POST"])
@auth.login_required
def api_repetir():
    """
    Repete a última chamada realizada NO PRÓPRIO guichê/mesa do usuário
    logado (nova animação/bip no painel).

    Importante: o escopo é o guichê/mesa exato da sessão (ver
    ``_guiche_formatado``), não apenas a empresa — várias pessoas podem
    atender na MESMA empresa em mesas diferentes (ver seção 4.6 do
    README), então repetir na Mesa 02 nunca deve reanunciar por engano a
    última chamada da Mesa 01 (de outro recrutador da mesma empresa). Ver
    ``database.repetir_ultima_chamada``.
    """
    try:
        usuario_sessao = auth.usuario_logado()
        eh_recrutador = usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR

        guiche_formatado = _guiche_formatado(usuario_sessao)
        if not guiche_formatado:
            mensagem = (
                "Você não possui uma mesa atribuída no momento."
                if eh_recrutador
                else "Você não possui um guichê atribuído no momento."
            )
            return resposta_erro(mensagem, 409)

        resultado = database.repetir_ultima_chamada(guiche=guiche_formatado)
        if resultado is None:
            mensagem = (
                "Você ainda não chamou nenhuma senha nesta mesa."
                if eh_recrutador
                else "Você ainda não chamou nenhuma senha neste guichê."
            )
            return resposta_erro(mensagem, 404)

        return resposta_sucesso({"chamada": resultado})

    # A senha já finalizada/cancelada não pode ser rechamada (ver regra em
    # database.repetir_ultima_chamada) — vale para qualquer perfil
    # (atendente ou recrutador), por isso a checagem fica lá, compartilhada
    # por todos que passam por esta mesma rota.
    except ValueError as erro:
        return resposta_erro(str(erro), 409)

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
                "Você não possui uma mesa atribuída no momento. Apenas usuários "
                "com perfil recrutador e mesa ativa podem finalizar atendimentos."
                if eh_recrutador
                else "Você não possui um guichê atribuído no momento. Apenas "
                "usuários com perfil atendente e guichê ativo podem finalizar "
                "atendimentos."
            )
            return resposta_erro(mensagem, 409)

        guiche = _guiche_formatado(usuario_sessao)
        usuario = usuario_sessao.get("nome_completo")
        empresa_id_filtro = usuario_sessao.get("empresa_id") if eh_recrutador else None

        resultado = database.finalizar_atendimento_e_chamar_proxima(
            guiche=guiche, usuario=usuario, empresa_id=empresa_id_filtro
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


@app.route("/api/bloquear-emissao", methods=["POST"])
@auth.login_required
def api_bloquear_emissao():
    """
    Bloqueia a emissão de novas senhas da EMPRESA do recrutador logado
    (ver ``database.bloquear_emissao_empresa``): a partir de agora, esta
    empresa não aceita mais emissão de novas senhas (``/api/emitir``).

    IMPORTANTE: chamar/atender a fila já existente CONTINUA funcionando
    normalmente (``/api/chamar``, ``/api/repetir``,
    ``/api/senha/<id>/finalizar``) — bloquear a emissão não é o mesmo que
    encerrar o expediente; é só uma forma de dizer "pare de mandar mais
    gente pra nossa fila", mantendo o atendimento de quem já está nela.
    Nenhuma senha em espera é cancelada por este bloqueio.

    A empresa é sempre a do PRÓPRIO recrutador logado (``usuario_sessao
    ['empresa_id']``), nunca um id vindo do corpo da requisição — um
    recrutador só pode bloquear a própria empresa, nunca a de outra.

    Restrito ao perfil "recrutador" (o botão que dispara esta ação só
    existe na tela dele — ver index.html). Tanto o próprio recrutador
    (ver ``/api/reativar-emissao``) quanto um administrador (ver
    ``/api/admin/empresas/<id>/reativar-emissao``) podem reverter.
    """
    try:
        usuario_sessao = auth.usuario_logado()
        if usuario_sessao.get("perfil") != PerfilUsuario.RECRUTADOR:
            return resposta_erro(
                "Apenas o perfil recrutador pode bloquear a emissão de senhas de uma empresa.", 403
            )

        empresa_id = usuario_sessao.get("empresa_id")
        if not empresa_id:
            return resposta_erro(
                "Você não está vinculado a nenhuma empresa no momento. Procure um administrador.", 409
            )

        resultado = database.bloquear_emissao_empresa(empresa_id)
        if resultado is None:
            return resposta_erro("Empresa não encontrada.", 404)

        if resultado["ja_bloqueado"]:
            return resposta_sucesso(
                {
                    "mensagem": "A emissão de senhas desta empresa já estava bloqueada.",
                    "ja_bloqueado": True,
                    "bloqueado_em": resultado["bloqueado_em"],
                }
            )

        return resposta_sucesso(
            {
                "mensagem": "Emissão de senhas bloqueada. Novas senhas não poderão ser emitidas para "
                "esta empresa até a emissão ser reativada.",
                "ja_bloqueado": False,
                "bloqueado_em": resultado["bloqueado_em"],
            }
        )

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao bloquear emissão de senhas: {erro}", 500)


@app.route("/api/reativar-emissao", methods=["POST"])
@auth.login_required
def api_reativar_emissao():
    """
    Reativa a emissão de senhas da EMPRESA do recrutador logado (ver
    ``database.desbloquear_emissao_empresa``) — autoatendimento: o
    próprio recrutador que bloqueou (ou qualquer recrutador da mesma
    empresa) pode desfazer o bloqueio a qualquer momento, sem precisar
    de um administrador. Um administrador também pode fazer isso por
    qualquer empresa (ver
    ``/api/admin/empresas/<id>/reativar-emissao``).

    A empresa é sempre a do PRÓPRIO recrutador logado, nunca um id vindo
    do corpo da requisição — mesmo padrão de ``/api/bloquear-emissao``.
    """
    try:
        usuario_sessao = auth.usuario_logado()
        if usuario_sessao.get("perfil") != PerfilUsuario.RECRUTADOR:
            return resposta_erro(
                "Apenas o perfil recrutador pode reativar a emissão de senhas de uma empresa.", 403
            )

        empresa_id = usuario_sessao.get("empresa_id")
        if not empresa_id:
            return resposta_erro(
                "Você não está vinculado a nenhuma empresa no momento. Procure um administrador.", 409
            )

        if database.desbloquear_emissao_empresa(empresa_id):
            return resposta_sucesso({"mensagem": "Emissão de senhas reativada."})
        return resposta_erro("Empresa não encontrada, ou a emissão dela já estava liberada.", 404)

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao reativar emissão de senhas: {erro}", 500)


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


FILA_ITENS_POR_PAGINA = 20


@app.route("/api/fila")
@auth.login_required
def api_fila():
    """
    Retorna a fila atual de senhas aguardando chamada.

    Para o perfil "recrutador", a fila é automaticamente restrita à
    empresa vinculada ao usuário — ele só vê (e só pode cancelar) as
    senhas da sua própria empresa. Para os demais perfis, mantém o
    comportamento de sempre: a fila GERAL, sem filtro de empresa.

    Aceita dois parâmetros de querystring opcionais, usados pela Fila de
    Espera na tela principal (ver index.html/index.js):

        - ``busca``: filtra por número da senha ou nome da pessoa (ver
          ``database.listar_fila_atual``).
        - ``pagina``: página de resultados (1-indexada; ``por_pagina`` é
          fixo em ``FILA_ITENS_POR_PAGINA``). Existe porque, antes desta
          paginação, apenas as 20 senhas mais antigas da fila eram
          acessíveis — qualquer senha além dessas nunca aparecia,
          mesmo que o usuário soubesse exatamente o que procurar.

    ``total_aguardando`` no retorno continua sendo o total da fila
    INTEIRA (sem filtro de busca) — usado pelo contador no cabeçalho do
    card "Fila de Espera". ``total_filtrado``/``total_paginas`` refletem
    a busca atual, para desenhar a paginação.

    ``total_emitidas_hoje`` (ver ``database.contar_emitidas_hoje``) é um
    total à parte: TODAS as senhas emitidas hoje, em qualquer status —
    inclui, por exemplo, as senhas das duas opções fixas ("Criar
    Currículos"/"Imprimir Currículos"), que nascem já 'Finalizada' e por
    isso nunca entram em ``total_aguardando`` nem nos painéis públicos.
    Serve de confirmação visível para o perfil Emissor (que não tem
    acesso a Relatórios) de que a emissão foi mesmo contabilizada.

    ``ultimas_por_empresa`` (ver ``database.listar_ultima_senha_por_empresa``)
    traz a última senha emitida de CADA empresa ativa — exibido no card
    "Última Senha por Empresa" da tela do Emissor, acima da Fila de
    Espera. Como a tela já consulta este endpoint em polling (e também
    logo após emitir/reimprimir uma senha — ver index.js), o card se
    atualiza sozinho tanto quando uma nova senha é emitida quanto
    quando uma nova empresa é cadastrada (sem precisar de outro
    endpoint/gatilho separado).
    """
    try:
        usuario_sessao = auth.usuario_logado()
        empresa_id_filtro = (
            usuario_sessao.get("empresa_id")
            if usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR
            else None
        )

        busca = (request.args.get("busca") or "").strip()[:60]
        try:
            pagina = int(request.args.get("pagina", 1))
        except (TypeError, ValueError):
            pagina = 1
        pagina = max(pagina, 1)

        total_aguardando = database.contar_aguardando(empresa_id=empresa_id_filtro)
        total_filtrado = (
            database.contar_aguardando(empresa_id=empresa_id_filtro, busca=busca)
            if busca
            else total_aguardando
        )
        total_paginas = max(-(-total_filtrado // FILA_ITENS_POR_PAGINA), 1)
        pagina = min(pagina, total_paginas)

        fila = database.listar_fila_atual(
            empresa_id=empresa_id_filtro,
            busca=busca or None,
            pagina=pagina,
            por_pagina=FILA_ITENS_POR_PAGINA,
        )

        total_emitidas_hoje = database.contar_emitidas_hoje(empresa_id=empresa_id_filtro)
        ultimas_por_empresa = database.listar_ultima_senha_por_empresa()

        return resposta_sucesso(
            {
                "fila": fila,
                "total_aguardando": total_aguardando,
                "total_filtrado": total_filtrado,
                "pagina_atual": pagina,
                "total_paginas": total_paginas,
                "por_pagina": FILA_ITENS_POR_PAGINA,
                "total_emitidas_hoje": total_emitidas_hoje,
                "ultimas_por_empresa": ultimas_por_empresa,
            }
        )
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


@app.route("/api/senha/<int:senha_id>/reimprimir", methods=["POST"])
@auth.login_required
def api_reimprimir(senha_id: int):
    """
    Reimprime o ticket de uma senha já emitida (segunda via), usada pelo
    botão "Reimprimir" na Fila de Espera (visível para o perfil Emissor —
    ver index.js). Não altera nenhum dado da senha no banco: é apenas uma
    nova impressão física do mesmo ticket, marcada com "REIMPRESSO" (ver
    printer.py:imprimir_senha) para deixar claro a quem for atender que
    não se trata de uma senha nova. O "Primeiro Nome" eventualmente
    digitado na emissão original (``senha.nome_pessoa``) também sai na
    reimpressão, sem precisar ser digitado de novo.

    SÓ é permitido reimprimir enquanto a senha ainda está com status
    'Emitida' (aguardando na fila) — rejeitado se ela já foi chamada,
    finalizada ou cancelada, tanto porque essas senhas já saíram do
    fluxo normal de atendimento quanto para evitar confusão de reimprimir
    uma senha que já foi (ou nunca será) atendida. Essa validação é
    sempre feita aqui no servidor, mesmo que a Fila de Espera do
    navegador já só liste senhas 'Emitida' — a lista pode estar
    desatualizada no instante do clique (ex.: a senha acabou de ser
    chamada por outro atendente).

    Segue o mesmo controle de acesso de finalizar/cancelar: um
    recrutador só pode reimprimir senhas da sua própria empresa (ver
    ``_pode_gerenciar_senha``).
    """
    try:
        if not _pode_gerenciar_senha(auth.usuario_logado(), senha_id):
            return resposta_erro("Você não tem permissão para gerenciar esta senha.", 403)

        senha = database.obter_senha_por_id(senha_id)
        if senha is None:
            return resposta_erro("Senha não encontrada.", 404)

        if senha.status != StatusSenha.EMITIDA:
            return resposta_erro(
                f"Esta senha não pode ser reimpressa: status atual é "
                f"'{senha.status}' (só é permitido reimprimir senhas com "
                f"status 'Emitida', ainda aguardando na fila).",
                409,
            )

        dados = request.get_json(silent=True) or {}
        impressora_escolhida = str(dados.get("impressora") or "").strip()

        configuracoes = config_manager.obter_todas()
        nome_impressora = impressora_escolhida or configuracoes.get("nome_impressora") or None
        impressora = ImpressoraTermica(nome_impressora)

        empresa = database.obter_empresa_por_id(senha.empresa_id) if senha.empresa_id else None
        caminho_logo_empresa = f"static/{empresa.logo_path}" if empresa and empresa.logo_path else None
        nome_empresa = empresa.nome if empresa else senha.empresa

        impressora.imprimir_senha(
            numero=senha.numero,
            nome_evento=configuracoes.get("nome_evento", ""),
            caminho_logo=caminho_logo_empresa,
            nome_empresa=nome_empresa,
            reimpressao=True,
            nome_pessoa=senha.nome_pessoa,
        )

        return resposta_sucesso({"mensagem": f"Senha {senha.numero:03d} reimpressa."})

    except ErroImpressora as erro:
        return resposta_erro(f"Falha ao reimprimir: {erro}", 500)
    except Exception as erro:  # pragma: no cover - proteção contra falhas inesperadas
        return resposta_erro(f"Erro ao reimprimir senha: {erro}", 500)


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
        qtd_exibidas = config_manager.obter("qtd_senhas_exibidas", 10)

        agora = datetime.now()

        return resposta_sucesso(
            {
                "chamada_atual": database.obter_chamada_atual(),
                "ultimas_emitidas": database.listar_ultimas_emitidas(qtd_exibidas),
                "data": agora.strftime("%d/%m/%Y"),
                "hora": agora.strftime("%H:%M:%S"),
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
    com o parâmetro ``empresa_id``).
    """
    empresa = database.obter_empresa_por_id(empresa_id)
    if empresa is None:
        return resposta_erro("Empresa não encontrada.", 404)

    try:
        qtd_exibidas = config_manager.obter("qtd_senhas_exibidas", 10)

        agora = datetime.now()

        return resposta_sucesso(
            {
                # to_dict_publico() (NUNCA to_dict() aqui): esta rota é
                # pública, sem login, consultada pelo painel de exibição —
                # não pode vazar "chave_acesso" (a credencial de login do
                # recrutador desta empresa).
                "empresa": empresa.to_dict_publico(),
                "chamada_atual": database.obter_chamada_atual(empresa_id=empresa.id),
                "ultimas_emitidas": database.listar_ultimas_emitidas(qtd_exibidas, empresa_id=empresa.id),
                "total_aguardando": database.contar_aguardando(empresa_id=empresa.id),
                "data": agora.strftime("%d/%m/%Y"),
                "hora": agora.strftime("%H:%M:%S"),
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
    ``database.resumo_geral_senhas`` — além do bloco ``resumo_feirao``:
    totais gerais de TODO o evento (sem filtro de período, já que este
    painel não tem seletor de data), exibidos na seção "Resumo do
    Feirão" da tela (ver templates/painel_geral.html):

        - ``total_emitidas``: todas as senhas emitidas, EXCETO as
          Canceladas (``resumo.total_emitidas - resumo.total_canceladas``
          — uma senha cancelada não reflete um atendimento nem uma
          emissão "válida" para fins deste indicador, então não deve
          inflar o total exibido no painel). Continua incluindo as duas
          opções fixas ("Criar Currículos"/"Imprimir Currículos", que
          nascem direto 'Finalizada').
        - ``total_atendidas``: quantas senhas foram efetivamente
          atendidas (``database.contar_chamadas_realizadas_periodo``,
          sem período = todo o histórico) — já inclui as duas opções
          fixas ("Criar Currículos"/"Imprimir Currículos", ver seção
          12.11 do README), já que "emitir" uma delas É o próprio
          atendimento (não existe fila/chamada para elas).
        - ``tempo_medio``: tempo médio entre emissão e primeira chamada
          (``database.tempo_medio_atendimento``, sem período).
    """
    try:
        agora = datetime.now()
        resumo = database.resumo_geral_senhas()

        return resposta_sucesso(
            {
                "resumo": resumo,
                "resumo_feirao": {
                    "total_emitidas": resumo["total_emitidas"] - resumo["total_canceladas"],
                    "total_atendidas": database.contar_chamadas_realizadas_periodo(),
                    "tempo_medio": database.tempo_medio_atendimento(),
                },
                "data": agora.strftime("%d/%m/%Y"),
                "hora": agora.strftime("%H:%M:%S"),
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


# Faixas aceitas para cada campo numérico de configuração, espelhando os
# atributos min/max/step já usados nos <input type="number"> de
# templates/configuracoes.html. Validar aqui também (e não só no HTML) é
# necessário porque o campo do formulário pode ser contornado por quem
# chamar a API diretamente (ex.: valor negativo, texto, ou um número
# absurdamente grande que travaria o painel/atualização em polling).
_FAIXAS_CONFIG_NUMERICAS = {
    "qtd_senhas_exibidas": (1, 50),
    "tempo_atualizacao_ms": (500, 300_000),  # 300.000 ms = 5 minutos
    "qtd_guiches": (1, 50),
    "qtd_guiches_por_empresa": (1, 50),
}


def _validar_configuracoes_numericas(dados: dict) -> Optional[str]:
    """
    Valida os campos numéricos de configuração presentes em ``dados``.

    Retorna uma mensagem de erro (em português, pronta para exibir ao
    usuário) descrevendo o PRIMEIRO campo inválido encontrado, ou
    ``None`` se todos os campos numéricos enviados estiverem OK. Campos
    não numéricos (ex.: ``nome_evento``, ``cor_principal``) e campos
    numéricos que simplesmente não foram enviados neste POST não são
    validados aqui.
    """
    for chave, (minimo, maximo) in _FAIXAS_CONFIG_NUMERICAS.items():
        if chave not in dados:
            continue

        valor_bruto = dados[chave]
        try:
            valor = int(valor_bruto)
        except (TypeError, ValueError):
            return f"O campo '{chave}' deve ser um número inteiro (recebido: {valor_bruto!r})."

        if valor < minimo or valor > maximo:
            return f"O campo '{chave}' deve estar entre {minimo} e {maximo} (recebido: {valor})."

    return None


@app.route("/api/config", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_config_salvar():
    """Atualiza uma ou mais configurações do sistema. Restrito a administradores."""
    try:
        dados = request.get_json(silent=True) or {}
        if not dados:
            return resposta_erro("Nenhum dado de configuração foi enviado.", 400)

        erro_validacao = _validar_configuracoes_numericas(dados)
        if erro_validacao:
            return resposta_erro(erro_validacao, 400)

        config_manager.salvar(dados)
        return resposta_sucesso({"config": config_manager.obter_todas()})

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao salvar configurações: {erro}", 500)


@app.route("/api/impressoras")
@auth.login_required
def api_impressoras():
    """
    Lista as impressoras instaladas no Windows desta estação.

    Diferente das demais rotas de configuração, esta é acessível a
    QUALQUER usuário logado (não apenas administradores): o emissor de
    senhas precisa desta lista para escolher a impressora local na janela
    exibida ao clicar em "Emitir Senha" (ver index.js), pré-selecionando
    a impressora configurada em Configurações quando ela existir na
    lista. Escolher sempre a partir desta lista (em vez de confiar num
    nome digitado à mão em Configurações) evita o erro "StartDoc failed"
    causado por um nome que não bate exatamente com o nome real da
    impressora no Windows. Apenas ALTERAR a impressora padrão do sistema
    (tela Configurações) continua restrito a administradores.
    """
    return resposta_sucesso({"impressoras": ImpressoraTermica.listar_impressoras_instaladas()})


@app.route("/api/empresas")
@auth.login_required
def api_empresas():
    """
    Lista as empresas ATIVAS e com a EMISSÃO de senhas liberada, usadas
    para popular o seletor de empresa exibido ao emitir uma senha (ver
    index.html/index.js) — uma empresa com a emissão bloqueada (ver
    ``/api/bloquear-emissao``) some deste seletor, no mesmo espírito de
    uma empresa desativada, para não permitir a emissão de uma senha que
    não deveria ser criada agora.

    Assim como ``/api/impressoras``, é acessível a QUALQUER usuário
    logado (não apenas administradores), pois é o perfil "emissor" — não
    o admin — quem efetivamente emite senhas.
    """
    # Remove "chave_acesso" de cada empresa antes de responder: esta rota é
    # acessível a QUALQUER perfil logado (não só admin — ver docstring
    # acima), então não pode vazar a credencial de login do recrutador de
    # cada empresa para, por exemplo, um usuário "emissor".
    empresas = [
        {chave: valor for chave, valor in empresa.items() if chave != "chave_acesso"}
        for empresa in database.listar_empresas(somente_ativas=True)
        if not empresa.get("emissao_bloqueada_em")
    ]
    return resposta_sucesso({"empresas": empresas})


# ---------------------------------------------------------------------------
# API - Relatórios
# ---------------------------------------------------------------------------

# Caracteres que o Excel/LibreOffice interpretam como início de fórmula
# quando são o PRIMEIRO caractere de uma célula.
_CARACTERES_FORMULA_PERIGOSOS = ("=", "+", "-", "@", "\t", "\r")


def _sanitizar_celula_planilha(valor):
    """
    Neutraliza um possível "CSV/Excel formula injection": se ``valor``
    (texto vindo do banco, como nome de empresa ou de usuário — ambos
    cadastrados livremente por um administrador) começar com um
    caractere que o Excel/LibreOffice interpretam como início de fórmula,
    prefixa com um apóstrofo. O apóstrofo faz o programa de planilha
    tratar a célula como TEXTO literal (não aparece visualmente depois de
    aberta), sem executar nada.

    Sem isso, um nome cadastrado sem má intenção (ex.: uma empresa
    chamada "-1 a 5 funcionários") já produziria uma célula quebrada ao
    abrir no Excel; em caso malicioso, uma fórmula (ex.: iniciando com
    "=") poderia rodar comandos ou vazar dados ao abrir a planilha
    exportada — daí sanitizar sempre, não só em cenários intencionais.
    """
    if isinstance(valor, str) and valor[:1] in _CARACTERES_FORMULA_PERIGOSOS:
        return "'" + valor
    return valor


def _parametros_periodo():
    """
    Extrai os parâmetros de período (inicio/fim) da querystring e resolve
    o filtro de empresa a partir do PERFIL logado, NUNCA confiando no que
    o cliente envia livremente:

        - admin: pode filtrar por qualquer empresa via querystring
          ``empresa_id`` (ou nenhuma, para ver o relatório de TODAS).
        - recrutador: sempre forçado à PRÓPRIA empresa (``empresa_id`` da
          sessão) — qualquer valor de ``empresa_id`` enviado na
          querystring é ignorado, para que um recrutador jamais consiga
          ver dados de outra empresa só editando a URL.
    """
    inicio = request.args.get("inicio") or None
    fim = request.args.get("fim") or None

    usuario_sessao = auth.usuario_logado() or {}
    if usuario_sessao.get("perfil") == PerfilUsuario.RECRUTADOR:
        empresa_id = usuario_sessao.get("empresa_id")
    else:
        empresa_id_bruto = request.args.get("empresa_id")
        try:
            empresa_id = int(empresa_id_bruto) if empresa_id_bruto else None
        except (TypeError, ValueError):
            empresa_id = None

    return inicio, fim, empresa_id


_FORMATO_DATA_HORA = "%Y-%m-%d %H:%M:%S"


def _formatar_duracao(segundos: float) -> str:
    """Formata uma duração em segundos como texto "HH:MM:SS"."""
    horas, resto = divmod(int(segundos), 3600)
    minutos, segundos_restantes = divmod(resto, 60)
    return f"{horas:02d}:{minutos:02d}:{segundos_restantes:02d}"


def _tempos_relatorio_senha(item: dict):
    """
    Calcula as três colunas de tempo do relatório "Senhas Emitidas" —
    hora da chamada, tempo de atendimento (chamada → finalizada) e hora
    finalizada — a partir do dicionário de uma senha (ver
    ``database.listar_senhas_periodo``, que já inclui ``hora_chamada`` e
    ``hora_finalizada`` desde a migração
    ``_migrar_tabela_senhas_adicionar_marcos_tempo``).

    Para senhas CANCELADAS, retorna as três colunas VAZIAS
    propositalmente — mesmo que ``hora_chamada`` esteja preenchida no
    banco (ex.: uma senha que chegou a ser chamada e depois foi
    cancelada em vez de finalizada): uma senha cancelada é tratada como
    "sem atendimento" no relatório, por definição.

    Retorna a tupla ``(hora_chamada, tempo_atendimento, hora_finalizada)``,
    cada um como string (vazia quando não aplicável).
    """
    if item.get("status") == StatusSenha.CANCELADA:
        return "", "", ""

    hora_chamada = item.get("hora_chamada") or ""
    hora_finalizada = item.get("hora_finalizada") or ""

    tempo_atendimento = ""
    if hora_chamada and hora_finalizada:
        try:
            inicio_dt = datetime.strptime(hora_chamada, _FORMATO_DATA_HORA)
            fim_dt = datetime.strptime(hora_finalizada, _FORMATO_DATA_HORA)
            tempo_atendimento = _formatar_duracao((fim_dt - inicio_dt).total_seconds())
        except (TypeError, ValueError):
            tempo_atendimento = ""

    return hora_chamada, tempo_atendimento, hora_finalizada


@app.route("/api/relatorios/resumo")
@auth.login_required
@auth.admin_ou_recrutador_required
def api_relatorios_resumo():
    """
    Retorna um resumo estatístico (JSON) para exibição na tela de
    relatórios: total emitidas, total chamadas, tempo médio de espera e a
    contagem de senhas emitidas por empresa (``por_empresa``).

    Acessível a administradores (todas as empresas, ou uma específica via
    querystring ``empresa_id``) e a recrutadores (sempre restrito à
    própria empresa — ver ``_parametros_periodo``).

    ``total_chamadas`` usa ``database.contar_chamadas_realizadas_periodo``
    (contagem de SENHAS distintas chamadas, não de eventos de chamada) —
    invariante de negócio: "chamadas realizadas" nunca pode ser maior que
    "senhas emitidas". Ver a docstring daquela função para o motivo de
    não usarmos apenas ``len(listar_chamadas_periodo(...))`` aqui
    (repetições de chamada inflavam essa contagem).
    """
    try:
        inicio, fim, empresa_id = _parametros_periodo()
        emitidas = database.listar_senhas_periodo(inicio, fim, empresa_id=empresa_id)
        total_chamadas = database.contar_chamadas_realizadas_periodo(inicio, fim, empresa_id=empresa_id)
        tempo_medio = database.tempo_medio_atendimento(inicio, fim, empresa_id=empresa_id)
        por_empresa = database.listar_contagem_por_empresa(inicio, fim, empresa_id=empresa_id)

        return resposta_sucesso(
            {
                "total_emitidas": len(emitidas),
                "total_chamadas": total_chamadas,
                "tempo_medio": tempo_medio,
                "por_empresa": por_empresa,
            }
        )
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao gerar resumo: {erro}", 500)


@app.route("/api/relatorios/csv")
@auth.login_required
@auth.admin_ou_recrutador_required
def api_relatorios_csv():
    """Gera e retorna um relatório em formato CSV para download. Acessível
    a administradores e recrutadores (ver ``_parametros_periodo`` para o
    recorte por empresa)."""
    try:
        tipo = request.args.get("tipo", "emitidas")
        inicio, fim, empresa_id = _parametros_periodo()

        buffer_texto = io.StringIO()
        escritor = csv.writer(buffer_texto, delimiter=";")

        if tipo == "chamadas":
            escritor.writerow(["ID Evento", "ID Senha", "Número", "Empresa", "Guichê", "Usuário", "Data/Hora"])
            for item in database.listar_chamadas_periodo(inicio, fim, empresa_id=empresa_id):
                escritor.writerow(
                    [
                        item["id"], item["senha_id"], item["numero"],
                        _sanitizar_celula_planilha(item.get("empresa") or "-"),
                        item["guiche"], _sanitizar_celula_planilha(item["usuario"]), item["data_hora"],
                    ]
                )
            nome_arquivo = "relatorio_chamadas.csv"
        else:
            escritor.writerow(
                [
                    "ID", "Número", "Status", "Empresa", "Hora Emissão", "Hora Chamada",
                    "Tempo de Atendimento", "Hora Finalizada", "Guichê", "Usuário",
                ]
            )
            for item in database.listar_senhas_periodo(inicio, fim, empresa_id=empresa_id):
                hora_chamada, tempo_atendimento, hora_finalizada = _tempos_relatorio_senha(item)
                escritor.writerow(
                    [
                        item["id"], item["numero"], item["status"],
                        _sanitizar_celula_planilha(item.get("empresa") or "-"),
                        item["data_hora"], hora_chamada, tempo_atendimento, hora_finalizada,
                        item["guiche"], _sanitizar_celula_planilha(item["usuario"]),
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
@auth.admin_ou_recrutador_required
def api_relatorios_excel():
    """Gera e retorna um relatório em formato Excel (.xlsx) para download.
    Acessível a administradores e recrutadores (ver ``_parametros_periodo``
    para o recorte por empresa)."""
    try:
        # Importação local para não exigir openpyxl caso o relatório em
        # Excel nunca seja utilizado (reduz acoplamento e tempo de boot).
        from openpyxl import Workbook
        from openpyxl.styles import Font

        tipo = request.args.get("tipo", "emitidas")
        inicio, fim, empresa_id = _parametros_periodo()

        pasta = Workbook()
        planilha = pasta.active

        if tipo == "chamadas":
            planilha.title = "Chamadas"
            cabecalho = ["ID Evento", "ID Senha", "Número", "Empresa", "Guichê", "Usuário", "Data/Hora"]
            planilha.append(cabecalho)
            for item in database.listar_chamadas_periodo(inicio, fim, empresa_id=empresa_id):
                planilha.append(
                    [
                        item["id"], item["senha_id"], item["numero"],
                        _sanitizar_celula_planilha(item.get("empresa") or "-"),
                        item["guiche"], _sanitizar_celula_planilha(item["usuario"]), item["data_hora"],
                    ]
                )
            nome_arquivo = "relatorio_chamadas.xlsx"
        else:
            planilha.title = "Emitidas"
            cabecalho = [
                "ID", "Número", "Status", "Empresa", "Hora Emissão", "Hora Chamada",
                "Tempo de Atendimento", "Hora Finalizada", "Guichê", "Usuário",
            ]
            planilha.append(cabecalho)
            for item in database.listar_senhas_periodo(inicio, fim, empresa_id=empresa_id):
                hora_chamada, tempo_atendimento, hora_finalizada = _tempos_relatorio_senha(item)
                planilha.append(
                    [
                        item["id"], item["numero"], item["status"],
                        _sanitizar_celula_planilha(item.get("empresa") or "-"),
                        item["data_hora"], hora_chamada, tempo_atendimento, hora_finalizada,
                        item["guiche"], _sanitizar_celula_planilha(item["usuario"]),
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
@auth.admin_ou_recrutador_required
def api_relatorios_pdf():
    """Gera e retorna um relatório em formato PDF para download.

    Acessível a administradores e recrutadores (ver ``_parametros_periodo``
    para o recorte por empresa).

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
        inicio, fim, empresa_id = _parametros_periodo()

        buffer_bytes = io.BytesIO()
        documento = SimpleDocTemplate(buffer_bytes, pagesize=A4)
        estilos = getSampleStyleSheet()
        elementos = []

        titulo = "Relatório de Senhas Emitidas" if tipo != "chamadas" else "Relatório de Chamadas"
        elementos.append(Paragraph(titulo, estilos["Title"]))
        elementos.append(Spacer(1, 0.5 * cm))

        if tipo == "chamadas":
            dados_tabela = [["ID Evento", "ID Senha", "Número", "Empresa", "Guichê", "Usuário", "Data/Hora"]]
            for item in database.listar_chamadas_periodo(inicio, fim, empresa_id=empresa_id):
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
            dados_tabela = [
                [
                    "ID", "Número", "Status", "Empresa", "Hora Emissão", "Hora Chamada",
                    "Tempo Atend.", "Hora Finalizada", "Guichê", "Usuário",
                ]
            ]
            for item in database.listar_senhas_periodo(inicio, fim, empresa_id=empresa_id):
                hora_chamada, tempo_atendimento, hora_finalizada = _tempos_relatorio_senha(item)
                dados_tabela.append(
                    [
                        str(item["id"]),
                        f"{item['numero']:03d}",
                        item["status"],
                        item.get("empresa") or "-",
                        item["data_hora"],
                        hora_chamada,
                        tempo_atendimento,
                        hora_finalizada,
                        item["guiche"] or "-",
                        item["usuario"] or "-",
                    ]
                )
            nome_arquivo = "relatorio_emitidas.pdf"

        # Inclui o resumo de tempo médio de atendimento ao final do relatório.
        tempo_medio = database.tempo_medio_atendimento(inicio, fim, empresa_id=empresa_id)

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

        # Recrutador não é mais cadastrado manualmente aqui: a conta é
        # criada automaticamente quando alguém entra pela chave de acesso
        # da própria empresa (ver app.py:api_empresa_entrar e
        # database.provisionar_usuario_recrutador). Bloqueado também no
        # servidor (não só escondido no formulário) para não depender só
        # do front-end.
        if perfil == PerfilUsuario.RECRUTADOR:
            return resposta_erro(
                "Recrutadores não são mais cadastrados manualmente. A conta é criada "
                "automaticamente quando alguém entra pela chave de acesso da empresa "
                "(ver tela Empresas).",
                400,
            )

        # "empresa_id_bruto" não é mais usado: nenhum perfil cadastrável
        # nesta rota precisa de empresa vinculada (recrutador, o único que
        # precisava, foi bloqueado acima).
        empresa_id = None

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

        # Mesma regra da criação (ver api_admin_criar_usuario): recrutador
        # não é mais atribuído manualmente — só existe como conta efêmera
        # provisionada pela chave de acesso da empresa.
        if perfil == PerfilUsuario.RECRUTADOR:
            return resposta_erro(
                "Recrutadores não são mais atribuídos manualmente. A conta é criada "
                "automaticamente quando alguém entra pela chave de acesso da empresa.",
                400,
            )

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
                # Libera imediatamente o guichê/mesa do usuário desativado
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


@app.route("/api/admin/empresas/<int:empresa_id>/regenerar-chave", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_regenerar_chave_empresa(empresa_id: int):
    """
    Gera uma NOVA chave de acesso de 8 dígitos para a empresa, invalidando
    a anterior imediatamente (quem ainda não entrou com a chave antiga
    precisa da nova; sessões de recrutador já ativas não são afetadas).
    Uso típico: suspeita de vazamento da chave atual.
    """
    try:
        nova_chave = database.regenerar_chave_empresa(empresa_id)
        if nova_chave is None:
            return resposta_erro("Empresa não encontrada.", 404)
        return resposta_sucesso({"chave_acesso": nova_chave})
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao regenerar chave de acesso: {erro}", 500)


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

    except ValueError as erro:
        return resposta_erro(str(erro), 409)
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


@app.route("/api/admin/empresas/<int:empresa_id>/reativar-emissao", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_admin_reativar_emissao_empresa(empresa_id: int):
    """
    Reativa a emissão de senhas de uma empresa cuja emissão foi bloqueada
    (ver ``/api/bloquear-emissao`` e
    ``database.desbloquear_emissao_empresa``) — volta a permitir a
    emissão de novas senhas para ela.

    Um administrador pode reativar QUALQUER empresa a qualquer momento —
    complementa (não substitui) a autorreativação já disponível para o
    próprio recrutador da empresa (ver ``/api/reativar-emissao``), útil
    por exemplo quando não há ninguém logado como recrutador daquela
    empresa no momento.
    """
    try:
        if database.desbloquear_emissao_empresa(empresa_id):
            return resposta_sucesso({"mensagem": "Emissão de senhas da empresa reativada."})
        return resposta_erro("Empresa não encontrada, ou a emissão dela já estava liberada.", 404)

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao reativar emissão de senhas da empresa: {erro}", 500)


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
