/**
 * painel_geral.js
 * ================
 * Lógica do painel público resumo do feirão inteiro: consulta
 * periodicamente /api/painel/geral/status e atualiza os totais
 * (aguardando, em atendimento, total em andamento) e a tabela detalhada
 * por empresa. Sem chamada/bip/animação — é um painel de leitura, não de
 * anúncio de senha.
 *
 * Os cards de topo ("Aguardando"/"Em Atendimento"/"Total em Andamento")
 * e a tabela "Por Empresa" propositalmente NÃO exibem "Atendidas"
 * (Finalizada) nem "Canceladas" — mesmo critério aplicado a todos os
 * painéis públicos (ver templates/painel_geral.html e
 * database.listar_ultimas_emitidas): refletem só a situação ATUAL da
 * fila. Já a seção "Resumo do Feirão" (``atualizarResumoFeirao``,
 * abaixo) mostra o TOTAL geral do evento — inclui as senhas já
 * finalizadas, de propósito, pois ali o objetivo é o resultado
 * acumulado, não a fila do momento.
 */

"use strict";

const elementoData = document.getElementById("painel-data");
const elementoHora = document.getElementById("painel-hora");
const elementoAguardando = document.getElementById("resumo-aguardando");
const elementoEmAtendimento = document.getElementById("resumo-em-atendimento");
const elementoTotal = document.getElementById("resumo-total");
const elementoEmpresasCorpo = document.getElementById("painel-resumo-empresas-corpo");
const elementoFeiraoTotalEmitidas = document.getElementById("feirao-total-emitidas");
const elementoFeiraoTotalAtendidas = document.getElementById("feirao-total-atendidas");
const elementoFeiraoTempoMedio = document.getElementById("feirao-tempo-medio");

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
        atualizarResumoFeirao(dados.resumo_feirao);
    } catch (erro) {
        console.error("Falha de comunicação com o servidor:", erro);
    }
}

/**
 * Atualiza os cartões de totais gerais — só "Aguardando", "Em
 * Atendimento" e "Total em Andamento" (soma dos dois). "Atendidas" e
 * "Canceladas" continuam vindo do servidor (``resumo.total_atendidas``/
 * ``resumo.total_canceladas``), mas propositalmente não são lidos aqui
 * (ver docstring do arquivo).
 */
function atualizarTotais(resumo) {
    elementoAguardando.textContent = resumo.total_aguardando;
    elementoEmAtendimento.textContent = resumo.total_em_atendimento;
    elementoTotal.textContent = resumo.total_aguardando + resumo.total_em_atendimento;
}

/**
 * Atualiza a tabela com o detalhamento por empresa — mesmo critério da
 * função acima: só Aguardando/Em Atendimento/Total (soma dos dois),
 * mesmo que ``linha.atendidas``/``linha.canceladas`` venham preenchidos
 * do servidor.
 */
function atualizarTabelaEmpresas(porEmpresa) {
    elementoEmpresasCorpo.innerHTML = "";

    if (!porEmpresa || porEmpresa.length === 0) {
        elementoEmpresasCorpo.innerHTML = "<tr><td colspan=\"4\">Nenhuma senha emitida ainda.</td></tr>";
        return;
    }

    porEmpresa.forEach((linha) => {
        const tr = document.createElement("tr");
        [
            linha.empresa,
            linha.aguardando,
            linha.em_atendimento,
            linha.aguardando + linha.em_atendimento,
        ].forEach((valor) => {
            const td = document.createElement("td");
            td.textContent = valor;
            tr.appendChild(td);
        });
        elementoEmpresasCorpo.appendChild(tr);
    });
}

/**
 * Atualiza a seção "Resumo do Feirão" — totais de TODO o evento (sem
 * filtro de período), diferente dos cards "em andamento" acima: aqui
 * entram também as senhas já finalizadas/canceladas, já que o objetivo
 * é mostrar o resultado geral do feirão, não a fila do momento (ver
 * app.py:api_painel_geral_status).
 */
function atualizarResumoFeirao(resumoFeirao) {
    if (!resumoFeirao) {
        return;
    }
    elementoFeiraoTotalEmitidas.textContent = resumoFeirao.total_emitidas;
    elementoFeiraoTotalAtendidas.textContent = resumoFeirao.total_atendidas;
    elementoFeiraoTempoMedio.textContent = resumoFeirao.tempo_medio.tempo_medio_formatado;
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
