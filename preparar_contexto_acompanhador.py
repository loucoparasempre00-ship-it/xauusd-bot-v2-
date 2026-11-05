
import csv
import json
import math

from pathlib import Path
from datetime import datetime, timezone

from motor_contexto_xau import carregar_candles, analisar
from integrar_leitura import integrar_leitura


PASTA = Path("data")
ARQUIVO_TABULEIRO = PASTA / "contexto_tabuleiro_m5.json"
ARQUIVO_SAIDA = PASTA / "contexto_inteligente_acompanhador.json"


def numero_seguro(valor):
    try:
        valor = float(valor)
        if math.isfinite(valor):
            return round(valor, 5)
    except (TypeError, ValueError):
        pass

    return None


def resumir_estrutura(estrutura, preco):
    topos = estrutura.get("topos", [])
    fundos = estrutura.get("fundos", [])
    suportes = estrutura.get("suportes", [])
    resistencias = estrutura.get("resistencias", [])

    suportes = sorted(
        suportes,
        key=lambda x: abs(float(x["centro"]) - preco)
    )

    resistencias = sorted(
        resistencias,
        key=lambda x: abs(float(x["centro"]) - preco)
    )

    return {
        "tendencia_estrutural": estrutura.get(
            "tendencia", "INDEFINIDA"
        ),
        "ultimos_topos": topos[-6:],
        "ultimos_fundos": fundos[-6:],
        "suportes_proximos": suportes[:4],
        "resistencias_proximas": resistencias[:4],
        "rompimentos": estrutura.get("rompimentos", []),
        "observacao": estrutura.get("observacao", "")
    }


def resumir_matematica(candles):
    dados = [
        {
            "timestamp": c["tempo"].isoformat(),
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"]
        }
        for c in candles
    ]

    leitura = integrar_leitura(dados)

    indicadores = leitura.get("indicadores", {})

    return {
        "timeframe": leitura.get("timeframe"),
        "preco": numero_seguro(leitura.get("preco")),
        "indicadores": {
            "ema6": numero_seguro(indicadores.get("ema6")),
            "ema20": numero_seguro(indicadores.get("ema20")),
            "alinhamento_emas": indicadores.get(
                "alinhamento_emas"
            ),
            "macd_linha": numero_seguro(
                indicadores.get("macd_linha")
            ),
            "macd_sinal": numero_seguro(
                indicadores.get("macd_sinal")
            ),
            "macd_histograma": numero_seguro(
                indicadores.get("macd_histograma")
            ),
            "consolidacao": indicadores.get("consolidacao"),
            "vwap": indicadores.get("vwap"),
            "vwap_status": indicadores.get("vwap_status")
        },
        "estrutura": resumir_estrutura(
            leitura.get("estrutura", {}),
            float(leitura["preco"])
        ),
        "status": (
            "LEITURA_MATEMATICA_ESTRUTURAL"
        ),
        "decisao_final": leitura.get(
            "decisao_final",
            "PENDENTE_DE_VALIDACAO"
        ),
        "observacao": (
            "Esta leitura não é um sinal aprovado. "
            "É uma fonte independente de contexto."
        )
    }


def carregar_tabuleiro():
    if not ARQUIVO_TABULEIRO.exists():
        raise FileNotFoundError(
            "Tabuleiro estável não encontrado: "
            + str(ARQUIVO_TABULEIRO)
        )

    with ARQUIVO_TABULEIRO.open(
        "r", encoding="utf-8"
    ) as arquivo:
        return json.load(arquivo)


def validar_horarios(candle_tecnico, candle_tabuleiro):
    if not candle_tecnico or not candle_tabuleiro:
        return False

    try:
        data_tecnica = datetime.fromisoformat(
            candle_tecnico.replace("Z", "+00:00")
        )

        data_tabuleiro = datetime.fromisoformat(
            candle_tabuleiro.replace(" ", "T")
            + "+00:00"
        )

        return data_tecnica == data_tabuleiro

    except (ValueError, TypeError):
        return False


def main():
    print("Preparando contexto inteligente do Acompanhador...")

    candles = carregar_candles()

    if len(candles) < 100:
        raise RuntimeError(
            "Histórico insuficiente para análise."
        )

    # 1. Contexto técnico M5, M15 e H1.
    tecnico = analisar(candles)

    # 2. Leitura matemática e estrutural independente.
    matematico = resumir_matematica(candles)

    # 3. Tabuleiro de regiões estáveis.
    tabuleiro = carregar_tabuleiro()

    mercado = tabuleiro.get("mercado_atual", {})

    candle_tecnico = tecnico.get("candle_utc")
    candle_tabuleiro = mercado.get("timestamp")

    horarios_coincidem = validar_horarios(
        candle_tecnico,
        candle_tabuleiro
    )

    contexto = {
        "versao": "1.0",
        "gerado_utc": datetime.now(
            timezone.utc
        ).isoformat(),

        "ativo": "XAUUSD",

        "candle_utc": candle_tecnico,

        "preco_atual": numero_seguro(
            tecnico.get("preco")
        ),

        "validacao_temporal": {
            "candle_tecnico": candle_tecnico,
            "candle_tabuleiro": candle_tabuleiro,
            "coincidem": horarios_coincidem
        },

        "contexto_tecnico": tecnico,

        "contexto_matematico": matematico,

        "tabuleiro_estavel": {
            "metodo": tabuleiro.get("metodo", {}),
            "diagnostico": tabuleiro.get(
                "diagnostico", {}
            ),
            "mercado_atual": mercado,
            "transicoes_historicas": tabuleiro.get(
                "transicoes_historicas_da_regiao",
                []
            )
        },

        "regras_de_utilizacao": [
            "Não alterar a decisão original do Gemini.",
            "Não modificar entrada, stop ou alvo originais.",
            "Não tratar frequências históricas como certezas.",
            "Não somar fontes como votos de mesmo peso.",
            "Invalidadores estruturais devem ser avaliados separadamente.",
            "Dados ausentes ou horários divergentes exigem cautela.",
            "Nenhuma ordem será enviada ou executada."
        ]
    }

    avisos = []

    if not horarios_coincidem:
        avisos.append(
            "Os horários do contexto técnico e do tabuleiro "
            "não coincidem."
        )

    transicoes = tabuleiro.get(
        "transicoes_historicas_da_regiao", []
    )

    if transicoes:
        amostra = transicoes[0].get(
            "saidas_origem", 0
        )

        if amostra < 30:
            avisos.append(
                "A amostra de transições da região atual "
                "ainda é pequena."
            )

    if matematico.get("indicadores", {}).get(
        "vwap"
    ) is None:
        avisos.append(
            "VWAP indisponível nesta leitura matemática."
        )

    contexto["avisos"] = avisos

    PASTA.mkdir(exist_ok=True)

    ARQUIVO_SAIDA.write_text(
        json.dumps(
            contexto,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print("Contexto produzido com sucesso.")
    print("Candles analisados:", len(candles))
    print("Candle:", candle_tecnico)
    print(
        "Tendência M5:",
        tecnico.get("tendencia_m5")
    )
    print(
        "Tendência M15:",
        tecnico.get("tendencia_m15")
    )
    print(
        "Tendência H1:",
        tecnico.get("tendencia_h1")
    )
    print(
        "Estrutura matemática:",
        matematico["estrutura"]["tendencia_estrutural"]
    )
    print(
        "Região estável:",
        mercado.get("regiao")
    )
    print(
        "Horários coincidem:",
        horarios_coincidem
    )
    print("Avisos:", len(avisos))
    print("Arquivo:", ARQUIVO_SAIDA)


if __name__ == "__main__":
    main()
