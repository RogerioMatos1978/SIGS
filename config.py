# -*- coding: utf-8 -*-
"""
config.py
=========

Módulo central de configuração do SIGS (Sistema Integrado de Gerenciamento
de Senhas).

Responsabilidades deste módulo:
    - Carregar variáveis de ambiente (inclusive de um arquivo ``.env``, se
      existir) com os dados de conexão do PostgreSQL.
    - Definir os caminhos absolutos de arquivos e pastas usados pelo sistema
      (logs, arquivos estáticos, logotipo, chave de sessão).
    - Definir os valores padrão de configuração do sistema.
    - Fornecer a classe ``ConfigManager``, responsável por ler e gravar as
      configurações do sistema na tabela ``configuracoes`` do PostgreSQL.

Este módulo NÃO deve conter regras de negócio relacionadas à fila de senhas.
Essas regras ficam em ``database.py``. Aqui tratamos apenas de parâmetros
gerais do sistema (nome do evento, impressora, logotipo, cores, etc.) e da
configuração de acesso ao banco de dados.

Banco de dados: desde esta versão, o SIGS usa PostgreSQL (antes usava
SQLite). O banco roda em um container Docker (ver docker-compose.yml na
raiz do projeto), enquanto o aplicativo Flask continua rodando
nativamente no Windows — isso é necessário porque a impressão de tickets
depende de pywin32 (win32print/win32ui), que só funciona em Windows
nativo, não dentro de um container Linux.
"""

import logging
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Carregamento do arquivo .env (se existir)
# ---------------------------------------------------------------------------
#
# python-dotenv é opcional: se não estiver instalado, o sistema continua
# funcionando normalmente usando apenas variáveis de ambiente já definidas
# no sistema operacional (ou os valores padrão abaixo). Isso evita quebrar
# a inicialização do app por causa de uma dependência de conveniência.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - ambiente sem python-dotenv instalado
    pass

import os

# ---------------------------------------------------------------------------
# Caminhos base do projeto
# ---------------------------------------------------------------------------

# Diretório raiz do projeto (pasta onde este arquivo está localizado).
BASE_DIR = Path(__file__).resolve().parent

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
# Conexão com o PostgreSQL
# ---------------------------------------------------------------------------
#
# Os valores padrão abaixo correspondem exatamente aos padrões definidos em
# ".env.example" / "docker-compose.yml", para que "docker compose up -d"
# seguido de "python app.py" funcione sem nenhuma configuração adicional em
# ambiente de desenvolvimento. Em produção, defina essas variáveis (por
# exemplo, em um arquivo ".env" real, nunca versionado) com credenciais
# fortes e específicas do ambiente.

DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
DB_NAME = os.environ.get("POSTGRES_DB", "sigs")
DB_USER = os.environ.get("POSTGRES_USER", "sigs")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "sigs")

# Quantas vezes tentar conectar ao banco na inicialização antes de desistir,
# e quantos segundos esperar entre tentativas. Útil porque o container do
# PostgreSQL pode levar alguns segundos para ficar pronto para aceitar
# conexões após "docker compose up" (mesmo com o healthcheck configurado).
DB_TENTATIVAS_CONEXAO = int(os.environ.get("SIGS_DB_TENTATIVAS", "10"))
DB_INTERVALO_TENTATIVAS_SEGUNDOS = float(os.environ.get("SIGS_DB_INTERVALO_SEGUNDOS", "2"))

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
# Conexão psycopg2 com espera/retentativa (o container do Postgres pode
# ainda estar inicializando quando o app sobe)
# ---------------------------------------------------------------------------

def conectar_com_retentativas():
    """
    Abre uma conexão psycopg2 com o PostgreSQL, tentando novamente algumas
    vezes em caso de falha (``DB_TENTATIVAS_CONEXAO`` vezes, aguardando
    ``DB_INTERVALO_TENTATIVAS_SEGUNDOS`` segundos entre cada tentativa).

    Isso evita que o app.py falhe imediatamente ao subir logo após
    ``docker compose up`` — o container do PostgreSQL relata como "healthy"
    assim que aceita conexões, mas ainda pode haver uma pequena janela de
    latência entre o container subir e o app tentar se conectar.

    Levanta a última exceção de conexão se todas as tentativas falharem.
    """
    # Importação local: evita exigir psycopg2 apenas para importar config.py
    # (por exemplo, em scripts que só leem outras configurações).
    import psycopg2
    import psycopg2.extras

    ultimo_erro = None
    for tentativa in range(1, DB_TENTATIVAS_CONEXAO + 1):
        try:
            conexao = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                cursor_factory=psycopg2.extras.RealDictCursor,
            )
            if tentativa > 1:
                logger.info("Conexão com o PostgreSQL estabelecida na tentativa %d.", tentativa)
            return conexao
        except psycopg2.OperationalError as erro:
            ultimo_erro = erro
            logger.warning(
                "Falha ao conectar ao PostgreSQL (tentativa %d/%d): %s",
                tentativa,
                DB_TENTATIVAS_CONEXAO,
                erro,
            )
            if tentativa < DB_TENTATIVAS_CONEXAO:
                time.sleep(DB_INTERVALO_TENTATIVAS_SEGUNDOS)

    logger.error(
        "Não foi possível conectar ao PostgreSQL após %d tentativas. "
        "Verifique se o container está rodando ('docker compose up -d') e "
        "se as variáveis POSTGRES_HOST/PORT/DB/USER/PASSWORD estão corretas.",
        DB_TENTATIVAS_CONEXAO,
    )
    raise ultimo_erro


# ---------------------------------------------------------------------------
# Gerenciador de configurações
# ---------------------------------------------------------------------------

class ConfigManager:
    """
    Responsável por ler e gravar as configurações do sistema, persistidas na
    tabela ``configuracoes`` (chave/valor) do PostgreSQL.

    A classe garante que a tabela exista e esteja populada com os valores
    padrão na primeira execução, evitando erros de chave inexistente.
    """

    def __init__(self) -> None:
        self._inicializar_tabela()

    # -- Infraestrutura -----------------------------------------------------

    def _conectar(self):
        """Abre uma conexão com o PostgreSQL (com retentativas)."""
        return conectar_com_retentativas()

    def _inicializar_tabela(self) -> None:
        """Cria a tabela de configurações (se necessário) e popula os
        valores padrão que ainda não existirem."""
        conexao = self._conectar()
        try:
            with conexao.cursor() as cursor:
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {TABELA_CONFIGURACOES} (
                        chave TEXT PRIMARY KEY,
                        valor TEXT NOT NULL
                    )
                    """
                )
                for chave, valor in CONFIGURACOES_PADRAO.items():
                    cursor.execute(
                        f"""
                        INSERT INTO {TABELA_CONFIGURACOES} (chave, valor)
                        VALUES (%s, %s)
                        ON CONFLICT (chave) DO NOTHING
                        """,
                        (chave, valor),
                    )
            conexao.commit()
        finally:
            conexao.close()

    # -- Leitura --------------------------------------------------------------

    def obter(self, chave: str, padrao: Optional[Any] = None) -> Any:
        """Retorna o valor de uma única chave de configuração, já convertido
        para o tipo apropriado (int quando aplicável)."""
        conexao = self._conectar()
        try:
            with conexao.cursor() as cursor:
                cursor.execute(
                    f"SELECT valor FROM {TABELA_CONFIGURACOES} WHERE chave = %s",
                    (chave,),
                )
                linha = cursor.fetchone()
        finally:
            conexao.close()

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
        conexao = self._conectar()
        try:
            with conexao.cursor() as cursor:
                cursor.execute(f"SELECT chave, valor FROM {TABELA_CONFIGURACOES}")
                linhas = cursor.fetchall()
        finally:
            conexao.close()

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
        conexao = self._conectar()
        try:
            with conexao.cursor() as cursor:
                for chave, valor in dados.items():
                    if chave not in chaves_validas:
                        logger.warning("Tentativa de gravar configuração desconhecida: %s", chave)
                        continue
                    cursor.execute(
                        f"""
                        INSERT INTO {TABELA_CONFIGURACOES} (chave, valor)
                        VALUES (%s, %s)
                        ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor
                        """,
                        (chave, str(valor)),
                    )
            conexao.commit()
        finally:
            conexao.close()
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
