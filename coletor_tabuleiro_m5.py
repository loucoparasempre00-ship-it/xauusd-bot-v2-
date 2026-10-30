import csv
import os
import math
from datetime import datetime

ARQUIVO_ENTRADA = "data/xauusd_m5_fechados.csv"
ARQUIVO_SAIDA = "data/tabuleiro_m5.csv"

PERIODO_ATR = 14
DIVISAO_ATR = 0.50


def normalizar(texto):
    return (
        str(texto).strip().lower()
        .replace(" ", "_")
        .replace("-", "_")
    )


def localizar_coluna(campos, alternativas):
    mapa = {normalizar(c): c for c in campos}

    for nome in alternativas:
        if normalizar(nome) in mapa:
            return mapa[normalizar(nome)]

    return None


def numero(valor):
    if valor is None:
        raise ValueError("Valor numérico ausente")

    return float(str(valor).strip().replace(",", "."))


def carregar_velas():
    if not os.path.exists(ARQUIVO_ENTRADA):
        raise FileNotFoundError(
            f"Arquivo não encontrado: {ARQUIVO_ENTRADA}"
        )

    with open(ARQUIVO_ENTRADA, "r", encoding="utf-8-sig") as arquivo:
        amostra = arquivo.read(4096)
        arquivo.seek(0)

        try:
            delimitador = csv.Sniffer().sniff(
                amostra, delimiters=",;|\t"
            ).delimiter
        except csv.Error:
            delimitador = ","

        leitor = csv.DictReader(arquivo, delimiter=delimitador)

        campos = leitor.fieldnames or []

        colunas = {
            "tempo": localizar_coluna(
                campos,
                ["timestamp_utc", "datetime", "timestamp",
                 "time", "date", "datetime_utc"]
            ),
            "open": localizar_coluna(
                campos, ["open", "o"]
            ),
            "high": localizar_coluna(
                campos, ["high", "h"]
            ),
            "low": localizar_coluna(
                campos, ["low", "l"]
            ),
            "close": localizar_coluna(
                campos, ["close", "c"]
            )
        }

        ausentes = [
            nome for nome, coluna in colunas.items()
            if coluna is None
        ]

        if ausentes:
            raise ValueError(
                f"Colunas ausentes no CSV: {ausentes}. "
                f"Cabeçalhos encontrados: {campos}"
            )

        velas = []

        for linha in leitor:
            try:
                vela = {
                    "timestamp": linha[colunas["tempo"]].strip(),
                    "open": numero(linha[colunas["open"]]),
                    "high": numero(linha[colunas["high"]]),
                    "low": numero(linha[colunas["low"]]),
                    "close": numero(linha[colunas["close"]])
                }

                if not vela["timestamp"]:
                    continue

                if vela["high"] < vela["low"]:
                    continue

                velas.append(vela)

            except (ValueError, TypeError, KeyError):
                continue

    velas.sort(key=lambda v: v["timestamp"])
    return velas


def calcular_atr(velas, periodo=14):
    resultados = []
    true_ranges = []

    for i, vela in enumerate(velas):
        if i == 0:
            tr = vela["high"] - vela["low"]
        else:
            anterior = velas[i - 1]["close"]

            tr = max(
                vela["high"] - vela["low"],
                abs(vela["high"] - anterior),
                abs(vela["low"] - anterior)
            )

        true_ranges.append(tr)

        inicio = max(0, i - periodo + 1)
        janela = true_ranges[inicio:i + 1]

        atr = sum(janela) / len(janela)

        resultados.append(atr)

    return resultados


def carregar_historico():
    historico = {}

    if not os.path.exists(ARQUIVO_SAIDA):
        return historico

    with open(
        ARQUIVO_SAIDA, "r", encoding="utf-8-sig"
    ) as arquivo:
        leitor = csv.DictReader(arquivo)

        for linha in leitor:
            timestamp = linha.get("timestamp", "").strip()

            if timestamp:
                historico[timestamp] = linha

    return historico


def processar():
    velas = carregar_velas()
    atrs = calcular_atr(velas, PERIODO_ATR)
    historico = carregar_historico()

    novas = 0

    for i, vela in enumerate(velas):
        timestamp = vela["timestamp"]

        # Uma vela já coletada não é duplicada.
        if timestamp in historico:
            continue

        atr = atrs[i]

        if atr <= 0:
            continue

        largura_regiao = atr * DIVISAO_ATR

        # Coordenada X: posição temporal da vela.
        eixo_x = i

        # Coordenada Y: região matemática de preço.
        eixo_y = math.floor(
            vela["close"] / largura_regiao
        )

        amplitude = vela["high"] - vela["low"]
        corpo = abs(vela["close"] - vela["open"])

        if vela["close"] > vela["open"]:
            direcao = "ALTA"
        elif vela["close"] < vela["open"]:
            direcao = "BAIXA"
        else:
            direcao = "NEUTRA"

        historico[timestamp] = {
            "timestamp": timestamp,
            "eixo_x": eixo_x,
            "eixo_y": eixo_y,
            "open": round(vela["open"], 5),
            "high": round(vela["high"], 5),
            "low": round(vela["low"], 5),
            "close": round(vela["close"], 5),
            "atr": round(atr, 5),
            "largura_regiao": round(largura_regiao, 5),
            "amplitude": round(amplitude, 5),
            "corpo": round(corpo, 5),
            "direcao": direcao,
            "evento": "VISITA"
        }

        novas += 1

    os.makedirs(os.path.dirname(ARQUIVO_SAIDA), exist_ok=True)

    campos_saida = [
        "timestamp",
        "eixo_x",
        "eixo_y",
        "open",
        "high",
        "low",
        "close",
        "atr",
        "largura_regiao",
        "amplitude",
        "corpo",
        "direcao",
        "evento"
    ]

    registros = sorted(
        historico.values(),
        key=lambda r: r["timestamp"]
    )

    # Recalcula o eixo X pela ordem cronológica persistida.
    for indice, registro in enumerate(registros):
        registro["eixo_x"] = indice

    with open(
        ARQUIVO_SAIDA,
        "w",
        newline="",
        encoding="utf-8"
    ) as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos_saida
        )
        escritor.writeheader()
        escritor.writerows(registros)

    print("================================")
    print("COLETA DO TABULEIRO M5")
    print("================================")
    print(f"Velas recebidas: {len(velas)}")
    print(f"Novas velas coletadas: {novas}")
    print(f"Total no histórico: {len(registros)}")
    print(f"Arquivo gerado: {ARQUIVO_SAIDA}")
    print("Coleta concluída.")


if __name__ == "__main__":
    processar()
