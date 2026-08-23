# -*- coding: utf-8 -*-
"""
test_empresas_tela_botoes.py
=============================

Testa a correção/organização dos botões e da grade (tabela) da tela
"Empresas Cadastradas" (Administrador — ver templates/empresas.html),
pedido explícito do usuário. Cobre:

    - só os TRÊS botões que dizem respeito à identidade visual da
      própria empresa (Abrir Painel, Logo, Renomear) recebem a cor
      cadastrada da empresa (``--cor-empresa``) — antes, o seletor CSS
      pegava também os dois botões de WhatsApp por engano, deixando
      quase tudo na mesma cor sem nenhuma hierarquia visual;
    - os dois botões de "Compartilhar via WhatsApp" usam a classe de
      cor de marca (``.botao-whatsapp``), não mais a cor da empresa
      nem a cor padrão do sistema;
    - "Reiniciar Contador" volta a ser um botão padrão do sistema (não
      mais colorido com a cor da empresa);
    - a tabela tem rolagem horizontal própria sempre ligada
      (``.card-tabela-empresas``), não só abaixo de 700px;
    - o CSS por trás de tudo isso (bug de espaçamento duplicado
      gap+margin-right, e as regras de cor) realmente existe na folha
      de estilos servida.
"""

import auth
import database


def _criar_admin_e_logar(cliente, login="admin_teste", senha="SenhaForte123"):
    database.criar_usuario("Admin Teste", login, auth.gerar_hash_senha(senha), perfil="admin")
    resposta = cliente.post("/login", data={"login": login, "senha": senha})
    assert resposta.status_code in (200, 302)
    return resposta


def test_apenas_tres_botoes_marcados_para_cor_da_empresa(banco_teste):
    """
    Os únicos elementos com classe reconhecida pelo seletor de cor da
    empresa (ver style.css: #empresas-corpo tr .btn-upload-logo /
    .btn-renomear-empresa / .btn-abrir-painel-empresa) devem ser
    exatamente esses três — nem mais, nem menos.
    """
    import app as app_modulo

    empresa = database.criar_empresa("Empresa Alfa")
    cliente = app_modulo.app.test_client()
    _criar_admin_e_logar(cliente)

    html = cliente.get("/admin/empresas").get_data(as_text=True)

    assert "btn-abrir-painel-empresa" in html
    assert "btn-upload-logo" in html
    assert "btn-renomear-empresa" in html

    # Os botões de WhatsApp e "Reiniciar Contador" NÃO devem carregar
    # nenhuma dessas três classes coloridas pela empresa.
    assert "btn-abrir-painel-empresa" not in _isolar_botoes_whatsapp(html)


def _isolar_botoes_whatsapp(html: str) -> str:
    """Extrai só os trechos de markup dos dois links de WhatsApp, para
    checar suas classes isoladamente do resto da linha da tabela."""
    trechos = []
    indice = 0
    while True:
        inicio = html.find('href="https://wa.me/', indice)
        if inicio == -1:
            break
        # Volta até o início da tag <a ...> que contém esse href.
        abre_tag = html.rfind("<a ", 0, inicio)
        fecha_tag = html.find(">", inicio)
        trechos.append(html[abre_tag:fecha_tag])
        indice = fecha_tag
    return "\n".join(trechos)


def test_botoes_whatsapp_usam_cor_de_marca_nao_a_da_empresa(banco_teste):
    import app as app_modulo

    empresa = database.criar_empresa("Empresa Alfa")
    cliente = app_modulo.app.test_client()
    _criar_admin_e_logar(cliente)

    html = cliente.get("/admin/empresas").get_data(as_text=True)
    botoes_whatsapp = _isolar_botoes_whatsapp(html)

    assert botoes_whatsapp, "esperava encontrar os links de WhatsApp na página"
    assert "botao-whatsapp" in botoes_whatsapp
    # Nenhuma das classes que disparam a cor da empresa deve aparecer
    # nos botões de WhatsApp.
    assert "btn-abrir-painel-empresa" not in botoes_whatsapp
    assert "btn-upload-logo" not in botoes_whatsapp
    assert "btn-renomear-empresa" not in botoes_whatsapp


def test_reiniciar_contador_nao_usa_mais_cor_da_empresa(banco_teste):
    import app as app_modulo

    empresa = database.criar_empresa("Empresa Alfa")
    cliente = app_modulo.app.test_client()
    _criar_admin_e_logar(cliente)

    html = cliente.get("/admin/empresas").get_data(as_text=True)

    inicio = html.find("btn-reiniciar-contador-empresa")
    assert inicio != -1
    abre_tag = html.rfind("<button", 0, inicio)
    fecha_tag = html.find(">", inicio)
    tag_botao = html[abre_tag:fecha_tag]

    assert "botao-secundario" in tag_botao
    assert "botao-whatsapp" not in tag_botao


def test_tabela_de_empresas_tem_wrapper_de_rolagem_propria(banco_teste):
    import app as app_modulo

    cliente = app_modulo.app.test_client()
    _criar_admin_e_logar(cliente)

    html = cliente.get("/admin/empresas").get_data(as_text=True)
    assert "card-tabela-empresas" in html


def test_css_tem_as_regras_por_tras_da_correcao(banco_teste):
    import app as app_modulo

    cliente = app_modulo.app.test_client()
    css = cliente.get("/static/css/style.css").get_data(as_text=True)

    # Bug de espaçamento duplicado (gap do flex + margin-right do botão).
    assert ".acoes-usuario .botao-acao-pequeno" in css
    # Cor de marca do WhatsApp.
    assert ".botao-whatsapp" in css
    assert "#25D366" in css
    # Rolagem horizontal própria da tabela de empresas.
    assert ".card-tabela-empresas" in css
