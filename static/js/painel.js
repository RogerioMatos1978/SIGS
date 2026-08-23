/**
 * painel.js
 * =========
 * Lógica do painel público de chamadas do SIGS. Consulta periodicamente
 * (via Fetch API) o endpoint /api/painel/status e atualiza somente os
 * dados na tela (nunca recarrega a página inteira). Dispara animação e
 * bip sonoro sempre que detecta uma nova chamada (ou repetição de
 * chamada), identificada pelo id do evento de chamada.
 */

"use strict";

const elementoChamadaAtual = document.getElementById("painel-chamada-atual");
const elementoNumeroAtual = document.getElementById("painel-numero-atual");
const elementoEmpresaAtual = document.getElementById("painel-empresa-atual");
const elementoGuicheAtual = document.getElementById("painel-guiche-atual");
const elementoListaEmitidas = document.getElementById("painel-lista-emitidas");
const elementoData = document.getElementById("painel-data");
const elementoHora = document.getElementById("painel-hora");

const TEMPO_ATUALIZACAO_MS = (window.SIGS_CONFIG && window.SIGS_CONFIG.tempoAtualizacaoMs) || 2000;

// Guarda o id do último evento de chamada já anunciado neste painel, para
// detectar mudanças (nova chamada OU repetição) e disparar bip/animação
// apenas quando necessário.
let ultimoEventoAnunciadoId = null;

// Rótulos amigáveis para o status de cada senha no histórico.
const ROTULOS_STATUS = {
    Emitida: "Aguardando",
    Chamada: "Chamada",
    Finalizada: "Finalizada",
    Cancelada: "Cancelada",
};

/**
 * Consulta o status atual do sistema (chamada em destaque, últimas
 * emitidas, data/hora do servidor) e atualiza a interface.
 */
async function atualizarPainel() {
    try {
        const resposta = await fetch("/api/painel/status");
        const dados = await resposta.json();

        if (!dados.sucesso) {
            console.error("Erro ao consultar status do painel:", dados.erro);
            return;
        }

        elementoData.textContent = dados.data;
        elementoHora.textContent = dados.hora;

        atualizarChamadaAtual(dados.chamada_atual);
        atualizarListaEmitidas(dados.ultimas_emitidas);
    } catch (erro) {
        console.error("Falha de comunicação com o servidor:", erro);
    }
}

/**
 * Atualiza a senha em destaque no painel. Quando o id do evento de
 * chamada muda em relação ao último anunciado, dispara a animação visual
 * e o bip sonoro — mesmo que o número da senha seja o mesmo (repetição).
 */
function atualizarChamadaAtual(chamada) {
    if (!chamada) {
        elementoNumeroAtual.textContent = "---";
        elementoEmpresaAtual.textContent = "";
        elementoGuicheAtual.textContent = "Aguardando primeira chamada";
        return;
    }

    const numeros = formatarNumerosChamada(chamada);
    elementoNumeroAtual.textContent = numeros;
    // Classe usada pelo CSS para reduzir a fonte quando várias senhas
    // aparecem juntas (ver "Chamar Selecionadas" em index.js), já que o
    // texto fica bem mais longo que um número isolado de 3 dígitos.
    elementoNumeroAtual.classList.toggle("painel-numero--sequencia", chamada.senhas && chamada.senhas.length > 1);
    // Fica vazio (e some via CSS ":empty") para senhas emitidas antes da
    // funcionalidade de empresas existir, que não têm esse dado.
    elementoEmpresaAtual.textContent = chamada.empresa || "";
    elementoGuicheAtual.textContent = `${chamada.guiche} — ${chamada.usuario}`;

    const eventoMudou = ultimoEventoAnunciadoId !== null && ultimoEventoAnunciadoId !== chamada.id;
    const primeiraCarga = ultimoEventoAnunciadoId === null;

    if (eventoMudou || primeiraCarga) {
        // Evita tocar o bip logo no primeiro carregamento da página (não
        // faz sentido anunciar uma chamada "antiga" ao abrir o painel).
        if (!primeiraCarga) {
            dispararAnimacaoEChamada();
        }
        ultimoEventoAnunciadoId = chamada.id;
    }
}

/**
 * Formata o(s) número(s) da chamada atual em destaque. Quando o
 * recrutador chama várias senhas de uma vez ("Chamar Selecionadas" — ver
 * database.chamar_varias/obter_chamada_atual), ``chamada.senhas`` traz
 * TODOS os eventos do mesmo lote (em ordem de chamada) e o painel exibe
 * a sequência inteira separada por vírgula (ex.: "005, 006, 007"), em
 * vez de mostrar só o primeiro número. Chamadas individuais continuam
 * mostrando um único número, como sempre.
 */
function formatarNumerosChamada(chamada) {
    const lista = chamada.senhas && chamada.senhas.length > 0 ? chamada.senhas : [chamada];
    return lista.map((senha) => String(senha.numero).padStart(3, "0")).join(", ");
}

/** Dispara a animação de pulso e o bip sonoro no painel. */
function dispararAnimacaoEChamada() {
    elementoChamadaAtual.classList.remove("animar");
    // Força o navegador a recalcular o layout antes de reaplicar a classe,
    // garantindo que a animação CSS seja reiniciada mesmo em chamadas
    // consecutivas rápidas.
    void elementoChamadaAtual.offsetWidth;
    elementoChamadaAtual.classList.add("animar");

    tocarBip();
}

/**
 * Extrai apenas o "Mesa NN" (ou "Guichê NN") do texto completo gravado em
 * ``senha.guiche`` (ex.: "Mesa 01 — Empresa A") — descarta o nome da
 * empresa aqui porque ele já é exibido separadamente (ver
 * ``senha.empresa`` em ``atualizarListaEmitidas``), evitando repetição.
 * Retorna ``null`` se a senha ainda não foi chamada (``guiche`` só é
 * preenchido no momento da chamada — ver ``database.chamar_proxima``).
 */
function extrairMesa(guiche) {
    if (!guiche) {
        return null;
    }
    return guiche.split("—")[0].trim();
}

/** Atualiza a lista das últimas senhas emitidas exibida no painel. */
function atualizarListaEmitidas(lista) {
    elementoListaEmitidas.innerHTML = "";

    if (!lista || lista.length === 0) {
        elementoListaEmitidas.innerHTML = "<li>Nenhuma senha emitida ainda.</li>";
        return;
    }

    lista.forEach((senha) => {
        const item = document.createElement("li");

        const numero = document.createElement("span");
        numero.textContent = `Senha ${String(senha.numero).padStart(3, "0")}`;
        item.appendChild(numero);

        // Nome da empresa para a qual a senha foi emitida (ex.: "Comigo"),
        // sempre presente desde que a emissão exige a escolha de uma
        // empresa (ver modal-empresa-select em index.html).
        if (senha.empresa) {
            const empresa = document.createElement("span");
            empresa.className = "painel-guiche-info";
            empresa.textContent = senha.empresa;
            item.appendChild(empresa);
        }

        // Mesa/guichê que realizou a chamada (só existe depois que a senha
        // é efetivamente chamada — ver extrairMesa acima).
        const mesa = extrairMesa(senha.guiche);
        if (mesa) {
            const elementoMesa = document.createElement("span");
            elementoMesa.className = "painel-guiche-info";
            elementoMesa.textContent = mesa;
            item.appendChild(elementoMesa);
        }

        const status = document.createElement("span");
        status.className = "status-badge";
        status.textContent = ROTULOS_STATUS[senha.status] || senha.status;
        item.appendChild(status);

        elementoListaEmitidas.appendChild(item);
    });
}

/** Atualiza o relógio local a cada segundo, entre as chamadas ao servidor,
 * para uma exibição mais fluida (sem esperar o próximo polling). */
function atualizarRelogioLocal() {
    const agora = new Date();
    elementoHora.textContent = agora.toLocaleTimeString("pt-BR");
}

function inicializar() {
    atualizarPainel();
    setInterval(atualizarPainel, TEMPO_ATUALIZACAO_MS);
    setInterval(atualizarRelogioLocal, 1000);
}

document.addEventListener("DOMContentLoaded", inicializar);
