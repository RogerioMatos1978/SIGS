# -*- coding: utf-8 -*-
"""
test_emitidas_hoje.py
======================

Testa ``database.contar_emitidas_hoje``, usada pelo contador "Emitidas
hoje" da tela principal (ver app.py:api_fila e templates/index.html).

Contexto do bug corrigido: nem "Fila de Espera" (``contar_aguardando``,
só conta status 'Emitida') nem os Painéis públicos (que propositalmente
ocultam 'Finalizada'/'Cancelada') davam ao perfil Emissor nenhuma
confirmação visível de que uma senha das duas opções fixas ("Criar
Currículos"/"Imprimir Currículos" — nascem já 'Finalizada', ver
``database.NOMES_EMPRESAS_FIXAS``/``criar_senha``) tinha sido
efetivamente registrada, mesmo estando corretamente contabilizada nos
Relatórios (tela que esse perfil não acessa). Esta função conta por
``date(data_hora)``, sem filtro de status, justamente para cobrir esse
caso.
"""

import database


def _obter_fixa(nome):
    for linha in database.listar_empresas():
        if linha["nome"] == nome:
            return linha
    return None


def test_conta_senhas_de_qualquer_status_emitidas_hoje(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    finalizada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    cancelada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    database.chamar_proxima(guiche="01", usuario="Teste", empresa_id=empresa.id)
    database.finalizar_senha(finalizada.id)
    database.cancelar_senha(cancelada.id)

    # As quatro senhas foram criadas hoje, independente do status atual.
    assert database.contar_emitidas_hoje(empresa_id=empresa.id) == 4


def test_opcoes_fixas_contam_como_emitidas_hoje(banco_teste):
    """
    O caso central do bug relatado: uma senha "realizada sem fila"
    (opções fixas) nasce direto como 'Finalizada' — nunca aparece na
    Fila de Espera nem nos Painéis públicos — mas DEVE contar aqui,
    dando ao Emissor confirmação visível de que a emissão foi
    registrada.
    """
    criar = _obter_fixa(database.NOMES_EMPRESAS_FIXAS[0])
    imprimir = _obter_fixa(database.NOMES_EMPRESAS_FIXAS[1])
    database.criar_senha(empresa_id=criar["id"], empresa=criar["nome"], finalizar_imediatamente=True)
    database.criar_senha(empresa_id=criar["id"], empresa=criar["nome"], finalizar_imediatamente=True)
    database.criar_senha(empresa_id=imprimir["id"], empresa=imprimir["nome"], finalizar_imediatamente=True)

    # Mesmo não aparecendo na fila nem no painel...
    assert database.contar_aguardando() == 0

    # ...o total geral de hoje reflete as três emissões.
    assert database.contar_emitidas_hoje() == 3
    assert database.contar_emitidas_hoje(empresa_id=criar["id"]) == 2
    assert database.contar_emitidas_hoje(empresa_id=imprimir["id"]) == 1


def test_escopo_por_empresa_nao_vaza_para_outras(banco_teste):
    empresa_a = database.criar_empresa("Empresa A")
    empresa_b = database.criar_empresa("Empresa B")
    database.criar_senha(empresa_id=empresa_a.id, empresa=empresa_a.nome)
    database.criar_senha(empresa_id=empresa_b.id, empresa=empresa_b.nome)
    database.criar_senha(empresa_id=empresa_b.id, empresa=empresa_b.nome)

    assert database.contar_emitidas_hoje(empresa_id=empresa_a.id) == 1
    assert database.contar_emitidas_hoje(empresa_id=empresa_b.id) == 2
    assert database.contar_emitidas_hoje() == 3


def test_sem_senhas_retorna_zero(banco_teste):
    assert database.contar_emitidas_hoje() == 0
