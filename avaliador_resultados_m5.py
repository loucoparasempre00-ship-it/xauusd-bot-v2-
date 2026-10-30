
import csv
import os
from datetime import datetime, timezone

ARQUIVO_VELAS = "data/tabuleiro_m5.csv"
ARQUIVO_EVENTOS = "data/eventos_bordas_m5.csv"

SAIDA = "data/resultados_eventos_m5.csv"
RESUMO = "data/resumo_resultados_m5.csv"

# Quantas velas fechadas serão observadas depois do evento.
HORIZONTE = 12

# Distâncias de avaliação em ATR.
ALVOS_ATR = [0.5, 1.0]

# Relação entre evento e direção esperada.
DIRECOES = {
    "REJEICAO_SUPERIOR": -1,
    "REJEICAO_INFERIOR": 1,
    "ROMPIMENTO_ALTA": 1,
    "ROMPIMENTO_BAIXA": -1,

    # Toques são hipóteses, não confirmações.
    "TOQUE_SUPERIOR": -1,
    "TOQUE_INFERIOR": 1,
}


def ler_csv(caminho):
    if not os.path.exists(caminho):
        raise FileNotFoundError(
            f"Arquivo não encontrado: {caminho}"
        )

    with open(
        caminho,
        "r",
        newline="",
        encoding="utf-8-sig"
    ) as arquivo:
        return list(csv.DictReader(arquivo))


def numero(valor, padrao=None):
    try:
        return float(str(valor).replace(",", "."))
    except (ValueError, TypeError):
        return padrao


def campo(linha, nomes, padrao=None):
    for nome in nomes:
        valor = linha.get(nome)

        if valor not in (None, ""):
            return valor

    return padrao


def timestamp(linha):
    return str(campo(
        linha,
        ["timestamp", "timestamp_utc"],
        ""
    ))


def preparar_velas():
    velas = ler_csv(ARQUIVO_VELAS)

    velas.sort(key=timestamp)

    resultado = []

    for vela in velas:
        resultado.append({
            "timestamp": timestamp(vela),
            "open": numero(vela.get("open")),
            "high": numero(vela.get("high")),
            "low": numero(vela.get("low")),
            "close": numero(vela.get("close")),
            "atr": numero(vela.get("atr")),
            "regiao": campo(
                vela,
                ["eixo_y", "regiao"],
                ""
            )
        })

    return resultado


def preparar_eventos():
    eventos = ler_csv(ARQUIVO_EVENTOS)

    eventos.sort(key=timestamp)

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

    for indice, vela in enumerate(velas_futuras, start=1):
        maxima = vela["high"]
        minima = vela["low"]

        if maxima is None or minima is None:
            continue

        if direcao == 1:
            atingiu_alvo = maxima >= alvo
            atingiu_adverso = minima <= adverso
        else:
            atingiu_alvo = minima <= alvo
            atingiu_adverso = maxima >= adverso

        # Se os dois níveis forem atingidos na mesma vela,
        # não sabemos qual ocorreu primeiro usando apenas OHLC.
        if atingiu_alvo and atingiu_adverso:
            return {
                "resultado": "AMBIGUO",
                "velas": indice,
                "preco_alvo": alvo,
                "preco_adverso": adverso
            }

        if atingiu_alvo:
            return {
                "resultado": "FAVORAVEL",
                "velas": indice,
                "preco_alvo": alvo,
                "preco_adverso": adverso
            }

        if atingiu_adverso:
            return {
                "resultado": "DESFAVORAVEL",
                "velas": indice,
                "preco_alvo": alvo,
                "preco_adverso": adverso
            }

    return {
        "resultado": "NAO_ATINGIDO",
        "velas": len(velas_futuras),
        "preco_alvo": alvo,
        "preco_adverso": adverso
    }


def avaliar_evento(evento, indice, velas):
    tipo = campo(evento, ["event", "evento"], "")

    direcao = DIRECOES.get(tipo)

    if direcao is None:
        return None

    vela_evento = velas[indice]

    entrada = vela_evento["close"]
    atr = vela_evento["atr"]

    if entrada is None or atr is None or atr <= 0:
        return None

    futuras = velas[
        indice + 1:
        indice + 1 + HORIZONTE
    ]

    if not futuras:
        return None

    # Somente eventos com horizonte completo são avaliados.
    if len(futuras) < HORIZONTE:
        return None

    melhor_movimento = 0
    pior_movimento = 0

    for vela in futuras:
        if direcao == 1:
            favoravel = vela["high"] - entrada
            contrario = entrada - vela["low"]
        else:
            favoravel = entrada - vela["low"]
            contrario = vela["high"] - entrada

        if vela["high"] is None or vela["low"] is None:
            continue

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

        resultados[f"resultado_{alvo}atr"] = (
            avaliacao["resultado"]
        )

        resultados[f"velas_{alvo}atr"] = (
            avaliacao["velas"]
        )

    registro = {
        "timestamp_evento": timestamp(evento),
        "tipo_evento": tipo,
        "regiao": vela_evento["regiao"],
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
        "timestamp_fim": futuras[-1]["timestamp"]
    }

    registro.update(resultados)

    return registro


def salvar_resultados(resultados):
    os.makedirs("data", exist_ok=True)

    campos = [
        "timestamp_evento",
        "tipo_evento",
        "regiao",
        "direcao_esperada",
        "entrada",
        "atr_evento",
        "horizonte_velas",
        "maximo_favoravel_atr",
        "maximo_contrario_atr",
        "resultado_0.5atr",
        "velas_0.5atr",
        "resultado_1.0atr",
        "velas_1.0atr",
        "timestamp_fim"
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
        escritor.writerows(resultados)


def gerar_resumo(resultados):
    grupos = {}

    for resultado in resultados:
        tipo = resultado["tipo_evento"]

        if tipo not in grupos:
            grupos[tipo] = {
                "eventos_avaliados": 0,
                "favoravel_05": 0,
                "desfavoravel_05": 0,
                "ambiguo_05": 0,
                "nao_atingido_05": 0,
                "favoravel_10": 0,
                "desfavoravel_10": 0,
                "ambiguo_10": 0,
                "nao_atingido_10": 0
            }

        grupo = grupos[tipo]
        grupo["eventos_avaliados"] += 1

        for alvo, sufixo in [
            ("0.5atr", "05"),
            ("1.0atr", "10")
        ]:
            valor = resultado[f"resultado_{alvo}"]

            chave = {
                "FAVORAVEL": f"favoravel_{sufixo}",
                "DESFAVORAVEL": f"desfavoravel_{sufixo}",
                "AMBIGUO": f"ambiguo_{sufixo}",
                "NAO_ATINGIDO": f"nao_atingido_{sufixo}"
            }.get(valor)

            if chave:
                grupo[chave] += 1

    with open(
        RESUMO,
        "w",
        newline="",
        encoding="utf-8"
    ) as arquivo:

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
            "taxa_favoravel_10"
        ]

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
                grupo["favoravel_05"] / avaliaveis_05 * 100
                if avaliaveis_05 else 0
            )

            taxa_10 = (
                grupo["favoravel_10"] / avaliaveis_10 * 100
                if avaliaveis_10 else 0
            )

            escritor.writerow({
                "tipo_evento": tipo,
                **grupo,
                "taxa_favoravel_05": round(taxa_05, 2),
                "taxa_favoravel_10": round(taxa_10, 2)
            })


def main():
    print("1/4 - Carregando velas fechadas...")

    velas = preparar_velas()

    print(f"Velas carregadas: {len(velas)}")

    print("2/4 - Carregando eventos de borda...")

    eventos = preparar_eventos()

    print(f"Eventos encontrados: {len(eventos)}")

    indice_por_timestamp = {
        vela["timestamp"]: i
        for i, vela in enumerate(velas)
    }

    print("3/4 - Avaliando resultados futuros...")

    resultados = []

    for evento in eventos:
        momento = timestamp(evento)

        indice = indice_por_timestamp.get(momento)

        if indice is None:
            continue

        resultado = avaliar_evento(
            evento,
            indice,
            velas
        )

        if resultado is not None:
            resultados.append(resultado)

    salvar_resultados(resultados)

    print(f"Eventos avaliados: {len(resultados)}")

    print("4/4 - Gerando resumo por tipo de evento...")

    gerar_resumo(resultados)

    print("\nArquivos gerados:")
    print(SAIDA)
    print(RESUMO)

    print("\nAvaliação concluída.")


if __name__ == "__main__":
    main()
