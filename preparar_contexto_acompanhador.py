
import json
import math

from pathlib import Path
from datetime import datetime, timezone

from motor_contexto_xau import carregar_candles, analisar
from integrar_leitura import integrar_leitura


PASTA = Path("data")

ARQUIVO_TABULEIRO = (
    PASTA / "contexto_tabuleiro_m5.json"
)

ARQUIVO_SAIDA = (
    PASTA / "contexto_inteligente_acompanhador.json"
)


def numero_seguro(valor):
    try:
        resultado = float(valor)

        if math.isfinite(resultado):
            return round(resultado, 5)

    except (TypeError, ValueError):
        pass

    return None


def converter_data(valor):
    if not valor:
        return None

    try:
        data = datetime.fromisoformat(
            str(valor).strip().replace("Z", "+00:00")
        )

        if data.tzinfo is None:
            data = data.replace(tzinfo=timezone.utc)

        return data.astimezone(timezone.utc)

    except (ValueError, TypeError):
        return None


def resumir_niveis(niveis, preco, limite=4):
    resultado = []

    if not isinstance(niveis, list):
        return resultado

    for nivel in niveis:
        if not isinstance(nivel, dict):
            continue

        centro = numero_seguro(nivel.get("centro"))

        if centro is None:
            continue

        item = dict(nivel)
        item["centro"] = centro

        resultado.append(item)

    resultado.sort(
        key=lambda item: abs(item["centro"] - preco)
    )

    return resultado[:limite]


def resumir_estrutura(estrutura, preco):
    if not isinstance(estrutura, dict):
        estrutura = {}

    return {
        "tendencia_estrutural": estrutura.get(
            "tendencia",
            "INDEFINIDA"
        ),

        "ultimos_topos": (
            estrutura.get("topos", [])[-6:]
            if isinstance(estrutura.get("topos"), list)
            else []
        ),

        "ultimos_fundos": (
            estrutura.get("fundos", [])[-6:]
            if isinstance(estrutura.get("fundos"), list)
            else []
        ),

        "suportes_proximos": resumir_niveis(
            estrutura.get("suportes", []),
            preco
        ),

        "resistencias_proximas": resumir_niveis(
            estrutura.get("resistencias", []),
            preco
        ),

        "rompimentos": estrutura.get(
            "rompimentos",
            []
        ),

        "observacao": estrutura.get(
            "observacao",
            ""
        )
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

    preco = numero_seguro(
        leitura.get("preco")
    )

    estrutura = resumir_estrutura(
        leitura.get("estrutura", {}),
        preco if preco is not None else 0
    )

    return {
        "timeframe": leitura.get("timeframe"),

        "preco": preco,

        "indicadores": {
            "ema6": numero_seguro(
                indicadores.get("ema6")
            ),

            "ema20": numero_seguro(
                indicadores.get("ema20")
            ),

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

            "consolidacao": indicadores.get(
                "consolidacao"
            ),

            "vwap": numero_seguro(
                indicadores.get("vwap")
            ),

            "vwap_status": indicadores.get(
                "vwap_status"
            )
        },

        "estrutura": estrutura,

        "status": "LEITURA_MATEMATICA_ESTRUTURAL",

        "decisao_final": leitura.get(
            "decisao_final",
            "PENDENTE_DE_VALIDACAO"
        ),

        "observacao": (
            "Esta leitura é uma fonte independente de contexto. "
            "Não substitui nem altera o sinal original."
        )
    }


def calcular_serie_ema(valores, periodo):
    if len(valores) < periodo:
        return [None] * len(valores)

    fator = 2 / (periodo + 1)

    resultado = None
    serie = []

    for indice, valor in enumerate(valores):

        if indice + 1 < periodo:
            serie.append(None)
            continue

        if resultado is None:
            resultado = sum(
                valores[:periodo]
            ) / periodo

        else:
            resultado = (
                valor * fator
                + resultado * (1 - fator)
            )

        serie.append(
            round(resultado, 5)
        )

    return serie


def carregar_tabuleiro():
    if not ARQUIVO_TABULEIRO.exists():
        raise FileNotFoundError(
            "Contexto do Tabuleiro não encontrado: "
            + str(ARQUIVO_TABULEIRO)
        )

    with ARQUIVO_TABULEIRO.open(
        "r",
        encoding="utf-8"
    ) as arquivo:
        dados = json.load(arquivo)

    if not isinstance(dados, dict):
        raise ValueError(
            "O contexto do Tabuleiro não possui formato válido."
        )

    return dados


def validar_horarios(candle_tecnico, candle_tabuleiro):
    data_tecnica = converter_data(
        candle_tecnico
    )

    data_tabuleiro = converter_data(
        candle_tabuleiro
    )

    if data_tecnica is None or data_tabuleiro is None:
        return False

    return data_tecnica == data_tabuleiro


def construir_grafico(candles):
    fechamentos = [
        float(c["close"])
        for c in candles
    ]

    ema9_serie = calcular_serie_ema(
        fechamentos,
        9
    )

    ema21_serie = calcular_serie_ema(
        fechamentos,
        21
    )

    serie = []

    inicio = max(
        0,
        len(candles) - 240
    )

    for indice in range(inicio, len(candles)):
        candle = candles[indice]

        serie.append({
            "timestamp": candle["tempo"].isoformat(),

            "open": numero_seguro(
                candle["open"]
            ),

            "high": numero_seguro(
                candle["high"]
            ),

            "low": numero_seguro(
                candle["low"]
            ),

            "close": numero_seguro(
                candle["close"]
            ),

            "ema9": ema9_serie[indice],

            "ema21": ema21_serie[indice]
        })

    return serie


def calcular_idade_central(tabuleiro):
    data_central = converter_data(
        tabuleiro.get("atualizado_em")
    )

    if data_central is None:
        return None

    idade = (
        datetime.now(timezone.utc) - data_central
    ).total_seconds() / 60

    return max(0.0, idade)


def gerar_avisos(
    horarios_coincidem,
    idade_central,
    matematico,
    transicoes
):
    avisos = []

    if not horarios_coincidem:
        avisos.append(
            "Os horários do contexto técnico e do Tabuleiro "
            "não coincidem."
        )

    if idade_central is None:
        avisos.append(
            "Não foi possível validar a atualização do Tabuleiro."
        )

    elif idade_central > 30:
        avisos.append(
            "O contexto do Tabuleiro está desatualizado "
            "há mais de 30 minutos."
        )

    if transicoes:
        primeira = transicoes[0]

        amostra = primeira.get(
            "saidas_origem",
            0
        )

        try:
            amostra = int(amostra)
        except (TypeError, ValueError):
            amostra = 0

        if amostra < 30:
            avisos.append(
                "A amostra histórica de transições "
                "ainda é pequena."
            )

    indicadores = matematico.get(
        "indicadores",
        {}
    )

    if indicadores.get("vwap") is None:
        avisos.append(
            "VWAP indisponível nesta leitura matemática."
        )

    return avisos


def main():
    print(
        "Preparando contexto inteligente do Acompanhador..."
    )

    PASTA.mkdir(
        parents=True,
        exist_ok=True
    )

    # 1. Carrega candles M5 fechados.
    candles = carregar_candles()

    if not candles or len(candles) < 100:
        raise RuntimeError(
            "Histórico insuficiente para análise."
        )

    # 2. Análise técnica existente.
    tecnico = analisar(candles)

    # 3. Leitura matemática e estrutural independente.
    matematico = resumir_matematica(candles)

    # 4. Contexto do Tabuleiro estável.
    tabuleiro = carregar_tabuleiro()

    mercado = tabuleiro.get(
        "mercado_atual",
        {}
    )

    if not isinstance(mercado, dict):
        mercado = {}

    # 5. Validação temporal.
    candle_tecnico = tecnico.get(
        "candle_utc"
    )

    candle_tabuleiro = mercado.get(
        "timestamp"
    )

    horarios_coincidem = validar_horarios(
        candle_tecnico,
        candle_tabuleiro
    )

    # 6. Reconstrução cronológica do gráfico M5.
    serie_grafico = construir_grafico(
        candles
    )

    # 7. Idade da atualização do Tabuleiro.
    idade_central = calcular_idade_central(
        tabuleiro
    )

    transicoes = tabuleiro.get(
        "transicoes_historicas_da_regiao",
        []
    )

    if not isinstance(transicoes, list):
        transicoes = []

    avisos = gerar_avisos(
        horarios_coincidem,
        idade_central,
        matematico,
        transicoes
    )

    # 8. Montagem do contexto para o Acompanhador.
    contexto = {
        "versao": "3.0",

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

        "grafico_m5": {
            "eixo_x": "tempo_utc_crescente",

            "eixo_y": "preco_xauusd",

            "total_candles_historico": len(candles),

            "candles_exibidos": len(
                serie_grafico
            ),

            "serie": serie_grafico
        },

        "estado_central": {
            "disponivel": bool(mercado),

            "atualizado_em": tabuleiro.get(
                "atualizado_em"
            ),

            "idade_minutos": (
                round(idade_central, 1)
                if idade_central is not None
                else None
            ),

            "desatualizada": (
                idade_central is None
                or idade_central > 30
            ),

            "horario_igual_ao_candle_atual": (
                horarios_coincidem
            )
        },

        "tabuleiro_estavel": {
            "metodo": tabuleiro.get(
                "metodo",
                {}
            ),

            "diagnostico": tabuleiro.get(
                "diagnostico",
                {}
            ),

            "mercado_atual": mercado,

            "transicoes_historicas": transicoes
        },

        "regras_de_utilizacao": [
            "Não alterar a decisão original do Gemini.",
            "Não modificar entrada, stop ou alvo originais.",
            "Não tratar frequências históricas como certezas.",
            "Não somar fontes como votos de mesmo peso.",
            "Avaliar invalidadores estruturais separadamente.",
            "Dados ausentes ou horários divergentes exigem cautela.",
            "Nenhuma ordem será enviada ou executada."
        ],

        "avisos": avisos
    }

    # 9. Grava somente o arquivo de entrega.
    with ARQUIVO_SAIDA.open(
        "w",
        encoding="utf-8"
    ) as arquivo:
        json.dump(
            contexto,
            arquivo,
            ensure_ascii=False,
            indent=2
        )

    print("\nCONTEXTO PREPARADO")

    print(
        "Candles analisados:",
        len(candles)
    )

    print(
        "Candles no gráfico:",
        len(serie_grafico)
    )

    print(
        "Candle técnico:",
        candle_tecnico
    )

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

    print(
        "Idade do Tabuleiro:",
        idade_central
    )

    print(
        "Avisos:",
        len(avisos)
    )

    print(
        "Arquivo gerado:",
        ARQUIVO_SAIDA
    )


if __name__ == "__main__":
    main()
