# -*- coding: utf-8 -*-
"""
test_empresas.py
=================

Testa o cadastro e a administração de empresas do feirão do emprego:
criação, duplicidade de nome (inclusive por maiúsculas/minúsculas),
renomeação, ativação/inativação e o ciclo de bloquear/reativar a
emissão de senhas por empresa.
"""

import pytest

import database
from models import StatusSenha


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


def test_bloquear_e_reativar_emissao_de_senhas_por_empresa(banco_teste):
    empresa = database.criar_empresa("Empresa Teste")

    resultado = database.bloquear_emissao_empresa(empresa.id)
    assert resultado["ja_bloqueado"] is False
    assert resultado["bloqueado_em"] is not None

    atualizada = database.obter_empresa_por_id(empresa.id)
    assert atualizada.emissao_bloqueada_em is not None

    # Bloquear de novo deve ser idempotente (sinalizado por ja_bloqueado).
    resultado2 = database.bloquear_emissao_empresa(empresa.id)
    assert resultado2["ja_bloqueado"] is True

    ok = database.desbloquear_emissao_empresa(empresa.id)
    assert ok is True
    reativada = database.obter_empresa_por_id(empresa.id)
    assert reativada.emissao_bloqueada_em is None


def test_bloquear_emissao_nao_cancela_senhas_em_espera(banco_teste):
    """
    Regressão: diferente do antigo "Finalizar Atendimento do Dia", o novo
    "Bloqueio de Emissão de Senhas" NÃO cancela as senhas que ainda
    estavam esperando na fila — apenas impede que NOVAS sejam emitidas. A
    fila existente continua podendo ser chamada/atendida normalmente.
    """
    empresa = database.criar_empresa("Empresa Teste")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    database.bloquear_emissao_empresa(empresa.id)

    senha_apos_bloqueio = database.obter_senha_por_id(senha.id)
    assert senha_apos_bloqueio.status == StatusSenha.EMITIDA

    # A fila continua atendível normalmente: dá para chamar essa mesma
    # senha mesmo com a emissão bloqueada.
    chamada = database.chamar_proxima(guiche="Guichê 01", usuario="Atendente Teste", empresa_id=empresa.id)
    assert chamada is not None
    assert chamada["senha_id"] == senha.id
