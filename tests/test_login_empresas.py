# -*- coding: utf-8 -*-
"""
test_login_empresas.py
=======================

Testa o painel de "Acesso das Empresas" exibido ao lado do formulário
de login (``/login`` — ver templates/login.html e
app.py:login_tela/_listar_empresas_publicas), pedido explícito do
usuário para que o recrutador encontre sua empresa sem precisar navegar
até ``/empresas/entrar``.

Cobre:
    - o painel lista as empresas ATIVAS cadastradas, com link direto
      para ``/empresas/<id>/entrar``;
    - as duas opções fixas do sistema ("Criar Currículos"/"Imprimir
      Currículos") NUNCA aparecem ali (não são empresas reais do
      feirão, não têm recrutador);
    - empresas INATIVAS não aparecem;
    - a chave de acesso de 8 dígitos de cada empresa NUNCA vaza para o
      HTML desta página pública;
    - sem nenhuma empresa cadastrada, aparece a mensagem de aviso;
    - ``/empresas/entrar`` (tela cheia equivalente) continua funcionando
      normalmente após a refatoração que passou a compartilhar a mesma
      função de listagem (``_listar_empresas_publicas``).
"""

import database


def test_login_sem_empresas_mostra_mensagem(banco_teste):
    import app as app_modulo

    cliente = app_modulo.app.test_client()
    resposta = cliente.get("/login")
    html = resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    assert "Nenhuma empresa cadastrada no momento." in html


def test_login_lista_empresas_ativas_com_link_de_acesso(banco_teste):
    import app as app_modulo

    alfa = database.criar_empresa("Alfa Recrutamento")
    beta = database.criar_empresa("Beta Talentos")

    cliente = app_modulo.app.test_client()
    html = cliente.get("/login").get_data(as_text=True)

    assert "Alfa Recrutamento" in html
    assert "Beta Talentos" in html
    assert f"/empresas/{alfa.id}/entrar" in html
    assert f"/empresas/{beta.id}/entrar" in html


def test_login_nao_lista_empresas_fixas(banco_teste):
    import app as app_modulo

    cliente = app_modulo.app.test_client()
    html = cliente.get("/login").get_data(as_text=True)

    assert database.NOMES_EMPRESAS_FIXAS[0] not in html
    assert database.NOMES_EMPRESAS_FIXAS[1] not in html


def test_login_nao_lista_empresa_inativa(banco_teste):
    import app as app_modulo

    ativa = database.criar_empresa("Empresa Ativa")
    inativa = database.criar_empresa("Empresa Inativa")
    database.definir_status_empresa(inativa.id, ativa=False)

    cliente = app_modulo.app.test_client()
    html = cliente.get("/login").get_data(as_text=True)

    assert "Empresa Ativa" in html
    assert "Empresa Inativa" not in html


def test_login_nunca_vaza_chave_de_acesso(banco_teste):
    import app as app_modulo

    empresa = database.criar_empresa("Empresa Alfa")
    assert empresa.chave_acesso  # sanidade: a empresa tem mesmo uma chave

    cliente = app_modulo.app.test_client()
    html = cliente.get("/login").get_data(as_text=True)

    assert empresa.chave_acesso not in html


def test_empresas_entrar_tela_continua_funcionando_apos_refatoracao(banco_teste):
    """
    ``login_tela`` e ``empresas_entrar_tela`` passaram a compartilhar
    ``_listar_empresas_publicas`` — este teste garante que a tela cheia
    original não regrediu com essa mudança.
    """
    import app as app_modulo

    empresa = database.criar_empresa("Empresa Alfa")

    cliente = app_modulo.app.test_client()
    html = cliente.get("/empresas/entrar").get_data(as_text=True)

    assert "Empresa Alfa" in html
    assert f"/empresas/{empresa.id}/entrar" in html
    assert empresa.chave_acesso not in html
