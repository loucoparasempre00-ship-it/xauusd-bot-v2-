import csv
import json
from pathlib import Path
from datetime import datetime, timezone

DATA = Path("data")


def ler_csv(nome, separador=","):
    caminho = DATA / nome

    if not caminho.exists():
        return []

    with caminho.open(
        encoding="utf-8-sig",
        newline=""
    ) as arquivo:
        return list(csv.DictReader(
            arquivo,
            delimiter=separador
        ))


def converter_horario(valor):
    if not valor:
        return None

    try:
        texto = str(valor).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(texto)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except (ValueError, TypeError):
        return None


def anteriores(registros, campo, limite):
    validos = []

    for registro in registros:
        momento = converter_horario(registro.get(campo))

        if momento is not None and momento <= limite:
            validos.append(registro)

    return validos


def main():
    arquivo_analise = DATA / "analise_tecnica_contexto.json"

    if not arquivo_analise.exists():
        raise SystemExit("Analise tecnica nao encontrada.")

    analise = json.loads(
        arquivo_analise.read_text(encoding="utf-8")
    )

    candle_atual = converter_horario(
        analise.get("candle_utc")
    )

    if candle_atual is None:
        raise SystemExit("Horario do candle invalido.")

    tabuleiro = ler_csv("tabuleiro_m5.csv")
    eventos = ler_csv("eventos_bordas_m5.csv")
    resultados = ler_csv("resultados_eventos_m5.csv")
    regioes = ler_csv("estatisticas_regioes_m5.csv")
    transicoes = ler_csv("transicoes_probabilidade_m5.csv")
    sequencias = ler_csv("sequencias_provaveis_m5.csv")
    operacoes = ler_csv(
        "resultados_operacoes.csv",
        separador=";"
    )

    candles = anteriores(
        tabuleiro,
        "timestamp",
        candle_atual
    )

    candles.sort(
        key=lambda item: converter_horario(
            item.get("timestamp")
        )
    )

    resultados_concluidos = [
        item for item in resultados
        if (
            converter_horario(item.get("timestamp_fim"))
            is not None
            and converter_horario(item.get("timestamp_fim"))
            < candle_atual
        )
    ]

    eventos_relacionados = []

    for evento in eventos:
        momento = converter_horario(
            evento.get("timestamp")
        )

        if momento is None or momento > candle_atual:
            continue

        avaliacao = next(
            (
                resultado
                for resultado in resultados_concluidos
                if (
                    converter_horario(
                        resultado.get("timestamp_evento")
                    ) == momento
                    and resultado.get("tipo_evento", "").upper()
                    == evento.get("evento", "").upper()
                )
            ),
            None
        )

        eventos_relacionados.append({
            "evento": evento,
            "avaliacao": avaliacao
        })

    operacoes_reais = []

    for operacao in operacoes:
        if operacao.get("tipo_avaliacao", "").upper() != "REAL":
            continue

        if not operacao.get("status"):
            continue

        if operacao.get("status").upper() == "EM_ABERTO":
            continue

        horario_resultado = converter_horario(
            operacao.get("horario_resultado_utc")
        )

        if horario_resultado is None:
            continue

        if horario_resultado < candle_atual:
            operacoes_reais.append(operacao)

    contexto = {
        "candle_analisado": candle_atual.isoformat(),

        "mercado_atual": analise,

        "tabuleiro": {
            "candle_mais_recente": (
                candles[-1] if candles else None
            ),
            "ultimos_12_candles": candles[-12:],
            "eventos": eventos_relacionados[-80:]
        },

        "resultados_concluidos": resultados_concluidos[-100:],

        "operacoes_reais_concluidas": operacoes_reais[-100:],

        "estatisticas_historicas": {
            "regioes": regioes[-30:],
            "transicoes": transicoes[-30:],
            "sequencias": sequencias[-20:]
        }
    }

    saida = DATA / "contexto_aprendizado_gemini.json"

    saida.write_text(
        json.dumps(
            contexto,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print("Contexto de aprendizado criado.")
    print("Candles:", len(candles))
    print("Eventos:", len(eventos_relacionados))
    print("Resultados de eventos:", len(resultados_concluidos))
    print("Operacoes reais:", len(operacoes_reais))


if __name__ == "__main__":
    main()
