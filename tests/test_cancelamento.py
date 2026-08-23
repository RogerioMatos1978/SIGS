# -*- coding: utf-8 -*-
"""
test_cancelamento.py
=====================

Testa a regra de que só é possível cancelar uma senha que AINDA esteja
aguardando na fila (status 'Emitida') — ver database.cancelar_senha.

Regressão corrigida na revisão geral do sistema: antes, ``cancelar_senha``
não tinha nenhum guarda de status e cancelava uma senha em QUALQUER
estado, inclusive uma que já tivesse sido chamada. Isso criava uma
inconsistência entre os números do Painel Geral: a senha saía de "Total
de Senhas Emitidas" (que exclui Canceladas), mas continuava contando em
"Total de Atendimentos Realizados" (baseado só em ``hora_chamada`` ter
sido preenchida, sem checar o status atual).
"""

import database
from models import StatusSenha


def test_cancelar_senha_aguardando_funciona_normalmente(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    assert database.cancelar_senha(senha.id) is True
    assert database.obter_senha_por_id(senha.id).status == StatusSenha.CANCELADA


def test_cancelar_senha_ja_chamada_e_rejeitado(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.chamar_proxima(guiche="Guichê 01", usuario="Atendente")

    assert database.cancelar_senha(senha.id) is False
    # Continua 'Chamada' — não virou 'Cancelada' por engano.
    assert database.obter_senha_por_id(senha.id).status == StatusSenha.CHAMADA


def test_cancelar_senha_ja_finalizada_e_rejeitado(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.chamar_proxima(guiche="Guichê 01", usuario="Atendente")
    database.finalizar_senha(senha.id)

    assert database.cancelar_senha(senha.id) is False
    assert database.obter_senha_por_id(senha.id).status == StatusSenha.FINALIZADA


def test_cancelar_senha_ja_cancelada_e_rejeitado_segunda_vez(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    assert database.cancelar_senha(senha.id) is True
    assert database.cancelar_senha(senha.id) is False


def test_cancelar_apos_chamada_nao_gera_inconsistencia_atendidas_vs_emitidas(banco_teste):
    """
    O cenário exato relatado: cancelar uma senha já chamada não pode
    mais acontecer — então "Total de Atendimentos Realizados" nunca
    conta uma senha que "sumiu" de "Total de Senhas Emitidas".
    """
    empresa = database.criar_empresa("Empresa Alfa")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    chamada = database.chamar_proxima(guiche="Guichê 01", usuario="Atendente")

    # Tentativa de cancelar uma senha já chamada é rejeitada...
    assert database.cancelar_senha(chamada["senha_id"]) is False

    resumo = database.resumo_geral_senhas()
    total_emitidas = resumo["total_emitidas"] - resumo["total_canceladas"]
    total_atendidas = database.contar_chamadas_realizadas_periodo()

    # ...então o invariante básico continua valendo.
    assert total_atendidas <= total_emitidas


def test_api_cancelar_senha_ja_chamada_retorna_erro_via_http(banco_teste):
    import auth
    import app as app_modulo

    database.criar_usuario("Admin Teste", "admin_teste", auth.gerar_hash_senha("SenhaForte123"), perfil="admin")
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.chamar_proxima(guiche="Guichê 01", usuario="Atendente")

    cliente = app_modulo.app.test_client()
    cliente.post("/login", data={"login": "admin_teste", "senha": "SenhaForte123"})

    resposta = cliente.post(f"/api/senha/{senha.id}/cancelar")
    corpo = resposta.get_json()

    assert resposta.status_code == 404
    assert corpo["sucesso"] is False
    assert database.obter_senha_por_id(senha.id).status == StatusSenha.CHAMADA
