
import csv
import gzip
import json
import math
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


API_URL = "https://api.twelvedata.com/time_series"
MAX_CANDLES = 5000

DATA_DIR = Path("data")
CSV_PATH = DATA_DIR / "xauusd_m5.csv"

BANCO_GRAFICOS_GZ = DATA_DIR / "graficos.db.gz"
BANCO_GRAFICOS = DATA_DIR / "graficos.db"

CAMPOS = [
    "timestamp_utc",
    "open",
    "high",
    "low",
    "close",
    "volume",
]


def validar_candle(item):
    if not isinstance(item, dict):
        raise ValueError("Registro não é um objeto.")

    timestamp_original = (
        item.get("timestamp_utc")
        or item.get("datetime")
        or item.get("timestamp")
    )

    if not timestamp_original:
        raise ValueError("Timestamp ausente.")

    timestamp_original = str(timestamp_original).strip()

    formatos = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M",
    )

    instante = None

    for formato in formatos:
        try:
            instante = datetime.strptime(
                timestamp_original,
                formato,
            )
            break
        except ValueError:
            continue

    if instante is None:
        raise ValueError(
            f"Timestamp inválido: {timestamp_original}"
        )

    timestamp = instante.strftime("%Y-%m-%d %H:%M:%S")

    abertura = float(item["open"])
    maxima = float(item["high"])
    minima = float(item["low"])
    fechamento = float(item["close"])

    precos = [
        abertura,
        maxima,
        minima,
        fechamento,
    ]

    if not all(
        math.isfinite(valor) and valor > 0
        for valor in precos
    ):
        raise ValueError("Preço inválido.")

    if maxima < max(abertura, fechamento, minima):
        raise ValueError("Máxima inconsistente.")

    if minima > min(abertura, fechamento, maxima):
        raise ValueError("Mínima inconsistente.")

    volume = item.get("volume", "")

    if volume not in ("", None):
        volume = float(volume)

        if not math.isfinite(volume) or volume < 0:
            raise ValueError("Volume inválido.")
    else:
        volume = ""

    return {
        "timestamp_utc": timestamp,
        "open": abertura,
        "high": maxima,
        "low": minima,
        "close": fechamento,
        "volume": volume,
    }


def abrir_banco_graficos():
    """
    Abre o banco oficial somente para leitura.
    O arquivo compactado nunca é modificado.
    """

    if BANCO_GRAFICOS.exists():
        conexao = sqlite3.connect(
            f"file:{BANCO_GRAFICOS.resolve()}?mode=ro",
            uri=True,
        )
        return conexao, None

    if not BANCO_GRAFICOS_GZ.exists():
        return None, None

    temporario = None

    try:
        conteudo = gzip.decompress(
            BANCO_GRAFICOS_GZ.read_bytes()
        )

        arquivo = tempfile.NamedTemporaryFile(
            prefix="leitura_graficos_",
            suffix=".db",
            delete=False,
        )

        temporario = Path(arquivo.name)

        with arquivo:
            arquivo.write(conteudo)

        conexao = sqlite3.connect(
            f"file:{temporario.resolve()}?mode=ro",
            uri=True,
        )

        return conexao, temporario

    except Exception:
        if temporario is not None:
            temporario.unlink(missing_ok=True)

        raise


def carregar_historico_banco():
    """
    Reconstrói candles a partir dos registros importados
    na tabela oficial graficos.

    Não cria tabelas e não grava no banco.
    """

    avisos = []
    candles = {}

    try:
        conexao, temporario = abrir_banco_graficos()

    except Exception as erro:
        return candles, [
            "Não foi possível abrir graficos.db.gz: "
            f"{type(erro).__name__}: {erro}"
        ]

    if conexao is None:
        return candles, [
            "Banco graficos.db.gz ainda não encontrado. "
            "Coleta seguirá apenas com os candles novos."
        ]

    try:
        tabelas = {
            linha[0]
            for linha in conexao.execute("""
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
            """).fetchall()
        }

        if "graficos" not in tabelas:
            avisos.append(
                "Tabela oficial graficos não encontrada."
            )
            return candles, avisos

        colunas = {
            linha[1]
            for linha in conexao.execute(
                'PRAGMA table_info("graficos")'
            ).fetchall()
        }

        obrigatorias = {
            "arquivo_origem",
            "payload_json",
        }

        if not obrigatorias.issubset(colunas):
            avisos.append(
                "Estrutura da tabela graficos não reconhecida."
            )
            return candles, avisos

        registros = conexao.execute("""
            SELECT arquivo_origem, payload_json
            FROM graficos
            WHERE
                LOWER(arquivo_origem) LIKE '%xauusd_m5%'
                OR LOWER(arquivo_origem) LIKE '%historico_velas_m5%'
        """).fetchall()

        invalidos = 0

        for arquivo_origem, payload_json in registros:
            try:
                item = json.loads(payload_json)

                if not isinstance(item, dict):
                    continue

                candle = validar_candle(item)

                candles[candle["timestamp_utc"]] = candle

            except (
                ValueError,
                TypeError,
                KeyError,
                json.JSONDecodeError,
            ):
                invalidos += 1
                continue

        if not registros:
            avisos.append(
                "Nenhum registro de candles M5 encontrado "
                "no banco oficial."
            )

        if invalidos:
            avisos.append(
                f"{invalidos} registros históricos inválidos "
                "foram ignorados."
            )

        print(
            "Histórico recuperado do banco: "
            f"{len(candles)} candles."
        )

    except sqlite3.Error as erro:
        avisos.append(
            "Erro na leitura do banco de gráficos: "
            f"{erro}"
        )

    finally:
        conexao.close()

        if temporario is not None:
            temporario.unlink(missing_ok=True)

    return candles, avisos


def baixar_candles():
    api_key = os.getenv("TWELVE_DATA_API_KEY")

    if not api_key:
        raise RuntimeError(
            "Secret TWELVE_DATA_API_KEY não encontrado."
        )

    parametros = {
        "symbol": "XAU/USD",
        "interval": "5min",
        "outputsize": MAX_CANDLES,
        "format": "JSON",
        "timezone": "UTC",
        "apikey": api_key,
    }

    url = API_URL + "?" + urlencode(parametros)

    requisicao = Request(
        url,
        headers={
            "User-Agent": "XAUUSD-Backtest-Collector/2.0"
        },
    )

    try:
        with urlopen(requisicao, timeout=30) as resposta:
            dados = json.loads(
                resposta.read().decode("utf-8")
            )

    except HTTPError as erro:
        raise RuntimeError(
            f"Erro HTTP {erro.code} ao consultar a Twelve Data."
        ) from None

    except (URLError, TimeoutError, OSError):
        raise RuntimeError(
            "Falha de conexão com a Twelve Data."
        ) from None

    except json.JSONDecodeError:
        raise RuntimeError(
            "Resposta JSON inválida da Twelve Data."
        ) from None

    if not isinstance(dados, dict):
        raise RuntimeError(
            "Formato inesperado da resposta da Twelve Data."
        )

    if dados.get("status") == "error":
        mensagem = str(
            dados.get("message", "erro não especificado")
        )
        raise RuntimeError(
            f"Twelve Data recusou a consulta: {mensagem}"
        )

    valores = dados.get("values")

    if not isinstance(valores, list) or not valores:
        raise RuntimeError(
            "A resposta não contém candles."
        )

    candles = {}
    invalidos = 0

    for item in valores:
        try:
            candle = validar_candle(item)

            candles[candle["timestamp_utc"]] = candle

        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            invalidos += 1
            continue

    if not candles:
        raise RuntimeError(
            "Nenhum candle válido recebido da Twelve Data."
        )

    if invalidos:
        print(
            f"AVISO: {invalidos} candles inválidos "
            "da API foram ignorados."
        )

    return candles


def salvar_candles(candles):
    if not candles:
        raise RuntimeError(
            "Não há candles para salvar."
        )

    ordenados = sorted(
        candles.values(),
        key=lambda item: item["timestamp_utc"],
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporario = CSV_PATH.with_suffix(".tmp")

    try:
        with temporario.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as arquivo:
            escritor = csv.DictWriter(
                arquivo,
                fieldnames=CAMPOS,
            )

            escritor.writeheader()
            escritor.writerows(ordenados)

        temporario.replace(CSV_PATH)

    except Exception:
        temporario.unlink(missing_ok=True)
        raise

    return ordenados


def baixar_e_integrar():
    print("=" * 55)
    print("COLETOR DE CANDLES XAUUSD M5")
    print("=" * 55)

    historico, avisos = carregar_historico_banco()

    for aviso in avisos:
        print(f"AVISO: {aviso}")

    novos = baixar_candles()

    quantidade_historica = len(historico)
    quantidade_nova = len(novos)

    # Os candles novos substituem os históricos
    # quando possuem o mesmo timestamp.
    historico.update(novos)

    ordenados = salvar_candles(historico)

    print("-" * 55)
    print("INTEGRAÇÃO CONCLUÍDA")
    print(f"Candles recuperados do banco: {quantidade_historica}")
    print(f"Candles recebidos da API: {quantidade_nova}")
    print(f"Total sem duplicatas: {len(ordenados)}")
    print(
        "Primeiro candle UTC: "
        f"{ordenados[0]['timestamp_utc']}"
    )
    print(
        "Último candle UTC: "
        f"{ordenados[-1]['timestamp_utc']}"
    )
    print(f"Arquivo gerado: {CSV_PATH}")
    print("=" * 55)


if __name__ == "__main__":
    try:
        baixar_e_integrar()

    except RuntimeError as erro:
        print(f"FALHA: {erro}")
        sys.exit(1)

    except Exception as erro:
        print(
            "ERRO INESPERADO: "
            f"{type(erro).__name__}: {erro}"
        )
        sys.exit(1)
