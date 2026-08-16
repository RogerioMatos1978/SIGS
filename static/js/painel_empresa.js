/**
 * painel_empresa.js
 * ==================
 * Lógica do painel público de UMA empresa do feirão do emprego. É uma
 * cópia quase idêntica de painel.js, mudando apenas o endpoint consultado
 * (/api/painel/empresa/<id>/status em vez de /api/painel/status) — o id da
 * empresa vem de window.SIGS_CONFIG.empresaId, injetado pelo servidor em
 * painel_empresa.html. Mantido como arquivo separado (em vez de
 * parametrizar painel.js) para que cada painel continue simples de ler
 * isoladamente, seguindo o padrão já usado no restante do projeto.
 */

"use strict";

const elementoChamadaAtual = document.getElementById("painel-chamada-atual");
const elementoNumeroAtual = document.getElementById("painel-numero-atual");
const elementoGuicheAtual = document.getElementById("painel-guiche-atual");
const elementoListaEmitidas = document.getElementById("painel-lista-emitidas");
const elementoData = document.getElementById("painel-data");
const elementoHora = document.getElementById("painel-hora");

const TEMPO_ATUALIZACAO_MS = (window.SIGS_CONFIG && window.SIGS_CONFIG.tempoAtualizacaoMs) || 2000;
const EMPRESA_ID = window.SIGS_CONFIG && window.SIGS_CONFIG.empresaId;

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
 * Consulta o status atual da fila desta empresa (chamada em destaque,
 * últimas emitidas, data/hora do servidor) e atualiza a interface.
 */
async function atualizarPainel() {
    try {
        const resposta = await fetch(`/api/painel/empresa/${EMPRESA_ID}/status`);
        const dados = await resposta.json();

        if (!dados.sucesso) {
            console.error("Erro ao consultar status do painel da empresa:", dados.erro);
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
        elementoGuicheAtual.textContent = "Aguardando primeira chamada";
        return;
    }

    elementoNumeroAtual.textContent = String(chamada.numero).padStart(3, "0");
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

/** Dispara a animação de pulso e o bip sonoro no painel. */
function dispararAnimacaoEChamada() {
    elementoChamadaAtual.classList.remove("animar");
    void elementoChamadaAtual.offsetWidth;
    elementoChamadaAtual.classList.add("animar");

    tocarBip();
}

/** Atualiza a lista das últimas senhas emitidas desta empresa. */
function atualizarListaEmitidas(lista) {
    elementoListaEmitidas.innerHTML = "";

    if (!lista || lista.length === 0) {
        elementoListaEmitidas.innerHTML = "<li>Nenhuma senha emitida ainda para esta empresa.</li>";
        return;
    }

    lista.forEach((senha) => {
        const item = document.createElement("li");

        const numero = document.createElement("span");
        numero.textContent = `Senha ${String(senha.numero).padStart(3, "0")}`;

        const status = document.createElement("span");
        status.className = "status-badge";
        status.textContent = ROTULOS_STATUS[senha.status] || senha.status;

        item.appendChild(numero);
        item.appendChild(status);
        elementoListaEmitidas.appendChild(item);
    });
}

/** Atualiza o relógio local a cada segundo, entre as chamadas ao servidor. */
function atualizarRelogioLocal() {
    const agora = new Date();
    elementoHora.textContent = agora.toLocaleTimeString("pt-BR");
}

function inicializar() {
    if (!EMPRESA_ID) {
        console.error("Painel da empresa carregado sem um id de empresa válido.");
        return;
    }
    atualizarPainel();
    setInterval(atualizarPainel, TEMPO_ATUALIZACAO_MS);
    setInterval(atualizarRelogioLocal, 1000);
}

document.addEventListener("DOMContentLoaded", inicializar);
