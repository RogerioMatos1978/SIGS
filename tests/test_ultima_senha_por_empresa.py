# -*- coding: utf-8 -*-
"""
test_ultima_senha_por_empresa.py
=================================

Testa ``database.listar_ultima_senha_por_empresa``, usada pelo card
"Última Senha por Empresa" da tela principal do perfil Emissor (ver
templates/index.html/app.py:api_fila), exibido acima da Fila de Espera.
"""

import database


def test_empresa_sem_senha_aparece_com_campos_nulos(banco_teste):
    database.criar_empresa("Empresa Sem Senha")

    lista = database.listar_ultima_senha_por_empresa()
    por_nome = {item["empresa_nome"]: item for item in lista}

    assert "Empresa Sem Senha" in por_nome
    assert por_nome["Empresa Sem Senha"]["numero"] is None
    assert por_nome["Empresa Sem Senha"]["nome_pessoa"] is None
    assert por_nome["Empresa Sem Senha"]["data_hora"] is None
    assert por_nome["Empresa Sem Senha"]["chamada_numero"] is None
    assert por_nome["Empresa Sem Senha"]["chamada_nome_pessoa"] is None
    assert por_nome["Empresa Sem Senha"]["chamada_hora"] is None


def test_mostra_sempre_a_senha_mais_recente(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome, nome_pessoa="Primeira")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome, nome_pessoa="Segunda")
    ultima = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome, nome_pessoa="Terceira")

    lista = database.listar_ultima_senha_por_empresa()
    item = next(i for i in lista if i["empresa_nome"] == "Empresa Alfa")

    assert item["numero"] == ultima.numero
    assert item["nome_pessoa"] == "Terceira"


def test_atualiza_apos_nova_empresa_cadastrada(banco_teste):
    """Regressão do pedido original: uma empresa recém-cadastrada já deve
    aparecer na lista (com última senha nula) sem precisar de nenhuma
    ação adicional — a consulta é sempre feita "ao vivo"."""
    antes = database.listar_ultima_senha_por_empresa()
    nomes_antes = {item["empresa_nome"] for item in antes}
    assert "Empresa Nova" not in nomes_antes

    database.criar_empresa("Empresa Nova")

    depois = database.listar_ultima_senha_por_empresa()
    nomes_depois = {item["empresa_nome"] for item in depois}
    assert "Empresa Nova" in nomes_depois


def test_opcoes_fixas_aparecem_com_ultima_senha_mesmo_finalizada(banco_teste):
    """As duas opções fixas nascem já 'Finalizada' — mesmo assim devem
    aparecer aqui (diferente da Fila de Espera/Painéis, que as
    escondem), pois o objetivo deste card é mostrar até onde a
    numeração de cada empresa chegou, não o estado da fila."""
    fixa = None
    for linha in database.listar_empresas():
        if linha["nome"] == database.NOMES_EMPRESAS_FIXAS[0]:
            fixa = linha
    senha = database.criar_senha(empresa_id=fixa["id"], empresa=fixa["nome"], finalizar_imediatamente=True)

    lista = database.listar_ultima_senha_por_empresa()
    item = next(i for i in lista if i["empresa_nome"] == database.NOMES_EMPRESAS_FIXAS[0])

    assert item["empresa_fixa"] is True
    assert item["numero"] == senha.numero
    assert item["status"] == "Finalizada"


def test_empresa_desativada_some_da_lista_por_padrao(banco_teste):
    empresa = database.criar_empresa("Empresa Inativa")
    database.definir_status_empresa(empresa.id, False)

    lista_ativas = database.listar_ultima_senha_por_empresa(somente_ativas=True)
    lista_todas = database.listar_ultima_senha_por_empresa(somente_ativas=False)

    assert "Empresa Inativa" not in {item["empresa_nome"] for item in lista_ativas}
    assert "Empresa Inativa" in {item["empresa_nome"] for item in lista_todas}


def test_ultima_chamada_fica_nula_enquanto_ninguem_foi_chamado(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    item = next(
        i for i in database.listar_ultima_senha_por_empresa() if i["empresa_nome"] == "Empresa Alfa"
    )
    # Já tem última EMITIDA, mas nenhuma chamada ainda.
    assert item["numero"] is not None
    assert item["chamada_numero"] is None


def test_ultima_chamada_reflete_a_senha_realmente_chamada(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    chamada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome, nome_pessoa="Fulano")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome, nome_pessoa="Beltrano")

    database.chamar_proxima(guiche="01", usuario="Teste", empresa_id=empresa.id)

    item = next(
        i for i in database.listar_ultima_senha_por_empresa() if i["empresa_nome"] == "Empresa Alfa"
    )
    # A última EMITIDA é a segunda senha ("Beltrano"), mas a última
    # CHAMADA é a primeira ("Fulano", chamada pela fila FIFO).
    assert item["nome_pessoa"] == "Beltrano"
    assert item["chamada_numero"] == chamada.numero
    assert item["chamada_nome_pessoa"] == "Fulano"
    assert item["chamada_hora"] is not None


def test_opcoes_fixas_ja_aparecem_como_chamadas(banco_teste):
    """As duas opções fixas nascem com hora_chamada preenchida (mesmo
    critério de database.contar_chamadas_realizadas_periodo) — devem
    aparecer como "última chamada" também, sem precisar de nenhum
    guichê de verdade."""
    fixa = next(
        l for l in database.listar_empresas() if l["nome"] == database.NOMES_EMPRESAS_FIXAS[1]
    )
    senha = database.criar_senha(empresa_id=fixa["id"], empresa=fixa["nome"], finalizar_imediatamente=True)

    item = next(
        i for i in database.listar_ultima_senha_por_empresa()
        if i["empresa_nome"] == database.NOMES_EMPRESAS_FIXAS[1]
    )
    assert item["chamada_numero"] == senha.numero


def test_ordem_segue_fixas_primeiro_depois_alfabetica(banco_teste):
    database.criar_empresa("Zebra")
    database.criar_empresa("Abelha")

    nomes = [item["empresa_nome"] for item in database.listar_ultima_senha_por_empresa()]

    # As duas opções fixas vêm primeiro, depois as comuns em ordem alfabética.
    assert nomes[:2] == list(database.NOMES_EMPRESAS_FIXAS)
    assert nomes[2:] == ["Abelha", "Zebra"]
