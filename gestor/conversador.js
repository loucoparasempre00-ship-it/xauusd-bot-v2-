/**
 * Conversador independente do Gemini Sinal e dos fluxos da Central.
 *
 * As dependências de inteligência, memória e contexto são injetadas pelo
 * ponto de entrada do Gestor. Assim, este módulo não importa o Gemini Sinal,
 * não dispara repository_dispatch e não consulta arquivos espelhados.
 */

import { responderMensagemGestor, enviarMensagemGestor } from "./telegram.js";
import { consultarGeminiGestor as consultarGeminiGestorPadrao } from "./gemini_gestor.js";
import { recuperarMemoriaGestor as recuperarMemoriaPadrao, salvarMemoriaGestor as salvarMemoriaPadrao } from "./memoria.js";

function resposta(dados, status = 200) {
  return new Response(JSON.stringify(dados), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" }
  });
}

export async function processarEventoGestor(evento, env, dependencias = {}) {
  let etapa = "validar evento";
  const chatId = evento?.conversa?.id;

  try {
    if (evento?.origem !== "telegram_gestor" || evento?.tipo !== "mensagem") {
      return resposta({ erro: "Evento não pertence ao canal conversacional do Gestor" }, 400);
    }

    if (!chatId || typeof evento.texto !== "string" || !evento.texto.trim()) {
      return resposta({ erro: "Mensagem incompleta" }, 400);
    }

    const {
      consultarGeminiGestor,
      recuperarMemoriaGestor,
      salvarMemoriaGestor,
      obterContextoGestor
    } = dependencias;

    const consultarInteligencia = typeof consultarGeminiGestor === "function"
      ? consultarGeminiGestor
      : consultarGeminiGestorPadrao;

    etapa = "recuperar memória do Gestor";
    const recuperarMemoria = typeof recuperarMemoriaGestor === "function"
      ? recuperarMemoriaGestor
      : recuperarMemoriaPadrao;
    const salvarMemoria = typeof salvarMemoriaGestor === "function"
      ? salvarMemoriaGestor
      : salvarMemoriaPadrao;
    const memoria = await recuperarMemoria(chatId, env);

    etapa = "obter contexto do Gestor";
    const contextoMercado = typeof obterContextoGestor === "function"
      ? await obterContextoGestor(evento, env)
      : null;

    const contexto = {
      sistema: "Gestor XAUUSD",
      contexto_mercado: contextoMercado,
      memoria: memoria?.contexto || {},
      regras: [
        "Você é o Gemini Gestor, independente do Gemini Sinal da Central.",
        "Converse e avalie o mercado usando somente dados disponíveis.",
        "Diferencie fatos, hipóteses e probabilidades.",
        "Pode recomendar manter, proteger stop, realizar parcial ou encerrar uma operação.",
        "Não execute ordens financeiras; qualquer ação depende de confirmação manual do usuário.",
        "Não altere nem gere sinais da Central."
      ]
    };

    etapa = "consultar Gemini Gestor";
    const resultado = await consultarInteligencia({
      mensagem: evento.texto.trim(),
      contexto,
      historico: Array.isArray(memoria?.historico) ? memoria.historico : []
    }, env);

    if (!resultado || typeof resultado.texto !== "string" || !resultado.texto.trim()) {
      throw new Error("Gemini Gestor não retornou uma resposta utilizável.");
    }

    etapa = "responder no Telegram do Gestor";
    await responderMensagemGestor(evento, resultado.texto, env);

    {
      etapa = "salvar memória do Gestor";
      await salvarMemoria(chatId, {
        usuario: evento.texto.trim(),
        assistente: resultado.texto,
        modelo: resultado.modelo_utilizado || resultado.modelo || null
      }, env);
    }

    return resposta({
      sucesso: true,
      canal: "telegram_gestor",
      modelo: resultado.modelo_utilizado || resultado.modelo || null,
      memoria_salva: true
    });
  } catch (erro) {
    console.error("Falha no conversador do Gestor:", etapa, erro?.message || erro);

    if (chatId) {
      try {
        await enviarMensagemGestor(
          chatId,
          `O Gestor não conseguiu concluir o processamento na etapa: ${etapa}.\nNenhuma ordem foi executada.`,
          env
        );
      } catch (erroTelegram) {
        console.error("Falha ao enviar diagnóstico:", erroTelegram?.message || erroTelegram);
      }
    }

    return resposta({ sucesso: false, etapa, erro: "Falha ao processar conversa do Gestor" }, 500);
  }
}
