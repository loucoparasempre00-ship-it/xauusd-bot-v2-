import csv
import json
import math
import os
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

API = "https://generativelanguage.googleapis.com/v1beta"
DATA = Path("data")
OUT = DATA / "experimento"
ANTERIOR = DATA / "anterior"


def agora():
    return datetime.now(timezone.utc)


def horario(valor):
    dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def ler_json(caminho, padrao=None):
    caminho = Path(caminho)
    if not caminho.exists():
        return padrao
    return json.loads(caminho.read_text(encoding="utf-8"))


def salvar_json(caminho, dados):
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(dados, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def normalizar(nome):
    return "".join(c.lower() for c in nome if c.isalnum())


def carregar_candles():
    caminho = DATA / "candles" / "xauusd_m5_fechados.csv"

    with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
        leitor = csv.DictReader(arquivo)
        colunas = {normalizar(c): c for c in leitor.fieldnames}

        def coluna(*nomes):
            for nome in nomes:
                if normalizar(nome) in colunas:
                    return colunas[normalizar(nome)]
            raise ValueError("Coluna ausente: " + str(nomes))

        ct = coluna("timestamp_utc", "timestamp", "datetime", "time", "date")
        co = coluna("open", "abertura")
        ch = coluna("high", "maxima", "máxima")
        cl = coluna("low", "minima", "mínima")
        cc = coluna("close", "fechamento")

        candles = []
        for linha in leitor:
            try:
                item = {
                    "time": horario(linha[ct]),
                    "open": float(linha[co]),
                    "high": float(linha[ch]),
                    "low": float(linha[cl]),
                    "close": float(linha[cc])
                }
                valores = [
                    item["open"], item["high"],
                    item["low"], item["close"]
                ]
                if not all(math.isfinite(v) and v > 0 for v in valores):
                    continue
                candles.append(item)
            except (ValueError, TypeError, KeyError):
                continue

    unicos = {c["time"]: c for c in candles}
    return [unicos[t] for t in sorted(unicos)]


def agregar(candles, minutos):
    quantidade = minutos // 5
    grupos = defaultdict(list)

    for candle in candles:
        segundo = int(candle["time"].timestamp())
        inicio = (segundo // (minutos * 60)) * (minutos * 60)
        grupos[inicio].append(candle)

    resultado = []

    for inicio in sorted(grupos):
        grupo = sorted(grupos[inicio], key=lambda x: x["time"])

        if len(grupo) != quantidade:
            continue

        consecutivos = all(
            (grupo[i]["time"] - grupo[i - 1]["time"]).total_seconds() == 300
            for i in range(1, len(grupo))
        )

        if not consecutivos:
            continue

        resultado.append({
            "time": datetime.fromtimestamp(inicio, timezone.utc),
            "open": grupo[0]["open"],
            "high": max(x["high"] for x in grupo),
            "low": min(x["low"] for x in grupo),
            "close": grupo[-1]["close"]
        })

    return resultado


def resumir(candles, limite):
    return [
        {
            "utc": c["time"].isoformat(),
            "open": round(c["open"], 3),
            "high": round(c["high"], 3),
            "low": round(c["low"], 3),
            "close": round(c["close"], 3)
        }
        for c in candles[-limite:]
    ]


def calcular_atr(candles):
    if len(candles) < 15:
        raise ValueError("Candles insuficientes para ATR.")

    ranges = []

    for i in range(1, len(candles)):
        atual = candles[i]
        anterior = candles[i - 1]["close"]

        ranges.append(max(
            atual["high"] - atual["low"],
            abs(atual["high"] - anterior),
            abs(atual["low"] - anterior)
        ))

    atr = sum(ranges[-14:]) / 14

    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("ATR invalido.")

    return atr


def avaliar_operacoes(historico, candles, fim):
    for operacao in historico:
        if operacao.get("status") != "EM_ABERTO":
            continue

        entrada_tempo = horario(operacao["candle_utc"])
        entrada = float(operacao["entrada"])
        stop = float(operacao["stop"])
        alvo = float(operacao["alvo"])
        lado = operacao["decisao"]

        for candle in candles:
            if candle["time"] <= entrada_tempo:
                continue
            if candle["time"] > fim:
                break

            stop_atingido = (
                candle["low"] <= stop if lado == "COMPRA"
                else candle["high"] >= stop
            )
            alvo_atingido = (
                candle["high"] >= alvo if lado == "COMPRA"
                else candle["low"] <= alvo
            )

            if stop_atingido and alvo_atingido:
                operacao.update({
                    "status": "AMBIGUO_MESMO_CANDLE",
                    "resultado_r": None,
                    "resultado_utc": candle["time"].isoformat()
                })
                break

            if stop_atingido:
                operacao.update({
                    "status": "STOP",
                    "resultado_r": -1.0,
                    "resultado_utc": candle["time"].isoformat()
                })
                break

            if alvo_atingido:
                operacao.update({
                    "status": "ALVO",
                    "resultado_r": 1.5,
                    "resultado_utc": candle["time"].isoformat()
                })
                break


def salvar_resultados(historico, estado):
    OUT.mkdir(parents=True, exist_ok=True)

    salvar_json(OUT / "historico_gemini_livre.json", historico)
    salvar_json(OUT / "estado_experimento.json", estado)

    campos = [
        "candle_utc", "decisao", "confianca", "entrada",
        "stop", "alvo", "status", "resultado_r",
        "resultado_utc", "justificativa",
        "contexto_m5", "contexto_m15", "contexto_h1"
    ]

    with (OUT / "resultados_gemini_livre.csv").open(
        "w", encoding="utf-8", newline=""
    ) as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=campos)
        escritor.writeheader()
        for item in historico:
            escritor.writerow({
                campo: item.get(campo) for campo in campos
            })


def resumo(historico, estado):
    concluidas = [
        x for x in historico if x.get("resultado_r") is not None
    ]

    return {
        "status": estado["status"],
        "inicio_utc": estado["inicio_utc"],
        "fim_previsto_utc": estado["fim_previsto_utc"],
        "total_avaliacoes": len(historico),
        "compras": sum(x["decisao"] == "COMPRA" for x in historico),
        "vendas": sum(x["decisao"] == "VENDA" for x in historico),
        "sem_sinal": sum(x["decisao"] == "SEM_SINAL" for x in historico),
        "operacoes_concluidas": len(concluidas),
        "resultado_total_r": round(
            sum(x["resultado_r"] for x in concluidas), 4
        ),
        "observacao": (
            "Simulacao sem spread, slippage ou custos. "
            "Nao representa resultado garantido."
        )
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    analise = ler_json(DATA / "analise_tecnica_contexto.json")
    selecao = ler_json(DATA / "modelo_gemini.json")

    if not analise or selecao.get("status") != "pronto":
        raise SystemExit("Analise tecnica ou selecao Gemini indisponivel.")

    modelo = selecao.get("modelo_escolhido")
    if not isinstance(modelo, str) or not modelo.startswith("models/gemini-"):
        raise SystemExit("Modelo Gemini invalido.")

    candles = carregar_candles()
    if len(candles) < 100:
        raise SystemExit("Historico M5 insuficiente.")

    estado_anterior = ler_json(
        ANTERIOR / "estado_experimento.json", {}
    )
    historico = ler_json(
        ANTERIOR / "historico_gemini_livre.json", []
    )

    if not isinstance(historico, list):
        historico = []

    instante = agora()

    if estado_anterior.get("inicio_utc"):
        inicio = horario(estado_anterior["inicio_utc"])
        status = estado_anterior.get("status", "ATIVO")
    else:
        inicio = instante
        status = "ATIVO"

    fim = inicio + timedelta(hours=24)

    estado = {
        "experimento": "Gemini Livre XAUUSD",
        "status": status,
        "inicio_utc": inicio.isoformat(),
        "fim_previsto_utc": fim.isoformat(),
        "ultima_execucao_utc": instante.isoformat(),
        "telegram": False,
        "ordens_automaticas": False
    }

    avaliar_operacoes(historico, candles, fim)

    if status == "ENCERRADO" or instante >= fim:
        ultimos = [c for c in candles if c["time"] <= fim]

        if ultimos:
            fechamento = ultimos[-1]["close"]
            fechamento_tempo = ultimos[-1]["time"].isoformat()

            for op in historico:
                if op.get("status") != "EM_ABERTO":
                    continue

                risco = abs(float(op["entrada"]) - float(op["stop"]))
                movimento = (
                    fechamento - float(op["entrada"])
                    if op["decisao"] == "COMPRA"
                    else float(op["entrada"]) - fechamento
                )

                op.update({
                    "status": "ENCERRADO_24H",
                    "resultado_r": round(movimento / risco, 4),
                    "resultado_utc": fechamento_tempo
                })

        estado["status"] = "ENCERRADO"
        salvar_resultados(historico, estado)
        salvar_json(OUT / "resumo_experimento.json", resumo(historico, estado))
        print("Experimento de 24 horas encerrado.")
        return

    candle = candles[-1]
    candle_id = candle["time"].isoformat()

    if any(x.get("candle_utc") == candle_id for x in historico):
        estado["ultima_decisao"] = "CANDLE_JA_AVALIADO"
        salvar_resultados(historico, estado)
        return

    m15 = agregar(candles, 15)
    h1 = agregar(candles, 60)

    if len(m15) < 30 or len(h1) < 30:
        raise SystemExit("Historico insuficiente para M15/H1.")

    contexto = {
        "analise_tecnica_existente_apenas_como_referencia": analise,
        "candles_m5": resumir(candles, 120),
        "candles_m15": resumir(m15, 48),
        "candles_h1": resumir(h1, 36),
        "historico_experimental_recente": historico[-20:]
    }

    prompt = """
Voce e um analista independente de XAUUSD M5.

Este experimento busca avaliar sua capacidade de interpretar o mercado
com autonomia. A analise tecnica existente e apenas uma referencia.
NAO siga obrigatoriamente a pontuacao, os pesos, os filtros ou a decisao
do motor atual.

Interprete os candles diretamente.

M5 mostra o comportamento imediato e o possivel gatilho.
M15 mostra a estrutura intermediaria, correcao ou continuidade.
H1 mostra o contexto mais amplo.

Nao exija alinhamento perfeito entre os tres tempos graficos.
Uma divergencia nao e veto automatico. Avalie se ela representa
correcao, transicao, perda de impulso ou reversao.

Considere tendencia, estrutura, impulso, rejeicao, rompimento,
falso rompimento, consolidacao, volatilidade e esgotamento.
Nao force operacoes. SEM_SINAL e uma decisao valida.

Nao invente indicadores, precos, noticias ou dados ausentes.
Nao trate confianca como probabilidade estatistica comprovada.
Nao execute ordens e nao prometa lucro.

Escolha COMPRA, VENDA ou SEM_SINAL.
Explique a leitura separadamente para M5, M15 e H1.
Informe os riscos que poderiam invalidar sua leitura.

Responda somente JSON valido:
{
  "decisao": "SEM_SINAL",
  "confianca": 0,
  "justificativa": "Leitura objetiva",
  "contexto_m5": "Resumo",
  "contexto_m15": "Resumo",
  "contexto_h1": "Resumo",
  "riscos": ["risco 1", "risco 2"]
}

DADOS:
""" + json.dumps(contexto, ensure_ascii=False, separators=(",", ":"))

    chave = os.getenv("GEMINI_API_KEY", "").strip()
    if not chave:
        raise SystemExit("Secret GEMINI_API_KEY ausente.")

    corpo = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.6,
            "maxOutputTokens": 1200,
            "responseMimeType": "application/json"
        }
    }

    pedido = urllib.request.Request(
        API + "/" + modelo + ":generateContent",
        data=json.dumps(corpo).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": chave
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(pedido, timeout=60) as resposta:
            retorno = json.loads(resposta.read().decode("utf-8"))

        texto = retorno["candidates"][0]["content"]["parts"][0]["text"]
        decisao = json.loads(texto)

    except urllib.error.HTTPError as erro:
        print("Erro Gemini:", erro.code)
        print(erro.read().decode("utf-8", "replace")[:1000])
        raise
    except Exception as erro:
        raise SystemExit("Falha na resposta Gemini: " + str(erro))

    lado = decisao.get("decisao")
    if lado not in ("COMPRA", "VENDA", "SEM_SINAL"):
        raise SystemExit("Decisao Gemini invalida.")

    try:
        confianca = float(decisao.get("confianca", 0))
        if not math.isfinite(confianca):
            confianca = 0
        confianca = max(0, min(100, confianca))
    except (TypeError, ValueError):
        confianca = 0

    registro = {
        "candle_utc": candle_id,
        "decisao": lado,
        "confianca": round(confianca, 1),
        "justificativa": str(decisao.get("justificativa", ""))[:1500],
        "contexto_m5": str(decisao.get("contexto_m5", ""))[:500],
        "contexto_m15": str(decisao.get("contexto_m15", ""))[:500],
        "contexto_h1": str(decisao.get("contexto_h1", ""))[:500],
        "riscos": decisao.get("riscos", []),
        "modelo": modelo,
        "status": "SEM_OPERACAO",
        "entrada": None,
        "stop": None,
        "alvo": None,
        "resultado_r": None,
        "telegram": False,
        "ordens_automaticas": False
    }

    if lado in ("COMPRA", "VENDA"):
        atr = calcular_atr(candles)
        entrada = candle["close"]
        distancia = atr * 1.5

        if lado == "COMPRA":
            stop = entrada - distancia
            alvo = entrada + distancia * 1.5
        else:
            stop = entrada + distancia
            alvo = entrada - distancia * 1.5

        registro.update({
            "status": "EM_ABERTO",
            "entrada": round(entrada, 3),
            "atr14": round(atr, 5),
            "stop": round(stop, 3),
            "alvo": round(alvo, 3),
            "risco_retorno": "1:1.5"
        })

    historico.append(registro)

    estado.update({
        "status": "ATIVO",
        "ultima_decisao": lado,
        "ultimo_candle_utc": candle_id,
        "total_avaliacoes": len(historico)
    })

    salvar_resultados(historico, estado)
    salvar_json(OUT / "ultima_decisao.json", registro)
    salvar_json(OUT / "resumo_experimento.json", resumo(historico, estado))

    print(json.dumps(registro, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
