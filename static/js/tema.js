/**
 * tema.js
 * =======
 * Controla o botão de troca de tema claro/escuro (ver .botao-tema em
 * templates/layout.html e a paleta em static/css/style.css). A
 * PREFERÊNCIA em si é aplicada o mais cedo possível por um script
 * INLINE no <head> de layout.html (antes deste arquivo sequer
 * terminar de baixar), para não piscar em tema claro por uma fração de
 * segundo a cada carregamento de página — este arquivo só cuida do
 * clique no botão e de manter o ícone (☀️/🌙) sincronizado com o tema
 * atual.
 *
 * Não é incluído nas três telas do painel público de TV (painel,
 * painel_empresa, painel_geral — ver a exclusão em layout.html): esse
 * painel tem sua própria identidade visual fixa, sempre escura,
 * independente da preferência de tema salva no navegador.
 */

"use strict";

const CHAVE_TEMA = "sigs_tema";

/** Retorna true se o tema escuro está ativo agora (atributo já aplicado no <html>). */
function temaEscuroAtivo() {
    return document.documentElement.getAttribute("data-tema") === "escuro";
}

/**
 * Alterna entre os temas claro/escuro: atualiza o atributo "data-tema"
 * no <html> (o que já basta para toda a folha de estilos reagir, via
 * as variáveis CSS em html[data-tema="escuro"]) e salva a escolha em
 * localStorage, para persistir entre visitas e outras abas/páginas.
 */
function alternarTema() {
    if (temaEscuroAtivo()) {
        document.documentElement.removeAttribute("data-tema");
        salvarPreferencia("claro");
    } else {
        document.documentElement.setAttribute("data-tema", "escuro");
        salvarPreferencia("escuro");
    }
    atualizarBotaoTema();
}

/**
 * Salva a preferência em localStorage. Envolvido em try/catch porque
 * localStorage pode estar bloqueado (ex.: navegação privada restrita
 * em alguns navegadores) — nesse caso, a troca de tema ainda funciona
 * na página atual, só não persiste para a próxima visita.
 */
function salvarPreferencia(valor) {
    try {
        localStorage.setItem(CHAVE_TEMA, valor);
    } catch (erro) {
        // Sem persistência disponível — segue o jogo sem quebrar a troca.
    }
}

/** Sincroniza o ícone/rótulo do botão com o tema atualmente ativo. */
function atualizarBotaoTema() {
    const botoes = document.querySelectorAll(".botao-tema");
    if (botoes.length === 0) {
        return;
    }

    const escuro = temaEscuroAtivo();
    const icone = escuro ? "☀️" : "🌙";
    const rotulo = escuro ? "Mudar para tema claro" : "Mudar para tema escuro";

    botoes.forEach((botao) => {
        botao.textContent = icone;
        botao.setAttribute("aria-label", rotulo);
        botao.title = rotulo;
    });
}

function inicializar() {
    atualizarBotaoTema();
    document.querySelectorAll(".botao-tema").forEach((botao) => {
        botao.addEventListener("click", alternarTema);
    });
}

document.addEventListener("DOMContentLoaded", inicializar);
