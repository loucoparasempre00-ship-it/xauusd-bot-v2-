/**
 * Memória conversacional persistente e exclusiva do Gestor XAUUSD.
 *
 * Utiliza o D1 já existente no Worker por meio do binding GESTOR_DB.
 * Não cria outro banco e não acessa os dados/memórias do Gemini Sinal.
 */

function obterBanco(env) {
  if (!env?.GESTOR_DB) {
    throw new Error("Binding GESTOR_DB não encontrado no Worker do Gestor.");
  }
  return env.GESTOR_DB;
}

function normalizarTexto(valor, limite = 12000) {
  return String(valor ?? "").trim().slice(0, limite);
}

export async function recuperarMemoriaGestor(chatId, env) {
  if (chatId === undefined || chatId === null || String(chatId).trim() === "") {
    throw new Error("Identificador da conversa não informado.");
  }

  const db = obterBanco(env);
  const id = String(chatId);

  const conversa = await db.prepare(
    `SELECT contexto_json
       FROM conversas
      WHERE chat_id = ?`
  ).bind(id).first();

  const registros = await db.prepare(
    `SELECT papel, conteudo, modelo, criada_em
       FROM mensagens
      WHERE chat_id = ?
      ORDER BY id DESC
      LIMIT 30`
  ).bind(id).all();

  let contexto = {};
  if (conversa?.contexto_json) {
    try {
      const salvo = JSON.parse(conversa.contexto_json);
      if (salvo && typeof salvo === "object" && !Array.isArray(salvo)) {
        contexto = salvo;
      }
    } catch {
      console.warn("Contexto salvo da conversa inválido; iniciando contexto vazio.");
    }
  }

  const historico = (registros?.results || [])
    .slice()
    .reverse()
    .map(item => ({
      role: item.papel === "assistant" || item.papel === "model"
        ? "model"
        : "user",
      text: normalizarTexto(item.conteudo),
      modelo: item.modelo || null,
      data: item.criada_em || null
    }))
    .filter(item => item.text);

  return { contexto, historico };
}

export async function salvarMemoriaGestor(chatId, dados = {}, env) {
  if (chatId === undefined || chatId === null || String(chatId).trim() === "") {
    throw new Error("Identificador da conversa não informado.");
  }

  const usuario = normalizarTexto(dados.usuario);
  const assistente = normalizarTexto(dados.assistente);
  if (!usuario || !assistente) {
    throw new Error("É necessário salvar a mensagem do usuário e a resposta do Gestor.");
  }

  const db = obterBanco(env);
  const id = String(chatId);
  const agora = new Date().toISOString();

  // A tabela conversas e a tabela mensagens já pertencem ao D1 gestor-xauusd-db.
  await db.prepare(
    `INSERT INTO conversas
       (chat_id, contexto_json, criada_em, atualizada_em)
     VALUES (?, '{}', ?, ?)
     ON CONFLICT(chat_id)
     DO UPDATE SET atualizada_em = excluded.atualizada_em`
  ).bind(id, agora, agora).run();

  await db.prepare(
    `INSERT INTO mensagens
       (chat_id, papel, conteudo, modelo, criada_em)
     VALUES (?, 'user', ?, NULL, ?)`
  ).bind(id, usuario, agora).run();

  await db.prepare(
    `INSERT INTO mensagens
       (chat_id, papel, conteudo, modelo, criada_em)
     VALUES (?, 'assistant', ?, ?, ?)`
  ).bind(id, assistente, dados.modelo || null, agora).run();

  return { sucesso: true, chat_id: id };
}
