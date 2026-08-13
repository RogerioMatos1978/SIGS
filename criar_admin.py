# -*- coding: utf-8 -*-
"""
criar_admin.py
===============

Script de linha de comando para criar um usuário administrador do SIGS ou
redefinir a senha de um administrador já existente, sem precisar passar
pela tela de cadastro do navegador.

Quando usar este script:

    - Você perdeu a senha do único administrador do sistema e ninguém mais
      tem acesso à tela "Gerenciar Usuários" para resetá-la.
    - Você quer criar o primeiro administrador direto no servidor (por
      exemplo, em uma instalação nova, sem depender do "bootstrap" da tela
      de cadastro, que só torna admin o PRIMEIRO usuário cadastrado).
    - Você quer automatizar a criação do administrador em um script de
      implantação (deploy).

Pré-requisitos:
    - O PostgreSQL precisa estar rodando e acessível (ex.: já executou
      "docker compose up -d" na raiz do projeto).
    - As variáveis de ambiente / arquivo ".env" com os dados de conexão
      devem estar configurados (ver ".env.example").

Modo interativo (recomendado):
    python criar_admin.py

    O script pede o login, nome completo e senha (a senha não aparece na
    tela enquanto é digitada). Se o login já existir, a senha desse
    usuário é redefinida e o perfil é promovido para administrador. Se o
    login não existir, um novo usuário administrador é criado.

Modo não interativo (para scripts de automação):
    python criar_admin.py --login admin --nome "Administrador do Sistema" --senha "SenhaForte123"

    Use com cuidado: a senha fica visível no histórico do terminal e em
    logs de processo. Prefira o modo interativo sempre que possível.

Ver também: seção "Administração via linha de comando" no README.md.
"""

import argparse
import getpass
import sys

import auth
import database
from models import PerfilUsuario


def _ler_login(login_arg: str) -> str:
    if login_arg:
        return login_arg.strip().lower()
    login = input("Login do administrador: ").strip().lower()
    while not login:
        login = input("O login não pode ficar vazio. Login do administrador: ").strip().lower()
    return login


def _ler_nome(nome_arg: str, login: str) -> str:
    if nome_arg:
        return nome_arg.strip()
    nome = input(f"Nome completo [padrão: {login}]: ").strip()
    return nome or login


def _ler_senha(senha_arg: str) -> str:
    if senha_arg:
        erro = auth.validar_forca_senha(senha_arg)
        if erro:
            print(f"Erro: {erro}")
            sys.exit(1)
        return senha_arg

    while True:
        senha = getpass.getpass("Nova senha (mínimo 6 caracteres, não aparece na tela): ")
        erro = auth.validar_forca_senha(senha)
        if erro:
            print(f"Erro: {erro}")
            continue

        confirmacao = getpass.getpass("Confirme a nova senha: ")
        if senha != confirmacao:
            print("As senhas não coincidem. Tente novamente.\n")
            continue

        return senha


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Cria um usuário administrador do SIGS ou redefine a senha de um "
            "administrador já existente, promovendo-o se necessário."
        )
    )
    parser.add_argument("--login", help="Login do administrador (criado ou atualizado).")
    parser.add_argument("--nome", help="Nome completo (usado apenas ao criar um novo usuário).")
    parser.add_argument(
        "--senha",
        help=(
            "Senha em texto puro (não recomendado; se omitido, o script pede a "
            "senha de forma oculta, com confirmação)."
        ),
    )
    argumentos = parser.parse_args()

    print("=" * 60)
    print("SIGS - Criação/Reset de usuário Administrador")
    print("=" * 60)

    try:
        database.inicializar_banco()
    except Exception as erro:  # noqa: BLE001 - queremos capturar qualquer falha de conexão
        print(
            "\nNão foi possível conectar ao PostgreSQL.\n"
            "Verifique se o container está rodando ('docker compose up -d') e se "
            "o arquivo '.env' está configurado corretamente (ver '.env.example').\n"
            f"\nDetalhe técnico: {erro}"
        )
        sys.exit(1)

    login = _ler_login(argumentos.login)
    usuario_existente = database.obter_usuario_por_login(login)

    if usuario_existente is not None:
        print(f"\nUsuário '{login}' já existe (perfil atual: {usuario_existente.perfil}).")
        senha = _ler_senha(argumentos.senha)

        database.resetar_senha_usuario(usuario_existente.id, auth.gerar_hash_senha(senha))

        if usuario_existente.perfil != PerfilUsuario.ADMIN:
            database.definir_perfil_usuario(usuario_existente.id, PerfilUsuario.ADMIN)
            print(f"Usuário '{login}' promovido a administrador.")

        if not usuario_existente.ativo:
            database.definir_status_usuario(usuario_existente.id, True)
            print(f"Usuário '{login}' reativado.")

        print(f"\nSenha do usuário '{login}' redefinida com sucesso. Perfil: administrador.")
    else:
        nome = _ler_nome(argumentos.nome, login)
        senha = _ler_senha(argumentos.senha)

        database.criar_usuario(
            nome_completo=nome,
            login=login,
            senha_hash=auth.gerar_hash_senha(senha),
            perfil=PerfilUsuario.ADMIN,
        )
        print(f"\nUsuário administrador '{login}' criado com sucesso.")

    print("\nJá pode fazer login no SIGS com este usuário.")


if __name__ == "__main__":
    main()
