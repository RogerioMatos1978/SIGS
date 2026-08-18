# -*- coding: utf-8 -*-
"""
test_nome_pessoa.py
====================

Testa o campo opcional "Primeiro Nome" (``senhas.nome_pessoa``),
digitado livremente pelo Emissor no momento da emissão (ver
app.py:api_emitir) e impresso no ticket quando preenchido (ver
printer.py:imprimir_senha). Cobre a persistência via
``database.criar_senha`` e a leitura de volta via
``database.obter_senha_por_id`` — o mesmo caminho usado pela reimpressão
(``app.py:api_reimprimir``) para preservar o nome numa segunda via.
"""

import database


def test_criar_senha_com_nome_pessoa(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(
        empresa_id=empresa.id, empresa=empresa.nome, nome_pessoa="Maria"
    )

    assert senha.nome_pessoa == "Maria"

    recarregada = database.obter_senha_por_id(senha.id)
    assert recarregada.nome_pessoa == "Maria"


def test_criar_senha_sem_nome_pessoa_fica_none(banco_teste):
    """O campo é OPCIONAL — diferente de empresa_id, omiti-lo não é erro,
    só significa que a linha "Nome:" não sai no ticket."""
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    assert senha.nome_pessoa is None

    recarregada = database.obter_senha_por_id(senha.id)
    assert recarregada.nome_pessoa is None


def test_nome_pessoa_preservado_apos_chamar_e_finalizar(banco_teste):
    """
    Garante que o nome sobrevive às transições de status (Emitida ->
    Chamada -> Finalizada) — é o mesmo dado que uma reimpressão (só
    permitida enquanto 'Emitida', mas o valor gravado não muda depois
    disso) usa para reimprimir a linha "Nome:" corretamente.
    """
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(
        empresa_id=empresa.id, empresa=empresa.nome, nome_pessoa="João"
    )

    database.chamar_proxima(guiche="Mesa 01", usuario="Recepção")
    database.finalizar_senha(senha.id)

    recarregada = database.obter_senha_por_id(senha.id)
    assert recarregada.nome_pessoa == "João"


def test_nome_pessoa_nao_afeta_numeracao_nem_outras_senhas(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    com_nome = database.criar_senha(
        empresa_id=empresa.id, empresa=empresa.nome, nome_pessoa="Ana"
    )
    sem_nome = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    assert com_nome.numero == 1
    assert sem_nome.numero == 2
    assert sem_nome.nome_pessoa is None
