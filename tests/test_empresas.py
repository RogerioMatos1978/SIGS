# -*- coding: utf-8 -*-
"""
test_empresas.py
=================

Testa o cadastro e a administração de empresas do feirão do emprego:
criação, duplicidade de nome (inclusive por maiúsculas/minúsculas),
renomeação, ativação/inativação e o ciclo de finalizar/reabrir o
atendimento do dia por empresa.
"""

import pytest

import database


def test_criar_empresa(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    assert empresa.id is not None
    assert empresa.nome == "Empresa Alfa"
    assert empresa.ativa is True
    assert empresa.contador_atual == 0


def test_criar_empresa_com_nome_duplicado_exato_falha(banco_teste):
    database.criar_empresa("Empresa Alfa")
    with pytest.raises(ValueError):
        database.criar_empresa("Empresa Alfa")


def test_criar_empresa_com_nome_duplicado_por_maiusc_minusc_falha(banco_teste):
    """
    Regressão: antes da migração ``idx_empresas_nome_nocase``, era possível
    cadastrar "Empresa Alfa" e "empresa alfa" como duas empresas
    DIFERENTES, gerando confusão na emissão de senhas e nos relatórios.
    """
    database.criar_empresa("Empresa Alfa")
    with pytest.raises(ValueError):
        database.criar_empresa("empresa alfa")
    with pytest.raises(ValueError):
        database.criar_empresa("EMPRESA ALFA")


def test_renomear_empresa(banco_teste):
    empresa = database.criar_empresa("Nome Antigo")
    ok = database.renomear_empresa(empresa.id, "Nome Novo")
    assert ok is True

    atualizada = database.obter_empresa_por_id(empresa.id)
    assert atualizada.nome == "Nome Novo"


def test_renomear_empresa_para_nome_ja_usado_por_maiusc_minusc_falha(banco_teste):
    database.criar_empresa("Alfa")
    beta = database.criar_empresa("Beta")

    with pytest.raises(ValueError):
        database.renomear_empresa(beta.id, "alfa")


def test_definir_status_empresa_ativa_inativa(banco_teste):
    empresa = database.criar_empresa("Empresa Teste")

    assert database.definir_status_empresa(empresa.id, False) is True
    assert database.obter_empresa_por_id(empresa.id).ativa is False

    assert database.definir_status_empresa(empresa.id, True) is True
    assert database.obter_empresa_por_id(empresa.id).ativa is True


def test_listar_empresas_somente_ativas(banco_teste):
    ativa = database.criar_empresa("Ativa")
    inativa = database.criar_empresa("Inativa")
    database.definir_status_empresa(inativa.id, False)

    todas = database.listar_empresas(somente_ativas=False)
    apenas_ativas = database.listar_empresas(somente_ativas=True)

    ids_todas = {linha["id"] for linha in todas}
    ids_ativas = {linha["id"] for linha in apenas_ativas}

    assert {ativa.id, inativa.id}.issubset(ids_todas)
    assert ativa.id in ids_ativas
    assert inativa.id not in ids_ativas


def test_finalizar_e_reabrir_atendimento_do_dia_por_empresa(banco_teste):
    empresa = database.criar_empresa("Empresa Teste")

    resultado = database.finalizar_atendimento_dia_empresa(empresa.id)
    assert resultado["ja_finalizado"] is False
    assert resultado["finalizado_em"] is not None

    atualizada = database.obter_empresa_por_id(empresa.id)
    assert atualizada.atendimento_finalizado_em is not None

    # Finalizar de novo deve ser idempotente (sinalizado por ja_finalizado).
    resultado2 = database.finalizar_atendimento_dia_empresa(empresa.id)
    assert resultado2["ja_finalizado"] is True

    ok = database.reabrir_atendimento_empresa(empresa.id)
    assert ok is True
    reaberta = database.obter_empresa_por_id(empresa.id)
    assert reaberta.atendimento_finalizado_em is None
