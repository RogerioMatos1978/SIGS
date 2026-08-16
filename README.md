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
├── wsgi.py                  # Ponto de entrada de PRODUÇÃO (waitress, rede local)
├── dev.py                   # Ponto de entrada de DESENVOLVIMENTO (Flask debug/reload)
├── auth.py                  # Autenticação, sessão de login e atribuição de guichê
├── database.py             # Acesso ao SQLite (CRUD, fila FIFO, usuários, relatórios)
├── printer.py               # Impressão física do ticket (win32print/win32ui)
├── models.py                # Modelos de dados (Senha, ChamadaEvento, Usuario, Empresa)
├── config.py                # Configurações, caminhos, logger e chave de sessão
├── criar_admin.py           # Script de linha de comando para criar/resetar o administrador
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
│   │   ├── empresas.js       # Administração de empresas do feirão
│   │   └── bip.js            # Web Audio API (bip sonoro)
│   └── img/logo.png          # Logotipo (placeholder — substituir)
├── templates/
│   ├── layout.html
│   ├── login.html
│   ├── usuarios.html
│   ├── empresas.html
│   ├── index.html
│   ├── painel.html
│   ├── configuracoes.html
│   ├── relatorios.html
│   └── erro.html              # Página amigável de erro (404/403/500)
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

O banco de dados SQLite (`database/senhas.db`) é criado automaticamente
na primeira execução — não há nenhum serviço externo para instalar ou
configurar.

---

## 4. Configuração

### 4.1 Logotipo

Substitua o arquivo `static/img/logo.png` pelo logotipo oficial do SENAI
(mantendo o nome `logo.png`). O arquivo entregue é apenas um placeholder
de exemplo. Este logo é usado como imagem de CABEÇALHO nas telas internas
do sistema — ele NÃO é mais usado na impressão do ticket: cada empresa
tem seu próprio logo, impresso no ticket em seu lugar (ver seção 4.7).

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
- Quantidade de guichês de atendimento disponíveis, fila geral (ver seção 4.4).
- Quantidade de salas por empresa, usadas pelos recrutadores (ver seção 4.6).
- Cor principal do sistema (paleta visual).
- Frase do Menu: um texto livre (opcional) exibido em uma faixa logo
  abaixo do menu superior, em TODAS as telas internas do sistema (tela
  principal, Configurações, Relatórios, Usuários, Empresas). Útil para
  avisos do dia (ex.: "Documento com foto obrigatório"). Deixe em branco
  para não exibir nenhuma faixa.

Todas as configurações são persistidas na tabela `configuracoes` do
SQLite e aplicadas imediatamente, sem necessidade de reiniciar o
servidor.

### 4.4 Login, perfis de acesso e guichês

O SIGS exige login para acessar qualquer tela operacional ou
administrativa. A única exceção é o **Painel** (`/painel`), que continua
público, pois é voltado ao público que aguarda atendimento, não a
operadores do sistema.

**Não há autocadastro público.** Todo usuário é criado por um
administrador, pela tela **Usuários** (`/admin/usuarios`) — não existe
nenhuma tela pública de "criar conta".

**Primeiro acesso:** como ainda não existe nenhum administrador para
criar os demais usuários, o primeiro é criado pela linha de comando:

```bat
python criar_admin.py
```

O script pede login, nome completo e senha, e cria o usuário já como
administrador (ver seção 12.3 para detalhes). A partir daí, esse
administrador cria todos os demais usuários (atendentes, emissores ou
outros administradores) pela tela Usuários.

**Quatro perfis de acesso:**

- **Administrador** — acesso total ao sistema (Configurações,
  Relatórios, Gerenciar Usuários, reinício de contador, reset de senha de
  outros usuários, reset de todas as senhas emitidas). NÃO ocupa guichê e
  não opera a fila (não emite nem chama senhas) — seu papel é de gestão,
  não de atendimento.
- **Atendente** (perfil sugerido por padrão ao cadastrar um novo usuário)
  — ao logar, assume automaticamente um guichê da fila GERAL de
  atendimento (compartilhada entre todas as empresas). Responsável por
  Chamar Próxima, Repetir Chamada e Finalizar Atendimento.
- **Emissor de Senhas** — perfil restrito, criado apenas por um
  administrador na tela Usuários. Não ocupa guichê. Só enxerga o botão
  Emitir Senha — pensado para operar um totem de emissão na entrada do
  evento; as senhas que ele emite alimentam a fila consumida pelos
  atendentes e recrutadores.
- **Recrutador** — vinculado a UMA empresa específica pelo administrador
  (ver seção 4.6). Ao logar, assume automaticamente uma sala dentro da
  fila DAQUELA empresa (pool independente da fila geral do atendente) e
  só chama, repete chamada e finaliza (dá baixa) senhas emitidas para
  essa empresa.

| Recurso | Atendente | Emissor | Recrutador | Administrador |
|---|---|---|---|---|
| Emitir senha | ❌ | ✅ | ❌ | ❌ |
| Chamar / Repetir / Finalizar atendimento | ✅ (fila geral) | ❌ | ✅ (só da própria empresa) | ❌ |
| Abrir painel / Painel Geral / Testar bip | ✅ | ✅ | ✅ | ✅ |
| Ocupa guichê/sala automaticamente | ✅ | ❌ | ✅ | ❌ |
| Configurações do sistema | ❌ | ❌ | ❌ | ✅ |
| Relatórios (CSV/Excel/PDF) | ❌ | ❌ | ✅ (só da própria empresa) | ✅ (todas, com filtro) |
| Finalizar atendimento do dia (própria empresa) | ❌ | ❌ | ✅ | ❌ |
| Reabrir atendimento de uma empresa finalizada | ❌ | ❌ | ❌ | ✅ |
| Gerenciar usuários | ❌ | ❌ | ❌ | ✅ |
| Gerenciar empresas do feirão | ❌ | ❌ | ❌ | ✅ |
| Reiniciar contador de senhas | ❌ | ❌ | ❌ | ✅ |
| Resetar senha de outro usuário | ❌ | ❌ | ❌ | ✅ |
| Resetar (apagar) todas as senhas emitidas | ❌ | ❌ | ❌ | ✅ |

**Guichês:** ao fazer login, um usuário **atendente** assume
automaticamente o primeiro guichê disponível (entre 1 e a quantidade
configurada em "Quantidade de Guichês de Atendimento"), sem precisar
digitar ou selecionar nada. Administradores e emissores nunca ocupam
guichê. O guichê é liberado automaticamente no logout, ficando disponível
para o próximo login. Se todos os guichês estiverem ocupados, a tela
principal avisa o atendente e ele não conseguirá chamar senhas até que um
guichê seja liberado (ou até um administrador aumentar a quantidade de
guichês em Configurações). O perfil **recrutador** segue a mesma lógica,
mas em um pool de "salas" separado POR EMPRESA — ver seção 4.6.

**Finalizar Atendimento:** o botão "Finalizar Atendimento" (visível para
atendentes e recrutadores) marca a senha em atendimento no guichê/sala
como finalizada e, na mesma ação, já chama automaticamente a próxima
senha da fila — não é necessário clicar em "Chamar Próxima" separadamente
depois de atender um cliente. Se não houver mais senhas aguardando, o
sistema exibe um aviso "Aguardando nova senha ser emitida" (isso não é
tratado como erro, é uma situação normal de baixa demanda momentânea).

**Reset de senha (login) x Reiniciar contador x Resetar senhas emitidas**
— são três operações diferentes, todas restritas a administradores:

- *Resetar senha de usuário* (tela Usuários): redefine a senha de LOGIN
  de um usuário do sistema. Se ninguém tiver mais acesso administrativo
  (por exemplo, a senha do único admin foi esquecida), rode
  `python criar_admin.py` diretamente no servidor (ver seção 12.3).
- *Reiniciar Contador* (tela principal): zera a numeração das próximas
  senhas de TODAS as empresas de uma vez (a próxima emitida por cada
  empresa volta a ser 001), sem apagar o histórico. Cada empresa tem sua
  PRÓPRIA sequência independente de numeração (seção 4.5) — para
  reiniciar apenas UMA empresa, sem afetar as demais, use o botão
  "🔄 Reiniciar Contador" da linha daquela empresa em `/admin/empresas`.
- *Resetar Todas as Senhas Emitidas* (tela Usuários, "Zona de Perigo"):
  apaga PERMANENTEMENTE todo o histórico de senhas e chamadas, e também
  reinicia o contador de todas as empresas. Use apenas ao iniciar um
  evento totalmente novo.

### 4.5 Empresas do feirão do emprego

O SIGS permite cadastrar as empresas participantes de cada feirão do
emprego, exigindo a escolha de uma empresa toda vez que uma senha é
emitida — o nome da empresa também sai impresso no próprio ticket.

- **Cadastro** (`/admin/empresas`, restrito a administradores): cadastrar
  uma nova empresa, renomear uma já existente, e ativar/desativar. Apenas
  empresas **ativas** aparecem no seletor de emissão.
- **Emissão** (tela principal, perfil "Emissor"): ao clicar em "Emitir
  Senha", a janela que se abre agora exige a escolha da empresa (campo
  obrigatório) antes de confirmar a impressão — o servidor rejeita a
  emissão caso nenhuma empresa válida e ativa seja informada, mesmo que
  alguém tente contornar o formulário.
- **Impressão**: o nome da empresa selecionada é impresso logo abaixo do
  número da senha no próprio ticket (ver `printer.py`). O LOGO impresso no
  topo do ticket também é o da PRÓPRIA EMPRESA (não mais o logo padrão do
  sistema) — se a empresa ainda não tiver um logo cadastrado (seção 4.7),
  o ticket simplesmente sai sem nenhum logo.
- **Numeração por empresa**: cada empresa possui sua PRÓPRIA sequência
  independente de numeração de senhas (001, 002, 003...) — duas empresas
  diferentes podem emitir, ao mesmo tempo, uma senha de número 001, sem
  conflito entre si. Veja "Reiniciar Contador" acima para reiniciar a
  numeração de uma empresa específica.
- **Relatórios**: a tela de Relatórios ganhou um filtro por empresa
  (incluindo empresas já desativadas, para não perder o histórico de
  eventos passados) e uma tabela "Senhas por Empresa" com a contagem de
  senhas emitidas para cada uma no período selecionado.
- **Desativar não apaga histórico**: renomear ou desativar uma empresa
  nunca altera o nome já gravado em senhas emitidas anteriormente — o
  nome fica congelado no ticket/relatório no momento da emissão.

> Se nenhuma empresa estiver cadastrada (ou todas estiverem inativas), a
> janela de emissão exibe um aviso orientando a procurar um
> administrador — não é possível emitir senha sem selecionar uma empresa.

### 4.6 Recrutadores e Painéis por Empresa

Além da fila geral (atendentes) e do painel público único, o SIGS
oferece uma fila e um painel **independentes para cada empresa** —
pensado para feirões em que cada empresa entrevista em sua própria sala,
com seu próprio recrutador chamando as senhas.

**Vincular um recrutador a uma empresa** (`/admin/usuarios`, restrito a
administradores):

1. Cadastre a empresa primeiro em `/admin/empresas` (seção 4.5), caso
   ainda não exista.
2. Em "Novo Usuário", selecione o perfil **Recrutador** — um seletor de
   empresa aparece e é obrigatório.
3. Para um usuário já existente, mude o perfil dele para "Recrutador" na
   tabela "Usuários Cadastrados" e, em seguida, escolha a empresa no
   seletor "Empresa (Recrutador)" da mesma linha.
4. Mudar o perfil de um recrutador para qualquer outro (atendente,
   emissor, admin) limpa automaticamente o vínculo com a empresa.

**Painel de uma empresa** (`http://localhost:5000/painel/empresa/<id>`,
público, sem login): mostra apenas a chamada atual e as últimas senhas
emitidas DAQUELA empresa. O link direto para cada empresa está disponível
no botão "🖥️ Abrir Painel" da tela `/admin/empresas`; o recrutador logado
também tem um atalho "🖥️ Abrir Painel" na tela principal, que já abre
direto o painel da sua própria empresa.

**Painel Geral** (`http://localhost:5000/painel/geral`, público, sem
login): mostra o resumo agregado de todo o feirão — total de senhas
aguardando, em atendimento, atendidas e canceladas — e uma tabela com o
mesmo detalhamento por empresa. Acessível pelo botão "📺 Painel Geral" na
tela principal (visível para qualquer perfil logado).

**Salas (guichês por empresa):** ao logar, um recrutador assume
automaticamente a primeira sala disponível (entre 1 e a quantidade
configurada em "Quantidade de Salas por Empresa", seção 4.3) **dentro da
sua própria empresa** — a numeração de salas é independente entre
empresas (a "Sala 01" da Empresa A e a "Sala 01" da Empresa B não
conflitam). Um recrutador só pode chamar, repetir chamada, finalizar ou
cancelar senhas da SUA empresa; tentar gerenciar uma senha de outra
empresa retorna erro 403.

### 4.7 Identidade visual por empresa (logo + cor)

Cada empresa pode ter seu PRÓPRIO logo e cor de destaque, aplicados
automaticamente em duas telas: o painel público daquela empresa
(`/painel/empresa/<id>`) e a tela principal de um recrutador vinculado a
ela — as demais telas (do atendente, emissor e administrador) continuam
sempre com o logo/cor padrão do sistema (seção 4.1/4.3).

Além das telas, o logo da empresa também é usado no PRÓPRIO TICKET
impresso na emissão de senha (seção 4.5) — o logo padrão do sistema
deixou de ser usado na impressão.

**Como configurar** (`/admin/empresas`, restrito a administradores), na
coluna "Identidade Visual" de cada empresa:

1. Clique em "📷 Logo" e escolha uma imagem (PNG, JPG, GIF ou WEBP).
2. O sistema calcula automaticamente uma cor de destaque a partir da
   própria imagem (a cor média do logo) e já preenche o seletor de cor ao
   lado com o resultado — não é necessário fazer nada além do upload para
   "gerar" a identidade visual da empresa.
3. Se a cor sugerida não agradar, clique no seletor de cor e escolha
   outra manualmente — a alteração é salva imediatamente, sem afetar o
   logo já enviado.
4. Enviar um novo logo substitui tanto o arquivo quanto a cor extraída
   automaticamente (a cor escolhida manualmente no passo 3 seria
   sobrescrita nesse caso).

Logos com fundo transparente são tratados corretamente (compostos sobre
um fundo branco antes do cálculo da cor), e o arquivo antigo é removido
do disco automaticamente ao enviar um novo logo com extensão diferente,
evitando acúmulo de arquivos órfãos.

Na própria tela `/admin/empresas`, os botões "🖥️ Abrir Painel", "📷 Logo"
e "✏️ Renomear" de cada linha já usam a cor DAQUELA empresa (em vez da
cor padrão do sistema), facilitando identificar visualmente qual linha é
de qual empresa. O botão "🚫 Desativar"/"✅ Ativar" fica de fora
propositalmente, para manter o vermelho/verde de alerta sempre
reconhecível.

### 4.8 Encerramento do atendimento do dia (por empresa) e Relatórios do recrutador

Cada recrutador pode encerrar, por conta própria, o atendimento do dia da
SUA empresa — útil ao final do expediente/feirão, quando a empresa não
vai mais receber candidatos naquele dia.

**Como funciona** (botão "🔒 Finalizar Atendimento do Dia", tela
principal do recrutador):

1. Ao clicar, uma janela de confirmação explica claramente o efeito da
   ação antes de prosseguir (emissão e chamadas serão bloqueadas; senhas
   sem atendimento serão canceladas).
2. Confirmando, a empresa passa a rejeitar tanto a EMISSÃO de novas
   senhas (ela some do seletor de emissão, como se estivesse inativa)
   quanto a CHAMADA de senhas novas (`/api/chamar` retorna 409).
3. Toda senha daquela empresa que ainda estava com status "Emitida"
   (nunca chegou a ser chamada) é **cancelada automaticamente**, e passa
   a constar como cancelada tanto no relatório da própria empresa quanto
   no relatório do administrador.
4. Uma senha que já estava "em atendimento" (status "Chamada") no
   momento do encerramento **não é cancelada** — o recrutador ainda pode
   clicar em "Finalizar Atendimento" normalmente para dar baixa nela,
   mesmo depois de encerrar o dia.
5. Clicar de novo no botão (ou o servidor receber a requisição duas
   vezes) não tem efeito adicional — a ação é idempotente, apenas
   informa que o dia já estava finalizado.

**Reabertura:** só um **administrador** pode reverter um encerramento
feito por engano, pela tela `/admin/empresas` (botão "🔓 Reabrir" ao lado
do rótulo "🔒 Finalizado (data/hora)" na coluna "Atendimento do Dia"). O
próprio recrutador que finalizou não consegue reabrir sozinho. Reabrir
NÃO restaura as senhas que foram canceladas automaticamente — o
cancelamento permanece no histórico/relatório.

**Relatórios do recrutador:** com esta etapa, o recrutador ganhou acesso
à tela `/relatorios` (link "Relatórios" no menu superior) — mas sempre
restrito, no servidor, à SUA PRÓPRIA empresa: o seletor "Empresa" nem é
exibido para esse perfil, e tentar forçar outra empresa manualmente pela
URL (`?empresa_id=...`) é ignorado — o servidor sempre resolve a empresa
a partir da sessão de login, nunca do que o cliente envia.

---

## 5. Execução

### 5.1 Modo desenvolvimento

```bat
venv\Scripts\activate
python dev.py
```

`dev.py` usa o servidor embutido do Flask com `debug=True` e reinício
automático a cada alteração salva em um arquivo `.py` — ideal para testar
mudanças no código. Fica acessível apenas em `localhost` (não é exposto à
rede).

Antes do primeiro acesso, crie o administrador com `python criar_admin.py`
(ver seção 4.4). Com o servidor no ar, acesse:

- `http://localhost:5000/login` — Login (obrigatório para as telas abaixo).
- `http://localhost:5000/` — Tela principal (emissão/chamada de senhas).
- `http://localhost:5000/painel` — Painel público (abrir em uma TV/monitor, sem login).
- `http://localhost:5000/configuracoes` — Configurações do sistema (admin).
- `http://localhost:5000/relatorios` — Relatórios (CSV/Excel/PDF) (admin).
- `http://localhost:5000/admin/usuarios` — Gerenciar usuários (admin).
- `http://localhost:5000/admin/empresas` — Gerenciar empresas do feirão (admin).

### 5.2 Modo produção (recomendado)

Em produção, utilize `wsgi.py`, que serve a aplicação com o servidor WSGI
`waitress` (incluído no `requirements.txt`) em vez do servidor de
desenvolvimento do Flask — mais estável para ficar no ar o dia inteiro
atendendo vários dispositivos na rede local:

```bat
venv\Scripts\activate
python wsgi.py
```

Isso equivale a rodar `waitress-serve --host=0.0.0.0 --port=5000 app:app`,
mas sem precisar digitar o comando completo toda vez.

Para que o sistema inicie automaticamente com o Windows, crie uma tarefa
agendada (Agendador de Tarefas do Windows) que execute:

```bat
<caminho-do-venv>\Scripts\python.exe <caminho-do-projeto>\wsgi.py
```

na inicialização da máquina.

---

## 6. Rede e Firewall

Para que o painel público seja acessado de outro dispositivo na mesma
rede (por exemplo, um Smart TV ou outro computador exibindo o painel):

1. Descubra o IP local da máquina que roda o SIGS (`ipconfig` no cmd).
2. No dispositivo remoto, acesse `http://<IP-DA-MAQUINA>:5000/painel`
   (painel geral de chamadas), `http://<IP-DA-MAQUINA>:5000/painel/geral`
   (resumo do feirão) ou `http://<IP-DA-MAQUINA>:5000/painel/empresa/<id>`
   (painel de uma empresa específica — ver botão "Abrir Painel" em
   `/admin/empresas`).
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
`wsgi.py`, `dev.py`, `auth.py`, `database.py`, `printer.py`, `models.py`,
`config.py`, `criar_admin.py`, `templates/`, `static/`) por uma versão
mais nova, mantendo `database/` e `secret.key` intactos, para atualizar o
sistema sem perda de dados e sem deslogar os usuários.

> **Depois de atualizar os arquivos, dois passos são obrigatórios para as
> mudanças valerem:**
> 1. Pare o servidor (`Ctrl+C` no terminal onde `wsgi.py`/`dev.py` está
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

A tela de Relatórios (`/relatorios`) é acessível tanto a administradores
(veem todas as empresas, com filtro) quanto a recrutadores (veem apenas
a própria empresa, sem seletor — ver seção 4.8). Permite filtrar por
período (data início/fim) e por tipo (senhas emitidas ou chamadas
realizadas), exportando em três formatos:

- **CSV** — compatível com Excel, Google Sheets, etc.
- **Excel (.xlsx)** — planilha formatada, pronta para análise.
- **PDF** — relatório gerencial formatado para impressão/arquivamento.

No relatório de "Senhas Emitidas", cada linha traz as colunas: **Hora
Emissão**, **Hora Chamada**, **Tempo de Atendimento** (intervalo entre a
chamada e a finalização) e **Hora Finalizada**. Para uma senha
**cancelada** (seja manualmente, seja automaticamente pelo encerramento
do dia — ver seção 4.8), essas três últimas colunas ficam em branco,
independentemente de ela ter chegado a ser chamada antes do
cancelamento — uma senha cancelada é sempre tratada como "sem
atendimento" no relatório.

Também é exibido um resumo com o tempo médio de atendimento (intervalo
entre a emissão e a primeira chamada de cada senha) e uma tabela "Senhas
por Empresa", com a contagem de senhas emitidas para cada empresa dentro
do período selecionado (para o recrutador, mostra apenas a própria
empresa).

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
- Um recrutador só consegue finalizar/cancelar/chamar/repetir senhas da
  SUA PRÓPRIA empresa — tentar gerenciar (mesmo sabendo o id) uma senha
  de outra empresa também retorna HTTP 403.
- Todo o escopo por empresa (fila, chamadas e agora também relatórios) é
  resolvido sempre pelo `empresa_id` estável gravado na sessão de login
  do servidor, nunca por um valor enviado pelo cliente (formulário ou
  querystring) — um recrutador não consegue ver dados de outra empresa
  mesmo editando manualmente a URL do relatório.
- Somente um administrador pode reabrir o atendimento de uma empresa cujo
  dia foi finalizado por um recrutador (seção 4.8) — o próprio recrutador
  que finalizou não pode reverter sozinho.
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
  tabela `usuarios`), com perfis admin/atendente/emissor/recrutador,
  guichê/sala automática e reset de senha/contador/histórico pelo
  administrador.
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
| `GET /painel` | Público | Painel geral de chamadas (tela cheia, para TV/monitor) |
| `GET /painel/empresa/<id>` | Público | Painel de UMA empresa (fila e chamada daquela empresa) |
| `GET /painel/geral` | Público | Painel-resumo (emitidas/aguardando/atendidas/canceladas, por empresa) |
| `GET /health` | Público | Health check: confirma que o app e o banco de dados estão respondendo |
| `GET/POST /login` | Público | Autenticação |
| `POST /logout` | Login | Encerra sessão e libera o guichê |
| `GET /configuracoes` | Admin | Configurações do sistema |
| `GET /relatorios` | Admin | Geração de relatórios |
| `GET /admin/usuarios` | Admin | Gerenciamento de usuários e guichês |
| `GET /admin/empresas` | Admin | Gerenciamento de empresas do feirão |

### 12.2 Rotas de API (JSON)

| Rota | Acesso | Descrição |
|---|---|---|
| `POST /api/emitir` | Login | Emite uma nova senha (grava + imprime); exige `empresa_id` no corpo |
| `POST /api/chamar` | Login | Chama a próxima senha da fila (escopo automático por empresa p/ recrutador) |
| `POST /api/repetir` | Login | Repete a última chamada (idem) |
| `POST /api/finalizar-atendimento` | Login | Finaliza o atendimento e chama a próxima (idem) |
| `POST /api/reiniciar` | Admin | Reinicia o contador de senhas de TODAS as empresas |
| `GET /api/painel/status` | Público | Dados consumidos pelo painel geral (polling) |
| `GET /api/painel/empresa/<id>/status` | Público | Dados consumidos pelo painel de uma empresa |
| `GET /api/painel/geral/status` | Público | Dados consumidos pelo painel-resumo |
| `GET /api/fila` | Login | Lista da fila atual (escopo automático por empresa p/ recrutador) |
| `POST /api/senha/<id>/finalizar` | Login | Finaliza uma senha específica (recrutador só a da própria empresa) |
| `POST /api/senha/<id>/cancelar` | Login | Cancela uma senha específica (idem) |
| `GET/POST /api/config` | Admin | Lê/atualiza as configurações do sistema |
| `GET /api/impressoras` | Login | Lista as impressoras instaladas no Windows |
| `GET /api/empresas` | Login | Lista as empresas ATIVAS e com o dia ainda aberto (seletor de emissão) |
| `POST /api/finalizar-atendimento-dia` | Recrutador | Encerra o atendimento do dia da PRÓPRIA empresa (bloqueia emissão/chamada; cancela senhas sem atendimento) |
| `GET /api/relatorios/{csv,excel,pdf,resumo}` | Admin/Recrutador | Exporta/consulta relatórios (admin: filtro `empresa_id` opcional; recrutador: sempre restrito à própria empresa) |
| `POST /api/admin/usuarios` | Admin | Cria um usuário com perfil escolhido (exige `empresa_id` se "recrutador") |
| `POST /api/admin/usuarios/<id>/resetar-senha` | Admin | Reseta a senha de um usuário |
| `POST /api/admin/usuarios/<id>/perfil` | Admin | Altera o perfil de um usuário (limpa a empresa se sair de "recrutador") |
| `POST /api/admin/usuarios/<id>/empresa` | Admin | Vincula/desvincula a empresa de um recrutador |
| `POST /api/admin/usuarios/<id>/status` | Admin | Ativa/desativa um usuário |
| `POST /api/admin/reset-senhas-emitidas` | Admin | Apaga todo o histórico de senhas |
| `GET /api/admin/guiches` | Admin | Lista os guichês atualmente ocupados (fila geral) |
| `GET /api/admin/empresas` | Admin | Lista TODAS as empresas (ativas e inativas) |
| `POST /api/admin/empresas` | Admin | Cadastra uma nova empresa |
| `POST /api/admin/empresas/<id>/renomear` | Admin | Renomeia uma empresa |
| `POST /api/admin/empresas/<id>/status` | Admin | Ativa/desativa uma empresa |
| `POST /api/admin/empresas/<id>/reiniciar-contador` | Admin | Reinicia o contador de senhas de UMA empresa (não afeta as demais) |
| `POST /api/admin/empresas/<id>/logo` | Admin | Envia o logo da empresa (multipart) e extrai a cor automaticamente |
| `POST /api/admin/empresas/<id>/cor` | Admin | Sobrescreve manualmente a cor da empresa |
| `POST /api/admin/empresas/<id>/reabrir-atendimento` | Admin | Reabre o atendimento de uma empresa cujo dia foi finalizado (não restaura senhas já canceladas) |

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
- **Campo de login preservado após erro**: em `/login`, se a senha
  informada estiver errada, o login digitado permanece preenchido —
  apenas a senha é sempre limpa, por segurança. Antes, o usuário
  precisava redigitar tudo a cada tentativa.
- **Health check (`/health`)**: novo endpoint público que confirma, em
  uma única requisição, que o servidor Flask está no ar e que o banco de
  dados SQLite está acessível — útil em scripts de monitoramento ou para
  diagnosticar rapidamente um problema de arquivo/permissão em
  `database/senhas.db`.
- **Favicon silencioso**: requisições automáticas de `/favicon.ico`
  feitas pelo navegador não caem mais no tratamento de erro 404,
  evitando ruído nos logs.
- **`criar_admin.py`**: como não existe autocadastro público, este script
  de linha de comando é a única forma de criar o PRIMEIRO administrador
  do sistema (todos os demais usuários são cadastrados por um admin já
  logado, pela tela Usuários). Também serve para **resetar a senha do
  administrador** — por exemplo, se ela for esquecida e não houver mais
  ninguém com acesso à tela Usuários. O script pede login, nome completo
  e senha (a senha não aparece na tela) e não exige nenhuma configuração
  adicional (usa o mesmo `database/senhas.db` do sistema).

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
| Logotipo não aparece no ticket | A empresa selecionada na emissão não tem logo cadastrado | Faça o upload do logo da empresa em "Identidade Visual" na tela `/admin/empresas` (seção 4.7) — o ticket usa o logo DA EMPRESA, não mais o logo padrão do sistema |
| Esqueci a senha do administrador | — | Rode `python criar_admin.py` na pasta do projeto (ver seção 12.3) para redefinir a senha e garantir o perfil administrador |
| `/health` retorna erro 500 | Problema de arquivo/permissão em `database/senhas.db` | Confirme que a pasta `database/` existe e que o processo tem permissão de escrita nela |
| `database is locked` | Duas instâncias rodando ao mesmo tempo (`dev.py`/`wsgi.py`), ou antivírus bloqueando o arquivo | Feche instâncias duplicadas; adicione uma exceção ao antivírus para a pasta `database/` se persistir |

---

## 15. Licença e créditos

Sistema desenvolvido sob encomenda para uso interno do SENAI. Ajuste os
termos de uso conforme a política interna da instituição.
