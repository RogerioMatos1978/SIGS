# -*- coding: utf-8 -*-
"""
test_fila.py
============

Testa a busca (por número da senha ou nome da pessoa) e a paginação da
Fila de Espera (``database.listar_fila_atual``/``database.contar_aguardando``,
usadas por ``app.py:api_fila``). Antes desta funcionalidade, a fila
sempre trazia só as 20 senhas mais antigas — qualquer senha além dessas
era inacessível, mesmo sabendo exatamente o número ou o nome procurado.
"""

import database


def test_busca_por_numero_da_senha(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    alvo = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    # Busca tanto pelo número "cru" quanto formatado com zeros à esquerda
    # (como aparece na tela) devem encontrar a mesma senha.
    resultado_cru = database.listar_fila_atual(busca="1")
    resultado_formatado = database.listar_fila_atual(busca="001")

    assert [linha["id"] for linha in resultado_cru] == [alvo.id]
    assert [linha["id"] for linha in resultado_formatado] == [alvo.id]


def test_busca_por_nome_da_pessoa_parcial_e_sem_case(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    alvo = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome, nome_pessoa="Maria Silva")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome, nome_pessoa="João Souza")

    resultado = database.listar_fila_atual(busca="maria")
    assert [linha["id"] for linha in resultado] == [alvo.id]

    resultado_parcial = database.listar_fila_atual(busca="silv")
    assert [linha["id"] for linha in resultado_parcial] == [alvo.id]


def test_busca_sem_correspondencia_retorna_lista_vazia(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome, nome_pessoa="Maria")

    assert database.listar_fila_atual(busca="Nome Que Nao Existe") == []
    assert database.contar_aguardando(busca="Nome Que Nao Existe") == 0


def test_paginacao_percorre_toda_a_fila(banco_teste):
    """
    Regressão principal: antes da paginação, criar mais de 20 senhas
    deixava as excedentes permanentemente fora de alcance (a função só
    trazia as 20 mais antigas). Agora, todas continuam acessíveis,
    navegando pelas páginas.
    """
    empresa = database.criar_empresa("Empresa Alfa")
    senhas = [database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome) for _ in range(25)]

    assert database.contar_aguardando() == 25

    pagina_1 = database.listar_fila_atual(pagina=1, por_pagina=20)
    pagina_2 = database.listar_fila_atual(pagina=2, por_pagina=20)

    assert len(pagina_1) == 20
    assert len(pagina_2) == 5

    ids_pagina_1 = [linha["id"] for linha in pagina_1]
    ids_pagina_2 = [linha["id"] for linha in pagina_2]

    # Nenhuma repetição entre páginas, e juntas cobrem as 25 senhas
    # criadas, na ordem de chegada (FIFO).
    assert ids_pagina_1 == [senha.id for senha in senhas[:20]]
    assert ids_pagina_2 == [senha.id for senha in senhas[20:]]
    assert set(ids_pagina_1).isdisjoint(ids_pagina_2)


def test_paginacao_combinada_com_busca(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    for indice in range(3):
        database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome, nome_pessoa=f"Candidato {indice}")
    # Ruído: senhas que não devem aparecer na busca por "Candidato".
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome, nome_pessoa="Outra Pessoa")

    total_filtrado = database.contar_aguardando(busca="Candidato")
    assert total_filtrado == 3

    pagina_1 = database.listar_fila_atual(busca="Candidato", pagina=1, por_pagina=2)
    pagina_2 = database.listar_fila_atual(busca="Candidato", pagina=2, por_pagina=2)

    assert len(pagina_1) == 2
    assert len(pagina_2) == 1
    assert all("Candidato" in (linha["nome_pessoa"] or "") for linha in pagina_1 + pagina_2)


def test_busca_e_paginacao_respeitam_escopo_por_empresa(banco_teste):
    """O filtro de empresa (usado pelo perfil recrutador) continua
    valendo junto com busca e paginação — um recrutador nunca deve achar
    (nem contar) senhas de outra empresa."""
    empresa_a = database.criar_empresa("Empresa A")
    empresa_b = database.criar_empresa("Empresa B")
    alvo_a = database.criar_senha(empresa_id=empresa_a.id, empresa=empresa_a.nome, nome_pessoa="Fulano")
    database.criar_senha(empresa_id=empresa_b.id, empresa=empresa_b.nome, nome_pessoa="Fulano")

    resultado = database.listar_fila_atual(empresa_id=empresa_a.id, busca="Fulano")
    assert [linha["id"] for linha in resultado] == [alvo_a.id]
    assert database.contar_aguardando(empresa_id=empresa_a.id, busca="Fulano") == 1
