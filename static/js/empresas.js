/**
 * empresas.js
 * ===========
 * Lógica da tela de administração de empresas do feirão do emprego
 * (/admin/empresas): cadastro, renomeação e ativação/desativação. Todas
 * as ações chamam a API REST protegida por login_required + admin_required
 * em app.py. Segue exatamente o mesmo padrão de usuarios.js.
 */

"use strict";

const formularioNovaEmpresa = document.getElementById("form-nova-empresa");
const mensagemNovaEmpresa = document.getElementById("nova-empresa-mensagem");

/** Executa uma chamada à API, tratando erros de forma padronizada. */
async function chamarApiAdmin(url, opcoes = {}) {
    const resposta = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        ...opcoes,
    });

    if (resposta.status === 401) {
        window.location.href = "/login";
        throw new Error("Sessão expirada.");
    }

    const dados = await resposta.json().catch(() => ({}));

    if (!resposta.ok || dados.sucesso === false) {
        throw new Error(dados.erro || `Erro inesperado (HTTP ${resposta.status}).`);
    }

    return dados;
}

/**
 * Cadastra uma nova empresa a partir do formulário "Nova Empresa".
 *
 * O botão de envio é desabilitado imediatamente para impedir duplo envio
 * do formulário, seguindo o mesmo cuidado adotado em usuarios.js para a
 * criação de usuários.
 */
async function criarEmpresa(evento) {
    evento.preventDefault();

    const botaoEnviar = formularioNovaEmpresa.querySelector('button[type="submit"]');
    if (botaoEnviar.disabled) {
        return;
    }

    const nome = document.getElementById("nova-empresa-nome").value.trim();

    botaoEnviar.disabled = true;
    const textoOriginalBotao = botaoEnviar.textContent;
    botaoEnviar.textContent = "Cadastrando...";
    mensagemNovaEmpresa.textContent = "";
    mensagemNovaEmpresa.className = "mensagem-status";

    try {
        await chamarApiAdmin("/api/admin/empresas", {
            method: "POST",
            body: JSON.stringify({ nome }),
        });

        mensagemNovaEmpresa.textContent = "Empresa cadastrada com sucesso!";
        mensagemNovaEmpresa.className = "mensagem-status sucesso";

        // Recarrega a página para exibir a nova empresa na tabela (mais
        // simples e confiável do que reconstruir a linha em JS).
        setTimeout(() => window.location.reload(), 900);
    } catch (erro) {
        mensagemNovaEmpresa.textContent = `Erro: ${erro.message}`;
        mensagemNovaEmpresa.className = "mensagem-status erro";

        botaoEnviar.disabled = false;
        botaoEnviar.textContent = textoOriginalBotao;
    }
}

/** Renomeia uma empresa, solicitando o novo nome ao administrador. */
async function renomearEmpresa(empresaId, nomeAtual) {
    const novoNome = window.prompt("Novo nome da empresa:", nomeAtual);
    if (!novoNome || novoNome.trim() === "" || novoNome.trim() === nomeAtual) {
        return;
    }

    try {
        await chamarApiAdmin(`/api/admin/empresas/${empresaId}/renomear`, {
            method: "POST",
            body: JSON.stringify({ nome: novoNome.trim() }),
        });
        window.location.reload();
    } catch (erro) {
        alert(`Erro ao renomear empresa: ${erro.message}`);
    }
}

/** Ativa ou desativa uma empresa. */
async function alternarStatusEmpresa(empresaId, ativaAtual) {
    const novoStatus = !ativaAtual;
    const acao = novoStatus ? "ativar" : "desativar";

    if (!window.confirm(`Tem certeza que deseja ${acao} esta empresa?`)) {
        return;
    }

    try {
        await chamarApiAdmin(`/api/admin/empresas/${empresaId}/status`, {
            method: "POST",
            body: JSON.stringify({ ativa: novoStatus }),
        });
        window.location.reload();
    } catch (erro) {
        alert(`Erro ao atualizar status: ${erro.message}`);
    }
}

function inicializar() {
    if (formularioNovaEmpresa) {
        formularioNovaEmpresa.addEventListener("submit", criarEmpresa);
    }

    document.querySelectorAll(".btn-renomear-empresa").forEach((botao) => {
        botao.addEventListener("click", () => {
            renomearEmpresa(botao.dataset.empresaId, botao.dataset.empresaNome);
        });
    });

    document.querySelectorAll(".btn-toggle-status-empresa").forEach((botao) => {
        botao.addEventListener("click", () => {
            const ativaAtual = botao.dataset.ativa === "true";
            alternarStatusEmpresa(botao.dataset.empresaId, ativaAtual);
        });
    });
}

document.addEventListener("DOMContentLoaded", inicializar);
