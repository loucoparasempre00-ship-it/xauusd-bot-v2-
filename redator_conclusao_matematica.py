#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transforma dados ja existentes nas dez tabelas em uma conclusao matematica compacta.
Nao altera o banco, nao recalcula indicadores e nao substitui o motor de sinais.
"""
import json
import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "xauusd.db"
OUTPUT = ROOT / "data" / "conclusao_matematica.json"
TABLES = {
    "m5", "m15", "h1", "indicadores", "tabuleiro",
    "sinais_validados", "sinais_negados", "ranking",
    "estatisticas_regioes", "estatisticas_transicoes",
}
TIMEFRAMES = {"M5": ("m5", 5), "M15": ("m15", 15), "H1": ("h1", 60)}
REGION_WIDTH = 1.50


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def open_db():
    if not DB.is_file():
        raise FileNotFoundError(f"Banco oficial ausente: {DB}")
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    names = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    if names != TABLES:
        raise RuntimeError("Banco nao contem exatamente as dez tabelas oficiais: " + str(sorted(names)))
    return connection


def rows(connection, query, params=()):
    return [dict(row) for row in connection.execute(query, params).fetchall()]


def candles_context(connection):
    result = {}
    for tf, (table, minutes) in TIMEFRAMES.items():
        raw = rows(connection, f"SELECT data_hora,abertura,maxima,minima,fechamento FROM {table} ORDER BY data_hora DESC LIMIT 30")
        now_utc = datetime.now(timezone.utc)
        data = []
        for candle in raw:
            try:
                opened = datetime.strptime(candle["data_hora"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if opened + timedelta(minutes=minutes) <= now_utc:
                data.append(candle)
        data.reverse()
        if not data:
            raise RuntimeError(f"Sem candles fechados em {table}.")
        latest = data[-1]
        result[tf] = {
            "referencia": latest["data_hora"],
            "ultimo_fechamento": number(latest["fechamento"]),
            "velas": data[-6:],
            "ponto_a": number(data[0]["fechamento"]),
            "ponto_b": number(latest["fechamento"]),
            "deslocamento": round(number(latest["fechamento"]) - number(data[0]["fechamento"]), 5),
            "direcao_deslocamento": "ALTA" if number(latest["fechamento"]) > number(data[0]["fechamento"]) else "BAIXA" if number(latest["fechamento"]) < number(data[0]["fechamento"]) else "LATERAL",
            "quantidade_velas_contexto": len(data[-6:]),
        }
    return result


def indicator_context(connection):
    result = {}
    for tf in TIMEFRAMES:
        data = rows(connection, """
            SELECT data,hora,preco,ema9,ema21,rsi,macd,atr
            FROM indicadores WHERE timeframe=?
            ORDER BY data DESC,hora DESC LIMIT 8
        """, (tf,))
        data.reverse()
        if not data:
            result[tf] = {"disponibilidade": "SEM_DADOS"}
            continue
        latest = data[-1]
        prev = data[-2] if len(data) > 1 else None
        e9, e21 = number(latest["ema9"]), number(latest["ema21"])
        pe9 = number(prev["ema9"]) if prev else None
        pe21 = number(prev["ema21"]) if prev else None
        cross = "SEM_CRUZAMENTO_RECENTE"
        if None not in (e9, e21, pe9, pe21):
            if pe9 <= pe21 and e9 > e21:
                cross = "CRUZAMENTO_DE_ALTA"
            elif pe9 >= pe21 and e9 < e21:
                cross = "CRUZAMENTO_DE_BAIXA"
        rsi_now = number(latest["rsi"])
        rsi_prev = number(prev["rsi"]) if prev else None
        macd_now = number(latest["macd"])
        macd_prev = number(prev["macd"]) if prev else None
        result[tf] = {
            "referencia": latest["data"] + " " + latest["hora"],
            "ema9": e9, "ema21": e21,
            "relacao_ema": "EMA9_ACIMA_EMA21" if e9 is not None and e21 is not None and e9 > e21 else "EMA9_ABAIXO_EMA21" if e9 is not None and e21 is not None and e9 < e21 else "INDEFINIDA",
            "cruzamento_entre_ultimos_registros": cross,
            "distancia_ema": round(e9 - e21, 6) if e9 is not None and e21 is not None else None,
            "rsi14": rsi_now,
            "direcao_rsi": "SUBINDO" if rsi_now is not None and rsi_prev is not None and rsi_now > rsi_prev else "CAINDO" if rsi_now is not None and rsi_prev is not None and rsi_now < rsi_prev else "ESTAVEL_OU_SEM_COMPARACAO",
            "macd": macd_now,
            "momentum_macd": "ACELERANDO_POSITIVO" if macd_now is not None and macd_prev is not None and macd_now > macd_prev and macd_now > 0 else "PERDENDO_FORCA_POSITIVA" if macd_now is not None and macd_prev is not None and macd_now < macd_prev and macd_now > 0 else "ACELERANDO_NEGATIVO" if macd_now is not None and macd_prev is not None and macd_now < macd_prev and macd_now < 0 else "PERDENDO_FORCA_NEGATIVA" if macd_now is not None and macd_prev is not None and macd_now > macd_prev and macd_now < 0 else "SEM_VARIACAO_COMPARAVEL",
            "atr": number(latest["atr"]),
        }
    return result


def board_context(connection):
    result = {}
    for tf in TIMEFRAMES:
        latest = connection.execute(
            "SELECT MAX(data_hora) FROM tabuleiro WHERE timeframe=?", (tf,)
        ).fetchone()[0]
        if not latest:
            result[tf] = {"disponibilidade": "SEM_DADOS"}
            continue
        state = connection.execute("""
            SELECT sequencia_json,quantidade_candles,ocorrencias_encontradas,ocorrencias_exatas,distancia_maxima,versao
            FROM tabuleiro WHERE timeframe=? AND data_hora=? AND tipo_registro='ESTADO'
              AND sequencia_json IS NOT NULL
            ORDER BY id DESC LIMIT 1
        """, (tf, latest)).fetchone()
        probabilities = rows(connection, """
            SELECT horizonte,evento_futuro,ocorrencias,total_amostras,probabilidade,distancia_media
            FROM tabuleiro WHERE timeframe=? AND data_hora=? AND tipo_registro='PROBABILIDADE'
            ORDER BY horizonte,probabilidade DESC
        """, (tf, latest))
        parsed_state = None
        if state:
            try:
                parsed_state = json.loads(state["sequencia_json"])
            except (TypeError, json.JSONDecodeError):
                parsed_state = state["sequencia_json"]
        result[tf] = {
            "referencia": latest,
            "estado_sequencia": parsed_state,
            "candles_historicos_analisados": state["quantidade_candles"] if state else None,
            "correspondencias_historicas": state["ocorrencias_encontradas"] if state else None,
            "correspondencias_exatas": state["ocorrencias_exatas"] if state else None,
            "probabilidades_horizontes_1_2_3": probabilities,
            "nota": "Frequencias historicas do Tabuleiro; nao sao garantias."
        }
    return result


def border_context(connection, price):
    regions = rows(connection, """
        SELECT regiao,limite_inferior,limite_superior,visitas,toques,rejeicoes,rompimentos,
               prob_toque,prob_rejeicao,prob_rompimento,amostras,amostra_suficiente,
               rejeicoes_superiores,rejeicoes_inferiores,rompimentos_alta,rompimentos_baixa,
               classificacao
        FROM estatisticas_regioes WHERE timeframe='M5'
        ORDER BY regiao
    """)
    if price is None:
        return {"regioes_proximas": [], "transicoes_da_regiao_atual": []}
    current_region = math.floor(price / REGION_WIDTH)
    nearby = sorted(regions, key=lambda x: min(abs(price-number(x["limite_inferior"])), abs(price-number(x["limite_superior"]))))[:8]
    transitions = rows(connection, """
        SELECT origem,destino,transicoes,saidas_origem,probabilidade,visitas_origem,amostra_suficiente
        FROM estatisticas_transicoes WHERE timeframe='M5' AND origem=?
        ORDER BY probabilidade DESC
    """, (current_region,))
    return {
        "regiao_atual": current_region,
        "regioes_proximas": nearby,
        "transicoes_da_regiao_atual": transitions,
        "nota": "Estatisticas de regioes e transicoes existentes no banco; niveis M5."
    }


def history_context(connection):
    result = {}
    for table in ("sinais_validados", "sinais_negados", "ranking"):
        total = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        result[table] = {"registros": total}
    ranking = rows(connection, """
        SELECT direcao,status_resultado,resultado_usdt FROM ranking
    """)
    closed = [x for x in ranking if number(x["resultado_usdt"]) is not None and str(x["status_resultado"]).upper() != "PENDENTE"]
    result["ranking"].update({
        "encerradas": len(closed),
        "pendentes": len(ranking) - len(closed),
        "resultado_liquido_usdt": round(sum(number(x["resultado_usdt"]) for x in closed), 4),
        "por_direcao": {
            direction: {
                "encerradas": sum(1 for x in closed if str(x["direcao"]).upper() == direction),
                "positivas": sum(1 for x in closed if str(x["direcao"]).upper() == direction and number(x["resultado_usdt"]) > 0),
                "negativas": sum(1 for x in closed if str(x["direcao"]).upper() == direction and number(x["resultado_usdt"]) < 0),
            } for direction in ("LONG", "SHORT")
        }
    })
    return result


def main():
    connection = open_db()
    try:
        candles = candles_context(connection)
        price = candles["M5"]["ultimo_fechamento"]
        report = {
            "tipo": "CONCLUSAO_MATEMATICA_XAUUSD",
            "versao": 1,
            "gerado_em_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "referencia_m5": candles["M5"]["referencia"],
            "preco_referencia": price,
            "trajetoria_dos_precos": candles,
            "medias_rsi_macd_atr": indicator_context(connection),
            "projecoes_tabuleiro": board_context(connection),
            "suportes_resistencias_e_transicoes_m5": border_context(connection, price),
            "historico_operacional_resumido": history_context(connection),
            "limites_da_leitura": [
                "Somente organiza dados ja armazenados; nao altera nem recalcula indicadores.",
                "Nao substitui o motor matematico, seus pesos, medias ou criterios.",
                "Probabilidades do Tabuleiro sao frequencias historicas, nao garantias.",
                "Nao ha estatisticas consolidadas de regioes M15/H1 no esquema oficial; nesses tempos usam-se candles e indicadores existentes."
            ]
        }
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        temp = OUTPUT.with_suffix(".tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")
        temp.replace(OUTPUT)
        encoded = OUTPUT.read_bytes()
        print("CONCLUSAO MATEMATICA GERADA")
        print("Referencia M5:", report["referencia_m5"])
        print("Preco:", report["preco_referencia"])
        print("Tamanho do relatorio:", len(encoded), "bytes")
        print("Dados consultados: candles, indicadores, tabuleiro, regioes, transicoes e historico agregado.")
        print("Banco original preservado; nenhuma tabela ou registro foi alterado.")
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ERRO AO GERAR CONCLUSAO MATEMATICA:", error, file=sys.stderr)
        raise
