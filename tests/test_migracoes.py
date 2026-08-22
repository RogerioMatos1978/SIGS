# -*- coding: utf-8 -*-
"""
test_migracoes.py
==================

Testa a criação inicial do schema e a IDEMPOTÊNCIA das migrações
automáticas de ``database.inicializar_banco()`` — ou seja, garante que
rodar a inicialização várias vezes seguidas (o que acontece sempre que o
servidor Flask é reiniciado) nunca falha nem duplica colunas/índices.
"""

import database


TABELAS_ESPERADAS = {
    "senhas",
    "eventos_chamada",
    "logs",
    "usuarios",
    "guiches_ocupados",
    "guiches_empresa_ocupados",
    "empresas",
}


def _listar_tabelas(conexao):
    linhas = conexao.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {linha[0] for linha in linhas}


def _colunas_da_tabela(conexao, tabela):
    linhas = conexao.execute(f"PRAGMA table_info({tabela})").fetchall()
    return {linha[1] for linha in linhas}


def test_inicializar_banco_cria_todas_as_tabelas(banco_teste):
    with database.get_connection() as conexao:
        tabelas = _listar_tabelas(conexao)

    faltando = TABELAS_ESPERADAS - tabelas
    assert not faltando, f"Tabelas não criadas: {faltando}"


def test_inicializar_banco_e_idempotente(banco_teste):
    # A fixture banco_teste já chamou inicializar_banco() uma vez.
    # Chamar de novo (simulando reinícios do servidor) não pode falhar.
    database.inicializar_banco()
    database.inicializar_banco()
    database.inicializar_banco()

    with database.get_connection() as conexao:
        tabelas = _listar_tabelas(conexao)
    assert TABELAS_ESPERADAS.issubset(tabelas)


def test_tabela_senhas_possui_colunas_de_empresa_e_marcos_de_tempo(banco_teste):
    with database.get_connection() as conexao:
        colunas = _colunas_da_tabela(conexao, "senhas")

    for coluna in ("empresa", "empresa_id", "hora_chamada", "hora_finalizada"):
        assert coluna in colunas, f"Coluna '{coluna}' ausente em 'senhas'"


def test_tabela_empresas_possui_colunas_de_identidade_e_contador(banco_teste):
    with database.get_connection() as conexao:
        colunas = _colunas_da_tabela(conexao, "empresas")

    for coluna in (
        "logo_path",
        "cor_principal",
        "contador_atual",
        "emissao_bloqueada_em",
    ):
        assert coluna in colunas, f"Coluna '{coluna}' ausente em 'empresas'"


def test_indice_unico_nocase_de_empresas_existe(banco_teste):
    with database.get_connection() as conexao:
        indices = [
            linha[0]
            for linha in conexao.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND tbl_name = 'empresas'"
            ).fetchall()
        ]
    assert "idx_empresas_nome_nocase" in indices
