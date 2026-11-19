/**
 * GESTOR XAUUSD
 * Organiza o contexto recebido da Central.
 *
 * Não coleta preços, não recalcula indicadores e não cria
 * cópias de bancos. Apenas normaliza a estrutura recebida.
 */

export function organizarMercado(contexto) {
  if (
    !contexto ||
    typeof contexto !== "object" ||
    Array.isArray(contexto)
  ) {
    throw new Error("Contexto de mercado inválido.");
  }

  const indicadores =
    contexto.indicadores &&
    typeof contexto.indicadores === "object" &&
    !Array.isArray(contexto.indicadores)
      ? contexto.indicadores
      : {};

  return {
    timeframe: contexto.timeframe ?? null,
    ultima_atualizacao: contexto.ultima_atualizacao ?? null,
    velas: Array.isArray(contexto.velas) ? contexto.velas : [],
    indicadores,
    tendencias: contexto.tendencias ?? contexto.tendencia ?? null,
    bordas: Array.isArray(contexto.bordas) ? contexto.bordas : [],
    regioes: Array.isArray(contexto.regioes) ? contexto.regioes : [],
    origem: "CENTRAL_XAUUSD"
  };
}
