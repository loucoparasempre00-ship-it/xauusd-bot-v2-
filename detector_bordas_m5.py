import csv
import os
from collections import Counter

ENTRADA = "data/tabuleiro_m5.csv"
SAIDA = "data/eventos_bordas_m5.csv"

JANELA = 24
TOLERANCIA_ATR = 0.25
CONFIRMACAO_ATR = 0.15
MIN_ATR = 0.00001


def carregar_dados():
    if not os.path.exists(ENTRADA):
        raise FileNotFoundError(
            f"Arquivo não encontrado: {ENTRADA}"
        )

    with open(ENTRADA, "r", encoding="utf-8-sig") as arquivo:
        leitor = csv.DictReader(arquivo)

        obrigatorias = [
            "timestamp", "open", "high",
            "low", "close", "atr"
        ]

        if not leitor.fieldnames:
            raise ValueError("CSV sem cabeçalho.")

        ausentes = [
            c for c in obrigatorias
            if c not in leitor.fieldnames
        ]

        if ausentes:
            raise ValueError(
                f"Colunas ausentes: {ausentes}"
            )

        dados = []

        for linha in leitor:
            try:
                vela = {
                    "timestamp": linha["timestamp"],
                    "open": float(linha["open"]),
                    "high": float(linha["high"]),
                    "low": float(linha["low"]),
                    "close": float(linha["close"]),
                    "atr": float(linha["atr"])
                }

                if vela["atr"] > MIN_ATR:
                    dados.append(vela)

            except (ValueError, TypeError):
                continue

    dados.sort(key=lambda x: x["timestamp"])
    return dados


def identificar_bordas(velas):
    eventos = []

    for i in range(JANELA, len(velas)):
        atual = velas[i]
        anteriores = velas[i - JANELA:i]

        # As bordas são calculadas apenas com candles anteriores.
        borda_superior = max(
            v["high"] for v in anteriores
        )

        borda_inferior = min(
            v["low"] for v in anteriores
        )

        atr = atual["atr"]

        tolerancia = atr * TOLERANCIA_ATR
        confirmacao = atr * CONFIRMACAO_ATR

        abertura = atual["open"]
        maxima = atual["high"]
        minima = atual["low"]
        fechamento = atual["close"]

        eventos_vela = []

        # Distância entre preço e bordas.
        toque_superior = (
            maxima >= borda_superior - tolerancia
            and abertura <= borda_superior + tolerancia
        )

        toque_inferior = (
            minima <= borda_inferior + tolerancia
            and abertura >= borda_inferior - tolerancia
        )

        # Rompimentos confirmados pelo fechamento.
        rompimento_alta = (
            fechamento > borda_superior + confirmacao
        )

        rompimento_baixa = (
            fechamento < borda_inferior - confirmacao
        )

        # Rejeições: o preço testa a região e fecha
        # novamente para dentro dela.
        rejeicao_superior = (
            toque_superior
            and fechamento < borda_superior - confirmacao
            and fechamento < abertura
        )

        rejeicao_inferior = (
            toque_inferior
            and fechamento > borda_inferior + confirmacao
            and fechamento > abertura
        )

        if rompimento_alta:
            eventos_vela.append((
                "ROMPIMENTO_ALTA",
                borda_superior
            ))

        elif rejeicao_superior:
            eventos_vela.append((
                "REJEICAO_SUPERIOR",
                borda_superior
            ))

        elif toque_superior:
            eventos_vela.append((
                "TOQUE_SUPERIOR",
                borda_superior
            ))

        if rompimento_baixa:
            eventos_vela.append((
                "ROMPIMENTO_BAIXA",
                borda_inferior
            ))

        elif rejeicao_inferior:
            eventos_vela.append((
                "REJEICAO_INFERIOR",
                borda_inferior
            ))

        elif toque_inferior:
            eventos_vela.append((
                "TOQUE_INFERIOR",
                borda_inferior
            ))

        for tipo, nivel in eventos_vela:
            distancia = abs(fechamento - nivel)

            eventos.append({
                "timestamp": atual["timestamp"],
                "indice_vela": i,
                "evento": tipo,
                "nivel_borda": round(nivel, 5),
                "preco_fechamento": round(fechamento, 5),
                "atr": round(atr, 5),
                "distancia_atr": round(
                    distancia / atr, 4
                ),
                "janela": JANELA
            })

    return eventos


def salvar_eventos(eventos):
    os.makedirs("data", exist_ok=True)

    campos = [
        "timestamp",
        "indice_vela",
        "evento",
        "nivel_borda",
        "preco_fechamento",
        "atr",
        "distancia_atr",
        "janela"
    ]

    with open(
        SAIDA,
        "w",
        newline="",
        encoding="utf-8"
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos
        )

        escritor.writeheader()
        escritor.writerows(eventos)


def gerar_resumo(eventos):
    contagem = Counter(
        evento["evento"] for evento in eventos
    )

    print("\n========== DETECTOR DE BORDAS M5 ==========")

    for tipo in [
        "TOQUE_SUPERIOR",
        "TOQUE_INFERIOR",
        "REJEICAO_SUPERIOR",
        "REJEICAO_INFERIOR",
        "ROMPIMENTO_ALTA",
        "ROMPIMENTO_BAIXA"
    ]:
        print(f"{tipo}: {contagem[tipo]}")

    print("-------------------------------------------")
    print(f"Total de eventos: {len(eventos)}")
    print(f"Arquivo: {SAIDA}")
    print("Processamento concluído.")
    print("===========================================\n")


def processar():
    velas = carregar_dados()

    print(f"Velas carregadas: {len(velas)}")

    if len(velas) <= JANELA:
        raise ValueError(
            f"São necessárias mais de {JANELA} velas."
        )

    eventos = identificar_bordas(velas)

    salvar_eventos(eventos)
    gerar_resumo(eventos)


if __name__ == "__main__":
    processar()
