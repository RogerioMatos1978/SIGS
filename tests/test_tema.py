# -*- coding: utf-8 -*-
"""
test_tema.py
============

Testa o botão de troca de tema claro/escuro (pedido explícito do
usuário: "Adicionar o TEMA Escuro atual em todos o sistema, deixando
um botão de troca de tema") — ver:
    - static/css/style.css (variáveis CSS + bloco html[data-tema="escuro"])
    - static/js/tema.js (aplica/alterna/persiste em localStorage)
    - templates/layout.html (script inline anti-flash, inclusão de
      tema.js e o botão em si — ``.botao-tema`` dentro da barra-usuario
      para quem está logado, ``.botao-tema--flutuante`` nas páginas
      públicas de autenticação)

Cobre a regra central do recurso: o botão (e tudo que o sustenta —
script inline, tema.js) deve aparecer em QUALQUER tela do sistema,
EXCETO nas três telas do painel público de TV (``/painel``,
``/painel/empresa/<id>``, ``/painel/geral``), que têm identidade
visual própria e sempre escura, deliberadamente isolada da preferência
de tema salva no navegador (ver README, seção sobre o painel público).
"""

import auth
import database


def _criar_admin_e_logar(cliente, login="admin_teste", senha="SenhaForte123"):
    """Cria um usuário administrador e autentica ``cliente`` com ele."""
    database.criar_usuario("Admin Teste", login, auth.gerar_hash_senha(senha), perfil="admin")
    resposta = cliente.post("/login", data={"login": login, "senha": senha})
    assert resposta.status_code in (200, 302)
    return resposta


def _tem_sistema_de_tema(html: str) -> bool:
    """
    Verifica, num HTML renderizado, se os TRÊS componentes do recurso
    de tema estão presentes juntos: o botão, o script inline
    anti-flash (que lê "sigs_tema" do localStorage) e a inclusão de
    tema.js. Os três sempre aparecem ou somem juntos (ver
    "pagina_com_tema" em layout.html) — checar os três evita um falso
    positivo (ex.: o botão sumir mas o script inline continuar).
    """
    tem_botao = 'class="botao-tema' in html
    tem_script_inline = "sigs_tema" in html and "localStorage.getItem" in html
    tem_temajs = "js/tema.js" in html
    return tem_botao and tem_script_inline and tem_temajs


def _nada_de_tema(html: str) -> bool:
    return (
        'class="botao-tema' not in html
        and "sigs_tema" not in html
        and "js/tema.js" not in html
    )


def test_paginas_publicas_de_auth_tem_botao_flutuante(banco_teste):
    import app as app_modulo

    empresa = database.criar_empresa("Empresa Alfa")
    cliente = app_modulo.app.test_client()

    for url in ("/login", "/empresas/entrar", f"/empresas/{empresa.id}/entrar"):
        html = cliente.get(url).get_data(as_text=True)
        assert _tem_sistema_de_tema(html), f"esperava sistema de tema em {url}"
        assert 'botao-tema--flutuante' in html, f"esperava variante flutuante em {url}"


def test_paineis_publicos_de_tv_nunca_tem_sistema_de_tema(banco_teste):
    """
    Requisito de design: o painel de TV é sempre escuro, com identidade
    visual fixa, independente de qualquer preferência de tema salva no
    navegador de quem o administra — por isso NUNCA deve receber o
    script/atributo/botão de tema, mesmo indiretamente.
    """
    import app as app_modulo

    empresa = database.criar_empresa("Empresa Alfa")
    cliente = app_modulo.app.test_client()

    for url in ("/painel", f"/painel/empresa/{empresa.id}", "/painel/geral"):
        html = cliente.get(url).get_data(as_text=True)
        assert _nada_de_tema(html), f"painel de TV não deveria ter nada de tema em {url}"


def test_paineis_publicos_sem_tema_mesmo_com_admin_logado(banco_teste):
    """
    Regressão específica: um administrador logado que navega até um
    painel de TV (ex.: para conferir a tela antes do evento) continua
    vendo a barra-usuário normalmente (comportamento pré-existente),
    mas SEM o botão de tema — a exclusão é por rota, não por sessão.
    """
    import app as app_modulo

    cliente = app_modulo.app.test_client()
    _criar_admin_e_logar(cliente)

    html = cliente.get("/painel/geral").get_data(as_text=True)
    assert _nada_de_tema(html)


def test_tela_principal_logada_tem_botao_na_barra_usuario(banco_teste):
    import app as app_modulo

    cliente = app_modulo.app.test_client()
    _criar_admin_e_logar(cliente)

    html = cliente.get("/").get_data(as_text=True)
    assert _tem_sistema_de_tema(html)
    # Dentro da barra-usuario (logado), NÃO é a variante flutuante.
    assert "botao-tema--flutuante" not in html


def test_telas_administrativas_tem_botao_de_tema(banco_teste):
    import app as app_modulo

    cliente = app_modulo.app.test_client()
    _criar_admin_e_logar(cliente)

    for url in ("/relatorios", "/admin/usuarios", "/admin/empresas", "/configuracoes"):
        html = cliente.get(url).get_data(as_text=True)
        assert _tem_sistema_de_tema(html), f"esperava sistema de tema em {url}"


def test_estilo_do_tema_escuro_esta_definido_no_css(banco_teste):
    """
    Confirma, no arquivo servido estaticamente, que a paleta escura
    (variáveis sob ``html[data-tema="escuro"]``) e a classe do botão
    (``.botao-tema``) realmente existem — evita um regressão boba tipo
    "botão sem nenhum CSS por trás" passar despercebida só porque os
    testes de HTML acima checam apenas a presença de texto/classes.
    """
    import app as app_modulo

    cliente = app_modulo.app.test_client()
    resposta = cliente.get("/static/css/style.css")
    css = resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    assert 'html[data-tema="escuro"]' in css
    assert "--cor-superficie" in css
    assert ".botao-tema" in css


def test_tema_js_existe_e_e_servido(banco_teste):
    import app as app_modulo

    cliente = app_modulo.app.test_client()
    resposta = cliente.get("/static/js/tema.js")
    js = resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    assert "alternarTema" in js
    assert "sigs_tema" in js
