
import csv
import json
import os
from datetime import datetime, timezone

ARQUIVOS = {
    "regioes": "data/estatisticas_regioes_m5.csv",
    "transicoes": "data/transicoes_probabilidade_m5.csv",
    "sequencias": "data/sequencias_provaveis_m5.csv",
    "resultados": "data/resultados_eventos_m5.csv",
    "resumo": "data/resumo_resultados_m5.csv",
    "eventos": "data/eventos_bordas_m5.csv",
    "tabuleiro": "data/tabuleiro_m5.csv"
}

SAIDA_JSON = "data/relatorio_tabuleiro_m5.json"
SAIDA_CSV = "data/relatorio_geral_tabuleiro_m5.csv"


def ler_csv(caminho):
    if not os.path.exists(caminho):
        return []

    with open(
        caminho,
        "r",
        newline="",
        encoding="utf-8-sig"
    ) as arquivo:
        return list(csv.DictReader(arquivo))


def numero(valor, padrao=0):
    try:
        return float(str(valor).replace(",", "."))
    except (ValueError, TypeError):
        return padrao


def carregar_dados():
    return {
        nome: ler_csv(caminho)
        for nome, caminho in ARQUIVOS.items()
    }


def contar_eventos(eventos):
    contagem = {}

    for evento in eventos:
        tipo = evento.get("event", "DESCONHECIDO")

        contagem[tipo] = contagem.get(tipo, 0) + 1

    return contagem


def melhores_regioes(regioes):
    def pontuacao(item):
        return numero(
            item.get("probabilidade_rejeicao"),
            numero(item.get("taxa_rejeicao"))
        )

    ordenadas = sorted(
        regioes,
        key=pontuacao,
        reverse=True
    )

    return ordenadas[:10]


def melhores_transicoes(transicoes):
    ordenadas = sorted(
        transicoes,
        key=lambda x: numero(
            x.get("probabilidade"),
            numero(x.get("probabilidade_percentual"))
        ),
        reverse=True
    )

    return ordenadas[:15]


def melhores_sequencias(sequencias):
    ordenadas = sorted(
        sequencias,
        key=lambda x: numero(
            x.get("probabilidade")
        ),
        reverse=True
    )

    return ordenadas[:15]


def consolidar_resultados(resumos):
    resultado = []

    for item in resumos:
        total = int(
            numero(item.get("eventos_avaliados"))
        )

        resultado.append({
            "evento": item.get("tipo_evento"),
            "amostras": total,
            "taxa_favoravel_05": numero(
                item.get("taxa_favoravel_05")
            ),
            "taxa_favoravel_10": numero(
                item.get("taxa_favoravel_10")
            ),
            "favoraveis_05": int(
                numero(item.get("favoravel_05"))
            ),
            "desfavoraveis_05": int(
                numero(item.get("desfavoravel_05"))
            ),
            "ambiguos_05": int(
                numero(item.get("ambiguo_05"))
            )
        })

    return resultado


def gerar_diagnostico(dados):
    quantidade_velas = len(dados["tabuleiro"])
    quantidade_eventos = len(dados["eventos"])
    quantidade_resultados = len(dados["resultados"])

    if quantidade_velas == 0:
        estado = "SEM_DADOS"
    elif quantidade_eventos == 0:
        estado = "AGUARDANDO_EVENTOS"
    elif quantidade_resultados == 0:
        estado = "AGUARDANDO_HORIZONTE"
    else:
        estado = "DADOS_DISPONIVEIS"

    return {
        "estado": estado,
        "velas": quantidade_velas,
        "eventos": quantidade_eventos,
        "eventos_avaliados": quantidade_resultados,
        "regioes_analisadas": len(dados["regioes"]),
        "transicoes_analisadas": len(dados["transicoes"]),
        "sequencias_calculadas": len(dados["sequencias"])
    }


def gerar_relatorio(dados):
    diagnostico = gerar_diagnostico(dados)

    relatorio = {
        "atualizado_em": datetime.now(
            timezone.utc
        ).isoformat(),

        "diagnostico": diagnostico,

        "eventos": contar_eventos(
            dados["eventos"]
        ),

        "melhores_regioes": melhores_regioes(
            dados["regioes"]
        ),

        "melhores_transicoes": melhores_transicoes(
            dados["transicoes"]
        ),

        "melhores_sequencias": melhores_sequencias(
            dados["sequencias"]
        ),

        "desempenho_eventos": consolidar_resultados(
            dados["resumo"]
        ),

        "limites": [
            "Probabilidades representam frequências históricas.",
            "Sequências não garantem movimentos futuros.",
            "Resultados recentes podem estar incompletos.",
            "Regiões precisam de numeração estável.",
            "Amostras pequenas não devem ser tratadas como evidência forte."
        ]
    }

    return relatorio


def salvar_json(relatorio):
    with open(
        SAIDA_JSON,
        "w",
        encoding="utf-8"
    ) as arquivo:
        json.dump(
            relatorio,
            arquivo,
            indent=4,
            ensure_ascii=False
        )


def salvar_csv(relatorio):
    linhas = []

    diagnostico = relatorio["diagnostico"]

    for chave, valor in diagnostico.items():
        linhas.append({
            "categoria": "DIAGNOSTICO",
            "indicador": chave,
            "valor": valor
        })

    for tipo, quantidade in relatorio["eventos"].items():
        linhas.append({
            "categoria": "EVENTO",
            "indicador": tipo,
            "valor": quantidade
        })

    for item in relatorio["desempenho_eventos"]:
        linhas.append({
            "categoria": "DESEMPENHO",
            "indicador": item["evento"],
            "valor": (
                f"Amostras={item['amostras']}; "
                f"Favorável 0.5 ATR="
                f"{item['taxa_favoravel_05']}%; "
                f"Favorável 1 ATR="
                f"{item['taxa_favoravel_10']}%"
            )
        })

    with open(
        SAIDA_CSV,
        "w",
        newline="",
        encoding="utf-8"
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=[
                "categoria",
                "indicador",
                "valor"
            ]
        )

        escritor.writeheader()
        escritor.writerows(linhas)


def main():
    print("1/3 - Carregando módulos do tabuleiro...")

    dados = carregar_dados()

    print("2/3 - Consolidando estatísticas...")

    relatorio = gerar_relatorio(dados)

    print("3/3 - Salvando relatório geral...")

    salvar_json(relatorio)
    salvar_csv(relatorio)

    print("\nDIAGNÓSTICO DO TABULEIRO")

    for chave, valor in relatorio["diagnostico"].items():
        print(f"{chave}: {valor}")

    print("\nRelatórios gerados:")
    print(SAIDA_JSON)
    print(SAIDA_CSV)

    print("\nConsolidação concluída.")


if __name__ == "__main__":
    main()
