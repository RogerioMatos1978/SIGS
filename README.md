# SIGS — Sistema Integrado de Gerenciamento de Senhas

Sistema profissional de gerenciamento de senhas para atendimento presencial,
desenvolvido para o SENAI em Python (Flask) + PostgreSQL + HTML5/CSS3/JavaScript,
com impressão direta de tickets via bibliotecas nativas do Windows
(pywin32), sem uso de PDF na impressão.

O banco de dados PostgreSQL roda em um container Docker; o aplicativo
Flask (`app.py`) continua rodando **nativamente no Windows** (fora do
Docker), pois a impressão depende de `pywin32`, que só funciona em Windows
nativo — não dentro de um container Linux. Ver seção 3 para o passo a
passo completo.

---

## 1. Visão geral da arquitetura

```
SIGS/
├── app.py                    # Rotas Flask (camada web/API)
├── auth.py                   # Autenticação, sessão de login e atribuição de guichê
├── database.py                # Acesso ao PostgreSQL (CRUD, fila FIFO, usuários, relatórios)
├── printer.py                 # Impressão física do ticket (win32print/win32ui)
├── models.py                  # Modelos de dados (Senha, ChamadaEvento, Usuario)
├── config.py                  # Configurações, conexão com PostgreSQL, logger e chave de sessão
├── criar_admin.py             # Script de linha de comando para criar/resetar o administrador
├── docker-compose.yml         # Sobe o PostgreSQL em um container Docker
├── .env.example                # Modelo de variáveis de ambiente (copiar para ".env")
├── requirements.txt
├── README.md
├── secret.key                  # Chave de sessão (gerada automaticamente, não versionar)
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
└── templates/
    ├── layout.html
    ├── login.html
    ├── cadastro.html
    ├── usuarios.html
    ├── index.html
    ├── painel.html
    ├── configuracoes.html
    ├── relatorios.html
    └── erro.html              # Página amigável de erro (404/403/500)
```

Cada camada tem responsabilidade única: `app.py` nunca acessa o
PostgreSQL diretamente (delega a `database.py`), a impressão está isolada
em `printer.py`, e as configurações do sistema (incluindo a conexão com o
banco) em `config.py`. Isso facilita manutenção e evolução futura.

---

## 2. Requisitos

- Windows 10/11 (necessário para a impressão física dos tickets e para
  rodar o app.py nativamente).
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  instalado e em execução (usado apenas para o banco PostgreSQL).
- Python 3.10 ou superior.
- Uma impressora térmica (ou comum) instalada e compartilhada no Windows.
- Navegador moderno: Chrome, Edge ou Firefox.

> A parte web (Flask) também roda em Linux/Mac para fins de
> desenvolvimento e testes, mas a impressão física só funciona no Windows,
> pois depende de `pywin32`.

---

## 3. Instalação

1. Instale o Python 3.10+ (marque "Add Python to PATH" durante a
   instalação, no Windows) e o Docker Desktop.

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

5. Configure e suba o banco de dados PostgreSQL (ver seção 3.1 abaixo)
   antes de rodar `python app.py` pela primeira vez.

### 3.1 Docker e PostgreSQL

O PostgreSQL roda em um container Docker gerenciado pelo
`docker-compose.yml` incluído no projeto. O app.py continua rodando
normalmente no Windows (fora do container) e se conecta ao banco pela
porta publicada no host.

1. Copie `.env.example` para `.env` (mesma pasta) e, se desejar, altere a
   senha padrão:

   ```bat
   copy .env.example .env
   ```

   > Em produção, **troque `POSTGRES_PASSWORD`** para uma senha forte
   > antes de expor a porta em qualquer rede que não seja totalmente
   > confiável (mesmo em rede local).

2. Suba o banco de dados:

   ```bat
   docker compose up -d
   ```

   Isso baixa a imagem `postgres:16-alpine` (na primeira vez) e inicia o
   container `sigs_postgres`, publicando a porta 5432 no host. Os dados
   ficam persistidos em um volume Docker nomeado (`sigs_postgres_data`),
   sobrevivendo a reinicializações do container.

3. Confirme que o banco está saudável:

   ```bat
   docker compose ps
   ```

   A coluna "STATUS" deve mostrar `healthy` (pode levar alguns segundos
   após o `up -d`). Também é possível acompanhar os logs com
   `docker compose logs -f db`.

4. Crie o primeiro administrador via linha de comando (alternativa ao
   autocadastro pela tela `/cadastro` — ver seção 4.4):

   ```bat
   python criar_admin.py
   ```

   O script pede login, nome completo e senha (a senha não aparece na
   tela) e cria o usuário já como administrador. Se o login informado já
   existir, a senha desse usuário é redefinida e ele é promovido a
   administrador. Use este mesmo comando sempre que precisar **resetar a
   senha do administrador** (por exemplo, se ela for esquecida e não
   houver mais ninguém com acesso à tela Usuários).

5. Só então inicie o app:

   ```bat
   python app.py
   ```

   Se o PostgreSQL ainda não estiver pronto para aceitar conexões (por
   exemplo, `docker compose up -d` acabou de ser executado), o app tenta
   se reconectar automaticamente por até ~20 segundos antes de desistir
   (ver `config.conectar_com_retentativas`).

**Comandos úteis do dia a dia:**

```bat
docker compose up -d       :: sobe o banco em segundo plano
docker compose down        :: para o banco (mantém os dados)
docker compose down -v     :: para o banco E APAGA os dados (cuidado!)
docker compose logs -f db  :: acompanha os logs do PostgreSQL
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
   branco para usar a impressora padrão do Windows. Esta é a impressora
   usada por padrão em toda emissão.
3. Além disso, todo usuário **emissor** vê uma janela de escolha de
   impressora toda vez que clica em "Emitir Senha" (útil quando há mais
   de uma impressora na máquina, por exemplo, uma térmica e uma comum).
   Escolher uma impressora ali vale apenas para aquele ticket; deixar em
   "Impressora padrão do sistema" usa a impressora configurada no item 2.

### 4.3 Demais parâmetros

Na tela de Configurações também é possível ajustar:

- Nome do evento (impresso no ticket e exibido no painel).
- Quantidade de senhas exibidas no painel (histórico).
- Tempo de atualização do painel (em milissegundos).
- Quantidade de guichês de atendimento disponíveis (ver seção 4.4).
- Cor principal do sistema (paleta visual).

Todas as configurações são persistidas na tabela `configuracoes` do
PostgreSQL e aplicadas imediatamente, sem necessidade de reiniciar o
servidor.

### 4.4 Login, perfis de acesso e guichês

O SIGS exige login para acessar qualquer tela operacional ou
administrativa. A única exceção é o **Painel** (`/painel`), que continua
público, pois é voltado ao público que aguarda atendimento, não a
operadores do sistema.

**Primeiro acesso:** acesse `http://localhost:5000/cadastro` e crie o
primeiro usuário. Ele se torna **administrador** automaticamente. Todos
os cadastros seguintes feitos por qualquer pessoa em `/cadastro` recebem
o perfil **atendente** (acesso restrito, é o perfil "padrão" do sistema)
— apenas um administrador pode promover outro usuário a administrador ou
a emissor, pela tela **Usuários** (`/admin/usuarios`).

**Três perfis de acesso:**

- **Administrador** — acesso total ao sistema (Configurações,
  Relatórios, Gerenciar Usuários, reinício de contador, reset de senha de
  outros usuários, reset de todas as senhas emitidas). NÃO ocupa guichê e
  não opera a fila (não emite nem chama senhas) — seu papel é de gestão,
  não de atendimento.
- **Atendente** (perfil padrão do autocadastro) — ao logar, assume
  automaticamente um guichê de atendimento disponível. Responsável por
  Chamar Próxima, Repetir Chamada e Finalizar Atendimento.
- **Emissor de Senhas** — perfil restrito, criado apenas por um
  administrador na tela Usuários. Não ocupa guichê. Só enxerga o botão
  Emitir Senha — pensado para operar um totem de emissão na entrada do
  evento; as senhas que ele emite alimentam a fila consumida pelos
  atendentes.

| Recurso | Atendente | Emissor | Administrador |
|---|---|---|---|
| Emitir senha | ❌ | ✅ | ❌ |
| Chamar / Repetir / Finalizar atendimento | ✅ | ❌ | ❌ |
| Abrir painel / Testar bip | ✅ | ✅ | ✅ |
| Ocupa guichê automaticamente | ✅ | ❌ | ❌ |
| Configurações do sistema | ❌ | ❌ | ✅ |
| Relatórios (CSV/Excel/PDF) | ❌ | ❌ | ✅ |
| Gerenciar usuários | ❌ | ❌ | ✅ |
| Reiniciar contador de senhas | ❌ | ❌ | ✅ |
| Resetar senha de outro usuário | ❌ | ❌ | ✅ |
| Resetar (apagar) todas as senhas emitidas | ❌ | ❌ | ✅ |

**Guichês:** ao fazer login, um usuário **atendente** assume
automaticamente o primeiro guichê disponível (entre 1 e a quantidade
configurada em "Quantidade de Guichês de Atendimento"), sem precisar
digitar ou selecionar nada. Administradores e emissores nunca ocupam
guichê. O guichê é liberado automaticamente no logout, ficando disponível
para o próximo login. Se todos os guichês estiverem ocupados, a tela
principal avisa o atendente e ele não conseguirá chamar senhas até que um
guichê seja liberado (ou até um administrador aumentar a quantidade de
guichês em Configurações).

**Finalizar Atendimento:** o botão "Finalizar Atendimento" (visível
apenas para atendentes) marca a senha em atendimento no guichê como
finalizada e, na mesma ação, já chama automaticamente a próxima senha da
fila — não é necessário clicar em "Chamar Próxima" separadamente depois
de atender um cliente. Se não houver mais senhas aguardando, o sistema
exibe um aviso "Aguardando nova senha ser emitida" (isso não é tratado
como erro, é uma situação normal de baixa demanda momentânea).

**Reset de senha (login) x Reiniciar contador x Resetar senhas emitidas**
— são três operações diferentes, todas restritas a administradores:

- *Resetar senha de usuário* (tela Usuários): redefine a senha de LOGIN
  de um usuário do sistema. Se ninguém tiver mais acesso administrativo
  (por exemplo, a senha do único admin foi esquecida), use
  `python criar_admin.py` diretamente no servidor — ver seção 3.1.
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

Com o PostgreSQL rodando em Docker, o backup tem duas partes: os dados do
banco e a chave de sessão.

**Banco de dados (PostgreSQL):** gere um dump lógico com `pg_dump`,
executado dentro do próprio container:

```bat
docker compose exec db pg_dump -U sigs -d sigs -F c -f /tmp/sigs_backup.dump
docker cp sigs_postgres:/tmp/sigs_backup.dump .\backups\sigs_backup_2026-08-13.dump
```

Para restaurar esse dump em um banco novo:

```bat
docker compose exec -T db pg_restore -U sigs -d sigs --clean --if-exists < sigs_backup.dump
```

Alternativamente, para um backup mais simples (porém menos portável),
basta copiar a pasta do volume Docker (`sigs_postgres_data`) enquanto o
container estiver parado (`docker compose down`, copiar, depois
`docker compose up -d` novamente).

**Chave de sessão:**

```
SIGS/secret.key    # chave de sessão (evite perdê-la ou trocá-la)
```

Recomenda-se automatizar o `pg_dump` diariamente (por exemplo, via
Agendador de Tarefas do Windows executando um `.bat` com o comando acima),
preservando o histórico de senhas emitidas, chamadas e os usuários
cadastrados.

> Atenção: se o arquivo `secret.key` for apagado, o sistema gera uma nova
> chave automaticamente na próxima execução, mas isso invalida todas as
> sessões de login ativas (todos os usuários precisarão logar novamente).
> Isso não afeta os dados do PostgreSQL.

O sistema também mantém um arquivo de log (`SIGS/sigs.log`) com o
histórico de eventos técnicos (emissões, chamadas, logins, alterações de
usuários, erros de impressão), útil para auditoria e diagnóstico.

---

## 8. Atualização do sistema

Como o banco de dados agora vive no volume Docker (`sigs_postgres_data`,
fora da pasta do projeto) e a chave de sessão fica isolada em
`secret.key`, basta substituir os demais arquivos do projeto (`app.py`,
`auth.py`, `database.py`, `printer.py`, `models.py`, `config.py`,
`criar_admin.py`, `templates/`, `static/`) por uma versão mais nova,
mantendo `secret.key` e o volume `sigs_postgres_data` intactos, para
atualizar o sistema sem perda de dados e sem deslogar os usuários. Não é
necessário rodar `docker compose down -v` (isso apagaria o banco) — um
simples `docker compose up -d` reaproveita o container e os dados
existentes.

> **Depois de atualizar os arquivos, dois passos são obrigatórios para as
> mudanças valerem:**
> 1. Pare o servidor (`Ctrl+C` no terminal onde `python app.py` está
>    rodando) e inicie de novo. Alterações em arquivos `.py` só têm
>    efeito depois que o processo Python é reiniciado.
> 2. Dê um "hard refresh" no navegador (`Ctrl+F5` ou `Ctrl+Shift+R`) em
>    cada tela aberta do SIGS. Alterações em arquivos `static/*.js` e
>    `*.css` podem ficar em cache no navegador mesmo após o servidor ser
>    atualizado.
>
> Esquecer esses dois passos é a causa mais comum de "a mudança não
> funcionou" — o sistema continua executando a versão antiga em memória
> (servidor) ou em cache (navegador) até que ambos sejam renovados.

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

- Todas as consultas SQL utilizam parâmetros (`%s`, via psycopg2),
  prevenindo SQL Injection.
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
- O arquivo `.env` (com `POSTGRES_PASSWORD`) nunca deve ser versionado em
  repositórios públicos; use `.env.example` apenas como modelo. Em redes
  compartilhadas, evite publicar a porta 5432 do PostgreSQL além do
  necessário — o padrão do `docker-compose.yml` já a expõe apenas no
  host local, o que é suficiente para o app.py se conectar.

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

## 12. Mapa de rotas e usabilidade

### 12.1 Rotas de páginas (HTML)

| Rota | Acesso | Descrição |
|---|---|---|
| `GET /` | Login | Tela principal (emissão/chamada de senhas) |
| `GET /painel` | Público | Painel de chamadas (tela cheia, para TV/monitor) |
| `GET /health` | Público | Health check: confirma que o app e o PostgreSQL estão respondendo |
| `GET/POST /login` | Público | Autenticação |
| `POST /logout` | Login | Encerra sessão e libera o guichê |
| `GET/POST /cadastro` | Público | Autocadastro (1º usuário = admin; demais = atendente) |
| `GET /configuracoes` | Admin | Configurações do sistema |
| `GET /relatorios` | Admin | Geração de relatórios |
| `GET /admin/usuarios` | Admin | Gerenciamento de usuários e guichês |

### 12.2 Rotas de API (JSON)

| Rota | Acesso | Descrição |
|---|---|---|
| `POST /api/emitir` | Login | Emite uma nova senha (grava + imprime) |
| `POST /api/chamar` | Login | Chama a próxima senha da fila |
| `POST /api/repetir` | Login | Repete a última chamada |
| `POST /api/finalizar-atendimento` | Login | Finaliza o atendimento e chama a próxima |
| `POST /api/reiniciar` | Admin | Reinicia o contador de senhas |
| `GET /api/painel/status` | Público | Dados consumidos pelo painel (polling) |
| `GET /api/fila` | Login | Lista da fila atual |
| `POST /api/senha/<id>/finalizar` | Login | Finaliza uma senha específica |
| `POST /api/senha/<id>/cancelar` | Login | Cancela uma senha específica |
| `GET/POST /api/config` | Admin | Lê/atualiza as configurações do sistema |
| `GET /api/impressoras` | Login | Lista as impressoras instaladas no Windows |
| `GET /api/relatorios/{csv,excel,pdf,resumo}` | Admin | Exporta/consulta relatórios |
| `POST /api/admin/usuarios` | Admin | Cria um usuário com perfil escolhido |
| `POST /api/admin/usuarios/<id>/resetar-senha` | Admin | Reseta a senha de um usuário |
| `POST /api/admin/usuarios/<id>/perfil` | Admin | Altera o perfil de um usuário |
| `POST /api/admin/usuarios/<id>/status` | Admin | Ativa/desativa um usuário |
| `POST /api/admin/reset-senhas-emitidas` | Admin | Apaga todo o histórico de senhas |
| `GET /api/admin/guiches` | Admin | Lista os guichês atualmente ocupados |

### 12.3 Melhorias de usabilidade desta versão

Ao revisar todas as rotas acima, foram identificados e corrigidos os
seguintes pontos de atrito:

- **Páginas de erro amigáveis**: antes, qualquer erro 404/500 em uma
  página HTML (não API) retornava um JSON cru no navegador. Agora,
  `templates/erro.html` exibe uma página consistente com o visual do
  sistema, com um link de volta para o início. Chamadas de API
  (`/api/...`) continuam recebendo JSON puro, sem alteração de
  comportamento para o JavaScript do front-end.
- **Mensagens ao ser redirecionado por falta de permissão**: antes, um
  atendente que tentava acessar `/configuracoes` diretamente pela URL era
  redirecionado silenciosamente para a tela principal, sem explicação. Da
  mesma forma, uma sessão expirada redirecionava para `/login` sem aviso.
  Agora essas situações exibem uma mensagem clara ("Esta área é restrita
  a administradores.", "Sua sessão expirou...") através de um sistema de
  mensagens (`flash`) exibido no topo da página.
- **Campos de formulário preservados após erro**: em `/login` e
  `/cadastro`, se o envio falhar (senha errada, login duplicado, senhas
  não conferem), o login (e o nome completo, no cadastro) digitados
  permanecem preenchidos — apenas os campos de senha são sempre limpos,
  por segurança. Antes, o usuário precisava redigitar tudo a cada
  tentativa.
- **Health check (`/health`)**: novo endpoint público que confirma, em
  uma única requisição, que o servidor Flask está no ar e que a conexão
  com o PostgreSQL está funcionando — útil logo após `docker compose up
  -d`, ou em scripts de monitoramento.
- **Favicon silencioso**: requisições automáticas de `/favicon.ico`
  feitas pelo navegador não caem mais no tratamento de erro 404,
  evitando ruído nos logs.

---

## 13. Referências e projetos utilizados como case de sucesso

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

## 14. Solução de problemas comuns

| Problema | Causa provável | Solução |
|---|---|---|
| Erro "pywin32 não está instalado" ao emitir senha | Rodando fora do Windows, ou pywin32 não instalado | Instale `pywin32` (`pip install pywin32`) e rode no Windows |
| Ticket não centralizado corretamente | Impressora com driver antigo | Atualize o driver da impressora; o sistema já calcula a largura dinamicamente via `GetDeviceCaps()` |
| Painel não atualiza | Bloqueio de firewall/rede | Verifique a seção 6 (Rede e Firewall) |
| Bip não toca no painel | Navegador bloqueando áudio automático | Interaja uma vez com a página (clique) antes de abrir o painel, ou configure o navegador para permitir autoplay de áudio no domínio |
| Logotipo não aparece no ticket | Caminho do logotipo incorreto | Verifique o campo "Caminho do Logotipo" em Configurações |
| `python app.py` trava/falha ao iniciar com erro de conexão | PostgreSQL não está rodando, ou `.env` incorreto | Rode `docker compose ps` para confirmar que o container está `healthy`; confira `POSTGRES_HOST/PORT/DB/USER/PASSWORD` no `.env` |
| `docker compose up -d` falha | Docker Desktop não está em execução, ou porta 5432 já em uso | Abra o Docker Desktop; se a porta já estiver ocupada por outro PostgreSQL local, altere `POSTGRES_PORT` no `.env` |
| Esqueci a senha do administrador | — | Rode `python criar_admin.py` no servidor (ver seção 3.1) para redefinir a senha e garantir o perfil administrador |
| `/health` retorna erro 503 | PostgreSQL inacessível a partir do app.py | Confirme `docker compose ps`; se o container acabou de subir, aguarde alguns segundos e tente novamente |

---

## 15. Licença e créditos

Sistema desenvolvido sob encomenda para uso interno do SENAI. Ajuste os
termos de uso conforme a política interna da instituição.
