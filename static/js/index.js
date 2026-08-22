/**
 * index.js
 * ========
 * Lógica da tela principal do SIGS: emissão de senhas, chamada da
 * próxima senha (FIFO), repetição de chamada, abertura do painel em
 * nova aba, teste de bip, navegação para Configurações/Relatórios,
 * reinício do contador e atualização periódica da fila de espera.
 *
 * Todo o código roda em modo estrito e é organizado em funções
 * pequenas e nomeadas, sem qualquer JavaScript inline no HTML.
 */

"use strict";

// -----------------------------------------------------------------------
// Referências de elementos DOM
// -----------------------------------------------------------------------

const elementoSenhaDestaque = document.getElementById("senha-atual-destaque");
const elementoSenhaInfo = document.getElementById("senha-atual-info");
const elementoFilaCorpo = document.getElementById("fila-corpo");
const elementoFilaTotal = document.getElementById("fila-total");
const elementoFilaBusca = document.getElementById("busca-fila");
const elementoFilaPaginaInfo = document.getElementById("fila-pagina-info");
const elementoFilaBotaoAnterior = document.getElementById("btn-fila-anterior");
const elementoFilaBotaoProxima = document.getElementById("btn-fila-proxima");
const elementoNotificacoes = document.getElementById("area-notificacoes");
const elementoModalImpressao = document.getElementById("modal-impressao");
const elementoModalEmpresaSelect = document.getElementById("modal-empresa-select");
const elementoModalEmpresaAviso = document.getElementById("modal-empresa-aviso");
const elementoModalImpressoraSelect = document.getElementById("modal-impressora-select");
const elementoModalImpressoraAviso = document.getElementById("modal-impressora-aviso");
const elementoModalNomePessoa = document.getElementById("modal-nome-pessoa");

const TEMPO_ATUALIZACAO_MS = (window.SIGS_CONFIG && window.SIGS_CONFIG.tempoAtualizacaoMs) || 2000;

// Estado da busca/paginação da Fila de Espera (ver atualizarFila) — fica
// fora de qualquer função para sobreviver entre as atualizações
// automáticas por polling (setInterval), senão a página do usuário
// voltaria para a 1 a cada atualização.
let filaBuscaAtual = "";
let filaPaginaAtual = 1;
let filaTimeoutBusca = null;

// -----------------------------------------------------------------------
// Utilitários de interface
// -----------------------------------------------------------------------

/**
 * Exibe uma notificação temporária (toast) no canto da tela.
 * @param {string} mensagem - Texto a ser exibido.
 * @param {"sucesso"|"erro"|"info"} tipo - Tipo visual da notificação.
 */
function exibirNotificacao(mensagem, tipo = "info") {
    const notificacao = document.createElement("div");
    notificacao.className = `notificacao ${tipo}`;
    notificacao.textContent = mensagem;
    elementoNotificacoes.appendChild(notificacao);

    setTimeout(() => {
        notificacao.remove();
    }, 4500);
}

/**
 * Executa uma requisição à API do SIGS, tratando erros de rede e de
 * aplicação de forma padronizada.
 * @param {string} url
 * @param {Object} opcoes - Opções do fetch (method, headers, body...).
 */
async function chamarApi(url, opcoes = {}) {
    const resposta = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        ...opcoes,
    });

    const dados = await resposta.json().catch(() => ({}));

    // Sessão expirada ou usuário desativado: redireciona para o login em
    // vez de apenas exibir um erro, já que nenhuma ação faria sentido.
    if (resposta.status === 401) {
        window.location.href = "/login";
        throw new Error("Sessão expirada. Redirecionando para o login...");
    }

    if (!resposta.ok || dados.sucesso === false) {
        const mensagemErro = dados.erro || `Erro inesperado (HTTP ${resposta.status}).`;
        throw new Error(mensagemErro);
    }

    return dados;
}

/**
 * Vincula um evento de clique a um elemento apenas se ele existir na
 * página. Necessário porque os botões restritos a administradores (ex.:
 * Configurações, Relatórios, Reiniciar Contador) não são renderizados no
 * HTML para usuários com perfil "atendente" (ver index.html).
 */
function vincularClique(idElemento, manipulador) {
    const elemento = document.getElementById(idElemento);
    if (elemento) {
        elemento.addEventListener("click", manipulador);
    }
}

// -----------------------------------------------------------------------
// Ações dos botões principais
// -----------------------------------------------------------------------

/**
 * Emite uma nova senha e atualiza a fila em seguida. Guichê e atendente
 * são resolvidos no servidor a partir da sessão de login (ver
 * app.py:api_emitir) — não são mais informados manualmente aqui.
 *
 * @param {string} empresaId - Id da empresa selecionada (obrigatório —
 *   o servidor rejeita a emissão se este campo estiver ausente/inválido).
 * @param {string} nomeImpressora - Impressora local escolhida na janela
 *   de emissão (ver abrirModalImpressao), sempre uma das listadas ao
 *   vivo via /api/impressoras nesta máquina.
 * @param {string} nomePessoa - "Primeiro Nome" OPCIONAL digitado na
 *   janela de emissão; pode vir vazio, o servidor simplesmente não
 *   imprime a linha "Nome:" nesse caso.
 */
async function emitirSenha(empresaId = "", nomeImpressora = "", nomePessoa = "") {
    try {
        const dados = await chamarApi("/api/emitir", {
            method: "POST",
            body: JSON.stringify({
                empresa_id: empresaId,
                impressora: nomeImpressora,
                nome_pessoa: nomePessoa,
            }),
        });

        const numero = String(dados.senha.numero).padStart(3, "0");
        exibirNotificacao(`Senha ${numero} emitida e enviada para impressão.`, "sucesso");

        if (dados.aviso_impressao) {
            exibirNotificacao(`Aviso de impressão: ${dados.aviso_impressao}`, "erro");
        }

        await atualizarFila();
    } catch (erro) {
        exibirNotificacao(`Erro ao emitir senha: ${erro.message}`, "erro");
    }
}

/**
 * Abre a janela (modal) de escolha de empresa/impressora, exibida sempre
 * que o usuário clica em "Emitir Senha". Busca a lista de empresas
 * ATIVAS via /api/empresas e a lista de impressoras instaladas NESTA
 * máquina via /api/impressoras (ambas obrigatórias), populando os
 * respectivos seletores.
 */
async function abrirModalImpressao() {
    if (!elementoModalImpressao) {
        // Segurança: se o modal não existir no HTML (perfil sem permissão
        // de emissão), não há como escolher a empresa — não emite.
        return;
    }

    // Limpa o "Primeiro Nome" de uma abertura anterior do modal — sem
    // isso, o nome da última pessoa emitida ficaria sugerido (e fácil de
    // esquecer de trocar) na próxima emissão.
    if (elementoModalNomePessoa) {
        elementoModalNomePessoa.value = "";
    }

    await Promise.all([carregarEmpresasNoModal(), carregarImpressorasNoModal()]);

    elementoModalImpressao.classList.remove("modal-oculto");
}

/**
 * Busca as impressoras instaladas NESTA máquina via /api/impressoras
 * (win32print.EnumPrinters no servidor — nunca um nome digitado à mão,
 * evitando o erro clássico de "StartDoc failed" por um nome configurado
 * que não bate exatamente com o nome real da impressora no Windows) e
 * popula o seletor de impressora do modal de emissão.
 *
 * Se a impressora configurada em Configurações (``window.SIGS_CONFIG.
 * impressoraPadrao``) estiver entre as listadas, ela já vem
 * pré-selecionada — o Emissor só precisa confirmar. A seleção é
 * obrigatória: se não houver nenhuma impressora instalada, exibe um
 * aviso e desabilita o seletor.
 */
async function carregarImpressorasNoModal() {
    if (!elementoModalImpressoraSelect) {
        return;
    }

    elementoModalImpressoraSelect.innerHTML = '<option value="" disabled selected>Selecione a impressora...</option>';
    elementoModalImpressoraAviso.textContent = "";

    try {
        const dados = await chamarApi("/api/impressoras");
        const impressoras = dados.impressoras || [];
        const impressoraPadrao = (window.SIGS_CONFIG && window.SIGS_CONFIG.impressoraPadrao) || "";

        impressoras.forEach((nomeImpressora) => {
            const opcao = document.createElement("option");
            opcao.value = nomeImpressora;
            opcao.textContent = nomeImpressora;
            if (nomeImpressora === impressoraPadrao) {
                opcao.selected = true;
            }
            elementoModalImpressoraSelect.appendChild(opcao);
        });

        if (impressoras.length === 0) {
            elementoModalImpressoraAviso.textContent =
                "Nenhuma impressora encontrada nesta máquina. Verifique se há uma impressora instalada no Windows.";
            elementoModalImpressoraSelect.disabled = true;
        } else {
            elementoModalImpressoraSelect.disabled = false;
        }
    } catch (erro) {
        elementoModalImpressoraAviso.textContent = `Não foi possível listar as impressoras: ${erro.message}`;
        elementoModalImpressoraSelect.disabled = true;
    }
}

/**
 * Busca as empresas ATIVAS via /api/empresas e popula o seletor de
 * empresa do modal de emissão. Se não houver nenhuma empresa cadastrada
 * (ou todas estiverem inativas), exibe um aviso orientando a procurar um
 * administrador, já que a seleção de empresa é obrigatória para emitir.
 */
async function carregarEmpresasNoModal() {
    if (!elementoModalEmpresaSelect) {
        return;
    }

    elementoModalEmpresaSelect.innerHTML = '<option value="" disabled selected>Selecione a empresa...</option>';
    elementoModalEmpresaAviso.textContent = "";

    try {
        const dados = await chamarApi("/api/empresas");
        const empresas = dados.empresas || [];

        empresas.forEach((empresa) => {
            const opcao = document.createElement("option");
            opcao.value = empresa.id;
            opcao.textContent = empresa.nome;
            elementoModalEmpresaSelect.appendChild(opcao);
        });

        if (empresas.length === 0) {
            elementoModalEmpresaAviso.textContent =
                "Nenhuma empresa cadastrada. Peça a um administrador para cadastrar em Administração > Empresas.";
            elementoModalEmpresaSelect.disabled = true;
        } else {
            elementoModalEmpresaSelect.disabled = false;
        }
    } catch (erro) {
        elementoModalEmpresaAviso.textContent = `Não foi possível carregar as empresas: ${erro.message}`;
        elementoModalEmpresaSelect.disabled = true;
    }
}

/** Fecha a janela de escolha de impressora sem emitir nenhuma senha. */
function fecharModalImpressao() {
    if (elementoModalImpressao) {
        elementoModalImpressao.classList.add("modal-oculto");
    }
}

/**
 * Confirma a empresa e a impressora escolhidas na janela e emite a
 * senha, junto com o "Primeiro Nome" (opcional). Empresa e impressora
 * são obrigatórias: se alguma não estiver selecionada, a janela
 * permanece aberta e um aviso é exibido, sem chamar a API.
 */
async function confirmarImpressaoEEmitir() {
    const empresaId = elementoModalEmpresaSelect ? elementoModalEmpresaSelect.value : "";
    const nomeImpressora = elementoModalImpressoraSelect ? elementoModalImpressoraSelect.value : "";
    const nomePessoa = elementoModalNomePessoa ? elementoModalNomePessoa.value.trim() : "";

    if (!empresaId) {
        elementoModalEmpresaAviso.textContent = "Selecione a empresa antes de emitir a senha.";
        return;
    }

    if (!nomeImpressora) {
        elementoModalImpressoraAviso.textContent = "Selecione a impressora antes de emitir a senha.";
        return;
    }

    fecharModalImpressao();
    await emitirSenha(empresaId, nomeImpressora, nomePessoa);
}

/**
 * Chama a próxima senha da fila, respeitando a ordem FIFO. O guichê e o
 * atendente são sempre os da sessão logada no momento (o servidor rejeita
 * qualquer tentativa de sobrescrever esses dados pelo cliente).
 */
async function chamarProximaSenha() {
    try {
        const dados = await chamarApi("/api/chamar", { method: "POST", body: JSON.stringify({}) });

        atualizarDestaqueSenha(dados.chamada);
        exibirNotificacao(`Senha ${String(dados.chamada.numero).padStart(3, "0")} chamada.`, "sucesso");
        await atualizarFila();
    } catch (erro) {
        exibirNotificacao(erro.message, "erro");
    }
}

/** Repete a última chamada realizada (nova animação/bip no painel). */
async function repetirChamada() {
    try {
        const dados = await chamarApi("/api/repetir", { method: "POST" });
        atualizarDestaqueSenha(dados.chamada);
        exibirNotificacao(`Chamada da senha ${String(dados.chamada.numero).padStart(3, "0")} repetida.`, "sucesso");
    } catch (erro) {
        exibirNotificacao(erro.message, "erro");
    }
}

/**
 * Finaliza o atendimento em andamento no guichê do usuário logado e já
 * chama automaticamente a próxima senha da fila. Se não houver mais
 * senhas aguardando, exibe um aviso informativo (não um erro) pedindo
 * para aguardar a emissão de uma nova senha.
 */
async function finalizarAtendimento() {
    try {
        const dados = await chamarApi("/api/finalizar-atendimento", { method: "POST", body: JSON.stringify({}) });

        if (dados.senha_finalizada) {
            exibirNotificacao(
                `Senha ${String(dados.senha_finalizada.numero).padStart(3, "0")} finalizada.`,
                "sucesso"
            );
        }

        if (dados.chamada) {
            atualizarDestaqueSenha(dados.chamada);
            exibirNotificacao(
                `Chamando a próxima: senha ${String(dados.chamada.numero).padStart(3, "0")}.`,
                "sucesso"
            );
        } else {
            // Fila vazia: não é um erro, apenas uma situação de espera.
            elementoSenhaDestaque.textContent = "--";
            elementoSenhaInfo.textContent = "Aguardando nova senha ser emitida.";
            exibirNotificacao(dados.aviso || "Aguardando nova senha ser emitida.", "info");
        }

        await atualizarFila();
    } catch (erro) {
        exibirNotificacao(erro.message, "erro");
    }
}

/**
 * Bloqueia a emissão de novas senhas da empresa do recrutador logado
 * (ver app.py:api_bloquear_emissao). Diferente do antigo "Finalizar
 * Atendimento do Dia": não cancela as senhas que já estavam esperando, e
 * chamar/atender a fila continua funcionando normalmente — o único
 * efeito é impedir o Emissor de criar NOVAS senhas para esta empresa.
 * Pode ser desfeito a qualquer momento pelo próprio recrutador (ver
 * reativarEmissao) ou por um administrador, por isso a confirmação é
 * mais simples que a de uma ação irreversível.
 */
async function bloquearEmissao() {
    const confirmado = window.confirm(
        "Bloquear a emissão de novas senhas desta empresa?\n\n" +
        "A partir de agora, o Emissor não conseguirá mais emitir senhas para esta " +
        "empresa. As senhas que já estão na fila continuam sendo chamadas e atendidas " +
        "normalmente. Você (ou um administrador) pode reativar a emissão a qualquer momento."
    );
    if (!confirmado) {
        return;
    }

    try {
        const dados = await chamarApi("/api/bloquear-emissao", { method: "POST" });
        exibirNotificacao(dados.mensagem || "Emissão de senhas bloqueada.", "sucesso");
    } catch (erro) {
        exibirNotificacao(erro.message, "erro");
    } finally {
        // Recarrega a página para refletir o novo estado (aviso e botão
        // "Reativar Emissão de Senhas") vindo do servidor.
        window.location.reload();
    }
}

/**
 * Reativa a emissão de senhas da empresa do recrutador logado (ver
 * app.py:api_reativar_emissao) — autoatendimento, sem precisar de um
 * administrador.
 */
async function reativarEmissao() {
    try {
        const dados = await chamarApi("/api/reativar-emissao", { method: "POST" });
        exibirNotificacao(dados.mensagem || "Emissão de senhas reativada.", "sucesso");
    } catch (erro) {
        exibirNotificacao(erro.message, "erro");
    } finally {
        window.location.reload();
    }
}

/**
 * Abre o painel público em uma nova aba/janela. Para o perfil "recrutador",
 * abre o painel DA SUA EMPRESA (window.SIGS_CONFIG.painelUrl, calculado no
 * servidor — ver index.html); para os demais perfis, abre o painel geral.
 */
function abrirPainel() {
    const url = (window.SIGS_CONFIG && window.SIGS_CONFIG.painelUrl) || "/painel";
    window.open(url, "_blank");
}

/** Abre o painel-resumo público (todas as empresas) em uma nova aba/janela. */
function abrirPainelGeral() {
    window.open("/painel/geral", "_blank");
}

/** Reinicia o contador de numeração de senhas, mediante confirmação. */
async function reiniciarContador() {
    const confirmado = window.confirm(
        "Tem certeza que deseja reiniciar o contador de senhas? A próxima senha emitida voltará a ser 001."
    );
    if (!confirmado) {
        return;
    }

    try {
        await chamarApi("/api/reiniciar", { method: "POST" });
        exibirNotificacao("Contador de senhas reiniciado.", "sucesso");
    } catch (erro) {
        exibirNotificacao(erro.message, "erro");
    }
}

/** Atualiza o destaque visual da última senha chamada na tela principal. */
function atualizarDestaqueSenha(chamada) {
    if (!chamada) {
        return;
    }
    elementoSenhaDestaque.textContent = String(chamada.numero).padStart(3, "0");
    // Para o atendente (fila GERAL, com senhas de várias empresas
    // misturadas), mostrar a empresa aqui é essencial — sem isso não há
    // como saber para qual empresa é a senha que acabou de ser chamada.
    // Para o recrutador (fila já restrita à própria empresa), o dado é
    // redundante mas inofensivo.
    const empresaTexto = chamada.empresa ? ` — ${chamada.empresa}` : "";
    elementoSenhaInfo.textContent = `${chamada.guiche} — ${chamada.usuario}${empresaTexto} (${chamada.data_hora})`;
}

/**
 * Busca a chamada atual já ao abrir a tela (mesmo endpoint usado pelo
 * painel público correspondente — ver window.SIGS_CONFIG.statusUrl,
 * calculado no servidor em index.html) e popula a caixa "Última Senha
 * Chamada" imediatamente, ANTES de qualquer clique em Chamar/Repetir/
 * Finalizar nesta aba.
 *
 * Sem isso, a caixa ficava com o texto estático "Nenhuma chamada
 * realizada." (definido no HTML) mesmo quando já existia uma chamada em
 * andamento — por exemplo, logo depois de um F5 na página, ou ao logar
 * novamente após a sessão expirar — só voltando a refletir a realidade
 * depois da primeira ação manual. "Repetir Chamada" sempre funcionou
 * corretamente (ver repetirChamada), mas o estado inicial da tela não
 * refletia a última chamada já existente.
 */
async function carregarChamadaAtualInicial() {
    const url = window.SIGS_CONFIG && window.SIGS_CONFIG.statusUrl;
    if (!url || !elementoSenhaDestaque) {
        return;
    }

    try {
        const dados = await chamarApi(url);
        atualizarDestaqueSenha(dados.chamada_atual);
    } catch (erro) {
        // Falha aqui não é crítica (a caixa simplesmente permanece com o
        // texto padrão) — não interrompe o carregamento do resto da tela.
        console.error("Não foi possível carregar a chamada atual:", erro);
    }
}

// -----------------------------------------------------------------------
// Fila de espera
// -----------------------------------------------------------------------

/**
 * Busca e renderiza a fila de senhas aguardando chamada, respeitando a
 * busca e a página atualmente selecionadas (ver ``filaBuscaAtual``/
 * ``filaPaginaAtual``) — assim, tanto o polling automático (setInterval)
 * quanto uma ação do usuário (digitar na busca, trocar de página) usam
 * exatamente o mesmo caminho, sem duplicar lógica.
 */
async function atualizarFila() {
    try {
        const parametros = new URLSearchParams();
        if (filaBuscaAtual) {
            parametros.set("busca", filaBuscaAtual);
        }
        parametros.set("pagina", String(filaPaginaAtual));

        const dados = await chamarApi(`/api/fila?${parametros.toString()}`);
        // O servidor pode ter "corrigido" a página (ex.: busca reduziu o
        // total de páginas e a atual ficou fora do intervalo) — mantém o
        // estado local sincronizado com o que realmente foi retornado.
        filaPaginaAtual = dados.pagina_atual || 1;
        renderizarFila(dados);
    } catch (erro) {
        console.error("Erro ao atualizar fila:", erro);
    }
}

/** Renderiza a tabela HTML da fila de espera e os controles de paginação. */
function renderizarFila(dados) {
    const fila = dados.fila || [];
    elementoFilaTotal.textContent = dados.total_aguardando || 0;

    if (elementoFilaPaginaInfo) {
        const totalPaginas = dados.total_paginas || 1;
        const paginaAtual = dados.pagina_atual || 1;
        const totalFiltrado = dados.total_filtrado ?? dados.total_aguardando ?? 0;
        elementoFilaPaginaInfo.textContent = `Página ${paginaAtual} de ${totalPaginas} (${totalFiltrado} resultado${totalFiltrado === 1 ? "" : "s"})`;
        if (elementoFilaBotaoAnterior) {
            elementoFilaBotaoAnterior.disabled = paginaAtual <= 1;
        }
        if (elementoFilaBotaoProxima) {
            elementoFilaBotaoProxima.disabled = paginaAtual >= totalPaginas;
        }
    }

    if (!fila || fila.length === 0) {
        const mensagem = filaBuscaAtual ? "Nenhuma senha encontrada para esta busca." : "Nenhuma senha aguardando.";
        elementoFilaCorpo.innerHTML = `<tr><td colspan="5">${mensagem}</td></tr>`;
        return;
    }

    elementoFilaCorpo.innerHTML = "";
    fila.forEach((senha) => {
        const linha = document.createElement("tr");

        const celulaNumero = document.createElement("td");
        celulaNumero.textContent = String(senha.numero).padStart(3, "0");

        const celulaNome = document.createElement("td");
        celulaNome.textContent = senha.nome_pessoa || "—";

        const celulaEmpresa = document.createElement("td");
        celulaEmpresa.textContent = senha.empresa || "—";

        const celulaData = document.createElement("td");
        celulaData.textContent = senha.data_hora;

        const celulaAcoes = document.createElement("td");

        // "Reimprimir" só para o perfil Emissor (ver window.SIGS_CONFIG,
        // definido em index.html). A Fila de Espera só lista senhas com
        // status 'Emitida' (ver database.listar_fila_atual) — uma senha
        // já chamada, finalizada ou cancelada simplesmente some desta
        // lista, então toda linha aqui já é elegível para reimpressão; a
        // validação de status é repetida no servidor mesmo assim, para
        // cobrir o caso de a lista estar desatualizada no instante do
        // clique (ver app.py:api_reimprimir).
        if (window.SIGS_CONFIG && window.SIGS_CONFIG.perfilUsuario === "emissor") {
            const botaoReimprimir = document.createElement("button");
            botaoReimprimir.className = "botao botao-secundario botao-acao-pequeno";
            botaoReimprimir.textContent = "🖨️ Reimprimir";
            botaoReimprimir.addEventListener("click", () => reimprimirSenha(senha.id, senha.numero));
            celulaAcoes.appendChild(botaoReimprimir);
        }

        const botaoCancelar = document.createElement("button");
        botaoCancelar.className = "botao botao-alerta botao-acao-pequeno";
        botaoCancelar.textContent = "Cancelar";
        botaoCancelar.addEventListener("click", () => cancelarSenha(senha.id));

        celulaAcoes.appendChild(botaoCancelar);

        linha.appendChild(celulaNumero);
        linha.appendChild(celulaNome);
        linha.appendChild(celulaEmpresa);
        linha.appendChild(celulaData);
        linha.appendChild(celulaAcoes);

        elementoFilaCorpo.appendChild(linha);
    });
}

/**
 * Reimprime o ticket de uma senha que ainda está aguardando na fila
 * (segunda via, marcada com "REIMPRESSO" no papel — ver
 * app.py:api_reimprimir). Só disponível para o perfil Emissor (ver
 * renderizarFila).
 */
async function reimprimirSenha(senhaId, numeroSenha) {
    const numeroFormatado = String(numeroSenha).padStart(3, "0");
    const confirmado = window.confirm(`Reimprimir a senha ${numeroFormatado}?`);
    if (!confirmado) {
        return;
    }

    try {
        await chamarApi(`/api/senha/${senhaId}/reimprimir`, { method: "POST" });
        exibirNotificacao(`Senha ${numeroFormatado} reimpressa.`, "sucesso");
    } catch (erro) {
        exibirNotificacao(`Erro ao reimprimir: ${erro.message}`, "erro");
    }
}

/** Cancela uma senha específica da fila. */
async function cancelarSenha(senhaId) {
    const confirmado = window.confirm(`Cancelar a senha #${senhaId}?`);
    if (!confirmado) {
        return;
    }

    try {
        await chamarApi(`/api/senha/${senhaId}/cancelar`, { method: "POST" });
        exibirNotificacao("Senha cancelada.", "sucesso");
        await atualizarFila();
    } catch (erro) {
        exibirNotificacao(erro.message, "erro");
    }
}

/**
 * Reage à digitação no campo de busca da Fila de Espera, com debounce
 * (aguarda uma pausa de 350ms na digitação antes de consultar o
 * servidor) para não disparar uma requisição a cada tecla. Sempre volta
 * para a página 1, já que o conjunto de resultados muda a cada letra
 * digitada.
 */
function aoDigitarBuscaFila() {
    filaBuscaAtual = elementoFilaBusca.value.trim();
    filaPaginaAtual = 1;

    if (filaTimeoutBusca) {
        clearTimeout(filaTimeoutBusca);
    }
    filaTimeoutBusca = setTimeout(atualizarFila, 350);
}

/** Vai para a página anterior da Fila de Espera, se houver. */
function irParaPaginaAnteriorFila() {
    if (filaPaginaAtual > 1) {
        filaPaginaAtual -= 1;
        atualizarFila();
    }
}

/** Vai para a próxima página da Fila de Espera, se houver. */
function irParaProximaPaginaFila() {
    filaPaginaAtual += 1;
    atualizarFila();
}

// -----------------------------------------------------------------------
// Inicialização e vínculo de eventos
// -----------------------------------------------------------------------

function inicializar() {
    // Botões disponíveis para qualquer usuário logado (atendente ou admin).
    // "Emitir Senha" abre primeiro a janela de escolha de impressora — a
    // emissão de fato só ocorre quando o usuário confirma nessa janela.
    vincularClique("btn-emitir", abrirModalImpressao);
    vincularClique("btn-confirmar-impressao", confirmarImpressaoEEmitir);
    vincularClique("btn-cancelar-impressao", fecharModalImpressao);
    vincularClique("btn-chamar", chamarProximaSenha);
    vincularClique("btn-repetir", repetirChamada);
    vincularClique("btn-finalizar", finalizarAtendimento);
    vincularClique("btn-bloquear-emissao", bloquearEmissao);
    vincularClique("btn-reativar-emissao", reativarEmissao);
    vincularClique("btn-abrir-painel", abrirPainel);
    vincularClique("btn-abrir-painel-geral", abrirPainelGeral);
    vincularClique("btn-testar-bip", tocarBip);

    // Botões restritos a administradores. Podem não existir no DOM para
    // usuários com perfil "atendente" (o Jinja simplesmente não os
    // renderiza), por isso o uso de vincularClique (que verifica a
    // existência do elemento antes de anexar o evento).
    vincularClique("btn-configuracoes", () => { window.location.href = "/configuracoes"; });
    vincularClique("btn-relatorios", () => { window.location.href = "/relatorios"; });
    vincularClique("btn-usuarios", () => { window.location.href = "/admin/usuarios"; });
    vincularClique("btn-empresas", () => { window.location.href = "/admin/empresas"; });
    vincularClique("btn-reiniciar", reiniciarContador);

    // Busca e paginação da Fila de Espera — disponíveis para todos os
    // perfis (ver templates/index.html).
    vincularClique("btn-fila-anterior", irParaPaginaAnteriorFila);
    vincularClique("btn-fila-proxima", irParaProximaPaginaFila);
    if (elementoFilaBusca) {
        elementoFilaBusca.addEventListener("input", aoDigitarBuscaFila);
    }

    carregarChamadaAtualInicial();
    atualizarFila();
    setInterval(atualizarFila, TEMPO_ATUALIZACAO_MS);
}

document.addEventListener("DOMContentLoaded", inicializar);
