/**
 * bip.js
 * ======
 * Módulo utilitário compartilhado responsável por gerar o sinal sonoro
 * ("bip") utilizando a Web Audio API nativa do navegador — nenhum arquivo
 * de áudio externo é necessário. Utilizado tanto pelo painel público
 * (a cada nova chamada/repetição) quanto pelo botão "Testar Bip" da tela
 * principal.
 */

/**
 * Toca um bip curto e agradável, composto por dois tons ascendentes,
 * simulando o som característico de painéis de senha profissionais.
 */
function tocarBip() {
    try {
        const ContextoAudio = window.AudioContext || window.webkitAudioContext;
        const contexto = new ContextoAudio();

        const tocarTom = (frequencia, inicio, duracao) => {
            const oscilador = contexto.createOscillator();
            const ganho = contexto.createGain();

            oscilador.type = "sine";
            oscilador.frequency.setValueAtTime(frequencia, contexto.currentTime + inicio);

            // Envelope simples de volume (fade-in/fade-out) para evitar
            // estalos (clicks) no início/fim do som.
            ganho.gain.setValueAtTime(0, contexto.currentTime + inicio);
            ganho.gain.linearRampToValueAtTime(0.4, contexto.currentTime + inicio + 0.02);
            ganho.gain.linearRampToValueAtTime(0, contexto.currentTime + inicio + duracao);

            oscilador.connect(ganho);
            ganho.connect(contexto.destination);

            oscilador.start(contexto.currentTime + inicio);
            oscilador.stop(contexto.currentTime + inicio + duracao);
        };

        // Dois tons ascendentes (típico "ding-dong" de painéis de senha).
        tocarTom(784.0, 0, 0.18);   // Nota G5
        tocarTom(1046.5, 0.18, 0.25); // Nota C6

        // Fecha o contexto de áudio após a reprodução, liberando recursos.
        setTimeout(() => contexto.close(), 600);
    } catch (erro) {
        console.error("Não foi possível reproduzir o bip:", erro);
    }
}
