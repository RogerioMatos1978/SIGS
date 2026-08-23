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
├── resetar_sistema.py        # Script de linha de comando para zerar o sistema (mantém só os admins)
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
- Quantidade de mesas por empresa, usadas pelos recrutadores (ver seção 4.6).
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
  atendentes e recrutadores. O seletor de empresa sempre inclui, além das
  empresas cadastradas pelo administrador, as duas opções fixas do
  sistema "Criar Currículos" e "Imprimir Currículos" (ver seção 4.9) —
  serviços de apoio ao candidato, sem fila nem chamada.
- **Recrutador** — vinculado a UMA empresa específica pelo administrador
  (ver seção 4.6). Ao logar, assume automaticamente uma mesa dentro da
  fila DAQUELA empresa (pool independente da fila geral do atendente) e
  só chama, repete chamada e finaliza (dá baixa) senhas emitidas para
  essa empresa.

| Recurso | Atendente | Emissor | Recrutador | Administrador |
|---|---|---|---|---|
| Emitir senha | ❌ | ✅ | ❌ | ❌ |
| Chamar / Repetir / Finalizar atendimento | ✅ (fila geral) | ❌ | ✅ (só da própria empresa) | ❌ |
| Abrir painel / Painel Geral / Testar bip | ✅ | ✅ | ✅ | ✅ |
| Ocupa guichê/mesa automaticamente | ✅ | ❌ | ✅ | ❌ |
| Configurações do sistema | ❌ | ❌ | ❌ | ✅ |
| Relatórios (CSV/Excel/PDF) | ❌ | ❌ | ✅ (só da própria empresa) | ✅ (todas, com filtro) |
| Bloquear emissão de senhas (própria empresa) | ❌ | ❌ | ✅ | ❌ |
| Reativar emissão de senhas de uma empresa bloqueada | ❌ | ❌ | ✅ (só da própria empresa) | ✅ (qualquer empresa) |
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
mas em um pool de "mesas" separado POR EMPRESA — ver seção 4.6.

**Finalizar Atendimento:** o botão "Finalizar Atendimento" (visível para
atendentes e recrutadores) marca a senha em atendimento no guichê/mesa
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
pensado para feirões em que cada empresa entrevista em sua própria sala
(uma única sala física, compartilhada por todos os recrutadores daquela
empresa), com cada recrutador atendendo em sua própria mesa dentro dessa
sala e chamando as senhas de sua vez.

**Acesso do recrutador: chave de 8 dígitos, sem login/senha individual.**
A partir da v2.4.0, o recrutador NÃO é mais cadastrado manualmente em
"Gerenciar Usuários" (essa opção foi removida do formulário e também é
recusada pelo servidor, caso alguém tente forçar via API). Em vez disso:

1. Cadastre a empresa em `/admin/empresas` (seção 4.5) — ao criá-la, o
   sistema gera automaticamente uma **chave numérica de 8 dígitos**,
   exibida na coluna "Chave de Acesso (Recrutador)" da própria tela.
2. Compartilhe com a empresa o link `http://localhost:5000/empresas/entrar`
   (uma página pública, sem login, com um card para cada empresa ativa) e
   a chave de 8 dígitos — o botão "🔑 WhatsApp (Acesso)" em cada linha da
   tela Empresas já monta essa mensagem pronta para enviar.
3. Qualquer pessoa da empresa toca no card correspondente, informa o
   PRÓPRIO NOME e a chave, e entra — o sistema cria automaticamente uma
   sessão de recrutador vinculada àquela empresa (sem senha própria, sem
   cadastro prévio pelo administrador) e atribui a próxima mesa livre,
   exatamente como já acontecia antes.
4. Ao deslogar, essa conta "temporária" é apagada automaticamente — nada
   se acumula em "Gerenciar Usuários". O histórico de senhas/relatórios
   não é afetado (o nome de quem atendeu fica gravado normalmente).
5. Se a chave vazar ou precisar ser trocada, use "🔄 Nova Chave" na tela
   Empresas: a chave antiga para de funcionar na hora (sessões já abertas
   continuam válidas até deslogarem).

> **Segurança da chave:** tentativas incorretas de chave para uma mesma
> empresa são bloqueadas temporariamente após 5 erros seguidos (mesma
> proteção contra força bruta já usada no login tradicional — ver seção
> 10). A chave nunca aparece em nenhuma resposta pública (painel, página
> de seleção de empresas) — só é visível para um administrador logado, na
> tela Empresas.

**Painel de uma empresa** (`http://localhost:5000/painel/empresa/<id>`,
público, sem login): mostra apenas a chamada atual e as últimas senhas
emitidas DAQUELA empresa. O link direto para cada empresa está disponível
no botão "🖥️ Abrir Painel" da tela `/admin/empresas`; o recrutador logado
também tem um atalho "🖥️ Abrir Painel" na tela principal, que já abre
direto o painel da sua própria empresa.

**Painel Geral** (`http://localhost:5000/painel/geral`, público, sem
login): mostra o resumo agregado de todo o feirão — total de senhas
aguardando e em atendimento — e uma tabela com o mesmo detalhamento por
empresa. Acessível pelo botão "📺 Painel Geral" na tela principal
(visível para qualquer perfil logado).

> **Nenhum painel público mostra senhas "Finalizada" nem "Cancelada"**
> (nem na lista de "Últimas Senhas Emitidas" do painel geral de chamadas
> e do painel por empresa, nem nos totais/tabela do Painel Geral) — um
> painel de tela cheia (TV/monitor) deve refletir a situação ATUAL da
> fila, não o histórico de atendimentos já encerrados. Esses números
> continuam disponíveis normalmente na tela `/relatorios` (ver seção
> 4.9.1), que é onde o histórico completo tem utilidade.

**Mesas (guichês por empresa):** ao logar, um recrutador assume
automaticamente a primeira mesa disponível (entre 1 e a quantidade
configurada em "Quantidade de Mesas por Empresa", seção 4.3) **dentro da
sua própria empresa** — a numeração de mesas é independente entre
empresas (a "Mesa 01" da Empresa A e a "Mesa 01" da Empresa B não
conflitam). Vários recrutadores da MESMA empresa atendem simultaneamente
na mesma sala, cada um em sua própria mesa numerada. Um recrutador só
pode chamar, repetir chamada, finalizar ou cancelar senhas da SUA
empresa; tentar gerenciar uma senha de outra empresa retorna erro 403.

> **"Repetir Chamada" é sempre por MESA, não por empresa.** Com vários
> recrutadores atendendo na mesma empresa, "Repetir Chamada" reanuncia a
> última senha chamada NAQUELA mesa especificamente — nunca a última
> chamada de outra mesa da mesma empresa. Assim, o recrutador da Mesa 02
> nunca repete por engano uma senha que foi chamada pela Mesa 01. O mesmo
> vale para o atendente (fila geral): repete sempre a última chamada do
> PRÓPRIO guichê.

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

### 4.8 Bloqueio de Emissão de Senhas (por empresa) e Relatórios do recrutador

Cada recrutador pode bloquear, por conta própria, a emissão de novas
senhas da SUA empresa — útil ao final do expediente/feirão, ou sempre
que a empresa quiser pausar temporariamente a entrada de novos
candidatos na fila, sem interromper o atendimento de quem já está
esperando.

**Como funciona** (botão "🚫 Bloquear Emissão de Senhas", tela principal
do recrutador):

1. Ao clicar, uma janela de confirmação explica claramente o efeito da
   ação antes de prosseguir.
2. Confirmando, a empresa passa a rejeitar apenas a EMISSÃO de novas
   senhas (ela some do seletor de emissão do Emissor, e `/api/emitir`
   retorna 409 para essa empresa).
3. A fila já existente NÃO é afetada: senhas com status "Emitida" (ainda
   esperando) continuam podendo ser chamadas normalmente, e senhas
   "Chamadas" continuam podendo ser finalizadas ou ter a chamada
   repetida — nada é cancelado automaticamente.
4. Clicar de novo no botão (ou o servidor receber a requisição duas
   vezes) não tem efeito adicional — a ação é idempotente, apenas
   informa que a emissão já estava bloqueada.

**Reativação:** tanto o **próprio recrutador** da empresa (botão "🔓
Reativar Emissão de Senhas" na tela principal) quanto um
**administrador** (botão "🔓 Reativar" na tela `/admin/empresas`, coluna
"Emissão de Senhas") podem liberar a emissão novamente a qualquer
momento. Como nenhuma senha é cancelada ao bloquear, não há nada para
"restaurar" — a fila segue intacta durante todo o período bloqueado.

**Relatórios do recrutador:** com esta etapa, o recrutador ganhou acesso
à tela `/relatorios` (link "Relatórios" no menu superior) — mas sempre
restrito, no servidor, à SUA PRÓPRIA empresa: o seletor "Empresa" nem é
exibido para esse perfil, e tentar forçar outra empresa manualmente pela
URL (`?empresa_id=...`) é ignorado — o servidor sempre resolve a empresa
a partir da sessão de login, nunca do que o cliente envia.

### 4.9 Opções fixas de emissão: Criar Currículos / Imprimir Currículos

Além das empresas cadastradas por um administrador, o seletor de empresa
exibido ao Emissor sempre traz, fixas no topo da lista, duas opções que
não representam empresas reais participantes do feirão: **"Criar
Currículos"** e **"Imprimir Currículos"** — dois serviços de apoio ao
candidato (ajuda para montar e para imprimir o currículo antes de entrar
na fila das empresas).

**Como funcionam** (por baixo dos panos, ambas são empresas com
`fixa = 1` no banco de dados — ver `database.NOMES_EMPRESAS_FIXAS`):

- São criadas automaticamente na primeira vez que o sistema inicia (e
  recriadas se, por algum motivo, tiverem sido removidas do banco), sem
  qualquer ação manual do administrador.
- Não podem ser renomeadas nem desativadas (a tela `/admin/empresas`
  esconde os botões "Renomear" e "Desativar"/"Ativar" para essas duas
  linhas, marcadas com o selo "🔒 Fixa"; tentar forçar pela API retorna
  erro 409 com uma mensagem explicando o motivo).
- **Não têm fila nem chamada**: diferente de uma senha emitida para uma
  empresa comum (que nasce "Emitida" e espera ser chamada), uma senha
  emitida para "Criar Currículos"/"Imprimir Currículos" já nasce
  **"Finalizada"** — o ticket ainda é impresso normalmente (como
  comprovante), mas a senha nunca aparece na Fila de Espera, nunca é
  chamada de fato, e não gera nenhum evento em `eventos_chamada` (não
  existe guichê anunciando nada — ver `database.criar_senha`, parâmetro
  `finalizar_imediatamente`). Mesmo assim, ela CONTA normalmente tanto
  como "senha emitida" quanto como "chamada realizada" nos relatórios e
  no Painel Geral (e aparece na tabela "Senhas por Empresa") — ver seção
  4.9.1 sobre como esse invariante inclui as duas opções fixas.
- Não têm recrutador: não aparecem na página pública de login por chave
  (`/empresas/entrar`), e acessar a URL de login de uma delas
  diretamente é bloqueado.

#### 4.9.1 Invariante: "chamadas realizadas" nunca é maior que "senhas emitidas" (e inclui as opções fixas)

O "Resumo do Período" da tela de Relatórios usa
`database.contar_chamadas_realizadas_periodo`, que conta **senhas com
`hora_chamada` preenchida** dentro do período — não o total bruto de
eventos em `eventos_chamada`, e não exige que a senha tenha
necessariamente passado pela fila. Essa coluna é gravada em exatamente
dois momentos, e nunca reescrita depois:

- Na PRIMEIRA vez que uma senha é chamada (`chamar_proxima`). Cada
  clique em "Repetir Chamada" grava um NOVO evento em `eventos_chamada`
  para a MESMA senha, mas NÃO altera `hora_chamada` — uma senha repetida
  5 vezes continua contando como UMA chamada, não cinco. Contar os
  eventos brutos (comportamento antigo) podia fazer "Chamadas
  Realizadas" ultrapassar "Senhas Emitidas" no resumo — algo que nunca
  deveria acontecer.
- Na criação de uma senha para uma das duas opções fixas ("Criar
  Currículos"/"Imprimir Currículos" — ver seção 4.9), que nasce direto
  como "Finalizada" com `hora_chamada` já preenchida. Mesmo sem fila,
  sem chamada de guichê e sem nenhum evento em `eventos_chamada`, ela É
  um atendimento realizado — por isso conta normalmente na soma de
  "Chamadas Realizadas", tanto no Resumo do Período quanto (por
  consequência) em qualquer outro lugar do sistema que use essa mesma
  função.

O relatório de exportação "Chamadas Realizadas" (CSV/Excel/PDF)
continua listando apenas eventos reais de `eventos_chamada` (inclusive
repetições) — ali o objetivo é um log auditável de anúncios feitos em
guichês/mesas, então as opções fixas (que nunca são anunciadas em
guichê nenhum) propositalmente não aparecem nesse log específico, só na
contagem-resumo.

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
  guichê/mesa automática e reset de senha/contador/histórico pelo
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
| `GET/POST /login` | Público | Autenticação (admin/atendente/emissor — recrutador NÃO usa mais esta tela) |
| `GET /empresas/entrar` | Público | Cards das empresas ativas — ponto de entrada do recrutador |
| `GET/POST /empresas/<id>/entrar` | Público | Acesso da empresa: nome + chave de 8 dígitos (login do recrutador) |
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
| `POST /api/repetir` | Login | Repete a última chamada do PRÓPRIO guichê/mesa do usuário logado |
| `POST /api/finalizar-atendimento` | Login | Finaliza o atendimento e chama a próxima (idem) |
| `POST /api/reiniciar` | Admin | Reinicia o contador de senhas de TODAS as empresas |
| `GET /api/painel/status` | Público | Dados consumidos pelo painel geral (polling) |
| `GET /api/painel/empresa/<id>/status` | Público | Dados consumidos pelo painel de uma empresa |
| `GET /api/painel/geral/status` | Público | Dados consumidos pelo painel-resumo |
| `GET /api/fila` | Login | Lista (paginada) da fila atual, com busca opcional por número/nome — `?busca=texto&pagina=N` (escopo automático por empresa p/ recrutador) |
| `POST /api/senha/<id>/finalizar` | Login | Finaliza uma senha específica (recrutador só a da própria empresa) |
| `POST /api/senha/<id>/cancelar` | Login | Cancela uma senha específica (idem) |
| `GET/POST /api/config` | Admin | Lê/atualiza as configurações do sistema |
| `GET /api/impressoras` | Login | Lista as impressoras instaladas no Windows |
| `GET /api/empresas` | Login | Lista as empresas ATIVAS e com a emissão de senhas ainda liberada (seletor de emissão) |
| `POST /api/bloquear-emissao` | Recrutador | Bloqueia a emissão de novas senhas da PRÓPRIA empresa (não afeta chamar/repetir/finalizar da fila já existente) |
| `POST /api/reativar-emissao` | Recrutador | Reativa a emissão de senhas da PRÓPRIA empresa |
| `GET /api/relatorios/{csv,excel,pdf,resumo}` | Admin/Recrutador | Exporta/consulta relatórios (admin: filtro `empresa_id` opcional; recrutador: sempre restrito à própria empresa) |
| `POST /api/admin/usuarios` | Admin | Cria um usuário com perfil escolhido (admin/atendente/emissor — "recrutador" é recusado, ver seção 4.6) |
| `POST /api/admin/usuarios/<id>/resetar-senha` | Admin | Reseta a senha de um usuário |
| `POST /api/admin/usuarios/<id>/perfil` | Admin | Altera o perfil de um usuário (limpa a empresa se sair de "recrutador"; não aceita "recrutador" como destino) |
| `POST /api/admin/usuarios/<id>/empresa` | Admin | Vincula/desvincula a empresa de um recrutador |
| `POST /api/admin/usuarios/<id>/status` | Admin | Ativa/desativa um usuário |
| `POST /api/admin/reset-senhas-emitidas` | Admin | Apaga todo o histórico de senhas |
| `GET /api/admin/guiches` | Admin | Lista os guichês atualmente ocupados (fila geral) |
| `GET /api/admin/empresas` | Admin | Lista TODAS as empresas (ativas e inativas) |
| `POST /api/admin/empresas` | Admin | Cadastra uma nova empresa |
| `POST /api/admin/empresas/<id>/renomear` | Admin | Renomeia uma empresa |
| `POST /api/admin/empresas/<id>/regenerar-chave` | Admin | Gera uma nova chave de acesso de 8 dígitos, invalidando a anterior |
| `POST /api/admin/empresas/<id>/status` | Admin | Ativa/desativa uma empresa |
| `POST /api/admin/empresas/<id>/reiniciar-contador` | Admin | Reinicia o contador de senhas de UMA empresa (não afeta as demais) |
| `POST /api/admin/empresas/<id>/logo` | Admin | Envia o logo da empresa (multipart) e extrai a cor automaticamente |
| `POST /api/admin/empresas/<id>/cor` | Admin | Sobrescreve manualmente a cor da empresa |
| `POST /api/admin/empresas/<id>/reativar-emissao` | Admin | Reativa a emissão de senhas de qualquer empresa com a emissão bloqueada |

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
- **`resetar_sistema.py`**: script de linha de comando para "zerar" o
  sistema entre um evento e outro, mantendo apenas os usuários
  Administrador. Apaga todas as senhas, chamadas, empresas (e seus
  logos), usuários não-administradores, logs técnicos, e restaura as
  Configurações para o padrão de fábrica. Antes de apagar qualquer coisa,
  mostra na tela exatamente o que será removido/mantido e exige que você
  digite a frase `APAGAR TUDO` para confirmar — nada é apagado sem essa
  confirmação explícita. Uso:

  ```bat
  venv\Scripts\activate
  python resetar_sistema.py
  ```

  > **Faça um backup de `database/senhas.db` antes de rodar este
  > script** (ver seção 7) — a operação não pode ser desfeita.

### 12.4 Evolução recente do sistema (v2.3.0)

- **Correção do botão "Entrar" sem texto na tela de login**: a rota
  `/login` era a única do sistema que não repassava a variável `config`
  ao template. Sem ela, o Flask injetava sua própria variável interna
  `config` (as configurações do próprio Flask, sem `cor_principal`), o
  que fazia `layout.html` calcular `--cor-principal: ;` (vazio). Isso
  invalidava a cor de fundo do botão "Entrar" (que voltava ao branco
  padrão do navegador) enquanto o texto continuava branco — ou seja,
  texto branco sobre fundo branco, invisível. Corrigido passando
  `config=config_manager.obter_todas()` também nesta rota, como já era
  feito em todas as demais.
- **Menu superior compacto no celular**: em telas de até 600px, a barra
  de usuário (nome, perfil, mesa/guichê e links de ação) agora usa
  padding, fontes e espaçamentos reduzidos, ocupando bem menos altura de
  tela — antes só empilhava os itens, sem de fato compactar o tamanho.
- **Painel geral ("Abrir Painel" do emissor/atendente) com empresa e
  mesa**: a lista "Últimas Senhas Emitidas" deste painel (usado quando
  não há uma empresa específica logada) agora mostra também o nome da
  empresa da senha e a mesa/guichê que a chamou — ex.: `Senha 001
  Comigo Mesa 01` — no mesmo padrão já usado pelo painel de cada
  empresa.
- **Compartilhamento do painel da empresa via WhatsApp**: a tela
  Empresas (`/admin/empresas`) ganhou um botão "📲 WhatsApp" ao lado de
  "Abrir Painel" em cada linha, que abre o WhatsApp com o link público
  do painel daquela empresa já preenchido, pronto para enviar a um
  contato.
- **"Repetir Chamada" bloqueada para senha já finalizada ou cancelada**:
  antes, era possível clicar em "Repetir Chamada" mesmo depois de a
  senha chamada naquela mesa/guichê já ter sido finalizada (ou
  cancelada), reanunciando no painel uma senha cujo atendimento já
  havia terminado. Agora `database.repetir_ultima_chamada` verifica o
  status atual da senha antes de gerar uma nova chamada e recusa a
  repetição (erro 409) nesses casos — regra aplicada de forma
  centralizada, valendo igualmente para atendente e recrutador.

### 12.6 Evolução recente do sistema (v2.4.0)

- **Login do recrutador substituído por chave de acesso da empresa**:
  mudança de maior porte desta versão — descrita em detalhe na seção 4.6.
  Em resumo: recrutador não tem mais conta individual (login/senha)
  cadastrada por um administrador; em vez disso, cada empresa ganha uma
  chave numérica de 8 dígitos (gerada automaticamente ao cadastrá-la, e
  regenerável a qualquer momento em `/admin/empresas`), e qualquer pessoa
  da empresa entra pela página pública `/empresas/entrar` informando o
  próprio nome e essa chave. A conta de sessão é criada e removida
  automaticamente (ver `database.provisionar_usuario_recrutador` e
  `auth.encerrar_sessao`), sem deixar cadastros acumulados em "Gerenciar
  Usuários". Tentativas de chave incorreta usam a mesma proteção contra
  força bruta do login tradicional, e a chave nunca é exposta em nenhuma
  resposta pública (painel, página de seleção de empresas).

### 12.7 Evolução recente do sistema (v2.5.0)

- **"Finalizar Atendimento do Dia" renomeado para "Bloqueio de Emissão de
  Senhas" e com efeito mais restrito**: antes, encerrar o dia de uma
  empresa bloqueava tanto a emissão quanto a chamada de senhas, e
  cancelava automaticamente toda senha que ainda estava esperando na
  fila. Agora o bloqueio afeta **apenas a emissão** (`/api/emitir`) —
  chamar, repetir chamada e finalizar continuam funcionando normalmente
  para a fila já existente, e nenhuma senha é cancelada ao bloquear.
- **Reativação também pelo próprio recrutador**: antes, só um
  administrador podia reabrir o atendimento de uma empresa. Agora o
  próprio recrutador da empresa também pode reativar a emissão a
  qualquer momento (botão "🔓 Reativar Emissão de Senhas" na tela
  principal), além do administrador continuar podendo fazê-lo pela tela
  `/admin/empresas` para qualquer empresa.
- Rotas renomeadas: `POST /api/finalizar-atendimento-dia` →
  `POST /api/bloquear-emissao`; nova rota `POST /api/reativar-emissao`
  (recrutador, própria empresa); `POST
  /api/admin/empresas/<id>/reabrir-atendimento` →
  `POST /api/admin/empresas/<id>/reativar-emissao`. A coluna do banco
  `empresas.atendimento_finalizado_em` foi renomeada para
  `emissao_bloqueada_em` (migração automática e idempotente ao iniciar o
  servidor, preservando os dados existentes).

### 12.8 Evolução recente do sistema (v2.6.0)

- **Busca na Fila de Espera**: em todos os perfis, a tela principal
  ganhou um campo de pesquisa acima da tabela "Fila de Espera",
  filtrando por número da senha (aceita com ou sem os zeros à esquerda,
  ex.: "7" ou "007") ou por trecho do nome da pessoa (`nome_pessoa`,
  quando preenchido na emissão). Facilita localizar rapidamente uma
  senha específica antes de reimprimir (perfil Emissor) ou cancelar. A
  coluna "Nome" também passou a ser exibida na tabela, para mostrar o
  que casou com a busca.
- **Paginação da Fila de Espera**: antes, a fila sempre trazia apenas as
  20 senhas mais antigas aguardando — qualquer senha além dessas ficava
  inacessível (inclusive para a nova busca) sempre que havia mais de 20
  na fila. Agora a fila é paginada (20 por página, com botões "«
  Anterior"/"Próxima »"), dando acesso a todas as senhas aguardando,
  não só às mais antigas.
- Rota `GET /api/fila` passou a aceitar `?busca=texto&pagina=N`,
  retornando também `total_filtrado`, `pagina_atual`, `total_paginas` e
  `por_pagina` (ver `database.listar_fila_atual`/`contar_aguardando`).
  O contador "Fila de Espera (N)" no topo do card continua mostrando o
  total geral da fila, independente do filtro de busca aplicado.

### 12.9 Evolução recente do sistema (v2.7.0)

- **Correção — "Chamadas Realizadas" nunca mais ultrapassa "Senhas
  Emitidas"**: o "Resumo do Período" (tela Relatórios) contava todo
  EVENTO de chamada, inclusive repetições geradas por "Repetir Chamada"
  — uma senha repetida 5 vezes contava como 5 chamadas, podendo
  facilmente superar o total de senhas emitidas. Agora conta senhas
  DISTINTAS chamadas ao menos uma vez (ver
  `database.contar_chamadas_realizadas_periodo` e seção 4.9.1). O
  relatório de exportação "Chamadas Realizadas" (CSV/Excel/PDF) não
  mudou — continua listando cada evento individualmente, como um log
  auditável.
- **Duas opções fixas de emissão: "Criar Currículos" e "Imprimir
  Currículos"** (ver seção 4.9): sempre disponíveis para o Emissor,
  independente de quais empresas o administrador cadastrou. Senhas
  emitidas para elas já nascem "Finalizada" (sem fila, sem chamada) —
  ainda imprimem o ticket normalmente, mas contam só como "emitidas" nos
  relatórios, nunca como "chamadas". Não podem ser renomeadas nem
  desativadas, e não aparecem no login público de recrutador por chave.

### 12.10 Evolução recente do sistema (v2.8.0)

- **Painéis públicos não mostram mais senhas Finalizadas nem Canceladas**
  (ver seção 4.6): antes, a lista "Últimas Senhas Emitidas" do painel
  geral de chamadas (`/painel`) e do painel por empresa
  (`/painel/empresa/<id>`) misturava senhas de qualquer status,
  incluindo atendimentos já encerrados ou cancelados há tempo. O Painel
  Geral (`/painel/geral`) também exibia cartões e uma coluna "Atendidas"
  e "Canceladas" na tabela por empresa. Agora todos os três painéis
  mostram só o que está em andamento (aguardando ou em atendimento) —
  ver `database.listar_ultimas_emitidas`. O cálculo completo (incluindo
  atendidas/canceladas) continua disponível no backend
  (`database.resumo_geral_senhas`) e na tela `/relatorios`, para quem
  precisar do histórico.

### 12.11 Evolução recente do sistema (v2.9.0)

- **Correção — "Criar Currículos"/"Imprimir Currículos" agora contam
  como "Chamadas Realizadas"** (ver seção 4.9.1): desde que essas duas
  opções fixas passaram a existir (v2.7.0), elas já contavam
  corretamente como "senhas emitidas" em todo o sistema (Painel Geral,
  Resumo do Período, relatórios de exportação), mas ficavam de fora da
  contagem de "chamadas realizadas" — por não gerarem nenhum evento em
  `eventos_chamada` (não têm fila nem guichê chamando), a antiga lógica
  (baseada em `eventos_chamada`) simplesmente não as via. Como uma
  senha emitida para elas nasce diretamente "Finalizada" — ou seja, o
  atendimento de fato aconteceu — isso estava incorreto: elas deveriam
  contar como "realizadas", só não como "chamadas por um guichê".
  `database.contar_chamadas_realizadas_periodo` foi reescrita para
  contar por `senhas.hora_chamada` (preenchida tanto por uma chamada de
  verdade quanto pela criação de uma senha fixa) em vez de por linhas em
  `eventos_chamada` — resultado idêntico para empresas comuns (o
  invariante "chamadas ≤ emitidas" da v2.7.0 continua valendo), e agora
  inclui as duas opções fixas também. O relatório de exportação
  "Chamadas Realizadas" (CSV/Excel/PDF, um log de eventos reais em
  guichê) continua sem incluí-las — critério inalterado.

### 12.12 Evolução recente do sistema (v2.10.0)

- **Correção — perfil Emissor não tinha nenhuma confirmação visível de
  que uma emissão para "Criar Currículos"/"Imprimir Currículos" havia
  sido contabilizada**: essas duas opções fixas nascem já com status
  'Finalizada' (sem fila, sem chamada — ver seção 4.9), então nunca
  aparecem no contador "Fila de Espera" (`database.contar_aguardando`,
  só conta status 'Emitida') nem nos Painéis públicos
  (`database.listar_ultimas_emitidas`, que desde a v2.8.0 oculta
  Finalizada/Cancelada de propósito — ver seção 12.10). O total correto
  já existia nos Relatórios, mas essa tela é restrita a admin/recrutador
  (`auth.admin_ou_recrutador_required`) — o perfil Emissor não consegue
  abri-la. Na prática, um operador Emissor que emitia um ticket dessas
  duas opções não via NENHUM número mudar na própria tela, dando a
  impressão de que a emissão "sumiu" (mesmo estando corretamente
  registrada no banco e nos Relatórios). Corrigido adicionando um novo
  contador "📋 Emitidas hoje" no card "Fila de Espera" da tela principal
  (`templates/index.html`), alimentado por
  `database.contar_emitidas_hoje` — conta por `date(data_hora)`, em
  QUALQUER status, sem a exclusão de Finalizada/Cancelada que os
  Painéis aplicam de propósito. O campo `total_emitidas_hoje` foi
  adicionado à resposta de `/api/fila` (já consultada em polling pela
  tela principal, sem precisar de uma rota nova) e é renderizado por
  `static/js/index.js`.

### 12.13 Evolução recente do sistema (v2.11.0)

- **Painel Geral ganhou a seção "Resumo do Feirão"** (ver seção 4.6):
  até aqui, apesar do subtítulo da tela já dizer "Painel Geral — Resumo
  do Feirão", o conteúdo mostrado era só a fila em andamento
  (Aguardando/Em Atendimento/Total) — nenhum total ACUMULADO do evento
  inteiro. Agora, logo abaixo desses cards, uma nova seção "Resumo do
  Feirão" mostra: Total de Senhas Emitidas, Total de Atendimentos
  Realizados e Tempo Médio de Atendimento — todos calculados sobre TODO
  o histórico (sem filtro de período, já que este painel não tem
  seletor de data), incluindo as senhas já finalizadas/canceladas e as
  duas opções fixas ("Criar Currículos"/"Imprimir Currículos"). Isso é
  uma exceção proposital ao critério "esconde Finalizada/Cancelada" que
  vale para os cards "em andamento" e a tabela "Por Empresa" logo
  acima — ali o objetivo continua sendo mostrar só a fila do momento;
  aqui, o resultado geral acumulado. Implementado em
  `app.py:api_painel_geral_status` (novo campo `resumo_feirao`, usando
  `database.contar_chamadas_realizadas_periodo` e
  `database.tempo_medio_atendimento` sem período) e renderizado por
  `static/js/painel_geral.js`.

- **Novo card "Última Senha por Empresa" na tela do Emissor** (ver
  seção 4.5), exibido logo ACIMA da Fila de Espera: lista, para cada
  empresa ativa, o número da última senha emitida (e o nome da pessoa,
  se informado) — qualquer que seja o status atual, inclusive das duas
  opções fixas (que nascem já 'Finalizada'). Diferente da Fila de
  Espera (só mostra o que está aguardando), o objetivo aqui é dar ao
  Emissor uma visão rápida de "até onde a numeração de cada empresa já
  chegou", sem precisar abrir o Painel Geral ou os Relatórios. O card
  se atualiza sozinho no mesmo ciclo de polling da Fila de Espera logo
  abaixo — tanto ao emitir uma nova senha quanto ao cadastrar uma nova
  empresa (a consulta busca a lista de empresas "ao vivo" a cada
  requisição, sem cache). Implementado em
  `database.listar_ultima_senha_por_empresa` (um `LEFT JOIN`
  correlacionado, para que empresas sem nenhuma senha ainda também
  apareçam na lista), exposto em `app.py:api_fila` (campo
  `ultimas_por_empresa`) e renderizado por `static/js/index.js`.

### 12.14 Evolução recente do sistema (v2.12.0)

- **Confirmado (sem necessidade de correção) — Resumo do Feirão já
  soma Criar Currículos/Imprimir Currículos e senhas Canceladas**:
  `database.resumo_geral_senhas` conta TODAS as senhas por status, sem
  nenhum filtro de empresa ou status — então `total_emitidas` do
  `resumo_feirao` (ver seção 12.13) já incluía as duas opções fixas e
  as canceladas desde a v2.11.0. Foram adicionados testes de regressão
  explícitos (`tests/test_paineis.py`) travando esse comportamento,
  para garantir que uma mudança futura não quebre isso silenciosamente.

- **"Última Senha por Empresa" (tela do Emissor) ganhou a última senha
  CHAMADA, além da última EMITIDA**: agora a tabela tem duas colunas a
  mais — "Última Chamada" e "Chamada em" — mostrando qual senha foi
  efetivamente chamada por último em cada empresa (usando
  `hora_chamada`, o mesmo campo já usado por
  `contar_chamadas_realizadas_periodo` — inclui as duas opções fixas,
  que "chamam a si mesmas" ao nascer). Como a fila é FIFO, a última
  chamada normalmente é uma senha mais ANTIGA que a última emitida
  (ex.: emissor emite as senhas 001, 002 e 003; só a 001 foi chamada
  até agora — a tela mostra "Última Emitida: 003" e "Última Chamada:
  001" lado a lado). Implementado estendendo
  `database.listar_ultima_senha_por_empresa` com um segundo `LEFT
  JOIN` correlacionado (ordenado por `hora_chamada DESC`, não por
  `id DESC`).

- **Relatórios (Administrador) — nova coluna "Senhas Atendidas" em
  "Senhas por Empresa"**: até aqui essa tabela só mostrava "Senhas
  Emitidas" por empresa; agora mostra também quantas dessas senhas
  foram efetivamente atendidas (mesmo critério de `hora_chamada IS NOT
  NULL` usado no invariante "chamadas ≤ emitidas" da v2.7.0 — imune à
  inflação por repetição de chamada, e nunca maior que "Senhas
  Emitidas" da mesma linha). Implementado adicionando `SUM(CASE WHEN
  hora_chamada IS NOT NULL THEN 1 ELSE 0 END)` à mesma consulta de
  `database.listar_contagem_por_empresa` (evita uma segunda consulta
  separada e problemas de sincronização entre duas listas agrupadas
  por empresa).

### 12.15 Evolução recente do sistema (v2.12.1)

- **Correção — coluna "Total" da tabela "Por Empresa" no Painel Geral
  ficava sempre em 0 para empresas sem senha "em andamento"**: a
  coluna era calculada no frontend como `aguardando + em_atendimento`,
  ignorando o campo `total` que o backend já calculava (soma de TODOS
  os status, inclusive Finalizada/Cancelada — ver
  `database.resumo_geral_senhas`). Na prática, qualquer empresa já
  totalmente atendida no momento da consulta — e, sempre, as duas
  opções fixas "Criar Currículos"/"Imprimir Currículos", que nascem
  direto 'Finalizada' — aparecia com "Total: 0" na tabela, mesmo tendo
  emitido senhas normalmente. Corrigido em
  `static/js/painel_geral.js` (`atualizarTabelaEmpresas`), que agora
  usa `linha.total` (já vem pronto do servidor) em vez de recalcular a
  soma parcial. As colunas "Aguardando"/"Em Atendimento" continuam
  mostrando só a fila do momento, de propósito — só "Total" passou a
  refletir o total geral de cada empresa.

### 12.16 Evolução recente do sistema (v2.13.0)

- **Tela principal (index.html) ganhou layout em duas colunas**: antes,
  o cartão de identificação (Atendente/Recrutador/Emissor/Administrador
  Logado), o menu de botões e a Fila de Espera ficavam todos empilhados
  em uma única coluna estreita, deixando bastante espaço horizontal sem
  uso em telas grandes e obrigando bastante rolagem vertical até
  chegar na Fila. Agora, à esquerda fica uma coluna estreita (cartão de
  identificação + menu de botões) e à direita uma coluna larga com a
  Fila de Espera (e, para o Emissor, também "Última Senha por
  Empresa") — tabelas se beneficiam de mais espaço horizontal. A
  página também ficou um pouco mais larga (1600px em vez do padrão de
  1200px usado pelas demais telas) para acomodar as duas colunas
  confortavelmente. Em telas estreitas (celular/tablet), as duas
  colunas voltam a empilhar verticalmente, como antes. Implementado
  via duas classes CSS restritas a esta tela
  (`.conteudo-principal--tela-principal`/`.pagina-larga`), sem afetar o
  layout das demais páginas (Relatórios, Empresas, Usuários,
  Configurações), que continuam reaproveitando `.conteudo-principal`
  em coluna única.

### 12.17 Evolução recente do sistema (v2.14.0)

- **Fila de Espera — o recrutador agora pode selecionar várias senhas e
  chamá-las de uma vez ("Chamar Selecionadas")**: até aqui, cada senha
  só podia ser chamada individualmente (uma de cada vez, sempre a
  próxima da fila em ordem FIFO, via "Chamar Próxima"). Agora, na Fila
  de Espera, o recrutador marca os checkboxes das senhas desejadas
  (uma coluna nova, só visível para o perfil recrutador) e clica em
  "Chamar Selecionadas" para chamar todas de uma vez, em qualquer
  ordem escolhida — útil quando várias vagas da mesma empresa vão ser
  atendidas juntas. A seleção é restrita à página atual da fila (some
  ao trocar de página ou buscar, evitando ids "fantasma" de uma tela
  antiga) e existe também "Selecionar todas" (cabeçalho da tabela) e
  "Limpar seleção".

- **Painel Público exibe a sequência inteira chamada em conjunto**: as
  senhas chamadas juntas por "Chamar Selecionadas" aparecem no painel
  (geral, por empresa, e na caixa "Última Senha Chamada" da tela do
  recrutador/atendente) como uma sequência única, ex. "005, 006, 007",
  em vez de mostrar só a primeira ou disparar várias animações
  separadas. Chamadas individuais continuam mostrando um único número,
  como sempre.

- **Isolamento entre empresas chamando ao mesmo tempo**: o requisito
  explícito era que a sequência chamada por uma empresa NUNCA vaze ou
  se misture com a de outra empresa chamando simultaneamente. Isso é
  garantido por um novo conceito de "lote" (`eventos_chamada.lote_chamada`,
  um identificador aleatório de 12 caracteres gerado a cada operação de
  chamada — ver `database._gerar_lote_chamada`): todas as senhas
  chamadas juntas numa mesma operação compartilham o mesmo lote, e
  `database.obter_chamada_atual` sempre escopa a busca do "lote mais
  recente" por `empresa_id` antes de buscar os eventos daquele lote —
  então o painel de uma empresa nunca enxerga o lote de outra, mesmo
  com chamadas em lote intercaladas no tempo. Testado explicitamente em
  `tests/test_chamar_varias.py`
  (`test_obter_chamada_atual_isolamento_entre_empresas_com_lotes_simultaneos`).

- **"Repetir Chamada" continua repetindo só a última senha**, mesmo que
  ela tenha feito parte de uma chamada em lote — decisão deliberada
  para não reanunciar o lote inteiro sem o recrutador pedir
  explicitamente (gera um novo lote próprio, de uma única senha).

- Implementado em: `database.py` (`chamar_varias` — validação "tudo ou
  nada", nenhuma senha é alterada se qualquer uma da lista for
  inválida/já chamada/de outra empresa; `obter_chamada_atual`
  reescrito para buscar o lote inteiro), `app.py` (rota
  `POST /api/chamar-varias`, com a mesma checagem de permissão por id
  já usada em `/api/cancelar`/`/api/reimprimir`), `templates/index.html`
  + `static/js/index.js` (checkboxes, barra de seleção, botão) e
  `static/js/painel.js`/`painel_empresa.js` (exibição da sequência).

### 12.18 Evolução recente do sistema (v2.15.0)

- **Confirmado (sem necessidade de correção) — "Total de Atendimentos
  Realizados" (Resumo do Feirão, Painel Geral) já contabiliza Criar
  Currículos/Imprimir Currículos**: esse total é calculado por
  `database.contar_chamadas_realizadas_periodo` (baseado em
  `senhas.hora_chamada IS NOT NULL`), e as duas opções fixas já nascem
  com `hora_chamada` preenchida no momento da emissão (ver
  `criar_senha`, `finalizar_imediatamente` — elas não têm fila nem
  chamada, a própria emissão já É o atendimento). Foi adicionado um
  teste de regressão explícito e isolado
  (`test_resumo_do_feirao_atendimentos_realizados_inclui_curriculos_fixos`
  em `tests/test_paineis.py`) para travar esse comportamento, já que
  era exatamente o que foi pedido.

- **Correção — "Total de Senhas Emitidas" (Resumo do Feirão) não deve
  contar senhas Canceladas**: antes, esse total somava TODOS os
  status (Aguardando + Em Atendimento + Finalizada + Cancelada), então
  uma senha cancelada ainda inflava o número exibido no painel público,
  mesmo não representando nenhum atendimento real. Agora o cálculo
  subtrai as Canceladas (`resumo.total_emitidas - resumo.total_canceladas`
  em `app.py:api_painel_geral_status`), sem alterar o dado bruto
  retornado por `database.resumo_geral_senhas` (que continua somando
  por status sem filtro, usado por outras partes do sistema, como a
  coluna "Total" da tabela "Por Empresa" do mesmo painel — essa
  continua incluindo Canceladas de propósito, para não voltar a mostrar
  "Total: 0" em empresas já totalmente atendidas). "Total de
  Atendimentos Realizados" não muda com esta correção — já não contava
  Canceladas antes (soma por `hora_chamada`, que na prática nunca é
  preenchida antes do cancelamento: o botão "Cancelar" só é oferecido
  na Fila de Espera, que só lista senhas ainda com status 'Emitida').

### 12.19 Evolução recente do sistema (v2.16.0)

- **Painéis públicos não devem mais destacar uma senha já
  finalizada**: antes, depois que o recrutador clicava em "Finalizar
  Atendimento" (sem chamar mais nenhuma senha em seguida), o painel
  público (`/painel`, `/painel/empresa/<id>`) continuava mostrando
  aquela mesma senha em destaque "para sempre" — porque
  `database.obter_chamada_atual` só olhava para o evento mais recente
  em `eventos_chamada` (um LOG que nunca é reescrito), sem checar se a
  senha correspondente já tinha sido atendida. Agora a função só
  considera senhas com status ainda 'Chamada' (em atendimento); se
  todas as senhas do lote mais recente já estiverem 'Finalizada', o
  destaque desaparece — sem "recuar" para um lote mais antigo (um
  destaque velho seria tão confuso quanto nenhum). Isso também
  funciona corretamente no meio de uma chamada em lote ("Chamar
  Selecionadas" — ver seção 12.17): se 1 de 3 senhas do lote já foi
  finalizada, o destaque passa a mostrar só as 2 que continuam em
  atendimento.

- **Nova mensagem de espera "Aguardando emissão de senha"**: tanto a
  caixa de destaque quanto a lista "Últimas Senhas Emitidas" dos
  painéis públicos passam a mostrar essa mensagem sempre que não há
  nenhuma senha pendente (aguardando ou em atendimento) no momento —
  seja porque nenhuma senha foi emitida ainda, seja porque todas as
  emitidas já foram atendidas. Antes, a caixa de destaque usava o
  texto "Aguardando primeira chamada" só para o caso de nunca ter
  havido nenhuma chamada; agora os dois cenários (nunca chamou / já
  finalizou tudo) mostram a mesma mensagem, já que não há diferença
  prática entre eles do ponto de vista de quem olha o painel.

- Implementado em `database.py` (`obter_chamada_atual` — filtro
  `s.status = 'Chamada'` no passo 2 da busca) e
  `static/js/painel.js`/`painel_empresa.js` (textos de espera). A
  tela principal do recrutador/atendente (`index.html`, caixa "Última
  Senha Chamada") reaproveita a mesma rota/lógica de status, então se
  beneficia automaticamente da mesma correção.

### 12.20 Evolução recente do sistema (v2.17.0)

- **`/login` ganhou um painel com as empresas cadastradas ao lado do
  formulário**: antes, um recrutador que caísse na tela de login
  tradicional (usuário/senha) só descobria o acesso por chave da sua
  empresa através de um link de texto ("Entre por aqui") levando à
  tela cheia `/empresas/entrar`. Agora a própria tela de login já
  mostra, ao lado do formulário, os mesmos cards de empresa dessa tela
  (logo + nome), cada um linkando direto para
  `/empresas/<id>/entrar` — sem precisar navegar para outra página. Em
  telas estreitas (celular), o painel empilha abaixo do formulário de
  login em vez de ficar ao lado.

- Segue as mesmas regras de sempre para o que pode aparecer nessa
  listagem pública: só empresas ATIVAS, nunca as duas opções fixas do
  sistema ("Criar Currículos"/"Imprimir Currículos" — não são
  participantes reais do feirão e não têm recrutador), e a
  `chave_acesso` de 8 dígitos de cada empresa nunca é enviada ao HTML
  (removida antes de chegar ao template). Sem nenhuma empresa
  cadastrada, aparece o aviso "Nenhuma empresa cadastrada no momento."

- Implementado extraindo a lógica de listagem/filtro, que já existia
  duplicada em `empresas_entrar_tela`, para uma função compartilhada
  `_listar_empresas_publicas()` em `app.py`, reaproveitada tanto por
  `login_tela` quanto por `empresas_entrar_tela` (que continua
  existindo normalmente, como a versão em tela cheia). Layout novo
  restrito a `templates/login.html` via a classe modificadora
  `.pagina-auth--com-empresas` (ver `static/css/style.css`), sem afetar
  `empresa_login.html`/`empresas_publico.html`, que continuam com o
  card único centralizado de sempre.

### 12.21 Evolução recente do sistema (v2.18.0)

- **Tema escuro em todo o sistema, com botão de troca**: adicionado um
  botão (ícone ☀️/🌙) que alterna entre tema claro e escuro, disponível
  em toda tela do sistema — dentro da `barra-usuario` para quem está
  logado, e flutuando no canto superior direito nas páginas públicas de
  autenticação (`/login`, `/empresas/entrar`, `/empresas/<id>/entrar`).
  A escolha é salva em `localStorage` (`sigs_tema`) e persiste entre
  visitas; aplicada por um pequeno script inline no `<head>` de
  `layout.html`, ANTES do CSS ser processado, para não piscar em tema
  claro por uma fração de segundo a cada carregamento de página.

- **Paleta pesquisada no GitHub**: a cor escura segue o tema oficial
  do GitHub (Primer design system) — `canvas.default #0D1117`,
  `canvas.overlay #161B22`, `border.default #30363D`,
  `fg.default #E6EDF3`, `fg.muted #8B949E`, `accent.emphasis #2F81F7`,
  `success.fg #3FB950`, `danger.fg #F85149` — um dos esquemas de tema
  escuro mais usados e testados do mundo, adaptado mantendo o azul
  institucional e o amarelo de destaque do SENAI como identidade visual
  (não substituídos por um azul genérico de "app dark mode").

- **O painel público de TV fica de fora do tema, de propósito**: as
  três telas do painel de exibição pública (`/painel`,
  `/painel/empresa/<id>`, `/painel/geral`) NUNCA recebem o botão, o
  script inline nem `tema.js` — nem mesmo se um administrador logado
  (com preferência de tema escuro salva) navegar até lá. Esse painel já
  tem identidade visual própria e permanentemente escura (gradiente
  `--cor-principal` → `#001F3D`), pensada para ficar sempre igual numa
  tela compartilhada (TV/monitor do evento), independente da
  preferência de quem estiver controlando o navegador — trocar de tema
  ali faria a cor mudar para quem está vendo a TV sem ligação nenhuma
  com a preferência de ninguém em especial.

- Implementado via variáveis CSS existentes (`static/css/style.css`):
  introduzida `--cor-superficie` (fundo de cards — a única que
  realmente escurece; `--cor-branco` continua sempre branco de
  verdade, usada por texto sobre botões coloridos e o fundo do logo no
  painel de TV) e mais algumas variáveis novas para cores antes
  "soltas" no arquivo (`--cor-texto-secundario`, `--cor-texto-label`,
  fundos translúcidos de badges/mensagens de status), todas
  sobrescritas em bloco por `html[data-tema="escuro"]`. Com isso, o
  tema inteiro reage a UM único atributo no `<html>`, sem precisar
  duplicar regra por regra da folha de estilos. `templates/layout.html`
  ganhou a variável Jinja `pagina_com_tema` (`request.endpoint not in
  ['painel', 'painel_empresa', 'painel_geral']`), usada nos três pontos
  do recurso (script inline, `tema.js`, botão) para garantir a exclusão
  do painel de TV de forma consistente.

### 12.22 Evolução recente do sistema (v2.19.0)

- **Correção e organização dos botões/grade de "Empresas Cadastradas"
  (Administrador)**: a tabela de empresas é a mais densa do sistema (8
  colunas, várias com múltiplos botões cada) e tinha três problemas
  reais:

    1. **Espaçamento duplicado** — o container flex `.acoes-usuario`
       já espaça os botões com `gap: 8px`, mas cada botão também tinha
       `margin-right: 6px` próprio (necessário em OUTRO contexto, a
       coluna "Ações" da Fila de Espera, montada via JS sem esse
       container flex ao redor) — as duas coisas somadas deixavam o
       respiro horizontal entre botões maior que o vertical quando a
       linha quebrava, um desalinhamento visual perceptível. Corrigido
       zerando a margem só dentro de `.acoes-usuario`.
    2. **Cor da empresa vazando para os botões errados** — o comentário
       do código sempre disse que só "Abrir Painel", "Logo" e
       "Renomear" deveriam usar a cor cadastrada da empresa
       (`--cor-empresa`), mas o seletor CSS real pegava QUALQUER link
       dentro de `.acoes-usuario`, incluindo por engano os dois botões
       de "Compartilhar via WhatsApp" — na prática, quase todos os
       botões da linha ficavam da mesma cor (a cor clara padrão, para
       empresas sem cor própria definida), sem nenhuma hierarquia
       visual. Corrigido restringindo o seletor aos três botões
       corretos (o link "Abrir Painel" ganhou uma classe própria,
       `btn-abrir-painel-empresa`, para isso).
    3. **Sem rolagem própria em telas de desktop comuns** — as tabelas
       do sistema só ganhavam `overflow-x: auto` abaixo de 700px; nesta
       tabela específica, mesmo em laptops normais, o conteúdo (8
       colunas bem cheias) facilmente ultrapassava a largura da página,
       forçando quebras de linha feias dentro das células em vez de
       simplesmente rolar. Corrigido com uma classe própria
       (`.card-tabela-empresas`) que liga a rolagem horizontal sempre,
       com `min-width: 1100px` na tabela.

  Também aproveitado para dar aos dois botões de "Compartilhar via
  WhatsApp" a cor de marca do WhatsApp (`.botao-whatsapp`, `#25D366`),
  em vez de reutilizar a cor institucional/da empresa — agora dá para
  reconhecer de relance quais botões da linha são "compartilhar" e
  quais são "gerenciar" (Renomear, Reiniciar Contador — este último
  também voltou a ser um botão padrão do sistema, não mais colorido
  com a cor da empresa).

### 12.23 Revisão sênior geral do sistema (v2.20.0)

Revisão pedida explicitamente pelo usuário ("revise todo o sistema,
corrija os bugs, corrija o CSS, corrija todos os Relatórios, Resumos e
Painéis"), cobrindo bugs de negócio, contraste/CSS e consistência entre
Relatórios/Painéis. Correções aplicadas:

- **Contraste do tema escuro em botões sólidos (CSS)** — `--cor-principal`
  e `--cor-principal-clara`, no tema escuro, tinham um conflito de "dois
  papéis": funcionavam bem como cor de TEXTO sobre o fundo escuro
  (`#2F81F7`/`#58A6FF`, alto contraste), mas como fundo SÓLIDO de botão
  com texto branco por cima (`.botao-primario`, `.botao-secundario`,
  `.barra-usuario`, `.rodape-sigs`, botões de identidade visual da
  empresa) o contraste ficava abaixo do recomendado pelo WCAG AA
  (calculado: branco sobre `#2F81F7` = 3,75:1; branco sobre `#58A6FF` =
  2,53:1 — o mínimo recomendado é 4,5:1). Criadas duas variáveis novas,
  só para esse papel de preenchimento sólido — `--cor-botao-principal`
  (`#1F6FEB` no escuro, 4,63:1 com texto branco) e
  `--cor-botao-secundario` (`#0969DA`, 5,19:1) —, sem alterar em nada o
  tema claro (valores idênticos a `--cor-principal`/`-clara`) nem o papel
  de texto/borda das variáveis originais.
- **Mensagens "flash" do Flask sem estilo nenhum (CSS)** — o wrapper
  `.flash-mensagens` (topo de qualquer página após um redirecionamento,
  ex.: erro de login) não tinha NENHUMA regra CSS: as mensagens
  apareciam como texto solto colado no topo da página. Adicionado um
  estilo de "faixa colorida" (mesmo espírito de `.notificacao`), com
  fundo sutil verde/vermelho conforme sucesso/erro.
- **Reanúncio falso no painel público após finalização parcial de um
  lote (bug de negócio, `database.obter_chamada_atual`)** — quando o
  recrutador chama VÁRIAS senhas de uma vez ("Chamar Selecionadas") e
  finaliza a PRIMEIRA delas enquanto as demais continuam em atendimento,
  o "id" usado pelo painel para decidir se toca o bipe/anima a tela
  mudava mesmo sem nenhuma chamada nova ter ocorrido — disparando um
  reanúncio indevido no meio do atendimento normal do lote. Corrigido:
  os campos de nível raiz (id/número/...) agora sempre espelham o
  PRIMEIRO evento do LOTE COMPLETO (estável), independente de quantas
  senhas do lote já tenham sido finalizadas individualmente.
- **Cancelamento de senha sem checar o status (bug de negócio,
  `database.cancelar_senha`)** — a função cancelava uma senha em
  QUALQUER status, inclusive uma que já tivesse sido chamada. Isso
  criava uma inconsistência no Painel Geral: a senha saía de "Total de
  Senhas Emitidas" (que exclui Canceladas) mas continuava contando em
  "Total de Atendimentos Realizados" (baseado em `hora_chamada` ter
  sido preenchida). Corrigido: só é permitido cancelar uma senha ainda
  com status `'Emitida'` (aguardando na fila).
- **Lote de chamada misturando empresas diferentes (bug de negócio,
  `database.chamar_varias`)** — o perfil "atendente" opera a fila
  GERAL, compartilhada entre todas as empresas, e chama
  `chamar_varias` sem `empresa_id`. Sem checagem, isso permitia
  selecionar e chamar juntas, num único lote, senhas de empresas
  DIFERENTES — mas o painel só tem um campo "empresa" por lote, então a
  sequência exibida ficava rotulada só com o nome da primeira empresa.
  Corrigido: a operação inteira é rejeitada se as senhas selecionadas
  pertencerem a mais de uma empresa.
- **Inconsistência de data no "Resumo do Período" dos Relatórios
  (`database.contar_chamadas_realizadas_periodo`)** — o filtro de
  período usava `date(hora_chamada)` (data da CHAMADA), diferente de
  TODAS as demais consultas de Relatórios, que usam `date(data_hora)`
  (data de EMISSÃO). Uma senha emitida perto da virada do dia e só
  chamada no dia seguinte contava em "Atendidas" de um dia diferente de
  "Emitidas", quebrando o invariante básico do resumo (atendidas nunca
  maior que emitidas naquele período). Corrigido para usar a mesma
  coluna de data (emissão) que o restante do relatório.
- **Proteção contra requisições de polling sobrepostas (JS)** —
  `painel.js`, `painel_empresa.js`, `painel_geral.js` (atualização
  automática dos painéis públicos) e `relatorios.js` (botão "Atualizar
  Resumo") não tinham nenhuma proteção contra chamadas de rede
  sobrepostas: numa rede lenta/instável, uma resposta mais ANTIGA podia
  chegar DEPOIS de uma mais nova (fora de ordem), fazendo a tela
  "voltar no tempo" — inclusive reanunciando (bipe/animação) uma
  chamada já superada. Adicionado um guarda simples (flag booleana) que
  pula a rodada de polling seguinte enquanto a anterior ainda não
  respondeu.

Cobertura de testes: 11 testes novos (`tests/test_chamar_varias.py`,
`tests/test_relatorios.py`, `tests/test_cancelamento.py` — novo
arquivo), cobrindo cada correção de negócio acima; suíte completa
(133 testes) e smoke tests manuais (CSS servido e estabilidade do
painel após finalização parcial de lote) confirmados após a correção.

### 12.24 "Senhas Emitidas" da tela de Relatórios ainda contava Canceladas (v2.20.1)

Ajuste de continuidade da revisão sênior acima, apontado pelo usuário
depois de ver a tela em uso: o card "Senhas Emitidas" do "Resumo do
Período" (tela de Relatórios) e a coluna "Senhas Emitidas" da tabela
"Senhas por Empresa", na mesma tela, ainda contavam TODAS as senhas do
período — inclusive as Canceladas. O mesmo critério já tinha sido
aplicado ao "Total de Senhas Emitidas" do Painel Geral desde a
v2.15.0 (`resumo_feirao.total_emitidas`), mas não tinha chegado a esta
tela.

Corrigido em dois pontos:
- `app.py:api_relatorios_resumo` — o total do card agora exclui
  Canceladas, contando a partir da lista completa retornada por
  `database.listar_senhas_periodo` (que continua trazendo TODAS as
  senhas, cancelada ou não — ela também alimenta a exportação em
  CSV/Excel/PDF, onde uma senha cancelada precisa continuar aparecendo
  na lista para fins de auditoria; só o total do resumo muda).
- `database.listar_contagem_por_empresa` — a coluna "total" (rotulada
  "Senhas Emitidas" na tabela por empresa) agora também exclui
  Canceladas diretamente na consulta SQL, para que a soma dessa coluna
  volte a bater com o card de resumo logo acima.

### 12.25 Revisão de performance e concorrência (v2.21.0)

Revisão pedida explicitamente pelo usuário para o cenário de um feirão
grande: até **24 empresas** com recrutador próprio, **6 pontos de
emissão de senha** e **1 painel de TV (85")**, todos na mesma rede
Wi-Fi (AP Ruckus), acessando o SIGS ao mesmo tempo — com o objetivo de
eliminar travamentos e qualquer colisão de informação nas chamadas de
senha. Pesquisa de boas práticas (SQLite WAL/PRAGMAs, dimensionamento
de threads do waitress em Windows) usada para embasar as correções.

**O achado mais importante — lock global demais em `chamar_proxima`**
(`database.py`): a operação de "Chamar Próxima" (o clique mais
repetido do sistema, disparado por CADA um dos até 24 recrutadores)
usava um único lock GLOBAL (`_lock`), serializando entre si até
chamadas de empresas completamente diferentes, com filas totalmente
independentes — um recrutador da Empresa A esperava, sem necessidade
nenhuma, o recrutador da Empresa B terminar de chamar a própria senha.
Com poucas empresas isso era imperceptível (por isso a escolha
original), mas com até 24 recrutadores clicando por perto do mesmo
instante (ex.: logo após um intervalo do evento), essa espera
artificial é exatamente o tipo de "travadinha" relatado. Corrigido com
um novo esquema de lock (`database._lock_para_chamar`), escopado por
empresa sempre que possível:
- Recrutador (`empresa_id` informado): usa só o lock DAQUELA empresa
  (`_lock_da_empresa`) — chamadas de empresas diferentes nunca mais
  esperam umas pelas outras.
- Perfil "atendente" (fila GERAL, sem `empresa_id`, mistura senhas de
  TODAS as empresas): adquire o lock de TODAS as empresas antes de ler
  a fila (mesmo princípio já usado por `reiniciar_contador`) — do
  contrário, o atendente e um recrutador específico, chamando ao mesmo
  tempo, poderiam disputar a MESMA senha sem nenhum lock em comum
  protegendo os dois.

Aplicado também a `chamar_varias` ("Chamar Selecionadas") e a
`ocupar_proximo_guiche_empresa_disponivel` (atribuição automática de
mesa a um recrutador no login — antes também serializada globalmente
entre as 24 empresas sem necessidade). Provado com testes de
concorrência REAIS (threads de verdade, ver `tests/test_concorrencia.py`):
nenhuma senha é chamada duas vezes sob disputa simultânea, nenhuma
senha "vaza" para a empresa errada, e uma chamada de uma empresa NÃO
espera o lock de outra (teste cronometra e confirma que uma chamada da
Empresa B retorna quase instantaneamente mesmo com a Empresa A
segurando o próprio lock por 0,6s).

**Servidor de produção (`wsgi.py`)**: threads do waitress aumentadas de
24 para **64** — cada tela em polling automático (toda tela
operacional + cada painel público aberto) ocupa uma thread enquanto a
requisição está em andamento; com até ~31 dispositivos fazendo polling
a cada poucos segundos (24 recrutadores + 6 emissão + 1 painel de TV),
o valor antigo (dimensionado para bem menos empresas) ficava perto do
limite, fazendo requisições esperarem uma thread livre nos picos.

**Consultas de banco desnecessárias em cada poll (`app.py:api_fila`)**:
a rota consultada por toda tela operacional a cada poucos segundos
calculava `listar_ultima_senha_por_empresa()` (duas subconsultas
correlacionadas POR EMPRESA ATIVA) em TODO poll de TODO perfil, mesmo
que esse dado só seja exibido na tela do perfil Emissor — para
recrutadores e atendentes (a maioria das ~30 telas operacionais), era
trabalho de banco puro desperdício. Agora só é calculado quando quem
está pedindo é, de fato, um Emissor.

**PRAGMAs de performance do SQLite** (`database.get_connection`,
`config.ConfigManager._conectar`): adicionados `synchronous=NORMAL`
(seguro em conjunto com o `journal_mode=WAL` já existente, bem mais
rápido que o padrão `FULL`), `cache_size=-20000` (~20 MB de cache de
páginas por conexão) e `temp_store=MEMORY` (ordenações dos relatórios
em memória, não em arquivo temporário).

**Colisão de informação sob rede instável (`static/js/index.js`)**: a
tela mais usada do sistema (`atualizarFila`, aberta o evento inteiro
por todo perfil operacional) não tinha proteção contra requisições de
polling sobrepostas — sob variação de latência da rede Wi-Fi, duas
respostas podiam chegar fora de ordem e deixar a tela mostrando uma
página/fila desatualizada por engano. Adicionado o mesmo guarda simples
já usado nos painéis públicos (ver v2.20.0): enquanto uma chamada ainda
está em voo, a próxima é pulada.

Cobertura de testes: 4 testes novos de concorrência com threads reais
(`tests/test_concorrencia.py`) + um smoke test manual simulando as 31
conexões simultâneas (24 recrutadores + 6 emissores + 1 painel de TV)
via requisições HTTP reais contra a aplicação Flask — 0 erros, 0
duplicatas, 0 senhas "vazando" entre empresas, ~0,15s de duração total.
Suíte completa (139 testes) confirmada após a correção.

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
