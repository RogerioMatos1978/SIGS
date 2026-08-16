# -*- coding: utf-8 -*-
"""
resetar_sistema.py
===================

Script de linha de comando para "zerar" o SIGS antes do início de um novo
uso/evento, apagando TODOS os dados operacionais do sistema e mantendo
apenas os usuários com perfil Administrador.

O que este script APAGA:
    - Todas as senhas emitidas e todo o histórico de chamadas.
    - Todas as empresas cadastradas (e os arquivos de logo delas em
      static/img/empresas/).
    - Todos os usuários que NÃO forem administrador (atendente, emissor,
      recrutador).
    - Todos os guichês/mesas atualmente ocupados.
    - Todo o histórico de logs técnicos (tabela "logs" e o arquivo
      sigs.log).
    - Todas as Configurações do sistema, que voltam ao valor padrão de
      fábrica (nome do evento, cor, quantidade de guichês, frase do menu
      etc. — ver config.CONFIGURACOES_PADRAO).

O que este script MANTÉM:
    - TODOS os usuários com perfil Administrador (login e senha
      continuam funcionando normalmente após o reset).
    - O arquivo do banco de dados em si (database/senhas.db) e sua
      estrutura de tabelas — apenas os DADOS são apagados, não o banco.
    - A chave de sessão (secret.key) — ninguém precisa logar de novo por
      causa deste script (exceto os usuários que foram apagados).

Modo de uso:
    python resetar_sistema.py

    O script mostra exatamente o que será apagado (com contagens reais
    do banco de dados atual) e só prossegue depois que você digitar a
    frase de confirmação exibida na tela. Nada é apagado sem essa
    confirmação explícita.

Modo não interativo (para scripts de automação — use com MUITO cuidado):
    python resetar_sistema.py --forcar

ATENÇÃO: esta operação NÃO PODE ser desfeita. Se quiser manter um
registro do que existia antes, faça um backup de database/senhas.db
(ver seção 7 do README.md) antes de rodar este script.
"""

import argparse
import logging
import sys

import database
from config import CONFIGURACOES_PADRAO, STATIC_DIR, TABELA_CONFIGURACOES, config_manager
from models import PerfilUsuario

# Pasta onde ficam os arquivos de logo enviados para cada empresa (ver
# app.py:PASTA_LOGOS_EMPRESAS) — mesmo caminho usado lá, repetido aqui
# para não criar uma dependência deste script de dentro de app.py.
PASTA_LOGOS_EMPRESAS = STATIC_DIR / "img" / "empresas"

FRASE_CONFIRMACAO = "APAGAR TUDO"


def _contar(conexao, tabela: str, condicao: str = "") -> int:
    """Conta quantas linhas existem em uma tabela (opcionalmente com WHERE)."""
    sql = f"SELECT COUNT(*) AS total FROM {tabela} {condicao}"
    return conexao.execute(sql).fetchone()["total"]


def _mostrar_resumo_antes(conexao) -> list:
    """Mostra na tela o que será apagado e o que será mantido, com
    contagens reais do banco atual. Retorna a lista de logins dos
    administradores que serão preservados."""
    qtd_senhas = _contar(conexao, "senhas")
    qtd_eventos = _contar(conexao, "eventos_chamada")
    qtd_empresas = _contar(conexao, "empresas")
    qtd_logs = _contar(conexao, "logs")
    qtd_nao_admin = _contar(conexao, "usuarios", "WHERE perfil != 'admin'")

    admins = conexao.execute(
        "SELECT login, nome_completo FROM usuarios WHERE perfil = 'admin' ORDER BY login"
    ).fetchall()

    print("\nSerão APAGADOS permanentemente:")
    print(f"  - {qtd_senhas} senha(s) emitida(s) e {qtd_eventos} evento(s) de chamada")
    print(f"  - {qtd_empresas} empresa(s) cadastrada(s) (incluindo os logos em static/img/empresas/)")
    print(f"  - {qtd_nao_admin} usuário(s) que NÃO são administrador (atendente/emissor/recrutador)")
    print(f"  - {qtd_logs} registro(s) de log técnico (tabela 'logs' + arquivo sigs.log)")
    print("  - Todas as Configurações do sistema (voltam ao padrão de fábrica)")
    print("  - Todos os guichês/mesas atualmente ocupados")

    print("\nSerão MANTIDOS:")
    if admins:
        for admin in admins:
            print(f"  - Administrador: {admin['login']} ({admin['nome_completo']})")
    else:
        print("  - NENHUM administrador encontrado no banco de dados!")

    return [admin["login"] for admin in admins]


def _confirmar(forcar: bool) -> bool:
    if forcar:
        return True

    print(
        f"\nEsta ação NÃO PODE SER DESFEITA. Para confirmar, digite exatamente "
        f"a frase abaixo e pressione Enter:\n\n    {FRASE_CONFIRMACAO}\n"
    )
    resposta = input("Confirmação: ").strip()
    return resposta == FRASE_CONFIRMACAO


def _apagar_arquivos_de_logo() -> int:
    """Remove todos os arquivos de logo de empresas do disco. Retorna a
    quantidade de arquivos removidos."""
    if not PASTA_LOGOS_EMPRESAS.exists():
        return 0

    removidos = 0
    for arquivo in PASTA_LOGOS_EMPRESAS.iterdir():
        if arquivo.is_file():
            arquivo.unlink()
            removidos += 1
    return removidos


def _limpar_arquivo_de_log() -> None:
    """Esvazia o arquivo sigs.log. Fecha primeiro o handler de arquivo do
    logger "SIGS" (aberto em modo 'append' por config.configurar_logger),
    para evitar escrever no meio de um arquivo que acabou de ser
    truncado por outro processo/handle."""
    logger_sigs = logging.getLogger("SIGS")
    for handler in list(logger_sigs.handlers):
        if isinstance(handler, logging.FileHandler):
            caminho = handler.baseFilename
            handler.close()
            logger_sigs.removeHandler(handler)
            with open(caminho, "w", encoding="utf-8"):
                pass  # abrir em modo "w" já esvazia o arquivo


def resetar(forcar: bool = False) -> None:
    print("=" * 70)
    print("SIGS - Reset do sistema (manter apenas Administradores)")
    print("=" * 70)

    database.inicializar_banco()

    with database.get_connection() as conexao:
        admins_mantidos = _mostrar_resumo_antes(conexao)

        if not admins_mantidos:
            print(
                "\nNão há nenhum usuário Administrador cadastrado — abortando por "
                "segurança (rode 'python criar_admin.py' antes de resetar, para "
                "garantir que sobra pelo menos um acesso ao sistema)."
            )
            sys.exit(1)

        if not _confirmar(forcar):
            print("\nOperação cancelada. Nada foi apagado.")
            sys.exit(0)

        try:
            # Ordem importa: apaga sempre as tabelas "filhas" (com chave
            # estrangeira) antes das tabelas "pai" que elas referenciam,
            # já que o banco roda com "PRAGMA foreign_keys = ON".
            conexao.execute("DELETE FROM eventos_chamada")
            conexao.execute("DELETE FROM senhas")
            conexao.execute("DELETE FROM guiches_ocupados")
            conexao.execute("DELETE FROM guiches_empresa_ocupados")
            conexao.execute("DELETE FROM usuarios WHERE perfil != ?", (PerfilUsuario.ADMIN,))
            conexao.execute("DELETE FROM empresas")
            conexao.execute("DELETE FROM logs")

            # Reinicia a numeração (autoincrement) das tabelas totalmente
            # esvaziadas, para que o sistema "pareça" recém-instalado (a
            # próxima senha/empresa/log criado volta a ter id=1). A tabela
            # "usuarios" fica de fora porque ainda restam administradores
            # nela — mexer no contador dela poderia causar conflito de id.
            conexao.execute(
                "DELETE FROM sqlite_sequence WHERE name IN "
                "('senhas', 'eventos_chamada', 'empresas', 'logs')"
            )

            conexao.commit()
        except Exception:
            conexao.rollback()
            raise

    # Configurações: apaga tudo e regrava os valores padrão de fábrica.
    # (usa a mesma conexão/tabela que config.ConfigManager usa internamente)
    with config_manager._conectar() as conexao_config:  # noqa: SLF001 - reuso interno deliberado
        conexao_config.execute(f"DELETE FROM {TABELA_CONFIGURACOES}")
        conexao_config.commit()
    config_manager.salvar(CONFIGURACOES_PADRAO)

    qtd_logos_removidos = _apagar_arquivos_de_logo()
    _limpar_arquivo_de_log()

    print("\n" + "=" * 70)
    print("Reset concluído com sucesso!")
    print("=" * 70)
    print(f"\n  - {qtd_logos_removidos} arquivo(s) de logo removido(s) de static/img/empresas/")
    print("  - Configurações restauradas para o padrão de fábrica")
    print("  - Arquivo sigs.log esvaziado")
    print(f"  - Administrador(es) preservado(s): {', '.join(admins_mantidos)}")
    print(
        "\nO sistema está pronto para um novo início de uso. Cadastre as "
        "empresas do novo feirão em /admin/empresas e os usuários "
        "(atendente/emissor/recrutador) em /admin/usuarios."
    )
    print(
        "\nAtenção: se o servidor (wsgi.py/dev.py) estiver rodando agora, "
        "sessões de usuários que foram apagados serão encerradas "
        "automaticamente na próxima requisição deles — não é necessário "
        "reiniciar o servidor para isso."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apaga todos os dados operacionais do SIGS (senhas, empresas, "
            "usuários não-administradores, logs e configurações), mantendo "
            "apenas os usuários com perfil Administrador."
        )
    )
    parser.add_argument(
        "--forcar",
        action="store_true",
        help=(
            "Pula a confirmação interativa (use apenas em scripts de "
            "automação — não recomendado para uso manual)."
        ),
    )
    argumentos = parser.parse_args()
    resetar(forcar=argumentos.forcar)


if __name__ == "__main__":
    main()
