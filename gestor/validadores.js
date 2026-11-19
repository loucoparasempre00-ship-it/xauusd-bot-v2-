/**
 * GESTOR XAUUSD
 * Validação dos pacotes recebidos da Central.
 *
 * Mantém as regras do módulo original do Gestor:
 * identificador obrigatório, pacote recente, sinal aprovado
 * e contexto de mercado estruturado.
 */

export function validarPacote(pacote, agora = Date.now()) {
  const problemas = [];

  if (
    !pacote ||
    typeof pacote !== "object" ||
    Array.isArray(pacote)
  ) {
    return {
      valido: false,
      problemas: ["Pacote não é um objeto válido."]
    };
  }

  if (
    typeof pacote.identificador !== "string" ||
    !pacote.identificador.trim()
  ) {
    problemas.push("Identificador ausente ou inválido.");
  }

  if (
    typeof pacote.gerado_em !== "string" ||
    !Number.isFinite(Date.parse(pacote.gerado_em))
  ) {
    problemas.push("Data de geração inválida.");
  } else {
    const idade = agora - Date.parse(pacote.gerado_em);

    if (idade > 15 * 60 * 1000) {
      problemas.push("Pacote com horário desatualizado.");
    }

    if (idade < -60 * 1000) {
      problemas.push("Horário de geração no futuro.");
    }
  }

  if (
    !pacote.sinal ||
    typeof pacote.sinal !== "object" ||
    Array.isArray(pacote.sinal)
  ) {
    problemas.push("Dados do sinal ausentes ou inválidos.");
  } else {
    const aprovado =
      pacote.sinal.aprovado === true ||
      pacote.sinal.status === "APROVADO";

    if (!aprovado) {
      problemas.push("O sinal não está aprovado.");
    }
  }

  if (
    !pacote.contexto_mercado ||
    typeof pacote.contexto_mercado !== "object" ||
    Array.isArray(pacote.contexto_mercado)
  ) {
    problemas.push("Contexto de mercado inválido.");
  }

  return {
    valido: problemas.length === 0,
    problemas
  };
}
