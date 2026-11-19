/**
 * Inteligência exclusiva do Gemini Gestor XAUUSD.
 *
 * Não importa módulos da Central nem do Gemini Sinal.
 * A chave da API pode ser compartilhada como credencial técnica, mas o
 * prompt, histórico, contexto e seleção de modelo são próprios do Gestor.
 */

const API = "https://generativelanguage.googleapis.com/v1beta/models/";

export const INSTRUCAO_GEMINI_GESTOR = `
Você é o cérebro de inteligência do Gestor XAUUSD.

Sua função é conversar com o usuário e ajudá-lo a compreender o mercado
de ouro (XAUUSD), acompanhar operações abertas e avaliar riscos.

REGRAS FUNDAMENTAIS:
1. Converse naturalmente, como um analista de mercado.
2. Analise o contexto recebido antes de responder.
3. Considere histórico de mercado e operações quando disponíveis.
4. Explique os motivos das suas avaliações.
5. Seja adaptável; não fique preso a padrões rígidos.
6. Não invente preços, indicadores, horários ou informações ausentes.
7. Se faltarem dados, informe claramente.
8. Diferencie fatos, hipóteses e probabilidades.
9. Considere o risco financeiro em todas as recomendações.
10. Nunca execute ordens financeiras nem acione corretoras.
11. Qualquer ação operacional depende de confirmação manual do usuário.
12. Responda em português brasileiro.

Você pode conversar livremente sobre o mercado, explicar movimentos,
avaliar cenários e discutir operações.

O Gestor acompanha a carteira, o risco e as operações. Você fornece
inteligência e recomendações, podendo sugerir manter uma operação,
proteger o stop, realizar parcial ou encerrar, sempre explicando os motivos.

Use somente os dados presentes no contexto. Não confunda timeframes.
Quando houver horário de atualização, considere a atualidade dos dados.
Não afirme que consultou o banco oficial se os dados não foram fornecidos.
`;

const STATUS_COM_FALLBACK = new Set([408, 429, 500, 502, 503, 504]);

function normalizarModelo(valor) {
  if (typeof valor !== "string") return "";
  return valor.trim().replace(/^models\//, "");
}

function obterModelos(env) {
  const modelos = [];

  // Lista opcional e exclusiva do Gestor, definida na configuração do Worker.
  if (typeof env?.GESTOR_GEMINI_MODELS_JSON === "string") {
    try {
      const lista = JSON.parse(env.GESTOR_GEMINI_MODELS_JSON);
      if (Array.isArray(lista)) {
        for (const item of lista) {
          const modelo = normalizarModelo(
            typeof item === "string" ? item : item?.modelo
          );
          if (modelo) modelos.push(modelo);
        }
      }
    } catch {
      console.warn("GESTOR_GEMINI_MODELS_JSON inválido; usando modelo principal.");
    }
  }

  const principal = normalizarModelo(
    env?.GESTOR_GEMINI_MODEL || env?.GEMINI_MODEL || "gemini-3.1-flash-lite"
  );
  if (principal) modelos.unshift(principal);

  return [...new Set(modelos)].slice(0, 5);
}

function prepararHistorico(historico) {
  if (!Array.isArray(historico)) return [];

  return historico.slice(-30).map(item => {
    const role = item?.role === "model" || item?.role === "assistant"
      ? "model"
      : "user";

    let texto = item?.text || item?.conteudo || "";
    if (!texto && Array.isArray(item?.parts)) {
      texto = item.parts
        .filter(parte => typeof parte?.text === "string")
        .map(parte => parte.text)
        .join("\n");
    }

    if (typeof texto !== "string" || !texto.trim()) return null;
    return { role, parts: [{ text: texto.trim().slice(0, 12000) }] };
  }).filter(Boolean);
}

function prepararContexto(contexto) {
  if (contexto == null) return "";
  if (typeof contexto === "string") return contexto.slice(0, 30000);

  try {
    return JSON.stringify(contexto).slice(0, 30000);
  } catch {
    throw new Error("Não foi possível preparar o contexto do Gemini Gestor.");
  }
}

async function consultarModelo({ modelo, mensagem, contexto, historico, instrucao, chave }) {
  const url = `${API}${encodeURIComponent(modelo)}:generateContent`;
  const texto = contexto
    ? `CONTEXTO DISPONÍVEL DO GESTOR XAUUSD:\n${contexto}\n\nMENSAGEM DO USUÁRIO:\n${mensagem}`
    : mensagem;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-goog-api-key": chave
    },
    body: JSON.stringify({
      systemInstruction: { parts: [{ text: instrucao }] },
      contents: [
        ...historico,
        { role: "user", parts: [{ text: texto }] }
      ],
      generationConfig: {
        temperature: 0.7,
        maxOutputTokens: 2048
      }
    }),
    signal: AbortSignal.timeout(60000)
  });

  let dados;
  try {
    dados = await response.json();
  } catch {
    const erro = new Error("Resposta inválida da API Gemini.");
    erro.status = response.status;
    throw erro;
  }

  if (!response.ok) {
    const erro = new Error(
      dados?.error?.message || `Gemini respondeu HTTP ${response.status}.`
    );
    erro.status = response.status;
    throw erro;
  }

  const candidato = dados?.candidates?.[0];
  const resposta = (candidato?.content?.parts || [])
    .filter(parte => typeof parte?.text === "string")
    .map(parte => parte.text)
    .join("\n")
    .trim();

  if (!resposta) {
    const erro = new Error("Gemini Gestor não retornou uma resposta utilizável.");
    erro.status = 503;
    throw erro;
  }

  return {
    sucesso: true,
    texto: resposta,
    modelo,
    modelo_utilizado: modelo,
    latencia_ms: null,
    motivo_finalizacao: candidato?.finishReason || null,
    uso: dados?.usageMetadata || null,
    data: new Date().toISOString()
  };
}

export async function consultarGeminiGestor(
  { mensagem, contexto = null, historico = [], instrucaoSistema = "" } = {},
  env
) {
  const chave = env?.GESTOR_GEMINI_API_KEY || env?.GEMINI_API_KEY;
  if (!chave) {
    throw new Error("GESTOR_GEMINI_API_KEY ou GEMINI_API_KEY não configurada.");
  }

  if (typeof mensagem !== "string" || !mensagem.trim()) {
    throw new Error("Mensagem para Gemini Gestor não informada.");
  }

  const modelos = obterModelos(env);
  if (!modelos.length) throw new Error("Nenhum modelo Gemini Gestor configurado.");

  const contextoTexto = prepararContexto(contexto);
  const historicoPreparado = prepararHistorico(historico);
  const instrucao = [INSTRUCAO_GEMINI_GESTOR, instrucaoSistema]
    .filter(Boolean)
    .join("\n\n");

  let ultimoErro = null;

  for (let i = 0; i < modelos.length; i++) {
    const modelo = modelos[i];
    try {
      const inicio = Date.now();
      const resultado = await consultarModelo({
        modelo,
        mensagem: mensagem.trim(),
        contexto: contextoTexto,
        historico: historicoPreparado,
        instrucao,
        chave
      });

      return {
        ...resultado,
        latencia_ms: Date.now() - inicio,
        modelo_principal_selecionado: modelos[0],
        utilizou_reserva: i > 0,
        tentativas: i + 1,
        origem_selecao: env?.GESTOR_GEMINI_MODELS_JSON
          ? "configuracao_modelos_gestor"
          : "configuracao_worker_gestor"
      };
    } catch (erro) {
      ultimoErro = erro;
      const podeTentar = erro.status === undefined || STATUS_COM_FALLBACK.has(erro.status);
      if (!podeTentar || i === modelos.length - 1) break;
    }
  }

  const erroFinal = new Error(
    ultimoErro?.message || "Nenhum modelo conseguiu responder ao Gemini Gestor."
  );
  erroFinal.status = ultimoErro?.status || 503;
  throw erroFinal;
}
