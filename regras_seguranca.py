"""
Regras de segurança da V2.

Este módulo não executa ordens nem envia mensagens.
As verificações devem ser chamadas explicitamente
pelos componentes que serão integrados depois.
"""


def validar_candles(candles):
    """Verifica a estrutura básica de uma lista de candles."""
    if not isinstance(candles, list) or not candles:
        return False, "Lista de candles vazia ou inválida."

    campos = ("open", "high", "low", "close", "timestamp")

    for indice, candle in enumerate(candles):
        if not isinstance(candle, dict):
            return False, f"Candle {indice} não é um dicionário."

        if any(campo not in candle for campo in campos):
            return False, f"Candle {indice} possui campos ausentes."

        try:
            abertura = float(candle["open"])
            maxima = float(candle["high"])
            minima = float(candle["low"])
            fechamento = float(candle["close"])
        except (TypeError, ValueError):
            return False, f"Candle {indice} possui preços inválidos."

        precos = (abertura, maxima, minima, fechamento)

        if any(not (0 < preco < float("inf")) for preco in precos):
            return False, f"Candle {indice} possui preço não válido."

        if maxima < max(abertura, minima, fechamento):
            return False, f"Candle {indice} possui máxima inconsistente."

        if minima > min(abertura, fechamento):
            return False, f"Candle {indice} possui mínima inconsistente."

        if not isinstance(candle["timestamp"], str):
            return False, f"Candle {indice} possui timestamp inválido."

    return True, "Estrutura básica dos candles válida."


def validar_decisao(decisao):
    """Aceita apenas decisões explicitamente reconhecidas."""
    permitidas = {"APROVADO", "REJEITADO", "AGUARDAR_INTEGRACAO"}

    if not isinstance(decisao, str):
        return False, "Decisão inválida."

    if decisao.upper() not in permitidas:
        return False, "Decisão desconhecida."

    return True, "Decisão reconhecida."


def envio_telegram_permitido(decisao, habilitado=False):
    """
    Só autoriza o envio quando ele está explicitamente habilitado
    e a decisão é APROVADO.

    O valor padrão mantém o envio desativado.
    """
    if habilitado is not True:
        return False

    if not isinstance(decisao, str):
        return False

    return decisao.upper() == "APROVADO"


def execucao_ordens_permitida():
    """A V2 foi projetada sem execução de ordens."""
    return False


if __name__ == "__main__":
    print("Regras básicas de segurança carregadas.")
    print("Envio ao Telegram desativado por padrão.")
    print("Execução de ordens:", execucao_ordens_permitida())
