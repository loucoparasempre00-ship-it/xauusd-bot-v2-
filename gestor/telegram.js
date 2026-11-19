/**
 * Comunicação exclusiva do canal Telegram do Gestor.
 *
 * Não compartilha token, segredo ou chat padrão com o canal da Central.
 * Configuração esperada:
 * GESTOR_TELEGRAM_BOT_TOKEN
 * GESTOR_TELEGRAM_WEBHOOK_SECRET
 * GESTOR_TELEGRAM_ALLOWED_USER_IDS (opcional, IDs separados por vírgula)
 * GESTOR_TELEGRAM_CHAT_ID (somente para avisos iniciados pelo Gestor)
 */

const TELEGRAM_API = "https://api.telegram.org";
const LIMITE_MENSAGEM = 4000;
const mensagensProcessadas = new Map();

function resposta(dados, status = 200) {
  return new Response(JSON.stringify(dados), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" }
  });
}

function dividirMensagem(texto, limite = LIMITE_MENSAGEM) {
  const partes = [];
  let restante = String(texto || "");

  while (restante.length > limite) {
    let corte = restante.lastIndexOf("\n", limite);
    if (corte < limite * 0.5) corte = restante.lastIndexOf(" ", limite);
    if (corte < limite * 0.5) corte = limite;
    partes.push(restante.slice(0, corte));
    restante = restante.slice(corte).trimStart();
  }

  if (restante) partes.push(restante);
  return partes;
}

function mensagemDuplicada(chave) {
  const agora = Date.now();

  for (const [id, tempo] of mensagensProcessadas) {
    if (agora - tempo > 600000) mensagensProcessadas.delete(id);
  }

  if (mensagensProcessadas.has(chave)) return true;
  mensagensProcessadas.set(chave, agora);
  return false;
}

async function chamarTelegram(env, metodo, dados) {
  const token = env.GESTOR_TELEGRAM_BOT_TOKEN;
  if (!token) throw new Error("GESTOR_TELEGRAM_BOT_TOKEN não configurado.");

  const response = await fetch(`${TELEGRAM_API}/bot${token}/${metodo}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dados)
  });

  let resultado;
  try {
    resultado = await response.json();
  } catch {
    throw new Error("Resposta inválida da API Telegram.");
  }

  if (!response.ok || !resultado.ok) {
    throw new Error(resultado.description || `Telegram HTTP ${response.status}`);
  }

  return resultado.result;
}

export async function enviarMensagemGestor(chatId, texto, env, opcoes = {}) {
  if (!chatId) throw new Error("Identificador da conversa não informado.");
  if (typeof texto !== "string" || !texto.trim()) {
    throw new Error("Mensagem vazia.");
  }

  const partes = dividirMensagem(texto.trim());
  const enviadas = [];

  for (let i = 0; i < partes.length; i++) {
    const dados = {
      chat_id: chatId,
      text: partes[i],
      disable_web_page_preview: true
    };

    if (i === 0 && opcoes.reply_to_message_id) {
      dados.reply_to_message_id = opcoes.reply_to_message_id;
    }
    if (opcoes.disable_notification === true) {
      dados.disable_notification = true;
    }

    enviadas.push(await chamarTelegram(env, "sendMessage", dados));
  }

  return { sucesso: true, quantidade: enviadas.length, mensagens: enviadas };
}

export async function responderMensagemGestor(evento, texto, env) {
  if (!evento?.conversa?.id) {
    throw new Error("Evento sem identificador de conversa.");
  }

  return enviarMensagemGestor(
    evento.conversa.id,
    texto,
    env,
    { reply_to_message_id: evento.mensagem_id }
  );
}

export async function enviarAvisoGestor(texto, env) {
  if (!env.GESTOR_TELEGRAM_CHAT_ID) {
    throw new Error("GESTOR_TELEGRAM_CHAT_ID não configurado.");
  }
  return enviarMensagemGestor(env.GESTOR_TELEGRAM_CHAT_ID, texto, env);
}

/**
 * Webhook do canal conversacional do Gestor.
 * O callback processarEvento é fornecido pelo ponto de entrada do Worker.
 */
export async function receberWebhookGestor(request, env, ctx, processarEvento) {
  try {
    if (request.method !== "POST") {
      return resposta({ erro: "Método não permitido" }, 405);
    }

    const segredo = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (
      !env.GESTOR_TELEGRAM_WEBHOOK_SECRET ||
      segredo !== env.GESTOR_TELEGRAM_WEBHOOK_SECRET
    ) {
      return resposta({ erro: "Webhook do Gestor não autorizado" }, 401);
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return resposta({ erro: "JSON inválido" }, 400);
    }

    if (!update || typeof update.update_id !== "number") {
      return resposta({ erro: "Atualização inválida" }, 400);
    }

    const mensagem = update.message;
    if (!mensagem?.chat) {
      return resposta({ recebido: true, processado: false, motivo: "Atualização sem mensagem" });
    }

    if (typeof mensagem.text !== "string" || !mensagem.text.trim()) {
      return resposta({ recebido: true, processado: false, motivo: "Mensagem sem texto" });
    }

    const usuario = mensagem.from || {};
    if (env.GESTOR_TELEGRAM_ALLOWED_USER_IDS) {
      const permitidos = env.GESTOR_TELEGRAM_ALLOWED_USER_IDS
        .split(",").map(id => id.trim()).filter(Boolean);
      if (!permitidos.includes(String(usuario.id))) {
        return resposta({ erro: "Usuário não autorizado" }, 403);
      }
    }

    const evento = {
      origem: "telegram_gestor",
      tipo: "mensagem",
      update_id: update.update_id,
      mensagem_id: mensagem.message_id,
      usuario: {
        id: usuario.id ?? null,
        nome: usuario.first_name || "",
        sobrenome: usuario.last_name || "",
        username: usuario.username || ""
      },
      conversa: {
        id: mensagem.chat.id,
        tipo: mensagem.chat.type
      },
      texto: mensagem.text.trim(),
      data: mensagem.date || null
    };

    const chave = `${evento.conversa.id}:${evento.update_id}`;
    if (mensagemDuplicada(chave)) {
      return resposta({ recebido: true, duplicado: true });
    }

    if (typeof processarEvento !== "function") {
      return resposta({ erro: "Conversador do Gestor não conectado ao webhook" }, 503);
    }

    return await processarEvento(evento, env, ctx);
  } catch (erro) {
    console.error("Erro no webhook Telegram do Gestor:", erro?.message || erro);
    return resposta({ erro: "Falha ao processar mensagem do Gestor" }, 500);
  }
}
