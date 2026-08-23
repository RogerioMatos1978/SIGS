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
    # POR EMPRESA e resumo), cada tela operacional logada (atendente,
    # recrutador, emissor) e a tela principal fazem polling HTTP a cada
    # poucos segundos (padrão 2s, configurável em Configurações) — cada
    # requisição em andamento ocupa uma thread do waitress até a
    # resposta ser enviada. Um valor de threads menor que o número de
    # clientes fazendo polling ao mesmo tempo faz requisições ESPERAREM
    # uma thread livre, sentido pelo usuário como uma pequena travada —
    # mais perceptível quanto mais dispositivos conectados ao mesmo
    # tempo, como num feirão grande.
    #
    # Regra prática (revisada na auditoria de performance do sistema
    # para o cenário de até 24 empresas cadastradas + 6 pontos de
    # emissão de senha + 1 painel de TV, todos na mesma rede Wi-Fi):
    #   threads >= (2 x nº de recrutadores/empresas logadas)
    #            + nº de pontos de emissão
    #            + 1 painel de TV
    #            + folga para admin/relatórios
    # Para 24 empresas: 2x24 = 48, +6 pontos de emissão, +1 painel de TV,
    # +~9 de folga = 64. Se o feirão crescer bem além disso, aumente
    # este número (cada thread ociosa custa pouca memória) ou rode atrás
    # de um proxy reverso, se necessário.
    serve(app, host="0.0.0.0", port=5000, threads=64)
