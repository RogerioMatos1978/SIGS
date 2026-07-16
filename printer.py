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

FONTE_NOME = "Arial"
TAMANHO_FONTE_SENHA = 65     # Palavra "SENHA" + número
TAMANHO_FONTE_SAUDACAO = 45  # "Bom Dia.", "Boa Tarde.", "Boa Noite."
TAMANHO_FONTE_PADRAO = 35    # Demais textos (cabeçalho, data, hora, evento)

MARGEM_SUPERIOR_MM = 5
ESPACAMENTO_LINHA_MM = 3


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
    def _desenhar_texto_centralizado(hdc, texto: str, y: int, largura_pagina: int) -> int:
        """
        Desenha uma linha de texto horizontalmente centralizada em relação
        à largura real da página (obtida dinamicamente via
        GetDeviceCaps/HORZRES). Retorna a altura (em pixels) ocupada pela
        linha, para que o chamador posicione a próxima linha corretamente.
        """
        largura_texto, altura_texto = hdc.GetTextExtent(texto)
        x = max(0, (largura_pagina - largura_texto) // 2)
        hdc.TextOut(x, y, texto)
        return altura_texto

    def _desenhar_logo(self, hdc, caminho_logo: str, y: int, largura_pagina: int) -> int:
        """
        Desenha o logotipo do SENAI centralizado no topo do ticket,
        utilizando PIL para carregar a imagem e convertê-la em um bitmap
        do Windows compatível com o contexto de impressão.

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
        # 60% da largura da página, mantendo a proporção original.
        largura_maxima = int(largura_pagina * 0.6)
        proporcao = largura_maxima / imagem.width
        nova_largura = largura_maxima
        nova_altura = int(imagem.height * proporcao)
        imagem = imagem.resize((nova_largura, nova_altura))

        x = max(0, (largura_pagina - nova_largura) // 2)

        dib = ImageWin.Dib(imagem)
        dib.draw(hdc.GetHandleOutput(), (x, y, x + nova_largura, y + nova_altura))

        return nova_altura

    # -- Operação principal ----------------------------------------------------

    def imprimir_senha(
        self,
        numero: int,
        nome_evento: str,
        caminho_logo: Optional[str] = None,
    ) -> None:
        """
        Imprime fisicamente o ticket da senha na impressora configurada.

        Layout impresso (todo centralizado horizontalmente):

            ==========================
            [logotipo SENAI, se disponível]
            [nome_evento]
            SENHA 001            <- Arial 65
            Data
            Hora
            [Saudação]           <- Arial 45
            Bem-vindo ao SENAI.
            ==========================

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

            # Largura real da página, obtida dinamicamente — jamais fixa.
            largura_pagina = hdc.GetDeviceCaps(win32con.HORZRES)

            hdc.StartDoc(f"SIGS - Senha {numero_formatado}")
            hdc.StartPage()

            y = self._mm_para_pixels(hdc, MARGEM_SUPERIOR_MM, eixo="y")
            espacamento = self._mm_para_pixels(hdc, ESPACAMENTO_LINHA_MM, eixo="y")

            fonte_padrao = self._criar_fonte(hdc, TAMANHO_FONTE_PADRAO)
            fonte_senha = self._criar_fonte(hdc, TAMANHO_FONTE_SENHA, negrito=True)
            fonte_saudacao = self._criar_fonte(hdc, TAMANHO_FONTE_SAUDACAO)

            # Linha decorativa superior.
            hdc.SelectObject(fonte_padrao)
            y += self._desenhar_texto_centralizado(hdc, "=" * 26, y, largura_pagina) + espacamento

            # Logotipo (opcional).
            if caminho_logo:
                y += self._desenhar_logo(hdc, caminho_logo, y, largura_pagina) + espacamento

            # Nome do evento.
            hdc.SelectObject(fonte_padrao)
            y += self._desenhar_texto_centralizado(hdc, nome_evento, y, largura_pagina) + espacamento

            # "SENHA 001" em fonte grande.
            hdc.SelectObject(fonte_senha)
            y += (
                self._desenhar_texto_centralizado(
                    hdc, f"SENHA {numero_formatado}", y, largura_pagina
                )
                + espacamento
            )

            # Data e hora.
            hdc.SelectObject(fonte_padrao)
            y += (
                self._desenhar_texto_centralizado(
                    hdc, agora.strftime("%d/%m/%Y"), y, largura_pagina
                )
                + espacamento
            )
            y += (
                self._desenhar_texto_centralizado(
                    hdc, agora.strftime("%H:%M:%S"), y, largura_pagina
                )
                + espacamento
            )

            # Saudação de acordo com o horário.
            hdc.SelectObject(fonte_saudacao)
            y += self._desenhar_texto_centralizado(hdc, saudacao, y, largura_pagina) + espacamento

            # Mensagem de boas-vindas.
            hdc.SelectObject(fonte_padrao)
            y += (
                self._desenhar_texto_centralizado(
                    hdc, "Bem-vindo ao SENAI.", y, largura_pagina
                )
                + espacamento
            )

            # Linha decorativa inferior.
            y += self._desenhar_texto_centralizado(hdc, "=" * 26, y, largura_pagina)

            hdc.EndPage()
            hdc.EndDoc()

            logger.info(
                "Ticket impresso com sucesso: senha %s na impressora '%s'.",
                numero_formatado,
                nome_impressora,
            )

        except Exception as erro:
            logger.error("Falha ao imprimir senha %s: %s", numero_formatado, erro)
            raise ErroImpressora(f"Falha ao imprimir o ticket: {erro}") from erro

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
