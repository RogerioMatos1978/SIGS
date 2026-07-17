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
const elementoNotificacoes = document.getElementById("area-notificacoes");
const elementoModalImpressao = document.getElementById("modal-impressao");
const elementoModalImpressoraSelect = document.getElementById("modal-impressora-select");

const TEMPO_ATUALIZACAO_MS = (window.SIGS_CONFIG && window.SIGS_CONFIG.tempoAtualizacaoMs) || 2000;

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
 * @param {string} [nomeImpressora] - Impressora escolhida na janela de
 *   impressão (ver abrirModalImpressao). Se vazio/omitido, o servidor usa
 *   a impressora padrão configurada em Configurações.
 */
async function emitirSenha(nomeImpressora = "") {
    try {
        const dados = await chamarApi("/api/emitir", {
            method: "POST",
            body: JSON.stringify({ impressora: nomeImpressora }),
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
 * Abre a janela (modal) de escolha de impressora, exibida sempre que o
 * usuário clica em "Emitir Senha". Busca a lista de impressoras
 * instaladas no Windows via /api/impressoras e popula o seletor,
 * mantendo a opção "Impressora padrão do sistema" sempre disponível.
 */
async function abrirModalImpressao() {
    if (!elementoModalImpressao) {
        // Segurança: se o modal não existir no HTML (perfil sem permissão
        // de emissão), apenas emite direto com a impressora padrão.
        await emitirSenha();
        return;
    }

    // Reseta o seletor para apenas a opção padrão enquanto carrega a lista,
    // evitando mostrar impressoras de uma abertura anterior do modal.
    elementoModalImpressoraSelect.innerHTML = '<option value="">Impressora padrão do sistema</option>';

    try {
        const dados = await chamarApi("/api/impressoras");
        (dados.impressoras || []).forEach((nomeImpressora) => {
            const opcao = document.createElement("option");
            opcao.value = nomeImpressora;
            opcao.textContent = nomeImpressora;
            elementoModalImpressoraSelect.appendChild(opcao);
        });
    } catch (erro) {
        // Mesmo se a listagem falhar (ex.: ambiente sem pywin32), o modal
        // continua utilizável com a opção de impressora padrão.
        console.error("Não foi possível listar impressoras:", erro);
    }

    elementoModalImpressao.classList.remove("modal-oculto");
}

/** Fecha a janela de escolha de impressora sem emitir nenhuma senha. */
function fecharModalImpressao() {
    if (elementoModalImpressao) {
        elementoModalImpressao.classList.add("modal-oculto");
    }
}

/** Confirma a impressora escolhida na janela e emite a senha. */
async function confirmarImpressaoEEmitir() {
    const nomeImpressora = elementoModalImpressoraSelect ? elementoModalImpressoraSelect.value : "";
    fecharModalImpressao();
    await emitirSenha(nomeImpressora);
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

/** Abre o painel público em uma nova aba/janela. */
function abrirPainel() {
    window.open("/painel", "_blank");
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
    elementoSenhaInfo.textContent = `${chamada.guiche} — ${chamada.usuario} (${chamada.data_hora})`;
}

// -----------------------------------------------------------------------
// Fila de espera
// -----------------------------------------------------------------------

/** Busca e renderiza a fila de senhas aguardando chamada. */
async function atualizarFila() {
    try {
        const dados = await chamarApi("/api/fila");
        renderizarFila(dados.fila, dados.total_aguardando);
    } catch (erro) {
        console.error("Erro ao atualizar fila:", erro);
    }
}

/** Renderiza a tabela HTML da fila de espera. */
function renderizarFila(fila, total) {
    elementoFilaTotal.textContent = total;

    if (!fila || fila.length === 0) {
        elementoFilaCorpo.innerHTML = '<tr><td colspan="3">Nenhuma senha aguardando.</td></tr>';
        return;
    }

    elementoFilaCorpo.innerHTML = "";
    fila.forEach((senha) => {
        const linha = document.createElement("tr");

        const celulaNumero = document.createElement("td");
        celulaNumero.textContent = String(senha.numero).padStart(3, "0");

        const celulaData = document.createElement("td");
        celulaData.textContent = senha.data_hora;

        const celulaAcoes = document.createElement("td");

        const botaoCancelar = document.createElement("button");
        botaoCancelar.className = "botao botao-alerta botao-acao-pequeno";
        botaoCancelar.textContent = "Cancelar";
        botaoCancelar.addEventListener("click", () => cancelarSenha(senha.id));

        celulaAcoes.appendChild(botaoCancelar);

        linha.appendChild(celulaNumero);
        linha.appendChild(celulaData);
        linha.appendChild(celulaAcoes);

        elementoFilaCorpo.appendChild(linha);
    });
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
    vincularClique("btn-abrir-painel", abrirPainel);
    vincularClique("btn-testar-bip", tocarBip);

    // Botões restritos a administradores. Podem não existir no DOM para
    // usuários com perfil "atendente" (o Jinja simplesmente não os
    // renderiza), por isso o uso de vincularClique (que verifica a
    // existência do elemento antes de anexar o evento).
    vincularClique("btn-configuracoes", () => { window.location.href = "/configuracoes"; });
    vincularClique("btn-relatorios", () => { window.location.href = "/relatorios"; });
    vincularClique("btn-usuarios", () => { window.location.href = "/admin/usuarios"; });
    vincularClique("btn-reiniciar", reiniciarContador);

    atualizarFila();
    setInterval(atualizarFila, TEMPO_ATUALIZACAO_MS);
}

document.addEventListener("DOMContentLoaded", inicializar);
