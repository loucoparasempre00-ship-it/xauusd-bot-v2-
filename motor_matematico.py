
import csv
import gzip
import json
import sqlite3
import tempfile

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from math import isfinite


# ============================================================
# BANCO OFICIAL DE GRAFICOS
# ============================================================

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

BANCO_GRAFICOS = DATA_DIR / "graficos.db"
BANCO_GRAFICOS_GZ = DATA_DIR / "graficos.db.gz"


def abrir_banco_graficos():
    """
    Abre graficos.db exclusivamente para leitura.

    Se existir somente graficos.db.gz, cria uma copia
    temporaria para consulta.

    Nunca modifica o banco oficial.
    """

    temporario = None

    if BANCO_GRAFICOS.is_file():
        caminho = BANCO_GRAFICOS

    elif BANCO_GRAFICOS_GZ.is_file():
        temporario = tempfile.TemporaryDirectory(
            prefix="consulta_graficos_"
        )

        caminho = Path(temporario.name) / "graficos.db"

        with gzip.open(BANCO_GRAFICOS_GZ, "rb") as origem:
            with open(caminho, "wb") as destino:
                destino.write(origem.read())

    else:
        raise FileNotFoundError(
            "Banco oficial graficos.db ou graficos.db.gz "
            "nao encontrado."
        )

    try:
        conexao = sqlite3.connect(
            caminho.resolve().as_uri() + "?mode=ro",
            uri=True,
        )

        conexao.row_factory = sqlite3.Row

        return conexao, temporario

    except Exception:
        if temporario is not None:
            temporario.cleanup()

        raise


def normalizar_candle_banco(registro):
    """
    Converte um registro armazenado no banco
    para o formato utilizado pelo motor matematico.
    """

    if not isinstance(registro, dict):
        return None

    timestamp = (
        registro.get("timestamp")
        or registro.get("timestamp_utc")
        or registro.get("datetime")
    )

    if not timestamp:
        return None

    timestamp = str(timestamp).strip()

    try:
        instante = datetime.fromisoformat(
            timestamp.replace("Z", "+00:00")
        )

        if instante.tzinfo is None:
            timestamp_normalizado = instante.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        else:
            timestamp_normalizado = (
                instante.astimezone(ZoneInfo("UTC"))
                .strftime("%Y-%m-%d %H:%M:%S")
            )

        abertura = float(registro["open"])
        maxima = float(registro["high"])
        minima = float(registro["low"])
        fechamento = float(registro["close"])

        volume_original = registro.get("volume", 0)

        if volume_original in (None, ""):
            volume = 0.0
        else:
            volume = float(volume_original)

    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    valores = [
        abertura,
        maxima,
        minima,
        fechamento,
        volume,
    ]

    if not all(isfinite(valor) for valor in valores):
        return None

    if min(abertura, maxima, minima, fechamento) <= 0:
        return None

    if maxima < max(abertura, fechamento, minima):
        return None

    if minima > min(abertura, fechamento, maxima):
        return None

    if volume < 0:
        return None

    return {
        "timestamp": timestamp_normalizado,
        "timestamp_utc": timestamp_normalizado,
        "open": abertura,
        "high": maxima,
        "low": minima,
        "close": fechamento,
        "volume": volume,
    }


def carregar_candles_graficos(limite=None):
    """
    Consulta o historico XAUUSD M5 no banco oficial graficos.

    Espera a tabela:
        graficos

    E os campos:
        arquivo_origem
        payload_json

    Nao cria tabelas.
    Nao grava dados.
    Nao altera o banco.

    Retorna candles em ordem cronologica.
    """

    conexao, temporario = abrir_banco_graficos()

    try:
        tabelas = {
            linha["name"]
            for linha in conexao.execute("""
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
            """).fetchall()
        }

        if "graficos" not in tabelas:
            raise RuntimeError(
                "Tabela graficos nao encontrada no banco oficial."
            )

        colunas = {
            linha["name"]
            for linha in conexao.execute(
                'PRAGMA table_info("graficos")'
            ).fetchall()
        }

        obrigatorias = {
            "arquivo_origem",
            "payload_json",
        }

        if not obrigatorias.issubset(colunas):
            raise RuntimeError(
                "Estrutura da tabela graficos nao reconhecida."
            )

        registros = conexao.execute("""
            SELECT arquivo_origem, payload_json
            FROM graficos
            WHERE
                LOWER(arquivo_origem) LIKE '%xauusd_m5%'
                OR LOWER(arquivo_origem) LIKE '%historico_velas_m5%'
            ORDER BY rowid
        """).fetchall()

        candles_por_tempo = {}

        invalidos = 0

        for linha in registros:
            try:
                payload = json.loads(
                    linha["payload_json"]
                )

                if not isinstance(payload, dict):
                    continue

                candle = normalizar_candle_banco(payload)

                if candle is None:
                    invalidos += 1
                    continue

                timestamp = candle["timestamp"]

                # Um candle por horario.
                # O ultimo registro valido substitui o anterior.
                candles_por_tempo[timestamp] = candle

            except (
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ):
                invalidos += 1
                continue

        candles = sorted(
            candles_por_tempo.values(),
            key=lambda item: item["timestamp"],
        )

        if limite is not None:
            limite = int(limite)

            if limite <= 0:
                raise ValueError(
                    "O limite deve ser maior que zero."
                )

            candles = candles[-limite:]

        print("CONSULTA AO BANCO DE GRAFICOS")
        print("Candles recuperados:", len(candles))
        print("Registros invalidos ignorados:", invalidos)

        return candles

    finally:
        conexao.close()

        if temporario is not None:
            temporario.cleanup()


# ============================================================
# CALCULOS MATEMATICOS ORIGINAIS
# ============================================================

def calcular_ema(valores, periodo):
    if periodo < 1 or len(valores) < periodo:
        raise ValueError("Valores insuficientes para EMA.")

    valores = [float(v) for v in valores]

    if not all(isfinite(v) for v in valores):
        raise ValueError("Valor inválido para EMA.")

    alpha = 2 / (periodo + 1)
    ema = valores[0]

    for valor in valores[1:]:
        ema = alpha * valor + (1 - alpha) * ema

    return ema


def calcular_macd(
    valores,
    periodo_rapido=12,
    periodo_lento=26,
    periodo_sinal=9
):
    if len(valores) < periodo_lento + periodo_sinal:
        raise ValueError("Valores insuficientes para MACD.")

    def serie_ema(lista, periodo):
        alpha = 2 / (periodo + 1)
        resultado = [lista[0]]

        for valor in lista[1:]:
            resultado.append(
                alpha * valor + (1 - alpha) * resultado[-1]
            )

        return resultado

    valores = [float(v) for v in valores]

    if not all(isfinite(v) for v in valores):
        raise ValueError("Valor inválido para MACD.")

    rapida = serie_ema(valores, periodo_rapido)
    lenta = serie_ema(valores, periodo_lento)

    linha = [
        a - b
        for a, b in zip(rapida, lenta)
    ]

    sinal = serie_ema(linha, periodo_sinal)

    return {
        "linha": linha[-1],
        "sinal": sinal[-1],
        "histograma": linha[-1] - sinal[-1],
    }


def calcular_vwap_ancorada(
    candles,
    abertura_nova_york="09:30"
):
    if not candles:
        raise ValueError("Nenhum candle recebido.")

    fuso = ZoneInfo("America/New_York")

    if "T" in abertura_nova_york:
        inicio = datetime.fromisoformat(abertura_nova_york)

        if inicio.tzinfo is None:
            inicio = inicio.replace(tzinfo=fuso)
        else:
            inicio = inicio.astimezone(fuso)

    else:
        hora, minuto = map(
            int,
            abertura_nova_york.split(":")
        )

        inicio = None

    soma_pv = 0.0
    soma_volume = 0.0

    for candle in candles:
        instante = datetime.fromisoformat(
            candle["timestamp"].replace("Z", "+00:00")
        )

        if instante.tzinfo is None:
            raise ValueError("Timestamp sem fuso horário.")

        instante = instante.astimezone(fuso)

        if inicio is None:
            ancora = datetime(
                instante.year,
                instante.month,
                instante.day,
                hora,
                minuto,
                tzinfo=fuso
            )
        else:
            ancora = inicio

        if instante < ancora:
            continue

        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        volume = float(candle["volume"])

        if (
            not all(
                isfinite(v)
                for v in (high, low, close, volume)
            )
            or high < low
            or volume <= 0
        ):
            raise ValueError("Preço ou volume inválido.")

        tipico = (high + low + close) / 3

        soma_pv += tipico * volume
        soma_volume += volume

    if soma_volume == 0:
        raise ValueError("Sem volume desde a ancoragem.")

    return soma_pv / soma_volume


def detectar_consolidacao(
    candles,
    periodo=20,
    limite_percentual=0.002
):
    if len(candles) < periodo:
        return False

    recentes = candles[-periodo:]

    try:
        maximo = max(
            float(c["high"])
            for c in recentes
        )

        minimo = min(
            float(c["low"])
            for c in recentes
        )

        fechamento = float(
            recentes[-1]["close"]
        )

    except (KeyError, TypeError, ValueError):
        return False

    if (
        not all(
            isfinite(v)
            for v in (maximo, minimo, fechamento)
        )
        or fechamento <= 0
        or maximo < minimo
    ):
        return False

    return (
        maximo - minimo
    ) / fechamento <= limite_percentual


def calcular_niveis_risco(
    direcao,
    preco_entrada,
    atr,
    stop_estrutura=None,
    multiplo_atr=1.5
):
    valores = [
        preco_entrada,
        atr,
        multiplo_atr
    ]

    if not all(
        isfinite(float(v))
        for v in valores
    ):
        raise ValueError("Valores de risco inválidos.")

    preco_entrada = float(preco_entrada)
    atr = float(atr)
    multiplo_atr = float(multiplo_atr)

    if (
        preco_entrada <= 0
        or atr <= 0
        or multiplo_atr <= 0
    ):
        raise ValueError(
            "Entrada, ATR e multiplicador devem ser positivos."
        )

    direcao = direcao.upper()

    if direcao == "COMPRA":
        stop = preco_entrada - atr * multiplo_atr

        if stop_estrutura is not None:
            stop_estrutura = float(stop_estrutura)

            if (
                not isfinite(stop_estrutura)
                or stop_estrutura >= preco_entrada
            ):
                raise ValueError(
                    "Stop estrutural inválido para compra."
                )

            stop = min(stop, stop_estrutura)

        risco = preco_entrada - stop
        alvo_15 = preco_entrada + risco * 1.5
        alvo_2 = preco_entrada + risco * 2

    elif direcao == "VENDA":
        stop = preco_entrada + atr * multiplo_atr

        if stop_estrutura is not None:
            stop_estrutura = float(stop_estrutura)

            if (
                not isfinite(stop_estrutura)
                or stop_estrutura <= preco_entrada
            ):
                raise ValueError(
                    "Stop estrutural inválido para venda."
                )

            stop = max(stop, stop_estrutura)

        risco = stop - preco_entrada
        alvo_15 = preco_entrada - risco * 1.5
        alvo_2 = preco_entrada - risco * 2

    else:
        raise ValueError(
            "Direção deve ser COMPRA ou VENDA."
        )

    return {
        "stop": round(stop, 5),
        "risco": round(risco, 5),
        "alvo_1_5R": round(alvo_15, 5),
        "alvo_2R": round(alvo_2, 5),
    }


def detectar_cruzamento(
    ema6,
    ema20,
    macd_histograma,
    preco,
    vwap,
    ema6_anterior,
    ema20_anterior,
    consolidacao=False
):
    if consolidacao:
        return None

    valores = [
        ema6,
        ema20,
        macd_histograma,
        preco,
        vwap,
        ema6_anterior,
        ema20_anterior
    ]

    if not all(
        isfinite(float(v))
        for v in valores
    ):
        return None

    if (
        ema6_anterior <= ema20_anterior
        and ema6 > ema20
        and macd_histograma > 0
        and preco > vwap
    ):
        return "COMPRA"

    if (
        ema6_anterior >= ema20_anterior
        and ema6 < ema20
        and macd_histograma < 0
        and preco < vwap
    ):
        return "VENDA"

    return None


def detectar_pullback(
    candles,
    ema6,
    ema20,
    macd_histograma,
    vwap,
    consolidacao=False,
    tolerancia_atr=0.25,
    atr=None
):
    if (
        consolidacao
        or len(candles) < 3
        or atr is None
    ):
        return None

    try:
        atr = float(atr)

        if not isfinite(atr) or atr <= 0:
            return None

        anterior = candles[-2]
        ultimo = candles[-1]

        o_ant = float(anterior["open"])
        c_ant = float(anterior["close"])

        abertura = float(ultimo["open"])
        maxima = float(ultimo["high"])
        minima = float(ultimo["low"])
        fechamento = float(ultimo["close"])

        valores = [
            ema6,
            ema20,
            macd_histograma,
            vwap,
            o_ant,
            c_ant,
            abertura,
            maxima,
            minima,
            fechamento
        ]

        if not all(
            isfinite(float(v))
            for v in valores
        ):
            return None

        if maxima < minima:
            return None

    except (KeyError, TypeError, ValueError):
        return None

    tolerancia = atr * tolerancia_atr

    tocou_media = (
        minima <= max(ema6, ema20) + tolerancia
        and maxima >= min(ema6, ema20) - tolerancia
    )

    if (
        tocou_media
        and ema6 > ema20
        and fechamento > abertura
        and fechamento > c_ant
        and fechamento > ema6
        and macd_histograma > 0
        and fechamento > vwap
    ):
        return "COMPRA"

    if (
        tocou_media
        and ema6 < ema20
        and fechamento < abertura
        and fechamento < c_ant
        and fechamento < ema6
        and macd_histograma < 0
        and fechamento < vwap
    ):
        return "VENDA"

    return None


if __name__ == "__main__":
    print("MOTOR MATEMATICO XAUUSD")
    print("Banco oficial: graficos.db")
    print("Acesso ao banco: SOMENTE LEITURA")
    print("Sem envio de sinais ou execucao de ordens.")
