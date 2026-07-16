# -*- coding: utf-8 -*-
"""
models.py
=========

Definição dos modelos de dados (representações estruturadas) utilizados
pelo SIGS. Este módulo não acessa o banco de dados diretamente; ele apenas
define a forma dos dados e funções auxiliares de conversão a partir de
linhas retornadas pelo SQLite (``sqlite3.Row``).

Manter os modelos separados da camada de acesso a dados (``database.py``)
facilita a evolução futura do sistema, por exemplo, a troca do SQLite por
outro banco de dados, ou a exposição desses mesmos modelos em uma API REST.
"""

import sqlite3
from dataclasses import dataclass, asdict
from typing import Optional


class StatusSenha:
    """
    Enumeração (simples, baseada em strings) dos status possíveis de uma
    senha dentro do fluxo de atendimento.

    Utilizar uma classe com constantes de string (em vez de ``enum.Enum``)
    mantém a compatibilidade direta com os valores gravados no SQLite, que
    armazena o status como texto puro.
    """

    EMITIDA = "Emitida"
    CHAMADA = "Chamada"
    FINALIZADA = "Finalizada"
    CANCELADA = "Cancelada"

    TODOS = (EMITIDA, CHAMADA, FINALIZADA, CANCELADA)


@dataclass
class Senha:
    """Representa uma senha emitida pelo totem de atendimento."""

    id: int
    numero: int
    status: str
    data_hora: str
    guiche: Optional[str] = None
    usuario: Optional[str] = None

    def to_dict(self) -> dict:
        """Converte a senha para um dicionário serializável em JSON."""
        return asdict(self)

    @staticmethod
    def from_row(linha: sqlite3.Row) -> "Senha":
        """Constrói uma instância de ``Senha`` a partir de uma linha do
        banco de dados (``sqlite3.Row``)."""
        return Senha(
            id=linha["id"],
            numero=linha["numero"],
            status=linha["status"],
            data_hora=linha["data_hora"],
            guiche=linha["guiche"],
            usuario=linha["usuario"],
        )


@dataclass
class ChamadaEvento:
    """
    Representa um "evento de chamada" de senha, ou seja, o momento em que
    uma senha foi anunciada no painel (seja pela primeira vez, seja por uma
    repetição solicitada pelo atendente).

    Separar os eventos de chamada da tabela ``senhas`` permite que uma
    mesma senha seja "repetida" no painel (nova animação/bip) sem que isso
    seja interpretado como pular ou reemitir uma senha da fila.
    """

    id: int
    senha_id: int
    numero: int
    guiche: Optional[str]
    usuario: Optional[str]
    data_hora: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_row(linha: sqlite3.Row) -> "ChamadaEvento":
        return ChamadaEvento(
            id=linha["id"],
            senha_id=linha["senha_id"],
            numero=linha["numero"],
            guiche=linha["guiche"],
            usuario=linha["usuario"],
            data_hora=linha["data_hora"],
        )
