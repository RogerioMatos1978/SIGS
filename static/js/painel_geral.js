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
 * propositalmente NÃO exibem "Atendidas" (Finalizada) nem "Canceladas"
 * — mesmo critério aplicado a todos os painéis públicos (ver
 * templates/painel_geral.html e database.listar_ultimas_emitidas):
 * refletem só a situação ATUAL da fila.
 *
 * Já a tabela "Por Empresa" (``atualizarTabelaEmpresas``) mostra
 * Aguardando/Em Atendimento normalmente, mas a coluna "Total" é o
 * total GERAL da empresa (``linha.total``, vindo pronto do backend —
 * ver database.resumo_geral_senhas), incluindo Finalizada/Cancelada —
 * do contrário, empresas com tudo já atendido (ou as duas opções
 * fixas "Criar Currículos"/"Imprimir Currículos", que nascem direto
 * 'Finalizada') sempre apareceriam com "Total: 0" ali, mesmo tendo
 * emitido senhas normalmente. Mesma lógica da seção "Resumo do
 * Feirão" (``atualizarResumoFeirao``, abaixo): o objetivo desta coluna
 * é o resultado acumulado da empresa, não a fila do momento.
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

// Evita chamadas de rede SOBREPOSTAS (mesmo motivo de painel.js): se o
// polling anterior ainda não respondeu quando o próximo setInterval
// dispara, a rodada nova é pulada em vez de arriscar uma resposta
// antiga chegar depois da mais nova e mostrar números desatualizados.
let atualizandoPainelGeral = false;

/** Consulta o resumo geral e atualiza a interface. */
async function atualizarPainelGeral() {
    if (atualizandoPainelGeral) {
        return;
    }
    atualizandoPainelGeral = true;
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
    } finally {
        atualizandoPainelGeral = false;
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
 * Atualiza a tabela com o detalhamento por empresa: Aguardando/Em
 * Atendimento refletem só a fila do momento, mas "Total" é o total
 * GERAL da empresa (``linha.total``, já vem pronto do backend — soma
 * de TODOS os status, inclusive Finalizada/Cancelada). Ver docstring
 * do arquivo para o motivo de não recalcular "Total" como só
 * aguardando + em_atendimento (isso fazia empresas já totalmente
 * atendidas, ou as duas opções fixas, aparecerem com "Total: 0").
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
            linha.total,
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
