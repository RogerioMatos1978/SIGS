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
from models import ChamadaEvento, Senha, StatusSenha

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

        # Índices para acelerar as consultas mais frequentes.
        conexao.execute("CREATE INDEX IF NOT EXISTS idx_senhas_status ON senhas (status)")
        conexao.execute(
            "CREATE INDEX IF NOT EXISTS idx_eventos_data ON eventos_chamada (data_hora)"
        )

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
