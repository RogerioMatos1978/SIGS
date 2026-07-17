# -*- coding: utf-8 -*-
"""
database.py
===========

Camada de acesso a dados (DAO - Data Access Object) do SIGS.

Este módulo concentra TODA a interação com o banco de dados SQLite,
incluindo:
    - Criação automática do banco e das tabelas.
    - Emissão de senhas (com geração atômica do número sequencial).
    - Chamada de senhas em regime FIFO (primeira a entrar, primeira a sair).
    - Repetição da última chamada (nova animação/bip no painel, sem alterar
      a posição da fila).
    - Consultas para o painel público (últimas emitidas, chamada atual).
    - Consultas para relatórios (emitidas, chamadas, tempo médio de espera).
    - Registro de logs de auditoria em tabela própria.

Todas as consultas utilizam parâmetros (``?``) do SQLite, nunca concatenação
de strings, prevenindo SQL Injection.

Tabelas criadas:

    senhas
        id, numero, status, data_hora, guiche, usuario

    eventos_chamada
        id, senha_id, numero, guiche, usuario, data_hora
        (cada chamada ou repetição de chamada gera uma nova linha aqui)

    logs
        id, data_hora, nivel, mensagem
"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, Generator, List, Optional

from config import DATABASE_DIR, DATABASE_PATH, config_manager, logger
from models import ChamadaEvento, PerfilUsuario, Senha, StatusSenha, Usuario

# Lock utilizado para proteger operações que precisam ser atômicas mesmo
# quando o servidor Flask é executado em modo threaded=True (várias
# requisições simultâneas). O SQLite já serializa escritas no nível do
# arquivo, mas o lock evita condições de corrida na lógica de aplicação
# (por exemplo, ler o contador, incrementar e gravar).
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Conexão e inicialização do banco
# ---------------------------------------------------------------------------

@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager que abre uma conexão com o banco SQLite e garante o
    fechamento correto (mesmo em caso de exceção).

    Utiliza ``sqlite3.Row`` como row_factory para permitir acesso aos
    campos por nome (ex.: linha["numero"]), tornando o código mais legível.
    """
    conexao = sqlite3.connect(str(DATABASE_PATH), timeout=10, check_same_thread=False)
    conexao.row_factory = sqlite3.Row
    # PRAGMA para melhorar concorrência de leitura/escrita.
    conexao.execute("PRAGMA journal_mode = WAL")
    conexao.execute("PRAGMA foreign_keys = ON")
    try:
        yield conexao
    finally:
        conexao.close()


def inicializar_banco() -> None:
    """
    Cria o diretório e o arquivo do banco de dados (se ainda não existirem)
    e garante a existência de todas as tabelas necessárias.

    Esta função deve ser chamada uma única vez, na inicialização da
    aplicação Flask (ver ``app.py``).
    """
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)

    with get_connection() as conexao:
        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS senhas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'Emitida'
                    CHECK (status IN ('Emitida', 'Chamada', 'Finalizada', 'Cancelada')),
                data_hora TEXT NOT NULL,
                guiche TEXT,
                usuario TEXT
            )
            """
        )

        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS eventos_chamada (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                senha_id INTEGER NOT NULL,
                numero INTEGER NOT NULL,
                guiche TEXT,
                usuario TEXT,
                data_hora TEXT NOT NULL,
                FOREIGN KEY (senha_id) REFERENCES senhas (id)
            )
            """
        )

        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_hora TEXT NOT NULL,
                nivel TEXT NOT NULL,
                mensagem TEXT NOT NULL
            )
            """
        )

        # Usuários do sistema (login obrigatório para qualquer acesso).
        #
        # Observação de projeto: o campo "perfil" NÃO possui uma cláusula
        # CHECK travando os valores possíveis (ex.: apenas admin/atendente).
        # Isso é proposital: a validação de perfis válidos é feita em
        # Python (``PerfilUsuario.TODOS``, checado em
        # ``definir_perfil_usuario`` e nas rotas de app.py), o que permite
        # adicionar novos perfis no futuro (ex.: um perfil de supervisor)
        # sem exigir migração de esquema do SQLite — apenas atualizar
        # ``models.PerfilUsuario``.
        conexao.execute(
            f"""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome_completo TEXT NOT NULL,
                login TEXT NOT NULL UNIQUE,
                senha_hash TEXT NOT NULL,
                perfil TEXT NOT NULL DEFAULT '{PerfilUsuario.ATENDENTE}',
                ativo INTEGER NOT NULL DEFAULT 1,
                data_criacao TEXT NOT NULL,
                ultimo_login TEXT
            )
            """
        )

        # Ocupação de guichês: cada guichê (1..N, N definido em
        # Configurações) só pode estar associado a um usuário logado por
        # vez. A linha é removida quando o usuário efetua logout.
        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS guiches_ocupados (
                guiche INTEGER PRIMARY KEY,
                usuario_id INTEGER NOT NULL,
                usuario_nome TEXT NOT NULL,
                ocupado_desde TEXT NOT NULL,
                FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
            )
            """
        )

        # Índices para acelerar as consultas mais frequentes.
        conexao.execute("CREATE INDEX IF NOT EXISTS idx_senhas_status ON senhas (status)")
        conexao.execute(
            "CREATE INDEX IF NOT EXISTS idx_eventos_data ON eventos_chamada (data_hora)"
        )
        conexao.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_login ON usuarios (login)")

        conexao.commit()

    logger.info("Banco de dados inicializado em: %s", DATABASE_PATH)


# ---------------------------------------------------------------------------
# Utilitários internos
# ---------------------------------------------------------------------------

def _agora_iso() -> str:
    """Retorna o timestamp atual no formato ISO 8601 (com segundos)."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def registrar_log(nivel: str, mensagem: str) -> None:
    """
    Registra uma mensagem de log tanto no arquivo de log da aplicação
    (via módulo ``logging``, configurado em ``config.py``) quanto na tabela
    ``logs`` do banco de dados, permitindo auditoria consultável via SQL.
    """
    nivel = nivel.upper()
    if nivel == "ERROR":
        logger.error(mensagem)
    elif nivel == "WARNING":
        logger.warning(mensagem)
    else:
        logger.info(mensagem)

    try:
        with get_connection() as conexao:
            conexao.execute(
                "INSERT INTO logs (data_hora, nivel, mensagem) VALUES (?, ?, ?)",
                (_agora_iso(), nivel, mensagem),
            )
            conexao.commit()
    except sqlite3.Error as erro:
        # Se o próprio registro de log falhar, ao menos garantimos que o
        # erro seja visível no console/arquivo de log da aplicação.
        logger.error("Falha ao gravar log no banco de dados: %s", erro)


# ---------------------------------------------------------------------------
# Emissão de senhas
# ---------------------------------------------------------------------------

def criar_senha(guiche: Optional[str] = None, usuario: Optional[str] = None) -> Senha:
    """
    Cria (emite) uma nova senha.

    O número sequencial da senha é obtido de forma atômica a partir da
    configuração ``contador_atual`` (tabela ``configuracoes``), protegida
    por um lock em memória para evitar que duas requisições simultâneas
    gerem o mesmo número.

    Retorna a instância de ``Senha`` recém-criada.
    """
    with _lock:
        numero_atual = config_manager.obter("contador_atual", 0)
        novo_numero = int(numero_atual) + 1
        config_manager.salvar({"contador_atual": novo_numero})

        data_hora = _agora_iso()
        with get_connection() as conexao:
            cursor = conexao.execute(
                """
                INSERT INTO senhas (numero, status, data_hora, guiche, usuario)
                VALUES (?, ?, ?, ?, ?)
                """,
                (novo_numero, StatusSenha.EMITIDA, data_hora, guiche, usuario),
            )
            conexao.commit()
            senha_id = cursor.lastrowid

    registrar_log("INFO", f"Senha emitida: número {novo_numero:03d} (id={senha_id})")

    return Senha(
        id=senha_id,
        numero=novo_numero,
        status=StatusSenha.EMITIDA,
        data_hora=data_hora,
        guiche=guiche,
        usuario=usuario,
    )


def reiniciar_contador() -> None:
    """
    Reinicia o contador de numeração de senhas para zero.

    Esta operação NÃO apaga o histórico de senhas já emitidas; apenas faz
    com que a próxima senha emitida volte a ser numerada a partir de 001.
    """
    with _lock:
        config_manager.salvar({"contador_atual": 0})
    registrar_log("WARNING", "Contador de senhas reiniciado manualmente.")


# ---------------------------------------------------------------------------
# Chamada de senhas (fila FIFO)
# ---------------------------------------------------------------------------

def obter_proxima_emitida() -> Optional[Senha]:
    """
    Retorna a próxima senha com status 'Emitida', respeitando a ordem de
    chegada (FIFO), ou ``None`` caso não haja senhas aguardando chamada.
    """
    with get_connection() as conexao:
        linha = conexao.execute(
            """
            SELECT * FROM senhas
            WHERE status = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (StatusSenha.EMITIDA,),
        ).fetchone()

    return Senha.from_row(linha) if linha else None


def chamar_proxima(guiche: str, usuario: str) -> Optional[Dict]:
    """
    Chama a próxima senha da fila (a mais antiga com status 'Emitida').

    A senha chamada tem seu status atualizado para 'Chamada' e um novo
    registro é criado em ``eventos_chamada``, representando o anúncio no
    painel. Retorna um dicionário com os dados da senha e do evento de
    chamada, ou ``None`` se a fila estiver vazia.

    A operação é protegida por lock para impedir que duas chamadas
    simultâneas peguem a mesma senha (condição de corrida).
    """
    with _lock:
        proxima = obter_proxima_emitida()
        if proxima is None:
            return None

        data_hora = _agora_iso()
        with get_connection() as conexao:
            conexao.execute(
                "UPDATE senhas SET status = ?, guiche = ?, usuario = ? WHERE id = ?",
                (StatusSenha.CHAMADA, guiche, usuario, proxima.id),
            )
            cursor = conexao.execute(
                """
                INSERT INTO eventos_chamada (senha_id, numero, guiche, usuario, data_hora)
                VALUES (?, ?, ?, ?, ?)
                """,
                (proxima.id, proxima.numero, guiche, usuario, data_hora),
            )
            conexao.commit()
            evento_id = cursor.lastrowid

    registrar_log(
        "INFO",
        f"Senha {proxima.numero:03d} chamada no guichê '{guiche}' por '{usuario}'.",
    )

    return {
        "evento_id": evento_id,
        "senha_id": proxima.id,
        "numero": proxima.numero,
        "guiche": guiche,
        "usuario": usuario,
        "data_hora": data_hora,
    }


def repetir_ultima_chamada() -> Optional[Dict]:
    """
    Repete a última senha chamada, gerando um NOVO evento de chamada com o
    mesmo número/guichê/usuário. Isso permite que o painel detecte a
    mudança (novo id de evento) e dispare novamente a animação e o bip,
    sem alterar a posição da fila nem duplicar a senha na tabela
    ``senhas``.

    Retorna ``None`` se ainda não houve nenhuma chamada.
    """
    with get_connection() as conexao:
        ultimo = conexao.execute(
            "SELECT * FROM eventos_chamada ORDER BY id DESC LIMIT 1"
        ).fetchone()

    if ultimo is None:
        return None

    data_hora = _agora_iso()
    with get_connection() as conexao:
        cursor = conexao.execute(
            """
            INSERT INTO eventos_chamada (senha_id, numero, guiche, usuario, data_hora)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ultimo["senha_id"], ultimo["numero"], ultimo["guiche"], ultimo["usuario"], data_hora),
        )
        conexao.commit()
        evento_id = cursor.lastrowid

    registrar_log("INFO", f"Repetição de chamada da senha {ultimo['numero']:03d}.")

    return {
        "evento_id": evento_id,
        "senha_id": ultimo["senha_id"],
        "numero": ultimo["numero"],
        "guiche": ultimo["guiche"],
        "usuario": ultimo["usuario"],
        "data_hora": data_hora,
    }


def obter_senha_em_atendimento(guiche: str) -> Optional[Senha]:
    """
    Retorna a senha atualmente em atendimento (status 'Chamada') em um
    guichê específico, ou ``None`` se não houver nenhuma senha em
    atendimento nesse guichê no momento.

    Como cada guichê só pode estar ocupado por um usuário logado por vez
    (ver ``ocupar_proximo_guiche_disponivel``), buscar pela string do
    guichê é suficiente para identificar de forma inequívoca a senha que
    o atendente está atualmente atendendo.
    """
    with get_connection() as conexao:
        linha = conexao.execute(
            """
            SELECT * FROM senhas
            WHERE status = ? AND guiche = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (StatusSenha.CHAMADA, guiche),
        ).fetchone()

    return Senha.from_row(linha) if linha else None


def finalizar_atendimento_e_chamar_proxima(guiche: str, usuario: str) -> Dict:
    """
    Implementa o botão "Finalizar Atendimento": encerra (marca como
    'Finalizada') a senha que está sendo atendida no guichê informado e,
    em seguida, chama automaticamente a próxima senha da fila (FIFO) para
    o mesmo guichê/atendente.

    Retorna um dicionário com duas chaves:
        - "senha_finalizada": dados da senha finalizada, ou ``None`` se
          não havia nenhuma senha em atendimento neste guichê (o botão
          então se comporta apenas como "Chamar Próxima").
        - "chamada": dados da nova chamada (mesmo formato de
          ``chamar_proxima``), ou ``None`` se a fila estiver vazia — caso
          em que o atendente deve aguardar a emissão de uma nova senha.
    """
    senha_em_atendimento = obter_senha_em_atendimento(guiche)

    senha_finalizada_dict = None
    if senha_em_atendimento is not None:
        finalizar_senha(senha_em_atendimento.id)
        senha_finalizada_dict = senha_em_atendimento.to_dict()

    proxima_chamada = chamar_proxima(guiche=guiche, usuario=usuario)

    return {
        "senha_finalizada": senha_finalizada_dict,
        "chamada": proxima_chamada,
    }


def obter_chamada_atual() -> Optional[Dict]:
    """
    Retorna os dados do evento de chamada mais recente (a senha que deve
    estar em destaque no painel neste momento), ou ``None`` se nenhuma
    chamada foi realizada ainda.
    """
    with get_connection() as conexao:
        linha = conexao.execute(
            "SELECT * FROM eventos_chamada ORDER BY id DESC LIMIT 1"
        ).fetchone()

    if linha is None:
        return None

    evento = ChamadaEvento.from_row(linha)
    return evento.to_dict()


# ---------------------------------------------------------------------------
# Consultas para o painel público
# ---------------------------------------------------------------------------

def listar_ultimas_emitidas(quantidade: int = 10) -> List[Dict]:
    """
    Retorna as últimas N senhas emitidas (independentemente do status),
    ordenadas da mais recente para a mais antiga. Utilizado pelo painel
    para exibir o histórico de senhas emitidas.
    """
    with get_connection() as conexao:
        linhas = conexao.execute(
            "SELECT * FROM senhas ORDER BY id DESC LIMIT ?",
            (quantidade,),
        ).fetchall()

    return [Senha.from_row(linha).to_dict() for linha in linhas]


def contar_aguardando() -> int:
    """Retorna a quantidade de senhas atualmente aguardando chamada."""
    with get_connection() as conexao:
        linha = conexao.execute(
            "SELECT COUNT(*) AS total FROM senhas WHERE status = ?",
            (StatusSenha.EMITIDA,),
        ).fetchone()
    return int(linha["total"])


# ---------------------------------------------------------------------------
# Gerenciamento manual de senhas (finalizar / cancelar)
# ---------------------------------------------------------------------------

def finalizar_senha(senha_id: int) -> bool:
    """Marca uma senha como 'Finalizada' (atendimento concluído)."""
    with get_connection() as conexao:
        cursor = conexao.execute(
            "UPDATE senhas SET status = ? WHERE id = ?",
            (StatusSenha.FINALIZADA, senha_id),
        )
        conexao.commit()
        alterou = cursor.rowcount > 0

    if alterou:
        registrar_log("INFO", f"Senha id={senha_id} finalizada.")
    return alterou


def cancelar_senha(senha_id: int) -> bool:
    """Marca uma senha como 'Cancelada' (não será chamada)."""
    with get_connection() as conexao:
        cursor = conexao.execute(
            "UPDATE senhas SET status = ? WHERE id = ?",
            (StatusSenha.CANCELADA, senha_id),
        )
        conexao.commit()
        alterou = cursor.rowcount > 0

    if alterou:
        registrar_log("WARNING", f"Senha id={senha_id} cancelada.")
    return alterou


def listar_fila_atual(limite: int = 20) -> List[Dict]:
    """Retorna as senhas atualmente aguardando chamada (status Emitida),
    em ordem de chegada, para exibição em uma tela de gerenciamento."""
    with get_connection() as conexao:
        linhas = conexao.execute(
            """
            SELECT * FROM senhas
            WHERE status = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (StatusSenha.EMITIDA, limite),
        ).fetchall()

    return [Senha.from_row(linha).to_dict() for linha in linhas]


# ---------------------------------------------------------------------------
# Relatórios
# ---------------------------------------------------------------------------

def listar_senhas_periodo(
    inicio: Optional[str] = None,
    fim: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict]:
    """
    Retorna as senhas emitidas dentro de um período (datas no formato
    'YYYY-MM-DD'), opcionalmente filtradas por status. Utilizado pela
    geração de relatórios (CSV, Excel, PDF).
    """
    condicoes = []
    parametros: List = []

    if inicio:
        condicoes.append("date(data_hora) >= date(?)")
        parametros.append(inicio)
    if fim:
        condicoes.append("date(data_hora) <= date(?)")
        parametros.append(fim)
    if status:
        condicoes.append("status = ?")
        parametros.append(status)

    where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""

    with get_connection() as conexao:
        linhas = conexao.execute(
            f"SELECT * FROM senhas {where} ORDER BY id ASC",
            parametros,
        ).fetchall()

    return [Senha.from_row(linha).to_dict() for linha in linhas]


def listar_chamadas_periodo(inicio: Optional[str] = None, fim: Optional[str] = None) -> List[Dict]:
    """Retorna todos os eventos de chamada realizados dentro de um período."""
    condicoes = []
    parametros: List = []

    if inicio:
        condicoes.append("date(data_hora) >= date(?)")
        parametros.append(inicio)
    if fim:
        condicoes.append("date(data_hora) <= date(?)")
        parametros.append(fim)

    where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""

    with get_connection() as conexao:
        linhas = conexao.execute(
            f"SELECT * FROM eventos_chamada {where} ORDER BY id ASC",
            parametros,
        ).fetchall()

    return [ChamadaEvento.from_row(linha).to_dict() for linha in linhas]


def tempo_medio_atendimento(inicio: Optional[str] = None, fim: Optional[str] = None) -> Dict:
    """
    Calcula o tempo médio de espera entre a emissão da senha e a sua
    primeira chamada, em segundos, para as senhas emitidas dentro do
    período informado.

    Retorna um dicionário com o tempo médio (em segundos e formatado como
    "MM:SS"), além da quantidade de senhas consideradas no cálculo.
    """
    condicoes = ["s.id = e.senha_id"]
    parametros: List = []

    if inicio:
        condicoes.append("date(s.data_hora) >= date(?)")
        parametros.append(inicio)
    if fim:
        condicoes.append("date(s.data_hora) <= date(?)")
        parametros.append(fim)

    where = " AND ".join(condicoes)

    # Considera apenas a PRIMEIRA chamada de cada senha (MIN(e.data_hora)),
    # pois repetições de chamada não devem distorcer o tempo médio de
    # espera real do cliente.
    consulta = f"""
        SELECT s.data_hora AS emissao, MIN(e.data_hora) AS primeira_chamada
        FROM senhas s
        JOIN eventos_chamada e ON {where}
        GROUP BY s.id
    """

    with get_connection() as conexao:
        linhas = conexao.execute(consulta, parametros).fetchall()

    if not linhas:
        return {"tempo_medio_segundos": 0, "tempo_medio_formatado": "00:00", "total_amostras": 0}

    formato = "%Y-%m-%d %H:%M:%S"
    diferencas = []
    for linha in linhas:
        try:
            emissao = datetime.strptime(linha["emissao"], formato)
            chamada = datetime.strptime(linha["primeira_chamada"], formato)
            diferencas.append((chamada - emissao).total_seconds())
        except (TypeError, ValueError):
            continue

    if not diferencas:
        return {"tempo_medio_segundos": 0, "tempo_medio_formatado": "00:00", "total_amostras": 0}

    media_segundos = sum(diferencas) / len(diferencas)
    minutos, segundos = divmod(int(media_segundos), 60)

    return {
        "tempo_medio_segundos": round(media_segundos, 1),
        "tempo_medio_formatado": f"{minutos:02d}:{segundos:02d}",
        "total_amostras": len(diferencas),
    }


# ---------------------------------------------------------------------------
# Usuários (autenticação e autorização)
# ---------------------------------------------------------------------------
#
# As funções de hashing/verificação de senha ficam em ``auth.py`` (camada
# de autenticação), não aqui. Este módulo apenas persiste e consulta os
# dados já com o hash pronto, mantendo a separação de responsabilidades.

def contar_usuarios() -> int:
    """Retorna a quantidade total de usuários cadastrados no sistema."""
    with get_connection() as conexao:
        linha = conexao.execute("SELECT COUNT(*) AS total FROM usuarios").fetchone()
    return int(linha["total"])


def criar_usuario(nome_completo: str, login: str, senha_hash: str, perfil: Optional[str] = None) -> Usuario:
    """
    Cria um novo usuário no sistema.

    Regra de negócio importante: o PRIMEIRO usuário cadastrado no sistema
    (quando a tabela ``usuarios`` está vazia) se torna administrador
    automaticamente, permitindo o "bootstrap" inicial do sistema sem
    exigir configuração manual do banco de dados. Todos os cadastros
    seguintes recebem, por padrão, o perfil "atendente" (acesso restrito),
    a menos que um administrador altere o perfil posteriormente pela tela
    de Gerenciar Usuários.

    O login é normalizado (espaços removidos e convertido para minúsculas)
    antes de ser gravado, evitando que "Joao", "joao" e " joao " sejam
    tratados como usuários diferentes por uma simples variação de
    maiúsculas/minúsculas ou espaços acidentais.
    """
    login_normalizado = (login or "").strip().lower()

    if perfil is None:
        perfil = PerfilUsuario.ADMIN if contar_usuarios() == 0 else PerfilUsuario.ATENDENTE

    data_criacao = _agora_iso()

    with get_connection() as conexao:
        try:
            cursor = conexao.execute(
                """
                INSERT INTO usuarios (nome_completo, login, senha_hash, perfil, ativo, data_criacao)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (nome_completo, login_normalizado, senha_hash, perfil, data_criacao),
            )
            conexao.commit()
        except sqlite3.IntegrityError as erro:
            raise ValueError(f"Já existe um usuário com o login '{login_normalizado}'.") from erro

        usuario_id = cursor.lastrowid

    registrar_log("INFO", f"Usuário '{login_normalizado}' cadastrado com perfil '{perfil}'.")

    return Usuario(
        id=usuario_id,
        nome_completo=nome_completo,
        login=login_normalizado,
        senha_hash=senha_hash,
        perfil=perfil,
        ativo=True,
        data_criacao=data_criacao,
        ultimo_login=None,
    )


def obter_usuario_por_login(login: str) -> Optional[Usuario]:
    """Busca um usuário pelo login (utilizado no processo de autenticação).

    A comparação é normalizada (espaços removidos e minúsculas) para
    corresponder à forma como o login é armazenado em ``criar_usuario``,
    evitando falhas de login por diferença de maiúsculas/minúsculas.
    """
    login_normalizado = (login or "").strip().lower()
    with get_connection() as conexao:
        linha = conexao.execute(
            "SELECT * FROM usuarios WHERE login = ?", (login_normalizado,)
        ).fetchone()
    return Usuario.from_row(linha) if linha else None


def obter_usuario_por_id(usuario_id: int) -> Optional[Usuario]:
    """Busca um usuário pelo id (utilizado para carregar a sessão logada)."""
    with get_connection() as conexao:
        linha = conexao.execute(
            "SELECT * FROM usuarios WHERE id = ?", (usuario_id,)
        ).fetchone()
    return Usuario.from_row(linha) if linha else None


def listar_usuarios() -> List[Dict]:
    """Retorna todos os usuários cadastrados (sem o hash de senha), para a
    tela de administração de usuários."""
    with get_connection() as conexao:
        linhas = conexao.execute("SELECT * FROM usuarios ORDER BY nome_completo ASC").fetchall()
    return [Usuario.from_row(linha).to_dict_publico() for linha in linhas]


def atualizar_ultimo_login(usuario_id: int) -> None:
    """Atualiza o timestamp de último login do usuário."""
    with get_connection() as conexao:
        conexao.execute(
            "UPDATE usuarios SET ultimo_login = ? WHERE id = ?",
            (_agora_iso(), usuario_id),
        )
        conexao.commit()


def definir_perfil_usuario(usuario_id: int, perfil: str) -> bool:
    """Altera o perfil (admin/atendente) de um usuário. Apenas
    administradores podem chamar esta operação (validado em app.py)."""
    if perfil not in PerfilUsuario.TODOS:
        raise ValueError(f"Perfil inválido: {perfil}")

    with get_connection() as conexao:
        cursor = conexao.execute(
            "UPDATE usuarios SET perfil = ? WHERE id = ?", (perfil, usuario_id)
        )
        conexao.commit()
        alterou = cursor.rowcount > 0

    if alterou:
        registrar_log("WARNING", f"Perfil do usuário id={usuario_id} alterado para '{perfil}'.")
    return alterou


def definir_status_usuario(usuario_id: int, ativo: bool) -> bool:
    """Ativa ou desativa o acesso de um usuário ao sistema (sem excluir o
    cadastro, preservando o histórico de senhas emitidas/chamadas)."""
    with get_connection() as conexao:
        cursor = conexao.execute(
            "UPDATE usuarios SET ativo = ? WHERE id = ?", (1 if ativo else 0, usuario_id)
        )
        conexao.commit()
        alterou = cursor.rowcount > 0

    if alterou:
        estado = "ativado" if ativo else "desativado"
        registrar_log("WARNING", f"Usuário id={usuario_id} {estado}.")
    return alterou


def resetar_senha_usuario(usuario_id: int, nova_senha_hash: str) -> bool:
    """
    Reseta (redefine) a senha de login de um usuário, gravando o novo
    hash informado. Esta é a operação de "reset de senha" exigida para o
    administrador do sistema — não deve ser confundida com o reinício do
    contador de numeração de senhas de atendimento (``reiniciar_contador``).
    """
    with get_connection() as conexao:
        cursor = conexao.execute(
            "UPDATE usuarios SET senha_hash = ? WHERE id = ?",
            (nova_senha_hash, usuario_id),
        )
        conexao.commit()
        alterou = cursor.rowcount > 0

    if alterou:
        registrar_log("WARNING", f"Senha do usuário id={usuario_id} foi redefinida por um administrador.")
    return alterou


def resetar_senhas_emitidas() -> None:
    """
    Apaga TODO o histórico de senhas emitidas e de eventos de chamada,
    reiniciando também o contador de numeração para zero.

    Esta é uma operação destrutiva e irreversível, disponível apenas para
    administradores (validado em app.py), útil por exemplo no início de um
    novo evento/feirão, quando se deseja começar do zero sem nenhum
    resquício de dados do evento anterior.
    """
    with get_connection() as conexao:
        conexao.execute("DELETE FROM eventos_chamada")
        conexao.execute("DELETE FROM senhas")
        conexao.execute("DELETE FROM sqlite_sequence WHERE name IN ('senhas', 'eventos_chamada')")
        conexao.commit()

    with _lock:
        config_manager.salvar({"contador_atual": 0})

    registrar_log("WARNING", "TODAS as senhas emitidas e eventos de chamada foram apagados por um administrador.")


# ---------------------------------------------------------------------------
# Ocupação de guichês
# ---------------------------------------------------------------------------

def obter_guiche_do_usuario(usuario_id: int) -> Optional[int]:
    """Retorna o número do guichê atualmente ocupado por um usuário, ou
    ``None`` caso ele não esteja ocupando nenhum guichê no momento."""
    with get_connection() as conexao:
        linha = conexao.execute(
            "SELECT guiche FROM guiches_ocupados WHERE usuario_id = ?", (usuario_id,)
        ).fetchone()
    return int(linha["guiche"]) if linha else None


def ocupar_proximo_guiche_disponivel(usuario_id: int, usuario_nome: str, qtd_guiches: int) -> Optional[int]:
    """
    Atribui automaticamente ao usuário o primeiro guichê disponível (entre
    1 e ``qtd_guiches``), implementando o requisito de que o usuário
    logado assume um guichê disponível sem necessidade de seleção manual.

    Se o usuário já estiver ocupando um guichê, retorna o mesmo guichê
    (idempotente — não ocupa um segundo guichê para o mesmo usuário).
    Retorna ``None`` se não houver nenhum guichê livre no momento.
    """
    with _lock:
        guiche_atual = obter_guiche_do_usuario(usuario_id)
        if guiche_atual is not None:
            return guiche_atual

        with get_connection() as conexao:
            ocupados = {
                linha["guiche"]
                for linha in conexao.execute("SELECT guiche FROM guiches_ocupados").fetchall()
            }

            guiche_livre = next(
                (numero for numero in range(1, qtd_guiches + 1) if numero not in ocupados),
                None,
            )

            if guiche_livre is None:
                return None

            conexao.execute(
                """
                INSERT INTO guiches_ocupados (guiche, usuario_id, usuario_nome, ocupado_desde)
                VALUES (?, ?, ?, ?)
                """,
                (guiche_livre, usuario_id, usuario_nome, _agora_iso()),
            )
            conexao.commit()

    registrar_log("INFO", f"Guichê {guiche_livre} atribuído automaticamente a '{usuario_nome}'.")
    return guiche_livre


def liberar_guiche(usuario_id: int) -> None:
    """Libera o guichê ocupado por um usuário (chamado no logout)."""
    with get_connection() as conexao:
        conexao.execute("DELETE FROM guiches_ocupados WHERE usuario_id = ?", (usuario_id,))
        conexao.commit()


def listar_guiches_ocupados() -> List[Dict]:
    """Retorna a lista de guichês atualmente ocupados, útil para telas de
    administração/monitoramento do atendimento."""
    with get_connection() as conexao:
        linhas = conexao.execute(
            "SELECT * FROM guiches_ocupados ORDER BY guiche ASC"
        ).fetchall()
    return [dict(linha) for linha in linhas]
