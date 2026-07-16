/**
 * configuracoes.js
 * ================
 * Lógica da tela de Configurações do SIGS: envia as alterações do
 * formulário para a API (/api/config) via Fetch, sem recarregar a
 * página, e exibe mensagem de sucesso ou erro ao usuário.
 */

"use strict";

const formulario = document.getElementById("form-configuracoes");
const elementoMensagem = document.getElementById("config-mensagem");

/**
 * Converte os dados do formulário HTML em um objeto plano de
 * configurações, pronto para ser enviado como JSON.
 */
function coletarDadosFormulario() {
    const dadosFormulario = new FormData(formulario);
    const configuracoes = {};

    for (const [chave, valor] of dadosFormulario.entries()) {
        configuracoes[chave] = valor;
    }

    return configuracoes;
}

/** Exibe uma mensagem de status (sucesso ou erro) abaixo do formulário. */
function exibirMensagem(texto, tipo) {
    elementoMensagem.textContent = texto;
    elementoMensagem.className = `mensagem-status ${tipo}`;
}

/** Envia as configurações atualizadas para o servidor. */
async function salvarConfiguracoes(evento) {
    evento.preventDefault();

    try {
        const configuracoes = coletarDadosFormulario();

        const resposta = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(configuracoes),
        });

        const dados = await resposta.json();

        if (!resposta.ok || dados.sucesso === false) {
            throw new Error(dados.erro || "Erro ao salvar configurações.");
        }

        exibirMensagem("Configurações salvas com sucesso!", "sucesso");
    } catch (erro) {
        exibirMensagem(`Erro: ${erro.message}`, "erro");
    }
}

function inicializar() {
    formulario.addEventListener("submit", salvarConfiguracoes);
}

document.addEventListener("DOMContentLoaded", inicializar);
