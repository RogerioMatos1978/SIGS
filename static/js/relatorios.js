/**
 * relatorios.js
 * =============
 * Lógica da tela de Relatórios do SIGS: consulta o resumo estatístico
 * (senhas emitidas, chamadas realizadas, tempo médio de atendimento,
 * senhas por empresa) e dispara o download dos relatórios em CSV, Excel
 * ou PDF, respeitando o período, o tipo e a empresa selecionados.
 */

"use strict";

const campoInicio = document.getElementById("filtro-inicio");
const campoFim = document.getElementById("filtro-fim");
const campoTipo = document.getElementById("filtro-tipo");
const campoEmpresa = document.getElementById("filtro-empresa");

const elementoResumoEmitidas = document.getElementById("resumo-emitidas");
const elementoResumoChamadas = document.getElementById("resumo-chamadas");
const elementoResumoTempoMedio = document.getElementById("resumo-tempo-medio");
const elementoResumoEmpresasCorpo = document.getElementById("resumo-empresas-corpo");

/** Monta a querystring com os filtros de período, tipo e empresa atualmente selecionados. */
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
    if (campoEmpresa && campoEmpresa.value) {
        parametros.set("empresa_id", campoEmpresa.value);
    }

    return parametros.toString();
}

/**
 * Busca, na tela de administração de empresas (todas, ativas e
 * inativas), a lista usada para popular o filtro "Empresa" — diferente
 * do seletor de emissão de senha, aqui é necessário incluir empresas já
 * desativadas, pois o histórico delas continua consultável.
 *
 * Chama um endpoint restrito a administradores (/api/admin/empresas) —
 * por isso NUNCA é chamada para uma sessão de recrutador (ver
 * inicializar(), que pula esta função quando window.SIGS_CONFIG.ehRecrutador
 * é verdadeiro; o próprio HTML também não renderiza o campo "Empresa"
 * nesse caso — ver relatorios.html/_parametros_periodo em app.py, que
 * força o recorte à empresa do recrutador independente do que a
 * querystring contiver).
 */
async function carregarFiltroEmpresas() {
    if (!campoEmpresa) {
        return;
    }

    try {
        const resposta = await fetch("/api/admin/empresas");
        const dados = await resposta.json();

        if (!dados.sucesso) {
            throw new Error(dados.erro || "Erro ao consultar empresas.");
        }

        (dados.empresas || []).forEach((empresa) => {
            const opcao = document.createElement("option");
            opcao.value = empresa.id;
            opcao.textContent = empresa.ativa ? empresa.nome : `${empresa.nome} (inativa)`;
            campoEmpresa.appendChild(opcao);
        });
    } catch (erro) {
        console.error("Não foi possível carregar o filtro de empresas:", erro);
    }
}

/** Renderiza a tabela "Senhas por Empresa" a partir do resumo retornado pela API. */
function renderizarResumoEmpresas(porEmpresa) {
    if (!elementoResumoEmpresasCorpo) {
        return;
    }

    if (!porEmpresa || porEmpresa.length === 0) {
        elementoResumoEmpresasCorpo.innerHTML = '<tr><td colspan="2">Nenhuma senha emitida no período.</td></tr>';
        return;
    }

    elementoResumoEmpresasCorpo.innerHTML = "";
    porEmpresa.forEach((item) => {
        const linha = document.createElement("tr");

        const celulaEmpresa = document.createElement("td");
        celulaEmpresa.textContent = item.empresa;

        const celulaTotal = document.createElement("td");
        celulaTotal.textContent = item.total;

        linha.appendChild(celulaEmpresa);
        linha.appendChild(celulaTotal);
        elementoResumoEmpresasCorpo.appendChild(linha);
    });
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
        renderizarResumoEmpresas(dados.por_empresa);
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

const EH_RECRUTADOR = Boolean(window.SIGS_CONFIG && window.SIGS_CONFIG.ehRecrutador);

function inicializar() {
    document.getElementById("btn-atualizar-resumo").addEventListener("click", atualizarResumo);
    document.getElementById("btn-download-csv").addEventListener("click", () => baixarRelatorio("csv"));
    document.getElementById("btn-download-excel").addEventListener("click", () => baixarRelatorio("excel"));
    document.getElementById("btn-download-pdf").addEventListener("click", () => baixarRelatorio("pdf"));

    // Recrutador não tem permissão para /api/admin/empresas (403) e o
    // campo "Empresa" nem é renderizado no HTML para esse perfil (ver
    // relatorios.html) — pular a chamada evita um erro 403 desnecessário
    // no console.
    if (!EH_RECRUTADOR) {
        carregarFiltroEmpresas();
    }
    atualizarResumo();
}

document.addEventListener("DOMContentLoaded", inicializar);
