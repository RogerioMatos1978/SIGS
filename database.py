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
        id, numero, status, data_hora, guiche, usuario, empresa

    eventos_chamada
        id, senha_id, numero, guiche, usuario, data_hora
        (cada chamada ou repetição de chamada gera uma nova linha aqui)

    logs
        id, data_hora, nivel, mensagem

    usuarios
        id, nome_completo, login, senha_hash, perfil, ativo, data_criacao,
        ultimo_login, empresa_id
        (``empresa_id`` só é usado pelo perfil "recrutador" — vincula o
        usuário a UMA empresa da tabela ``empresas``, ver seção de
        Usuários mais abaixo)

    guiches_ocupados
        guiche, usuario_id, usuario_nome, ocupado_desde
        (pool GERAL de guichês, usado pelo perfil "atendente")

    guiches_empresa_ocupados
        empresa_id, guiche, usuario_id, usuario_nome, ocupado_desde
        (pool de mesas/guichês POR EMPRESA, usado pelo perfil
        "recrutador" — independente do pool geral acima; dois recrutadores
        de empresas diferentes podem ocupar o mesmo número de mesa sem
        conflito, pois a chave primária é o par (empresa_id, guiche))

    empresas
        id, nome, ativa, data_criacao, logo_path, cor_principal,
        contador_atual
        (empresas participantes do feirão do emprego; o nome é gravado
        como texto na própria senha no momento da emissão — ver
        criar_empresa/listar_empresas mais abaixo. ``contador_atual``
        guarda o ÚLTIMO número de senha emitido PARA AQUELA EMPRESA —
        cada empresa tem sua própria sequência independente 001, 002,
        003... ver criar_senha/reiniciar_contador_empresa mais abaixo)
"""

import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, Generator, List, Optional

from config import DATABASE_DIR, DATABASE_PATH, config_manager, logger
from models import ChamadaEvento, Empresa, PerfilUsuario, Senha, StatusSenha, Usuario

# Lock utilizado para proteger operações que precisam ser atômicas mesmo
# quando o servidor Flask é executado em modo threaded=True (várias
# requisições simultâneas). O SQLite já serializa escritas no nível do
# arquivo, mas o lock evita condições de corrida na lógica de aplicação
# (por exemplo, ler o contador, incrementar e gravar) — usado por
# ``chamar_proxima``/``reiniciar_contador*`` (operações raras, disparadas
# por ação humana, não por polling; o custo de serializar globalmente é
# desprezível para elas).
_lock = threading.Lock()

# Locks SEPARADOS por empresa, usados especificamente por ``criar_senha``.
# Diferente do ``_lock`` acima (compartilhado por tudo), cada empresa tem
# sua PRÓPRIA sequência de numeração (``empresas.contador_atual``) desde
# que o contador deixou de ser global — não faz sentido a emissão de
# senha da Empresa A esperar a Empresa B terminar de emitir a dela. Em um
# feirão com vários totens emitindo simultaneamente para empresas
# diferentes (o cenário mais comum no início do evento), um lock global
# aqui seria um gargalo de throughput desnecessário.
_locks_contador_empresa: Dict[int, threading.Lock] = {}
_lock_registro_locks_empresa = threading.Lock()


def _lock_da_empresa(empresa_id: int) -> threading.Lock:
    """Retorna (criando se necessário) o lock exclusivo de uma empresa,
    usado para proteger o incremento atômico do contador dela em
    ``criar_senha``."""
    with _lock_registro_locks_empresa:
        lock_empresa = _locks_contador_empresa.get(empresa_id)
        if lock_empresa is None:
            lock_empresa = threading.Lock()
            _locks_contador_empresa[empresa_id] = lock_empresa
        return lock_empresa


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
                -- Identificador compartilhado por TODOS os eventos criados na
                -- MESMA operação de chamada (uma chamada única = lote de 1
                -- senha; "Chamar Selecionadas" = lote com várias senhas de
                -- uma vez — ver chamar_varias/chamar_proxima). Usado por
                -- obter_chamada_atual para montar a "sequência chamada"
                -- exibida no Painel Público, sem misturar lotes de
                -- operações diferentes nem de empresas diferentes.
                lote_chamada TEXT,
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

        # Empresas participantes do feirão do emprego. Cadastradas apenas
        # por um administrador (tela /admin/empresas); selecionadas
        # obrigatoriamente no momento da emissão de cada senha (ver
        # api_emitir em app.py) e impressas no próprio ticket. Criada ANTES
        # de "usuarios" porque a tabela "usuarios" abaixo referencia
        # "empresas" (coluna "empresa_id", usada pelo perfil "recrutador").
        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS empresas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE,
                ativa INTEGER NOT NULL DEFAULT 1,
                data_criacao TEXT NOT NULL,
                logo_path TEXT,
                cor_principal TEXT,
                contador_atual INTEGER NOT NULL DEFAULT 0
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
        #
        # "empresa_id" só é usado pelo perfil "recrutador": vincula o
        # usuário a UMA empresa (ver models.Usuario e a seção "Usuários"
        # mais abaixo). Fica NULL para os demais perfis.
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
                ultimo_login TEXT,
                empresa_id INTEGER REFERENCES empresas (id)
            )
            """
        )

        # Ocupação de guichês da fila GERAL: cada guichê (1..N, N definido
        # em Configurações) só pode estar associado a um usuário "atendente"
        # logado por vez. A linha é removida quando o usuário efetua logout.
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

        # Ocupação de guichês (mesas) POR EMPRESA: pool independente do
        # geral acima, usado pelo perfil "recrutador". A chave primária é o
        # par (empresa_id, guiche), permitindo que a mesma numeração de
        # mesa seja reutilizada por empresas diferentes simultaneamente
        # (ex.: "Mesa 01" da Empresa A e "Mesa 01" da Empresa B não
        # conflitam entre si). A linha é removida quando o recrutador
        # efetua logout.
        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS guiches_empresa_ocupados (
                empresa_id INTEGER NOT NULL,
                guiche INTEGER NOT NULL,
                usuario_id INTEGER NOT NULL,
                usuario_nome TEXT NOT NULL,
                ocupado_desde TEXT NOT NULL,
                PRIMARY KEY (empresa_id, guiche),
                FOREIGN KEY (empresa_id) REFERENCES empresas (id),
                FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
            )
            """
        )

        # Índices para acelerar as consultas mais frequentes.
        #
        # IMPORTANTE: o índice de "usuarios.empresa_id" NÃO é criado aqui —
        # em um banco de dados antigo (anterior ao perfil "recrutador"), a
        # coluna "empresa_id" ainda não existe neste ponto (a tabela já
        # existia, então "CREATE TABLE IF NOT EXISTS" acima não a recriou
        # com a coluna nova). Esse índice só é criado mais abaixo, DEPOIS
        # de "_migrar_tabela_usuarios_adicionar_empresa_id" garantir que a
        # coluna existe — ver o final desta função.
        conexao.execute("CREATE INDEX IF NOT EXISTS idx_senhas_status ON senhas (status)")
        conexao.execute(
            "CREATE INDEX IF NOT EXISTS idx_eventos_data ON eventos_chamada (data_hora)"
        )
        conexao.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_login ON usuarios (login)")
        conexao.execute("CREATE INDEX IF NOT EXISTS idx_empresas_ativa ON empresas (ativa)")

        conexao.commit()

        # Corrige bancos de dados criados por uma versão anterior do SIGS,
        # cuja tabela "usuarios" possuía uma restrição CHECK travando o
        # perfil em apenas admin/atendente. "CREATE TABLE IF NOT EXISTS"
        # não altera tabelas já existentes, então esse passo é necessário
        # para quem já usava o sistema antes do perfil "emissor" existir.
        _migrar_tabela_usuarios_sem_check(conexao)

        # Corrige um possível efeito colateral da migração acima: o SQLite
        # atualiza automaticamente a cláusula FOREIGN KEY de outras tabelas
        # quando a tabela referenciada é renomeada, o que podia deixar
        # "guiches_ocupados" apontando para o nome temporário usado durante
        # a migração (já removido) em vez de "usuarios". Ver docstring de
        # ``_reparar_guiches_ocupados`` para detalhes.
        _reparar_guiches_ocupados(conexao)

        # Adiciona a coluna "empresa" à tabela "senhas" em bancos de dados
        # criados antes da funcionalidade de empresas do feirão existir.
        _migrar_tabela_senhas_adicionar_empresa(conexao)

        # Adiciona a coluna "empresa_id" à tabela "usuarios" em bancos de
        # dados criados antes do perfil "recrutador" existir.
        _migrar_tabela_usuarios_adicionar_empresa_id(conexao)

        # Adiciona as colunas "logo_path"/"cor_principal" à tabela
        # "empresas" em bancos de dados criados antes da identidade visual
        # por empresa existir.
        _migrar_tabela_empresas_adicionar_identidade_visual(conexao)

        # Adiciona a coluna "contador_atual" à tabela "empresas" em bancos
        # de dados criados antes de cada empresa ter sua própria sequência
        # de numeração de senhas (antes disso, havia um único contador
        # global em "configuracoes").
        _migrar_tabela_empresas_adicionar_contador(conexao)

        # Adiciona a coluna "empresa_id" à tabela "senhas" (referência
        # ESTÁVEL à empresa, usada para escopo de fila e permissões do
        # recrutador — ver criar_senha e app.py:_pode_gerenciar_senha),
        # com backfill best-effort a partir do nome já gravado em
        # "empresa" para senhas emitidas antes desta migração existir.
        # Precisa rodar DEPOIS de "_migrar_tabela_senhas_adicionar_empresa"
        # (a coluna "empresa" precisa existir para o backfill funcionar).
        _migrar_tabela_senhas_adicionar_empresa_id(conexao)

        # Adiciona as colunas "hora_chamada" e "hora_finalizada" à tabela
        # "senhas" (marcos de tempo do ciclo de vida de uma senha, usados
        # pelos relatórios — ver ``chamar_proxima``/``finalizar_senha`` e
        # ``app.py:api_relatorios_*``).
        _migrar_tabela_senhas_adicionar_marcos_tempo(conexao)

        # Adiciona a coluna "nome_pessoa" à tabela "senhas" (campo opcional
        # preenchido pelo Emissor na emissão — ver api_emitir/index.html).
        _migrar_tabela_senhas_adicionar_nome_pessoa(conexao)

        # Adiciona a coluna "atendimento_finalizado_em" à tabela "empresas"
        # (nome histórico — renomeada para "emissao_bloqueada_em" logo
        # abaixo, ver _migrar_tabela_empresas_renomear_para_emissao_bloqueada).
        _migrar_tabela_empresas_adicionar_atendimento_finalizado(conexao)

        # Renomeia "atendimento_finalizado_em" para "emissao_bloqueada_em"
        # (a funcionalidade virou "Bloqueio de Emissão de Senhas" — ver
        # docstring da própria migração para o motivo).
        _migrar_tabela_empresas_renomear_para_emissao_bloqueada(conexao)

        # Adiciona a coluna "chave_acesso" à tabela "empresas" (chave
        # numérica de 8 dígitos que substitui a senha individual no login
        # do recrutador — ver app.py: rotas "/empresas/entrar" e
        # "/empresas/<id>/entrar"). Também gera a chave para qualquer
        # empresa que ainda esteja sem uma.
        _migrar_tabela_empresas_adicionar_chave_acesso(conexao)

        # Adiciona a coluna "provisionado_por_chave" à tabela "usuarios"
        # (marca contas de recrutador criadas automaticamente pelo novo
        # login por chave — ver provisionar_usuario_recrutador).
        _migrar_tabela_usuarios_adicionar_provisionado_por_chave(conexao)

        # Fecha a brecha de cadastrar duas empresas cujo nome só difere em
        # maiúsculas/minúsculas (ex.: "Empresa Alfa" e "empresa alfa").
        _migrar_indice_empresas_nome_nocase(conexao)

        # Adiciona a coluna "fixa" à tabela "empresas" (marca as duas
        # opções fixas de emissão "Criar Currículos"/"Imprimir
        # Currículos" — ver NOMES_EMPRESAS_FIXAS/_semear_empresas_fixas,
        # chamada mais abaixo, DEPOIS que esta migração garante a coluna).
        _migrar_tabela_empresas_adicionar_fixa(conexao)

        # Adiciona a coluna "lote_chamada" à tabela "eventos_chamada"
        # (identifica quais eventos pertencem à MESMA operação de chamada
        # — ver chamar_varias/chamar_proxima/obter_chamada_atual, usada
        # pelo botão "Chamar Selecionadas" da Fila de Espera).
        _migrar_tabela_eventos_chamada_adicionar_lote(conexao)

        # Só agora a coluna "empresa_id" está garantidamente presente
        # (bancos novos já a criam direto; bancos antigos acabaram de
        # recebê-la na migração acima), então é seguro criar o índice.
        conexao.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_empresa ON usuarios (empresa_id)")

        # Índices usados pelas consultas mais frequentes dos painéis
        # públicos (pollados a cada poucos segundos por cada painel
        # aberto): fila/chamada atual filtradas por empresa+status, e o
        # JOIN eventos_chamada -> senhas usado por obter_chamada_atual.
        conexao.execute("CREATE INDEX IF NOT EXISTS idx_senhas_empresa_status ON senhas (empresa_id, status)")
        conexao.execute("CREATE INDEX IF NOT EXISTS idx_eventos_senha_id ON eventos_chamada (senha_id)")
        # Usado por repetir_ultima_chamada para achar rapidamente a última
        # chamada de UM guichê/mesa específico (texto formatado, ex.:
        # "Mesa 01 — Empresa A"), sem varrer a tabela inteira.
        conexao.execute("CREATE INDEX IF NOT EXISTS idx_eventos_guiche ON eventos_chamada (guiche, id)")
        # Usado por obter_chamada_atual para buscar rapidamente todos os
        # eventos de um mesmo lote (chamada em conjunto de várias senhas).
        conexao.execute("CREATE INDEX IF NOT EXISTS idx_eventos_lote ON eventos_chamada (lote_chamada)")
        conexao.commit()

    # Garante que as duas empresas fixas do sistema existam — feito FORA
    # do "with" acima (abre sua própria conexão, como criar_empresa),
    # depois que a coluna "fixa" já foi garantida pela migração logo
    # acima.
    _semear_empresas_fixas()

    logger.info("Banco de dados inicializado em: %s", DATABASE_PATH)


def _migrar_tabela_usuarios_sem_check(conexao: sqlite3.Connection) -> None:
    """
    Detecta se a tabela ``usuarios`` foi criada com a antiga cláusula
    ``CHECK (perfil IN ('admin', 'atendente'))`` (presente em versões do
    SIGS anteriores à introdução do perfil "emissor") e, se for o caso,
    recria a tabela sem essa restrição, preservando todos os usuários já
    cadastrados.

    Sem esta migração, tentar criar ou promover um usuário para o perfil
    "emissor" em um banco de dados antigo falha com o erro:
    ``CHECK constraint failed: perfil IN ('admin', 'atendente')``.

    Esta função é chamada toda vez que o sistema inicia
    (``inicializar_banco``) e não faz nada se o banco já estiver no
    formato atual (sem a restrição), portanto é seguro executá-la
    repetidamente.

    IMPORTANTE: a tabela ``guiches_ocupados`` possui uma cláusula
    ``FOREIGN KEY (usuario_id) REFERENCES usuarios (id)``. Por padrão, o
    SQLite atualiza automaticamente essa referência quando a tabela
    "usuarios" é renomeada (comportamento ``legacy_alter_table = OFF``),
    o que deixaria ``guiches_ocupados`` apontando para o nome temporário
    usado durante a migração. Para evitar isso, desativamos
    temporariamente esse comportamento (``PRAGMA legacy_alter_table = ON``)
    e a checagem de chaves estrangeiras (``PRAGMA foreign_keys = OFF``)
    apenas durante a operação de renomear/recriar/copiar/remover, restau-
    rando ambas ao final.
    """
    linha = conexao.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'usuarios'"
    ).fetchone()

    if linha is None or not linha["sql"] or "CHECK" not in linha["sql"].upper():
        return  # Tabela não existe ainda, ou já está no formato novo.

    logger.warning(
        "Esquema antigo da tabela 'usuarios' detectado (restrição CHECK de "
        "perfil). Migrando automaticamente para permitir o perfil 'emissor'..."
    )

    conexao.execute("PRAGMA foreign_keys = OFF")
    conexao.execute("PRAGMA legacy_alter_table = ON")

    try:
        conexao.execute("ALTER TABLE usuarios RENAME TO usuarios_migracao_temp")

        conexao.execute(
            f"""
            CREATE TABLE usuarios (
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

        conexao.execute(
            """
            INSERT INTO usuarios (id, nome_completo, login, senha_hash, perfil, ativo, data_criacao, ultimo_login)
            SELECT id, nome_completo, login, senha_hash, perfil, ativo, data_criacao, ultimo_login
            FROM usuarios_migracao_temp
            """
        )

        conexao.execute("DROP TABLE usuarios_migracao_temp")
        conexao.commit()
    finally:
        conexao.execute("PRAGMA legacy_alter_table = OFF")
        conexao.execute("PRAGMA foreign_keys = ON")

    logger.warning(
        "Migração concluída: a tabela 'usuarios' agora aceita o perfil "
        "'emissor' sem necessidade de recriar o banco de dados."
    )


def _reparar_guiches_ocupados(conexao: sqlite3.Connection) -> None:
    """
    Corrige a tabela ``guiches_ocupados`` caso sua definição tenha ficado
    com uma referência de chave estrangeira quebrada, apontando para a
    tabela temporária "usuarios_migracao_temp" (que já foi removida) em
    vez de "usuarios".

    Isso podia ocorrer como efeito colateral de uma versão anterior desta
    migração, que renomeava a tabela "usuarios" sem desativar
    ``legacy_alter_table`` — o SQLite, por padrão, atualiza automaticamente
    as referências FOREIGN KEY de outras tabelas ao renomear a tabela
    referenciada. O sintoma é o erro
    ``OperationalError: no such table: main.usuarios_migracao_temp`` ao
    tentar inserir ou remover linhas de ``guiches_ocupados`` (por exemplo,
    ao fazer login ou logout).

    Como ``guiches_ocupados`` armazena apenas o estado transitório de
    "quem está em qual guichê agora" (é recriado a cada login e liberado a
    cada logout), é seguro recriá-la do zero sem qualquer perda de dados
    relevante — na pior hipótese, um usuário que estava com um guichê
    ocupado precisará relogar para assumir um guichê novamente.
    """
    linha = conexao.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'guiches_ocupados'"
    ).fetchone()

    if linha is None or not linha["sql"]:
        return  # Tabela ainda não existe; será criada normalmente.

    if "usuarios_migracao_temp" not in linha["sql"]:
        return  # Referência já está saudável (aponta para "usuarios").

    logger.warning(
        "Referência quebrada detectada na tabela 'guiches_ocupados' "
        "(apontava para uma tabela temporária de migração já removida). "
        "Recriando a tabela do zero..."
    )

    conexao.execute("PRAGMA foreign_keys = OFF")
    try:
        conexao.execute("DROP TABLE guiches_ocupados")
        conexao.execute(
            """
            CREATE TABLE guiches_ocupados (
                guiche INTEGER PRIMARY KEY,
                usuario_id INTEGER NOT NULL,
                usuario_nome TEXT NOT NULL,
                ocupado_desde TEXT NOT NULL,
                FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
            )
            """
        )
        conexao.commit()
    finally:
        conexao.execute("PRAGMA foreign_keys = ON")

    logger.warning("Tabela 'guiches_ocupados' recriada com sucesso.")


def _migrar_tabela_senhas_adicionar_empresa(conexao: sqlite3.Connection) -> None:
    """
    Adiciona a coluna ``empresa`` (TEXT, opcional) à tabela ``senhas`` em
    bancos de dados criados antes da funcionalidade de "Empresas do
    Feirão" existir.

    Diferente da migração de ``usuarios`` (que precisa recriar a tabela
    por causa da cláusula CHECK antiga), esta é uma simples adição de
    coluna: o SQLite suporta ``ALTER TABLE ... ADD COLUMN`` de forma
    direta e segura quando a nova coluna aceita valores nulos, preservando
    todas as senhas já emitidas (que simplesmente ficam com ``empresa =
    NULL``, exibido como "Não informado" nos relatórios).

    Não faz nada se a coluna já existir (portanto é seguro chamar esta
    função toda vez que o sistema inicia).
    """
    colunas = conexao.execute("PRAGMA table_info(senhas)").fetchall()
    nomes_colunas = {coluna["name"] for coluna in colunas}

    if "empresa" in nomes_colunas:
        return  # Já está no formato atual.

    logger.warning(
        "Esquema antigo da tabela 'senhas' detectado (sem a coluna "
        "'empresa'). Adicionando automaticamente..."
    )

    conexao.execute("ALTER TABLE senhas ADD COLUMN empresa TEXT")
    conexao.commit()

    logger.warning(
        "Migração concluída: a tabela 'senhas' agora possui a coluna "
        "'empresa'. Senhas emitidas antes desta atualização ficam sem "
        "empresa associada (exibidas como 'Não informado' nos relatórios)."
    )


def _migrar_tabela_senhas_adicionar_empresa_id(conexao: sqlite3.Connection) -> None:
    """
    Adiciona a coluna ``empresa_id`` (INTEGER, opcional) à tabela
    ``senhas``, e faz um backfill best-effort para as senhas já existentes.

    Por quê esta coluna existe além de ``empresa`` (texto): ``empresa``
    guarda apenas o NOME congelado no momento da emissão (correto para
    exibição/relatórios, mas FRÁGIL para controle de acesso — se uma
    empresa for renomeada e outro cadastro, novo ou existente, receber
    esse mesmo nome depois, uma comparação por nome passaria a enxergar
    senhas de uma empresa totalmente diferente). ``empresa_id`` é a
    referência ESTÁVEL usada a partir de agora para escopo de fila e
    permissões do recrutador (ver ``criar_senha`` e
    ``app.py:_pode_gerenciar_senha``) — ``empresa`` continua existindo,
    inalterado, só para exibição/relatórios.

    O backfill (``UPDATE ... SET empresa_id = (SELECT id FROM empresas
    WHERE nome = senhas.empresa)``) é best-effort: cobre corretamente o
    caso comum (nome da senha ainda bate com o nome atual da empresa),
    mas senhas antigas de uma empresa JÁ renomeada ficam com
    ``empresa_id = NULL`` (não há como recuperar com certeza a qual
    empresa pertenciam só pelo nome antigo) — elas simplesmente deixam de
    aparecer nos filtros por empresa daqui pra frente, sem quebrar nada
    (o valor de ``empresa`` em si nunca é apagado ou alterado).

    Não faz nada se a coluna já existir (portanto é seguro chamar esta
    função toda vez que o sistema inicia).
    """
    colunas = conexao.execute("PRAGMA table_info(senhas)").fetchall()
    nomes_colunas = {coluna["name"] for coluna in colunas}

    if "empresa_id" in nomes_colunas:
        return  # Já está no formato atual.

    logger.warning(
        "Esquema antigo da tabela 'senhas' detectado (sem a coluna "
        "'empresa_id'). Adicionando automaticamente..."
    )

    conexao.execute("ALTER TABLE senhas ADD COLUMN empresa_id INTEGER REFERENCES empresas (id)")
    conexao.execute(
        """
        UPDATE senhas
        SET empresa_id = (SELECT id FROM empresas WHERE empresas.nome = senhas.empresa)
        WHERE empresa_id IS NULL AND empresa IS NOT NULL
        """
    )
    conexao.commit()

    logger.warning(
        "Migração concluída: a tabela 'senhas' agora possui a coluna "
        "'empresa_id' (referência estável, usada para escopo de fila e "
        "permissões por empresa). Backfill best-effort aplicado a partir "
        "do nome já gravado em 'empresa'."
    )


def _migrar_tabela_senhas_adicionar_marcos_tempo(conexao: sqlite3.Connection) -> None:
    """
    Adiciona as colunas ``hora_chamada`` e ``hora_finalizada`` (ambas
    TEXT, opcionais) à tabela ``senhas``.

    ``hora_chamada`` é gravada uma única vez, no momento em que a senha é
    chamada pela PRIMEIRA vez (transição Emitida -> Chamada, ver
    ``chamar_proxima``) — repetições de chamada (``repetir_ultima_chamada``)
    NÃO alteram este valor, pois representam apenas um novo anúncio
    sonoro/visual da MESMA chamada, não uma nova chamada.

    ``hora_finalizada`` é gravada quando a senha é marcada como
    'Finalizada' (ver ``finalizar_senha``).

    As duas juntas permitem calcular o "tempo de atendimento" (hora
    finalizada − hora chamada) nos relatórios — ver
    ``app.py:_montar_linha_relatorio_emitidas``. Senhas canceladas
    exibem estes campos como vazios no relatório, independentemente do
    que estiver gravado aqui (ver a mesma função) — ex.: uma senha que
    chegou a ser chamada e depois foi cancelada em vez de finalizada
    mantém ``hora_chamada`` preenchida no banco (útil para auditoria),
    mas o relatório a trata como "sem atendimento" por estar cancelada.

    Senhas emitidas ANTES desta migração simplesmente ficam com os dois
    campos ``NULL`` (não há como reconstruir esses horários
    retroativamente a partir do histórico existente).

    Não faz nada se as colunas já existirem (portanto é seguro chamar
    esta função toda vez que o sistema inicia).
    """
    colunas = conexao.execute("PRAGMA table_info(senhas)").fetchall()
    nomes_colunas = {coluna["name"] for coluna in colunas}

    if "hora_chamada" in nomes_colunas and "hora_finalizada" in nomes_colunas:
        return  # Já está no formato atual.

    logger.warning(
        "Esquema antigo da tabela 'senhas' detectado (sem as colunas de "
        "marcos de tempo). Adicionando automaticamente..."
    )

    if "hora_chamada" not in nomes_colunas:
        conexao.execute("ALTER TABLE senhas ADD COLUMN hora_chamada TEXT")
    if "hora_finalizada" not in nomes_colunas:
        conexao.execute("ALTER TABLE senhas ADD COLUMN hora_finalizada TEXT")
    conexao.commit()

    logger.warning(
        "Migração concluída: a tabela 'senhas' agora possui as colunas "
        "'hora_chamada' e 'hora_finalizada', usadas para calcular o "
        "tempo de atendimento nos relatórios."
    )


def _migrar_tabela_senhas_adicionar_nome_pessoa(conexao: sqlite3.Connection) -> None:
    """
    Adiciona a coluna ``nome_pessoa`` (TEXT, opcional) à tabela ``senhas``.

    Preenchida OPCIONALMENTE pelo Emissor no momento da emissão (campo
    "Primeiro Nome", ver templates/index.html e app.py:api_emitir) —
    diferente de ``empresa``, nunca é obrigatória, então senhas sem nome
    de pessoa (seja por terem sido emitidas antes desta migração, seja
    porque o Emissor simplesmente deixou o campo em branco) ficam com
    ``NULL`` normalmente, sem gerar nenhum aviso especial nos relatórios
    (diferente do "Não informado" usado para ``empresa``).

    Impressa no ticket como "Nome: {nome_pessoa}" (ver
    printer.py:imprimir_senha) e preservada em reimpressões (ver
    app.py:api_reimprimir), já que fica gravada na própria senha.

    Não faz nada se a coluna já existir (portanto é seguro chamar esta
    função toda vez que o sistema inicia).
    """
    colunas = conexao.execute("PRAGMA table_info(senhas)").fetchall()
    nomes_colunas = {coluna["name"] for coluna in colunas}

    if "nome_pessoa" in nomes_colunas:
        return  # Já está no formato atual.

    logger.warning(
        "Esquema antigo da tabela 'senhas' detectado (sem a coluna "
        "'nome_pessoa'). Adicionando automaticamente..."
    )

    conexao.execute("ALTER TABLE senhas ADD COLUMN nome_pessoa TEXT")
    conexao.commit()

    logger.warning(
        "Migração concluída: a tabela 'senhas' agora possui a coluna "
        "'nome_pessoa' (campo opcional preenchido pelo Emissor na emissão)."
    )


def _migrar_tabela_empresas_adicionar_atendimento_finalizado(conexao: sqlite3.Connection) -> None:
    """
    Adiciona a coluna ``atendimento_finalizado_em`` (TEXT, opcional) à
    tabela ``empresas`` — nome histórico da funcionalidade que hoje se
    chama "Bloqueio de Emissão de Senhas" (ver
    ``_migrar_tabela_empresas_renomear_para_emissao_bloqueada``, chamada
    logo em seguida em ``inicializar_banco``, que renomeia esta coluna
    para ``emissao_bloqueada_em`` e é a que reflete o comportamento
    atual: ``bloquear_emissao_empresa``/``desbloquear_emissao_empresa``).
    Mantida como está, sem renomear a coluna aqui diretamente, apenas
    para preservar o histórico de como o schema evoluiu — bancos muito
    antigos passam primeiro por este nome antes de chegar ao atual.

    Não faz nada se a coluna já existir (portanto é seguro chamar esta
    função toda vez que o sistema inicia).
    """
    colunas = conexao.execute("PRAGMA table_info(empresas)").fetchall()
    nomes_colunas = {coluna["name"] for coluna in colunas}

    if "atendimento_finalizado_em" in nomes_colunas or "emissao_bloqueada_em" in nomes_colunas:
        # Segundo caso: a migração seguinte (_migrar_tabela_empresas_
        # renomear_para_emissao_bloqueada) já renomeou a coluna nesta
        # instalação — sem este check, esta função recriaria para sempre
        # uma coluna "atendimento_finalizado_em" vazia e órfã a cada
        # início do servidor, já que o nome antigo nunca mais existe
        # depois de renomeado.
        return  # Já está no formato atual (antigo ou novo).

    logger.warning(
        "Esquema antigo da tabela 'empresas' detectado (sem a coluna "
        "'atendimento_finalizado_em'). Adicionando automaticamente..."
    )

    conexao.execute("ALTER TABLE empresas ADD COLUMN atendimento_finalizado_em TEXT")
    conexao.commit()

    logger.warning(
        "Migração concluída: a tabela 'empresas' agora possui a coluna "
        "'atendimento_finalizado_em', usada para encerrar o atendimento "
        "do dia por empresa."
    )


def _migrar_tabela_empresas_renomear_para_emissao_bloqueada(conexao: sqlite3.Connection) -> None:
    """
    Renomeia a coluna ``atendimento_finalizado_em`` para
    ``emissao_bloqueada_em`` na tabela ``empresas``.

    Esta coluna nasceu com o significado de "encerrar o atendimento do
    dia" (bloqueava emissão E chamada de novas senhas, cancelando
    automaticamente as que ainda esperavam). A funcionalidade foi
    reformulada para "Bloqueio de Emissão de Senhas": agora impede APENAS
    novas emissões (ver ``app.py:api_emitir``) — o recrutador continua
    chamando/atendendo normalmente a fila já existente (ver
    ``bloquear_emissao_empresa``/``desbloquear_emissao_empresa``), e as
    senhas que já estavam esperando NÃO são mais canceladas
    automaticamente. O nome da coluna foi atualizado junto para refletir
    o novo significado e não confundir quem for ler o código depois.

    Sempre executada DEPOIS de
    ``_migrar_tabela_empresas_adicionar_atendimento_finalizado`` (que
    garante a existência da coluna, no nome antigo, em bancos bem
    antigos) — por isso trata três cenários:

        1. Banco já no formato atual (``emissao_bloqueada_em`` existe):
           não faz nada.
        2. Banco com o nome antigo (``atendimento_finalizado_em``,
           inclusive o do usuário em produção): renomeia a coluna,
           preservando os dados (empresas com atendimento já finalizado
           continuam com a emissão bloqueada após a migração).
        3. Banco sem nenhuma das duas colunas (não deveria acontecer,
           já que a migração anterior sempre roda antes — tratado apenas
           como salvaguarda): cria a coluna nova diretamente.

    Idempotente: seguro chamar toda vez que o sistema inicia.
    """
    colunas = conexao.execute("PRAGMA table_info(empresas)").fetchall()
    nomes_colunas = {coluna["name"] for coluna in colunas}

    if "emissao_bloqueada_em" in nomes_colunas:
        return  # Já está no formato atual.

    if "atendimento_finalizado_em" in nomes_colunas:
        logger.warning(
            "Renomeando coluna 'empresas.atendimento_finalizado_em' para "
            "'emissao_bloqueada_em' (Finalizar Atendimento do Dia virou "
            "Bloqueio de Emissão de Senhas)..."
        )
        conexao.execute(
            "ALTER TABLE empresas RENAME COLUMN atendimento_finalizado_em TO emissao_bloqueada_em"
        )
    else:  # pragma: no cover - salvaguarda, não deveria ocorrer na prática.
        conexao.execute("ALTER TABLE empresas ADD COLUMN emissao_bloqueada_em TEXT")

    conexao.commit()
    logger.warning(
        "Migração concluída: a tabela 'empresas' agora possui a coluna "
        "'emissao_bloqueada_em'."
    )


def _gerar_chave_acesso_unica(conexao: sqlite3.Connection) -> str:
    """
    Gera uma chave numérica de 8 dígitos (sempre com 8 algarismos, zeros à
    esquerda incluídos — ex.: "00734821") para o login do recrutador por
    empresa (ver ``criar_empresa``/``regenerar_chave_empresa``), garantindo
    que não colida com nenhuma chave já em uso por outra empresa.

    Usa ``secrets.randbelow`` (gerador criptograficamente forte) em vez de
    ``random``: esta chave funciona como uma credencial de acesso (substitui
    a senha individual do recrutador), então merece a mesma qualidade de
    aleatoriedade de uma senha/token, mesmo sendo curta.
    """
    for _ in range(50):  # colisão é extremamente rara (1 chance em 10^8)
        candidata = f"{secrets.randbelow(100_000_000):08d}"
        existe = conexao.execute(
            "SELECT 1 FROM empresas WHERE chave_acesso = ?", (candidata,)
        ).fetchone()
        if existe is None:
            return candidata
    raise RuntimeError(  # pragma: no cover - praticamente impossível de ocorrer
        "Não foi possível gerar uma chave de acesso única após várias tentativas."
    )


def _migrar_tabela_empresas_adicionar_chave_acesso(conexao: sqlite3.Connection) -> None:
    """
    Adiciona a coluna ``chave_acesso`` (TEXT, 8 dígitos) à tabela
    ``empresas`` — a chave numérica que substitui a senha individual no
    novo fluxo de acesso do recrutador (ver ``app.py``, rotas
    ``/empresas/entrar`` e ``/empresas/<id>/entrar``, e
    ``auth.autenticar_por_chave_empresa``).

    Toda empresa PRECISA de uma chave: além de adicionar a coluna (se ainda
    não existir), esta migração também gera e grava uma chave nova para
    qualquer empresa que esteja sem uma — seja por ter sido criada antes
    desta funcionalidade existir, seja por qualquer outro motivo.

    Idempotente: seguro chamar toda vez que o sistema inicia.
    """
    colunas = conexao.execute("PRAGMA table_info(empresas)").fetchall()
    nomes_colunas = {coluna["name"] for coluna in colunas}

    if "chave_acesso" not in nomes_colunas:
        logger.warning(
            "Esquema antigo da tabela 'empresas' detectado (sem a coluna "
            "'chave_acesso'). Adicionando automaticamente..."
        )
        conexao.execute("ALTER TABLE empresas ADD COLUMN chave_acesso TEXT")
        conexao.commit()
        logger.warning(
            "Migração concluída: a tabela 'empresas' agora possui a coluna "
            "'chave_acesso', usada pelo novo login do recrutador por chave "
            "numérica de 8 dígitos."
        )

    sem_chave = conexao.execute(
        "SELECT id, nome FROM empresas WHERE chave_acesso IS NULL OR chave_acesso = ''"
    ).fetchall()
    for linha in sem_chave:
        chave = _gerar_chave_acesso_unica(conexao)
        conexao.execute("UPDATE empresas SET chave_acesso = ? WHERE id = ?", (chave, linha["id"]))
        conexao.commit()
        logger.warning(
            "Chave de acesso gerada automaticamente para a empresa '%s' (id=%s).",
            linha["nome"],
            linha["id"],
        )


def _migrar_tabela_usuarios_adicionar_provisionado_por_chave(conexao: sqlite3.Connection) -> None:
    """
    Adiciona a coluna ``provisionado_por_chave`` (INTEGER 0/1) à tabela
    ``usuarios``.

    Marca contas de recrutador criadas AUTOMATICAMENTE pelo novo login por
    chave da empresa (ver ``provisionar_usuario_recrutador``) — diferente
    de uma conta cadastrada manualmente por um administrador em
    "Gerenciar Usuários". Essas contas são efêmeras: são excluídas
    automaticamente ao encerrar a sessão (ver ``auth.encerrar_sessao`` e
    ``excluir_usuario_provisionado``), e esta coluna funciona como
    salvaguarda — só uma conta com ``provisionado_por_chave=1`` pode ser
    excluída por esse caminho automático, nunca uma conta cadastrada
    manualmente por um admin.

    Idempotente: seguro chamar toda vez que o sistema inicia.
    """
    colunas = conexao.execute("PRAGMA table_info(usuarios)").fetchall()
    nomes_colunas = {coluna["name"] for coluna in colunas}

    if "provisionado_por_chave" in nomes_colunas:
        return

    logger.warning(
        "Esquema antigo da tabela 'usuarios' detectado (sem a coluna "
        "'provisionado_por_chave'). Adicionando automaticamente..."
    )
    conexao.execute(
        "ALTER TABLE usuarios ADD COLUMN provisionado_por_chave INTEGER NOT NULL DEFAULT 0"
    )
    conexao.commit()
    logger.warning(
        "Migração concluída: a tabela 'usuarios' agora possui a coluna "
        "'provisionado_por_chave'."
    )


def _migrar_indice_empresas_nome_nocase(conexao: sqlite3.Connection) -> None:
    """
    Cria um índice UNIQUE adicional em ``empresas.nome`` usando a colação
    ``NOCASE`` (comparação sem diferenciar maiúsculas/minúsculas).

    A coluna ``nome`` já possui uma restrição UNIQUE "de fábrica" (na
    definição da tabela), mas o SQLite usa colação BINARY por padrão —
    ou seja, "Empresa Alfa" e "empresa alfa" são consideradas nomes
    DIFERENTES e ambas seriam aceitas, permitindo cadastros duplicados
    por um simples descuido de digitação do administrador (diferente do
    login de usuário, já normalizado para minúsculas em
    ``criar_usuario``). Este índice extra fecha essa brecha sem precisar
    recriar a tabela (não é possível alterar a colação de uma coluna já
    existente via ``ALTER TABLE``) — ``criar_empresa``/``renomear_empresa``
    já tratam genericamente qualquer violação UNIQUE (looking for
    "UNIQUE" na mensagem do erro), então nenhuma mudança foi necessária
    nelas para que a mensagem amigável de "nome já cadastrado" também
    cubra este novo índice.

    Defensivo: se o banco JÁ tiver duas empresas cujos nomes só diferem
    em maiúsculas/minúsculas (cadastradas antes desta migração existir),
    a criação do índice falha com ``IntegrityError`` — nesse caso, a
    migração é pulada (com um aviso claro no log orientando o
    administrador a renomear manualmente uma delas) em vez de impedir o
    sistema de iniciar. A proteção passa a valer automaticamente na
    primeira inicialização em que não houver mais conflito.

    Não faz nada (é uma operação rápida e idempotente) se o índice já
    existir.
    """
    try:
        conexao.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_empresas_nome_nocase "
            "ON empresas (nome COLLATE NOCASE)"
        )
        conexao.commit()
    except sqlite3.IntegrityError:
        conexao.rollback()
        logger.warning(
            "Não foi possível ativar a proteção contra nomes de empresa "
            "duplicados por maiúsculas/minúsculas: já existem duas ou "
            "mais empresas cadastradas com nomes que só diferem nisso "
            "(ex.: 'Empresa Alfa' e 'empresa alfa'). Renomeie manualmente "
            "uma delas na tela Empresas para eliminar a duplicidade; a "
            "proteção será ativada automaticamente assim que não houver "
            "mais conflito."
        )


def _migrar_tabela_empresas_adicionar_fixa(conexao: sqlite3.Connection) -> None:
    """
    Adiciona a coluna ``fixa`` (INTEGER, 0/1, padrão 0) à tabela
    ``empresas`` — marca as duas opções fixas do sistema ("Criar
    Currículos"/"Imprimir Currículos", ver ``NOMES_EMPRESAS_FIXAS`` e
    ``_semear_empresas_fixas``), sempre disponíveis para o Emissor emitir
    senha, independente de quais empresas o administrador cadastrou.

    Diferente de uma empresa comum, uma empresa com ``fixa = 1`` não pode
    ser renomeada nem desativada (ver ``renomear_empresa``/
    ``definir_status_empresa``) e não aparece no login público de
    recrutador por chave (ver ``app.py:empresas_entrar_tela``) — ela não
    representa uma empresa real participante do feirão, e sim um serviço
    de apoio ao candidato.

    Idempotente: seguro chamar toda vez que o sistema inicia. Empresas já
    cadastradas (todas reais, cadastradas por um administrador) recebem
    ``fixa = 0`` automaticamente pelo próprio ``DEFAULT`` da coluna.
    """
    colunas = conexao.execute("PRAGMA table_info(empresas)").fetchall()
    nomes_colunas = {coluna["name"] for coluna in colunas}

    if "fixa" in nomes_colunas:
        return  # Já está no formato atual.

    logger.warning(
        "Esquema antigo da tabela 'empresas' detectado (sem a coluna "
        "'fixa'). Adicionando automaticamente..."
    )
    conexao.execute("ALTER TABLE empresas ADD COLUMN fixa INTEGER NOT NULL DEFAULT 0")
    conexao.commit()
    logger.warning(
        "Migração concluída: a tabela 'empresas' agora possui a coluna "
        "'fixa', usada pelas duas opções fixas de emissão de senha "
        "('Criar Currículos'/'Imprimir Currículos')."
    )


def _migrar_tabela_eventos_chamada_adicionar_lote(conexao: sqlite3.Connection) -> None:
    """
    Adiciona a coluna ``lote_chamada`` (TEXT, opcional) à tabela
    ``eventos_chamada`` — identifica quais eventos de chamada pertencem à
    MESMA operação (uma chamada normal gera um lote de 1 senha; "Chamar
    Selecionadas" gera um lote com várias senhas de uma vez — ver
    ``chamar_proxima``/``chamar_varias``). Usada por
    ``obter_chamada_atual`` para montar a "sequência chamada" exibida no
    Painel Público sem misturar lotes de operações (ou empresas)
    diferentes que aconteçam ao mesmo tempo.

    Eventos já existentes (gravados antes desta migração) ficam com
    ``lote_chamada = NULL`` — ``obter_chamada_atual`` trata isso como um
    lote de uma única senha (o próprio evento), preservando o
    comportamento antigo para o histórico já gravado.
    """
    colunas = conexao.execute("PRAGMA table_info(eventos_chamada)").fetchall()
    nomes_colunas = {coluna["name"] for coluna in colunas}

    if "lote_chamada" in nomes_colunas:
        return  # Já está no formato atual.

    logger.warning(
        "Esquema antigo da tabela 'eventos_chamada' detectado (sem a "
        "coluna 'lote_chamada'). Adicionando automaticamente..."
    )
    conexao.execute("ALTER TABLE eventos_chamada ADD COLUMN lote_chamada TEXT")
    conexao.commit()
    logger.warning(
        "Migração concluída: a tabela 'eventos_chamada' agora possui a "
        "coluna 'lote_chamada', usada para agrupar senhas chamadas juntas "
        "de uma vez ('Chamar Selecionadas')."
    )


def _migrar_tabela_usuarios_adicionar_empresa_id(conexao: sqlite3.Connection) -> None:
    """
    Adiciona a coluna ``empresa_id`` (INTEGER, opcional) à tabela
    ``usuarios`` em bancos de dados criados antes do perfil "recrutador"
    existir.

    Segue o mesmo padrão simples de
    ``_migrar_tabela_senhas_adicionar_empresa``: apenas ``ALTER TABLE ...
    ADD COLUMN``, sem necessidade de recriar a tabela, pois a nova coluna
    aceita valores nulos — usuários já cadastrados simplesmente ficam sem
    empresa vinculada (correto, já que nenhum deles era recrutador antes
    desta funcionalidade existir).

    Não faz nada se a coluna já existir (portanto é seguro chamar esta
    função toda vez que o sistema inicia).
    """
    colunas = conexao.execute("PRAGMA table_info(usuarios)").fetchall()
    nomes_colunas = {coluna["name"] for coluna in colunas}

    if "empresa_id" in nomes_colunas:
        return  # Já está no formato atual.

    logger.warning(
        "Esquema antigo da tabela 'usuarios' detectado (sem a coluna "
        "'empresa_id'). Adicionando automaticamente..."
    )

    conexao.execute("ALTER TABLE usuarios ADD COLUMN empresa_id INTEGER REFERENCES empresas (id)")
    conexao.commit()

    logger.warning(
        "Migração concluída: a tabela 'usuarios' agora possui a coluna "
        "'empresa_id', usada para vincular um usuário 'recrutador' à sua "
        "empresa."
    )


def _migrar_tabela_empresas_adicionar_identidade_visual(conexao: sqlite3.Connection) -> None:
    """
    Adiciona as colunas ``logo_path`` e ``cor_principal`` (ambas TEXT,
    opcionais) à tabela ``empresas`` em bancos de dados criados antes da
    identidade visual por empresa existir.

    Mesmo padrão simples de ``_migrar_tabela_senhas_adicionar_empresa``:
    duas chamadas de ``ALTER TABLE ... ADD COLUMN``, sem recriar a tabela.
    Empresas já cadastradas simplesmente ficam sem identidade visual
    própria (usam o logo/cor padrão do sistema) até que um administrador
    faça o upload de um logo pela tela Empresas.

    Não faz nada se as colunas já existirem (portanto é seguro chamar
    esta função toda vez que o sistema inicia).
    """
    colunas = conexao.execute("PRAGMA table_info(empresas)").fetchall()
    nomes_colunas = {coluna["name"] for coluna in colunas}

    if "logo_path" in nomes_colunas and "cor_principal" in nomes_colunas:
        return  # Já está no formato atual.

    logger.warning(
        "Esquema antigo da tabela 'empresas' detectado (sem as colunas de "
        "identidade visual). Adicionando automaticamente..."
    )

    if "logo_path" not in nomes_colunas:
        conexao.execute("ALTER TABLE empresas ADD COLUMN logo_path TEXT")
    if "cor_principal" not in nomes_colunas:
        conexao.execute("ALTER TABLE empresas ADD COLUMN cor_principal TEXT")
    conexao.commit()

    logger.warning(
        "Migração concluída: a tabela 'empresas' agora possui as colunas "
        "'logo_path' e 'cor_principal'."
    )


def _migrar_tabela_empresas_adicionar_contador(conexao: sqlite3.Connection) -> None:
    """
    Adiciona a coluna ``contador_atual`` (INTEGER, padrão 0) à tabela
    ``empresas`` em bancos de dados criados antes de cada empresa ter sua
    própria sequência de numeração de senhas.

    Antes desta migração, a numeração de senhas era controlada por um
    único contador GLOBAL (chave ``contador_atual`` na tabela
    ``configuracoes``, ver ``config.py``), compartilhado por todas as
    empresas. Esta coluna substitui aquele contador global: cada empresa
    passa a ter sua própria sequência independente 001, 002, 003...
    (ver ``criar_senha`` mais abaixo). A chave antiga em ``configuracoes``
    permanece no banco (não é removida), apenas deixa de ser usada — não
    há necessidade de migrar dados nela, pois o histórico de senhas já
    emitidas mantém seus números originais independentemente de qual
    contador os gerou.

    Mesmo padrão simples de ``_migrar_tabela_senhas_adicionar_empresa``:
    uma chamada de ``ALTER TABLE ... ADD COLUMN``, sem recriar a tabela.

    Não faz nada se a coluna já existir (portanto é seguro chamar esta
    função toda vez que o sistema inicia).
    """
    colunas = conexao.execute("PRAGMA table_info(empresas)").fetchall()
    nomes_colunas = {coluna["name"] for coluna in colunas}

    if "contador_atual" in nomes_colunas:
        return  # Já está no formato atual.

    logger.warning(
        "Esquema antigo da tabela 'empresas' detectado (sem a coluna "
        "'contador_atual'). Adicionando automaticamente..."
    )

    conexao.execute("ALTER TABLE empresas ADD COLUMN contador_atual INTEGER NOT NULL DEFAULT 0")
    conexao.commit()

    logger.warning(
        "Migração concluída: a tabela 'empresas' agora possui a coluna "
        "'contador_atual' — cada empresa passa a ter sua própria "
        "sequência de numeração de senhas."
    )


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

def criar_senha(
    empresa_id: int,
    empresa: Optional[str] = None,
    guiche: Optional[str] = None,
    usuario: Optional[str] = None,
    nome_pessoa: Optional[str] = None,
    finalizar_imediatamente: bool = False,
) -> Senha:
    """
    Cria (emite) uma nova senha.

    O número sequencial da senha é obtido de forma atômica a partir da
    coluna ``empresas.contador_atual`` DAQUELA empresa (``empresa_id``),
    protegida por um lock em memória para evitar que duas requisições
    simultâneas gerem o mesmo número. Cada empresa possui sua PRÓPRIA
    sequência independente — a Empresa A e a Empresa B podem ambas emitir,
    ao mesmo tempo, uma senha número 001, sem conflito entre si (antes
    desta funcionalidade existir, havia um único contador global
    compartilhado por todas as empresas, na tabela ``configuracoes``).

    ``empresa_id`` é obrigatório e deve corresponder a uma empresa já
    cadastrada (a validação de que ela existe e está ativa é
    responsabilidade da camada de rotas, ``app.py:api_emitir``, não desta
    função — aqui apenas assumimos que o id é válido).

    ``empresa`` é o NOME da empresa, gravado como texto na própria linha
    da senha (no mesmo espírito de ``guiche`` e ``usuario``) — usado
    APENAS para exibição/relatórios. ``empresa_id`` também é gravado na
    linha (referência estável) e é o que de fato controla o escopo de
    fila e as permissões do recrutador (ver
    ``_migrar_tabela_senhas_adicionar_empresa_id`` e
    ``app.py:_pode_gerenciar_senha`` para o motivo de não usarmos apenas
    o nome para isso).

    ``nome_pessoa`` é OPCIONAL — o "Primeiro Nome" digitado livremente
    pelo Emissor no momento da emissão (ver app.py:api_emitir), impresso
    no ticket quando preenchido (ver printer.py:imprimir_senha) e
    preservado em reimpressões, já que fica gravado na própria senha.

    ``finalizar_imediatamente`` (usado pelas duas opções fixas do sistema
    — ver ``NOMES_EMPRESAS_FIXAS`` e ``app.py:api_emitir``): quando
    ``True``, a senha já nasce com status ``'Finalizada'`` (em vez de
    ``'Emitida'``), com ``hora_chamada`` e ``hora_finalizada`` iguais à
    própria hora de emissão. "Criar Currículos"/"Imprimir Currículos" são
    serviços de apoio ao candidato sem fila nem chamada (não há
    guichê/mesa "chamando" ninguém) — a senha serve só como registro de
    que o atendimento aconteceu, então já entra direto como "realizada":
    nunca aparece na Fila de Espera (``listar_fila_atual`` só lista
    ``'Emitida'``) e nunca gera um evento em ``eventos_chamada`` (não há
    guichê anunciando nada). Mesmo assim, CONTA normalmente tanto como
    "senha emitida" quanto como "chamada realizada" nos relatórios e no
    Painel Geral — ver ``contar_chamadas_realizadas_periodo``, que conta
    por ``hora_chamada`` (preenchida aqui) em vez de por linhas em
    ``eventos_chamada``, exatamente para incluir este caso.

    Retorna a instância de ``Senha`` recém-criada.
    """
    with _lock_da_empresa(empresa_id):
        with get_connection() as conexao:
            linha_empresa = conexao.execute(
                "SELECT contador_atual FROM empresas WHERE id = ?", (empresa_id,)
            ).fetchone()
            if linha_empresa is None:
                raise ValueError(f"Empresa id={empresa_id} não encontrada.")

            novo_numero = int(linha_empresa["contador_atual"]) + 1
            conexao.execute(
                "UPDATE empresas SET contador_atual = ? WHERE id = ?",
                (novo_numero, empresa_id),
            )

            data_hora = _agora_iso()
            status_inicial = StatusSenha.FINALIZADA if finalizar_imediatamente else StatusSenha.EMITIDA
            hora_chamada = data_hora if finalizar_imediatamente else None
            hora_finalizada = data_hora if finalizar_imediatamente else None

            cursor = conexao.execute(
                """
                INSERT INTO senhas
                    (numero, status, data_hora, guiche, usuario, empresa, empresa_id,
                     nome_pessoa, hora_chamada, hora_finalizada)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    novo_numero,
                    status_inicial,
                    data_hora,
                    guiche,
                    usuario,
                    empresa,
                    empresa_id,
                    nome_pessoa,
                    hora_chamada,
                    hora_finalizada,
                ),
            )
            conexao.commit()
            senha_id = cursor.lastrowid

    registrar_log(
        "INFO",
        f"Senha emitida: número {novo_numero:03d} (id={senha_id}, empresa='{empresa}')"
        + (" — já registrada como realizada, sem fila." if finalizar_imediatamente else ""),
    )

    return Senha(
        id=senha_id,
        numero=novo_numero,
        status=status_inicial,
        data_hora=data_hora,
        guiche=guiche,
        usuario=usuario,
        empresa=empresa,
        empresa_id=empresa_id,
        nome_pessoa=nome_pessoa,
        hora_chamada=hora_chamada,
        hora_finalizada=hora_finalizada,
    )


def reiniciar_contador() -> None:
    """
    Reinicia o contador de numeração de senhas de TODAS as empresas para
    zero (cada empresa tem sua própria sequência independente — ver
    ``criar_senha``). Para reiniciar apenas UMA empresa, sem afetar as
    demais, use ``reiniciar_contador_empresa``.

    Esta operação NÃO apaga o histórico de senhas já emitidas; apenas faz
    com que a próxima senha emitida para cada empresa volte a ser
    numerada a partir de 001.

    Adquire o lock de TODAS as empresas (ver ``_lock_da_empresa``) antes
    de zerar, e só então executa o UPDATE — sem isso, uma emissão de
    senha (``criar_senha``) concorrente para alguma empresa, no meio
    desta operação, poderia ler o contador ANTES do reset e gravá-lo de
    volta incrementado LOGO DEPOIS do reset, fazendo o reset "sumir"
    silenciosamente para aquela empresa.
    """
    ids_empresas = [linha["id"] for linha in listar_empresas()]
    locks = [_lock_da_empresa(empresa_id) for empresa_id in sorted(ids_empresas)]

    for lock_empresa in locks:
        lock_empresa.acquire()
    try:
        with get_connection() as conexao:
            conexao.execute("UPDATE empresas SET contador_atual = 0")
            conexao.commit()
    finally:
        for lock_empresa in locks:
            lock_empresa.release()

    registrar_log("WARNING", "Contador de senhas reiniciado manualmente (todas as empresas).")


def reiniciar_contador_empresa(empresa_id: int) -> bool:
    """
    Reinicia o contador de numeração de senhas de UMA ÚNICA empresa para
    zero, sem afetar a sequência das demais empresas.

    Usado pelo botão "🔄 Reiniciar Contador" exibido na linha de cada
    empresa na tela Empresas (``/admin/empresas``).

    Esta operação NÃO apaga o histórico de senhas já emitidas; apenas faz
    com que a PRÓXIMA senha emitida para esta empresa volte a ser
    numerada a partir de 001.

    Usa o MESMO lock por empresa que ``criar_senha`` (``_lock_da_empresa``),
    não o lock global — assim o reset e uma emissão concorrente da MESMA
    empresa nunca correm ao mesmo tempo, sem travar a emissão de outras
    empresas nesse meio-tempo.

    Retorna ``True`` se a empresa existia (e foi reiniciada), ou ``False``
    se nenhuma empresa com esse id foi encontrada.
    """
    with _lock_da_empresa(empresa_id):
        with get_connection() as conexao:
            cursor = conexao.execute(
                "UPDATE empresas SET contador_atual = 0 WHERE id = ?", (empresa_id,)
            )
            conexao.commit()
            alterou = cursor.rowcount > 0

    if alterou:
        registrar_log(
            "WARNING", f"Contador de senhas da empresa id={empresa_id} reiniciado manualmente."
        )
    return alterou


# ---------------------------------------------------------------------------
# Chamada de senhas (fila FIFO)
# ---------------------------------------------------------------------------

def obter_proxima_emitida(empresa_id: Optional[int] = None) -> Optional[Senha]:
    """
    Retorna a próxima senha com status 'Emitida', respeitando a ordem de
    chegada (FIFO), ou ``None`` caso não haja senhas aguardando chamada.

    ``empresa_id`` é opcional: quando informado, restringe a busca à fila
    DAQUELA empresa apenas — usado pelo perfil "recrutador" (ver
    ``chamar_proxima``). Quando omitido (``None``), considera a fila
    GERAL, com senhas de todas as empresas misturadas — comportamento
    usado pelo perfil "atendente", inalterado desde antes da existência
    dos recrutadores.

    Filtra por ``empresa_id`` (referência estável), não pelo nome da
    empresa — ver ``_migrar_tabela_senhas_adicionar_empresa_id`` para o
    motivo.
    """
    condicao = "WHERE status = ?"
    parametros: List = [StatusSenha.EMITIDA]
    if empresa_id:
        condicao += " AND empresa_id = ?"
        parametros.append(empresa_id)

    with get_connection() as conexao:
        linha = conexao.execute(
            f"SELECT * FROM senhas {condicao} ORDER BY id ASC LIMIT 1",
            parametros,
        ).fetchone()

    return Senha.from_row(linha) if linha else None


def _gerar_lote_chamada() -> str:
    """
    Gera um identificador curto e aleatório para agrupar todos os
    eventos de ``eventos_chamada`` criados em UMA mesma operação de
    chamada (uma chamada normal = lote de 1 senha; "Chamar Selecionadas"
    = lote com várias senhas de uma vez — ver ``chamar_proxima``/
    ``chamar_varias``). Usado por ``obter_chamada_atual`` para montar a
    "sequência chamada" exibida no Painel Público sem misturar lotes de
    operações (ou empresas) diferentes que aconteçam ao mesmo tempo.

    Não precisa ser criptograficamente imprevisível (não é um segredo,
    como a chave de acesso de empresa) — só precisa ser praticamente
    único a cada chamada; ``token_hex`` já é aleatório o bastante para
    isso.
    """
    return secrets.token_hex(6)


def chamar_proxima(guiche: str, usuario: str, empresa_id: Optional[int] = None) -> Optional[Dict]:
    """
    Chama a próxima senha da fila (a mais antiga com status 'Emitida').

    A senha chamada tem seu status atualizado para 'Chamada' e um novo
    registro é criado em ``eventos_chamada``, representando o anúncio no
    painel. Retorna um dicionário com os dados da senha e do evento de
    chamada, ou ``None`` se a fila estiver vazia.

    ``empresa_id`` restringe a chamada à fila de uma empresa específica
    (ver ``obter_proxima_emitida``) — usado pelo perfil "recrutador".

    A operação é protegida por lock para impedir que duas chamadas
    simultâneas peguem a mesma senha (condição de corrida).

    Grava um ``lote_chamada`` próprio (lote de uma única senha) — ver
    ``_gerar_lote_chamada`` — para que ``obter_chamada_atual`` trate
    chamadas individuais e chamadas em conjunto (``chamar_varias``) da
    mesma forma.
    """
    with _lock:
        proxima = obter_proxima_emitida(empresa_id)
        if proxima is None:
            return None

        data_hora = _agora_iso()
        lote = _gerar_lote_chamada()
        with get_connection() as conexao:
            # "hora_chamada" é gravada aqui, na PRIMEIRA (e única) vez que
            # esta senha transiciona de Emitida para Chamada — repetições
            # de chamada não passam por aqui (ver repetir_ultima_chamada),
            # então este valor nunca é sobrescrito. Usado pelos relatórios
            # para calcular o tempo de atendimento.
            conexao.execute(
                "UPDATE senhas SET status = ?, guiche = ?, usuario = ?, hora_chamada = ? WHERE id = ?",
                (StatusSenha.CHAMADA, guiche, usuario, data_hora, proxima.id),
            )
            cursor = conexao.execute(
                """
                INSERT INTO eventos_chamada (senha_id, numero, guiche, usuario, data_hora, lote_chamada)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (proxima.id, proxima.numero, guiche, usuario, data_hora, lote),
            )
            conexao.commit()
            evento_id = cursor.lastrowid

    registrar_log(
        "INFO",
        f"Senha {proxima.numero:03d} chamada no guichê '{guiche}' por '{usuario}'.",
    )

    return {
        "evento_id": evento_id,
        "lote_chamada": lote,
        "senha_id": proxima.id,
        "numero": proxima.numero,
        "guiche": guiche,
        "usuario": usuario,
        "data_hora": data_hora,
        # "proxima" já é a Senha completa (obtida por obter_proxima_emitida),
        # então a empresa selecionada na emissão já está disponível aqui
        # sem precisar de nenhuma consulta extra. Usada pela tela principal
        # (destaque "Última Senha Chamada") e pelo painel geral (/painel),
        # onde a fila mistura senhas de várias empresas — sem isso, quem
        # acabou de chamar não sabia para qual empresa era a senha.
        "empresa": proxima.empresa,
    }


def chamar_varias(
    senha_ids: List[int], guiche: str, usuario: str, empresa_id: Optional[int] = None
) -> Dict:
    """
    Chama, de uma só vez, um CONJUNTO específico de senhas escolhidas
    manualmente pelo recrutador na Fila de Espera (ao contrário de
    ``chamar_proxima``, que sempre pega a mais antiga da fila em ordem
    FIFO) — usada pelo botão "Chamar Selecionadas".

    Todas as senhas chamadas nesta mesma operação compartilham o mesmo
    ``lote_chamada`` (ver ``_gerar_lote_chamada``), permitindo que o
    Painel Público monte a "sequência chamada" (ver
    ``obter_chamada_atual``) sem misturar com chamadas de OUTRAS
    empresas acontecendo ao mesmo tempo: cada lote pertence a uma única
    operação de chamada, e ``obter_chamada_atual`` sempre filtra por
    ``empresa_id`` (via JOIN com ``senhas``) antes de decidir qual é o
    lote mais recente a exibir — o lote de uma empresa nunca aparece no
    painel de outra, mesmo que as duas chamem simultaneamente.

    Validação "tudo ou nada": cada id em ``senha_ids`` precisa
    - existir;
    - estar com status ``'Emitida'`` (não é possível chamar uma senha
      que já foi chamada, finalizada ou cancelada);
    - pertencer à empresa de ``empresa_id``, quando informado (protege
      contra um recrutador tentar chamar a senha de outra empresa
      manipulando o id na requisição — mesmo princípio já aplicado em
      ``app.py:_pode_gerenciar_senha``).

    Se QUALQUER id da lista falhar em uma dessas checagens, a função
    levanta ``ValueError`` descrevendo o problema e NADA é alterado no
    banco — evita um estado parcialmente aplicado (algumas senhas
    chamadas, outras não) que confundiria o recrutador sobre o que
    realmente aconteceu.

    Protegida pelo mesmo lock global de ``chamar_proxima``, para não
    disputar a mesma senha com uma chamada concorrente (individual ou em
    lote) de outro guichê/mesa.
    """
    if not senha_ids:
        raise ValueError("Nenhuma senha selecionada.")

    # Remove duplicatas preservando a ordem original de seleção — evita
    # que o mesmo id repetido na lista (ex.: duplo clique) gere dois
    # eventos de chamada para a mesma senha.
    ids_unicos = list(dict.fromkeys(senha_ids))

    with _lock:
        with get_connection() as conexao:
            senhas_validadas: List[Senha] = []
            for senha_id in ids_unicos:
                linha = conexao.execute("SELECT * FROM senhas WHERE id = ?", (senha_id,)).fetchone()
                if linha is None:
                    raise ValueError(f"Senha id={senha_id} não encontrada.")

                senha = Senha.from_row(linha)

                if empresa_id and senha.empresa_id != empresa_id:
                    raise ValueError(
                        f"Senha {senha.numero:03d} não pertence à sua empresa."
                    )
                if senha.status != StatusSenha.EMITIDA:
                    raise ValueError(
                        f"Senha {senha.numero:03d} não está mais aguardando "
                        f"(status atual: {senha.status})."
                    )
                senhas_validadas.append(senha)

            # Todas as senhas do lote precisam ser da MESMA empresa — mesmo
            # quando ``empresa_id`` não foi informado (perfil "atendente",
            # que opera a fila GERAL, compartilhada entre todas as
            # empresas — ver PerfilUsuario.ATENDENTE). Corrigido na revisão
            # geral do sistema: sem essa checagem, um atendente podia
            # selecionar senhas de empresas DIFERENTES na mesma operação
            # "Chamar Selecionadas", gerando um lote misto — mas o
            # restante do sistema (obter_chamada_atual, static/js/painel.js)
            # assume que um lote pertence a UMA única empresa (só existe UM
            # campo "empresa" de nível raiz por lote), então o painel geral
            # acabava rotulando a sequência inteira com o nome de apenas a
            # primeira empresa, mesmo quando as demais senhas chamadas
            # eram de outra.
            empresas_do_lote = {senha.empresa_id for senha in senhas_validadas}
            if len(empresas_do_lote) > 1:
                raise ValueError(
                    "As senhas selecionadas são de empresas diferentes — "
                    "chame senhas de uma empresa por vez."
                )

            lote = _gerar_lote_chamada()
            data_hora = _agora_iso()
            chamadas = []
            for senha in senhas_validadas:
                conexao.execute(
                    "UPDATE senhas SET status = ?, guiche = ?, usuario = ?, hora_chamada = ? WHERE id = ?",
                    (StatusSenha.CHAMADA, guiche, usuario, data_hora, senha.id),
                )
                cursor = conexao.execute(
                    """
                    INSERT INTO eventos_chamada (senha_id, numero, guiche, usuario, data_hora, lote_chamada)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (senha.id, senha.numero, guiche, usuario, data_hora, lote),
                )
                chamadas.append(
                    {
                        "evento_id": cursor.lastrowid,
                        "senha_id": senha.id,
                        "numero": senha.numero,
                        "empresa": senha.empresa,
                    }
                )
            conexao.commit()

    numeros_formatados = ", ".join(f"{c['numero']:03d}" for c in chamadas)
    registrar_log(
        "INFO",
        f"Senhas {numeros_formatados} chamadas em conjunto no guichê '{guiche}' "
        f"por '{usuario}' (lote {lote}).",
    )

    primeira = chamadas[0]
    return {
        "lote_chamada": lote,
        "guiche": guiche,
        "usuario": usuario,
        "data_hora": data_hora,
        "chamadas": chamadas,
        # Campos no nível raiz espelhando a PRIMEIRA senha do lote, para
        # quem só precisa de uma confirmação simples (ex.: notificação de
        # sucesso na tela) sem percorrer a lista "chamadas".
        "evento_id": primeira["evento_id"],
        "senha_id": primeira["senha_id"],
        "numero": primeira["numero"],
        "empresa": primeira["empresa"],
    }


def repetir_ultima_chamada(guiche: Optional[str] = None) -> Optional[Dict]:
    """
    Repete a última senha chamada NAQUELE guichê/mesa específico, gerando
    um NOVO evento de chamada com o mesmo número/guichê/usuário. Isso
    permite que o painel detecte a mudança (novo id de evento) e dispare
    novamente a animação e o bip, sem alterar a posição da fila nem
    duplicar a senha na tabela ``senhas``.

    ``guiche`` é o texto EXATO já formatado (ex.: ``"Mesa 01 — Empresa A"``
    para recrutador, ``"Guichê 01"`` para atendente — ver
    ``app.py:_guiche_formatado``), correspondente ao guichê/mesa do
    usuário que está chamando a repetição. Restringir por esse texto (e
    não apenas pela empresa) é essencial quando várias pessoas atendem na
    MESMA empresa em mesas diferentes: sem isso, repetir na Mesa 02
    poderia acabar reanunciando por engano a última chamada da Mesa 01
    (de outro recrutador da mesma empresa), em vez da chamada feita
    DAQUELA mesa. Se omitido (``None``), cai no comportamento antigo de
    considerar a última chamada do sistema inteiro — mantido apenas como
    salvaguarda, já que todo perfil com acesso a este botão sempre possui
    um guichê/mesa atribuído no momento (ver ``app.py:api_repetir``).

    Retorna ``None`` se ainda não houve nenhuma chamada (naquele
    guichê/mesa, quando informado).

    Levanta ``ValueError`` se a última senha chamada NAQUELE guichê/mesa já
    estiver com status 'Finalizada' ou 'Cancelada' — o atendimento dela já
    terminou, então não faz sentido repetir a chamada (ver comentário mais
    abaixo).
    """
    with get_connection() as conexao:
        if guiche:
            ultimo = conexao.execute(
                "SELECT * FROM eventos_chamada WHERE guiche = ? ORDER BY id DESC LIMIT 1",
                (guiche,),
            ).fetchone()
        else:
            ultimo = conexao.execute(
                "SELECT * FROM eventos_chamada ORDER BY id DESC LIMIT 1"
            ).fetchone()

    if ultimo is None:
        return None

    # Uma senha cujo atendimento já foi FINALIZADO (ver ``finalizar_senha``)
    # ou CANCELADO não deve ser "rechamada": o atendimento dela já
    # terminou, então gerar um novo evento de chamada confundiria quem
    # está aguardando (o painel voltaria a exibir/anunciar uma senha que
    # já não está mais em atendimento). A checagem fica aqui, na camada de
    # dados compartilhada por TODOS os perfis (atendente e recrutador),
    # para que a regra valha igualmente para qualquer usuário que use o
    # botão "Repetir Chamada" — não apenas para um perfil específico.
    with get_connection() as conexao:
        # Também traz "empresa" nesta mesma consulta (em vez de um JOIN
        # separado nas duas variações da consulta de "ultimo" acima) para
        # incluir no retorno abaixo — usada pela tela principal (destaque
        # "Última Senha Chamada") e pelo painel geral, onde a fila mistura
        # senhas de várias empresas.
        senha_atual = conexao.execute(
            "SELECT status, empresa FROM senhas WHERE id = ?", (ultimo["senha_id"],)
        ).fetchone()

    if senha_atual is not None:
        if senha_atual["status"] == StatusSenha.FINALIZADA:
            raise ValueError(
                "Não é possível repetir: o atendimento desta senha já foi finalizado."
            )
        if senha_atual["status"] == StatusSenha.CANCELADA:
            raise ValueError("Não é possível repetir: esta senha foi cancelada.")

    empresa_da_senha = senha_atual["empresa"] if senha_atual is not None else None

    data_hora = _agora_iso()
    # Repetição sempre gera um lote NOVO, de uma única senha — mesmo que a
    # chamada original tenha sido feita em conjunto com outras (ver
    # chamar_varias): "Repetir" reanuncia especificamente a ÚLTIMA senha
    # chamada naquele guichê/mesa, não o lote inteiro de origem. Ver
    # _gerar_lote_chamada/obter_chamada_atual.
    lote = _gerar_lote_chamada()
    with get_connection() as conexao:
        cursor = conexao.execute(
            """
            INSERT INTO eventos_chamada (senha_id, numero, guiche, usuario, data_hora, lote_chamada)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ultimo["senha_id"], ultimo["numero"], ultimo["guiche"], ultimo["usuario"], data_hora, lote),
        )
        conexao.commit()
        evento_id = cursor.lastrowid

    registrar_log("INFO", f"Repetição de chamada da senha {ultimo['numero']:03d}.")

    return {
        "evento_id": evento_id,
        "lote_chamada": lote,
        "senha_id": ultimo["senha_id"],
        "numero": ultimo["numero"],
        "guiche": ultimo["guiche"],
        "usuario": ultimo["usuario"],
        "data_hora": data_hora,
        "empresa": empresa_da_senha,
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


def finalizar_atendimento_e_chamar_proxima(guiche: str, usuario: str, empresa_id: Optional[int] = None) -> Dict:
    """
    Implementa o botão "Finalizar Atendimento": encerra (marca como
    'Finalizada') a senha que está sendo atendida no guichê informado e,
    em seguida, chama automaticamente a próxima senha da fila (FIFO) para
    o mesmo guichê/atendente.

    ``empresa_id`` restringe a próxima chamada à fila de uma empresa
    específica — usado pelo perfil "recrutador" (ver ``chamar_proxima``).

    Retorna um dicionário com duas chaves:
        - "senha_finalizada": dados da senha finalizada, ou ``None`` se
          não havia nenhuma senha em atendimento neste guichê (o botão
          então se comporta apenas como "Chamar Próxima").
        - "chamada": dados da nova chamada (mesmo formato de
          ``chamar_proxima``), ou ``None`` se a fila estiver vazia — caso
          em que o atendente/recrutador deve aguardar a emissão de uma
          nova senha.
    """
    senha_em_atendimento = obter_senha_em_atendimento(guiche)

    senha_finalizada_dict = None
    if senha_em_atendimento is not None:
        finalizar_senha(senha_em_atendimento.id)
        senha_finalizada_dict = senha_em_atendimento.to_dict()

    proxima_chamada = chamar_proxima(guiche=guiche, usuario=usuario, empresa_id=empresa_id)

    return {
        "senha_finalizada": senha_finalizada_dict,
        "chamada": proxima_chamada,
    }


def obter_chamada_atual(empresa_id: Optional[int] = None) -> Optional[Dict]:
    """
    Retorna os dados do LOTE de chamada mais recente — uma ou mais
    senhas chamadas JUNTAS na mesma operação (ver
    ``chamar_varias``/``_gerar_lote_chamada``) — ou ``None`` se nenhuma
    chamada foi realizada ainda. Usado para popular o destaque "senha em
    atendimento" nos painéis públicos e na tela principal (caixa "Última
    Senha Chamada").

    Antes de existir o conceito de lote, esta função retornava só o
    EVENTO mais recente isoladamente. Agora, quando o recrutador chama
    várias senhas de uma vez ("Chamar Selecionadas"), o painel deve
    mostrar a sequência inteira — não só a última da lista — então esta
    função primeiro descobre qual foi o lote mais recente e depois busca
    TODOS os eventos daquele lote.

    ``empresa_id`` restringe a busca à empresa específica (via JOIN com
    ``senhas``) — usado pelo painel público de uma empresa
    (``/painel/empresa/<id>``). Quando omitido, considera todas as
    empresas — comportamento do painel geral (``/painel``).

    IMPORTANTE (isolamento entre empresas chamando ao mesmo tempo): como
    o passo 1 (achar o lote mais recente) já filtra por
    ``s.empresa_id`` quando ``empresa_id`` é informado, e o passo 2
    (buscar os eventos do lote) reaplica o mesmo filtro, o lote de uma
    empresa NUNCA aparece no painel de outra — mesmo que duas empresas
    chamem senhas exatamente ao mesmo tempo, cada painel de empresa
    continua vendo apenas o próprio lote mais recente. Eventos antigos
    sem ``lote_chamada`` (gravados antes desta coluna existir — ver
    ``_migrar_tabela_eventos_chamada_adicionar_lote``) são tratados como
    um lote de uma única senha (o próprio evento).

    IMPORTANTE (senhas já finalizadas somem do destaque): o passo 2 só
    traz eventos cuja senha AINDA esteja com status 'Chamada' (em
    atendimento) — uma senha já 'Finalizada' (ou cancelada depois de
    chamada, caso raro) sai da lista, mesmo que continue sendo,
    tecnicamente, o evento mais recente em ``eventos_chamada`` (uma
    tabela só de LOG, nunca reescrita). Sem esse filtro, o painel
    público continuaria destacando para sempre a última senha chamada
    mesmo depois dela já ter sido atendida e finalizada — só troca de
    destaque quando uma NOVA chamada acontece. Se, depois do filtro,
    nenhuma senha do lote mais recente ainda estiver em atendimento,
    a função retorna ``None`` (não recua para um lote mais antigo — um
    destaque antigo seria tão ou mais confuso quanto nenhum destaque),
    e o painel exibe a mensagem de espera (ver static/js/painel.js e
    static/js/painel_empresa.js).
    """
    condicao_empresa = "WHERE s.empresa_id = ?" if empresa_id else ""
    parametros_empresa = [empresa_id] if empresa_id else []

    with get_connection() as conexao:
        # Passo 1: descobre o evento (e o lote) mais recente, SEM filtrar
        # por status ainda — precisamos saber qual foi a ÚLTIMA operação
        # de chamada realizada, independente de já ter sido finalizada,
        # para não "ressuscitar" um lote mais antigo no passo 2.
        ultimo = conexao.execute(
            f"""
            SELECT e.id, e.lote_chamada
            FROM eventos_chamada e
            JOIN senhas s ON s.id = e.senha_id
            {condicao_empresa}
            ORDER BY e.id DESC
            LIMIT 1
            """,
            parametros_empresa,
        ).fetchone()

        if ultimo is None:
            return None

        # Passo 2: busca TODOS os eventos daquele lote (1 ou vários),
        # SEM filtrar por status ainda — precisamos do lote completo,
        # na ordem original, para que os campos de nível raiz (id,
        # numero, guiche...) fiquem estáveis mesmo que uma das senhas do
        # lote seja finalizada individualmente enquanto as demais
        # continuam em atendimento. Para eventos antigos sem
        # lote_chamada, cai de volta a buscar só pelo próprio id
        # (equivalente ao comportamento anterior a esta função existir).
        #
        # IMPORTANTE (bug corrigido na revisão geral do sistema): antes,
        # esse filtro por status já vinha aplicado aqui no SQL, e o
        # "primeiro" evento (usado nos campos de nível raiz) era
        # recalculado a cada chamada desta função a partir da lista JÁ
        # filtrada. Num lote com várias senhas, quando a PRIMEIRA delas
        # era finalizada antes das demais, o "primeiro" evento restante
        # passava a ser outro (id diferente) mesmo sem nenhuma chamada
        # nova ter ocorrido — e como o painel usa justamente esse "id"
        # para decidir se toca o bipe/anima a tela (ver
        # static/js/painel.js), isso disparava um re-anúncio falso no
        # meio do atendimento normal do lote.
        if ultimo["lote_chamada"]:
            condicao_lote = "WHERE e.lote_chamada = ?"
            parametros_lote = [ultimo["lote_chamada"]]
        else:
            condicao_lote = "WHERE e.id = ?"
            parametros_lote = [ultimo["id"]]

        if empresa_id:
            condicao_lote += " AND s.empresa_id = ?"
            parametros_lote.append(empresa_id)

        linhas = conexao.execute(
            f"""
            SELECT e.*, s.empresa AS empresa, s.status AS status_senha
            FROM eventos_chamada e
            JOIN senhas s ON s.id = e.senha_id
            {condicao_lote}
            ORDER BY e.id ASC
            """,
            parametros_lote,
        ).fetchall()

    if not linhas:
        return None

    todos_eventos_lote = [ChamadaEvento.from_row(linha).to_dict() for linha in linhas]
    # Guarda o status de cada senha (não faz parte de ChamadaEvento) só
    # para filtrar a lista de exibição logo abaixo.
    status_por_linha = [linha["status_senha"] for linha in linhas]

    # Campos de nível raiz sempre espelham o PRIMEIRO evento do LOTE
    # COMPLETO (estável independente de finalizações parciais) — mantém
    # compatibilidade com quem só lia um evento único antes de existir o
    # conceito de lote (ex.: comparar "id" para detectar mudança e
    # disparar bip/animação continua funcionando, já que o "id" do
    # primeiro evento só muda quando um NOVO lote é chamado).
    primeiro = todos_eventos_lote[0]

    # Lista exibida no painel: só as senhas do lote AINDA em atendimento
    # ('Chamada') — uma senha já 'Finalizada' (ou cancelada depois de
    # chamada, caso raro) some da lista visível, mesmo continuando no
    # log. Se nenhuma senha do lote mais recente ainda estiver em
    # atendimento, não há nada para destacar (ver docstring da função).
    eventos_ativos = [
        evento
        for evento, status in zip(todos_eventos_lote, status_por_linha)
        if status == StatusSenha.CHAMADA
    ]

    if not eventos_ativos:
        return None

    return {
        "id": primeiro["id"],
        "lote_chamada": primeiro["lote_chamada"],
        "senha_id": primeiro["senha_id"],
        "numero": primeiro["numero"],
        "guiche": primeiro["guiche"],
        "usuario": primeiro["usuario"],
        "data_hora": primeiro["data_hora"],
        "empresa": primeiro["empresa"],
        # Lista com as senhas do lote AINDA em atendimento — usada pelo
        # painel público para montar a "sequência chamada" (ver
        # static/js/painel.js e static/js/painel_empresa.js).
        "senhas": eventos_ativos,
    }


# ---------------------------------------------------------------------------
# Consultas para o painel público
# ---------------------------------------------------------------------------

def listar_ultimas_emitidas(quantidade: int = 10, empresa_id: Optional[int] = None) -> List[Dict]:
    """
    Retorna as últimas N senhas emitidas, ordenadas da mais recente para
    a mais antiga. Utilizado pelos painéis públicos (``painel.html`` e
    ``painel_empresa.html``) para exibir o histórico de senhas emitidas.

    Exclui senhas com status 'Finalizada' ou 'Cancelada': os painéis
    públicos devem mostrar apenas o que está em andamento (aguardando
    chamada ou em atendimento) — um atendimento já encerrado ou
    cancelado não tem mais utilidade nesse tipo de display, projetado
    para tela cheia (TV/monitor) mostrando a situação ATUAL da fila.

    ``empresa_id`` restringe o histórico a uma única empresa — usado pelo
    painel público de uma empresa (``/painel/empresa/<id>``).
    """
    condicao = "WHERE status NOT IN (?, ?)"
    parametros: List = [StatusSenha.FINALIZADA, StatusSenha.CANCELADA]
    if empresa_id:
        condicao += " AND empresa_id = ?"
        parametros.append(empresa_id)
    parametros.append(quantidade)

    with get_connection() as conexao:
        linhas = conexao.execute(
            f"SELECT * FROM senhas {condicao} ORDER BY id DESC LIMIT ?",
            parametros,
        ).fetchall()

    return [Senha.from_row(linha).to_dict() for linha in linhas]


def contar_aguardando(empresa_id: Optional[int] = None, busca: Optional[str] = None) -> int:
    """
    Retorna a quantidade de senhas atualmente aguardando chamada.

    ``empresa_id`` restringe a contagem a uma única empresa — usado pela
    fila do perfil "recrutador" e pelo painel público de uma empresa.

    ``busca`` (opcional) aplica o mesmo filtro de texto usado por
    ``listar_fila_atual`` (número da senha ou nome da pessoa) — usado
    pelo campo de pesquisa da Fila de Espera (ver app.py:api_fila) para
    calcular o total de páginas de resultados. Quando omitido, o
    comportamento é idêntico ao de antes desta busca existir.
    """
    condicao = "WHERE status = ?"
    parametros: List = [StatusSenha.EMITIDA]
    if empresa_id:
        condicao += " AND empresa_id = ?"
        parametros.append(empresa_id)
    if busca:
        condicao += " AND (printf('%03d', numero) LIKE ? OR nome_pessoa LIKE ?)"
        termo_busca = f"%{busca.strip()}%"
        parametros.extend([termo_busca, termo_busca])

    with get_connection() as conexao:
        linha = conexao.execute(
            f"SELECT COUNT(*) AS total FROM senhas {condicao}", parametros
        ).fetchone()
    return int(linha["total"])


def contar_emitidas_hoje(empresa_id: Optional[int] = None) -> int:
    """
    Retorna quantas senhas foram emitidas HOJE, em QUALQUER status
    (Emitida, Chamada, Finalizada ou Cancelada) — usado pelo contador
    "Emitidas Hoje" da tela principal (ver app.py:api_fila).

    Existe porque nem "Fila de Espera" (``contar_aguardando``, só conta
    status Emitida) nem os Painéis públicos (``listar_ultimas_emitidas``/
    ``resumo_geral_senhas`` renderizado por ``painel_geral.js``, que
    propositalmente ocultam Finalizada/Cancelada — ver Painel Geral)
    servem como confirmação, para quem está EMITINDO senhas, de que uma
    senha realmente foi registrada: as duas opções fixas de emissão
    ("Criar Currículos"/"Imprimir Currículos", ver
    ``NOMES_EMPRESAS_FIXAS``) nascem já com status 'Finalizada' (ver
    ``criar_senha``, ``finalizar_imediatamente``), então NUNCA aparecem
    na Fila de Espera nem nesses painéis — para quem só usa a tela do
    Emissor, era como se a emissão tivesse "sumido", mesmo estando
    corretamente contabilizada nos Relatórios (tela que o perfil Emissor
    não tem permissão para abrir). Esta função conta por
    ``date(data_hora)``, sem nenhum filtro de status, para servir de
    confirmação visível também para essas senhas fixas.

    ``empresa_id``, quando informado, restringe a contagem a uma única
    empresa (usado para o recrutador ver só o total da própria empresa).
    """
    hoje = datetime.now().strftime("%Y-%m-%d")
    condicao = "WHERE date(data_hora) = date(?)"
    parametros: List = [hoje]
    if empresa_id:
        condicao += " AND empresa_id = ?"
        parametros.append(empresa_id)

    with get_connection() as conexao:
        linha = conexao.execute(
            f"SELECT COUNT(*) AS total FROM senhas {condicao}", parametros
        ).fetchone()
    return int(linha["total"])


# ---------------------------------------------------------------------------
# Gerenciamento manual de senhas (finalizar / cancelar)
# ---------------------------------------------------------------------------

def finalizar_senha(senha_id: int) -> bool:
    """
    Marca uma senha como 'Finalizada' (atendimento concluído) e grava
    ``hora_finalizada`` — junto com ``hora_chamada`` (ver
    ``chamar_proxima``), permite calcular o tempo de atendimento nos
    relatórios.
    """
    with get_connection() as conexao:
        cursor = conexao.execute(
            "UPDATE senhas SET status = ?, hora_finalizada = ? WHERE id = ?",
            (StatusSenha.FINALIZADA, _agora_iso(), senha_id),
        )
        conexao.commit()
        alterou = cursor.rowcount > 0

    if alterou:
        registrar_log("INFO", f"Senha id={senha_id} finalizada.")
    return alterou


def cancelar_senha(senha_id: int) -> bool:
    """
    Marca uma senha como 'Cancelada' (não será chamada).

    Só é permitido cancelar uma senha que ainda esteja com status
    'Emitida' (aguardando na fila) — corrigido na revisão geral do
    sistema: antes, esta função cancelava uma senha em QUALQUER status,
    inclusive uma que já tivesse sido chamada. Cancelar uma senha já
    chamada criava uma inconsistência entre os números do Painel Geral:
    ela saía de "Total de Senhas Emitidas" (resumo_feirao exclui
    Canceladas — ver ``obter_resumo_feirao``), mas continuava contando
    em "Total de Atendimentos Realizados"
    (``contar_chamadas_realizadas_periodo`` só olha se ``hora_chamada``
    foi preenchida, sem checar o status atual da senha).
    """
    with get_connection() as conexao:
        cursor = conexao.execute(
            "UPDATE senhas SET status = ? WHERE id = ? AND status = ?",
            (StatusSenha.CANCELADA, senha_id, StatusSenha.EMITIDA),
        )
        conexao.commit()
        alterou = cursor.rowcount > 0

    if alterou:
        registrar_log("WARNING", f"Senha id={senha_id} cancelada.")
    return alterou


def obter_senha_por_id(senha_id: int) -> Optional[Senha]:
    """Busca uma senha pelo id. Usado para checar a que empresa uma senha
    pertence antes de permitir que um recrutador a cancele/finalize (ver
    app.py:_pode_gerenciar_senha)."""
    with get_connection() as conexao:
        linha = conexao.execute("SELECT * FROM senhas WHERE id = ?", (senha_id,)).fetchone()
    return Senha.from_row(linha) if linha else None


def listar_fila_atual(
    empresa_id: Optional[int] = None,
    busca: Optional[str] = None,
    pagina: int = 1,
    por_pagina: int = 20,
) -> List[Dict]:
    """
    Retorna as senhas atualmente aguardando chamada (status Emitida), em
    ordem de chegada, para exibição em uma tela de gerenciamento.

    ``empresa_id`` restringe a fila a uma única empresa — usado pelo
    perfil "recrutador", que só deve ver/gerenciar a fila da sua própria
    empresa.

    ``busca`` (opcional) filtra por número da senha (comparado já
    formatado com três dígitos, ex.: buscar "007" ou só "7" encontra a
    senha 007) ou por trecho do nome da pessoa (``nome_pessoa``, busca
    parcial). Usado pelo campo de pesquisa da Fila de Espera (ver
    app.py:api_fila), para localizar rapidamente uma senha específica —
    por exemplo, antes de reimprimir ou cancelar — mesmo quando a fila
    tem mais itens do que cabem em uma página. Comparação via SQL LIKE:
    insensível a maiúsculas/minúsculas apenas para letras sem acento
    (limitação do próprio SQLite).

    ``pagina``/``por_pagina`` paginam o resultado (``pagina`` é
    1-indexada): antes desta paginação, a fila sempre trazia só as 20
    senhas mais antigas, tornando qualquer uma além dessas inacessível
    (inclusive para busca) quando havia mais de 20 aguardando. Ver
    ``contar_aguardando`` para o total de páginas.
    """
    condicao = "WHERE status = ?"
    parametros: List = [StatusSenha.EMITIDA]
    if empresa_id:
        condicao += " AND empresa_id = ?"
        parametros.append(empresa_id)
    if busca:
        condicao += " AND (printf('%03d', numero) LIKE ? OR nome_pessoa LIKE ?)"
        termo_busca = f"%{busca.strip()}%"
        parametros.extend([termo_busca, termo_busca])

    pagina = max(int(pagina or 1), 1)
    por_pagina = max(int(por_pagina or 20), 1)
    offset = (pagina - 1) * por_pagina
    parametros.extend([por_pagina, offset])

    with get_connection() as conexao:
        linhas = conexao.execute(
            f"SELECT * FROM senhas {condicao} ORDER BY id ASC LIMIT ? OFFSET ?",
            parametros,
        ).fetchall()

    return [Senha.from_row(linha).to_dict() for linha in linhas]


# ---------------------------------------------------------------------------
# Relatórios
# ---------------------------------------------------------------------------

def listar_senhas_periodo(
    inicio: Optional[str] = None,
    fim: Optional[str] = None,
    status: Optional[str] = None,
    empresa_id: Optional[int] = None,
) -> List[Dict]:
    """
    Retorna as senhas emitidas dentro de um período (datas no formato
    'YYYY-MM-DD'), opcionalmente filtradas por status e/ou por empresa.
    Utilizado pela geração de relatórios (CSV, Excel, PDF).

    ``empresa_id`` (não o nome) é usado no filtro — importante para o
    relatório do recrutador (``app.py:api_relatorios_*``), que SEMPRE
    força o próprio ``empresa_id`` da sessão, nunca confiando em um nome
    vindo do cliente (ver motivo em
    ``_migrar_tabela_senhas_adicionar_empresa_id``).
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
    if empresa_id:
        condicoes.append("empresa_id = ?")
        parametros.append(empresa_id)

    where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""

    with get_connection() as conexao:
        linhas = conexao.execute(
            f"SELECT * FROM senhas {where} ORDER BY id ASC",
            parametros,
        ).fetchall()

    return [Senha.from_row(linha).to_dict() for linha in linhas]


def listar_chamadas_periodo(
    inicio: Optional[str] = None,
    fim: Optional[str] = None,
    empresa_id: Optional[int] = None,
) -> List[Dict]:
    """
    Retorna todos os eventos de chamada realizados dentro de um período,
    opcionalmente filtrados pela empresa da senha chamada (por
    ``empresa_id``, via JOIN com ``senhas``, já que ``eventos_chamada``
    não duplica esse dado).
    """
    condicoes = []
    parametros: List = []

    if inicio:
        condicoes.append("date(e.data_hora) >= date(?)")
        parametros.append(inicio)
    if fim:
        condicoes.append("date(e.data_hora) <= date(?)")
        parametros.append(fim)
    if empresa_id:
        condicoes.append("s.empresa_id = ?")
        parametros.append(empresa_id)

    where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""

    with get_connection() as conexao:
        linhas = conexao.execute(
            f"""
            SELECT e.*, s.empresa AS empresa
            FROM eventos_chamada e
            JOIN senhas s ON s.id = e.senha_id
            {where}
            ORDER BY e.id ASC
            """,
            parametros,
        ).fetchall()

    resultado = []
    for linha in linhas:
        evento = ChamadaEvento.from_row(linha).to_dict()
        evento["empresa"] = linha["empresa"]
        resultado.append(evento)
    return resultado


def contar_chamadas_realizadas_periodo(
    inicio: Optional[str] = None,
    fim: Optional[str] = None,
    empresa_id: Optional[int] = None,
) -> int:
    """
    Conta quantas senhas foram efetivamente chamadas (ou, no caso das
    duas opções fixas do sistema, já nasceram diretamente "realizadas")
    dentro de um período — usado pelo "Resumo do Período" da tela de
    Relatórios (ver app.py:api_relatorios_resumo).

    Baseada em ``senhas.hora_chamada`` (``WHERE hora_chamada IS NOT
    NULL``), em vez de contar linhas em ``eventos_chamada`` — essa coluna
    é preenchida em exatamente DOIS momentos, e nunca mais reescrita
    depois:

        1. Na PRIMEIRA transição de uma senha para o status 'Chamada'
           (ver ``chamar_proxima``). Repetições de chamada
           (``repetir_ultima_chamada``) gravam um NOVO evento em
           ``eventos_chamada`` para a MESMA senha, mas NÃO tocam em
           ``hora_chamada`` — por isso contar por esta coluna já não
           sofre com a inflação de "repetição = mais uma chamada",
           preservando o invariante de negócio "chamadas realizadas"
           nunca é maior que "senhas emitidas".
        2. Na CRIAÇÃO de uma senha para uma das duas opções fixas do
           sistema, "Criar Currículos"/"Imprimir Currículos" (ver
           ``criar_senha``, parâmetro ``finalizar_imediatamente``) —
           elas não têm fila nem chamada, mas SÃO um atendimento
           realizado, então devem contar aqui também. Como não geram
           nenhum evento em ``eventos_chamada`` (não existe guichê
           "chamando" ninguém), contar a partir de ``eventos_chamada``
           (como esta função fazia antes) as deixava de fora da soma —
           usar ``hora_chamada`` inclui-as automaticamente, sem precisar
           fabricar um evento de chamada artificial (o que poluiria o
           relatório de exportação "Chamadas Realizadas", que é um log
           real de anúncios feitos em guichês/mesas).

    IMPORTANTE (filtro de período usa a data de EMISSÃO, não a de
    chamada — corrigido na revisão geral do sistema): o período
    (``inicio``/``fim``) filtra por ``date(data_hora)`` — a mesma coluna
    usada por TODAS as demais consultas de Relatórios
    (``listar_senhas_periodo``, ``listar_contagem_por_empresa``,
    ``tempo_medio_atendimento``) — em vez de ``date(hora_chamada)`` como
    antes. Com o filtro na coluna de chamada, uma senha emitida perto da
    virada do dia e só chamada no dia seguinte contava em "Atendidas" num
    dia diferente de "Emitidas", quebrando o invariante básico de um
    relatório de período (atendidas nunca maior que emitidas NAQUELE
    período) e confundindo quem lê o resumo. ``hora_chamada IS NOT NULL``
    continua sendo o filtro de "foi de fato atendida" — só a coluna usada
    para decidir SE a senha pertence ao período pedido mudou.
    """
    condicoes = ["hora_chamada IS NOT NULL"]
    parametros: List = []

    if inicio:
        condicoes.append("date(data_hora) >= date(?)")
        parametros.append(inicio)
    if fim:
        condicoes.append("date(data_hora) <= date(?)")
        parametros.append(fim)
    if empresa_id:
        condicoes.append("empresa_id = ?")
        parametros.append(empresa_id)

    where = " AND ".join(condicoes)

    with get_connection() as conexao:
        linha = conexao.execute(
            f"SELECT COUNT(*) AS total FROM senhas WHERE {where}",
            parametros,
        ).fetchone()
    return int(linha["total"])


def listar_contagem_por_empresa(
    inicio: Optional[str] = None, fim: Optional[str] = None, empresa_id: Optional[int] = None
) -> List[Dict]:
    """
    Retorna a quantidade de senhas emitidas por empresa dentro de um
    período, ordenada da mais requisitada para a menos requisitada, e
    também quantas dessas senhas foram efetivamente ATENDIDAS
    (``atendidas``) — usado pela coluna "Senhas Atendidas" da tabela
    "Senhas por Empresa" da tela de Relatórios (ver
    app.py:api_relatorios_resumo/templates/relatorios.html). Senhas sem
    empresa associada (emitidas antes desta funcionalidade existir) são
    agrupadas sob o rótulo "Não informado".

    ``atendidas`` usa o mesmo critério de ``hora_chamada IS NOT NULL``
    de ``contar_chamadas_realizadas_periodo`` (preenchida na primeira
    chamada de guichê, ou na criação de uma senha das duas opções
    fixas "Criar Currículos"/"Imprimir Currículos") — nunca maior que
    ``total`` da mesma linha, e imune à inflação por repetição de
    chamada, pelo mesmo motivo já documentado naquela função.

    ``empresa_id``, quando informado, restringe o resultado a UMA única
    empresa (usado pelo relatório do recrutador, que só vê a própria).
    """
    condicoes = []
    parametros: List = []

    if inicio:
        condicoes.append("date(data_hora) >= date(?)")
        parametros.append(inicio)
    if fim:
        condicoes.append("date(data_hora) <= date(?)")
        parametros.append(fim)
    if empresa_id:
        condicoes.append("empresa_id = ?")
        parametros.append(empresa_id)

    where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""

    with get_connection() as conexao:
        linhas = conexao.execute(
            f"""
            SELECT
                COALESCE(empresa, 'Não informado') AS empresa,
                COUNT(*) AS total,
                SUM(CASE WHEN hora_chamada IS NOT NULL THEN 1 ELSE 0 END) AS atendidas
            FROM senhas
            {where}
            GROUP BY COALESCE(empresa, 'Não informado')
            ORDER BY total DESC, empresa ASC
            """,
            parametros,
        ).fetchall()

    return [
        {"empresa": linha["empresa"], "total": linha["total"], "atendidas": linha["atendidas"]}
        for linha in linhas
    ]


def resumo_geral_senhas() -> Dict:
    """
    Calcula o resumo agregado (TODOS os tempos, sem filtro de período) de
    senhas por status — total geral e, dentro de cada status, a
    contagem por empresa — usado pelo "Painel Geral" público
    (``/painel/geral``).

    Diferente de ``listar_contagem_por_empresa`` (que soma TODAS as
    senhas de uma empresa, independente do status), aqui cada empresa
    aparece com a contagem separada em aguardando/em atendimento/
    atendidas/canceladas, permitindo montar uma tabela-resumo por empresa
    no painel.
    """
    with get_connection() as conexao:
        linhas_status = conexao.execute(
            "SELECT status, COUNT(*) AS total FROM senhas GROUP BY status"
        ).fetchall()
        linhas_empresa = conexao.execute(
            """
            SELECT COALESCE(empresa, 'Não informado') AS empresa, status, COUNT(*) AS total
            FROM senhas
            GROUP BY COALESCE(empresa, 'Não informado'), status
            """
        ).fetchall()

    totais = {status: 0 for status in StatusSenha.TODOS}
    for linha in linhas_status:
        if linha["status"] in totais:
            totais[linha["status"]] = linha["total"]

    por_empresa: Dict[str, Dict[str, int]] = {}
    for linha in linhas_empresa:
        empresa = linha["empresa"]
        dados_empresa = por_empresa.setdefault(empresa, {status: 0 for status in StatusSenha.TODOS})
        if linha["status"] in dados_empresa:
            dados_empresa[linha["status"]] = linha["total"]

    lista_empresas = [
        {
            "empresa": empresa,
            "aguardando": dados[StatusSenha.EMITIDA],
            "em_atendimento": dados[StatusSenha.CHAMADA],
            "atendidas": dados[StatusSenha.FINALIZADA],
            "canceladas": dados[StatusSenha.CANCELADA],
            "total": sum(dados.values()),
        }
        for empresa, dados in sorted(por_empresa.items(), key=lambda item: item[0])
    ]

    return {
        "total_emitidas": sum(totais.values()),
        "total_aguardando": totais[StatusSenha.EMITIDA],
        "total_em_atendimento": totais[StatusSenha.CHAMADA],
        "total_atendidas": totais[StatusSenha.FINALIZADA],
        "total_canceladas": totais[StatusSenha.CANCELADA],
        "por_empresa": lista_empresas,
    }


def tempo_medio_atendimento(
    inicio: Optional[str] = None,
    fim: Optional[str] = None,
    empresa_id: Optional[int] = None,
) -> Dict:
    """
    Calcula o tempo médio de espera entre a emissão da senha e a sua
    primeira chamada, em segundos, para as senhas emitidas dentro do
    período informado (opcionalmente filtradas por empresa).

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
    if empresa_id:
        condicoes.append("s.empresa_id = ?")
        parametros.append(empresa_id)

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


def criar_usuario(
    nome_completo: str,
    login: str,
    senha_hash: str,
    perfil: Optional[str] = None,
    empresa_id: Optional[int] = None,
) -> Usuario:
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

    ``empresa_id`` só faz sentido quando ``perfil`` é "recrutador" — a
    validação de que foi informado (e de que a empresa existe) é
    responsabilidade da camada de rotas (``app.py``), não desta função.
    """
    login_normalizado = (login or "").strip().lower()

    if perfil is None:
        perfil = PerfilUsuario.ADMIN if contar_usuarios() == 0 else PerfilUsuario.ATENDENTE

    data_criacao = _agora_iso()

    with get_connection() as conexao:
        try:
            cursor = conexao.execute(
                """
                INSERT INTO usuarios (nome_completo, login, senha_hash, perfil, ativo, data_criacao, empresa_id)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (nome_completo, login_normalizado, senha_hash, perfil, data_criacao, empresa_id),
            )
            conexao.commit()
        except sqlite3.IntegrityError as erro:
            # Importante: nem todo IntegrityError é login duplicado — pode
            # ser, por exemplo, uma restrição CHECK antiga de uma versão
            # anterior do banco (ver ``_migrar_tabela_usuarios_sem_check``).
            # Diferenciar evita mostrar "login já existe" para um erro que
            # na verdade é outra coisa completamente diferente.
            if "UNIQUE" in str(erro).upper():
                raise ValueError(f"Já existe um usuário com o login '{login_normalizado}'.") from erro
            raise ValueError(f"Não foi possível criar o usuário: {erro}") from erro

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
        empresa_id=empresa_id,
    )


def provisionar_usuario_recrutador(empresa_id: int, nome_completo: str, senha_hash: str) -> Usuario:
    """
    Cria automaticamente uma conta de recrutador EFÊMERA, vinculada a
    ``empresa_id``, no momento em que alguém entra pela chave de acesso da
    empresa (ver app.py:api_empresa_entrar). Substitui o cadastro manual
    que um administrador fazia antes em "Gerenciar Usuários" — agora
    qualquer pessoa da empresa que souber a chave pode entrar, informando
    apenas o próprio nome.

    O ``login`` gravado é sintético (nunca digitado nem exibido a
    ninguém — apenas precisa ser único no banco). ``senha_hash`` deve ser
    o hash de um valor aleatório gerado pelo CHAMADOR (ver
    auth.gerar_hash_senha, mesmo padrão já usado por ``criar_usuario`` —
    este módulo não gera hashes de senha diretamente, essa
    responsabilidade é de ``auth.py``): esta conta NUNCA é autenticada por
    login/senha (ver auth.autenticar, que recusa qualquer conta com perfil
    "recrutador"), apenas por sessão já iniciada diretamente em
    auth.iniciar_sessao. Marcada com ``provisionado_por_chave=True`` para
    que ``excluir_usuario_provisionado`` possa apagá-la com segurança ao
    encerrar a sessão (ver auth.encerrar_sessao), sem nunca arriscar
    apagar uma conta cadastrada manualmente por um admin.
    """
    nome_normalizado = (nome_completo or "").strip()
    if not nome_normalizado:
        raise ValueError("Informe seu nome para entrar.")

    login_sintetico = f"chave-empresa-{empresa_id}-{secrets.token_hex(8)}"
    data_criacao = _agora_iso()

    with get_connection() as conexao:
        cursor = conexao.execute(
            """
            INSERT INTO usuarios
                (nome_completo, login, senha_hash, perfil, ativo, data_criacao, empresa_id, provisionado_por_chave)
            VALUES (?, ?, ?, ?, 1, ?, ?, 1)
            """,
            (nome_normalizado, login_sintetico, senha_hash, PerfilUsuario.RECRUTADOR, data_criacao, empresa_id),
        )
        conexao.commit()
        usuario_id = cursor.lastrowid

    registrar_log(
        "INFO",
        f"Conta de recrutador provisionada automaticamente para '{nome_normalizado}' "
        f"(empresa id={empresa_id}) via chave de acesso.",
    )

    return Usuario(
        id=usuario_id,
        nome_completo=nome_normalizado,
        login=login_sintetico,
        senha_hash=senha_hash,
        perfil=PerfilUsuario.RECRUTADOR,
        ativo=True,
        data_criacao=data_criacao,
        ultimo_login=None,
        empresa_id=empresa_id,
        provisionado_por_chave=True,
    )


def excluir_usuario_provisionado(usuario_id: int) -> None:
    """
    Remove definitivamente uma conta de recrutador EFÊMERA (ver
    ``provisionar_usuario_recrutador``), chamada ao encerrar a sessão (ver
    ``auth.encerrar_sessao``) — mantém a tabela ``usuarios`` limpa em vez
    de acumular uma linha nova a cada entrada pela chave da empresa.

    Salvaguarda importante: o ``DELETE`` só afeta linhas com
    ``provisionado_por_chave = 1`` — mesmo que um id incorreto ou
    manipulado chegue até aqui, uma conta cadastrada manualmente por um
    administrador NUNCA é apagada por este caminho.

    Não apaga o histórico de senhas/chamadas: ``senhas.usuario`` e
    ``eventos_chamada.usuario`` gravam o NOME como texto congelado no
    momento do evento (não uma referência viva ao usuário), então
    relatórios e o painel continuam corretos após esta exclusão.
    """
    with get_connection() as conexao:
        conexao.execute(
            "DELETE FROM usuarios WHERE id = ? AND provisionado_por_chave = 1",
            (usuario_id,),
        )
        conexao.commit()


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
    """
    Retorna todos os usuários cadastrados (sem o hash de senha), para a
    tela de administração de usuários.

    Cada dicionário retornado inclui também a chave ``empresa_nome``
    (via LEFT JOIN com ``empresas``), pronta para exibição direta na
    tabela de usuários — ``None`` para quem não é recrutador ou ainda não
    tem empresa vinculada.
    """
    with get_connection() as conexao:
        linhas = conexao.execute(
            """
            SELECT u.*, e.nome AS empresa_nome
            FROM usuarios u
            LEFT JOIN empresas e ON e.id = u.empresa_id
            ORDER BY u.nome_completo ASC
            """
        ).fetchall()

    resultado = []
    for linha in linhas:
        usuario_dict = Usuario.from_row(linha).to_dict_publico()
        usuario_dict["empresa_nome"] = linha["empresa_nome"]
        resultado.append(usuario_dict)
    return resultado


def atualizar_ultimo_login(usuario_id: int) -> None:
    """Atualiza o timestamp de último login do usuário."""
    with get_connection() as conexao:
        conexao.execute(
            "UPDATE usuarios SET ultimo_login = ? WHERE id = ?",
            (_agora_iso(), usuario_id),
        )
        conexao.commit()


def definir_perfil_usuario(usuario_id: int, perfil: str) -> bool:
    """
    Altera o perfil (admin/atendente/emissor/recrutador) de um usuário.
    Apenas administradores podem chamar esta operação (validado em
    app.py).

    Sempre que o NOVO perfil for diferente de "recrutador", o vínculo com
    a empresa (``empresa_id``) é limpo automaticamente — evita deixar um
    atendente/emissor/admin com um vínculo de empresa "fantasma" de uma
    época em que ele era recrutador. Para VINCULAR (ou trocar) a empresa
    de um recrutador, use ``definir_empresa_usuario`` separadamente.
    """
    if perfil not in PerfilUsuario.TODOS:
        raise ValueError(f"Perfil inválido: {perfil}")

    with get_connection() as conexao:
        try:
            if perfil == PerfilUsuario.RECRUTADOR:
                cursor = conexao.execute(
                    "UPDATE usuarios SET perfil = ? WHERE id = ?", (perfil, usuario_id)
                )
            else:
                cursor = conexao.execute(
                    "UPDATE usuarios SET perfil = ?, empresa_id = NULL WHERE id = ?",
                    (perfil, usuario_id),
                )
            conexao.commit()
        except sqlite3.IntegrityError as erro:
            # Em tese não deveria mais ocorrer (ver
            # ``_migrar_tabela_usuarios_sem_check``), mas se acontecer,
            # relata de forma clara em vez de deixar a exceção crua subir.
            raise ValueError(
                f"Não foi possível definir o perfil '{perfil}': {erro}. "
                "Reinicie o servidor para aplicar a migração automática do banco de dados."
            ) from erro
        alterou = cursor.rowcount > 0

    if alterou:
        registrar_log("WARNING", f"Perfil do usuário id={usuario_id} alterado para '{perfil}'.")
    return alterou


def definir_empresa_usuario(usuario_id: int, empresa_id: Optional[int]) -> bool:
    """
    Vincula (ou desvincula, passando ``empresa_id=None``) um recrutador a
    uma empresa. A validação de que a empresa existe é responsabilidade
    da camada de rotas (``app.py``), não desta função — assim como
    ``definir_perfil_usuario`` não valida se o usuário faz sentido ter o
    perfil escolhido.

    Não força o perfil do usuário a ser "recrutador": é possível vincular
    uma empresa a qualquer usuário (o vínculo só tem EFEITO prático —
    ocupar uma mesa ao logar — para quem tem perfil "recrutador", ver
    ``auth.iniciar_sessao``), mas isso é intencional: permite ao
    administrador pré-configurar a empresa antes de trocar o perfil, sem
    depender da ordem das duas ações.
    """
    with get_connection() as conexao:
        cursor = conexao.execute(
            "UPDATE usuarios SET empresa_id = ? WHERE id = ?", (empresa_id, usuario_id)
        )
        conexao.commit()
        alterou = cursor.rowcount > 0

    if alterou:
        registrar_log(
            "WARNING", f"Empresa do usuário id={usuario_id} definida para empresa_id={empresa_id}."
        )
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
    reiniciando também o contador de numeração de TODAS as empresas para
    zero (cada empresa tem sua própria sequência — ver ``criar_senha``).

    Esta é uma operação destrutiva e irreversível, disponível apenas para
    administradores (validado em app.py), útil por exemplo no início de um
    novo evento/feirão, quando se deseja começar do zero sem nenhum
    resquício de dados do evento anterior.
    """
    with get_connection() as conexao:
        conexao.execute("DELETE FROM eventos_chamada")
        conexao.execute("DELETE FROM senhas")
        conexao.execute("DELETE FROM sqlite_sequence WHERE name IN ('senhas', 'eventos_chamada')")
        conexao.execute("UPDATE empresas SET contador_atual = 0")
        conexao.commit()

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


# ---------------------------------------------------------------------------
# Ocupação de guichês (mesas) POR EMPRESA — perfil "recrutador"
# ---------------------------------------------------------------------------
#
# Pool independente do pool geral acima (usado pelo "atendente"): a mesma
# numeração de mesa pode ser ocupada simultaneamente por recrutadores de
# empresas diferentes, pois a chave primária de ``guiches_empresa_ocupados``
# é o par (empresa_id, guiche) — ver docstring da tabela em
# ``inicializar_banco``.

def obter_guiche_empresa_do_usuario(usuario_id: int) -> Optional[int]:
    """Retorna o número da mesa atualmente ocupada por um recrutador, ou
    ``None`` caso ele não esteja ocupando nenhuma mesa no momento."""
    with get_connection() as conexao:
        linha = conexao.execute(
            "SELECT guiche FROM guiches_empresa_ocupados WHERE usuario_id = ?", (usuario_id,)
        ).fetchone()
    return int(linha["guiche"]) if linha else None


def ocupar_proximo_guiche_empresa_disponivel(
    empresa_id: int, usuario_id: int, usuario_nome: str, qtd_guiches: int
) -> Optional[int]:
    """
    Atribui automaticamente ao recrutador a primeira mesa disponível
    (entre 1 e ``qtd_guiches``) DENTRO da empresa informada — mesma lógica
    de ``ocupar_proximo_guiche_disponivel``, mas com o pool restrito a
    ``empresa_id`` em vez de global.

    Se o usuário já estiver ocupando uma mesa (nesta ou em outra empresa
    — não deveria acontecer em uso normal, mas a busca já é por
    ``usuario_id`` sem filtrar empresa), retorna a mesma mesa (idempotente).
    Retorna ``None`` se não houver nenhuma mesa livre nesta empresa.
    """
    with _lock:
        guiche_atual = obter_guiche_empresa_do_usuario(usuario_id)
        if guiche_atual is not None:
            return guiche_atual

        with get_connection() as conexao:
            ocupadas = {
                linha["guiche"]
                for linha in conexao.execute(
                    "SELECT guiche FROM guiches_empresa_ocupados WHERE empresa_id = ?",
                    (empresa_id,),
                ).fetchall()
            }

            guiche_livre = next(
                (numero for numero in range(1, qtd_guiches + 1) if numero not in ocupadas),
                None,
            )

            if guiche_livre is None:
                return None

            conexao.execute(
                """
                INSERT INTO guiches_empresa_ocupados (empresa_id, guiche, usuario_id, usuario_nome, ocupado_desde)
                VALUES (?, ?, ?, ?, ?)
                """,
                (empresa_id, guiche_livre, usuario_id, usuario_nome, _agora_iso()),
            )
            conexao.commit()

    registrar_log(
        "INFO",
        f"Mesa {guiche_livre} (empresa id={empresa_id}) atribuída automaticamente a '{usuario_nome}'.",
    )
    return guiche_livre


def liberar_guiche_empresa(usuario_id: int) -> None:
    """Libera a mesa ocupada por um recrutador (chamado no logout, e
    também ao desativar o usuário — ver app.py:api_admin_definir_status).
    Não faz nada (sem erro) se o usuário não ocupava nenhuma mesa."""
    with get_connection() as conexao:
        conexao.execute("DELETE FROM guiches_empresa_ocupados WHERE usuario_id = ?", (usuario_id,))
        conexao.commit()


def listar_guiches_empresa_ocupados() -> List[Dict]:
    """Retorna a lista de mesas (guichês por empresa) atualmente ocupadas
    por recrutadores, já com o nome da empresa (via JOIN), útil para a
    tela de administração de usuários."""
    with get_connection() as conexao:
        linhas = conexao.execute(
            """
            SELECT g.*, e.nome AS empresa_nome
            FROM guiches_empresa_ocupados g
            JOIN empresas e ON e.id = g.empresa_id
            ORDER BY e.nome ASC, g.guiche ASC
            """
        ).fetchall()
    return [dict(linha) for linha in linhas]


# ---------------------------------------------------------------------------
# Empresas do feirão do emprego
# ---------------------------------------------------------------------------
#
# Cadastro simples (nome + status ativa/inativa), gerenciado exclusivamente
# por administradores pela tela "Empresas" (/admin/empresas). O nome da
# empresa selecionada é gravado como TEXTO na própria senha (coluna
# "empresa" de "senhas"), seguindo o mesmo padrão já usado para "guiche" e
# "usuario" nesta tabela — ou seja, sem FOREIGN KEY. Essa escolha é
# proposital: renomear ou desativar uma empresa nunca deve alterar o nome
# gravado em senhas já emitidas, preservando o histórico exato de cada
# atendimento para fins de relatório.

def criar_empresa(nome: str, fixa: bool = False) -> Empresa:
    """
    Cadastra uma nova empresa participante do feirão. O nome é
    normalizado (espaços nas pontas removidos) e deve ser único.

    Já nasce com uma ``chave_acesso`` (numérica, 8 dígitos) gerada
    automaticamente — é ela que os recrutadores dessa empresa usarão para
    entrar no sistema (ver app.py: rotas "/empresas/entrar" e
    "/empresas/<id>/entrar"), no lugar de login/senha individuais.

    ``fixa`` (``False`` por padrão — todo cadastro feito por um
    administrador pela tela Empresas usa o padrão) marca uma das duas
    opções fixas do sistema ("Criar Currículos"/"Imprimir Currículos" —
    ver ``NOMES_EMPRESAS_FIXAS``/``_semear_empresas_fixas``, única
    chamadora que passa ``fixa=True``).
    """
    nome_normalizado = (nome or "").strip()
    if not nome_normalizado:
        raise ValueError("Informe o nome da empresa.")

    data_criacao = _agora_iso()

    with get_connection() as conexao:
        chave_acesso = _gerar_chave_acesso_unica(conexao)
        try:
            cursor = conexao.execute(
                "INSERT INTO empresas (nome, ativa, data_criacao, chave_acesso, fixa) VALUES (?, 1, ?, ?, ?)",
                (nome_normalizado, data_criacao, chave_acesso, 1 if fixa else 0),
            )
            conexao.commit()
        except sqlite3.IntegrityError as erro:
            if "UNIQUE" in str(erro).upper():
                raise ValueError(
                    f"Já existe uma empresa cadastrada com o nome '{nome_normalizado}'."
                ) from erro
            raise ValueError(f"Não foi possível cadastrar a empresa: {erro}") from erro

        empresa_id = cursor.lastrowid

    registrar_log("INFO", f"Empresa '{nome_normalizado}' cadastrada (id={empresa_id}).")

    return Empresa(
        id=empresa_id,
        nome=nome_normalizado,
        ativa=True,
        data_criacao=data_criacao,
        chave_acesso=chave_acesso,
        fixa=fixa,
    )


# Nomes das duas opções fixas de emissão de senha do sistema (ver
# _semear_empresas_fixas). Não representam empresas reais participantes
# do feirão, e sim dois serviços de apoio ao candidato — ajuda para
# montar e para imprimir o currículo — sempre disponíveis para o Emissor,
# independente de quais empresas o administrador cadastrou. Senhas
# emitidas para elas já nascem "Finalizada" (ver criar_senha, parâmetro
# finalizar_imediatamente, e app.py:api_emitir): não existe fila nem
# chamada para esses dois serviços, então a senha entra direto como
# "realizada".
NOMES_EMPRESAS_FIXAS = ("Criar Currículos", "Imprimir Currículos")


def _semear_empresas_fixas() -> None:
    """
    Garante que as duas opções fixas do sistema (``NOMES_EMPRESAS_FIXAS``)
    existam como empresas com ``fixa = 1``, criando as que ainda
    faltarem. Chamada toda vez que o sistema inicia
    (``inicializar_banco``), depois que a coluna ``fixa`` já foi
    garantida pela migração — por isso não recebe ``conexao`` como
    parâmetro (abre sua própria conexão a cada nome, como
    ``criar_empresa``), em vez de rodar dentro do mesmo bloco ``with``
    das migrações.

    Idempotente: se uma empresa com esse nome já existir (comparação sem
    diferenciar maiúsculas/minúsculas, mesma regra de ``criar_empresa``),
    apenas garante que ``fixa`` esteja marcada, sem duplicar nem
    sobrescrever nada mais (chave de acesso, logo, cor etc. de uma
    empresa já existente com esse nome são preservados).
    """
    for nome in NOMES_EMPRESAS_FIXAS:
        with get_connection() as conexao:
            linha = conexao.execute(
                "SELECT id, fixa FROM empresas WHERE nome = ? COLLATE NOCASE", (nome,)
            ).fetchone()

        if linha is None:
            criar_empresa(nome, fixa=True)
            continue

        if not linha["fixa"]:
            with get_connection() as conexao:
                conexao.execute("UPDATE empresas SET fixa = 1 WHERE id = ?", (linha["id"],))
                conexao.commit()


def obter_empresa_por_chave(chave: str) -> Optional[Empresa]:
    """
    Busca uma empresa pela sua chave de acesso (login do recrutador — ver
    app.py:api_empresa_entrar). Não filtra por ``ativa`` aqui: a rota
    chamadora decide a mensagem apropriada (chave incorreta vs. empresa
    desativada), em vez de misturar os dois casos em um único "não
    encontrado" genérico.
    """
    chave_normalizada = (chave or "").strip()
    if not chave_normalizada:
        return None
    with get_connection() as conexao:
        linha = conexao.execute(
            "SELECT * FROM empresas WHERE chave_acesso = ?", (chave_normalizada,)
        ).fetchone()
    return Empresa.from_row(linha) if linha else None


def regenerar_chave_empresa(empresa_id: int) -> Optional[str]:
    """
    Gera uma NOVA chave de acesso para a empresa (invalidando a anterior)
    e a grava no lugar. Usado pelo administrador na tela Empresas quando a
    chave precisa ser trocada (ex.: suspeita de vazamento). Retorna a nova
    chave, ou ``None`` se a empresa não existir.
    """
    with get_connection() as conexao:
        existe = conexao.execute("SELECT 1 FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        if existe is None:
            return None

        nova_chave = _gerar_chave_acesso_unica(conexao)
        conexao.execute(
            "UPDATE empresas SET chave_acesso = ? WHERE id = ?", (nova_chave, empresa_id)
        )
        conexao.commit()

    registrar_log("INFO", f"Chave de acesso regenerada para a empresa id={empresa_id}.")
    return nova_chave


def listar_empresas(somente_ativas: bool = False) -> List[Dict]:
    """
    Retorna as empresas cadastradas, ordenadas por nome.

    ``somente_ativas=True`` é usado para popular o seletor exibido na
    emissão de senha (index.html/index.js) — empresas desativadas não
    devem mais receber novas senhas. A tela de administração e o filtro
    de relatórios, por outro lado, precisam ver TODAS as empresas
    (inclusive inativas), pois relatórios de eventos passados continuam
    consultáveis mesmo após a empresa ser desativada.

    As duas opções fixas do sistema ("Criar Currículos"/"Imprimir
    Currículos" — ver ``NOMES_EMPRESAS_FIXAS``) sempre aparecem PRIMEIRO
    na lista (``ORDER BY fixa DESC``), antes das empresas comuns em ordem
    alfabética — assim ficam fáceis de encontrar tanto no seletor de
    emissão quanto na tela de administração.
    """
    consulta = "SELECT * FROM empresas"
    if somente_ativas:
        consulta += " WHERE ativa = 1"
    consulta += " ORDER BY fixa DESC, nome ASC"

    with get_connection() as conexao:
        linhas = conexao.execute(consulta).fetchall()

    return [Empresa.from_row(linha).to_dict() for linha in linhas]


def listar_ultima_senha_por_empresa(somente_ativas: bool = True) -> List[Dict]:
    """
    Retorna, para CADA empresa cadastrada, os dados da última senha
    EMITIDA para ela (número, nome da pessoa, data/hora e status) e,
    separadamente, os dados da última senha CHAMADA (``hora_chamada``
    preenchida) — ou ``None`` nesses campos, quando a empresa ainda não
    tem nenhuma senha emitida/chamada. Usado pelo card "Última Senha
    por Empresa" da tela principal do perfil Emissor (ver
    app.py:api_fila/templates/index.html), exibido ACIMA da Fila de
    Espera para dar uma visão rápida do andamento de cada empresa sem
    precisar abrir o Painel Geral.

    Diferente da Fila de Espera (que só lista senhas com status
    'Emitida'), a última EMITIDA aparece qualquer que seja o status
    atual — inclusive senhas já chamadas/finalizadas, e as das duas
    opções fixas ("Criar Currículos"/"Imprimir Currículos", que nascem
    direto 'Finalizada') — o objetivo é mostrar "até onde a numeração
    de cada empresa já chegou", não o estado da fila.

    A última CHAMADA usa ``hora_chamada`` (preenchida tanto por uma
    chamada de guichê de verdade quanto pela criação de uma senha das
    opções fixas — ver ``criar_senha``/``chamar_proxima``, mesmo campo
    já usado por ``contar_chamadas_realizadas_periodo``), ordenando por
    ``hora_chamada DESC`` em vez de ``id DESC``: normalmente coincidem
    (a fila é FIFO), mas usar o horário real da chamada é mais preciso
    caso uma senha mais antiga acabe sendo chamada depois de uma mais
    nova já ter sido emitida.

    As subconsultas correlacionadas buscam o id da senha mais recente
    de cada empresa; os ``LEFT JOIN`` garantem que empresas sem nenhuma
    senha (emitida ou chamada) ainda apareçam na lista mesmo assim.

    ``somente_ativas=True`` (padrão) esconde empresas desativadas — não
    faz sentido mostrar "última senha" de uma empresa que não está mais
    recebendo candidatos.
    """
    condicao = "WHERE e.ativa = 1" if somente_ativas else ""

    with get_connection() as conexao:
        linhas = conexao.execute(
            f"""
            SELECT
                e.id AS empresa_id,
                e.nome AS empresa_nome,
                e.fixa AS empresa_fixa,
                s.numero AS senha_numero,
                s.nome_pessoa AS senha_nome_pessoa,
                s.data_hora AS senha_data_hora,
                s.status AS senha_status,
                c.numero AS chamada_numero,
                c.nome_pessoa AS chamada_nome_pessoa,
                c.hora_chamada AS chamada_hora
            FROM empresas e
            LEFT JOIN senhas s ON s.id = (
                SELECT id FROM senhas WHERE empresa_id = e.id ORDER BY id DESC LIMIT 1
            )
            LEFT JOIN senhas c ON c.id = (
                SELECT id FROM senhas
                WHERE empresa_id = e.id AND hora_chamada IS NOT NULL
                ORDER BY hora_chamada DESC LIMIT 1
            )
            {condicao}
            ORDER BY e.fixa DESC, e.nome ASC
            """
        ).fetchall()

    return [
        {
            "empresa_id": linha["empresa_id"],
            "empresa_nome": linha["empresa_nome"],
            "empresa_fixa": bool(linha["empresa_fixa"]),
            "numero": linha["senha_numero"],
            "nome_pessoa": linha["senha_nome_pessoa"],
            "data_hora": linha["senha_data_hora"],
            "status": linha["senha_status"],
            "chamada_numero": linha["chamada_numero"],
            "chamada_nome_pessoa": linha["chamada_nome_pessoa"],
            "chamada_hora": linha["chamada_hora"],
        }
        for linha in linhas
    ]


def obter_empresa_por_id(empresa_id: int) -> Optional[Empresa]:
    """Busca uma empresa pelo id. Usado para validar (existência e status
    ativo) a empresa escolhida no momento da emissão de uma senha."""
    with get_connection() as conexao:
        linha = conexao.execute(
            "SELECT * FROM empresas WHERE id = ?", (empresa_id,)
        ).fetchone()
    return Empresa.from_row(linha) if linha else None


def renomear_empresa(empresa_id: int, novo_nome: str) -> bool:
    """Altera o nome de uma empresa já cadastrada. Senhas já emitidas
    mantêm o nome antigo gravado (sem retroatividade), preservando o
    histórico exato de cada atendimento.

    Levanta ``ValueError`` se a empresa for uma das duas opções fixas do
    sistema (``fixa = 1`` — ver ``NOMES_EMPRESAS_FIXAS``): seu nome faz
    parte da identidade fixa da opção, então não pode ser alterado.
    """
    novo_nome_normalizado = (novo_nome or "").strip()
    if not novo_nome_normalizado:
        raise ValueError("Informe o novo nome da empresa.")

    with get_connection() as conexao:
        linha = conexao.execute("SELECT fixa FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        if linha is None:
            return False
        if linha["fixa"]:
            raise ValueError(
                "Esta é uma opção fixa do sistema (Criar Currículos / Imprimir "
                "Currículos) e não pode ser renomeada."
            )

        try:
            cursor = conexao.execute(
                "UPDATE empresas SET nome = ? WHERE id = ?",
                (novo_nome_normalizado, empresa_id),
            )
            conexao.commit()
        except sqlite3.IntegrityError as erro:
            if "UNIQUE" in str(erro).upper():
                raise ValueError(
                    f"Já existe uma empresa cadastrada com o nome '{novo_nome_normalizado}'."
                ) from erro
            raise ValueError(f"Não foi possível renomear a empresa: {erro}") from erro

        alterou = cursor.rowcount > 0

    if alterou:
        registrar_log("INFO", f"Empresa id={empresa_id} renomeada para '{novo_nome_normalizado}'.")
    return alterou


def definir_status_empresa(empresa_id: int, ativa: bool) -> bool:
    """Ativa ou desativa uma empresa (sem excluir o cadastro). Empresas
    inativas somem do seletor de emissão de senha, mas o histórico de
    senhas já emitidas para elas permanece intacto.

    Levanta ``ValueError`` se a empresa for uma das duas opções fixas do
    sistema (``fixa = 1`` — ver ``NOMES_EMPRESAS_FIXAS``): elas devem
    estar sempre disponíveis para o Emissor, então não podem ser
    desativadas (nem reativadas manualmente, já que nunca ficam
    inativas).
    """
    with get_connection() as conexao:
        linha = conexao.execute("SELECT fixa FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        if linha is None:
            return False
        if linha["fixa"]:
            raise ValueError(
                "Esta é uma opção fixa do sistema (Criar Currículos / Imprimir "
                "Currículos) e está sempre ativa — não pode ser desativada."
            )

        cursor = conexao.execute(
            "UPDATE empresas SET ativa = ? WHERE id = ?", (1 if ativa else 0, empresa_id)
        )
        conexao.commit()
        alterou = cursor.rowcount > 0

    if alterou:
        estado = "ativada" if ativa else "desativada"
        registrar_log("WARNING", f"Empresa id={empresa_id} {estado}.")
    return alterou


def bloquear_emissao_empresa(empresa_id: int) -> Optional[Dict]:
    """
    Bloqueia a EMISSÃO de novas senhas de UMA empresa: marca
    ``empresas.emissao_bloqueada_em`` com o horário atual.

    IMPORTANTE — o que esta função NÃO faz (diferente da antiga
    "Finalizar Atendimento do Dia"): não cancela as senhas que ainda
    estavam esperando (status 'Emitida'), e não impede CHAMAR novas
    senhas da fila. O recrutador continua atendendo normalmente quem já
    está na fila (ou quem entrar depois pela fila de outra empresa/canal,
    se aplicável) — o único efeito é impedir o Emissor de criar NOVAS
    senhas para esta empresa a partir de agora (ver
    ``app.py:api_emitir``). Pode ser desfeito a qualquer momento pelo
    próprio recrutador da empresa OU por um administrador (ver
    ``desbloquear_emissao_empresa``).

    Usa o mesmo lock por empresa de ``criar_senha``
    (``_lock_da_empresa``): evita que uma emissão consiga "passar" entre
    a checagem de que a empresa ainda aceita emissão e a gravação do
    bloqueio (condição de corrida).

    Retorna ``None`` se a empresa não existir. Caso exista, retorna um
    dicionário:
        - "ja_bloqueado": ``True`` se a emissão da empresa JÁ estava
          bloqueada (clique duplicado/repetido) — neste caso nada é
          alterado.
        - "bloqueado_em": o timestamp do bloqueio (o já existente, se
          ``ja_bloqueado``, ou o novo, gravado agora).
    """
    with _lock_da_empresa(empresa_id):
        with get_connection() as conexao:
            linha_empresa = conexao.execute(
                "SELECT emissao_bloqueada_em FROM empresas WHERE id = ?", (empresa_id,)
            ).fetchone()
            if linha_empresa is None:
                return None

            if linha_empresa["emissao_bloqueada_em"]:
                return {
                    "ja_bloqueado": True,
                    "bloqueado_em": linha_empresa["emissao_bloqueada_em"],
                }

            agora = _agora_iso()
            conexao.execute(
                "UPDATE empresas SET emissao_bloqueada_em = ? WHERE id = ?",
                (agora, empresa_id),
            )
            conexao.commit()

    registrar_log("WARNING", f"Emissão de senhas bloqueada para a empresa id={empresa_id}.")
    return {
        "ja_bloqueado": False,
        "bloqueado_em": agora,
    }


def desbloquear_emissao_empresa(empresa_id: int) -> bool:
    """
    Reativa a emissão de novas senhas de uma empresa cuja emissão estava
    bloqueada (ver ``bloquear_emissao_empresa``).

    Chamável tanto pelo PRÓPRIO recrutador da empresa (autoatendimento —
    ver ``app.py:api_reativar_emissao``) quanto por um administrador (ver
    ``app.py:api_admin_reativar_emissao_empresa``); a permissão em si é
    checada na camada de rotas, não aqui.

    Como o bloqueio não cancela mais nenhuma senha (ver
    ``bloquear_emissao_empresa``), reativar também não precisa restaurar
    nada — apenas limpa a marca de bloqueio.

    Retorna ``True`` se a empresa existia e estava com a emissão
    bloqueada (e foi reativada), ou ``False`` se não existe ou a emissão
    já estava liberada.
    """
    with get_connection() as conexao:
        cursor = conexao.execute(
            "UPDATE empresas SET emissao_bloqueada_em = NULL "
            "WHERE id = ? AND emissao_bloqueada_em IS NOT NULL",
            (empresa_id,),
        )
        conexao.commit()
        alterou = cursor.rowcount > 0

    if alterou:
        registrar_log("WARNING", f"Emissão de senhas reativada para a empresa id={empresa_id}.")
    return alterou


def definir_logo_empresa(empresa_id: int, logo_path: str, cor_principal: str) -> bool:
    """
    Grava o logo (``logo_path``, relativo à pasta ``static/`` — ver
    docstring de ``models.Empresa``) e a cor extraída automaticamente
    dele (``cor_principal``) para uma empresa. Chamada pela rota de
    upload (``app.py:api_admin_upload_logo_empresa``) logo após salvar o
    arquivo em disco e calcular a cor predominante.

    Grava os dois valores juntos (em vez de duas funções separadas)
    porque, na prática, todo novo logo enviado substitui a cor extraída
    anterior — para SÓ mudar a cor sem trocar o logo, use
    ``definir_cor_empresa``.
    """
    with get_connection() as conexao:
        cursor = conexao.execute(
            "UPDATE empresas SET logo_path = ?, cor_principal = ? WHERE id = ?",
            (logo_path, cor_principal, empresa_id),
        )
        conexao.commit()
        alterou = cursor.rowcount > 0

    if alterou:
        registrar_log(
            "INFO",
            f"Logo da empresa id={empresa_id} atualizado (cor extraída automaticamente: {cor_principal}).",
        )
    return alterou


def definir_cor_empresa(empresa_id: int, cor_principal: str) -> bool:
    """
    Sobrescreve manualmente a cor de identidade visual de uma empresa
    (``cor_principal``), sem alterar o logo já cadastrado. Usado quando o
    administrador não gosta da cor extraída automaticamente do logo e
    prefere escolher outra pelo seletor de cor da tela Empresas.
    """
    with get_connection() as conexao:
        cursor = conexao.execute(
            "UPDATE empresas SET cor_principal = ? WHERE id = ?",
            (cor_principal, empresa_id),
        )
        conexao.commit()
        alterou = cursor.rowcount > 0

    if alterou:
        registrar_log("INFO", f"Cor da empresa id={empresa_id} definida manualmente para {cor_principal}.")
    return alterou
