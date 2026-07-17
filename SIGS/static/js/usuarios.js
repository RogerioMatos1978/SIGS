/**
 * usuarios.js
 * ===========
 * Lógica da tela de administração de usuários do SIGS (/admin/usuarios):
 * criação de novos usuários, reset de senha, ativação/desativação,
 * alteração de perfil (admin/atendente) e reset total das senhas
 * emitidas ("Zona de Perigo"). Todas as ações chamam a API REST
 * protegida por login_required + admin_required em app.py.
 */

"use strict";

const formularioNovoUsuario = document.getElementById("form-novo-usuario");
const mensagemNovoUsuario = document.getElementById("novo-usuario-mensagem");

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

/** Cria um novo usuário a partir do formulário "Novo Usuário". */
async function criarUsuario(evento) {
    evento.preventDefault();

    const nomeCompleto = document.getElementById("novo-nome").value;
    const login = document.getElementById("novo-login").value;
    const senha = document.getElementById("novo-senha").value;
    const perfil = document.getElementById("novo-perfil").value;

    try {
        await chamarApiAdmin("/api/admin/usuarios", {
            method: "POST",
            body: JSON.stringify({ nome_completo: nomeCompleto, login, senha, perfil }),
        });

        mensagemNovoUsuario.textContent = "Usuário criado com sucesso!";
        mensagemNovoUsuario.className = "mensagem-status sucesso";

        // Recarrega a página para exibir o novo usuário na tabela (mais
        // simples e confiável do que reconstruir a linha da tabela em JS).
        setTimeout(() => window.location.reload(), 900);
    } catch (erro) {
        mensagemNovoUsuario.textContent = `Erro: ${erro.message}`;
        mensagemNovoUsuario.className = "mensagem-status erro";
    }
}

/** Reseta a senha de um usuário, solicitando a nova senha ao administrador. */
async function resetarSenha(usuarioId) {
    const novaSenha = window.prompt("Digite a nova senha para este usuário (mínimo 6 caracteres):");
    if (!novaSenha) {
        return;
    }

    try {
        await chamarApiAdmin(`/api/admin/usuarios/${usuarioId}/resetar-senha`, {
            method: "POST",
            body: JSON.stringify({ nova_senha: novaSenha }),
        });
        alert("Senha redefinida com sucesso.");
    } catch (erro) {
        alert(`Erro ao redefinir senha: ${erro.message}`);
    }
}

/** Ativa ou desativa um usuário. */
async function alternarStatus(usuarioId, ativoAtual) {
    const novoStatus = !ativoAtual;
    const acao = novoStatus ? "ativar" : "desativar";

    if (!window.confirm(`Tem certeza que deseja ${acao} este usuário?`)) {
        return;
    }

    try {
        await chamarApiAdmin(`/api/admin/usuarios/${usuarioId}/status`, {
            method: "POST",
            body: JSON.stringify({ ativo: novoStatus }),
        });
        window.location.reload();
    } catch (erro) {
        alert(`Erro ao atualizar status: ${erro.message}`);
    }
}

/** Altera o perfil (admin/atendente) de um usuário. */
async function alterarPerfil(usuarioId, novoPerfil) {
    try {
        await chamarApiAdmin(`/api/admin/usuarios/${usuarioId}/perfil`, {
            method: "POST",
            body: JSON.stringify({ perfil: novoPerfil }),
        });
    } catch (erro) {
        alert(`Erro ao atualizar perfil: ${erro.message}`);
        window.location.reload();
    }
}

/** Reseta TODAS as senhas emitidas e chamadas (ação destrutiva). */
async function resetarSenhasEmitidas() {
    const confirmacao = window.prompt(
        'Esta ação apaga PERMANENTEMENTE todo o histórico de senhas. ' +
        'Digite "CONFIRMAR" (em maiúsculas) para prosseguir:'
    );

    if (confirmacao !== "CONFIRMAR") {
        return;
    }

    try {
        await chamarApiAdmin("/api/admin/reset-senhas-emitidas", {
            method: "POST",
            body: JSON.stringify({ confirmar: true }),
        });
        alert("Todas as senhas emitidas foram apagadas.");
        window.location.reload();
    } catch (erro) {
        alert(`Erro ao resetar senhas emitidas: ${erro.message}`);
    }
}

function inicializar() {
    if (formularioNovoUsuario) {
        formularioNovoUsuario.addEventListener("submit", criarUsuario);
    }

    document.querySelectorAll(".btn-resetar-senha").forEach((botao) => {
        botao.addEventListener("click", () => resetarSenha(botao.dataset.usuarioId));
    });

    document.querySelectorAll(".btn-toggle-status").forEach((botao) => {
        botao.addEventListener("click", () => {
            const ativoAtual = botao.dataset.ativo === "true";
            alternarStatus(botao.dataset.usuarioId, ativoAtual);
        });
    });

    document.querySelectorAll(".select-perfil").forEach((select) => {
        select.addEventListener("change", () => {
            alterarPerfil(select.dataset.usuarioId, select.value);
        });
    });

    const botaoReset = document.getElementById("btn-reset-senhas-emitidas");
    if (botaoReset) {
        botaoReset.addEventListener("click", resetarSenhasEmitidas);
    }
}

document.addEventListener("DOMContentLoaded", inicializar);
