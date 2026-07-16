# -*- coding: utf-8 -*-
"""
app.py
======

Ponto de entrada da aplicação SIGS (Sistema Integrado de Gerenciamento de
Senhas). Este módulo contém exclusivamente as rotas Flask (camada de
apresentação/API); toda a lógica de negócio está em ``database.py``, a
impressão física em ``printer.py`` e a configuração em ``config.py``.

Rotas principais:

    GET  /                      Tela principal (emissão/chamada de senhas)
    GET  /painel                Painel público de chamadas (tela cheia)
    GET  /configuracoes         Tela de configurações do sistema
    GET  /relatorios            Tela de geração de relatórios

    POST /api/emitir            Emite uma nova senha (grava + imprime)
    POST /api/chamar            Chama a próxima senha da fila (FIFO)
    POST /api/repetir           Repete a última chamada realizada
    POST /api/reiniciar         Reinicia o contador de senhas
    GET  /api/painel/status     Dados consumidos pelo painel (polling)
    GET  /api/fila              Lista da fila atual (tela principal)
    POST /api/senha/<id>/finalizar
    POST /api/senha/<id>/cancelar

    GET  /api/config            Retorna as configurações atuais (JSON)
    POST /api/config            Atualiza as configurações do sistema
    GET  /api/impressoras       Lista as impressoras instaladas no Windows

    GET  /api/relatorios/csv        Exporta relatório em CSV
    GET  /api/relatorios/excel      Exporta relatório em Excel (.xlsx)
    GET  /api/relatorios/pdf        Exporta relatório em PDF
    GET  /api/relatorios/resumo     Retorna estatísticas resumidas (JSON)

Execução:
    python app.py
"""

import csv
import io
from datetime import datetime

from flask import Flask, jsonify, render_template, request, send_file

import database
from config import STATIC_DIR, TEMPLATES_DIR, config_manager, logger
from printer import ErroImpressora, ImpressoraTermica

# ---------------------------------------------------------------------------
# Inicialização da aplicação Flask
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    static_folder=str(STATIC_DIR),
    template_folder=str(TEMPLATES_DIR),
)

# Garante que o banco de dados e as tabelas existam antes de qualquer
# requisição ser atendida.
database.inicializar_banco()


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


# ---------------------------------------------------------------------------
# Rotas de páginas (HTML)
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Tela principal, utilizada pelo atendente para emitir e chamar senhas."""
    configuracoes = config_manager.obter_todas()
    return render_template("index.html", config=configuracoes)


@app.route("/painel")
def painel():
    """Painel público de chamadas, projetado para exibição em TV/monitor."""
    configuracoes = config_manager.obter_todas()
    return render_template("painel.html", config=configuracoes)


@app.route("/configuracoes")
def configuracoes_tela():
    """Tela de configurações gerais do sistema."""
    configuracoes = config_manager.obter_todas()
    impressoras = ImpressoraTermica.listar_impressoras_instaladas()
    return render_template("configuracoes.html", config=configuracoes, impressoras=impressoras)


@app.route("/relatorios")
def relatorios_tela():
    """Tela de geração de relatórios (CSV, Excel, PDF)."""
    configuracoes = config_manager.obter_todas()
    return render_template("relatorios.html", config=configuracoes)


# ---------------------------------------------------------------------------
# API - Emissão e chamada de senhas
# ---------------------------------------------------------------------------

@app.route("/api/emitir", methods=["POST"])
def api_emitir():
    """
    Emite uma nova senha: grava no banco de dados e envia para impressão
    imediatamente. Caso a impressão falhe, a senha permanece gravada no
    banco (o atendimento não deve ser bloqueado por falha de impressora),
    mas o erro é reportado ao cliente para que o atendente seja avisado.
    """
    try:
        dados = request.get_json(silent=True) or {}
        guiche = str(dados.get("guiche") or "").strip() or None
        usuario = str(dados.get("usuario") or "").strip() or None

        senha = database.criar_senha(guiche=guiche, usuario=usuario)

        erro_impressao = None
        try:
            configuracoes = config_manager.obter_todas()
            impressora = ImpressoraTermica(configuracoes.get("nome_impressora") or None)
            impressora.imprimir_senha(
                numero=senha.numero,
                nome_evento=configuracoes.get("nome_evento", ""),
                caminho_logo=configuracoes.get("logo_path"),
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
def api_chamar():
    """Chama a próxima senha da fila (FIFO), respeitando guichê/usuário
    informados pelo atendente."""
    try:
        dados = request.get_json(silent=True) or {}
        guiche = str(dados.get("guiche") or "Guichê 01").strip()
        usuario = str(dados.get("usuario") or "Atendente").strip()

        resultado = database.chamar_proxima(guiche=guiche, usuario=usuario)
        if resultado is None:
            return resposta_erro("Não há senhas aguardando chamada.", 404)

        return resposta_sucesso({"chamada": resultado})

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao chamar próxima senha: {erro}", 500)


@app.route("/api/repetir", methods=["POST"])
def api_repetir():
    """Repete a última chamada realizada (nova animação/bip no painel)."""
    try:
        resultado = database.repetir_ultima_chamada()
        if resultado is None:
            return resposta_erro("Nenhuma chamada foi realizada ainda.", 404)

        return resposta_sucesso({"chamada": resultado})

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao repetir chamada: {erro}", 500)


@app.route("/api/reiniciar", methods=["POST"])
def api_reiniciar():
    """Reinicia o contador de numeração de senhas para zero."""
    try:
        database.reiniciar_contador()
        return resposta_sucesso({"mensagem": "Contador reiniciado com sucesso."})
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao reiniciar contador: {erro}", 500)


@app.route("/api/fila")
def api_fila():
    """Retorna a fila atual de senhas aguardando chamada."""
    try:
        fila = database.listar_fila_atual()
        return resposta_sucesso({"fila": fila, "total_aguardando": database.contar_aguardando()})
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao consultar fila: {erro}", 500)


@app.route("/api/senha/<int:senha_id>/finalizar", methods=["POST"])
def api_finalizar(senha_id: int):
    """Marca uma senha como finalizada."""
    if database.finalizar_senha(senha_id):
        return resposta_sucesso({"mensagem": "Senha finalizada."})
    return resposta_erro("Senha não encontrada.", 404)


@app.route("/api/senha/<int:senha_id>/cancelar", methods=["POST"])
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
def api_config_obter():
    """Retorna todas as configurações atuais do sistema."""
    return resposta_sucesso({"config": config_manager.obter_todas()})


@app.route("/api/config", methods=["POST"])
def api_config_salvar():
    """Atualiza uma ou mais configurações do sistema."""
    try:
        dados = request.get_json(silent=True) or {}
        if not dados:
            return resposta_erro("Nenhum dado de configuração foi enviado.", 400)

        config_manager.salvar(dados)
        return resposta_sucesso({"config": config_manager.obter_todas()})

    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao salvar configurações: {erro}", 500)


@app.route("/api/impressoras")
def api_impressoras():
    """Lista as impressoras instaladas no Windows, para a tela de
    Configurações popular o campo de seleção."""
    return resposta_sucesso({"impressoras": ImpressoraTermica.listar_impressoras_instaladas()})


# ---------------------------------------------------------------------------
# API - Relatórios
# ---------------------------------------------------------------------------

def _parametros_periodo():
    """Extrai e retorna os parâmetros de período (inicio/fim) da querystring."""
    inicio = request.args.get("inicio") or None
    fim = request.args.get("fim") or None
    return inicio, fim


@app.route("/api/relatorios/resumo")
def api_relatorios_resumo():
    """Retorna um resumo estatístico (JSON) para exibição na tela de
    relatórios: total emitidas, total chamadas e tempo médio de espera."""
    try:
        inicio, fim = _parametros_periodo()
        emitidas = database.listar_senhas_periodo(inicio, fim)
        chamadas = database.listar_chamadas_periodo(inicio, fim)
        tempo_medio = database.tempo_medio_atendimento(inicio, fim)

        return resposta_sucesso(
            {
                "total_emitidas": len(emitidas),
                "total_chamadas": len(chamadas),
                "tempo_medio": tempo_medio,
            }
        )
    except Exception as erro:  # pragma: no cover
        return resposta_erro(f"Erro ao gerar resumo: {erro}", 500)


@app.route("/api/relatorios/csv")
def api_relatorios_csv():
    """Gera e retorna um relatório em formato CSV para download."""
    try:
        tipo = request.args.get("tipo", "emitidas")
        inicio, fim = _parametros_periodo()

        buffer_texto = io.StringIO()
        escritor = csv.writer(buffer_texto, delimiter=";")

        if tipo == "chamadas":
            escritor.writerow(["ID Evento", "ID Senha", "Número", "Guichê", "Usuário", "Data/Hora"])
            for item in database.listar_chamadas_periodo(inicio, fim):
                escritor.writerow(
                    [item["id"], item["senha_id"], item["numero"], item["guiche"], item["usuario"], item["data_hora"]]
                )
            nome_arquivo = "relatorio_chamadas.csv"
        else:
            escritor.writerow(["ID", "Número", "Status", "Data/Hora", "Guichê", "Usuário"])
            for item in database.listar_senhas_periodo(inicio, fim):
                escritor.writerow(
                    [item["id"], item["numero"], item["status"], item["data_hora"], item["guiche"], item["usuario"]]
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
def api_relatorios_excel():
    """Gera e retorna um relatório em formato Excel (.xlsx) para download."""
    try:
        # Importação local para não exigir openpyxl caso o relatório em
        # Excel nunca seja utilizado (reduz acoplamento e tempo de boot).
        from openpyxl import Workbook
        from openpyxl.styles import Font

        tipo = request.args.get("tipo", "emitidas")
        inicio, fim = _parametros_periodo()

        pasta = Workbook()
        planilha = pasta.active

        if tipo == "chamadas":
            planilha.title = "Chamadas"
            cabecalho = ["ID Evento", "ID Senha", "Número", "Guichê", "Usuário", "Data/Hora"]
            planilha.append(cabecalho)
            for item in database.listar_chamadas_periodo(inicio, fim):
                planilha.append(
                    [item["id"], item["senha_id"], item["numero"], item["guiche"], item["usuario"], item["data_hora"]]
                )
            nome_arquivo = "relatorio_chamadas.xlsx"
        else:
            planilha.title = "Emitidas"
            cabecalho = ["ID", "Número", "Status", "Data/Hora", "Guichê", "Usuário"]
            planilha.append(cabecalho)
            for item in database.listar_senhas_periodo(inicio, fim):
                planilha.append(
                    [item["id"], item["numero"], item["status"], item["data_hora"], item["guiche"], item["usuario"]]
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
        inicio, fim = _parametros_periodo()

        buffer_bytes = io.BytesIO()
        documento = SimpleDocTemplate(buffer_bytes, pagesize=A4)
        estilos = getSampleStyleSheet()
        elementos = []

        titulo = "Relatório de Senhas Emitidas" if tipo != "chamadas" else "Relatório de Chamadas"
        elementos.append(Paragraph(titulo, estilos["Title"]))
        elementos.append(Spacer(1, 0.5 * cm))

        if tipo == "chamadas":
            dados_tabela = [["ID Evento", "ID Senha", "Número", "Guichê", "Usuário", "Data/Hora"]]
            for item in database.listar_chamadas_periodo(inicio, fim):
                dados_tabela.append(
                    [
                        str(item["id"]),
                        str(item["senha_id"]),
                        f"{item['numero']:03d}",
                        item["guiche"] or "-",
                        item["usuario"] or "-",
                        item["data_hora"],
                    ]
                )
            nome_arquivo = "relatorio_chamadas.pdf"
        else:
            dados_tabela = [["ID", "Número", "Status", "Data/Hora", "Guichê", "Usuário"]]
            for item in database.listar_senhas_periodo(inicio, fim):
                dados_tabela.append(
                    [
                        str(item["id"]),
                        f"{item['numero']:03d}",
                        item["status"],
                        item["data_hora"],
                        item["guiche"] or "-",
                        item["usuario"] or "-",
                    ]
                )
            nome_arquivo = "relatorio_emitidas.pdf"

        # Inclui o resumo de tempo médio de atendimento ao final do relatório.
        tempo_medio = database.tempo_medio_atendimento(inicio, fim)

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
# Tratamento global de erros
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def erro_404(_erro):
    return jsonify({"sucesso": False, "erro": "Recurso não encontrado."}), 404


@app.errorhandler(500)
def erro_500(erro):
    logger.error("Erro interno não tratado: %s", erro)
    return jsonify({"sucesso": False, "erro": "Erro interno do servidor."}), 500


# ---------------------------------------------------------------------------
# Execução direta (desenvolvimento)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # host="0.0.0.0" permite o acesso do painel a partir de outros
    # dispositivos na mesma rede (ex.: TV/monitor conectado via navegador).
    # Em produção, considere usar um servidor WSGI dedicado (waitress no
    # Windows), conforme documentado no README.md.
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
