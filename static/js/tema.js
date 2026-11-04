/**
 * tema.js
 * =======
 * Controla o botão de troca de tema claro/escuro (ver .botao-tema em
 * templates/layout.html e a paleta em static/css/style.css). A
 * PREFERÊNCIA em si é aplicada o mais cedo possível por um script
 * INLINE no <head> de layout.html (antes deste arquivo sequer
 * terminar de baixar), para não piscar em tema claro por uma fração de
 * segundo a cada carregamento de página — este arquivo só cuida do
 * clique no botão e de manter o ícone (sol/lua, SVG inline) sincronizado
 * com o tema atual.
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

/**
 * Ícones (estilo Lucide, traço de 2px) do botão de tema — SVG inline, sem
 * emoji e sem depender de biblioteca externa (o Design System pede ícones
 * consistentes e evita emoji em controles). Aparecem ESCONDIDOS de leitores
 * de tela; o nome acessível do botão vem do aria-label.
 */
const SVG_ABRE = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">';
const ICONE_LUA = SVG_ABRE + '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>';
const ICONE_SOL = SVG_ABRE + '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/></svg>';

/** Sincroniza o ícone/rótulo do botão com o tema atualmente ativo. */
function atualizarBotaoTema() {
    const botoes = document.querySelectorAll(".botao-tema");
    if (botoes.length === 0) {
        return;
    }

    const escuro = temaEscuroAtivo();
    const icone = escuro ? ICONE_SOL : ICONE_LUA;
    const rotulo = escuro ? "Mudar para tema claro" : "Mudar para tema escuro";

    botoes.forEach((botao) => {
        botao.innerHTML = icone;
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
