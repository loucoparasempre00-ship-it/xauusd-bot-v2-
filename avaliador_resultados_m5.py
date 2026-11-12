
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AVALIADOR DE RESULTADOS M5 - TABULEIRO ESTAVEL

Responsabilidades:
- Ler as velas da matriz XY.
- Ler os eventos do detector de bordas.
- Avaliar o comportamento nas 12 velas seguintes.
- Medir movimentos favoráveis e contrários.
- Avaliar alvos de 0.5 ATR e 1.0 ATR.
- Gerar arquivos de entrega para o Coletor.

Não cria banco paralelo.
Não grava diretamente nos bancos oficiais.
"""

import csv
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

ARQUIVO_VELAS = DATA / "tabuleiro_m5.csv"
ARQUIVO_EVENTOS = DATA / "eventos_bordas_m5.csv"

SAIDA = DATA / "resultados_eventos_m5.csv"
RESUMO = DATA / "resumo_resultados_m5.csv"

HORIZONTE = 12
ALVOS_ATR = [0.5, 1.0]

DIRECOES = {
    "REJEICAO_SUPERIOR": -1,
    "REJEICAO_INFERIOR": 1,
    "ROMPIMENTO_ALTA": 1,
    "ROMPIMENTO_BAIXA": -1,

    # Toques são hipóteses, não confirmações.
    "TOQUE_SUPERIOR": -1,
    "TOQUE_INFERIOR": 1,
}


def numero(valor, campo):
    try:
        return float(
            str(valor).strip().replace(",", ".")
        )
    except (ValueError, TypeError):
        raise ValueError(
            f"Valor inválido em {campo}: {valor}"
        )


def inteiro(valor, campo):
    try:
        return int(float(str(valor).strip()))
    except (ValueError, TypeError):
        raise ValueError(
            f"Valor inválido em {campo}: {valor}"
        )


def ler_csv(caminho):
    if not caminho.is_file():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {caminho}"
        )

    with caminho.open(
        "r",
        newline="",
        encoding="utf-8-sig"
    ) as arquivo:

        leitor = csv.DictReader(arquivo)

        if not leitor.fieldnames:
            raise ValueError(
                f"CSV sem cabeçalho: {caminho}"
            )

        return list(leitor)


def preparar_velas():
    registros = ler_csv(ARQUIVO_VELAS)

    obrigatorias = [
        "timestamp",
        "eixo_x",
        "eixo_y",
        "open",
        "high",
        "low",
        "close",
        "atr",
    ]

    campos = list(registros[0].keys()) if registros else []

    ausentes = [
        nome for nome in obrigatorias
        if nome not in campos
    ]

    if ausentes:
        raise ValueError(
            f"Colunas ausentes na matriz: {ausentes}"
        )

    por_tempo = {}

    for linha in registros:
        try:
            momento = str(
                linha["timestamp"] or ""
            ).strip()

            if not momento:
                continue

            vela = {
                "timestamp": momento,
                "eixo_x": inteiro(
                    linha["eixo_x"], "eixo_x"
                ),
                "regiao": inteiro(
                    linha["eixo_y"], "eixo_y"
                ),
                "open": numero(linha["open"], "open"),
                "high": numero(linha["high"], "high"),
                "low": numero(linha["low"], "low"),
                "close": numero(linha["close"], "close"),
                "atr": numero(linha["atr"], "atr"),
            }

            if vela["high"] < vela["low"]:
                continue

            if vela["atr"] <= 0:
                continue

            por_tempo[momento] = vela

        except (ValueError, TypeError, KeyError):
            continue

    velas = sorted(
        por_tempo.values(),
        key=lambda item: item["eixo_x"]
    )

    if not velas:
        raise ValueError(
            "Nenhuma vela válida encontrada."
        )

    for anterior, atual in zip(velas, velas[1:]):
        if atual["eixo_x"] <= anterior["eixo_x"]:
            raise ValueError(
                "Eixo X duplicado ou fora de ordem."
            )

    return velas


def preparar_eventos():
    registros = ler_csv(ARQUIVO_EVENTOS)

    eventos = []

    for linha in registros:
        try:
            momento = str(
                linha["timestamp"] or ""
            ).strip()

            tipo = str(
                linha["evento"] or ""
            ).strip().upper()

            if not momento or tipo not in DIRECOES:
                continue

            evento = {
                "timestamp": momento,
                "evento": tipo,
                "nivel_borda": numero(
                    linha["nivel_borda"],
                    "nivel_borda"
                ),
            }

            # A região anterior é a região cuja borda
            # foi testada pelo detector novo.
            if linha.get("regiao_anterior") not in (
                None, ""
            ):
                evento["regiao_borda"] = inteiro(
                    linha["regiao_anterior"],
                    "regiao_anterior"
                )
            else:
                evento["regiao_borda"] = None

            if linha.get("regiao_atual") not in (
                None, ""
            ):
                evento["regiao_atual"] = inteiro(
                    linha["regiao_atual"],
                    "regiao_atual"
                )
            else:
                evento["regiao_atual"] = None

            eventos.append(evento)

        except (ValueError, TypeError, KeyError):
            continue

    eventos.sort(key=lambda item: item["timestamp"])

    return eventos


def avaliar_alvo(
    velas_futuras,
    entrada,
    atr,
    direcao,
    multiplicador
):
    distancia = atr * multiplicador

    if direcao == 1:
        alvo = entrada + distancia
        adverso = entrada - distancia
    else:
        alvo = entrada - distancia
        adverso = entrada + distancia

    for indice, vela in enumerate(
        velas_futuras,
        start=1
    ):
        maxima = vela["high"]
        minima = vela["low"]

        if direcao == 1:
            atingiu_alvo = maxima >= alvo
            atingiu_adverso = minima <= adverso
        else:
            atingiu_alvo = minima <= alvo
            atingiu_adverso = maxima >= adverso

        # OHLC não informa qual nível foi atingido primeiro
        # quando alvo e adverso aparecem na mesma vela.
        if atingiu_alvo and atingiu_adverso:
            resultado = "AMBIGUO"

        elif atingiu_alvo:
            resultado = "FAVORAVEL"

        elif atingiu_adverso:
            resultado = "DESFAVORAVEL"

        else:
            continue

        return {
            "resultado": resultado,
            "velas": indice,
            "preco_alvo": round(alvo, 5),
            "preco_adverso": round(adverso, 5),
        }

    return {
        "resultado": "NAO_ATINGIDO",
        "velas": len(velas_futuras),
        "preco_alvo": round(alvo, 5),
        "preco_adverso": round(adverso, 5),
    }


def avaliar_evento(evento, indice, velas):
    tipo = evento["evento"]
    direcao = DIRECOES[tipo]

    vela_evento = velas[indice]

    entrada = vela_evento["close"]
    atr = vela_evento["atr"]

    if atr <= 0:
        return None

    futuras = velas[
        indice + 1:
        indice + 1 + HORIZONTE
    ]

    # Só avalia eventos que tenham as 12 velas seguintes.
    if len(futuras) < HORIZONTE:
        return None

    melhor_movimento = 0.0
    pior_movimento = 0.0

    for vela in futuras:
        if direcao == 1:
            favoravel = vela["high"] - entrada
            contrario = entrada - vela["low"]
        else:
            favoravel = entrada - vela["low"]
            contrario = vela["high"] - entrada

        melhor_movimento = max(
            melhor_movimento,
            favoravel
        )

        pior_movimento = max(
            pior_movimento,
            contrario
        )

    resultados = {}

    for alvo in ALVOS_ATR:
        avaliacao = avaliar_alvo(
            futuras,
            entrada,
            atr,
            direcao,
            alvo
        )

        chave = str(alvo).replace(".", "_")

        resultados[f"resultado_{chave}atr"] = (
            avaliacao["resultado"]
        )

        resultados[f"velas_{chave}atr"] = (
            avaliacao["velas"]
        )

        resultados[f"preco_alvo_{chave}atr"] = (
            avaliacao["preco_alvo"]
        )

    regiao_borda = evento["regiao_borda"]

    if regiao_borda is None:
        regiao_borda = vela_evento["regiao"]

    registro = {
        "timestamp_evento": evento["timestamp"],
        "tipo_evento": tipo,
        "regiao_borda": regiao_borda,
        "regiao_fechamento": vela_evento["regiao"],
        "nivel_borda": round(
            evento["nivel_borda"], 5
        ),
        "direcao_esperada": (
            "ALTA" if direcao == 1 else "BAIXA"
        ),
        "entrada": round(entrada, 5),
        "atr_evento": round(atr, 5),
        "horizonte_velas": HORIZONTE,
        "maximo_favoravel_atr": round(
            melhor_movimento / atr, 4
        ),
        "maximo_contrario_atr": round(
            pior_movimento / atr, 4
        ),
        "timestamp_fim": futuras[-1]["timestamp"],
    }

    registro.update(resultados)

    return registro


def salvar_resultados(resultados):
    campos = [
        "timestamp_evento",
        "tipo_evento",
        "regiao_borda",
        "regiao_fechamento",
        "nivel_borda",
        "direcao_esperada",
        "entrada",
        "atr_evento",
        "horizonte_velas",
        "maximo_favoravel_atr",
        "maximo_contrario_atr",
        "resultado_0_5atr",
        "velas_0_5atr",
        "preco_alvo_0_5atr",
        "resultado_1_0atr",
        "velas_1_0atr",
        "preco_alvo_1_0atr",
        "timestamp_fim",
    ]

    DATA.mkdir(parents=True, exist_ok=True)

    with SAIDA.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos
        )

        escritor.writeheader()
        escritor.writerows(resultados)


def gerar_resumo(resultados):
    grupos = defaultdict(lambda: {
        "eventos_avaliados": 0,
        "favoravel_05": 0,
        "desfavoravel_05": 0,
        "ambiguo_05": 0,
        "nao_atingido_05": 0,
        "favoravel_10": 0,
        "desfavoravel_10": 0,
        "ambiguo_10": 0,
        "nao_atingido_10": 0,
    })

    for resultado in resultados:
        tipo = resultado["tipo_evento"]
        grupo = grupos[tipo]

        grupo["eventos_avaliados"] += 1

        for chave_alvo, sufixo in [
            ("0_5atr", "05"),
            ("1_0atr", "10"),
        ]:
            valor = resultado[
                f"resultado_{chave_alvo}"
            ]

            coluna = {
                "FAVORAVEL": f"favoravel_{sufixo}",
                "DESFAVORAVEL": f"desfavoravel_{sufixo}",
                "AMBIGUO": f"ambiguo_{sufixo}",
                "NAO_ATINGIDO": f"nao_atingido_{sufixo}",
            }.get(valor)

            if coluna:
                grupo[coluna] += 1

    campos = [
        "tipo_evento",
        "eventos_avaliados",
        "favoravel_05",
        "desfavoravel_05",
        "ambiguo_05",
        "nao_atingido_05",
        "favoravel_10",
        "desfavoravel_10",
        "ambiguo_10",
        "nao_atingido_10",
        "taxa_favoravel_05",
        "taxa_favoravel_10",
    ]

    DATA.mkdir(parents=True, exist_ok=True)

    with RESUMO.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos
        )

        escritor.writeheader()

        for tipo, grupo in sorted(grupos.items()):
            total = grupo["eventos_avaliados"]

            avaliaveis_05 = (
                total - grupo["ambiguo_05"]
            )

            avaliaveis_10 = (
                total - grupo["ambiguo_10"]
            )

            taxa_05 = (
                grupo["favoravel_05"]
                / avaliaveis_05 * 100
                if avaliaveis_05 else 0
            )

            taxa_10 = (
                grupo["favoravel_10"]
                / avaliaveis_10 * 100
                if avaliaveis_10 else 0
            )

            escritor.writerow({
                "tipo_evento": tipo,
                **grupo,
                "taxa_favoravel_05": round(
                    taxa_05, 2
                ),
                "taxa_favoravel_10": round(
                    taxa_10, 2
                ),
            })


def main():
    print("======================================")
    print("AVALIADOR DE RESULTADOS TABULEIRO M5")
    print("======================================")

    print("1/4 - Carregando matriz XY...")

    velas = preparar_velas()

    print("Velas válidas:", len(velas))

    print("2/4 - Carregando eventos de borda...")

    eventos = preparar_eventos()

    print("Eventos encontrados:", len(eventos))

    indice_por_timestamp = {
        vela["timestamp"]: indice
        for indice, vela in enumerate(velas)
    }

    print("3/4 - Avaliando histórico futuro...")

    resultados = []
    sem_vela = 0
    sem_horizonte = 0

    for evento in eventos:
        indice = indice_por_timestamp.get(
            evento["timestamp"]
        )

        if indice is None:
            sem_vela += 1
            continue

        if len(
            velas[indice + 1:
                  indice + 1 + HORIZONTE]
        ) < HORIZONTE:
            sem_horizonte += 1
            continue

        resultado = avaliar_evento(
            evento,
            indice,
            velas
        )

        if resultado is not None:
            resultados.append(resultado)

    salvar_resultados(resultados)

    print("Eventos avaliados:", len(resultados))
    print("Eventos sem vela correspondente:", sem_vela)
    print("Eventos sem horizonte completo:", sem_horizonte)

    print("4/4 - Gerando resumo...")

    gerar_resumo(resultados)

    print("--------------------------------------")
    print("Arquivos de entrega:")
    print(SAIDA)
    print(RESUMO)
    print("--------------------------------------")
    print("Horizonte:", HORIZONTE, "velas M5")
    print("Alvos:", ALVOS_ATR)
    print("Banco paralelo criado: NÃO")
    print("Gravação direta nos bancos oficiais: NÃO")
    print("Persistência: Coletor e Organizador")
    print("Atualizado em:", datetime.now(
        timezone.utc
    ).isoformat())
    print("PROCESSAMENTO CONCLUÍDO")
    print("======================================")


if __name__ == "__main__":
    main()
