/**
 * shell.js
 * ========
 * Comportamento da estrutura global do SIGS (sidebar + header), conforme o
 * Design System — ERP Web Profissional (ver static/css/design-system.css e
 * templates/layout.html):
 *
 *   - Desktop/tablet: botão "Recolher menu" alterna a sidebar entre
 *     expandida (256px) e recolhida (68px, só ícones). A escolha é salva em
 *     localStorage ("sigs_sidebar") e aplicada o mais cedo possível por um
 *     script inline no <head> de layout.html, para não "piscar".
 *   - Celular (< 768px): a sidebar vira um drawer aberto pelo botão de menu
 *     do header; fecha com Esc, clique no fundo escurecido ou ao escolher
 *     um item, devolvendo o foco ao botão que a abriu.
 *
 * Só é carregado nas telas internas com usuário logado (nunca nos painéis
 * públicos de TV).
 */

"use strict";

const CHAVE_SIDEBAR = "sigs_sidebar";
const LARGURA_MOBILE = 768;

const shell = document.getElementById("app-shell");
const sidebar = document.getElementById("sidebar");
const botaoRecolher = document.querySelector(".sidebar-recolher");
const botaoAbrir = document.querySelector(".sidebar-abrir");
const fundo = document.querySelector("[data-fechar-sidebar]");

function ehMobile() {
    return window.innerWidth < LARGURA_MOBILE;
}

function sidebarRecolhida() {
    return document.documentElement.getAttribute("data-sidebar") === "recolhida";
}

/** Salva a preferência; localStorage pode estar bloqueado (navegação privada). */
function salvarSidebar(valor) {
    try {
        localStorage.setItem(CHAVE_SIDEBAR, valor);
    } catch (erro) {
        // Sem persistência — a troca ainda vale para a página atual.
    }
}

/** Mantém rótulo, tooltip e aria-expanded do botão de recolher em dia. */
function atualizarBotaoRecolher() {
    if (!botaoRecolher) {
        return;
    }
    const recolhida = sidebarRecolhida();
    const rotulo = recolhida ? "Expandir menu" : "Recolher menu";
    botaoRecolher.setAttribute("aria-expanded", String(!recolhida));
    botaoRecolher.title = rotulo;
    const texto = botaoRecolher.querySelector(".rotulo-recolher");
    if (texto) {
        texto.textContent = rotulo;
    }
}

function alternarRecolhida() {
    const novoValor = sidebarRecolhida() ? "expandida" : "recolhida";
    document.documentElement.setAttribute("data-sidebar", novoValor);
    salvarSidebar(novoValor);
    atualizarBotaoRecolher();
}

function abrirDrawer() {
    if (!shell || !botaoAbrir) {
        return;
    }
    shell.classList.add("sidebar-aberta");
    botaoAbrir.setAttribute("aria-expanded", "true");
    const primeiroLink = sidebar.querySelector("a, button");
    if (primeiroLink) {
        primeiroLink.focus();
    }
}

function fecharDrawer(devolverFoco) {
    if (!shell || !shell.classList.contains("sidebar-aberta")) {
        return;
    }
    shell.classList.remove("sidebar-aberta");
    if (botaoAbrir) {
        botaoAbrir.setAttribute("aria-expanded", "false");
        if (devolverFoco) {
            botaoAbrir.focus();
        }
    }
}

function inicializarShell() {
    if (!shell || !sidebar) {
        return;
    }

    atualizarBotaoRecolher();

    if (botaoRecolher) {
        botaoRecolher.addEventListener("click", alternarRecolhida);
    }
    if (botaoAbrir) {
        botaoAbrir.addEventListener("click", abrirDrawer);
    }
    if (fundo) {
        fundo.addEventListener("click", () => fecharDrawer(true));
    }

    document.addEventListener("keydown", (evento) => {
        if (evento.key === "Escape") {
            fecharDrawer(true);
        }
    });

    // Escolher um item do menu no celular fecha o drawer.
    sidebar.querySelectorAll("a.nav-item").forEach((link) => {
        link.addEventListener("click", () => {
            if (ehMobile()) {
                fecharDrawer(false);
            }
        });
    });

    // Girou o aparelho / redimensionou a janela: não deixa o drawer preso aberto.
    window.addEventListener("resize", () => {
        if (!ehMobile()) {
            fecharDrawer(false);
        }
    });
}

document.addEventListener("DOMContentLoaded", inicializarShell);
