
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BANCO DE DADOS DO TABULEIRO M5

Responsabilidade:
- Ler a matriz M5 já construída.
- Validar e normalizar os registros.
- Gerar arquivos de entrega.
- Não criar banco SQLite.
- Não gravar diretamente nos bancos oficiais.

Toda persistência passa pelo Coletor e Organizador.
"""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

CSV_ENTRADA = DATA / "tabuleiro_m5.csv"

CSV_ENTREGA = DATA / "entrega_tabuleiro_m5.csv"
JSON_CONTROLE = DATA / "controle_tabuleiro_m5.json"

CAMPOS = [
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
    "evento",
]


def agora():
    return datetime.now(timezone.utc).isoformat()


def numero(valor, campo):
    try:
        resultado = float(str(valor).strip().replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError(
            f"Valor numérico inválido no campo {campo}: {valor}"
        )

    return resultado


def inteiro(valor, campo):
    try:
        return int(float(str(valor).strip()))
    except (TypeError, ValueError):
        raise ValueError(
            f"Valor inteiro inválido no campo {campo}: {valor}"
        )


def carregar_registros():
    if not CSV_ENTRADA.is_file():
        raise FileNotFoundError(
            f"Matriz do tabuleiro não encontrada: {CSV_ENTRADA}"
        )

    registros = {}
    descartados = 0

    with CSV_ENTRADA.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as arquivo:

        leitor = csv.DictReader(arquivo)
        campos_origem = leitor.fieldnames or []

        ausentes = [
            campo for campo in CAMPOS
            if campo not in campos_origem
        ]

        if ausentes:
            raise ValueError(
                f"Colunas ausentes na matriz: {ausentes}"
            )

        for linha in leitor:
            try:
                timestamp = str(
                    linha["timestamp"] or ""
                ).strip()

                if not timestamp:
                    descartados += 1
                    continue

                registro = {
                    "timestamp": timestamp,
                    "eixo_x": inteiro(
                        linha["eixo_x"], "eixo_x"
                    ),
                    "eixo_y": inteiro(
                        linha["eixo_y"], "eixo_y"
                    ),
                    "open": numero(linha["open"], "open"),
                    "high": numero(linha["high"], "high"),
                    "low": numero(linha["low"], "low"),
                    "close": numero(linha["close"], "close"),
                    "atr": numero(linha["atr"], "atr"),
                    "largura_regiao": numero(
                        linha["largura_regiao"],
                        "largura_regiao"
                    ),
                    "amplitude": numero(
                        linha["amplitude"], "amplitude"
                    ),
                    "corpo": numero(
                        linha["corpo"], "corpo"
                    ),
                    "direcao": str(
                        linha["direcao"] or "NEUTRA"
                    ).strip().upper(),
                    "evento": str(
                        linha["evento"] or "VISITA"
                    ).strip().upper(),
                }

                if registro["high"] < registro["low"]:
                    descartados += 1
                    continue

                if registro["largura_regiao"] <= 0:
                    descartados += 1
                    continue

                # Uma única ocorrência por timestamp.
                registros[timestamp] = registro

            except (ValueError, TypeError, KeyError):
                descartados += 1

    ordenados = sorted(
        registros.values(),
        key=lambda item: item["timestamp"]
    )

    if not ordenados:
        raise RuntimeError(
            "Nenhum registro válido foi encontrado na matriz."
        )

    return ordenados, descartados


def validar_sequencia(registros):
    erros = []

    for anterior, atual in zip(
        registros,
        registros[1:]
    ):
        if atual["eixo_x"] <= anterior["eixo_x"]:
            erros.append({
                "timestamp": atual["timestamp"],
                "erro": "Eixo X não está em ordem crescente."
            })

    return erros


def gerar_entrega(registros):
    DATA.mkdir(parents=True, exist_ok=True)

    with CSV_ENTREGA.open(
        "w",
        encoding="utf-8",
        newline=""
    ) as arquivo:

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=CAMPOS
        )

        escritor.writeheader()
        escritor.writerows(registros)


def gerar_controle(registros, descartados, erros):
    regioes = {}

    for registro in registros:
        eixo_y = str(registro["eixo_y"])

        if eixo_y not in regioes:
            regioes[eixo_y] = {
                "visitas": 0,
                "primeira_visita": registro["timestamp"],
                "ultima_visita": registro["timestamp"],
            }

        regioes[eixo_y]["visitas"] += 1
        regioes[eixo_y]["ultima_visita"] = registro["timestamp"]

    controle = {
        "versao": 2,
        "modulo": "banco_dados_tabuleiro",
        "atualizado_em": agora(),
        "status": "SUCESSO" if not erros else "ERRO",
        "origem": CSV_ENTRADA.name,
        "entrega": CSV_ENTREGA.name,
        "persistencia_direta": False,
        "persistencia": (
            "Coletor e Organizador"
        ),
        "metodo_regioes": {
            "largura_fixa_usd": 1.50,
            "eixo_x": "Tempo cronológico M5",
            "eixo_y": "Região de preço",
            "atr_altera_largura": False,
        },
        "resumo": {
            "total_registros": len(registros),
            "total_regioes": len(regioes),
            "registros_descartados": descartados,
            "total_erros": len(erros),
            "primeiro_timestamp": registros[0]["timestamp"],
            "ultimo_timestamp": registros[-1]["timestamp"],
        },
        "regioes": regioes,
        "erros": erros,
    }

    JSON_CONTROLE.write_text(
        json.dumps(
            controle,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def processar():
    print("======================================")
    print("PREPARADOR DE ENTREGA DO TABULEIRO M5")
    print("======================================")

    registros, descartados = carregar_registros()

    erros = validar_sequencia(registros)

    if erros:
        raise RuntimeError(
            "A sequência cronológica da matriz apresenta erros. "
            "Entrega interrompida para proteger os dados."
        )

    gerar_entrega(registros)

    gerar_controle(
        registros,
        descartados,
        erros
    )

    regioes = len({
        registro["eixo_y"]
        for registro in registros
    })

    print("--------------------------------------")
    print("Registros válidos:", len(registros))
    print("Regiões identificadas:", regioes)
    print("Registros descartados:", descartados)
    print("--------------------------------------")
    print("Arquivos de entrega:")
    print(CSV_ENTREGA)
    print(JSON_CONTROLE)
    print("--------------------------------------")
    print("Nenhum banco SQLite foi criado ou alterado.")
    print("Persistência reservada ao Coletor e Organizador.")
    print("PROCESSAMENTO CONCLUIDO")
    print("======================================")


if __name__ == "__main__":
    processar()
