
import csv
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone

# ============================================================
# TABULEIRO ESTAVEL XAUUSD M5 - VERSAO 1
# X = TEMPO | Y = PRECO | ATR = DISTANCIA
# ============================================================

ARQUIVO_HISTORICO = "data/historico_velas_m5.csv"
ARQUIVO_ALTERNATIVO = "data/xauusd_m5_fechados.csv"

SAIDA_TABULEIRO = "data/tabuleiro_estavel_m5.csv"
SAIDA_REGIOES = "data/regioes_estaveis_m5.csv"
SAIDA_TRANSICOES = "data/transicoes_estaveis_m5.csv"
SAIDA_CONTEXTO = "data/contexto_tabuleiro_m5.json"

LARGURA_REGIAO = 1.50
PERIODO_ATR = 14
MIN_AMOSTRAS = 10


def localizar_entrada():
    if os.path.exists(ARQUIVO_HISTORICO):
        return ARQUIVO_HISTORICO

    if os.path.exists(ARQUIVO_ALTERNATIVO):
        return ARQUIVO_ALTERNATIVO

    raise FileNotFoundError(
        "Nenhum arquivo de velas foi encontrado."
    )


def normalizar(texto):
    return (
        str(texto).strip().lower()
        .replace(" ", "_")
        .replace("-", "_")
    )


def localizar_coluna(campos, alternativas):
    mapa = {normalizar(c): c for c in campos}

    for nome in alternativas:
        chave = normalizar(nome)
        if chave in mapa:
            return mapa[chave]

    return None


def converter_numero(valor):
    if valor is None:
        raise ValueError("Número ausente")

    return float(str(valor).strip().replace(",", "."))


def carregar_velas():
    caminho = localizar_entrada()
    velas_por_tempo = {}

    with open(
        caminho,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as arquivo:

        amostra = arquivo.read(4096)
        arquivo.seek(0)

        try:
            delimitador = csv.Sniffer().sniff(
                amostra,
                delimiters=",;|\t"
            ).delimiter
        except csv.Error:
            delimitador = ","

        leitor = csv.DictReader(
            arquivo,
            delimiter=delimitador
        )

        campos = leitor.fieldnames or []

        colunas = {
            "timestamp": localizar_coluna(
                campos,
                [
                    "timestamp_utc",
                    "timestamp",
                    "datetime",
                    "datetime_utc",
                    "time",
                    "date"
                ]
            ),
            "open": localizar_coluna(campos, ["open", "o"]),
            "high": localizar_coluna(campos, ["high", "h"]),
            "low": localizar_coluna(campos, ["low", "l"]),
            "close": localizar_coluna(campos, ["close", "c"])
        }

        ausentes = [
            nome for nome, coluna in colunas.items()
            if coluna is None
        ]

        if ausentes:
            raise ValueError(
                f"Colunas ausentes: {ausentes}. "
                f"Cabeçalhos encontrados: {campos}"
            )

        for linha in leitor:
            try:
                timestamp = str(
                    linha[colunas["timestamp"]]
                ).strip()

                vela = {
                    "timestamp": timestamp,
                    "open": converter_numero(
                        linha[colunas["open"]]
                    ),
                    "high": converter_numero(
                        linha[colunas["high"]]
                    ),
                    "low": converter_numero(
                        linha[colunas["low"]]
                    ),
                    "close": converter_numero(
                        linha[colunas["close"]]
                    )
                }

                if not timestamp:
                    continue

                if vela["high"] < vela["low"]:
                    continue

                if vela["high"] < max(
                    vela["open"], vela["close"]
                ):
                    continue

                if vela["low"] > min(
                    vela["open"], vela["close"]
                ):
                    continue

                # Uma ocorrência por horário.
                velas_por_tempo[timestamp] = vela

            except (ValueError, TypeError, KeyError):
                continue

    velas = sorted(
        velas_por_tempo.values(),
        key=lambda v: v["timestamp"]
    )

    if len(velas) < PERIODO_ATR:
        raise ValueError(
            f"Histórico insuficiente: {len(velas)} velas."
        )

    print(f"Arquivo utilizado: {caminho}")
    return velas


def calcular_atr(velas):
    true_ranges = []
    atrs = []

    for i, vela in enumerate(velas):
        if i == 0:
            tr = vela["high"] - vela["low"]
        else:
            fechamento_anterior = velas[i - 1]["close"]

            tr = max(
                vela["high"] - vela["low"],
                abs(vela["high"] - fechamento_anterior),
                abs(vela["low"] - fechamento_anterior)
            )

        true_ranges.append(tr)

        inicio = max(0, i - PERIODO_ATR + 1)
        janela = true_ranges[inicio:i + 1]

        atr = sum(janela) / len(janela)
        atrs.append(atr)

    return atrs


def construir_tabuleiro(velas, atrs):
    registros = []

    for i, vela in enumerate(velas):
        preco = vela["close"]

        # Y depende exclusivamente do preço e da
        # largura fixa. Nunca depende do ATR.
        regiao = math.floor(
            preco / LARGURA_REGIAO
        )

        limite_inferior = regiao * LARGURA_REGIAO
        limite_superior = limite_inferior + LARGURA_REGIAO

        atr = atrs[i]

        amplitude = vela["high"] - vela["low"]
        corpo = abs(vela["close"] - vela["open"])

        if vela["close"] > vela["open"]:
            direcao = "ALTA"
        elif vela["close"] < vela["open"]:
            direcao = "BAIXA"
        else:
            direcao = "NEUTRA"

        registros.append({
            "timestamp": vela["timestamp"],
            "eixo_x": i,
            "eixo_y": regiao,
            "limite_inferior": round(limite_inferior, 5),
            "limite_superior": round(limite_superior, 5),
            "open": round(vela["open"], 5),
            "high": round(vela["high"], 5),
            "low": round(vela["low"], 5),
            "close": round(preco, 5),
            "atr": round(atr, 5),
            "largura_regiao": LARGURA_REGIAO,
            "amplitude": round(amplitude, 5),
            "corpo": round(corpo, 5),
            "direcao": direcao
        })

    return registros


def calcular_estatisticas(registros):
    visitas = Counter()
    primeira_visita = {}
    ultima_visita = {}
    transicoes = Counter()
    saidas = Counter()

    for registro in registros:
        regiao = registro["eixo_y"]
        tempo = registro["timestamp"]

        visitas[regiao] += 1

        if regiao not in primeira_visita:
            primeira_visita[regiao] = tempo

        ultima_visita[regiao] = tempo

    for i in range(1, len(registros)):
        origem = registros[i - 1]["eixo_y"]
        destino = registros[i]["eixo_y"]

        if origem == destino:
            continue

        transicoes[(origem, destino)] += 1
        saidas[origem] += 1

    regioes = []

    for regiao, quantidade in sorted(visitas.items()):
        inferior = regiao * LARGURA_REGIAO
        superior = inferior + LARGURA_REGIAO

        regioes.append({
            "regiao": regiao,
            "limite_inferior": round(inferior, 5),
            "limite_superior": round(superior, 5),
            "visitas": quantidade,
            "primeira_visita": primeira_visita[regiao],
            "ultima_visita": ultima_visita[regiao]
        })

    lista_transicoes = []

    for (origem, destino), quantidade in sorted(
        transicoes.items()
    ):
        total_saidas = saidas[origem]

        probabilidade = (
            quantidade / total_saidas * 100
            if total_saidas else 0
        )

        lista_transicoes.append({
            "origem": origem,
            "destino": destino,
            "transicoes": quantidade,
            "saidas_origem": total_saidas,
            "probabilidade": round(probabilidade, 2),
            "amostra_suficiente": (
                total_saidas >= MIN_AMOSTRAS
            )
        })

    return regioes, lista_transicoes


def salvar_csv(caminho, registros, campos):
    os.makedirs("data", exist_ok=True)

    with open(
        caminho,
        "w",
        newline="",
        encoding="utf-8"
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos
        )

        escritor.writeheader()
        escritor.writerows(registros)


def gerar_contexto(registros, regioes, transicoes):
    atual = registros[-1]
    regiao_atual = atual["eixo_y"]

    dados_regiao = next(
        (
            r for r in regioes
            if r["regiao"] == regiao_atual
        ),
        None
    )

    proximas = [
        t for t in transicoes
        if t["origem"] == regiao_atual
    ]

    proximas.sort(
        key=lambda t: t["transicoes"],
        reverse=True
    )

    proximas_confiaveis = [
        t for t in proximas
        if t["amostra_suficiente"]
    ]

    return {
        "versao": "1.0",
        "atualizado_em": datetime.now(
            timezone.utc
        ).isoformat(),

        "metodo": {
            "eixo_x": "Uma posição por vela M5 em ordem cronológica",
            "eixo_y": "Faixas fixas de preço",
            "largura_regiao": LARGURA_REGIAO,
            "ancoragem": 0,
            "atr": "Medida de volatilidade, sem alterar regiões",
            "volume": "Não utilizado"
        },

        "diagnostico": {
            "total_velas": len(registros),
            "total_regioes": len(regioes),
            "total_transicoes": len(transicoes),
            "minimo_amostras": MIN_AMOSTRAS
        },

        "mercado_atual": {
            "timestamp": atual["timestamp"],
            "eixo_x": atual["eixo_x"],
            "preco": atual["close"],
            "atr": atual["atr"],
            "direcao_vela": atual["direcao"],
            "regiao": regiao_atual,
            "limite_inferior": atual["limite_inferior"],
            "limite_superior": atual["limite_superior"],
            "visitas_regiao": (
                dados_regiao["visitas"]
                if dados_regiao else 0
            )
        },

        "transicoes_historicas_da_regiao": proximas,
        "transicoes_com_amostra_minima": proximas_confiaveis,

        "observacao": (
            "Frequências históricas não são garantias "
            "de movimentos futuros. Amostras pequenas "
            "não devem ser tratadas como probabilidades confiáveis."
        )
    }


def main():
    print("======================================")
    print("TABULEIRO ESTAVEL XAUUSD M5")
    print("======================================")

    velas = carregar_velas()
    print(f"Velas carregadas: {len(velas)}")

    atrs = calcular_atr(velas)
    registros = construir_tabuleiro(velas, atrs)

    regioes, transicoes = calcular_estatisticas(
        registros
    )

    salvar_csv(
        SAIDA_TABULEIRO,
        registros,
        [
            "timestamp",
            "eixo_x",
            "eixo_y",
            "limite_inferior",
            "limite_superior",
            "open",
            "high",
            "low",
            "close",
            "atr",
            "largura_regiao",
            "amplitude",
            "corpo",
            "direcao"
        ]
    )

    salvar_csv(
        SAIDA_REGIOES,
        regioes,
        [
            "regiao",
            "limite_inferior",
            "limite_superior",
            "visitas",
            "primeira_visita",
            "ultima_visita"
        ]
    )

    salvar_csv(
        SAIDA_TRANSICOES,
        transicoes,
        [
            "origem",
            "destino",
            "transicoes",
            "saidas_origem",
            "probabilidade",
            "amostra_suficiente"
        ]
    )

    contexto = gerar_contexto(
        registros,
        regioes,
        transicoes
    )

    with open(
        SAIDA_CONTEXTO,
        "w",
        encoding="utf-8"
    ) as arquivo:
        json.dump(
            contexto,
            arquivo,
            indent=4,
            ensure_ascii=False
        )

    print("--------------------------------------")
    print(f"Velas: {len(registros)}")
    print(f"Regiões: {len(regioes)}")
    print(f"Transições: {len(transicoes)}")
    print(f"Largura fixa: US$ {LARGURA_REGIAO:.2f}")
    print("--------------------------------------")
    print("Arquivos produzidos:")

    for caminho in [
        SAIDA_TABULEIRO,
        SAIDA_REGIOES,
        SAIDA_TRANSICOES,
        SAIDA_CONTEXTO
    ]:
        print(caminho)

    print("======================================")
    print("PROCESSAMENTO CONCLUIDO")
    print("======================================")


if __name__ == "__main__":
    main()
