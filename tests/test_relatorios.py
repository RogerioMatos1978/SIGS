# -*- coding: utf-8 -*-
"""
test_relatorios.py
===================

Testa o invariante de negócio do "Resumo do Período" (tela de
Relatórios): "chamadas realizadas" nunca pode ser maior que "senhas
emitidas" (ver database.contar_chamadas_realizadas_periodo). Antes desta
correção, cada clique em "Repetir Chamada" inflava a contagem de
chamadas (um novo evento por repetição, para a MESMA senha), podendo
facilmente ultrapassar o total de senhas emitidas.
"""

import database


def test_repetir_chamada_nao_infla_total_de_chamadas(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    database.chamar_proxima(guiche="Guichê 01", usuario="Atendente")
    # Repete a chamada várias vezes — cada repetição grava um NOVO evento
    # em eventos_chamada para a MESMA senha.
    for _ in range(5):
        database.repetir_ultima_chamada(guiche="Guichê 01")

    total_emitidas = len(database.listar_senhas_periodo())
    total_chamadas = database.contar_chamadas_realizadas_periodo()

    assert total_emitidas == 1
    # Sem a correção, total_chamadas seria 6 (1 chamada original + 5
    # repetições) — mais que o total de senhas emitidas.
    assert total_chamadas == 1
    assert total_chamadas <= total_emitidas


def test_invariante_chamadas_menor_ou_igual_emitidas_com_varias_senhas(banco_teste):
    """
    Cenário mais realista: várias senhas emitidas, algumas chamadas (com
    repetições em quantidades diferentes), outras ainda esperando. O
    invariante "chamadas <= emitidas" precisa valer sempre, qualquer que
    seja a combinação.
    """
    empresa = database.criar_empresa("Empresa Alfa")
    senhas = [database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome) for _ in range(4)]

    # Chama e repete a primeira senha 3 vezes.
    database.chamar_proxima(guiche="Guichê 01", usuario="Atendente")
    database.repetir_ultima_chamada(guiche="Guichê 01")
    database.repetir_ultima_chamada(guiche="Guichê 01")
    database.repetir_ultima_chamada(guiche="Guichê 01")

    # Chama a segunda senha, sem repetir.
    database.chamar_proxima(guiche="Guichê 01", usuario="Atendente")

    # As outras duas continuam esperando (nunca chamadas).

    total_emitidas = len(database.listar_senhas_periodo())
    total_chamadas = database.contar_chamadas_realizadas_periodo()

    assert total_emitidas == 4
    assert total_chamadas == 2  # só 2 das 4 senhas foram chamadas
    assert total_chamadas <= total_emitidas


def test_contar_chamadas_realizadas_respeita_filtro_de_empresa(banco_teste):
    empresa_a = database.criar_empresa("Empresa A")
    empresa_b = database.criar_empresa("Empresa B")
    database.criar_senha(empresa_id=empresa_a.id, empresa=empresa_a.nome)
    database.criar_senha(empresa_id=empresa_b.id, empresa=empresa_b.nome)

    database.chamar_proxima(guiche="Mesa 01 — Empresa A", usuario="Recrutador A", empresa_id=empresa_a.id)
    database.repetir_ultima_chamada(guiche="Mesa 01 — Empresa A")
    database.repetir_ultima_chamada(guiche="Mesa 01 — Empresa A")

    assert database.contar_chamadas_realizadas_periodo(empresa_id=empresa_a.id) == 1
    assert database.contar_chamadas_realizadas_periodo(empresa_id=empresa_b.id) == 0


def test_contar_chamadas_realizadas_usa_data_de_emissao_no_filtro_periodo(banco_teste):
    """
    Regressão (revisão geral do sistema): o filtro de período de
    ``contar_chamadas_realizadas_periodo`` usava ``date(hora_chamada)``,
    diferente de TODAS as demais consultas de Relatórios (que usam
    ``date(data_hora)``, a data de EMISSÃO). Uma senha emitida perto da
    virada do dia e só chamada no dia seguinte contava em "Atendidas" de
    um dia diferente de "Emitidas", quebrando o invariante básico do
    resumo (atendidas nunca maior que emitidas NAQUELE período). Agora
    as duas colunas usam a mesma data de referência (emissão).
    """
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.chamar_proxima(guiche="Guichê 01", usuario="Atendente")

    # Simula: emitida em 10/01, mas só chamada em 11/01 (virada do dia).
    with database.get_connection() as conexao:
        conexao.execute(
            "UPDATE senhas SET data_hora = ?, hora_chamada = ? WHERE id = ?",
            ("2026-01-10 23:58:00", "2026-01-11 00:02:00", senha.id),
        )
        conexao.commit()

    # No período do dia de EMISSÃO (10/01), a senha conta como atendida
    # — mesmo critério de data usado por "Emitidas" nesse mesmo período.
    assert database.contar_chamadas_realizadas_periodo(inicio="2026-01-10", fim="2026-01-10") == 1
    assert len(database.listar_senhas_periodo(inicio="2026-01-10", fim="2026-01-10")) == 1

    # No período do dia da CHAMADA (11/01), a senha NÃO deveria aparecer
    # como "emitida" nem "atendida" — ela pertence ao dia anterior.
    assert database.contar_chamadas_realizadas_periodo(inicio="2026-01-11", fim="2026-01-11") == 0


def test_listar_contagem_por_empresa_inclui_coluna_atendidas(banco_teste):
    """
    A coluna "Senhas Atendidas" da tabela "Senhas por Empresa" (tela de
    Relatórios do Administrador) usa o mesmo critério de
    ``hora_chamada IS NOT NULL`` de ``contar_chamadas_realizadas_periodo``
    — nunca maior que "total" (senhas emitidas) da mesma linha, e imune
    à inflação por repetição de chamada.
    """
    empresa_a = database.criar_empresa("Empresa A")
    empresa_b = database.criar_empresa("Empresa B")

    database.criar_senha(empresa_id=empresa_a.id, empresa=empresa_a.nome)  # será chamada
    database.criar_senha(empresa_id=empresa_a.id, empresa=empresa_a.nome)  # continua esperando
    database.criar_senha(empresa_id=empresa_b.id, empresa=empresa_b.nome)  # nunca chamada

    database.chamar_proxima(guiche="Mesa A", usuario="Recrutador A", empresa_id=empresa_a.id)
    database.repetir_ultima_chamada(guiche="Mesa A")
    database.repetir_ultima_chamada(guiche="Mesa A")

    contagem = database.listar_contagem_por_empresa()
    por_nome = {item["empresa"]: item for item in contagem}

    assert por_nome["Empresa A"]["total"] == 2
    # Só 1 das 2 senhas da Empresa A foi chamada, mesmo com repetições.
    assert por_nome["Empresa A"]["atendidas"] == 1
    assert por_nome["Empresa A"]["atendidas"] <= por_nome["Empresa A"]["total"]

    assert por_nome["Empresa B"]["total"] == 1
    assert por_nome["Empresa B"]["atendidas"] == 0


def test_listar_contagem_por_empresa_conta_opcoes_fixas_como_atendidas(banco_teste):
    fixa = next(l for l in database.listar_empresas() if l["nome"] == database.NOMES_EMPRESAS_FIXAS[0])
    database.criar_senha(empresa_id=fixa["id"], empresa=fixa["nome"], finalizar_imediatamente=True)
    database.criar_senha(empresa_id=fixa["id"], empresa=fixa["nome"], finalizar_imediatamente=True)

    contagem = database.listar_contagem_por_empresa()
    item = next(i for i in contagem if i["empresa"] == database.NOMES_EMPRESAS_FIXAS[0])

    assert item["total"] == 2
    assert item["atendidas"] == 2


def test_listar_contagem_por_empresa_exclui_canceladas_do_total(banco_teste):
    """
    Regressão (revisão geral do sistema): a coluna "Senhas Emitidas" da
    tabela "Senhas por Empresa" (tela de Relatórios) contava TODAS as
    senhas do período, inclusive as Canceladas — diferente do "Total de
    Senhas Emitidas" do Painel Geral, que já excluía Canceladas desde a
    v2.15.0. Isso fazia a soma da coluna por empresa não bater com o
    card de resumo sempre que havia cancelamentos no período. Agora as
    duas contagens usam o mesmo critério (Canceladas fora do total).
    """
    empresa = database.criar_empresa("Empresa Alfa")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)  # continua emitida
    cancelada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.cancelar_senha(cancelada.id)

    contagem = database.listar_contagem_por_empresa()
    item = next(i for i in contagem if i["empresa"] == "Empresa Alfa")

    # 2 senhas criadas, mas só 1 conta como "emitida" (a cancelada sai).
    assert item["total"] == 1


def test_resumo_relatorios_total_emitidas_exclui_canceladas_via_http(banco_teste):
    """
    Teste de ponta a ponta: o card "Senhas Emitidas" do resumo (rota
    ``/api/relatorios/resumo``) e a soma da coluna "Senhas Emitidas" da
    tabela "Senhas por Empresa" (mesma resposta) precisam bater — os
    dois excluindo Canceladas.
    """
    import auth
    import app as app_modulo

    database.criar_usuario("Admin Teste", "admin_teste", auth.gerar_hash_senha("SenhaForte123"), perfil="admin")
    empresa = database.criar_empresa("Empresa Alfa")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    cancelada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.cancelar_senha(cancelada.id)

    cliente = app_modulo.app.test_client()
    cliente.post("/login", data={"login": "admin_teste", "senha": "SenhaForte123"})

    resposta = cliente.get("/api/relatorios/resumo")
    corpo = resposta.get_json()

    assert corpo["sucesso"] is True
    assert corpo["total_emitidas"] == 2
    soma_por_empresa = sum(item["total"] for item in corpo["por_empresa"])
    assert soma_por_empresa == corpo["total_emitidas"]

    cliente.post("/logout")
