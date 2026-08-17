# -*- coding: utf-8 -*-
"""
test_chamada.py
================

Testa o fluxo de chamada de senhas: ``chamar_proxima``,
``repetir_ultima_chamada``, ``obter_chamada_atual`` (geral e por
empresa) e ``finalizar_atendimento_e_chamar_proxima``.

Cobre especificamente a regressão do "Alto impacto #1" do review: a
chamada atual (tanto no destaque do atendente quanto no painel público)
precisa trazer o nome da empresa correta mesmo na FILA GERAL (sem
``empresa_id``), que mistura senhas de várias empresas — antes da
correção, ``obter_chamada_atual()`` sem filtro de empresa não fazia o
JOIN com a tabela ``senhas`` e por isso nunca retornava o campo
``empresa``.
"""

import database


def test_chamar_proxima_retorna_empresa_da_senha(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    chamada = database.chamar_proxima(guiche="Mesa 01", usuario="Recepção")

    assert chamada is not None
    assert chamada["empresa"] == "Empresa Alfa"


def test_obter_chamada_atual_geral_traz_empresa_da_ultima_chamada(banco_teste):
    """Regressão do Alto impacto #1 (fila geral, sem empresa_id)."""
    alfa = database.criar_empresa("Alfa")
    beta = database.criar_empresa("Beta")

    database.criar_senha(empresa_id=alfa.id, empresa=alfa.nome)
    database.criar_senha(empresa_id=beta.id, empresa=beta.nome)

    database.chamar_proxima(guiche="Mesa 01", usuario="Rec")  # chama a de Alfa (FIFO)
    atual = database.obter_chamada_atual()

    assert atual is not None
    assert atual["empresa"] == "Alfa"

    database.chamar_proxima(guiche="Mesa 02", usuario="Rec2")  # chama a de Beta
    atual2 = database.obter_chamada_atual()

    assert atual2["empresa"] == "Beta"


def test_obter_chamada_atual_escopado_por_empresa_isola_entre_empresas(banco_teste):
    alfa = database.criar_empresa("Alfa")
    beta = database.criar_empresa("Beta")

    database.criar_senha(empresa_id=alfa.id, empresa=alfa.nome)
    database.criar_senha(empresa_id=beta.id, empresa=beta.nome)

    # Só Beta chamou até agora.
    database.chamar_proxima(guiche="Mesa 01", usuario="Rec", empresa_id=beta.id)

    assert database.obter_chamada_atual(empresa_id=alfa.id) is None
    atual_beta = database.obter_chamada_atual(empresa_id=beta.id)
    assert atual_beta is not None
    assert atual_beta["empresa"] == "Beta"


def test_repetir_ultima_chamada_mantem_empresa(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.chamar_proxima(guiche="Mesa 01", usuario="Recepção")

    repetida = database.repetir_ultima_chamada()

    assert repetida is not None
    assert repetida["empresa"] == "Empresa Alfa"


def test_repetir_ultima_chamada_sem_chamada_anterior_retorna_none(banco_teste):
    assert database.repetir_ultima_chamada() is None


def test_finalizar_atendimento_e_chamar_proxima(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    primeira = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    segunda = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    database.chamar_proxima(guiche="Mesa 01", usuario="Recepção")

    resultado = database.finalizar_atendimento_e_chamar_proxima(
        guiche="Mesa 01", usuario="Recepção"
    )

    assert resultado["senha_finalizada"]["id"] == primeira.id
    assert resultado["senha_finalizada"]["status"] == "Chamada"
    assert resultado["chamada"]["senha_id"] == segunda.id
    assert resultado["chamada"]["empresa"] == "Empresa Alfa"

    senha_finalizada_no_banco = database.obter_senha_por_id(primeira.id)
    assert senha_finalizada_no_banco.status == "Finalizada"
