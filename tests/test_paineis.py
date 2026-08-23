# -*- coding: utf-8 -*-
"""
test_paineis.py
================

Testa que os painéis públicos (painel.html, painel_empresa.html e
painel_geral.html) não mostram senhas "Finalizada" nem "Cancelada" — só a
situação ATUAL da fila (aguardando/em atendimento). Cobre
``database.listar_ultimas_emitidas`` (histórico usado pelo painel geral
de chamadas e pelo painel por empresa).
"""

import database
from models import StatusSenha


def test_listar_ultimas_emitidas_exclui_finalizadas_e_canceladas(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")

    ainda_esperando = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    sera_chamada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    finalizada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    cancelada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    # Chama só a PRIMEIRA senha da fila (FIFO) — "sera_chamada" continua
    # esperando por enquanto, então cria uma segunda senha antes para
    # garantir que "ainda_esperando" (a 1ª) seja a chamada, deixando
    # "sera_chamada" (a 2ª) ainda em status Emitida.
    database.chamar_proxima(guiche="Guichê 01", usuario="Atendente", empresa_id=empresa.id)
    database.finalizar_senha(finalizada.id)
    database.cancelar_senha(cancelada.id)

    lista = database.listar_ultimas_emitidas(quantidade=10, empresa_id=empresa.id)
    ids_na_lista = {item["id"] for item in lista}

    # "ainda_esperando" foi chamada (status Chamada) e "sera_chamada"
    # continua esperando (status Emitida) — ambas continuam aparecendo.
    assert ainda_esperando.id in ids_na_lista
    assert sera_chamada.id in ids_na_lista
    assert finalizada.id not in ids_na_lista
    assert cancelada.id not in ids_na_lista

    for item in lista:
        assert item["status"] not in (StatusSenha.FINALIZADA, StatusSenha.CANCELADA)


def test_listar_ultimas_emitidas_mostra_aguardando_e_chamada(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    senha1 = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    senha2 = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    database.chamar_proxima(guiche="Guichê 01", usuario="Atendente", empresa_id=empresa.id)

    lista = database.listar_ultimas_emitidas(quantidade=10, empresa_id=empresa.id)
    ids_na_lista = {item["id"] for item in lista}

    # senha1 foi chamada (status Chamada) e senha2 ainda está esperando
    # (status Emitida) — ambas continuam aparecendo, só Finalizada e
    # Cancelada é que somem.
    assert senha1.id in ids_na_lista
    assert senha2.id in ids_na_lista


def test_listar_ultimas_emitidas_respeita_filtro_de_empresa(banco_teste):
    empresa_a = database.criar_empresa("Empresa A")
    empresa_b = database.criar_empresa("Empresa B")
    senha_a = database.criar_senha(empresa_id=empresa_a.id, empresa=empresa_a.nome)
    database.criar_senha(empresa_id=empresa_b.id, empresa=empresa_b.nome)

    lista = database.listar_ultimas_emitidas(quantidade=10, empresa_id=empresa_a.id)
    ids_na_lista = {item["id"] for item in lista}

    assert ids_na_lista == {senha_a.id}


def test_resumo_geral_continua_calculando_atendidas_e_canceladas(banco_teste):
    """
    O backend (database.resumo_geral_senhas) continua calculando esses
    números normalmente — a decisão de NÃO exibi-los é só da camada de
    apresentação do painel geral (templates/painel_geral.html +
    static/js/painel_geral.js), não do banco de dados. Outras telas
    (relatórios) continuam podendo usar esses totais.
    """
    empresa = database.criar_empresa("Empresa Alfa")
    finalizada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    cancelada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.finalizar_senha(finalizada.id)
    database.cancelar_senha(cancelada.id)

    resumo = database.resumo_geral_senhas()
    assert resumo["total_atendidas"] == 1
    assert resumo["total_canceladas"] == 1


def test_api_painel_geral_status_expoe_resumo_do_feirao(banco_teste):
    """
    A seção "Resumo do Feirão" da tela pública /painel/geral (ver
    templates/painel_geral.html) é a exceção proposital ao critério
    "esconde Finalizada/Cancelada": ela mostra o TOTAL acumulado do
    evento inteiro. Este teste confirma, via HTTP (rota pública, sem
    login — ver app.py:api_painel_geral_status), que o campo
    ``resumo_feirao`` inclui as senhas já finalizadas nos totais.

    A rota usa o mesmo módulo ``database`` (mesmo objeto em
    ``sys.modules``) já isolado pela fixture ``banco_teste``, então não
    é preciso nenhum monkeypatch adicional aqui.
    """
    import app as app_modulo

    empresa = database.criar_empresa("Empresa Alfa")
    aguardando = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    finalizada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.chamar_proxima(guiche="01", usuario="Teste", empresa_id=empresa.id)
    database.finalizar_senha(finalizada.id)

    cliente = app_modulo.app.test_client()
    resposta = cliente.get("/api/painel/geral/status")
    corpo = resposta.get_json()

    assert resposta.status_code == 200
    assert corpo["sucesso"] is True
    # As duas senhas (aguardando/chamada e finalizada) contam no total
    # geral de emitidas do resumo do feirão, mesmo a finalizada não
    # aparecendo nos cards "em andamento" (dados.resumo).
    assert corpo["resumo_feirao"]["total_emitidas"] == 2
    assert "total_atendidas" in corpo["resumo_feirao"]
    assert "tempo_medio_formatado" in corpo["resumo_feirao"]["tempo_medio"]


def test_resumo_do_feirao_soma_empresas_fixas_mas_exclui_canceladas(banco_teste):
    """
    ``resumo_feirao.total_emitidas`` deve contar as senhas das duas
    opções fixas ("Criar Currículos"/"Imprimir Currículos" — são
    atendimentos reais, mesmo sem fila/chamada), mas NÃO as Canceladas
    — uma senha cancelada não representa um atendimento nem uma emissão
    válida para este indicador (regra explícita a partir da v2.15.0;
    antes disso, canceladas eram somadas também). ``total_atendidas``
    continua contando as duas fixas normalmente, já que "emiti-las" JÁ É
    o próprio atendimento.
    """
    import app as app_modulo

    empresa = database.criar_empresa("Empresa Alfa")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    cancelada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.cancelar_senha(cancelada.id)

    fixa1 = next(l for l in database.listar_empresas() if l["nome"] == database.NOMES_EMPRESAS_FIXAS[0])
    fixa2 = next(l for l in database.listar_empresas() if l["nome"] == database.NOMES_EMPRESAS_FIXAS[1])
    database.criar_senha(empresa_id=fixa1["id"], empresa=fixa1["nome"], finalizar_imediatamente=True)
    database.criar_senha(empresa_id=fixa2["id"], empresa=fixa2["nome"], finalizar_imediatamente=True)

    cliente = app_modulo.app.test_client()
    corpo = cliente.get("/api/painel/geral/status").get_json()

    # 1 aguardando + 2 das opções fixas = 3 — a cancelada fica de fora.
    assert corpo["resumo_feirao"]["total_emitidas"] == 3
    # As duas opções fixas contam como "atendidas" (nascem já
    # 'Finalizada', com hora_chamada preenchida); a cancelada não conta
    # em nenhum dos dois totais.
    assert corpo["resumo_feirao"]["total_atendidas"] == 2


def test_resumo_do_feirao_total_emitidas_nunca_conta_cancelada_isolada(banco_teste):
    """
    Regressão focada só na exclusão de Canceladas (sem outras senhas no
    banco): uma única senha cancelada não deve aparecer em
    ``total_emitidas`` nem em ``total_atendidas``.
    """
    import app as app_modulo

    empresa = database.criar_empresa("Empresa Alfa")
    cancelada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.cancelar_senha(cancelada.id)

    cliente = app_modulo.app.test_client()
    corpo = cliente.get("/api/painel/geral/status").get_json()

    assert corpo["resumo_feirao"]["total_emitidas"] == 0
    assert corpo["resumo_feirao"]["total_atendidas"] == 0


def test_resumo_do_feirao_atendimentos_realizados_inclui_curriculos_fixos(banco_teste):
    """
    Confirmação explícita do pedido do usuário: "Total de Atendimentos
    Realizados" deve contabilizar as senhas de Criar Currículos/Imprimir
    Currículos, já que são atendimentos (mesmo sem fila/chamada — a
    própria emissão já é o atendimento, ver criar_senha,
    finalizar_imediatamente). Cenário isolado, sem nenhuma outra senha
    no banco, para não depender de nenhum outro comportamento.
    """
    import app as app_modulo

    fixa1 = next(l for l in database.listar_empresas() if l["nome"] == database.NOMES_EMPRESAS_FIXAS[0])
    fixa2 = next(l for l in database.listar_empresas() if l["nome"] == database.NOMES_EMPRESAS_FIXAS[1])
    database.criar_senha(empresa_id=fixa1["id"], empresa=fixa1["nome"], finalizar_imediatamente=True)
    database.criar_senha(empresa_id=fixa2["id"], empresa=fixa2["nome"], finalizar_imediatamente=True)
    database.criar_senha(empresa_id=fixa1["id"], empresa=fixa1["nome"], finalizar_imediatamente=True)

    cliente = app_modulo.app.test_client()
    corpo = cliente.get("/api/painel/geral/status").get_json()

    assert corpo["resumo_feirao"]["total_atendidas"] == 3
    assert corpo["resumo_feirao"]["total_emitidas"] == 3
