/**
 * painel_geral.js
 * ================
 * Lógica do painel público resumo do feirão inteiro: consulta
 * periodicamente /api/painel/geral/status e atualiza os totais
 * (aguardando, em atendimento, atendidas, canceladas, total emitidas) e a
 * tabela detalhada por empresa. Sem chamada/bip/animação — é um painel de
 * leitura, não de anúncio de senha.
 */

"use strict";

const elementoData = document.getElementById("painel-data");
const elementoHora = document.getElementById("painel-hora");
const elementoAguardando = document.getElementById("resumo-aguardando");
const elementoEmAtendimento = document.getElementById("resumo-em-atendimento");
const elementoAtendidas = document.getElementById("resumo-atendidas");
const elementoCanceladas = document.getElementById("resumo-canceladas");
const elementoTotal = document.getElementById("resumo-total");
const elementoEmpresasCorpo = document.getElementById("painel-resumo-empresas-corpo");

const TEMPO_ATUALIZACAO_MS = (window.SIGS_CONFIG && window.SIGS_CONFIG.tempoAtualizacaoMs) || 2000;

/** Consulta o resumo geral e atualiza a interface. */
async function atualizarPainelGeral() {
    try {
        const resposta = await fetch("/api/painel/geral/status");
        const dados = await resposta.json();

        if (!dados.sucesso) {
            console.error("Erro ao consultar o painel geral:", dados.erro);
            return;
        }

        elementoData.textContent = dados.data;
        elementoHora.textContent = dados.hora;

        atualizarTotais(dados.resumo);
        atualizarTabelaEmpresas(dados.resumo.por_empresa);
    } catch (erro) {
        console.error("Falha de comunicação com o servidor:", erro);
    }
}

/** Atualiza os cinco cartões de totais gerais. */
function atualizarTotais(resumo) {
    elementoAguardando.textContent = resumo.total_aguardando;
    elementoEmAtendimento.textContent = resumo.total_em_atendimento;
    elementoAtendidas.textContent = resumo.total_atendidas;
    elementoCanceladas.textContent = resumo.total_canceladas;
    elementoTotal.textContent = resumo.total_emitidas;
}

/** Atualiza a tabela com o detalhamento por empresa. */
function atualizarTabelaEmpresas(porEmpresa) {
    elementoEmpresasCorpo.innerHTML = "";

    if (!porEmpresa || porEmpresa.length === 0) {
        elementoEmpresasCorpo.innerHTML = "<tr><td colspan=\"6\">Nenhuma senha emitida ainda.</td></tr>";
        return;
    }

    porEmpresa.forEach((linha) => {
        const tr = document.createElement("tr");
        [
            linha.empresa,
            linha.aguardando,
            linha.em_atendimento,
            linha.atendidas,
            linha.canceladas,
            linha.total,
        ].forEach((valor) => {
            const td = document.createElement("td");
            td.textContent = valor;
            tr.appendChild(td);
        });
        elementoEmpresasCorpo.appendChild(tr);
    });
}

/** Atualiza o relógio local a cada segundo, entre as chamadas ao servidor. */
function atualizarRelogioLocal() {
    const agora = new Date();
    elementoHora.textContent = agora.toLocaleTimeString("pt-BR");
}

function inicializar() {
    atualizarPainelGeral();
    setInterval(atualizarPainelGeral, TEMPO_ATUALIZACAO_MS);
    setInterval(atualizarRelogioLocal, 1000);
}

document.addEventListener("DOMContentLoaded", inicializar);
