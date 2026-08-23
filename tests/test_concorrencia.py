# -*- coding: utf-8 -*-
"""
test_concorrencia.py
=====================

Testes de concorrência REAL (threads de verdade, não mocks) para o
cenário descrito na revisão de performance do sistema: até 24 empresas
cadastradas + 6 pontos de emissão + 1 painel de TV, todos acessando o
SIGS ao mesmo tempo pela rede local — cobrindo especificamente a troca
do lock GLOBAL por locks ESCOPADOS por empresa em
``database.chamar_proxima``/``chamar_varias``/
``ocupar_proximo_guiche_empresa_disponivel`` (ver
``database._lock_para_chamar``).

O objetivo de cada teste é provar que a correção de performance (locks
mais finos) NÃO reintroduziu nenhuma condição de corrida: mesmo com
várias threads disputando a fila ao mesmo tempo, cada senha só pode ser
chamada por UMA thread, nunca duas, e chamadas de empresas diferentes
nunca "vazam" uma senha da outra.
"""

import threading
import time

import database


def test_chamadas_simultaneas_na_mesma_empresa_nunca_duplicam_senha(banco_teste):
    """
    Disputa clássica: N threads chamando ``chamar_proxima`` ao mesmo
    tempo, todas restritas à MESMA empresa, com só N senhas disponíveis
    na fila. Mesmo com o lock agora escopado por empresa (em vez de
    global), cada senha só pode sair para UMA thread — nunca duas
    threads recebendo o mesmo ``senha_id``, e nenhuma senha chamada mais
    de uma vez.
    """
    empresa = database.criar_empresa("Empresa Alfa")
    total_senhas = 20
    ids_criados = [
        database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome).id
        for _ in range(total_senhas)
    ]

    resultados = []
    lock_resultados = threading.Lock()
    barreira = threading.Barrier(total_senhas)

    def tentar_chamar(indice):
        barreira.wait()  # maximiza a chance de colisão real, todas largam juntas
        resultado = database.chamar_proxima(
            guiche=f"Mesa {indice:02d}", usuario=f"Recrutador {indice}", empresa_id=empresa.id
        )
        with lock_resultados:
            resultados.append(resultado)

    threads = [threading.Thread(target=tentar_chamar, args=(i,)) for i in range(total_senhas)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    senha_ids_chamados = [r["senha_id"] for r in resultados if r is not None]

    # Todas as N senhas foram chamadas, cada uma exatamente uma vez —
    # nenhuma duplicata, nenhuma perdida.
    assert len(senha_ids_chamados) == total_senhas
    assert sorted(senha_ids_chamados) == sorted(ids_criados)
    assert len(set(senha_ids_chamados)) == total_senhas  # sem duplicatas

    # Confirma no banco: todas 'Chamada', nenhuma ainda 'Emitida'.
    for senha_id in ids_criados:
        assert database.obter_senha_por_id(senha_id).status == "Chamada"


def test_chamadas_simultaneas_de_empresas_diferentes_nao_se_misturam(banco_teste):
    """
    O ponto central da correção de performance: chamadas concorrentes de
    empresas DIFERENTES não devem roubar a senha uma da outra, mesmo
    disputando ao mesmo tempo — e (ainda que este teste não meça tempo)
    o lock agora é por empresa, então uma NÃO deveria esperar a outra
    terminar para começar.
    """
    empresas = [database.criar_empresa(f"Empresa {i}") for i in range(6)]
    senhas_por_empresa = {
        empresa.id: [
            database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome).id for _ in range(10)
        ]
        for empresa in empresas
    }

    resultados_por_empresa = {empresa.id: [] for empresa in empresas}
    lock_resultados = threading.Lock()
    total_threads = sum(len(ids) for ids in senhas_por_empresa.values())
    barreira = threading.Barrier(total_threads)

    def tentar_chamar(empresa_id, indice):
        barreira.wait()
        resultado = database.chamar_proxima(
            guiche=f"Mesa {indice:02d} — Empresa {empresa_id}",
            usuario=f"Recrutador {empresa_id}-{indice}",
            empresa_id=empresa_id,
        )
        with lock_resultados:
            resultados_por_empresa[empresa_id].append(resultado)

    threads = []
    for empresa_id, ids_senhas in senhas_por_empresa.items():
        for indice in range(len(ids_senhas)):
            threads.append(threading.Thread(target=tentar_chamar, args=(empresa_id, indice)))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for empresa_id, ids_esperados in senhas_por_empresa.items():
        chamados = [r["senha_id"] for r in resultados_por_empresa[empresa_id] if r is not None]
        # Cada empresa recebeu EXATAMENTE as próprias senhas, nenhuma de outra.
        assert sorted(chamados) == sorted(ids_esperados)
        for r in resultados_por_empresa[empresa_id]:
            if r is not None:
                assert r["empresa"] == next(e.nome for e in empresas if e.id == empresa_id)


def test_fila_geral_e_fila_de_empresa_nao_disputam_a_mesma_senha(banco_teste):
    """
    Cenário do perfil "atendente" (fila GERAL, ``empresa_id=None``,
    mistura senhas de todas as empresas) correndo AO MESMO TEMPO que
    recrutadores (fila restrita à própria empresa). Antes da correção,
    ambos usavam o mesmo lock global, então nunca colidiam por
    construção; agora que o recrutador usa um lock só da própria
    empresa, é preciso confirmar que o atendente (que precisa considerar
    TODAS as empresas) ainda nunca rouba a MESMA senha que um recrutador
    está no processo de chamar, e que um recrutador nunca recebe a senha
    de outra empresa.

    Importante: atendente e recrutador disputam DELIBERADAMENTE o MESMO
    pool de senhas por design do sistema (a fila do atendente é a fila
    GERAL, com senhas de todas as empresas — ver
    ``obter_proxima_emitida``), então é normal e esperado que, sob
    disputa, um atendente "vença" e chame uma senha que um recrutador
    também poderia ter chamado (quem chega primeiro leva). Por isso o
    teste usa um suprimento de senhas bem maior que o número de threads
    disputando (ninguém deveria ficar sem senha por falta de estoque) —
    o que se está provando aqui é a AUSÊNCIA de disputa incorreta
    (chamada duplicada ou senha de empresa errada), não uma partilha
    exatamente igualitária entre os dois grupos.
    """
    empresa_alvo = database.criar_empresa("Empresa Alvo")
    outras_empresas = [database.criar_empresa(f"Outra {i}") for i in range(3)]

    QTD_ALVO = 40
    QTD_POR_OUTRA = 20
    RECRUTADORES = 10
    ATENDENTES = 10

    ids_empresa_alvo = [
        database.criar_senha(empresa_id=empresa_alvo.id, empresa=empresa_alvo.nome).id
        for _ in range(QTD_ALVO)
    ]
    for empresa in outras_empresas:
        for _ in range(QTD_POR_OUTRA):
            database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    resultados_recrutador = []
    resultados_atendente = []
    lock_resultados = threading.Lock()

    total_threads = RECRUTADORES + ATENDENTES
    barreira = threading.Barrier(total_threads)

    def chamar_recrutador(indice):
        barreira.wait()
        resultado = database.chamar_proxima(
            guiche=f"Mesa {indice:02d}", usuario=f"Recrutador {indice}", empresa_id=empresa_alvo.id
        )
        with lock_resultados:
            resultados_recrutador.append(resultado)

    def chamar_atendente(indice):
        barreira.wait()
        resultado = database.chamar_proxima(guiche=f"Guichê {indice:02d}", usuario=f"Atendente {indice}")
        with lock_resultados:
            resultados_atendente.append(resultado)

    threads = [threading.Thread(target=chamar_recrutador, args=(i,)) for i in range(RECRUTADORES)]
    threads += [threading.Thread(target=chamar_atendente, args=(i,)) for i in range(ATENDENTES)]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Com fartura de senhas (40 na empresa alvo, 60 espalhadas nas
    # outras, para só 20 threads disputando), NINGUÉM deveria ficar sem
    # chamar por falta de estoque — os dois grupos sempre têm senha
    # disponível no próprio escopo, mesmo perdendo alguma disputa
    # pontual para o outro grupo.
    assert all(r is not None for r in resultados_recrutador)
    assert all(r is not None for r in resultados_atendente)

    todos_chamados = [r["senha_id"] for r in resultados_recrutador]
    todos_chamados += [r["senha_id"] for r in resultados_atendente]

    # Nenhuma senha foi chamada duas vezes (nem pelo recrutador+atendente
    # disputando a mesma, nem por duas threads do mesmo grupo).
    assert len(todos_chamados) == len(set(todos_chamados)) == total_threads

    # Todo recrutador da empresa alvo recebeu uma senha DAQUELA empresa
    # — nunca uma senha de "Outra 0/1/2" vazando por engano.
    ids_alvo = set(ids_empresa_alvo)
    for resultado in resultados_recrutador:
        assert resultado["senha_id"] in ids_alvo
        assert resultado["empresa"] == empresa_alvo.nome


def test_chamada_de_uma_empresa_nao_espera_lock_de_outra_empresa(banco_teste):
    """
    Prova de que a correção realmente ELIMINOU a serialização
    desnecessária entre empresas diferentes (não só a correção, mas o
    ganho de performance em si): mantém o lock da Empresa A ocupado por
    um tempo "longo" (simulando uma operação lenta) e mede quanto tempo
    uma chamada da Empresa B leva para completar ao mesmo tempo — antes
    da correção (lock global), a chamada da Empresa B ficaria PRESA
    esperando o lock da Empresa A ser liberado; agora deve retornar
    quase instantaneamente, sem relação com o tempo que a Empresa A
    ainda está segurando o próprio lock.
    """
    empresa_a = database.criar_empresa("Empresa A")
    empresa_b = database.criar_empresa("Empresa B")
    database.criar_senha(empresa_id=empresa_a.id, empresa=empresa_a.nome)
    database.criar_senha(empresa_id=empresa_b.id, empresa=empresa_b.nome)

    TEMPO_SEGURANDO_LOCK = 0.6  # segundos — bem maior que qualquer chamada real

    def segurar_lock_da_empresa_a():
        with database._lock_da_empresa(empresa_a.id):
            time.sleep(TEMPO_SEGURANDO_LOCK)

    thread_segurando = threading.Thread(target=segurar_lock_da_empresa_a)
    thread_segurando.start()
    time.sleep(0.1)  # garante que a thread acima já está segurando o lock

    inicio = time.monotonic()
    resultado_b = database.chamar_proxima(guiche="Mesa 01", usuario="Recrutador B", empresa_id=empresa_b.id)
    duracao = time.monotonic() - inicio

    thread_segurando.join()

    assert resultado_b is not None
    # Bem menor que o tempo que a Empresa A ainda está segurando o lock
    # dela — prova de que a Empresa B não esperou por ele.
    assert duracao < (TEMPO_SEGURANDO_LOCK / 2)
