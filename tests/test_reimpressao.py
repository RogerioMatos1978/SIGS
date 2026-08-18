# -*- coding: utf-8 -*-
"""
test_reimpressao.py
====================

Testa a condição de negócio por trás do botão "Reimprimir" (perfil
Emissor, ver app.py:api_reimprimir): só é permitido reimprimir uma senha
enquanto ``senha.status == 'Emitida'`` — nunca depois de chamada,
finalizada ou cancelada.

``api_reimprimir`` em si não é chamada aqui (ela depende de Flask/sessão
e de pywin32 para a impressão física, indisponíveis neste ambiente de
teste — ver tests/test_config_validacao.py para o padrão usado nos casos
que precisam importar app.py). Este módulo garante, via banco real, que
os dados nos quais aquela rota se baseia (``database.obter_senha_por_id``
e ``models.StatusSenha``) mudam exatamente como o guard da rota espera em
cada transição de status — é a parte que realmente importa testar aqui.
"""

import database
from models import StatusSenha


def test_senha_recem_emitida_tem_status_emitida(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    recarregada = database.obter_senha_por_id(senha.id)
    assert recarregada.status == StatusSenha.EMITIDA


def test_senha_chamada_deixa_de_ter_status_emitida(banco_teste):
    """Depois de chamada, a rota de reimpressão deve rejeitar (status
    != 'Emitida')."""
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.chamar_proxima(guiche="Mesa 01", usuario="Recepção")

    recarregada = database.obter_senha_por_id(senha.id)
    assert recarregada.status == StatusSenha.CHAMADA
    assert recarregada.status != StatusSenha.EMITIDA


def test_senha_finalizada_deixa_de_ter_status_emitida(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.chamar_proxima(guiche="Mesa 01", usuario="Recepção")
    database.finalizar_senha(senha.id)

    recarregada = database.obter_senha_por_id(senha.id)
    assert recarregada.status == StatusSenha.FINALIZADA
    assert recarregada.status != StatusSenha.EMITIDA


def test_senha_cancelada_deixa_de_ter_status_emitida(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.cancelar_senha(senha.id)

    recarregada = database.obter_senha_por_id(senha.id)
    assert recarregada.status == StatusSenha.CANCELADA
    assert recarregada.status != StatusSenha.EMITIDA


def test_fila_de_espera_so_lista_senhas_emitidas(banco_teste):
    """
    A Fila de Espera (de onde o botão Reimprimir é acionado) já filtra
    por status='Emitida' — uma senha chamada, finalizada ou cancelada
    desaparece da lista, então nunca deveria nem aparecer um botão
    Reimprimir para elas na interface (o servidor ainda valida de novo,
    ver test_senha_*_deixa_de_ter_status_emitida acima).
    """
    empresa = database.criar_empresa("Empresa Alfa")
    # chamar_proxima segue FIFO (chama sempre a mais ANTIGA 'Emitida'), por
    # isso a ordem de criação aqui importa: a primeira criada é a que será
    # chamada a seguir.
    para_chamar = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    aguardando = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    para_cancelar = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    database.chamar_proxima(guiche="Mesa 01", usuario="Recepção")  # chama 'para_chamar' (FIFO)
    database.cancelar_senha(para_cancelar.id)

    fila = database.listar_fila_atual()
    ids_na_fila = {linha["id"] for linha in fila}

    assert aguardando.id in ids_na_fila
    assert para_chamar.id not in ids_na_fila
    assert para_cancelar.id not in ids_na_fila
