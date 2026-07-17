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


class PerfilUsuario:
    """
    Enumeração (baseada em strings) dos perfis de acesso disponíveis no
    SIGS. Mantida como classe de constantes (e não ``enum.Enum``) pelo
    mesmo motivo de ``StatusSenha``: compatibilidade direta com o valor
    armazenado como texto no SQLite.

    Três perfis, com responsabilidades bem separadas:

        ADMIN
            Acesso administrativo total (Configurações, Relatórios,
            Gerenciar Usuários, reinício de contador, reset de senhas
            emitidas, reset de senha de outros usuários). NÃO ocupa
            guichê e não opera a fila diretamente (não emite nem chama
            senhas) — seu papel é de gestão do sistema, não de
            atendimento.
        ATENDENTE
            Perfil "padrão" atribuído a quem se cadastra pela tela
            pública de cadastro. Ao logar, assume automaticamente um
            guichê de atendimento disponível e é responsável por chamar,
            repetir chamada e finalizar o atendimento das senhas — a
            finalização já dispara automaticamente a chamada da próxima
            senha da fila.
        EMISSOR
            Perfil restrito, criado apenas por um administrador pela
            tela de Gerenciar Usuários, destinado a operar um totem de
            emissão de senhas (por exemplo, na entrada do evento). Só
            emite senhas — essas senhas alimentam a fila consumida pelos
            usuários "atendente". Não ocupa guichê e não chama senhas.
    """

    ADMIN = "admin"
    ATENDENTE = "atendente"
    EMISSOR = "emissor"

    TODOS = (ADMIN, ATENDENTE, EMISSOR)


@dataclass
class Usuario:
    """
    Representa um usuário do sistema (atendente ou administrador).

    O campo ``senha_hash`` nunca armazena a senha em texto puro — apenas o
    hash gerado por ``werkzeug.security.generate_password_hash`` (ver
    ``auth.py``). O método ``to_dict_publico`` deve ser utilizado sempre
    que os dados do usuário forem enviados ao navegador (API/JSON), pois
    remove o hash da senha da resposta.
    """

    id: int
    nome_completo: str
    login: str
    senha_hash: str
    perfil: str
    ativo: bool
    data_criacao: str
    ultimo_login: Optional[str] = None

    def to_dict_publico(self) -> dict:
        """Retorna os dados do usuário SEM o hash de senha, seguro para
        ser enviado ao cliente (navegador) em respostas JSON."""
        dados = asdict(self)
        dados.pop("senha_hash", None)
        return dados

    @staticmethod
    def from_row(linha: sqlite3.Row) -> "Usuario":
        return Usuario(
            id=linha["id"],
            nome_completo=linha["nome_completo"],
            login=linha["login"],
            senha_hash=linha["senha_hash"],
            perfil=linha["perfil"],
            ativo=bool(linha["ativo"]),
            data_criacao=linha["data_criacao"],
            ultimo_login=linha["ultimo_login"],
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
