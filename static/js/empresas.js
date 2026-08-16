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

/**
 * Envia o arquivo de logo escolhido para uma empresa. Diferente de
 * ``chamarApiAdmin`` (que sempre envia JSON), aqui o corpo é
 * ``multipart/form-data`` (via ``FormData``) — por isso NÃO definimos o
 * cabeçalho ``Content-Type`` manualmente: o navegador precisa calcular
 * sozinho o "boundary" correto do multipart, o que só acontece se o
 * header for deixado para o próprio ``fetch`` preencher.
 */
async function enviarLogoEmpresa(empresaId, arquivo) {
    const formData = new FormData();
    formData.append("logo", arquivo);

    const resposta = await fetch(`/api/admin/empresas/${empresaId}/logo`, {
        method: "POST",
        body: formData,
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
 * Chamada quando o administrador escolhe um arquivo de logo no seletor
 * (input file oculto, aberto pelo botão "📷 Logo" — ver inicializar).
 * Envia o arquivo, e em caso de sucesso atualiza a miniatura do logo e o
 * seletor de cor na mesma linha com o resultado (a cor é extraída
 * automaticamente da imagem pelo servidor).
 */
async function tratarSelecaoLogo(evento) {
    const input = evento.target;
    const arquivo = input.files && input.files[0];
    if (!arquivo) {
        return;
    }

    const empresaId = input.dataset.empresaId;

    try {
        const dados = await enviarLogoEmpresa(empresaId, arquivo);

        const preview = document.getElementById(`logo-preview-${empresaId}`);
        if (preview) {
            // Bust de cache: sem isso, o navegador pode continuar exibindo
            // a imagem antiga (mesma URL) mesmo após o arquivo no servidor
            // ter sido substituído.
            preview.src = `/static/${dados.logo_path}?v=${Date.now()}`;
        }

        const seletorCor = document.querySelector(`.input-cor-empresa[data-empresa-id="${empresaId}"]`);
        if (seletorCor && dados.cor_principal) {
            seletorCor.value = dados.cor_principal;
        }
    } catch (erro) {
        alert(`Erro ao enviar o logo: ${erro.message}`);
    } finally {
        // Limpa o input para permitir escolher o MESMO arquivo de novo no
        // futuro (o evento "change" não dispara se o valor não mudar).
        input.value = "";
    }
}

/** Sobrescreve manualmente a cor de identidade visual de uma empresa. */
async function alterarCorEmpresa(empresaId, cor) {
    try {
        await chamarApiAdmin(`/api/admin/empresas/${empresaId}/cor`, {
            method: "POST",
            body: JSON.stringify({ cor }),
        });
    } catch (erro) {
        alert(`Erro ao atualizar cor da empresa: ${erro.message}`);
    }
}

/**
 * Reinicia para zero o contador de numeração de senhas de UMA empresa
 * (cada empresa tem sua própria sequência independente — ver
 * database.criar_senha). Não afeta as demais empresas nem apaga o
 * histórico de senhas já emitidas.
 */
async function reiniciarContadorEmpresa(empresaId, nomeEmpresa) {
    if (!window.confirm(`Reiniciar o contador de senhas de "${nomeEmpresa}"? A próxima senha emitida para ela voltará a ser 001.`)) {
        return;
    }

    try {
        await chamarApiAdmin(`/api/admin/empresas/${empresaId}/reiniciar-contador`, {
            method: "POST",
        });

        const celula = document.getElementById(`proxima-senha-${empresaId}`);
        if (celula) {
            celula.textContent = "001";
        }
    } catch (erro) {
        alert(`Erro ao reiniciar contador da empresa: ${erro.message}`);
    }
}

/**
 * Reabre o atendimento de uma empresa cujo dia foi finalizado pelo
 * recrutador (ver /api/finalizar-atendimento-dia em index.js) —
 * restrito a administradores, propositalmente (ver
 * app.py:api_admin_reabrir_atendimento_empresa). NÃO restaura as senhas
 * que foram canceladas automaticamente no encerramento.
 */
async function reabrirAtendimentoEmpresa(empresaId, nomeEmpresa) {
    if (
        !window.confirm(
            `Reabrir o atendimento de "${nomeEmpresa}"? Isso volta a permitir emissão e chamada de novas ` +
            `senhas para esta empresa hoje. As senhas já canceladas pelo encerramento NÃO serão restauradas.`
        )
    ) {
        return;
    }

    try {
        await chamarApiAdmin(`/api/admin/empresas/${empresaId}/reabrir-atendimento`, {
            method: "POST",
        });
        window.location.reload();
    } catch (erro) {
        alert(`Erro ao reabrir atendimento da empresa: ${erro.message}`);
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

    document.querySelectorAll(".btn-reiniciar-contador-empresa").forEach((botao) => {
        botao.addEventListener("click", () => {
            reiniciarContadorEmpresa(botao.dataset.empresaId, botao.dataset.empresaNome);
        });
    });

    document.querySelectorAll(".btn-reabrir-atendimento-empresa").forEach((botao) => {
        botao.addEventListener("click", () => {
            reabrirAtendimentoEmpresa(botao.dataset.empresaId, botao.dataset.empresaNome);
        });
    });

    document.querySelectorAll(".btn-toggle-status-empresa").forEach((botao) => {
        botao.addEventListener("click", () => {
            const ativaAtual = botao.dataset.ativa === "true";
            alternarStatusEmpresa(botao.dataset.empresaId, ativaAtual);
        });
    });

    // Botão "📷 Logo" apenas abre o seletor de arquivo oculto correspondente
    // (data-target aponta para o id do <input type="file">); o envio de
    // fato acontece no "change" desse input, tratado por tratarSelecaoLogo.
    document.querySelectorAll(".btn-upload-logo").forEach((botao) => {
        botao.addEventListener("click", () => {
            const input = document.getElementById(botao.dataset.target);
            if (input) {
                input.click();
            }
        });
    });

    document.querySelectorAll(".input-logo-empresa").forEach((input) => {
        input.addEventListener("change", tratarSelecaoLogo);
    });

    document.querySelectorAll(".input-cor-empresa").forEach((input) => {
        input.addEventListener("change", () => {
            alterarCorEmpresa(input.dataset.empresaId, input.value);
        });
    });
}

document.addEventListener("DOMContentLoaded", inicializar);
