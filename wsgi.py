# -*- coding: utf-8 -*-
"""
wsgi.py
=======

Ponto de entrada para rodar o SIGS em PRODUÇÃO na rede local.

Usa o servidor WSGI "waitress" (já incluído em requirements.txt) em vez
do servidor de desenvolvimento do Flask — mais estável e adequado para
ficar no ar o dia inteiro atendendo vários dispositivos ao mesmo tempo
(tela principal, painel público em TV/monitor, etc.).

Uso:
    venv\\Scripts\\activate
    python wsgi.py

O servidor fica acessível em http://<IP-da-máquina>:5000 para qualquer
dispositivo na mesma rede local (ver README.md, seção 6, para liberar a
porta 5000 no Firewall do Windows caso necessário).

Para iniciar automaticamente com o Windows, crie uma tarefa agendada
(Agendador de Tarefas do Windows) que execute:
    <caminho-do-venv>\\Scripts\\python.exe <caminho-do-projeto>\\wsgi.py

NÃO use este arquivo para desenvolvimento/testes no dia a dia — use
dev.py, que reinicia sozinho a cada alteração salva e mostra erros
detalhados na tela.
"""

from waitress import serve

from app import app

if __name__ == "__main__":
    serve(app, host="0.0.0.0", port=5000, threads=8)
