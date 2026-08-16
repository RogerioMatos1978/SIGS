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
    empresa: Optional[str] = None
    empresa_id: Optional[int] = None
    # "hora_chamada"/"hora_finalizada": marcos de tempo do ciclo de vida da
    # senha, usados pelos relatórios para calcular o tempo de atendimento
    # (ver database._migrar_tabela_senhas_adicionar_marcos_tempo,
    # database.chamar_proxima e database.finalizar_senha). Ambos ``None``
    # até a senha ser chamada/finalizada, respectivamente.
    hora_chamada: Optional[str] = None
    hora_finalizada: Optional[str] = None

    def to_dict(self) -> dict:
        """Converte a senha para um dicionário serializável em JSON."""
        return asdict(self)

    @staticmethod
    def from_row(linha: sqlite3.Row) -> "Senha":
        """Constrói uma instância de ``Senha`` a partir de uma linha do
        banco de dados (``sqlite3.Row``)."""
        chaves = linha.keys()
        return Senha(
            id=linha["id"],
            numero=linha["numero"],
            status=linha["status"],
            data_hora=linha["data_hora"],
            guiche=linha["guiche"],
            usuario=linha["usuario"],
            # A coluna "empresa" foi adicionada por migração automática (ver
            # database._migrar_tabela_senhas_adicionar_empresa); senhas
            # emitidas antes dessa migração simplesmente terão este campo
            # como None ("Não informado" nos relatórios).
            empresa=linha["empresa"] if "empresa" in chaves else None,
            # "empresa_id" (ver database._migrar_tabela_senhas_adicionar_empresa_id)
            # é a referência ESTÁVEL usada para filtrar fila/permissões por
            # empresa (ver app.py:_pode_gerenciar_senha) — "empresa" (acima)
            # é só o nome congelado no momento da emissão, usado apenas para
            # EXIBIÇÃO/relatórios, nunca para controle de acesso, pois o
            # nome de uma empresa pode ser reaproveitado depois de uma
            # renomeação.
            empresa_id=linha["empresa_id"] if "empresa_id" in chaves else None,
            hora_chamada=linha["hora_chamada"] if "hora_chamada" in chaves else None,
            hora_finalizada=linha["hora_finalizada"] if "hora_finalizada" in chaves else None,
        )


class PerfilUsuario:
    """
    Enumeração (baseada em strings) dos perfis de acesso disponíveis no
    SIGS. Mantida como classe de constantes (e não ``enum.Enum``) pelo
    mesmo motivo de ``StatusSenha``: compatibilidade direta com o valor
    armazenado como texto no SQLite.

    Quatro perfis, com responsabilidades bem separadas:

        ADMIN
            Acesso administrativo total (Configurações, Relatórios,
            Gerenciar Usuários, reinício de contador, reset de senhas
            emitidas, reset de senha de outros usuários). NÃO ocupa
            guichê e não opera a fila diretamente (não emite nem chama
            senhas) — seu papel é de gestão do sistema, não de
            atendimento.
        ATENDENTE
            Perfil "padrão" sugerido ao administrador ao cadastrar um
            novo usuário pela tela "Gerenciar Usuários" (não há
            autocadastro público). Ao logar, assume automaticamente um
            guichê de atendimento disponível (fila GERAL, compartilhada
            entre todas as empresas) e é responsável por chamar, repetir
            chamada e finalizar o atendimento das senhas — a finalização
            já dispara automaticamente a chamada da próxima senha da
            fila.
        EMISSOR
            Perfil restrito, criado apenas por um administrador pela
            tela de Gerenciar Usuários, destinado a operar um totem de
            emissão de senhas (por exemplo, na entrada do evento). Só
            emite senhas — essas senhas alimentam a fila consumida pelos
            usuários "atendente" e "recrutador". Não ocupa guichê e não
            chama senhas.
        RECRUTADOR
            Vinculado a UMA empresa específica do feirão (campo
            ``Usuario.empresa_id``, definido pelo administrador em
            "Gerenciar Usuários"). Ao logar, assume automaticamente uma
            sala/guichê disponível DENTRO da fila da sua própria empresa
            (pool independente da fila geral do Atendente — ver
            ``database.ocupar_proximo_guiche_empresa_disponivel``) e só
            chama, repete chamada e finaliza (dá baixa) senhas emitidas
            para essa empresa.
    """

    ADMIN = "admin"
    ATENDENTE = "atendente"
    EMISSOR = "emissor"
    RECRUTADOR = "recrutador"

    TODOS = (ADMIN, ATENDENTE, EMISSOR, RECRUTADOR)


@dataclass
class Usuario:
    """
    Representa um usuário do sistema (atendente ou administrador).

    O campo ``senha_hash`` nunca armazena a senha em texto puro — apenas o
    hash gerado por ``werkzeug.security.generate_password_hash`` (ver
    ``auth.py``). O método ``to_dict_publico`` deve ser utilizado sempre
    que os dados do usuário forem enviados ao navegador (API/JSON), pois
    remove o hash da senha da resposta.

    ``empresa_id`` só é relevante para o perfil "recrutador": é o id da
    empresa (tabela ``empresas``) à qual este usuário está vinculado.
    Para os demais perfis, permanece ``None``. Diferente do campo
    ``empresa`` de ``Senha`` (que grava o NOME como texto congelado no
    momento do evento), aqui usamos o ID com referência viva à empresa,
    pois o vínculo do recrutador deve sempre refletir o cadastro atual
    (inclusive se a empresa for renomeada).
    """

    id: int
    nome_completo: str
    login: str
    senha_hash: str
    perfil: str
    ativo: bool
    data_criacao: str
    ultimo_login: Optional[str] = None
    empresa_id: Optional[int] = None

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
            # A coluna "empresa_id" foi adicionada por migração automática
            # (ver database._migrar_tabela_usuarios_adicionar_empresa_id);
            # usuários de bancos antigos simplesmente ficam sem empresa
            # vinculada (correto, pois não eram recrutadores).
            empresa_id=linha["empresa_id"] if "empresa_id" in linha.keys() else None,
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


@dataclass
class Empresa:
    """
    Representa uma empresa participante do feirão do emprego.

    Cadastrada exclusivamente por um administrador, pela tela "Empresas"
    (``/admin/empresas``). Uma empresa ATIVA aparece no seletor exibido ao
    emitir uma senha (ver index.html/index.js); uma empresa desativada some
    desse seletor, mas o nome permanece gravado (como texto) em todas as
    senhas já emitidas para ela — desativar uma empresa NUNCA apaga ou
    altera o histórico de senhas/relatórios já gerados.

    ``logo_path`` e ``cor_principal`` formam a identidade visual da
    empresa (ambos opcionais — ``None`` até que um administrador faça o
    upload de um logo pela tela Empresas):

        logo_path
            Caminho do arquivo de logo RELATIVO À PASTA ``static/`` (ex.:
            ``"img/empresas/3.png"``), pronto para uso direto em
            ``url_for('static', filename=empresa.logo_path)``. Diferente
            de ``config.LOGO_PADRAO`` (que é relativo à raiz do projeto,
            usado por ``printer.py`` para abrir o arquivo diretamente com
            PIL) — os dois NÃO são intercambiáveis.
        cor_principal
            Cor hexadecimal (``"#RRGGBB"``) usada como ``--cor-principal``
            (ver layout.html) sempre que a página estiver no contexto
            desta empresa — painel público da empresa
            (``/painel/empresa/<id>``) ou tela principal de um recrutador
            vinculado a ela. É PREENCHIDA AUTOMATICAMENTE ao enviar um
            logo (extraída da imagem, ver
            ``app.py:_extrair_cor_predominante``), mas pode ser
            sobrescrita manualmente a qualquer momento (ver
            ``definir_cor_empresa``).

    ``contador_atual`` guarda o ÚLTIMO número de senha emitido PARA ESTA
    empresa — cada empresa tem sua própria sequência independente de
    numeração (001, 002, 003...), controlada por ``database.criar_senha``.
    Não confundir com a antiga configuração global ``contador_atual`` da
    tabela ``configuracoes`` (usada antes de cada empresa ter sua própria
    sequência), que não é mais lida.
    """

    id: int
    nome: str
    ativa: bool
    data_criacao: str
    logo_path: Optional[str] = None
    cor_principal: Optional[str] = None
    contador_atual: int = 0
    # ``None`` (padrão) = atendimento ABERTO normalmente. Um timestamp
    # aqui significa que um recrutador desta empresa clicou em
    # "Finalizar Atendimento do Dia" (ver
    # database.finalizar_atendimento_dia_empresa) — a empresa para de
    # aceitar novas emissões/chamadas até um administrador reabrir (ver
    # database.reabrir_atendimento_empresa).
    atendimento_finalizado_em: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_row(linha: sqlite3.Row) -> "Empresa":
        chaves = linha.keys()
        return Empresa(
            id=linha["id"],
            nome=linha["nome"],
            ativa=bool(linha["ativa"]),
            data_criacao=linha["data_criacao"],
            # As colunas abaixo foram adicionadas por migração automática
            # (ver database._migrar_tabela_empresas_adicionar_identidade_visual,
            # database._migrar_tabela_empresas_adicionar_contador e
            # database._migrar_tabela_empresas_adicionar_atendimento_finalizado);
            # empresas de bancos antigos simplesmente ficam sem identidade
            # visual própria (usam o logo/cor padrão do sistema), com
            # contador zerado, e com atendimento aberto (não finalizado)
            # até a próxima migração/ação.
            logo_path=linha["logo_path"] if "logo_path" in chaves else None,
            cor_principal=linha["cor_principal"] if "cor_principal" in chaves else None,
            contador_atual=linha["contador_atual"] if "contador_atual" in chaves else 0,
            atendimento_finalizado_em=(
                linha["atendimento_finalizado_em"] if "atendimento_finalizado_em" in chaves else None
            ),
        )
