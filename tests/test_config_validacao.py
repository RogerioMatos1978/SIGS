# -*- coding: utf-8 -*-
"""
test_config_validacao.py
=========================

Testa ``app._validar_configuracoes_numericas``, usada por
``POST /api/config`` para rejeitar valores fora de faixa ou não numéricos
ANTES de gravá-los (ex.: ``qtd_guiches: -1`` ou ``qtd_guiches: "abc"``
enviados diretamente à API, contornando os limites min/max do
``<input type="number">`` em templates/configuracoes.html).

Diferente dos demais testes (que exercitam só database.py/models.py),
este módulo importa ``app.py`` e por isso requer Flask instalado (ver
requirements.txt) — mas não inicia nenhum servidor nem abre conexão de
rede: a função testada é pura (recebe um dict, devolve uma mensagem de
erro ou ``None``).
"""

import app


def test_dicionario_vazio_e_valido():
    assert app._validar_configuracoes_numericas({}) is None


def test_campos_nao_numericos_sao_ignorados():
    assert app._validar_configuracoes_numericas({"nome_evento": "Feirão"}) is None


def test_valores_validos_dentro_da_faixa():
    dados = {
        "qtd_senhas_exibidas": 10,
        "tempo_atualizacao_ms": 2000,
        "qtd_guiches": 5,
        "qtd_guiches_por_empresa": 3,
    }
    assert app._validar_configuracoes_numericas(dados) is None


def test_valor_numerico_como_texto_e_aceito():
    # O formulário HTML sempre envia como texto; o próprio JS já converte
    # antes de mandar, mas a API precisa aceitar "10" tanto quanto 10.
    assert app._validar_configuracoes_numericas({"qtd_guiches": "10"}) is None


def test_valor_abaixo_do_minimo_e_rejeitado():
    erro = app._validar_configuracoes_numericas({"qtd_senhas_exibidas": 0})
    assert erro is not None
    assert "qtd_senhas_exibidas" in erro


def test_valor_acima_do_maximo_e_rejeitado():
    erro = app._validar_configuracoes_numericas({"qtd_guiches": 51})
    assert erro is not None
    assert "qtd_guiches" in erro


def test_valor_negativo_e_rejeitado():
    erro = app._validar_configuracoes_numericas({"qtd_guiches_por_empresa": -1})
    assert erro is not None


def test_valor_nao_numerico_e_rejeitado():
    erro = app._validar_configuracoes_numericas({"qtd_senhas_exibidas": "abc"})
    assert erro is not None


def test_valor_none_e_rejeitado():
    erro = app._validar_configuracoes_numericas({"qtd_guiches": None})
    assert erro is not None


def test_tempo_atualizacao_fora_da_faixa_e_rejeitado():
    assert app._validar_configuracoes_numericas({"tempo_atualizacao_ms": 100}) is not None
    assert app._validar_configuracoes_numericas({"tempo_atualizacao_ms": 999999}) is not None


def test_primeiro_campo_invalido_interrompe_a_validacao():
    """
    Garante que a função para no primeiro erro encontrado (não precisa
    validar os demais campos) e retorna uma mensagem mencionando o campo
    problemático — comportamento documentado na docstring da função.
    """
    erro = app._validar_configuracoes_numericas(
        {"qtd_senhas_exibidas": 999, "qtd_guiches": 5}
    )
    assert erro is not None
    assert "qtd_senhas_exibidas" in erro
