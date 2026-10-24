
import csv
import math
import sys
from pathlib import Path
from datetime import datetime

ARQUIVO = Path("data/xauusd_m5.csv")
PASTA_RESULTADOS = Path("resultados")
ATR_PERIODO = 14
EMA_RAPIDA = 6
EMA_LENTA = 20
MACD_RAPIDO = 12
MACD_LENTO = 26
MACD_SINAL = 9
ATR_MULTIPLICADOR = 1.5
ALVO_R = 1.5
CUSTO_R = 0.0  # Ajustar quando houver estimativa confiável de custos


def ema(valores, periodo):
    alpha = 2 / (periodo + 1)
    resultado = []
    atual = valores[0]
    for valor in valores:
        atual = alpha * valor + (1 - alpha) * atual
        resultado.append(atual)
    return resultado


def carregar_candles():
    if not ARQUIVO.exists():
        raise ValueError(f"Arquivo não encontrado: {ARQUIVO}")

    candles = []
    vistos = set()

    with ARQUIVO.open(newline="", encoding="utf-8") as f:
        leitor = csv.DictReader(f)
        obrigatorias = {"timestamp_utc", "open", "high", "low", "close"}
        if not leitor.fieldnames or not obrigatorias.issubset(leitor.fieldnames):
            raise ValueError("Colunas obrigatórias ausentes no CSV.")

        for linha in leitor:
            tempo = datetime.strptime(
                linha["timestamp_utc"], "%Y-%m-%d %H:%M:%S"
            )
            timestamp = tempo.isoformat()

            if timestamp in vistos:
                raise ValueError(f"Candle duplicado: {timestamp}")
            vistos.add(timestamp)

            o = float(linha["open"])
            h = float(linha["high"])
            l = float(linha["low"])
            c = float(linha["close"])

            if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)):
                raise ValueError(f"Preço inválido em {timestamp}")
            if h < max(o, c, l) or l > min(o, c, h):
                raise ValueError(f"OHLC inconsistente em {timestamp}")

            candles.append({
                "time": timestamp, "open": o,
                "high": h, "low": l, "close": c
            })

    candles.sort(key=lambda x: x["time"])

    if len(candles) < 100:
        raise ValueError("Histórico insuficiente para o teste.")

    return candles


def preparar_indicadores(c):
    closes = [x["close"] for x in c]
    fast = ema(closes, EMA_RAPIDA)
    slow = ema(closes, EMA_LENTA)
    macd_line = [
        a - b for a, b in zip(
            ema(closes, MACD_RAPIDO), ema(closes, MACD_LENTO)
        )
    ]
    macd_signal = ema(macd_line, MACD_SINAL)
    hist = [a - b for a, b in zip(macd_line, macd_signal)]

    atr = [None] * len(c)
    true_ranges = []

    for i, candle in enumerate(c):
        if i == 0:
            tr = candle["high"] - candle["low"]
        else:
            prev_close = c[i - 1]["close"]
            tr = max(
                candle["high"] - candle["low"],
                abs(candle["high"] - prev_close),
                abs(candle["low"] - prev_close),
            )
        true_ranges.append(tr)

        if i >= ATR_PERIODO - 1:
            atr[i] = sum(true_ranges[i - ATR_PERIODO + 1:i + 1]) / ATR_PERIODO

    consolidacao = [False] * len(c)
    for i in range(19, len(c)):
        trecho = c[i - 19:i + 1]
        faixa = max(x["high"] for x in trecho) - min(x["low"] for x in trecho)
        consolidacao[i] = faixa / c[i]["close"] <= 0.002

    for i, candle in enumerate(c):
        candle.update({
            "ema6": fast[i], "ema20": slow[i],
            "hist": hist[i], "atr": atr[i],
            "consolidacao": consolidacao[i]
        })


def sinal_cruzamento(c, i):
    atual, anterior = c[i], c[i - 1]
    if atual["consolidacao"] or atual["atr"] is None:
        return None

    if (
        anterior["ema6"] <= anterior["ema20"]
        and atual["ema6"] > atual["ema20"]
        and atual["hist"] > 0
    ):
        return "COMPRA"

    if (
        anterior["ema6"] >= anterior["ema20"]
        and atual["ema6"] < atual["ema20"]
        and atual["hist"] < 0
    ):
        return "VENDA"

    return None


def sinal_pullback(c, i):
    atual, anterior = c[i], c[i - 1]
    if atual["consolidacao"] or atual["atr"] is None:
        return None

    faixa_media = atual["atr"] * 0.25
    tocou = (
        atual["low"] <= max(atual["ema6"], atual["ema20"]) + faixa_media
        and atual["high"] >= min(atual["ema6"], atual["ema20"]) - faixa_media
    )

    if (
        tocou and atual["ema6"] > atual["ema20"]
        and atual["close"] > atual["open"]
        and atual["close"] > anterior["close"]
        and atual["close"] > atual["ema6"]
        and atual["hist"] > 0
    ):
        return "COMPRA"

    if (
        tocou and atual["ema6"] < atual["ema20"]
        and atual["close"] < atual["open"]
        and atual["close"] < anterior["close"]
        and atual["close"] < atual["ema6"]
        and atual["hist"] < 0
    ):
        return "VENDA"

    return None


def simular(c, funcao_sinal, nome):
    operacoes = []
    i = 30

    while i < len(c) - 1:
        direcao = funcao_sinal(c, i)
        if not direcao:
            i += 1
            continue

        # Sinal confirmado no fechamento; entrada no fechamento do candle.
        entrada = c[i]["close"]
        atr = c[i]["atr"]
        if atr is None or atr <= 0:
            i += 1
            continue

        distancia = atr * ATR_MULTIPLICADOR
        stop = entrada - distancia if direcao == "COMPRA" else entrada + distancia
        alvo = entrada + distancia * ALVO_R if direcao == "COMPRA" else entrada - distancia * ALVO_R

        resultado = None
        saida = None
        fim = i + 1

        while fim < len(c):
            candle = c[fim]
            if direcao == "COMPRA":
                tocou_stop = candle["low"] <= stop
                tocou_alvo = candle["high"] >= alvo
            else:
                tocou_stop = candle["high"] >= stop
                tocou_alvo = candle["low"] <= alvo

            # Se ambos são tocados no mesmo candle, assumir stop primeiro.
            if tocou_stop:
                resultado = -1.0
                saida = stop
                break
            if tocou_alvo:
                resultado = ALVO_R
                saida = alvo
                break
            fim += 1

        if resultado is None:
            # Operação sem stop/alvo antes do fim do histórico: não contar.
            break

        resultado_liquido = resultado - CUSTO_R
        operacoes.append({
            "estrategia": nome,
            "entrada_utc": c[i]["time"],
            "saida_utc": c[fim]["time"],
            "direcao": direcao,
            "preco_entrada": round(entrada, 5),
            "stop": round(stop, 5),
            "alvo": round(alvo, 5),
            "preco_saida": round(saida, 5),
            "resultado_R": round(resultado_liquido, 4),
        })

        i = fim + 1

    return operacoes


def salvar_resultados(todas):
    PASTA_RESULTADOS.mkdir(exist_ok=True)
    caminho_csv = PASTA_RESULTADOS / "operacoes_backtest.csv"

    campos = [
        "estrategia", "entrada_utc", "saida_utc", "direcao",
        "preco_entrada", "stop", "alvo", "preco_saida", "resultado_R"
    ]

    with caminho_csv.open("w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=campos)
        escritor.writeheader()
        escritor.writerows(todas)

    caminho_relatorio = PASTA_RESULTADOS / "relatorio_backtest.txt"
    with caminho_relatorio.open("w", encoding="utf-8") as f:
        f.write("BACKTEST XAUUSD M5 — RESULTADOS PRELIMINARES\n")
        f.write(f"Candles analisados: {len_candles}\n")
        f.write("VWAP: NÃO UTILIZADA (volume ausente no CSV)\n")
        f.write("Custo configurado: 0R; custos reais ainda não incluídos.\n")
        f.write("Alvo testado: 1,5R; stop: 1,5 ATR.\n")
        f.write("Entrada simulada no fechamento do candle de sinal.\n")
        f.write("Se stop e alvo forem tocados no mesmo candle, stop primeiro.\n\n")

        for nome in ("Cruzamento EMA 6/20", "Pullback EMA 6/20"):
            ops = [x for x in todas if x["estrategia"] == nome]
            if not ops:
                f.write(f"{nome}: nenhuma operação concluída.\n\n")
                continue

            ganhos = sum(x["resultado_R"] > 0 for x in ops)
            soma = sum(x["resultado_R"] for x in ops)
            pico = 0.0
            curva = 0.0
            drawdown = 0.0
            for op in ops:
                curva += op["resultado_R"]
                pico = max(pico, curva)
                drawdown = max(drawdown, pico - curva)

            f.write(f"Estratégia: {nome}\n")
            f.write(f"Operações concluídas: {len(ops)}\n")
            f.write(f"Taxa de acerto: {100 * ganhos / len(ops):.2f}%\n")
            f.write(f"Resultado total: {soma:.2f}R\n")
            f.write(f"Expectativa por operação: {soma / len(ops):.3f}R\n")
            f.write(f"Drawdown máximo: {drawdown:.2f}R\n\n")


def main():
    global len_candles
    candles = carregar_candles()
    len_candles = len(candles)
    preparar_indicadores(candles)

    cruzamentos = simular(candles, sinal_cruzamento, "Cruzamento EMA 6/20")
    pullbacks = simular(candles, sinal_pullback, "Pullback EMA 6/20")
    todas = cruzamentos + pullbacks

    salvar_resultados(todas)
    print(f"Candles analisados: {len(candles)}")
    print(f"Operações cruzamento: {len(cruzamentos)}")
    print(f"Operações pullback: {len(pullbacks)}")
    print("Relatório: resultados/relatorio_backtest.txt")
    print("Operações: resultados/operacoes_backtest.csv")
    print("VWAP não usada porque o volume está ausente.")
    print("Simulação informativa; nenhuma ordem foi executada.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as erro:
        print(f"FALHA NO BACKTEST: {erro}")
        sys.exit(1)
