# -*- coding: utf-8 -*-
"""
dev.py
======

Ponto de entrada para rodar o SIGS em modo DESENVOLVIMENTO, na sua
própria máquina, enquanto você mexe no código.

Usa o servidor embutido do Flask com:
    - debug=True   -> mostra o erro detalhado no navegador quando algo
                       quebra, em vez de uma página genérica.
    - reloader     -> reinicia o servidor sozinho sempre que você salva
                       um arquivo .py (não precisa parar e rodar de novo
                       na mão).

Uso:
    venv\\Scripts\\activate
    python dev.py

Depois acesse http://localhost:5000/login no navegador.

NÃO use este arquivo em produção (o modo debug do Flask expõe
informações internas do sistema e não foi feito para ficar exposto na
rede o dia inteiro). Para produção, use wsgi.py.
"""

from app import app

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
