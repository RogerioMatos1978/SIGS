# -*- coding: utf-8 -*-
"""
printer.py
==========

Módulo responsável pela impressão física do ticket de senha, utilizando
exclusivamente as bibliotecas nativas do Windows (pywin32: ``win32print``
e ``win32ui``). Não é utilizado PDF em nenhum momento — a impressão é
enviada diretamente ao driver da impressora padrão (ou à impressora
configurada em Configurações), como uma sequência de comandos GDI.

Principais características exigidas pela especificação do SIGS:

    - Uso de ``win32print`` para localizar/abrir a impressora.
    - Uso de ``win32ui`` para criar o contexto de dispositivo (DC) e
      desenhar o conteúdo do ticket.
    - Centralização automática do texto calculada dinamicamente a partir
      de ``GetDeviceCaps()`` — a largura do papel NUNCA é um valor fixo,
      e sim obtida em tempo real do driver da impressora selecionada
      (suportando bobinas de 58mm, 80mm, A4 ou qualquer outra largura).
    - Fonte "SENHA" em Arial 65pt, saudação em Arial 45pt, demais textos
      em Arial 35pt, todos centralizados horizontalmente.

Este módulo só funciona em ambiente Windows com o pywin32 instalado. Em
outros sistemas operacionais (usados apenas para desenvolvimento/teste da
parte web), a importação é protegida e uma exceção clara é lançada apenas
no momento em que uma impressão é efetivamente solicitada — isso permite
que o restante da aplicação Flask continue funcionando normalmente para
fins de desenvolvimento e testes automatizados fora do Windows.
"""

from datetime import datetime
from typing import Optional

from config import logger

# ---------------------------------------------------------------------------
# Importação condicional das bibliotecas do Windows
# ---------------------------------------------------------------------------

try:
    import win32print
    import win32ui
    import win32con

    PYWIN32_DISPONIVEL = True
except ImportError:
    # pywin32 não está disponível (por exemplo, ambiente de desenvolvimento
    # em Linux/Mac). A impressão real só é possível em produção, no
    # Windows, com o pacote "pywin32" instalado (ver requirements.txt).
    PYWIN32_DISPONIVEL = False

# A biblioteca PIL (Pillow) é utilizada apenas para o desenho do logotipo
# no ticket (conversão de imagem para bitmap do Windows). É opcional: se
# não estiver instalada, ou se o logotipo não existir, a impressão segue
# normalmente, apenas sem a imagem.
try:
    from PIL import Image
    from PIL import ImageWin

    PIL_DISPONIVEL = True
except ImportError:
    PIL_DISPONIVEL = False


class ErroImpressora(Exception):
    """Exceção lançada para qualquer falha relacionada à impressão física
    do ticket (impressora indisponível, driver ausente, papel fora etc.)."""


# ---------------------------------------------------------------------------
# Constantes de layout do ticket (conforme especificação)
# ---------------------------------------------------------------------------

# Reduzidos em relação à versão original (65/45/35pt) — nesses tamanhos o
# ticket saía grande demais e gastando papel em excesso no rolo estreito
# da impressora térmica (58/80mm). A proporção entre os três tamanhos foi
# mantida (SENHA continua sendo o maior texto do ticket, seguido da
# saudação e por fim o texto padrão), só a escala geral diminuiu. Ajuste
# estes números se o ticket físico ainda sair grande/pequeno demais — o
# efeito só é visível numa impressão real, não há como simular aqui.
FONTE_NOME = "Arial"
TAMANHO_FONTE_SENHA = 28     # Palavra "SENHA" + número
TAMANHO_FONTE_SAUDACAO = 20  # "Bom Dia.", "Boa Tarde.", "Boa Noite."
TAMANHO_FONTE_PADRAO = 16    # Demais textos (cabeçalho, data, hora, evento)

MARGEM_SUPERIOR_MM = 2
ESPACAMENTO_LINHA_MM = 2


def obter_saudacao(momento: Optional[datetime] = None) -> str:
    """
    Retorna a saudação apropriada de acordo com o horário atual:
        - Antes das 12h: "Bom Dia."
        - Entre 12h e 18h: "Boa Tarde."
        - Após 18h: "Boa Noite."
    """
    momento = momento or datetime.now()
    hora = momento.hour

    if hora < 12:
        return "Bom Dia."
    if hora < 18:
        return "Boa Tarde."
    return "Boa Noite."


class ImpressoraTermica:
    """
    Encapsula toda a lógica de impressão do ticket de senha utilizando GDI
    do Windows via pywin32. Cada chamada a ``imprimir_senha`` abre e fecha
    um contexto de impressão próprio (não mantemos conexão persistente com
    a impressora, evitando travamentos entre impressões).
    """

    def __init__(self, nome_impressora: Optional[str] = None):
        """
        :param nome_impressora: nome exato da impressora, conforme cadastrada
            no Windows (Painel de Controle > Dispositivos e Impressoras).
            Se vazio ou None, será utilizada a impressora padrão do sistema.
        """
        self.nome_impressora = nome_impressora or None

    # -- Utilitários internos -------------------------------------------------

    def _resolver_nome_impressora(self) -> str:
        """Retorna o nome da impressora configurada ou, na ausência dela,
        a impressora padrão do Windows."""
        if not PYWIN32_DISPONIVEL:
            raise ErroImpressora(
                "pywin32 não está instalado ou o sistema operacional não é "
                "Windows. A impressão direta requer Windows com pywin32 "
                "(win32print/win32ui) instalado."
            )

        if self.nome_impressora:
            return self.nome_impressora

        try:
            return win32print.GetDefaultPrinter()
        except Exception as erro:
            raise ErroImpressora(
                "Nenhuma impressora padrão foi encontrada no Windows. "
                "Configure uma impressora padrão ou informe o nome da "
                "impressora na tela de Configurações."
            ) from erro

    @staticmethod
    def _mm_para_pixels(hdc, milimetros: float, eixo: str = "y") -> int:
        """
        Converte um valor em milímetros para pixels do dispositivo, usando
        a resolução real informada pelo driver (GetDeviceCaps), nunca um
        valor fixo. ``eixo`` pode ser 'x' ou 'y'.
        """
        if eixo == "x":
            pixels_totais = hdc.GetDeviceCaps(win32con.HORZRES)
            mm_totais = hdc.GetDeviceCaps(win32con.HORZSIZE)
        else:
            pixels_totais = hdc.GetDeviceCaps(win32con.VERTRES)
            mm_totais = hdc.GetDeviceCaps(win32con.VERTSIZE)

        if mm_totais == 0:
            return 0
        pixels_por_mm = pixels_totais / mm_totais
        return int(milimetros * pixels_por_mm)

    @staticmethod
    def _altura_fonte_em_pixels(hdc, tamanho_pt: int) -> int:
        """
        Converte um tamanho de fonte em pontos (pt) para a altura em
        pixels lógicos do dispositivo, com base no DPI vertical real
        (LOGPIXELSY) obtido via GetDeviceCaps — nunca um valor fixo.
        """
        dpi_vertical = hdc.GetDeviceCaps(win32con.LOGPIXELSY)
        # Convenção do Windows GDI: altura negativa refere-se à altura do
        # caractere (sem contar espaçamento interno), resultando em texto
        # mais fiel ao tamanho em pontos solicitado.
        return -int(round(tamanho_pt * dpi_vertical / 72))

    def _criar_fonte(self, hdc, tamanho_pt: int, negrito: bool = False):
        """Cria e retorna um objeto de fonte GDI (win32ui.Font)."""
        altura = self._altura_fonte_em_pixels(hdc, tamanho_pt)
        return win32ui.CreateFont(
            {
                "name": FONTE_NOME,
                "height": altura,
                "weight": 700 if negrito else 400,
            }
        )

    @staticmethod
    def _centro_fisico_da_pagina(hdc) -> int:
        """
        Calcula o centro horizontal do PAPEL FÍSICO, expresso no sistema de
        coordenadas usado por TextOut/DIB (que tem origem no canto superior
        esquerdo da ÁREA IMPRIMÍVEL, não do papel físico).

        Por que não basta usar ``HORZRES / 2``: ``HORZRES`` é a largura da
        área imprimível segundo o driver da impressora, que pode ser
        MAIOR ou MENOR que o papel realmente carregado (ex.: driver
        configurado para bobina de 80mm com uma bobina de 58mm instalada)
        e pode não estar centralizada dentro do papel físico
        (``PHYSICALOFFSETX`` — a margem não-imprimível à esquerda). Centrar
        só em cima de ``HORZRES`` nesses casos resulta em texto correto
        DENTRO da área imprimível, mas deslocado para um dos lados quando
        olhado no papel físico real — foi exatamente o problema relatado
        ("o texto está mais para a direita").

        Usando ``PHYSICALWIDTH`` (largura do papel físico) e
        ``PHYSICALOFFSETX`` (deslocamento da área imprimível em relação à
        borda esquerda do papel), chegamos ao centro verdadeiro do papel,
        já convertido para as coordenadas que TextOut/DIB entendem.
        """
        largura_fisica = hdc.GetDeviceCaps(win32con.PHYSICALWIDTH)
        deslocamento_x = hdc.GetDeviceCaps(win32con.PHYSICALOFFSETX)

        # Alguns drivers (sobretudo de impressora térmica) retornam 0 para
        # PHYSICALWIDTH quando não implementam essa capability — nesse
        # caso, caímos de volta para HORZRES (comportamento anterior),
        # que ao menos centraliza corretamente DENTRO da área imprimível.
        if largura_fisica <= 0:
            return hdc.GetDeviceCaps(win32con.HORZRES) // 2

        return (largura_fisica // 2) - deslocamento_x

    @staticmethod
    def _desenhar_texto_centralizado(hdc, texto: str, y: int, centro_pagina: int) -> int:
        """
        Desenha uma linha de texto horizontalmente centralizada em relação
        ao CENTRO FÍSICO REAL do papel (ver ``_centro_fisico_da_pagina`` —
        não apenas ao centro da área imprimível segundo o driver). Retorna
        a altura (em pixels) ocupada pela linha, para que o chamador
        posicione a próxima linha corretamente.
        """
        largura_texto, altura_texto = hdc.GetTextExtent(texto)
        x = max(0, centro_pagina - largura_texto // 2)
        hdc.TextOut(x, y, texto)
        return altura_texto

    def _desenhar_logo(self, hdc, caminho_logo: str, y: int, centro_pagina: int, largura_pagina: int) -> int:
        """
        Desenha o logotipo centralizado no topo do ticket, utilizando PIL
        para carregar a imagem e convertê-la em um bitmap do Windows
        compatível com o contexto de impressão.

        ``caminho_logo`` é o logo DA EMPRESA selecionada na emissão (ver
        app.py:api_emitir), não mais o logo padrão do sistema — cada
        empresa imprime com seu próprio logo (ou sem logo algum, se ainda
        não tiver um cadastrado; ver imprimir_senha).

        Retorna a altura ocupada pela imagem em pixels (0 se a imagem não
        puder ser carregada, para que a impressão continue normalmente).
        """
        if not PIL_DISPONIVEL:
            logger.warning("Pillow não instalado: logotipo não será impresso.")
            return 0

        try:
            imagem = Image.open(caminho_logo).convert("RGB")
        except (FileNotFoundError, OSError) as erro:
            logger.warning("Não foi possível carregar o logotipo '%s': %s", caminho_logo, erro)
            return 0

        # Redimensiona o logotipo proporcionalmente para ocupar no máximo
        # 40% da largura da página (reduzido de 60% — no rolo estreito da
        # impressora térmica, 60% deixava o logo desproporcionalmente
        # grande em relação ao restante do ticket, já reduzido acima).
        largura_maxima = int(largura_pagina * 0.4)
        proporcao = largura_maxima / imagem.width
        nova_largura = largura_maxima
        nova_altura = int(imagem.height * proporcao)
        imagem = imagem.resize((nova_largura, nova_altura))

        # Centralizado em relação ao CENTRO FÍSICO REAL do papel (mesmo
        # cálculo do texto — ver _centro_fisico_da_pagina), não apenas ao
        # centro da área imprimível segundo o driver.
        x = max(0, centro_pagina - nova_largura // 2)

        dib = ImageWin.Dib(imagem)
        dib.draw(hdc.GetHandleOutput(), (x, y, x + nova_largura, y + nova_altura))

        return nova_altura

    # -- Operação principal ----------------------------------------------------

    def imprimir_senha(
        self,
        numero: int,
        nome_evento: str,
        caminho_logo: Optional[str] = None,
        nome_empresa: Optional[str] = None,
        reimpressao: bool = False,
        nome_pessoa: Optional[str] = None,
    ) -> None:
        """
        Imprime fisicamente o ticket da senha na impressora configurada.

        Layout impresso (todo centralizado horizontalmente):

            ==========================
            [logotipo da EMPRESA, se ela tiver um cadastrado]
            [nome_evento]
            SENHA 001            <- Arial 28, negrito
            [REIMPRESSO]         <- só quando reimpressao=True, negrito
            [nome_pessoa]        <- "Primeiro Nome" opcional digitado na emissão
            [nome_empresa]       <- empresa selecionada na emissão
            Data
            Hora
            [Saudação]           <- Arial 20
            Bem-vindo ao SENAI.
            ==========================

        ``caminho_logo`` é o logo DA EMPRESA selecionada na emissão
        (``empresa.logo_path``, resolvido em app.py:api_emitir), não mais
        o logo padrão do sistema (config.logo_path) — cada ticket é
        impresso com o logo da própria empresa. Se a empresa não tiver
        logo cadastrado, o ticket sai sem nenhum logo (sem fallback para
        o logo do sistema, propositalmente).

        ``nome_empresa`` é a empresa do feirão selecionada obrigatoriamente
        no momento da emissão (ver app.py:api_emitir). Se omitido (ex.:
        chamada direta desta função fora do fluxo normal da API), a linha
        simplesmente não é impressa.

        ``reimpressao=True`` imprime a palavra "REIMPRESSO" logo abaixo do
        número da senha, para deixar claro (a quem chamar/atender essa
        senha) que este ticket físico é uma SEGUNDA via de uma senha já
        emitida antes — não uma nova senha (ver app.py:api_reimprimir, que
        só permite reimprimir enquanto a senha ainda está com status
        'Emitida', nunca depois de chamada/finalizada/cancelada).

        ``nome_pessoa`` é o "Primeiro Nome" OPCIONAL digitado livremente
        pelo Emissor no momento da emissão (ver app.py:api_emitir) — ao
        contrário de ``nome_empresa``, nunca é obrigatório. Se vazio/None,
        a linha simplesmente não é impressa.

        Lança ``ErroImpressora`` em caso de qualquer falha de impressão.
        """
        if not PYWIN32_DISPONIVEL:
            raise ErroImpressora(
                "Impressão indisponível: este ambiente não possui pywin32. "
                "Execute o sistema em um Windows com pywin32 instalado."
            )

        nome_impressora = self._resolver_nome_impressora()
        agora = datetime.now()
        numero_formatado = f"{numero:03d}"
        saudacao = obter_saudacao(agora)

        hdc = None
        try:
            hdc = win32ui.CreateDC()
            hdc.CreatePrinterDC(nome_impressora)

            # Largura da ÁREA IMPRIMÍVEL (usada só para o limite de 40% do
            # logo) e centro do PAPEL FÍSICO real (usado para toda a
            # centralização — ver _centro_fisico_da_pagina), ambos obtidos
            # dinamicamente — jamais fixos.
            largura_pagina = hdc.GetDeviceCaps(win32con.HORZRES)
            centro_pagina = self._centro_fisico_da_pagina(hdc)

            titulo_trabalho = f"SIGS - Senha {numero_formatado}" + (" (reimpressão)" if reimpressao else "")
            hdc.StartDoc(titulo_trabalho)
            hdc.StartPage()

            y = self._mm_para_pixels(hdc, MARGEM_SUPERIOR_MM, eixo="y")
            espacamento = self._mm_para_pixels(hdc, ESPACAMENTO_LINHA_MM, eixo="y")

            fonte_padrao = self._criar_fonte(hdc, TAMANHO_FONTE_PADRAO)
            fonte_padrao_negrito = self._criar_fonte(hdc, TAMANHO_FONTE_PADRAO, negrito=True)
            fonte_senha = self._criar_fonte(hdc, TAMANHO_FONTE_SENHA, negrito=True)
            fonte_saudacao = self._criar_fonte(hdc, TAMANHO_FONTE_SAUDACAO)

            # Linha decorativa superior.
            hdc.SelectObject(fonte_padrao)
            y += self._desenhar_texto_centralizado(hdc, "=" * 26, y, centro_pagina) + espacamento

            # Logotipo (opcional).
            if caminho_logo:
                y += self._desenhar_logo(hdc, caminho_logo, y, centro_pagina, largura_pagina) + espacamento

            # Nome do evento.
            hdc.SelectObject(fonte_padrao)
            y += self._desenhar_texto_centralizado(hdc, nome_evento, y, centro_pagina) + espacamento

            # "SENHA 001" em fonte grande.
            hdc.SelectObject(fonte_senha)
            y += (
                self._desenhar_texto_centralizado(
                    hdc, f"SENHA {numero_formatado}", y, centro_pagina
                )
                + espacamento
            )

            # Marca de REIMPRESSÃO (segunda via de uma senha já emitida
            # antes) — só aparece quando reimpressao=True (ver
            # app.py:api_reimprimir). Em negrito para chamar atenção de
            # quem for atender, evitando confundir com uma senha nova.
            if reimpressao:
                hdc.SelectObject(fonte_padrao_negrito)
                y += (
                    self._desenhar_texto_centralizado(hdc, "REIMPRESSO", y, centro_pagina)
                    + espacamento
                )

            # "Primeiro Nome" digitado OPCIONALMENTE pelo Emissor na
            # emissão. Prefixado com o rótulo "Nome:" pelo mesmo motivo do
            # rótulo "Empresa:" logo abaixo — deixar claro do que se trata
            # esse texto no ticket.
            if nome_pessoa:
                hdc.SelectObject(fonte_padrao)
                y += (
                    self._desenhar_texto_centralizado(
                        hdc, f"Nome: {nome_pessoa}", y, centro_pagina
                    )
                    + espacamento
                )

            # Empresa selecionada no momento da emissão (feirão do emprego).
            # Prefixada com o rótulo "Empresa:" para deixar claro do que se
            # trata esse texto no ticket (sem o rótulo, o nome aparecia
            # "solto" logo abaixo de "SENHA 001", podendo ser confundido
            # com parte do nome do evento por quem lê o cupom).
            if nome_empresa:
                hdc.SelectObject(fonte_padrao)
                y += (
                    self._desenhar_texto_centralizado(
                        hdc, f"Empresa: {nome_empresa}", y, centro_pagina
                    )
                    + espacamento
                )

            # Data e hora.
            hdc.SelectObject(fonte_padrao)
            y += (
                self._desenhar_texto_centralizado(
                    hdc, agora.strftime("%d/%m/%Y"), y, centro_pagina
                )
                + espacamento
            )
            y += (
                self._desenhar_texto_centralizado(
                    hdc, agora.strftime("%H:%M:%S"), y, centro_pagina
                )
                + espacamento
            )

            # Saudação de acordo com o horário.
            hdc.SelectObject(fonte_saudacao)
            y += self._desenhar_texto_centralizado(hdc, saudacao, y, centro_pagina) + espacamento

            # Mensagem de boas-vindas.
            hdc.SelectObject(fonte_padrao)
            y += (
                self._desenhar_texto_centralizado(
                    hdc, "Bem-vindo ao SENAI.", y, centro_pagina
                )
                + espacamento
            )

            # Linha decorativa inferior.
            y += self._desenhar_texto_centralizado(hdc, "=" * 26, y, centro_pagina)

            hdc.EndPage()
            hdc.EndDoc()

            logger.info(
                "Ticket %simpresso com sucesso: senha %s na impressora '%s'.",
                "(REIMPRESSÃO) " if reimpressao else "",
                numero_formatado,
                nome_impressora,
            )

        except Exception as erro:
            # Inclui o NOME DA IMPRESSORA usada nesta tentativa — sem isso,
            # um erro genérico do driver (ex.: "StartDoc failed") não dá
            # pista nenhuma de qual impressora falhou, dificultando o
            # diagnóstico quando há mais de uma impressora instalada na
            # estação ou quando o nome configurado não bate exatamente com
            # o nome real da impressora no Windows.
            logger.error(
                "Falha ao imprimir senha %s na impressora '%s': %s",
                numero_formatado, nome_impressora, erro,
            )
            raise ErroImpressora(
                f"Falha ao imprimir o ticket na impressora '{nome_impressora}': {erro}"
            ) from erro

        finally:
            if hdc is not None:
                try:
                    hdc.DeleteDC()
                except Exception:
                    # Ignora falhas ao liberar o contexto de dispositivo;
                    # o erro relevante (se houver) já foi tratado acima.
                    pass

    @staticmethod
    def listar_impressoras_instaladas() -> list:
        """
        Retorna a lista de nomes de impressoras instaladas no Windows,
        utilizada pela tela de Configurações para permitir a seleção da
        impressora desejada em um combo box, evitando erros de digitação.
        """
        if not PYWIN32_DISPONIVEL:
            return []

        impressoras = win32print.EnumPrinters(
            win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        )
        return [impressora[2] for impressora in impressoras]
