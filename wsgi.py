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
    # Dimensionamento de "threads": cada painel público aberto (geral,
    # POR EMPRESA e resumo) e cada tela operacional logada faz polling
    # HTTP a cada poucos segundos (padrão 2s, configurável em
    # Configurações). Como cada empresa cadastrada agora tem seu próprio
    # painel (ver seção 4.6 do README), o número de conexões concorrentes
    # cresce com a quantidade de empresas do feirão — um valor fixo baixo
    # de threads, dimensionado para quando só existia UM painel, vira
    # gargalo (requisições esperando thread livre) num feirão com muitas
    # empresas. Regra prática: threads >= (2 x nº de empresas cadastradas)
    # + alguma folga para as telas de atendente/admin; 24 cobre
    # confortavelmente até ~10 empresas com folga para uso normal do
    # totem. Se o feirão tiver muito mais empresas que isso, aumente este
    # número (ou rode com waitress atrás de um proxy, se necessário).
    serve(app, host="0.0.0.0", port=5000, threads=24)
