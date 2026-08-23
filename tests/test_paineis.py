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
