# -*- coding: utf-8 -*-
"""
config.py
=========

Módulo central de configuração do SIGS (Sistema Integrado de Gerenciamento
de Senhas).

Responsabilidades deste módulo:
    - Definir os caminhos absolutos de arquivos e pastas usados pelo sistema
      (banco de dados, logs, arquivos estáticos, logotipo).
    - Definir os valores padrão de configuração do sistema.
    - Fornecer a classe ``ConfigManager``, responsável por ler e gravar as
      configurações do sistema na tabela ``configuracoes`` do banco SQLite.

Este módulo NÃO deve conter regras de negócio relacionadas à fila de senhas.
Essas regras ficam em ``database.py``. Aqui tratamos apenas de parâmetros
gerais do sistema (nome do evento, impressora, logotipo, cores, etc.).
"""

import secrets
import sqlite3
import logging
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Caminhos base do projeto
# ---------------------------------------------------------------------------

# Diretório raiz do projeto (pasta onde este arquivo está localizado).
BASE_DIR = Path(__file__).resolve().parent

# Diretório e arquivo do banco de dados SQLite.
DATABASE_DIR = BASE_DIR / "database"
DATABASE_PATH = DATABASE_DIR / "senhas.db"

# Diretórios estáticos e de templates (usados pelo Flask).
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
IMG_DIR = STATIC_DIR / "img"

# Logotipo padrão do SENAI (o usuário deve substituir pelo arquivo oficial).
LOGO_PADRAO = "static/img/logo.png"

# Arquivo de log de aplicação (eventos técnicos, erros, exceções).
LOG_FILE = BASE_DIR / "sigs.log"

# Arquivo que armazena a chave secreta usada para assinar as sessões
# (cookies) do Flask. É gerada automaticamente na primeira execução e
# reaproveitada nas execuções seguintes, para que sessões de login não
# sejam invalidadas a cada reinício do servidor.
SECRET_KEY_FILE = BASE_DIR / "secret.key"

# ---------------------------------------------------------------------------
# Nome da tabela de configurações e valores padrão
# ---------------------------------------------------------------------------

TABELA_CONFIGURACOES = "configuracoes"

# Valores padrão utilizados na primeira execução do sistema (quando a tabela
# de configurações ainda está vazia). Cada valor é armazenado como texto no
# banco de dados e convertido para o tipo apropriado na leitura.
CONFIGURACOES_PADRAO: Dict[str, str] = {
    "nome_evento": "Feirão do Emprego",
    "nome_impressora": "",             # vazio = usar impressora padrão do Windows
    "logo_path": LOGO_PADRAO,
    "qtd_senhas_exibidas": "10",       # quantidade de senhas exibidas no painel
    "tempo_atualizacao_ms": "2000",    # intervalo de atualização do painel (ms)
    "cor_principal": "#003C71",        # azul institucional SENAI
    "contador_atual": "0",             # último número de senha emitido
    "qtd_guiches": "5",                 # quantidade de guichês de atendimento disponíveis
}

# Chaves que devem ser tratadas como números inteiros na leitura.
CHAVES_INTEIRAS = {
    "qtd_senhas_exibidas",
    "tempo_atualizacao_ms",
    "contador_atual",
    "qtd_guiches",
}

# ---------------------------------------------------------------------------
# Logger da aplicação
# ---------------------------------------------------------------------------

def configurar_logger() -> logging.Logger:
    """
    Configura e retorna o logger principal da aplicação SIGS.

    O logger grava simultaneamente em arquivo (sigs.log) e no console,
    permitindo tanto auditoria posterior quanto acompanhamento em tempo
    real durante a execução do servidor Flask.
    """
    logger = logging.getLogger("SIGS")

    # Evita duplicar handlers caso a função seja chamada mais de uma vez
    # (por exemplo, quando o Flask reinicia em modo debug/reloader).
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formato = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler de arquivo.
    handler_arquivo = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    handler_arquivo.setFormatter(formato)
    logger.addHandler(handler_arquivo)

    # Handler de console.
    handler_console = logging.StreamHandler()
    handler_console.setFormatter(formato)
    logger.addHandler(handler_console)

    return logger


logger = configurar_logger()


# ---------------------------------------------------------------------------
# Gerenciador de configurações
# ---------------------------------------------------------------------------

class ConfigManager:
    """
    Responsável por ler e gravar as configurações do sistema, persistidas na
    tabela ``configuracoes`` (chave/valor) do banco SQLite.

    A classe garante que a tabela exista e esteja populada com os valores
    padrão na primeira execução, evitando erros de chave inexistente.
    """

    def __init__(self, db_path: Path = DATABASE_PATH) -> None:
        self.db_path = db_path
        DATABASE_DIR.mkdir(parents=True, exist_ok=True)
        self._inicializar_tabela()

    # -- Infraestrutura -----------------------------------------------------

    def _conectar(self) -> sqlite3.Connection:
        """Abre uma conexão curta com o banco de dados SQLite."""
        conexao = sqlite3.connect(str(self.db_path), timeout=10, check_same_thread=False)
        conexao.row_factory = sqlite3.Row
        return conexao

    def _inicializar_tabela(self) -> None:
        """Cria a tabela de configurações (se necessário) e popula os
        valores padrão que ainda não existirem."""
        with self._conectar() as conexao:
            conexao.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABELA_CONFIGURACOES} (
                    chave TEXT PRIMARY KEY,
                    valor TEXT NOT NULL
                )
                """
            )
            for chave, valor in CONFIGURACOES_PADRAO.items():
                conexao.execute(
                    f"INSERT OR IGNORE INTO {TABELA_CONFIGURACOES} (chave, valor) "
                    "VALUES (?, ?)",
                    (chave, valor),
                )
            conexao.commit()

    # -- Leitura --------------------------------------------------------------

    def obter(self, chave: str, padrao: Optional[Any] = None) -> Any:
        """Retorna o valor de uma única chave de configuração, já convertido
        para o tipo apropriado (int quando aplicável)."""
        with self._conectar() as conexao:
            linha = conexao.execute(
                f"SELECT valor FROM {TABELA_CONFIGURACOES} WHERE chave = ?",
                (chave,),
            ).fetchone()

        if linha is None:
            return padrao

        valor = linha["valor"]
        if chave in CHAVES_INTEIRAS:
            try:
                return int(valor)
            except (TypeError, ValueError):
                return padrao
        return valor

    def obter_todas(self) -> Dict[str, Any]:
        """Retorna um dicionário com todas as configurações do sistema,
        já com os tipos convertidos (inteiros ou texto)."""
        with self._conectar() as conexao:
            linhas = conexao.execute(
                f"SELECT chave, valor FROM {TABELA_CONFIGURACOES}"
            ).fetchall()

        resultado: Dict[str, Any] = {}
        for linha in linhas:
            chave, valor = linha["chave"], linha["valor"]
            if chave in CHAVES_INTEIRAS:
                try:
                    resultado[chave] = int(valor)
                except (TypeError, ValueError):
                    resultado[chave] = 0
            else:
                resultado[chave] = valor
        return resultado

    # -- Escrita --------------------------------------------------------------

    def salvar(self, dados: Dict[str, Any]) -> None:
        """
        Grava um conjunto de configurações no banco de dados.

        Apenas chaves conhecidas (presentes em ``CONFIGURACOES_PADRAO``) são
        aceitas, evitando a inserção de lixo arbitrário na tabela. Cada valor
        é convertido para string antes de ser persistido.
        """
        chaves_validas = set(CONFIGURACOES_PADRAO.keys())
        with self._conectar() as conexao:
            for chave, valor in dados.items():
                if chave not in chaves_validas:
                    logger.warning("Tentativa de gravar configuração desconhecida: %s", chave)
                    continue
                conexao.execute(
                    f"INSERT INTO {TABELA_CONFIGURACOES} (chave, valor) VALUES (?, ?) "
                    "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
                    (chave, str(valor)),
                )
            conexao.commit()
        logger.info("Configurações atualizadas: %s", list(dados.keys()))


# Instância única (singleton simples) utilizada por toda a aplicação.
config_manager = ConfigManager()


def obter_secret_key() -> str:
    """
    Retorna a chave secreta utilizada para assinar cookies de sessão do
    Flask (login), gerando uma nova chave aleatória e persistindo-a em
    ``secret.key`` na primeira execução do sistema.

    Manter a chave persistida (em vez de gerá-la em memória a cada
    execução) evita que todos os usuários sejam deslogados sempre que o
    servidor for reiniciado.
    """
    if SECRET_KEY_FILE.exists():
        chave = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
        if chave:
            return chave

    nova_chave = secrets.token_hex(32)
    SECRET_KEY_FILE.write_text(nova_chave, encoding="utf-8")
    logger.info("Nova chave secreta de sessão gerada em: %s", SECRET_KEY_FILE)
    return nova_chave
