# SIGS — Sistema Integrado de Gerenciamento de Senhas

Sistema profissional de gerenciamento de senhas para atendimento presencial,
desenvolvido para o SENAI em Python (Flask) + SQLite + HTML5/CSS3/JavaScript,
com impressão direta de tickets via bibliotecas nativas do Windows
(pywin32), sem uso de PDF na impressão.

---

## 1. Visão geral da arquitetura

```
SIGS/
├── app.py                 # Rotas Flask (camada web/API)
├── auth.py                  # Autenticação, sessão de login e atribuição de guichê
├── database.py             # Acesso ao SQLite (CRUD, fila FIFO, usuários, relatórios)
├── printer.py               # Impressão física do ticket (win32print/win32ui)
├── models.py                # Modelos de dados (Senha, ChamadaEvento, Usuario)
├── config.py                # Configurações, caminhos, logger e chave de sessão
├── requirements.txt
├── README.md
├── secret.key                # Chave de sessão (gerada automaticamente, não versionar)
├── static/
│   ├── css/style.css
│   ├── js/
│   │   ├── index.js          # Tela principal
│   │   ├── painel.js         # Painel público
│   │   ├── configuracoes.js
│   │   ├── relatorios.js
│   │   ├── usuarios.js       # Administração de usuários
│   │   └── bip.js            # Web Audio API (bip sonoro)
│   └── img/logo.png          # Logotipo (placeholder — substituir)
├── templates/
│   ├── layout.html
│   ├── login.html
│   ├── cadastro.html
│   ├── usuarios.html
│   ├── index.html
│   ├── painel.html
│   ├── configuracoes.html
│   └── relatorios.html
└── database/
    └── senhas.db              # Criado automaticamente na 1ª execução
```

Cada camada tem responsabilidade única: `app.py` nunca acessa o SQLite
diretamente (delega a `database.py`), a impressão está isolada em
`printer.py`, e as configurações do sistema em `config.py`. Isso facilita
manutenção e evolução futura.

---

## 2. Requisitos

- Windows 10/11 (necessário para a impressão física dos tickets).
- Python 3.10 ou superior.
- Uma impressora térmica (ou comum) instalada e compartilhada no Windows.
- Navegador moderno: Chrome, Edge ou Firefox.

> A parte web (Flask) também roda em Linux/Mac para fins de
> desenvolvimento e testes, mas a impressão física só funciona no Windows,
> pois depende de `pywin32`.

---

## 3. Instalação

1. Instale o Python 3.10+ e certifique-se de marcar "Add Python to PATH"
   durante a instalação (Windows).

2. Copie a pasta `SIGS` para o computador que ficará no totem/balcão de
   atendimento.

3. Abra o Prompt de Comando (cmd) dentro da pasta `SIGS` e crie um
   ambiente virtual (recomendado):

   ```bat
   python -m venv venv
   venv\Scripts\activate
   ```

4. Instale as dependências:

   ```bat
   pip install -r requirements.txt
   ```

---

## 4. Configuração

### 4.1 Logotipo

Substitua o arquivo `static/img/logo.png` pelo logotipo oficial do SENAI
(mantendo o nome `logo.png`, ou atualizando o caminho na tela de
Configurações). O arquivo entregue é apenas um placeholder de exemplo.

### 4.2 Impressora

1. Instale a impressora normalmente no Windows (Painel de Controle >
   Dispositivos e Impressoras) e imprima uma página de teste para
   confirmar que está funcionando.
2. Acesse a tela **Configurações** do SIGS pelo navegador
   (`http://localhost:5000/configuracoes`) e selecione a impressora na
   lista (ela é detectada automaticamente via `win32print`). Deixe em
   branco para usar a impressora padrão do Windows.

### 4.3 Demais parâmetros

Na tela de Configurações também é possível ajustar:

- Nome do evento (impresso no ticket e exibido no painel).
- Quantidade de senhas exibidas no painel (histórico).
- Tempo de atualização do painel (em milissegundos).
- Quantidade de guichês de atendimento disponíveis (ver seção 4.4).
- Cor principal do sistema (paleta visual).

Todas as configurações são persistidas na tabela `configuracoes` do
SQLite e aplicadas imediatamente, sem necessidade de reiniciar o
servidor.

### 4.4 Login, perfis de acesso e guichês

O SIGS exige login para acessar qualquer tela operacional ou
administrativa. A única exceção é o **Painel** (`/painel`), que continua
público, pois é voltado ao público que aguarda atendimento, não a
operadores do sistema.

**Primeiro acesso:** acesse `http://localhost:5000/cadastro` e crie o
primeiro usuário. Ele se torna **administrador** automaticamente. Todos
os cadastros seguintes (feitos por qualquer pessoa em `/cadastro`)
recebem o perfil **atendente** (acesso restrito) por padrão — apenas um
administrador pode promover outro usuário a administrador, pela tela
**Usuários** (`/admin/usuarios`).

**Perfis de acesso:**

| Recurso | Atendente | Administrador |
|---|---|---|
| Emitir / Chamar / Repetir senha | ✅ | ✅ |
| Abrir painel / Testar bip | ✅ | ✅ |
| Configurações do sistema | ❌ | ✅ |
| Relatórios (CSV/Excel/PDF) | ❌ | ✅ |
| Gerenciar usuários | ❌ | ✅ |
| Reiniciar contador de senhas | ❌ | ✅ |
| Resetar senha de outro usuário | ❌ | ✅ |
| Resetar (apagar) todas as senhas emitidas | ❌ | ✅ |

**Guichês:** ao fazer login, o usuário assume automaticamente o primeiro
guichê disponível (entre 1 e a quantidade configurada em "Quantidade de
Guichês de Atendimento"), sem precisar digitar ou selecionar nada. O
guichê é liberado automaticamente no logout, ficando disponível para o
próximo login. Se todos os guichês estiverem ocupados, a tela principal
avisa o usuário e ele não conseguirá chamar senhas até que um guichê seja
liberado (ou até um administrador aumentar a quantidade de guichês).

**Reset de senha (login) x Reiniciar contador x Resetar senhas emitidas**
— são três operações diferentes, todas restritas a administradores:

- *Resetar senha de usuário* (tela Usuários): redefine a senha de LOGIN
  de um usuário do sistema.
- *Reiniciar Contador* (tela principal): zera a numeração das próximas
  senhas de atendimento (a próxima emitida volta a ser 001), sem apagar
  o histórico.
- *Resetar Todas as Senhas Emitidas* (tela Usuários, "Zona de Perigo"):
  apaga PERMANENTEMENTE todo o histórico de senhas e chamadas, e também
  reinicia o contador. Use apenas ao iniciar um evento totalmente novo.

---

## 5. Execução

### 5.1 Modo desenvolvimento

```bat
venv\Scripts\activate
python app.py
```

O servidor sobe em `http://localhost:5000`. Acesse:

- `http://localhost:5000/cadastro` — Criar o primeiro usuário (administrador).
- `http://localhost:5000/login` — Login (obrigatório para as telas abaixo).
- `http://localhost:5000/` — Tela principal (emissão/chamada de senhas).
- `http://localhost:5000/painel` — Painel público (abrir em uma TV/monitor, sem login).
- `http://localhost:5000/configuracoes` — Configurações do sistema (admin).
- `http://localhost:5000/relatorios` — Relatórios (CSV/Excel/PDF) (admin).
- `http://localhost:5000/admin/usuarios` — Gerenciar usuários (admin).

### 5.2 Modo produção (recomendado)

Em produção, utilize um servidor WSGI dedicado em vez do servidor de
desenvolvimento do Flask. O pacote `waitress` (incluído no
`requirements.txt`) é uma boa opção para Windows:

```bat
waitress-serve --host=0.0.0.0 --port=5000 app:app
```

Para que o sistema inicie automaticamente com o Windows, crie uma tarefa
agendada (Agendador de Tarefas do Windows) que execute o comando acima na
inicialização da máquina.

---

## 6. Rede e Firewall

Para que o painel público seja acessado de outro dispositivo na mesma
rede (por exemplo, um Smart TV ou outro computador exibindo o painel):

1. Descubra o IP local da máquina que roda o SIGS (`ipconfig` no cmd).
2. No dispositivo remoto, acesse `http://<IP-DA-MAQUINA>:5000/painel`.
3. Se a conexão falhar, libere a porta 5000 no Firewall do Windows:
   - Painel de Controle > Sistema e Segurança > Firewall do Windows
     Defender > Configurações Avançadas > Regras de Entrada > Nova Regra.
   - Tipo: Porta > TCP > Porta específica: `5000` > Permitir a conexão.

---

## 7. Backup

Os arquivos que precisam ser copiados para backup são:

```
SIGS/database/senhas.db   # senhas, chamadas, usuários e configurações
SIGS/secret.key             # chave de sessão (evite perdê-la ou trocá-la)
```

Recomenda-se automatizar uma cópia diária desses arquivos (por exemplo,
via Agendador de Tarefas do Windows executando um `copy` para um pendrive
ou pasta de rede), preservando o histórico de senhas emitidas, chamadas e
os usuários cadastrados.

> Atenção: se o arquivo `secret.key` for apagado, o sistema gera uma nova
> chave automaticamente na próxima execução, mas isso invalida todas as
> sessões de login ativas (todos os usuários precisarão logar novamente).
> Isso não afeta os dados em `senhas.db`.

O sistema também mantém um arquivo de log (`SIGS/sigs.log`) com o
histórico de eventos técnicos (emissões, chamadas, logins, alterações de
usuários, erros de impressão), útil para auditoria e diagnóstico.

---

## 8. Atualização do sistema

Como o banco de dados (`senhas.db`) e a chave de sessão (`secret.key`)
ficam isolados (o primeiro na pasta `database/`, o segundo na raiz do
projeto), basta substituir os demais arquivos do projeto (`app.py`,
`auth.py`, `database.py`, `printer.py`, `models.py`, `config.py`,
`templates/`, `static/`) por uma versão mais nova, mantendo `database/` e
`secret.key` intactos, para atualizar o sistema sem perda de dados e sem
deslogar os usuários.

---

## 9. Relatórios

A tela de Relatórios permite filtrar por período (data início/fim) e por
tipo (senhas emitidas ou chamadas realizadas), exportando em três
formatos:

- **CSV** — compatível com Excel, Google Sheets, etc.
- **Excel (.xlsx)** — planilha formatada, pronta para análise.
- **PDF** — relatório gerencial formatado para impressão/arquivamento.

Também é exibido um resumo com o tempo médio de atendimento (intervalo
entre a emissão e a primeira chamada de cada senha).

> Importante: o uso de PDF nos relatórios gerenciais é independente da
> impressão do ticket de senha, que nunca utiliza PDF — o ticket é
> sempre impresso diretamente via GDI do Windows (`printer.py`).

---

## 10. Segurança

- Todas as consultas SQL utilizam parâmetros (`?`), prevenindo SQL
  Injection.
- Entradas de formulário (Configurações, emissão de senha) são validadas
  antes de gravação, e apenas chaves de configuração conhecidas são
  aceitas.
- Exceções são tratadas em todas as rotas da API, retornando mensagens de
  erro padronizadas em JSON, sem expor detalhes internos sensíveis.
- Todos os eventos relevantes (emissão, chamada, repetição, reinício de
  contador, login/logout, criação/alteração de usuários, erros de
  impressão) são registrados em log (arquivo e tabela `logs` do banco de
  dados).
- Login obrigatório em todas as telas operacionais/administrativas
  (`auth.py`), com sessão assinada por uma chave secreta persistida em
  `secret.key` (gerada automaticamente, nunca deve ser versionada em
  repositórios públicos).
- Senhas de usuários NUNCA são armazenadas em texto puro — apenas o hash
  gerado por `werkzeug.security.generate_password_hash` (PBKDF2).
- Rotas administrativas (`/configuracoes`, `/relatorios`,
  `/admin/usuarios` e respectivas APIs) exigem explicitamente o perfil
  "admin"; usuários com perfil "atendente" recebem HTTP 403 caso tentem
  acessá-las diretamente pela URL.
- Um usuário desativado por um administrador tem a sessão invalidada
  automaticamente na requisição seguinte, mesmo que o cookie de sessão
  ainda esteja presente no navegador.
- A operação de reset total das senhas emitidas exige confirmação
  explícita (`{"confirmar": true}`) para reduzir o risco de acionamento
  acidental.

> Para uso em produção fora de uma rede interna confiável, recomenda-se
> também habilitar HTTPS (por exemplo, via proxy reverso IIS/nginx) e
> marcar os cookies de sessão como `Secure`, o que não é feito por
> padrão no servidor de desenvolvimento do Flask.

---

## 11. Arquitetura preparada para expansões futuras

O sistema foi desenhado para crescer sem necessidade de reescrita:

- **Múltiplos guichês**: os campos `guiche` e `usuario` já existem na
  tabela `senhas` e em `eventos_chamada`; basta abrir múltiplas instâncias
  da tela principal, uma por guichê.
- **TV Samsung / Smart TV**: o painel (`/painel`) é uma página web
  comum, compatível com qualquer navegador embarcado de Smart TV.
- **Voz chamando senha**: pode ser adicionado em `painel.js`, usando a
  Web Speech API (`SpeechSynthesis`) no mesmo ponto onde o bip é
  disparado (`dispararAnimacaoEChamada`).
- **QR Code**: pode ser gerado no momento da emissão (`app.py`,
  `/api/emitir`) com uma biblioteca como `qrcode`, sem alterar o restante
  da arquitetura.
- **API REST**: as rotas já seguem convenções REST (`/api/...`) e podem
  ser consumidas diretamente por aplicativos móveis (Android/iOS) ou
  dashboards externos.
- **Login / Controle de usuários**: já implementado (`auth.py` +
  tabela `usuarios`), com perfis admin/atendente, guichê automático e
  reset de senha/contador/histórico pelo administrador.
- **LDAP / Active Directory**: a autenticação local (`auth.py`) pode ser
  estendida para validar contra um servidor LDAP/AD antes (ou em vez) de
  checar a tabela `usuarios`, mantendo o restante do fluxo de sessão e
  guichês inalterado.
- **Múltiplas unidades**: a estrutura de configuração em banco (tabela
  `configuracoes`) já permite, futuramente, um campo `unidade_id` para
  segregar dados por unidade do SENAI.

---

## 12. Referências e projetos utilizados como case de sucesso

Antes de desenhar a camada de autenticação/administração, foram
consultados projetos open source de sistemas de senha/fila e boas
práticas de mercado, para validar o padrão adotado aqui (login
obrigatório, perfil de administrador separado do operador, atribuição de
guichê, painel público responsivo):

- [FQM](https://github.com/mrf345/FQM) e o
  [queue-management-system](https://github.com/vladstudennikov/queue-management-system)
  (Flask + Flask-Login/Flask-Admin) confirmam o padrão de "área de
  superusuário" separada da operação comum, replicado aqui como o perfil
  "admin" e a tela `/admin/usuarios`.
- [gestaosenhas](https://github.com/pahique/gestaosenhas) e
  [phpsgf](https://github.com/igormenin/phpsgf) reforçam a prática de
  proteger com usuário/senha as ações administrativas (configuração,
  reinício de contador), o que motivou restringir Configurações,
  Relatórios e os resets ao perfil administrador no SIGS.
- [chamadas-de-senha](https://github.com/rafaxavier/chamadas-de-senha) e
  [SASE](https://github.com/gabrielduete/SASE) confirmam a separação
  entre "terminal de atendimento" (operacional, com login) e "painel/telão"
  (público, sem login), padrão mantido no SIGS (`/painel` continua aberto).
- Guias de mercado de sistemas de fila em 2025 (Qminder, Wavetec, Skiplino)
  destacam layout responsivo, botões grandes tocáveis e atualização em
  tempo real como requisitos centrais para uso em tablets/celulares — já
  cobertos pelo CSS responsivo do SIGS (`static/css/style.css`), que foi
  revisado para incluir a nova barra de usuário/login também em telas
  pequenas.

---

## 13. Solução de problemas comuns

| Problema | Causa provável | Solução |
|---|---|---|
| Erro "pywin32 não está instalado" ao emitir senha | Rodando fora do Windows, ou pywin32 não instalado | Instale `pywin32` (`pip install pywin32`) e rode no Windows |
| Ticket não centralizado corretamente | Impressora com driver antigo | Atualize o driver da impressora; o sistema já calcula a largura dinamicamente via `GetDeviceCaps()` |
| Painel não atualiza | Bloqueio de firewall/rede | Verifique a seção 6 (Rede e Firewall) |
| Bip não toca no painel | Navegador bloqueando áudio automático | Interaja uma vez com a página (clique) antes de abrir o painel, ou configure o navegador para permitir autoplay de áudio no domínio |
| Logotipo não aparece no ticket | Caminho do logotipo incorreto | Verifique o campo "Caminho do Logotipo" em Configurações |

---

## 14. Licença e créditos

Sistema desenvolvido sob encomenda para uso interno do SENAI. Ajuste os
termos de uso conforme a política interna da instituição.
