/**
 * dashboard-charts.js
 * ====================
 * Funções utilitárias para desenhar os gráficos do Dashboard Analítico
 * (tela de Relatórios — ver static/js/relatorios.js) usando SVG puro,
 * sem nenhuma biblioteca externa (Chart.js, D3 etc.). Decisão
 * deliberada: o SIGS roda em rede local, sem depender de CDN/internet
 * (ver ausência de qualquer <script src> externo em todo o projeto), e o
 * volume de dados de um feirão (algumas centenas/milhares de senhas) não
 * justifica o peso de uma biblioteca de gráficos completa.
 *
 * Cada função "renderizarGrafico*" recebe o elemento <container> onde
 * desenhar (o conteúdo anterior é sempre substituído, para permitir
 * redesenhar ao trocar os filtros de período/empresa) e não devolve
 * nada — o gráfico é montado diretamente no DOM.
 *
 * As cores são passadas como strings `var(--cor-...)` (ver style.css) e
 * usadas diretamente nos atributos de apresentação do SVG (fill/stroke).
 * Isso funciona porque o SVG é desenhado INLINE no documento (não como
 * <img> externo) — um atributo de apresentação pode referenciar uma
 * variável CSS custom normalmente nesse caso. Efeito colateral desejado:
 * os gráficos seguem automaticamente o tema claro/escuro (ver
 * static/js/tema.js) sem precisar recalcular nem redesenhar nada ao
 * trocar de tema.
 */

"use strict";

const NS_SVG = "http://www.w3.org/2000/svg";

function criarSvg(largura, altura) {
    const svg = document.createElementNS(NS_SVG, "svg");
    svg.setAttribute("viewBox", `0 0 ${largura} ${altura}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.classList.add("grafico-svg");
    return svg;
}

function elementoSvg(tag, atributos) {
    const el = document.createElementNS(NS_SVG, tag);
    Object.entries(atributos).forEach(([chave, valor]) => el.setAttribute(chave, valor));
    return el;
}

function mostrarGraficoVazio(container, mensagem) {
    container.innerHTML = `<p class="grafico-vazio">${mensagem || "Sem dados no período selecionado."}</p>`;
}

function criarLegenda(itens) {
    const legenda = document.createElement("div");
    legenda.className = "grafico-legenda";
    itens.forEach((item) => {
        const span = document.createElement("span");
        span.className = "grafico-legenda-item";
        const marcador = document.createElement("i");
        marcador.style.background = item.cor;
        span.appendChild(marcador);
        span.appendChild(document.createTextNode(item.nome));
        legenda.appendChild(span);
    });
    return legenda;
}

/* -------------------- Gráfico de linhas (tendência ao longo do tempo) -------------------- */
/**
 * @param {HTMLElement} container
 * @param {string[]} categorias Rótulos do eixo X (ex.: datas "dd/mm").
 * @param {{nome: string, cor: string, valores: number[]}[]} series Cada
 *   série precisa ter `valores.length === categorias.length`.
 */
function renderizarGraficoLinha(container, categorias, series, opcoes = {}) {
    container.innerHTML = "";
    const semDados = !categorias.length || series.every((serie) => serie.valores.every((v) => !v));
    if (semDados) {
        mostrarGraficoVazio(container, opcoes.mensagemVazio);
        return;
    }

    const largura = 720;
    const altura = 260;
    const margem = { topo: 16, direita: 16, baixo: 34, esquerda: 34 };
    const areaLargura = largura - margem.esquerda - margem.direita;
    const areaAltura = altura - margem.topo - margem.baixo;

    const valorMaximo = Math.max(1, ...series.flatMap((serie) => serie.valores));
    const passoX = categorias.length > 1 ? areaLargura / (categorias.length - 1) : 0;

    const svg = criarSvg(largura, altura);

    // Grade horizontal discreta (4 níveis) com rótulo do eixo Y.
    const niveis = 4;
    for (let i = 0; i <= niveis; i++) {
        const y = margem.topo + areaAltura - (areaAltura * i) / niveis;
        svg.appendChild(
            elementoSvg("line", {
                x1: margem.esquerda, x2: largura - margem.direita, y1: y, y2: y,
                stroke: "var(--cor-cinza-borda)", "stroke-width": "1",
            })
        );
        const rotulo = elementoSvg("text", { x: margem.esquerda - 6, y: y + 4, "text-anchor": "end" });
        rotulo.setAttribute("class", "grafico-rotulo-eixo");
        rotulo.textContent = Math.round((valorMaximo * i) / niveis);
        svg.appendChild(rotulo);
    }

    // No máximo ~7 rótulos no eixo X para não poluir a leitura em
    // períodos longos — mostra um subconjunto espaçado, sempre incluindo
    // o último dia.
    const maxRotulosX = 7;
    const passoRotulo = Math.max(1, Math.ceil(categorias.length / maxRotulosX));
    categorias.forEach((categoria, indice) => {
        if (indice % passoRotulo !== 0 && indice !== categorias.length - 1) {
            return;
        }
        const x = margem.esquerda + passoX * indice;
        const rotulo = elementoSvg("text", { x, y: altura - margem.baixo + 18, "text-anchor": "middle" });
        rotulo.setAttribute("class", "grafico-rotulo-eixo");
        rotulo.textContent = categoria;
        svg.appendChild(rotulo);
    });

    series.forEach((serie) => {
        const pontosXY = serie.valores.map((valor, indice) => ({
            x: margem.esquerda + passoX * indice,
            y: margem.topo + areaAltura - (areaAltura * valor) / valorMaximo,
        }));

        svg.appendChild(
            elementoSvg("polyline", {
                points: pontosXY.map((p) => `${p.x},${p.y}`).join(" "),
                fill: "none",
                stroke: serie.cor,
                "stroke-width": "2.5",
                "stroke-linejoin": "round",
                "stroke-linecap": "round",
            })
        );

        pontosXY.forEach((p, indice) => {
            const ponto = elementoSvg("circle", { cx: p.x, cy: p.y, r: "3", fill: serie.cor });
            const titulo = elementoSvg("title", {});
            titulo.textContent = `${serie.nome} — ${categorias[indice]}: ${serie.valores[indice]}`;
            ponto.appendChild(titulo);
            svg.appendChild(ponto);
        });
    });

    container.appendChild(svg);
    container.appendChild(criarLegenda(series.map((serie) => ({ nome: serie.nome, cor: serie.cor }))));
}

/* -------------------- Gráfico de barras verticais -------------------- */
/** @param {{rotulo: string, valor: number}[]} dados */
function renderizarGraficoBarras(container, dados, opcoes = {}) {
    container.innerHTML = "";
    if (!dados.length || dados.every((item) => !item.valor)) {
        mostrarGraficoVazio(container, opcoes.mensagemVazio);
        return;
    }

    const largura = 720;
    const altura = 220;
    const margem = { topo: 12, direita: 8, baixo: 28, esquerda: 28 };
    const areaLargura = largura - margem.esquerda - margem.direita;
    const areaAltura = altura - margem.topo - margem.baixo;
    const cor = opcoes.cor || "var(--cor-principal)";

    const valorMaximo = Math.max(1, ...dados.map((item) => item.valor));
    const passo = areaLargura / dados.length;
    const larguraBarra = passo * 0.6;
    // Em séries longas (ex.: 24 horas do dia), rotular só a cada 3 barras
    // evita poluir o eixo X com texto ilegível/sobreposto.
    const mostrarTodoRotulo = dados.length <= 12;

    const svg = criarSvg(largura, altura);

    dados.forEach((item, indice) => {
        const alturaBarra = (areaAltura * item.valor) / valorMaximo;
        const x = margem.esquerda + passo * indice + (passo - larguraBarra) / 2;
        const y = margem.topo + areaAltura - alturaBarra;

        const barra = elementoSvg("rect", {
            x, y, width: Math.max(2, larguraBarra), height: Math.max(0, alturaBarra),
            fill: cor, rx: "3",
        });
        const titulo = elementoSvg("title", {});
        titulo.textContent = `${item.rotulo}: ${item.valor}`;
        barra.appendChild(titulo);
        svg.appendChild(barra);

        if (mostrarTodoRotulo || indice % 3 === 0) {
            const rotulo = elementoSvg("text", {
                x: x + larguraBarra / 2, y: altura - margem.baixo + 16, "text-anchor": "middle",
            });
            rotulo.setAttribute("class", "grafico-rotulo-eixo");
            rotulo.textContent = item.rotulo;
            svg.appendChild(rotulo);
        }
    });

    container.appendChild(svg);
}

/* -------------------- Gráfico de barras horizontais (ranking) -------------------- */
/** @param {{rotulo: string, valor: number, valorFormatado?: string}[]} dados já ordenado. */
function renderizarGraficoBarrasHorizontais(container, dados, opcoes = {}) {
    container.innerHTML = "";
    if (!dados.length) {
        mostrarGraficoVazio(container, opcoes.mensagemVazio);
        return;
    }

    const cor = opcoes.cor || "var(--cor-principal)";
    const alturaLinha = 30;
    const largura = 720;
    const altura = dados.length * alturaLinha + 12;
    const margemEsquerda = opcoes.margemRotulo || 150;
    const margemDireita = 60;
    const areaLargura = largura - margemEsquerda - margemDireita;

    const valorMaximo = Math.max(1, ...dados.map((item) => item.valor));
    const svg = criarSvg(largura, altura);

    dados.forEach((item, indice) => {
        const y = indice * alturaLinha + 6;
        const larguraBarra = (areaLargura * item.valor) / valorMaximo;

        const rotulo = elementoSvg("text", { x: margemEsquerda - 8, y: y + alturaLinha / 2 - 2, "text-anchor": "end" });
        rotulo.setAttribute("class", "grafico-rotulo-eixo");
        rotulo.textContent = item.rotulo;
        svg.appendChild(rotulo);

        const barra = elementoSvg("rect", {
            x: margemEsquerda, y, width: Math.max(2, larguraBarra), height: alturaLinha - 10, fill: cor, rx: "4",
        });
        const titulo = elementoSvg("title", {});
        titulo.textContent = `${item.rotulo}: ${item.valorFormatado ?? item.valor}`;
        barra.appendChild(titulo);
        svg.appendChild(barra);

        const valorTexto = elementoSvg("text", {
            x: margemEsquerda + larguraBarra + 6, y: y + alturaLinha / 2 - 2,
        });
        valorTexto.setAttribute("class", "grafico-rotulo-valor");
        valorTexto.textContent = item.valorFormatado ?? item.valor;
        svg.appendChild(valorTexto);
    });

    container.appendChild(svg);
}

/* -------------------- Gráfico de barras horizontais comparativas -------------------- */
/**
 * Estilo "bullet chart": uma barra de fundo mais clara representando o
 * total (`valorPrincipal`) e, sobreposta, uma barra mais estreita e em
 * cor sólida representando a parte (`valorSecundario`) — usado para
 * comparar "emitidas x atendidas" por empresa numa única linha por
 * categoria, sem precisar de duas barras lado a lado.
 * @param {{rotulo: string, valorPrincipal: number, valorSecundario: number}[]} dados
 */
function renderizarGraficoBarrasComparativas(container, dados, opcoes = {}) {
    container.innerHTML = "";
    if (!dados.length) {
        mostrarGraficoVazio(container, opcoes.mensagemVazio);
        return;
    }

    const corPrincipal = opcoes.corPrincipal || "var(--cor-cinza-borda)";
    const corSecundaria = opcoes.corSecundaria || "var(--cor-principal)";
    const alturaLinha = 34;
    const largura = 720;
    const altura = dados.length * alturaLinha + 12;
    const margemEsquerda = opcoes.margemRotulo || 150;
    const margemDireita = 70;
    const areaLargura = largura - margemEsquerda - margemDireita;

    const valorMaximo = Math.max(1, ...dados.map((item) => item.valorPrincipal));
    const svg = criarSvg(largura, altura);

    dados.forEach((item, indice) => {
        const y = indice * alturaLinha + 6;
        const alturaBarraFundo = 16;
        const alturaBarraFrente = 8;
        const larguraFundo = (areaLargura * item.valorPrincipal) / valorMaximo;
        const larguraFrente = (areaLargura * item.valorSecundario) / valorMaximo;

        const rotulo = elementoSvg("text", { x: margemEsquerda - 8, y: y + alturaBarraFundo / 2 + 4, "text-anchor": "end" });
        rotulo.setAttribute("class", "grafico-rotulo-eixo");
        rotulo.textContent = item.rotulo;
        svg.appendChild(rotulo);

        const fundo = elementoSvg("rect", {
            x: margemEsquerda, y, width: Math.max(2, larguraFundo), height: alturaBarraFundo, fill: corPrincipal, rx: "3",
        });
        const tituloFundo = elementoSvg("title", {});
        tituloFundo.textContent = `${item.rotulo} — Emitidas: ${item.valorPrincipal}`;
        fundo.appendChild(tituloFundo);
        svg.appendChild(fundo);

        const frente = elementoSvg("rect", {
            x: margemEsquerda, y: y + (alturaBarraFundo - alturaBarraFrente) / 2,
            width: Math.max(2, larguraFrente), height: alturaBarraFrente, fill: corSecundaria, rx: "3",
        });
        const tituloFrente = elementoSvg("title", {});
        tituloFrente.textContent = `${item.rotulo} — Atendidas: ${item.valorSecundario}`;
        frente.appendChild(tituloFrente);
        svg.appendChild(frente);

        const valorTexto = elementoSvg("text", { x: margemEsquerda + larguraFundo + 6, y: y + alturaBarraFundo / 2 + 4 });
        valorTexto.setAttribute("class", "grafico-rotulo-valor");
        valorTexto.textContent = `${item.valorSecundario}/${item.valorPrincipal}`;
        svg.appendChild(valorTexto);
    });

    container.appendChild(svg);
    container.appendChild(
        criarLegenda([
            { nome: "Emitidas", cor: corPrincipal },
            { nome: "Atendidas", cor: corSecundaria },
        ])
    );
}

/* -------------------- Gráfico de rosca (parte-do-todo) -------------------- */
/** @param {{rotulo: string, valor: number, cor: string}[]} dados */
function renderizarGraficoRosca(container, dados, opcoes = {}) {
    container.innerHTML = "";
    const total = dados.reduce((soma, item) => soma + item.valor, 0);
    if (total === 0) {
        mostrarGraficoVazio(container, opcoes.mensagemVazio);
        return;
    }

    const tamanho = 200;
    const centro = tamanho / 2;
    const raio = 70;
    const raioInterno = 42;
    const circunferencia = 2 * Math.PI * raio;

    const svg = criarSvg(tamanho, tamanho);
    let acumulado = 0;

    dados.forEach((item) => {
        if (!item.valor) {
            return;
        }
        const fracao = item.valor / total;
        const comprimento = circunferencia * fracao;
        const circulo = elementoSvg("circle", {
            cx: centro, cy: centro, r: raio, fill: "none",
            stroke: item.cor, "stroke-width": raio - raioInterno,
            "stroke-dasharray": `${comprimento} ${circunferencia - comprimento}`,
            "stroke-dashoffset": String(-acumulado),
            transform: `rotate(-90 ${centro} ${centro})`,
        });
        const titulo = elementoSvg("title", {});
        titulo.textContent = `${item.rotulo}: ${item.valor} (${Math.round(fracao * 100)}%)`;
        circulo.appendChild(titulo);
        svg.appendChild(circulo);
        acumulado += comprimento;
    });

    const textoTotal = elementoSvg("text", { x: centro, y: centro - 2, "text-anchor": "middle" });
    textoTotal.setAttribute("class", "grafico-rosca-total");
    textoTotal.textContent = String(total);
    svg.appendChild(textoTotal);

    const textoLabel = elementoSvg("text", { x: centro, y: centro + 16, "text-anchor": "middle" });
    textoLabel.setAttribute("class", "grafico-rosca-legenda-central");
    textoLabel.textContent = "total";
    svg.appendChild(textoLabel);

    const wrapper = document.createElement("div");
    wrapper.className = "grafico-rosca-wrapper";
    wrapper.appendChild(svg);
    wrapper.appendChild(criarLegenda(dados.map((item) => ({ nome: `${item.rotulo} (${item.valor})`, cor: item.cor }))));
    container.appendChild(wrapper);
}
