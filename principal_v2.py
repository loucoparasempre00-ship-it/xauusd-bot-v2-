"""
Coordenador inicial do XAUUSD V2.
Nesta etapa, nao executa o fluxo operacional.
"""

import configuracao_v2


MODULOS_PROJETO = [
    "leitura_mercado",
    "integrar_leitura",
    "validador_sinais_ia",
    "agente_macro",
    "decisor_final",
    "gestao_sinais",
    "acompanhamento_resultados",
    "formatador_mensagem",
    "notificador_telegram",
]


def mostrar_status():
    print("=== XAUUSD V2 ===")
    print(f"Ativo: {configuracao_v2.ATIVO}")
    print(f"Timeframe: {configuracao_v2.TIMEFRAME}")
    print(f"Stop ATR: {configuracao_v2.MULTIPLICADOR_STOP_ATR}")
    print(f"Risco/retorno: 1:{configuracao_v2.RISCO_RETORNO}")
    print(f"Envio Telegram habilitado: {configuracao_v2.ENVIAR_TELEGRAM}")
    print(f"Execucao de ordens habilitada: {configuracao_v2.EXECUTAR_ORDENS}")
    print("")
    print("Modulos previstos:")

    for nome in MODULOS_PROJETO:
        print(f"- {nome}")

    print("")
    print("Modo atual: ESTRUTURA, SEM OPERACAO.")
    print("Nenhum candle foi coletado.")
    print("Nenhum sinal foi gerado ou enviado.")


if __name__ == "__main__":
    mostrar_status()
