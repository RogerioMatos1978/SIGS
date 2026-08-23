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


def test_obter_chamada_atual_some_depois_de_finalizada_sem_nova_chamada(banco_teste):
    """
    Regressão: uma senha já 'Finalizada' não deve mais aparecer como
    "chamada atual" nos painéis públicos (pedido explícito do usuário) —
    mesmo continuando sendo, tecnicamente, o evento mais recente em
    ``eventos_chamada`` (log que nunca é reescrito). Sem nenhuma NOVA
    chamada acontecendo depois da finalização, ``obter_chamada_atual``
    deve retornar ``None`` (não deve "ressuscitar" um lote mais antigo).
    """
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    database.chamar_proxima(guiche="Mesa 01", usuario="Recepção", empresa_id=empresa.id)
    assert database.obter_chamada_atual(empresa_id=empresa.id) is not None

    database.finalizar_senha(senha.id)

    assert database.obter_chamada_atual(empresa_id=empresa.id) is None
    assert database.obter_chamada_atual() is None  # também some do painel geral


def test_obter_chamada_atual_lote_parcialmente_finalizado_mostra_so_as_ativas(banco_teste):
    """
    Um lote de "Chamar Selecionadas" com 2 senhas, onde uma já foi
    finalizada e a outra continua em atendimento: o destaque deve
    mostrar SÓ a que ainda está ativa, não as duas.
    """
    empresa = database.criar_empresa("Empresa Alfa")
    s1 = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    s2 = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    database.chamar_varias(senha_ids=[s1.id, s2.id], guiche="Mesa 01", usuario="Recepção")
    database.finalizar_senha(s1.id)

    atual = database.obter_chamada_atual(empresa_id=empresa.id)
    assert atual is not None
    numeros = {evento["numero"] for evento in atual["senhas"]}
    assert numeros == {s2.numero}


def test_obter_chamada_atual_nao_recua_para_lote_antigo_apos_finalizar_o_ultimo(banco_teste):
    """
    Depois que o lote mais recente é totalmente finalizado, o painel não
    deve "voltar" a mostrar um lote mais antigo (mesmo que ele ainda
    tenha, teoricamente, alguma senha 'Chamada' pendurada) — um destaque
    velho seria tão confuso quanto nenhum destaque. A função deve
    retornar ``None`` nesse caso.
    """
    empresa = database.criar_empresa("Empresa Alfa")
    antiga = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    recente = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    database.chamar_proxima(guiche="Mesa 01", usuario="Rec 1", empresa_id=empresa.id)  # chama "antiga"
    database.chamar_proxima(guiche="Mesa 02", usuario="Rec 2", empresa_id=empresa.id)  # chama "recente"
    database.finalizar_senha(recente.id)

    # "antiga" continua 'Chamada' (nunca foi finalizada), mas como não é
    # mais o lote mais recente, o painel não deve voltar a exibi-la.
    assert database.obter_senha_por_id(antiga.id).status == "Chamada"
    assert database.obter_chamada_atual(empresa_id=empresa.id) is None


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
