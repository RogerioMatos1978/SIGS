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
const elementoResumoTaxaAtendimento = document.getElementById("resumo-taxa-atendimento");
const elementoResumoCanceladas = document.getElementById("resumo-canceladas");
const elementoResumoFilaAgora = document.getElementById("resumo-fila-agora");

// Cores dos gráficos do Dashboard Analítico (ver dashboard-charts.js) —
// sempre `var(--cor-...)` (nunca um hex fixo), para acompanhar o tema
// claro/escuro automaticamente (ver static/js/tema.js).
const CORES_STATUS = {
    Emitida: "var(--cor-secundaria)",
    Chamada: "var(--cor-principal-clara)",
    Finalizada: "var(--cor-sucesso)",
    Cancelada: "var(--cor-erro)",
};

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
        elementoResumoEmpresasCorpo.innerHTML = '<tr><td colspan="3">Nenhuma senha emitida no período.</td></tr>';
        return;
    }

    elementoResumoEmpresasCorpo.innerHTML = "";
    porEmpresa.forEach((item) => {
        const linha = document.createElement("tr");

        const celulaEmpresa = document.createElement("td");
        celulaEmpresa.textContent = item.empresa;

        const celulaTotal = document.createElement("td");
        celulaTotal.textContent = item.total;

        // "Senhas Atendidas" usa o mesmo critério de hora_chamada
        // preenchida já usado no Resumo do Período (ver
        // database.listar_contagem_por_empresa) — nunca maior que
        // "Senhas Emitidas" da mesma linha.
        const celulaAtendidas = document.createElement("td");
        celulaAtendidas.textContent = item.atendidas ?? 0;

        linha.appendChild(celulaEmpresa);
        linha.appendChild(celulaTotal);
        linha.appendChild(celulaAtendidas);
        elementoResumoEmpresasCorpo.appendChild(linha);
    });
}

/** Formata uma data "YYYY-MM-DD" (ver database.listar_emissoes_por_dia)
 * como "dd/mm", rótulo compacto para o eixo X do gráfico de tendência. */
function formatarDataCurta(dataIso) {
    const partes = String(dataIso).split("-");
    return partes.length === 3 ? `${partes[2]}/${partes[1]}` : dataIso;
}

/** Formata segundos como "MM:SS" — mesmo padrão usado pelo card de
 * Tempo Médio de Atendimento do Resumo do Período. */
function formatarSegundosComoMinutos(segundos) {
    const total = Math.round(segundos || 0);
    const minutos = Math.floor(total / 60);
    const restante = total % 60;
    return `${String(minutos).padStart(2, "0")}:${String(restante).padStart(2, "0")}`;
}

/**
 * Desenha os 5 gráficos do Dashboard Analítico a partir da MESMA
 * resposta já usada pelos cards/tabela do Resumo do Período (ver
 * app.py:api_relatorios_resumo) — um único fetch alimenta a tela
 * inteira, evitando requisições extras e garantindo que os números do
 * dashboard batam exatamente com os cards acima dele.
 */
function renderizarDashboardAnalitico(dados) {
    const porDia = dados.por_dia || [];
    renderizarGraficoLinha(
        document.getElementById("grafico-por-dia"),
        porDia.map((item) => formatarDataCurta(item.dia)),
        [
            { nome: "Emitidas", cor: "var(--cor-principal)", valores: porDia.map((item) => item.emitidas) },
            { nome: "Atendidas", cor: "var(--cor-sucesso)", valores: porDia.map((item) => item.atendidas) },
            { nome: "Canceladas", cor: "var(--cor-erro)", valores: porDia.map((item) => item.canceladas) },
        ],
        { mensagemVazio: "Nenhuma senha emitida no período selecionado." }
    );

    const porStatus = dados.por_status || [];
    renderizarGraficoRosca(
        document.getElementById("grafico-por-status"),
        porStatus.map((item) => ({
            rotulo: item.status,
            valor: item.total,
            cor: CORES_STATUS[item.status] || "var(--cor-principal)",
        })),
        { mensagemVazio: "Nenhuma senha no período selecionado." }
    );

    const porHora = dados.por_hora || [];
    renderizarGraficoBarras(
        document.getElementById("grafico-por-hora"),
        porHora.map((item) => ({ rotulo: `${item.hora}h`, valor: item.total })),
        { cor: "var(--cor-principal)", mensagemVazio: "Nenhuma emissão no período selecionado." }
    );

    const porEmpresa = dados.por_empresa || [];
    renderizarGraficoBarrasComparativas(
        document.getElementById("grafico-por-empresa"),
        porEmpresa.map((item) => ({
            rotulo: item.empresa,
            valorPrincipal: item.total,
            valorSecundario: item.atendidas ?? 0,
        })),
        {
            corPrincipal: "var(--cor-cinza-borda)",
            corSecundaria: "var(--cor-principal)",
            margemRotulo: 160,
            mensagemVazio: "Nenhuma senha emitida no período selecionado.",
        }
    );

    const tempoPorEmpresa = dados.tempo_medio_por_empresa || [];
    renderizarGraficoBarrasHorizontais(
        document.getElementById("grafico-tempo-por-empresa"),
        tempoPorEmpresa.map((item) => ({
            rotulo: item.empresa,
            valor: item.tempo_medio_segundos,
            valorFormatado: formatarSegundosComoMinutos(item.tempo_medio_segundos),
        })),
        {
            cor: "var(--cor-secundaria)",
            margemRotulo: 160,
            mensagemVazio: "Nenhuma chamada registrada no período selecionado.",
        }
    );
}

// Evita que cliques repetidos em "Atualizar Resumo" (ex.: usuário
// impaciente clicando várias vezes) disparem requisições sobrepostas
// cujas respostas podem chegar fora de ordem e deixar na tela o
// resultado de um filtro de período já trocado pelo usuário.
let atualizandoResumo = false;

/** Busca e exibe o resumo estatístico do período selecionado. */
async function atualizarResumo() {
    if (atualizandoResumo) {
        return;
    }
    atualizandoResumo = true;
    try {
        const resposta = await fetch(`/api/relatorios/resumo?${montarParametros(false)}`);
        const dados = await resposta.json();

        if (!dados.sucesso) {
            throw new Error(dados.erro || "Erro ao consultar resumo.");
        }

        elementoResumoEmitidas.textContent = dados.total_emitidas;
        elementoResumoChamadas.textContent = dados.total_chamadas;
        elementoResumoTempoMedio.textContent = dados.tempo_medio.tempo_medio_formatado;
        if (elementoResumoTaxaAtendimento) {
            elementoResumoTaxaAtendimento.textContent = `${dados.taxa_atendimento ?? 0}%`;
        }
        if (elementoResumoCanceladas) {
            elementoResumoCanceladas.textContent = dados.total_canceladas ?? 0;
        }
        if (elementoResumoFilaAgora) {
            elementoResumoFilaAgora.textContent = dados.fila_aguardando_agora ?? 0;
        }
        renderizarResumoEmpresas(dados.por_empresa);
        renderizarDashboardAnalitico(dados);
    } catch (erro) {
        console.error(erro);
        alert(`Erro ao atualizar resumo: ${erro.message}`);
    } finally {
        atualizandoResumo = false;
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

/** Formata um objeto Date como "YYYY-MM-DD" (formato aceito por <input type="date">), em horário LOCAL (nunca UTC, para não "vazar" um dia por fuso). */
function formatarDataParaInput(data) {
    const ano = data.getFullYear();
    const mes = String(data.getMonth() + 1).padStart(2, "0");
    const dia = String(data.getDate()).padStart(2, "0");
    return `${ano}-${mes}-${dia}`;
}

/**
 * Preenche Início/Fim a partir de um atalho de período (ver botões
 * ".btn-preset-periodo" em relatorios.html) e já dispara a atualização —
 * cobre os recortes de data mais comuns sem exigir abrir o seletor de
 * calendário duas vezes (ver pesquisa de boas práticas de dashboard: uso
 * de filtros rápidos/presets para acelerar a exploração dos dados).
 */
function aplicarPresetPeriodo(preset) {
    const hoje = new Date();
    let inicio = new Date(hoje);
    let fim = new Date(hoje);

    switch (preset) {
        case "hoje":
            break;
        case "7dias":
            inicio.setDate(inicio.getDate() - 6);
            break;
        case "30dias":
            inicio.setDate(inicio.getDate() - 29);
            break;
        case "mes":
            inicio = new Date(hoje.getFullYear(), hoje.getMonth(), 1);
            break;
        case "tudo":
            campoInicio.value = "";
            campoFim.value = "";
            atualizarResumo();
            return;
        default:
            return;
    }

    campoInicio.value = formatarDataParaInput(inicio);
    campoFim.value = formatarDataParaInput(fim);
    atualizarResumo();
}

const EH_RECRUTADOR = Boolean(window.SIGS_CONFIG && window.SIGS_CONFIG.ehRecrutador);

function inicializar() {
    document.getElementById("btn-atualizar-resumo").addEventListener("click", atualizarResumo);
    document.getElementById("btn-download-csv").addEventListener("click", () => baixarRelatorio("csv"));
    document.getElementById("btn-download-excel").addEventListener("click", () => baixarRelatorio("excel"));
    document.getElementById("btn-download-pdf").addEventListener("click", () => baixarRelatorio("pdf"));

    document.querySelectorAll(".btn-preset-periodo").forEach((botao) => {
        botao.addEventListener("click", () => aplicarPresetPeriodo(botao.dataset.preset));
    });

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
