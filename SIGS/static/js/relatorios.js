/**
 * relatorios.js
 * =============
 * Lógica da tela de Relatórios do SIGS: consulta o resumo estatístico
 * (senhas emitidas, chamadas realizadas, tempo médio de atendimento) e
 * dispara o download dos relatórios em CSV, Excel ou PDF, respeitando o
 * período e o tipo selecionados pelo usuário.
 */

"use strict";

const campoInicio = document.getElementById("filtro-inicio");
const campoFim = document.getElementById("filtro-fim");
const campoTipo = document.getElementById("filtro-tipo");

const elementoResumoEmitidas = document.getElementById("resumo-emitidas");
const elementoResumoChamadas = document.getElementById("resumo-chamadas");
const elementoResumoTempoMedio = document.getElementById("resumo-tempo-medio");

/** Monta a querystring com os filtros de período e tipo atualmente selecionados. */
function montarParametros(incluirTipo = true) {
    const parametros = new URLSearchParams();

    if (campoInicio.value) {
        parametros.set("inicio", campoInicio.value);
    }
    if (campoFim.value) {
        parametros.set("fim", campoFim.value);
    }
    if (incluirTipo) {
        parametros.set("tipo", campoTipo.value);
    }

    return parametros.toString();
}

/** Busca e exibe o resumo estatístico do período selecionado. */
async function atualizarResumo() {
    try {
        const resposta = await fetch(`/api/relatorios/resumo?${montarParametros(false)}`);
        const dados = await resposta.json();

        if (!dados.sucesso) {
            throw new Error(dados.erro || "Erro ao consultar resumo.");
        }

        elementoResumoEmitidas.textContent = dados.total_emitidas;
        elementoResumoChamadas.textContent = dados.total_chamadas;
        elementoResumoTempoMedio.textContent = dados.tempo_medio.tempo_medio_formatado;
    } catch (erro) {
        console.error(erro);
        alert(`Erro ao atualizar resumo: ${erro.message}`);
    }
}

/**
 * Dispara o download de um relatório em uma nova aba, delegando ao
 * navegador o tratamento do cabeçalho Content-Disposition retornado
 * pelo servidor Flask (send_file com as_attachment=True).
 */
function baixarRelatorio(formato) {
    const url = `/api/relatorios/${formato}?${montarParametros(true)}`;
    window.open(url, "_blank");
}

function inicializar() {
    document.getElementById("btn-atualizar-resumo").addEventListener("click", atualizarResumo);
    document.getElementById("btn-download-csv").addEventListener("click", () => baixarRelatorio("csv"));
    document.getElementById("btn-download-excel").addEventListener("click", () => baixarRelatorio("excel"));
    document.getElementById("btn-download-pdf").addEventListener("click", () => baixarRelatorio("pdf"));

    atualizarResumo();
}

document.addEventListener("DOMContentLoaded", inicializar);
