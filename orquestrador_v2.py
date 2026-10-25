"""
Orquestrador inicial da V2.

Organiza os módulos do projeto sem executar operações,
sem enviar ordens e sem disparar sinais no Telegram.
A integração completa será feita posteriormente.
"""


ETAPAS_V2 = [
    "coleta_de_candles",
    "filtro_de_candles_fechados",
    "monitor_saude",
    "leitura_mercado",
    "integrar_leitura",
    "padroes_estrutura",
    "gerador_candidatos",
    "validador_sinais_ia",
    "agente_macro",
    "decisor_final",
    "gestao_sinais",
    "acompanhamento_resultados",
    "formatador_mensagem",
    "notificador_telegram",
]


def consultar_fluxo():
    """Retorna as etapas previstas para a integração da V2."""
    return {
        "projeto": "XAUUSD Bot V2",
        "modo": "ESTRUTURA_PRELIMINAR",
        "execucao_automatica": False,
        "envio_telegram": False,
        "ordens_na_corretora": False,
        "etapas": ETAPAS_V2.copy(),
        "integracao_completa": False,
    }


if __name__ == "__main__":
    fluxo = consultar_fluxo()

    print(fluxo["projeto"])
    print("Modo:", fluxo["modo"])
    print("Etapas previstas:")

    for numero, etapa in enumerate(fluxo["etapas"], start=1):
        print(f"{numero}. {etapa}")

    print("Integração completa:", fluxo["integracao_completa"])
    print("Nenhuma ordem será executada.")
