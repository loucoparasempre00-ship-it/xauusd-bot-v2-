import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

ARQUIVO_TABULEIRO = "data/tabuleiro_m5.csv"
ARQUIVO_TRANSICOES = "data/transicoes_probabilidade_m5.csv"

SAIDA_SEQUENCIAS = "data/sequencias_provaveis_m5.csv"
SAIDA_JSON = "data/proximas_regioes_m5.json"

MIN_AMOSTRAS = 5
LIMITE_CAMINHOS = 100


def ler_csv(caminho):
    if not os.path.exists(caminho):
        return []

    with open(caminho, "r", newline="", encoding="utf-8-sig") as arquivo:
        return list(csv.DictReader(arquivo))


def numero(valor, padrao=0):
    try:
        return float(str(valor).replace(",", "."))
    except (ValueError, TypeError):
        return padrao


def inteiro(valor, padrao=0):
    try:
        return int(float(valor))
    except (ValueError, TypeError):
        return padrao


def obter_campo(linha, nomes, padrao=None):
    for nome in nomes:
        if nome in linha and linha[nome] not in (None, ""):
            return linha[nome]
    return padrao


def carregar_regiao_atual():
    velas = ler_csv(ARQUIVO_TABULEIRO)

    if not velas:
        raise RuntimeError("Tabuleiro M5 ainda não possui velas.")

    velas.sort(
        key=lambda x: obter_campo(
            x, ["timestamp", "timestamp_utc"], ""
        )
    )

    ultima = velas[-1]

    regiao = inteiro(
        obter_campo(ultima, ["eixo_y", "regiao", "nivel"])
    )

    timestamp = obter_campo(
        ultima, ["timestamp", "timestamp_utc"], ""
    )

    return regiao, timestamp


def carregar_transicoes():
    linhas = ler_csv(ARQUIVO_TRANSICOES)

    transicoes = defaultdict(list)

    for linha in linhas:
        origem = obter_campo(
            linha,
            ["origem", "regiao_origem", "de", "eixo_y_origem"]
        )

        destino = obter_campo(
            linha,
            ["destino", "regiao_destino", "para", "eixo_y_destino"]
        )

        probabilidade = obter_campo(
            linha,
            ["probabilidade", "probabilidade_transicao", "taxa"]
        )

        quantidade = obter_campo(
            linha,
            ["quantidade", "contagem", "ocorrencias", "total"]
        )

        if origem is None or destino is None:
            continue

        origem = inteiro(origem)
        destino = inteiro(destino)
        probabilidade = numero(probabilidade)
        quantidade = inteiro(quantidade)

        if probabilidade > 1:
            probabilidade /= 100

        transicoes[origem].append({
            "destino": destino,
            "probabilidade": probabilidade,
            "quantidade": quantidade
        })

    return transicoes


def normalizar_transicoes(transicoes):
    """
    Recalcula as probabilidades usando as contagens disponíveis.
    Se não houver contagens, preserva as probabilidades recebidas.
    """

    resultado = {}

    for origem, destinos in transicoes.items():
        total = sum(x["quantidade"] for x in destinos)

        resultado[origem] = []

        for item in destinos:
            if total > 0:
                prob = item["quantidade"] / total
            else:
                prob = item["probabilidade"]

            resultado[origem].append({
                "destino": item["destino"],
                "probabilidade": prob,
                "quantidade": item["quantidade"]
            })

        resultado[origem].sort(
            key=lambda x: x["probabilidade"],
            reverse=True
        )

    return resultado


def gerar_caminhos(regiao_atual, transicoes):
    caminhos = []

    proximos = transicoes.get(regiao_atual, [])

    for primeiro in proximos:
        regiao_2 = primeiro["destino"]

        if primeiro["quantidade"] < MIN_AMOSTRAS:
            continue

        prob_1 = primeiro["probabilidade"]

        caminhos.append({
            "origem": regiao_atual,
            "regiao_2": regiao_2,
            "regiao_3": "",
            "probabilidade": prob_1,
            "amostras": primeiro["quantidade"],
            "etapas": 1
        })

        seguintes = transicoes.get(regiao_2, [])

        for segundo in seguintes:
            regiao_3 = segundo["destino"]

            if segundo["quantidade"] < MIN_AMOSTRAS:
                continue

            prob_2 = segundo["probabilidade"]

            prob_caminho = prob_1 * prob_2

            caminhos.append({
                "origem": regiao_atual,
                "regiao_2": regiao_2,
                "regiao_3": regiao_3,
                "probabilidade": prob_caminho,
                "amostras": segundo["quantidade"],
                "etapas": 2
            })

    caminhos.sort(
        key=lambda x: x["probabilidade"],
        reverse=True
    )

    return caminhos[:LIMITE_CAMINHOS]


def salvar_csv(caminhos):
    os.makedirs("data", exist_ok=True)

    campos = [
        "origem",
        "regiao_2",
        "regiao_3",
        "probabilidade",
        "probabilidade_percentual",
        "amostras",
        "etapas",
        "confiabilidade_amostral"
    ]

    with open(
        SAIDA_SEQUENCIAS,
        "w",
        newline="",
        encoding="utf-8"
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos
        )

        escritor.writeheader()

        for caminho in caminhos:
            quantidade = caminho["amostras"]

            escritor.writerow({
                **caminho,
                "probabilidade_percentual": round(
                    caminho["probabilidade"] * 100, 2
                ),
                "probabilidade": round(
                    caminho["probabilidade"], 6
                ),
                "confiabilidade_amostral":
                    "AMOSTRA_MINIMA_ATINGIDA"
                    if quantidade >= MIN_AMOSTRAS
                    else "AMOSTRA_INSUFICIENTE"
            })


def salvar_json(regiao_atual, timestamp, caminhos):
    diretos = [
        c for c in caminhos
        if c["etapas"] == 1
    ]

    resultado = {
        "atualizado_em": datetime.now(
            timezone.utc
        ).isoformat(),

        "ultima_vela": timestamp,
        "regiao_atual": regiao_atual,

        "quantidade_caminhos": len(caminhos),

        "proximas_regioes": [
            {
                "regiao": c["regiao_2"],
                "probabilidade": round(
                    c["probabilidade"], 6
                ),
                "percentual": round(
                    c["probabilidade"] * 100, 2
                ),
                "amostras": c["amostras"]
            }
            for c in diretos
        ],

        "melhores_caminhos": caminhos[:10],

        "observacao": (
            "Probabilidades históricas condicionais. "
            "Não representam garantia de movimento futuro."
        )
    }

    with open(
        SAIDA_JSON,
        "w",
        encoding="utf-8"
    ) as arquivo:
        json.dump(
            resultado,
            arquivo,
            indent=4,
            ensure_ascii=False
        )


def main():
    print("1/4 - Identificando região atual...")

    regiao_atual, timestamp = carregar_regiao_atual()

    print(f"Região atual: {regiao_atual}")
    print(f"Última vela: {timestamp}")

    print("2/4 - Carregando transições históricas...")

    transicoes = carregar_transicoes()
    transicoes = normalizar_transicoes(transicoes)

    print(f"Regiões com transições: {len(transicoes)}")

    print("3/4 - Calculando caminhos prováveis...")

    caminhos = gerar_caminhos(
        regiao_atual,
        transicoes
    )

    salvar_csv(caminhos)

    print(f"Caminhos encontrados: {len(caminhos)}")

    print("4/4 - Gerando leitura do tabuleiro...")

    salvar_json(
        regiao_atual,
        timestamp,
        caminhos
    )

    if caminhos:
        print("\nPRINCIPAIS CAMINHOS:")

        for caminho in caminhos[:5]:
            print(
                f"{caminho['origem']} -> "
                f"{caminho['regiao_2']} -> "
                f"{caminho['regiao_3']} | "
                f"{caminho['probabilidade'] * 100:.2f}%"
            )
    else:
        print(
            "Ainda não existem amostras suficientes "
            "para construir caminhos."
        )

    print("\nSequenciador concluído.")


if __name__ == "__main__":
    main()
