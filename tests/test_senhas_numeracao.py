# -*- coding: utf-8 -*-
"""
test_senhas_numeracao.py
=========================

Testa a numeração de senhas por empresa: cada empresa tem sua própria
sequência independente (001, 002, 003...), sem interferência entre
empresas diferentes, e ``reiniciar_contador_empresa`` reinicia apenas a
sequência da empresa indicada.
"""

import database


def test_numeracao_comeca_em_um_para_cada_empresa(banco_teste):
    alfa = database.criar_empresa("Alfa")
    beta = database.criar_empresa("Beta")

    senha_alfa = database.criar_senha(empresa_id=alfa.id, empresa=alfa.nome)
    senha_beta = database.criar_senha(empresa_id=beta.id, empresa=beta.nome)

    assert senha_alfa.numero == 1
    assert senha_beta.numero == 1


def test_numeracao_e_independente_entre_empresas_intercalada(banco_teste):
    """
    Emissões intercaladas (A, B, A, B, A) não podem "vazar" número de uma
    empresa para outra — cada uma segue sua própria sequência.
    """
    alfa = database.criar_empresa("Alfa")
    beta = database.criar_empresa("Beta")

    numeros_alfa = []
    numeros_beta = []

    numeros_alfa.append(database.criar_senha(empresa_id=alfa.id, empresa=alfa.nome).numero)
    numeros_beta.append(database.criar_senha(empresa_id=beta.id, empresa=beta.nome).numero)
    numeros_alfa.append(database.criar_senha(empresa_id=alfa.id, empresa=alfa.nome).numero)
    numeros_beta.append(database.criar_senha(empresa_id=beta.id, empresa=beta.nome).numero)
    numeros_alfa.append(database.criar_senha(empresa_id=alfa.id, empresa=alfa.nome).numero)

    assert numeros_alfa == [1, 2, 3]
    assert numeros_beta == [1, 2]


def test_reiniciar_contador_empresa_afeta_somente_a_empresa_indicada(banco_teste):
    alfa = database.criar_empresa("Alfa")
    beta = database.criar_empresa("Beta")

    database.criar_senha(empresa_id=alfa.id, empresa=alfa.nome)
    database.criar_senha(empresa_id=alfa.id, empresa=alfa.nome)
    database.criar_senha(empresa_id=beta.id, empresa=beta.nome)
    database.criar_senha(empresa_id=beta.id, empresa=beta.nome)

    database.reiniciar_contador_empresa(alfa.id)

    proxima_alfa = database.criar_senha(empresa_id=alfa.id, empresa=alfa.nome)
    proxima_beta = database.criar_senha(empresa_id=beta.id, empresa=beta.nome)

    assert proxima_alfa.numero == 1  # reiniciado
    assert proxima_beta.numero == 3  # não afetado pelo reinício de Alfa


def test_senha_grava_empresa_id_estavel_e_nome_congelado(banco_teste):
    """
    ``empresa_id`` é a referência estável (usada para escopo/permissão);
    ``empresa`` é o nome congelado no momento da emissão (usado só para
    exibição/relatório). Renomear a empresa depois não deve alterar o
    nome já gravado nas senhas antigas.
    """
    empresa = database.criar_empresa("Nome Original")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    database.renomear_empresa(empresa.id, "Nome Novo")

    senha_recarregada = database.obter_senha_por_id(senha.id)
    assert senha_recarregada.empresa_id == empresa.id
    assert senha_recarregada.empresa == "Nome Original"
