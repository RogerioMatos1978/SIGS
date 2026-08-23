# -*- coding: utf-8 -*-
"""
test_chamar_varias.py
======================

Testa a chamada de VÁRIAS senhas de uma vez ("Chamar Selecionadas" na
Fila de Espera do recrutador — ver database.chamar_varias) e a leitura
do lote inteiro por ``obter_chamada_atual`` (usada pelos painéis
públicos para exibir a sequência chamada).

Cobre especificamente:
    - todas as senhas de uma chamada em lote compartilham o mesmo
      ``lote_chamada`` e ficam com status 'Chamada';
    - validação "tudo ou nada" (um id inválido/já chamado/de outra
      empresa cancela a operação inteira, sem alterar nada no banco);
    - duplicatas na lista de ids são ignoradas (cada senha chamada uma
      única vez);
    - ``obter_chamada_atual`` retorna a lista completa do lote mais
      recente (campo ``senhas``);
    - ISOLAMENTO entre empresas: o lote de uma empresa nunca aparece no
      painel de outra, mesmo com chamadas em lote intercaladas
      simulando duas empresas chamando "ao mesmo tempo" — este é o
      requisito explícito do usuário para esta funcionalidade;
    - ``repetir_ultima_chamada`` gera um lote NOVO e próprio (não repete
      o lote inteiro de uma chamada em conjunto).
"""

import pytest

import database
from models import StatusSenha


def test_chamar_varias_compartilha_mesmo_lote(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    s1 = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    s2 = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    s3 = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    resultado = database.chamar_varias(
        senha_ids=[s1.id, s2.id, s3.id], guiche="Mesa 01", usuario="Recepção"
    )

    assert resultado["lote_chamada"]
    numeros = {c["numero"] for c in resultado["chamadas"]}
    assert numeros == {s1.numero, s2.numero, s3.numero}

    for senha_id in (s1.id, s2.id, s3.id):
        senha_no_banco = database.obter_senha_por_id(senha_id)
        assert senha_no_banco.status == StatusSenha.CHAMADA
        assert senha_no_banco.hora_chamada is not None


def test_chamar_varias_ignora_ids_duplicados(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    senha = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    resultado = database.chamar_varias(
        senha_ids=[senha.id, senha.id, senha.id], guiche="Mesa 01", usuario="Recepção"
    )

    assert len(resultado["chamadas"]) == 1


def test_chamar_varias_tudo_ou_nada_id_inexistente_nao_altera_nada(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    valida = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    with pytest.raises(ValueError):
        database.chamar_varias(senha_ids=[valida.id, 99999], guiche="Mesa 01", usuario="Recepção")

    # Nada deve ter sido alterado — nem a senha válida da lista.
    assert database.obter_senha_por_id(valida.id).status == StatusSenha.EMITIDA


def test_chamar_varias_tudo_ou_nada_senha_ja_chamada_nao_altera_nada(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    ja_chamada = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    aguardando = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    database.chamar_proxima(guiche="Mesa 01", usuario="Recepção", empresa_id=empresa.id)  # chama ja_chamada

    with pytest.raises(ValueError):
        database.chamar_varias(
            senha_ids=[ja_chamada.id, aguardando.id], guiche="Mesa 02", usuario="Recepção 2"
        )

    # "aguardando" continua Emitida — não foi chamada junto por engano.
    assert database.obter_senha_por_id(aguardando.id).status == StatusSenha.EMITIDA


def test_chamar_varias_tudo_ou_nada_senha_de_outra_empresa_nao_altera_nada(banco_teste):
    alfa = database.criar_empresa("Alfa")
    beta = database.criar_empresa("Beta")
    senha_alfa = database.criar_senha(empresa_id=alfa.id, empresa=alfa.nome)
    senha_beta = database.criar_senha(empresa_id=beta.id, empresa=beta.nome)

    # Recrutador da Alfa tentando chamar uma senha da Beta junto.
    with pytest.raises(ValueError):
        database.chamar_varias(
            senha_ids=[senha_alfa.id, senha_beta.id],
            guiche="Mesa 01",
            usuario="Recrutador Alfa",
            empresa_id=alfa.id,
        )

    assert database.obter_senha_por_id(senha_alfa.id).status == StatusSenha.EMITIDA
    assert database.obter_senha_por_id(senha_beta.id).status == StatusSenha.EMITIDA


def test_chamar_varias_lista_vazia_levanta_erro(banco_teste):
    with pytest.raises(ValueError):
        database.chamar_varias(senha_ids=[], guiche="Mesa 01", usuario="Recepção")


def test_obter_chamada_atual_retorna_lista_completa_do_lote(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    s1 = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    s2 = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    database.chamar_varias(senha_ids=[s1.id, s2.id], guiche="Mesa 01", usuario="Recepção")
    atual = database.obter_chamada_atual(empresa_id=empresa.id)

    assert atual is not None
    assert len(atual["senhas"]) == 2
    numeros = {evento["numero"] for evento in atual["senhas"]}
    assert numeros == {s1.numero, s2.numero}
    # Campos no nível raiz continuam espelhando a primeira senha do lote,
    # para quem só lê um evento único (compatibilidade retroativa).
    assert atual["numero"] == s1.numero


def test_obter_chamada_atual_chamada_individual_traz_lote_de_um_item(banco_teste):
    empresa = database.criar_empresa("Empresa Alfa")
    database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    database.chamar_proxima(guiche="Mesa 01", usuario="Recepção", empresa_id=empresa.id)
    atual = database.obter_chamada_atual(empresa_id=empresa.id)

    assert len(atual["senhas"]) == 1


def test_obter_chamada_atual_isolamento_entre_empresas_com_lotes_simultaneos(banco_teste):
    """
    Requisito explícito do usuário: duas empresas chamando várias senhas
    "ao mesmo tempo" não podem misturar as sequências exibidas no painel
    uma da outra. Simula a concorrência intercalando as duas operações de
    chamada em lote (Alfa chama, depois Beta chama, sem que uma
    interfira na outra) e confirma que cada painel por empresa só vê o
    próprio lote — nunca o da outra.
    """
    alfa = database.criar_empresa("Alfa")
    beta = database.criar_empresa("Beta")

    alfa_s1 = database.criar_senha(empresa_id=alfa.id, empresa=alfa.nome)
    alfa_s2 = database.criar_senha(empresa_id=alfa.id, empresa=alfa.nome)
    beta_s1 = database.criar_senha(empresa_id=beta.id, empresa=beta.nome)
    beta_s2 = database.criar_senha(empresa_id=beta.id, empresa=beta.nome)
    beta_s3 = database.criar_senha(empresa_id=beta.id, empresa=beta.nome)

    # Alfa chama 2 senhas em lote.
    database.chamar_varias(
        senha_ids=[alfa_s1.id, alfa_s2.id], guiche="Mesa 01", usuario="Rec Alfa", empresa_id=alfa.id
    )
    # Logo em seguida, Beta chama 3 senhas em lote (simulando "ao mesmo
    # tempo" — outra empresa, outro guichê, outra operação).
    database.chamar_varias(
        senha_ids=[beta_s1.id, beta_s2.id, beta_s3.id],
        guiche="Mesa 05",
        usuario="Rec Beta",
        empresa_id=beta.id,
    )

    atual_alfa = database.obter_chamada_atual(empresa_id=alfa.id)
    atual_beta = database.obter_chamada_atual(empresa_id=beta.id)

    # O painel da Alfa continua vendo APENAS o lote da Alfa (2 senhas),
    # mesmo o lote da Beta (mais recente) tendo sido gravado depois.
    assert len(atual_alfa["senhas"]) == 2
    assert {e["numero"] for e in atual_alfa["senhas"]} == {alfa_s1.numero, alfa_s2.numero}
    for evento in atual_alfa["senhas"]:
        assert evento["empresa"] == "Alfa"

    # O painel da Beta vê o próprio lote (3 senhas), sem nada da Alfa.
    assert len(atual_beta["senhas"]) == 3
    assert {e["numero"] for e in atual_beta["senhas"]} == {beta_s1.numero, beta_s2.numero, beta_s3.numero}
    for evento in atual_beta["senhas"]:
        assert evento["empresa"] == "Beta"

    # O painel GERAL (sem empresa_id) mostra o lote mais recente de
    # todos — o da Beta, por ter sido chamado por último.
    atual_geral = database.obter_chamada_atual()
    assert len(atual_geral["senhas"]) == 3
    assert {e["numero"] for e in atual_geral["senhas"]} == {beta_s1.numero, beta_s2.numero, beta_s3.numero}


def _login_recrutador(cliente, empresa):
    """
    Loga como recrutador da empresa informada via o fluxo real de acesso
    por chave (``/empresas/<id>/entrar`` — ver app.py), o mesmo usado
    pelos recrutadores no feirão (não é login/senha tradicional). Retorna
    a resposta do POST, já com a sessão de recrutador ativa no cliente.
    """
    return cliente.post(
        f"/empresas/{empresa.id}/entrar",
        data={"nome_completo": "Recrutador de Teste", "chave": empresa.chave_acesso},
    )


def test_api_chamar_varias_chama_conjunto_via_http(banco_teste):
    """
    Teste de ponta a ponta da rota ``/api/chamar-varias``: login real de
    recrutador (por chave da empresa) + POST com uma lista de ids +
    confirma que todas viraram 'Chamada' com o mesmo lote.
    """
    import app as app_modulo

    empresa = database.criar_empresa("Empresa Alfa")
    s1 = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    s2 = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    cliente = app_modulo.app.test_client()
    _login_recrutador(cliente, empresa)

    resposta = cliente.post("/api/chamar-varias", json={"senha_ids": [s1.id, s2.id]})
    corpo = resposta.get_json()

    assert resposta.status_code == 200
    assert corpo["sucesso"] is True
    assert len(corpo["chamada"]["chamadas"]) == 2
    assert database.obter_senha_por_id(s1.id).status == StatusSenha.CHAMADA
    assert database.obter_senha_por_id(s2.id).status == StatusSenha.CHAMADA

    cliente.post("/logout")


def test_api_chamar_varias_bloqueia_senha_de_outra_empresa_via_http(banco_teste):
    """
    Regressão de IDOR: um recrutador logado na empresa Alfa não pode
    chamar, manipulando o corpo da requisição, uma senha que pertence à
    empresa Beta (ver app.py:_pode_gerenciar_senha, checado por id antes
    de chegar em database.chamar_varias).
    """
    import app as app_modulo

    alfa = database.criar_empresa("Alfa")
    beta = database.criar_empresa("Beta")
    senha_beta = database.criar_senha(empresa_id=beta.id, empresa=beta.nome)

    cliente = app_modulo.app.test_client()
    _login_recrutador(cliente, alfa)

    resposta = cliente.post("/api/chamar-varias", json={"senha_ids": [senha_beta.id]})

    assert resposta.status_code == 403
    assert database.obter_senha_por_id(senha_beta.id).status == StatusSenha.EMITIDA

    cliente.post("/logout")


def test_repetir_ultima_chamada_nao_repete_lote_inteiro(banco_teste):
    """
    ``repetir_ultima_chamada`` (botão "Repetir Chamada") continua
    repetindo só a ÚLTIMA senha chamada, mesmo que ela tenha feito parte
    de uma chamada em lote — decisão deliberada para não reanunciar o
    lote inteiro sem o recrutador pedir explicitamente. Gera um lote
    próprio (tamanho 1), diferente do lote original.
    """
    empresa = database.criar_empresa("Empresa Alfa")
    s1 = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)
    s2 = database.criar_senha(empresa_id=empresa.id, empresa=empresa.nome)

    lote_original = database.chamar_varias(
        senha_ids=[s1.id, s2.id], guiche="Mesa 01", usuario="Recepção"
    )

    repetida = database.repetir_ultima_chamada(guiche="Mesa 01")

    assert repetida is not None
    assert repetida["numero"] == s2.numero  # a última chamada do lote
    assert repetida["lote_chamada"] != lote_original["lote_chamada"]

    atual = database.obter_chamada_atual()
    assert len(atual["senhas"]) == 1
