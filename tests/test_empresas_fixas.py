# -*- coding: utf-8 -*-
"""
test_empresas_fixas.py
=======================

Testa as duas opções fixas de emissão de senha do sistema ("Criar
Currículos"/"Imprimir Currículos" — ver database.NOMES_EMPRESAS_FIXAS):
seed automático na inicialização do banco, proteção contra
renomear/desativar, e o comportamento de ``criar_senha`` com
``finalizar_imediatamente=True`` (a senha já nasce "Finalizada", sem
fila nem chamada).
"""

import pytest

import database
from models import StatusSenha


def _obter_fixa(nome):
    for linha in database.listar_empresas():
        if linha["nome"] == nome:
            return linha
    return None


def test_empresas_fixas_sao_criadas_automaticamente(banco_teste):
    """``banco_teste`` já chama ``database.inicializar_banco()``, que por
    sua vez chama ``_semear_empresas_fixas()`` — as duas opções já devem
    existir sem que nenhum teste precise criá-las manualmente."""
    for nome in database.NOMES_EMPRESAS_FIXAS:
        empresa = _obter_fixa(nome)
        assert empresa is not None, f"Empresa fixa '{nome}' não foi criada automaticamente."
        assert empresa["fixa"] is True or empresa["fixa"] == 1
        assert empresa["ativa"] is True or empresa["ativa"] == 1


def test_seed_e_idempotente_nao_duplica(banco_teste):
    # Chama de novo manualmente — não deve criar duplicatas.
    database._semear_empresas_fixas()
    database._semear_empresas_fixas()

    todas = database.listar_empresas()
    nomes = [linha["nome"] for linha in todas]
    for nome in database.NOMES_EMPRESAS_FIXAS:
        assert nomes.count(nome) == 1


def test_empresas_fixas_aparecem_primeiro_na_listagem(banco_teste):
    database.criar_empresa("Empresa Comum Z")
    database.criar_empresa("Empresa Comum A")

    todas = database.listar_empresas()
    primeiras_duas = {linha["nome"] for linha in todas[:2]}
    assert primeiras_duas == set(database.NOMES_EMPRESAS_FIXAS)


def test_nao_pode_renomear_empresa_fixa(banco_teste):
    fixa = _obter_fixa(database.NOMES_EMPRESAS_FIXAS[0])
    with pytest.raises(ValueError):
        database.renomear_empresa(fixa["id"], "Novo Nome Qualquer")

    # Nome permanece intacto.
    ainda_fixa = _obter_fixa(database.NOMES_EMPRESAS_FIXAS[0])
    assert ainda_fixa is not None


def test_nao_pode_desativar_empresa_fixa(banco_teste):
    fixa = _obter_fixa(database.NOMES_EMPRESAS_FIXAS[0])
    with pytest.raises(ValueError):
        database.definir_status_empresa(fixa["id"], False)

    ainda_ativa = _obter_fixa(database.NOMES_EMPRESAS_FIXAS[0])
    assert ainda_ativa["ativa"] is True or ainda_ativa["ativa"] == 1


def test_empresa_comum_continua_podendo_ser_renomeada_e_desativada(banco_teste):
    """Regressão: a proteção deve valer SÓ para as empresas fixas —
    empresas comuns continuam funcionando exatamente como antes."""
    comum = database.criar_empresa("Empresa Comum")

    assert database.renomear_empresa(comum.id, "Empresa Renomeada") is True
    assert database.definir_status_empresa(comum.id, False) is True


def test_criar_senha_finalizar_imediatamente(banco_teste):
    fixa = _obter_fixa(database.NOMES_EMPRESAS_FIXAS[0])
    senha = database.criar_senha(
        empresa_id=fixa["id"],
        empresa=fixa["nome"],
        finalizar_imediatamente=True,
    )

    assert senha.status == StatusSenha.FINALIZADA
    assert senha.hora_chamada is not None
    assert senha.hora_finalizada is not None
    assert senha.hora_chamada == senha.hora_finalizada == senha.data_hora

    recarregada = database.obter_senha_por_id(senha.id)
    assert recarregada.status == StatusSenha.FINALIZADA
    assert recarregada.hora_chamada == recarregada.hora_finalizada == recarregada.data_hora


def test_senha_finalizada_imediatamente_nao_aparece_na_fila(banco_teste):
    fixa = _obter_fixa(database.NOMES_EMPRESAS_FIXAS[1])
    database.criar_senha(empresa_id=fixa["id"], empresa=fixa["nome"], finalizar_imediatamente=True)

    assert database.listar_fila_atual(empresa_id=fixa["id"]) == []
    assert database.contar_aguardando(empresa_id=fixa["id"]) == 0


def test_senha_finalizada_imediatamente_conta_como_emitida_e_como_chamada(banco_teste):
    """
    Uma senha "realizada sem fila" (opções fixas) nunca gera um evento em
    ``eventos_chamada`` (não existe guichê anunciando nada), mas CONTA
    normalmente tanto como "senha emitida" quanto como "chamada
    realizada" nos relatórios/Painel Geral — afinal, o atendimento
    realmente aconteceu. ``contar_chamadas_realizadas_periodo`` conta por
    ``hora_chamada`` (preenchida na criação, ver ``criar_senha``), não
    por linhas em ``eventos_chamada``, exatamente para incluir este caso.
    """
    fixa = _obter_fixa(database.NOMES_EMPRESAS_FIXAS[0])
    database.criar_senha(empresa_id=fixa["id"], empresa=fixa["nome"], finalizar_imediatamente=True)

    assert database.contar_chamadas_realizadas_periodo(empresa_id=fixa["id"]) == 1
    emitidas = database.listar_senhas_periodo(empresa_id=fixa["id"])
    assert len(emitidas) == 1
    assert emitidas[0]["status"] == StatusSenha.FINALIZADA

    # Mas nenhum evento "real" de chamada é criado para ela (não existe
    # guichê/mesa anunciando nada) — o relatório de exportação "Chamadas
    # Realizadas" (log de eventos) continua sem incluí-la.
    assert database.listar_chamadas_periodo(empresa_id=fixa["id"]) == []


def test_senha_normal_continua_nascendo_emitida(banco_teste):
    """Regressão: sem passar finalizar_imediatamente (comportamento
    padrão), a senha continua nascendo 'Emitida', como sempre."""
    empresa = database.criar_empresa("Empresa Comum")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    assert senha.status == StatusSenha.EMITIDA
    assert senha.hora_chamada is None
    assert senha.hora_finalizada is None


def test_opcoes_fixas_contam_no_painel_geral(banco_teste):
    """
    database.resumo_geral_senhas (backend do Painel Geral) agrupa
    diretamente por senhas.status/senhas.empresa, sem depender de
    eventos_chamada — por isso já inclui as opções fixas corretamente
    nos totais gerais e na tabela "por empresa", sem precisar de nenhum
    tratamento especial.
    """
    criar = _obter_fixa(database.NOMES_EMPRESAS_FIXAS[0])
    imprimir = _obter_fixa(database.NOMES_EMPRESAS_FIXAS[1])
    database.criar_senha(empresa_id=criar["id"], empresa=criar["nome"], finalizar_imediatamente=True)
    database.criar_senha(empresa_id=criar["id"], empresa=criar["nome"], finalizar_imediatamente=True)
    database.criar_senha(empresa_id=imprimir["id"], empresa=imprimir["nome"], finalizar_imediatamente=True)

    resumo = database.resumo_geral_senhas()
    assert resumo["total_emitidas"] == 3
    assert resumo["total_atendidas"] == 3

    por_nome = {linha["empresa"]: linha for linha in resumo["por_empresa"]}
    assert por_nome[database.NOMES_EMPRESAS_FIXAS[0]]["atendidas"] == 2
    assert por_nome[database.NOMES_EMPRESAS_FIXAS[0]]["total"] == 2
    assert por_nome[database.NOMES_EMPRESAS_FIXAS[1]]["atendidas"] == 1
    assert por_nome[database.NOMES_EMPRESAS_FIXAS[1]]["total"] == 1


def test_opcoes_fixas_contam_em_listar_contagem_por_empresa(banco_teste):
    """listar_contagem_por_empresa (usada pela coluna "Senhas por
    Empresa" do Resumo do Período) conta por senhas.empresa, sem filtro
    de status — já inclui as opções fixas naturalmente."""
    fixa = _obter_fixa(database.NOMES_EMPRESAS_FIXAS[0])
    database.criar_senha(empresa_id=fixa["id"], empresa=fixa["nome"], finalizar_imediatamente=True)
    database.criar_senha(empresa_id=fixa["id"], empresa=fixa["nome"], finalizar_imediatamente=True)

    contagem = database.listar_contagem_por_empresa()
    por_nome = {linha["empresa"]: linha["total"] for linha in contagem}
    assert por_nome[database.NOMES_EMPRESAS_FIXAS[0]] == 2


def test_opcoes_fixas_aparecem_no_relatorio_de_emitidas(banco_teste):
    """listar_senhas_periodo (base do relatório "Senhas Emitidas" em
    CSV/Excel/PDF, e do total_emitidas do Resumo do Período) já inclui
    qualquer status, então as opções fixas aparecem normalmente."""
    fixa = _obter_fixa(database.NOMES_EMPRESAS_FIXAS[1])
    senha = database.criar_senha(empresa_id=fixa["id"], empresa=fixa["nome"], finalizar_imediatamente=True)

    emitidas = database.listar_senhas_periodo()
    ids = {item["id"] for item in emitidas}
    assert senha.id in ids
