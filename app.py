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
    GET  /painel                Painel público de chamadas (tela cheia) [público]
    GET  /configuracoes         Tela de configurações do sistema [admin]
    GET  /relatorios            Tela de geração de relatórios [admin]
    GET/POST /login             Autenticação de usuários
    POST /logout                Encerra sessão e libera o guichê
    GET  /admin/usuarios        Gerenciamento de usuários [admin]
    GET  /admin/empresas        Gerenciamento de empresas do feirão [admin]

    POST /api/emitir            Emite uma nova senha (grava + imprime)
    POST /api/chamar            Chama a próxima senha da fila (FIFO)
    POST /api/repetir           Repete a última chamada realizada
    POST /api/finalizar-atendimento  Finaliza o atendimento e já chama a próxima
    POST /api/reiniciar         Reinicia o contador de senhas
    GET  /api/painel/status     Dados consumidos pelo painel (polling)
    GET  /api/fila              Lista da fila atual (tela principal)
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
from datetime import datetime, timedelta
from typing import Optional

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

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
    Disponibiliza as variáveis ``usuario_logado``, ``eh_admin`` e
    ``sigs_versao`` para TODOS os templates automaticamente, sem precisar
    repassá-las manualmente em cada chamada a ``render_template``.
    ``sigs_versao`` é usada pelo rodapé fixo (ver templates/layout.html).
    """
    return {
        "usuario_logado": auth.usuario_logado(),
        "eh_admin": auth.eh_admin(),
        "sigs_versao": SIGS_VERSAO,
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
    """Formata o número do guichê do usuário logado como 'Guichê 01',
    'Guichê 02' etc., ou ``None`` se o usuário não ocupa guichê algum.

    Centralizar essa formatação evita repetir ``f"Guichê {n:02d}"`` em
    cada rota que precisa dela (``/api/chamar`` e
    ``/api/finalizar-atendimento``), reduzindo o risco de inconsistência."""
    guiche = usuario_sessao.get("guiche")
    return f"Guichê {guiche:02d}" if guiche else None


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
    ativação/desativação e alteração de perfil. Acesso restrito a
    administradores."""
    usuarios = database.listar_usuarios()
    guiches_ocupados = database.listar_guiches_ocupados()
    return render_template(
        "usuarios.html",
        config=config_manager.obter_todas(),
        usuarios=usuarios,
        guiches_ocupados=guiches_ocupados,
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

        senha = database.criar_senha(guiche=guiche, usuario=usuario, empresa=empresa.nome)

        erro_impressao = None
        try:
            configuracoes = config_manager.obter_todas()
            nome_impressora = impressora_escolhida or configuracoes.get("nome_impressora") or None
            impressora = ImpressoraTermica(nome_impressora)
            impressora.imprimir_senha(
                numero=senha.numero,
                nome_evento=configuracoes.get("nome_evento", ""),
                caminho_logo=configuracoes.get("logo_path"),
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
    """Chama a próxima senha da fila (FIFO), sempre em nome do guichê e do
    usuário atualmente logados (obtidos da sessão, nunca do corpo da
    requisição, para evitar chamadas em nome de outro guichê)."""
    try:
        usuario_sessao = auth.usuario_logado()
        if not usuario_sessao.get("guiche"):
            return resposta_erro(
                "Você não possui um guichê atribuído no momento (todos ocupados). "
                "Faça logout e login novamente ou contate um administrador.",
                409,
            )

        guiche = _guiche_formatado(usuario_sessao)
        usuario = usuario_sessao.get("nome_completo")

        resultado = database.chamar_proxima(guiche=guiche, usuario=usuario)
        if resultado is None:
            return resposta_erro("Não há senhas aguardando chamada.", 404)

        return resposta_sucesso({"chamada": resultado})

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao chamar próxima senha: {erro}", 500)


@app.route("/api/repetir", methods=["POST"])
@auth.login_required
def api_repetir():
    """Repete a última chamada realizada (nova animação/bip no painel)."""
    try:
        resultado = database.repetir_ultima_chamada()
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
        if not usuario_sessao.get("guiche"):
            return resposta_erro(
                "Você não possui um guichê atribuído no momento. Apenas usuários "
                "com perfil atendente e guichê ativo podem finalizar atendimentos.",
                409,
            )

        guiche = _guiche_formatado(usuario_sessao)
        usuario = usuario_sessao.get("nome_completo")

        resultado = database.finalizar_atendimento_e_chamar_proxima(guiche=guiche, usuario=usuario)

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
    """Reinicia o contador de numeração de senhas para zero. Restrito a
    administradores (é uma operação sensível que afeta todos os guichês)."""
    try:
        database.reiniciar_contador()
        return resposta_sucesso({"mensagem": "Contador reiniciado com sucesso."})
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao reiniciar contador: {erro}", 500)


@app.route("/api/fila")
@auth.login_required
def api_fila():
    """Retorna a fila atual de senhas aguardando chamada."""
    try:
        fila = database.listar_fila_atual()
        return resposta_sucesso({"fila": fila, "total_aguardando": database.contar_aguardando()})
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao consultar fila: {erro}", 500)


@app.route("/api/senha/<int:senha_id>/finalizar", methods=["POST"])
@auth.login_required
def api_finalizar(senha_id: int):
    """Marca uma senha como finalizada."""
    if database.finalizar_senha(senha_id):
        return resposta_sucesso({"mensagem": "Senha finalizada."})
    return resposta_erro("Senha não encontrada.", 404)


@app.route("/api/senha/<int:senha_id>/cancelar", methods=["POST"])
@auth.login_required
def api_cancelar(senha_id: int):
    """Marca uma senha como cancelada."""
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

        if not nome_completo or not login_novo:
            return resposta_erro("Informe nome completo e login.", 400)

        erro_senha = auth.validar_forca_senha(senha)
        if erro_senha:
            return resposta_erro(erro_senha, 400)

        if perfil not in PerfilUsuario.TODOS:
            return resposta_erro("Perfil inválido.", 400)

        usuario = database.criar_usuario(
            nome_completo=nome_completo,
            login=login_novo,
            senha_hash=auth.gerar_hash_senha(senha),
            perfil=perfil,
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
                # Libera imediatamente o guichê do usuário desativado.
                database.liberar_guiche(usuario_id)
            return resposta_sucesso({"mensagem": "Status do usuário atualizado."})
        return resposta_erro("Usuário não encontrado.", 404)

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao atualizar status: {erro}", 500)


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
