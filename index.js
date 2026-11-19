
const SECRET = process.env.CENTRAL_WEBHOOK_SECRET;
const DB_PATH = "./data/xauusd.db";

function responder(status, dados) {
  return {
    statusCode: status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store"
    },
    body: JSON.stringify(dados)
  };
}

function autorizado(event) {
  const headers = event.headers || {};
  const chave =
    headers["x-central-secret"] ||
    headers["X-Central-Secret"];

  return Boolean(SECRET && chave && chave === SECRET);
}

async function consultarBanco(event) {
  if (!autorizado(event)) {
    return responder(401, {
      sucesso: false,
      erro: "Acesso não autorizado."
    });
  }

  const tabela = event.queryStringParameters?.tabela;

  const tabelasPermitidas = [
    "m5",
    "m15",
    "h1",
    "indicadores",
    "tabuleiro",
    "sinais_validados",
    "sinais_negados",
    "ranking",
    "estatisticas_regioes",
    "estatisticas_transicoes"
  ];

  if (!tabelasPermitidas.includes(tabela)) {
    return responder(400, {
      sucesso: false,
      erro: "Tabela não permitida."
    });
  }

  return responder(501, {
    sucesso: false,
    banco: DB_PATH,
    tabela,
    mensagem:
      "Porta criada. O adaptador de leitura SQLite será conectado na próxima etapa."
  });
}

exports.handler = async (event) => {
  const metodo = event.httpMethod;

  if (metodo !== "GET") {
    return responder(405, {
      sucesso: false,
      erro: "Método não permitido."
    });
  }

  return consultarBanco(event);
};
