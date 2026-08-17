# -*- coding: utf-8 -*-
"""
conftest.py
============

Configuração compartilhada dos testes automatizados do SIGS.

A fixture ``banco_teste`` cria, para CADA teste, um banco SQLite novo e
isolado em uma pasta temporária (via ``tmp_path`` do pytest) — nunca o
banco real de produção (``database/senhas.db``). Isso é feito trocando,
via ``monkeypatch``, os caminhos ``database.DATABASE_PATH`` e
``database.DATABASE_DIR`` que o módulo ``database.py`` usa internamente
em toda função que abre conexão (``get_connection``) ou inicializa o
schema (``inicializar_banco``).

Importante: o módulo ``database.py`` faz
``from config import DATABASE_DIR, DATABASE_PATH, ...`` no topo do
arquivo, ou seja, esses nomes passam a existir também no namespace de
``database``. Por isso o patch é aplicado em ``database.DATABASE_PATH``/
``database.DATABASE_DIR`` (e não em ``config.DATABASE_PATH``) — é essa
referência que ``get_connection``/``inicializar_banco`` de fato
utilizam em tempo de execução.
"""

import sys
from pathlib import Path

import pytest

# Garante que a raiz do projeto (pasta que contém database.py, models.py,
# config.py, app.py) esteja no sys.path, independente de onde o pytest for
# executado (ex.: ``pytest`` na raiz do projeto ou dentro de ``tests/``).
RAIZ_PROJETO = Path(__file__).resolve().parent.parent
if str(RAIZ_PROJETO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROJETO))

import database  # noqa: E402  (import após ajuste do sys.path, ver acima)


@pytest.fixture
def banco_teste(tmp_path, monkeypatch):
    """
    Isola cada teste em um banco de dados SQLite próprio e descartável.

    Uso típico:

        def test_algo(banco_teste):
            empresa = database.criar_empresa("Empresa Teste")
            ...

    Ao final do teste, a pasta temporária (e o arquivo .db dentro dela) é
    removida automaticamente pelo próprio pytest.
    """
    pasta_banco = tmp_path / "database"
    caminho_banco = pasta_banco / "senhas_teste.db"

    monkeypatch.setattr(database, "DATABASE_DIR", pasta_banco)
    monkeypatch.setattr(database, "DATABASE_PATH", caminho_banco)

    database.inicializar_banco()
    return caminho_banco
