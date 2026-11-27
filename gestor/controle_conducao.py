#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Controle da condução operacional do Gestor XAUUSD.

O banco oficial é consultado somente em leitura. O estado de acompanhamento
fica em um JSON versionado no repositório; nenhum dado é enviado ao Cloudflare.
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "xauusd.db"
STATE = ROOT / "data" / "gestor_conducao_estado.json"
CONTEXT = ROOT / "gestor_operacao_contexto.json"
RESULT = ROOT / "gestor_conducao_resultado.json"
TABLES_REQUIRED = {
    "m5", "m15", "h1", "indicadores", "tabuleiro",
    "sinais_validados", "sinais_negados", "ranking",
    "estatisticas_regioes", "estatisticas_transicoes",
}


def now():
    return datetime.now(timezone.utc)


def stamp():
    return now().isoformat(timespec="seconds")


def parse_dt(value):
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def number(value):
    try:
        result = float(str(value).replace(",", "."))
        return result if result == result and abs(result) != float("inf") else None
    except (TypeError, ValueError):
        return None


def default_state():
    return {
        "versao": 1,
        "conducao_ativa": False,
        "operacao": None,
        "chat_id": None,
        "message_id": None,
        "ultima_decisao": None,
        "atualizado_utc": stamp(),
    }


def load_state():
    if not STATE.is_file():
        return default_state()
    data = json.loads(STATE.read_text(encoding="utf-8"))
    if data.get("versao") != 1:
        raise RuntimeError("Versão do estado de condução incompatível.")
    return data


def save_state(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix="gestor_conducao_", suffix=".json", dir=str(STATE.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, STATE)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


def output_active(value):
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as file:
            file.write("ativa=" + ("true" if value else "false") + "\n")


def db_open():
    if not DB.is_file():
        raise FileNotFoundError("Banco oficial data/xauusd.db não encontrado.")
    con = sqlite3.connect("file:" + str(DB.resolve()) + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    tables = {
        row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    missing = TABLES_REQUIRED - tables
    if missing:
        con.close()
        raise RuntimeError(
            "Banco oficial fora do esquema esperado; faltam tabelas: "
            + ", ".join(sorted(missing))
        )
    return con


def buscar_operacao(con, codigo):
    row = con.execute(
        """SELECT codigo, data_hora, direcao, entrada, stop_loss, take_profit,
                  classificacao, justificativa, modelo, pontuacao, resposta_json
           FROM sinais_validados WHERE codigo = ? LIMIT 2""",
        (codigo,),
    ).fetchall()
    if len(row) != 1:
        raise RuntimeError(
            "Código não encontrado de forma única em sinais_validados: " + codigo
        )
    signal = dict(row[0])
    ranking_rows = con.execute(
        """SELECT codigo, classificacao_gemini, direcao, entrada, stop_loss,
                  take_profit, resultado_usdt, status_resultado
           FROM ranking WHERE codigo = ? LIMIT 2""",
        (codigo,),
    ).fetchall()
    if len(ranking_rows) != 1:
        raise RuntimeError("Registro correspondente não encontrado de forma única em ranking.")
    ranking = dict(ranking_rows[0])
    if str(ranking.get("status_resultado") or "").upper() != "PENDENTE":
        raise RuntimeError("A operação não está pendente no ranking; não será ativada.")
    if ranking.get("resultado_usdt") is not None:
        raise RuntimeError("A operação já possui resultado registrado; não será reativada.")

    direction = str(signal.get("direcao") or "").upper()
    if direction not in ("LONG", "SHORT"):
        raise RuntimeError("A operação não possui direção LONG/SHORT válida.")
    entry = number(signal.get("entrada"))
    stop = number(signal.get("stop_loss"))
    target = number(signal.get("take_profit"))
    if None in (entry, stop, target) or min(entry, stop, target) <= 0:
        raise RuntimeError("Entrada, stop ou alvo original inválido no banco oficial.")
    if direction == "LONG" and not stop < entry < target:
        raise RuntimeError("Preços originais inconsistentes para operação LONG.")
    if direction == "SHORT" and not target < entry < stop:
        raise RuntimeError("Preços originais inconsistentes para operação SHORT.")
    return signal, ranking


def send_telegram(text, reply_to=None):
    from telegram import enviar_mensagem
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("GESTOR_CHAT_ID", "").strip()
    if not chat_id:
        raise RuntimeError("chat_id do Telegram não recebido.")
    enviar_mensagem(chat_id, text, token, reply_to_message_id=reply_to)


def handle_command():
    text = os.getenv("GESTOR_TEXTO", "").strip()
    chat_id = os.getenv("GESTOR_CHAT_ID", "").strip()
    message_id = os.getenv("GESTOR_MESSAGE_ID", "").strip() or None
    state = load_state()

    if re.fullmatch(r"STOP", text, flags=re.IGNORECASE):
        if state.get("conducao_ativa"):
            state["conducao_ativa"] = False
            state["ultima_acao_manual"] = "STOP"
            state["atualizado_utc"] = stamp()
            save_state(state)
            reply = (
                "⏸️ CONDUÇÃO INTERROMPIDA POR VOCÊ.\n"
                "A operação foi preservada no estado atual. Nenhuma ordem foi executada."
            )
        else:
            reply = (
                "A condução já está parada. A operação registrada foi preservada. "
                "Nenhuma ordem foi executada."
            )
        if chat_id:
            try:
                send_telegram(reply, message_id)
            except Exception as error:
                print("Aviso: não foi possível confirmar STOP pelo Telegram:", error)
        output_active(False)
        print("Comando STOP processado.")
        return

    match = re.fullmatch(
        r"ENTREI\s+(?:"
        r"(?P<preco1>[0-9]+(?:[.,][0-9]+)?)\s+(?P<codigo1>OP-[0-9]{6,})"
        r"|(?P<codigo2>OP-[0-9]{6,})\s+(?P<preco2>[0-9]+(?:[.,][0-9]+)?)"
        r")",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        raise RuntimeError(
            "Comando inválido. Use: ENTREI 3591.22 OP-000266 "
            "ou ENTREI OP-000266 3591.22 ou STOP."
        )

    price = number(match.group("preco1") or match.group("preco2"))
    code = (match.group("codigo1") or match.group("codigo2")).upper()
    if price is None or price <= 0:
        raise RuntimeError("Preço de entrada informado é inválido.")

    if state.get("conducao_ativa"):
        active = (state.get("operacao") or {}).get("codigo")
        raise RuntimeError(
            "Já existe condução ativa para " + str(active)
            + ". Envie STOP antes de iniciar outra operação."
        )

    con = db_open()
    try:
        signal, ranking = buscar_operacao(con, code)
    finally:
        con.close()

    direction = str(signal["direcao"]).upper()
    stop = number(signal["stop_loss"])
    target = number(signal["take_profit"])
    if direction == "LONG" and not stop < price < target:
        raise RuntimeError(
            "Preço informado não está entre o stop e o alvo da operação LONG."
        )
    if direction == "SHORT" and not target < price < stop:
        raise RuntimeError(
            "Preço informado não está entre o alvo e o stop da operação SHORT."
        )

    operation = {
        "codigo": code,
        "direcao": direction,
        "preco_entrada": price,
        "preco_sinal": number(signal["entrada"]),
        "stop_atual": stop,
        "alvo": target,
        "status": "ABERTA",
        "confirmacao_usuario": True,
        "confirmada_utc": stamp(),
        "data_sinal": signal.get("data_hora"),
        "classificacao": signal.get("classificacao"),
        "pontuacao": number(signal.get("pontuacao")),
        "justificativa_sinal": signal.get("justificativa"),
        "modelo_sinal": signal.get("modelo"),
        "status_resultado": ranking.get("status_resultado"),
    }

    state.update({
        "versao": 1,
        "conducao_ativa": True,
        "operacao": operation,
        "chat_id": chat_id,
        "message_id": message_id,
        "ultima_decisao": None,
        "ultima_acao_manual": "ENTREI",
        "atualizado_utc": stamp(),
    })
    save_state(state)

    reply = (
        "✅ CONDUÇÃO OPERACIONAL ATIVADA\n"
        f"Operação: {code} · {direction}\n"
        f"Sua entrada: {price:.2f}\n"
        f"Stop atual: {stop:.2f} · Alvo: {target:.2f}\n"
        "A primeira avaliação será iniciada agora e depois repetida a cada 5 minutos.\n"
        "Acompanhamento consultivo; nenhuma ordem será executada automaticamente."
    )
    output_active(True)
    if chat_id:
        try:
            send_telegram(reply, message_id)
        except Exception as error:
            print("Aviso: condução foi salva, mas a confirmação Telegram falhou:", error)
    print("Condução ativada para " + code)


def resumo_tendencia(indicador):
    if not indicador:
        return {"tendencia": "SEM DADOS", "forca": "INDETERMINADA"}
    close = number(indicador.get("preco"))
    ema9 = number(indicador.get("ema9"))
    ema21 = number(indicador.get("ema21"))
    rsi = number(indicador.get("rsi"))
    macd = number(indicador.get("macd"))
    if None in (close, ema9, ema21):
        trend = "INDETERMINADA"
        strength = "INDETERMINADA"
    elif close > ema9 > ema21:
        trend = "ALTA"
        strength = "FORTE" if rsi is not None and rsi >= 58 else "MODERADA"
    elif close < ema9 < ema21:
        trend = "BAIXA"
        strength = "FORTE" if rsi is not None and rsi <= 42 else "MODERADA"
    else:
        trend = "LATERAL/TRANSIÇÃO"
        strength = "FRACA"
    return {
        "tendencia": trend,
        "forca": strength,
        "preco": close,
        "ema9": ema9,
        "ema21": ema21,
        "rsi": rsi,
        "macd": macd,
        "atr": number(indicador.get("atr")),
    }


def analisar_pavio_candle(candle):
    """Calcula corpo e pavios sem alterar os valores OHLC originais."""
    abertura = number(candle.get("abertura"))
    maxima = number(candle.get("maxima"))
    minima = number(candle.get("minima"))
    fechamento = number(candle.get("fechamento"))
    valores = (abertura, maxima, minima, fechamento)
    if any(value is None for value in valores):
        return {"status": "SEM_DADOS", "motivo": "OHLC incompleto"}
    if (maxima < minima or maxima < max(abertura, fechamento)
            or minima > min(abertura, fechamento)):
        return {"status": "OHLC_INVALIDO", "motivo": "Valores OHLC inconsistentes"}
    amplitude = maxima - minima
    corpo = abs(fechamento - abertura)
    pavio_superior = maxima - max(abertura, fechamento)
    pavio_inferior = min(abertura, fechamento) - minima
    def percentual(valor):
        return round(valor / amplitude * 100, 2) if amplitude > 0 else None
    def razao(valor):
        return round(valor / corpo, 4) if corpo > 0 else None
    if amplitude == 0:
        dominante = "SEM_AMPLITUDE"
    elif pavio_superior > pavio_inferior:
        dominante = "SUPERIOR"
    elif pavio_inferior > pavio_superior:
        dominante = "INFERIOR"
    else:
        dominante = "EQUILIBRADO"
    return {
        "status": "OK",
        "corpo": round(corpo, 5),
        "pavio_superior": round(pavio_superior, 5),
        "pavio_inferior": round(pavio_inferior, 5),
        "amplitude_total": round(amplitude, 5),
        "corpo_pct_amplitude": percentual(corpo),
        "pavio_superior_pct_amplitude": percentual(pavio_superior),
        "pavio_inferior_pct_amplitude": percentual(pavio_inferior),
        "razao_pavio_superior_corpo": razao(pavio_superior),
        "razao_pavio_inferior_corpo": razao(pavio_inferior),
        "pavio_dominante": dominante,
        "direcao_corpo": ("ALTA" if fechamento > abertura
                          else "BAIXA" if fechamento < abertura else "NEUTRO"),
    }


def closed_market_context(con):
    current = now()
    m5_rows = [
        dict(row) for row in con.execute(
            """SELECT data_hora, abertura, maxima, minima, fechamento
               FROM m5 ORDER BY data_hora DESC LIMIT 36"""
        ).fetchall()
    ]
    closed = []
    for row in m5_rows:
        try:
            if parse_dt(row["data_hora"]) + timedelta(minutes=5) <= current:
                closed.append(row)
        except Exception:
            continue
    if not closed:
        raise RuntimeError("Não há candle M5 fechado disponível.")
    latest = closed[0]
    price = number(latest.get("fechamento"))
    if price is None or price <= 0:
        raise RuntimeError("Último fechamento M5 inválido.")

    m5_cronologico = list(reversed(closed[:12]))
    market = {
        "preco_atual": price,
        "ultima_vela_m5_fechada": latest,
        "m5": m5_cronologico,
        "analise_pavios_m5": [
            {
                "data_hora": candle.get("data_hora"),
                "ohlc": {
                    "abertura": candle.get("abertura"),
                    "maxima": candle.get("maxima"),
                    "minima": candle.get("minima"),
                    "fechamento": candle.get("fechamento"),
                },
                **analisar_pavio_candle(candle),
            }
            for candle in m5_cronologico
        ],
    }
    for table, limit in (("m15", 6), ("h1", 6)):
        market[table] = [
            dict(row) for row in con.execute(
                f"""SELECT data_hora, abertura, maxima, minima, fechamento
                    FROM {table} ORDER BY data_hora DESC LIMIT {limit}"""
            ).fetchall()
        ]

    market["indicadores"] = {}
    latest_indicators = {}
    for timeframe in ("M5", "M15", "H1"):
        rows = [
            dict(row) for row in con.execute(
                """SELECT timeframe, data, hora, preco, ema9, ema21, rsi, macd, atr
                   FROM indicadores WHERE timeframe = ?
                   ORDER BY data DESC, hora DESC LIMIT 3""",
                (timeframe,),
            ).fetchall()
        ]
        market["indicadores"][timeframe] = rows
        latest_indicators[timeframe] = rows[0] if rows else None

    # O resumo é calculado apenas com campos existentes no esquema oficial.
    resumo_tf = {
        tf: resumo_tendencia(row)
        for tf, row in latest_indicators.items()
    }
    valid_trends = [
        item["tendencia"] for item in resumo_tf.values()
        if item["tendencia"] in ("ALTA", "BAIXA")
    ]
    if len(valid_trends) == 3 and len(set(valid_trends)) == 1:
        direcao = "COMPRADORA" if valid_trends[0] == "ALTA" else "VENDEDORA"
        alinhamento = "CONCORDÂNCIA M5/M15/H1"
    elif valid_trends.count("ALTA") > valid_trends.count("BAIXA"):
        direcao, alinhamento = "COMPRADORA", "DIVERGÊNCIA ENTRE TIMEFRAMES"
    elif valid_trends.count("BAIXA") > valid_trends.count("ALTA"):
        direcao, alinhamento = "VENDEDORA", "DIVERGÊNCIA ENTRE TIMEFRAMES"
    else:
        direcao, alinhamento = "SEM PREDOMÍNIO", "CENÁRIO MISTO OU INDEFINIDO"

    rsi = number((latest_indicators.get("M5") or {}).get("rsi"))
    atr = number((latest_indicators.get("M5") or {}).get("atr"))
    atr_pct = (atr / price * 100) if atr is not None and price else None
    if atr_pct is None:
        volatilidade = "SEM DADOS"
    elif atr_pct < 0.08:
        volatilidade = "BAIXA"
    elif atr_pct < 0.20:
        volatilidade = "NORMAL"
    else:
        volatilidade = "ALTA"
    if rsi is None:
        momentum = "SEM DADOS"
    elif rsi >= 65:
        momentum = "COMPRA FORTE/ESTICADA"
    elif rsi >= 55:
        momentum = "MOMENTUM COMPRADOR"
    elif rsi <= 35:
        momentum = "VENDA FORTE/ESTICADA"
    elif rsi <= 45:
        momentum = "MOMENTUM VENDEDOR"
    else:
        momentum = "NEUTRO"

    market["resumo_mercado"] = {
        "referencia": "Resumo matemático auxiliar; não é ordem nem veto ao Gemini.",
        "gerado_utc": stamp(),
        "tendencia_por_timeframe": resumo_tf,
        "direcao_predominante": direcao,
        "alinhamento": alinhamento,
        "volatilidade_m5": volatilidade,
        "momentum_m5": momentum,
        "estrutura_recente": (
            "Máximas/mínimas recentes disponíveis nos candles M5; "
            "Gemini deve conferir a sequência antes de concluir."
        ),
    }

    # Tabuleiro: manter poucos registros e retirar sequências extensas.
    board = [
        dict(row) for row in con.execute(
            """SELECT data_hora, timeframe, tipo_registro, sequencia_json,
                      quantidade_candles, ocorrencias_encontradas,
                      ocorrencias_exatas, distancia_maxima, horizonte,
                      evento_futuro, ocorrencias, total_amostras,
                      probabilidade, distancia_media
               FROM tabuleiro ORDER BY data_hora DESC LIMIT 10"""
        ).fetchall()
    ]
    for row in board:
        raw = row.get("sequencia_json")
        if raw:
            try:
                sequence = json.loads(raw)
                if isinstance(sequence, list):
                    row["sequencia_json"] = sequence[-8:]
                elif isinstance(sequence, dict):
                    row["sequencia_json"] = {
                        key: value for key, value in list(sequence.items())[-8:]
                    }
            except Exception:
                row["sequencia_json"] = None
    market["tabuleiro"] = board

    # Regiões próximas ao preço atual e transições com maior probabilidade.
    regions = [
        dict(row) for row in con.execute(
            """SELECT regiao, limite_inferior, limite_superior, visitas,
                      rejeicoes, rompimentos, prob_toque, prob_rejeicao,
                      prob_rompimento, classificacao, amostras
               FROM estatisticas_regioes
               WHERE timeframe='M5'
               ORDER BY ABS((limite_inferior + limite_superior) / 2.0 - ?)
               LIMIT 8""",
            (price,),
        ).fetchall()
    ]
    transitions = [
        dict(row) for row in con.execute(
            """SELECT origem, destino, transicoes, probabilidade,
                      visitas_origem, amostra_suficiente
               FROM estatisticas_transicoes
               WHERE timeframe='M5'
               ORDER BY probabilidade DESC, transicoes DESC LIMIT 8"""
        ).fetchall()
    ]
    market["estatisticas"] = {
        "regioes_m5_proximas": regions,
        "transicoes_m5_relevantes": transitions,
    }

    # Últimos sinais/ operações gerados, aprovados e negados, com resultado
    # associado quando existir. A consulta é somente leitura.
    market["ultimas_operacoes_produzidas"] = [
        dict(row) for row in con.execute(
            """SELECT codigo, data_hora, direcao, entrada, stop_loss,
                      take_profit, classificacao, pontuacao, justificativa,
                      status_resultado, resultado_usdt
               FROM (
                   SELECT s.codigo, s.data_hora, s.direcao, s.entrada,
                          s.stop_loss, s.take_profit, s.classificacao,
                          s.pontuacao, s.justificativa,
                          r.status_resultado, r.resultado_usdt
                   FROM sinais_validados s
                   LEFT JOIN ranking r ON r.codigo = s.codigo
                   UNION ALL
                   SELECT n.codigo, n.data_hora, n.direcao,
                          NULL AS entrada, NULL AS stop_loss,
                          NULL AS take_profit, n.classificacao,
                          n.pontuacao, n.justificativa,
                          'NEGADO' AS status_resultado,
                          NULL AS resultado_usdt
                   FROM sinais_negados n
               )
               ORDER BY data_hora DESC LIMIT 10"""
        ).fetchall()
    ]
    return market

def prepare_context():
    state = load_state()
    if not state.get("conducao_ativa") or not state.get("operacao"):
        output_active(False)
        print("Sem operação ativa; ciclo encerrado sem chamar Gemini.")
        return

    operation = dict(state["operacao"])
    if operation.get("status") != "ABERTA":
        state["conducao_ativa"] = False
        state["atualizado_utc"] = stamp()
        save_state(state)
        output_active(False)
        print("Operação não está aberta; condução interrompida.")
        return

    con = db_open()
    try:
        signal, ranking = buscar_operacao(con, operation["codigo"])
        market = closed_market_context(con)
    finally:
        con.close()

    # A entrada é a confirmação manual do usuário; stop pode ter sido
    # atualizado por uma decisão anterior do Gemini e fica no estado.
    operation["stop_atual"] = number(operation.get("stop_atual"))
    operation["alvo"] = number(operation.get("alvo"))
    operation["status_resultado"] = ranking.get("status_resultado")
    context = {
        "origem": "CENTRAL_XAUUSD_BANCO_OFICIAL",
        "somente_leitura": True,
        "gerado_utc": stamp(),
        "operacao": operation,
        "mercado": market,
    }
    raw = json.dumps(context, ensure_ascii=False, default=str)
    if len(raw) > 29000:
        raise RuntimeError(
            "Contexto de condução excedeu 29.000 caracteres; "
            "não será enviado parcialmente ao Gemini."
        )
    CONTEXT.write_text(
        json.dumps(context, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    output_active(True)
    print(
        "Contexto preparado para " + operation["codigo"]
        + " | preço M5 fechado: " + str(market["preco_atual"])
    )


def apply_decision():
    state = load_state()
    if not state.get("conducao_ativa") or not state.get("operacao"):
        raise RuntimeError("Não há condução ativa para aplicar a decisão.")

    if not RESULT.is_file():
        raise FileNotFoundError("Resultado do Gemini de condução não encontrado.")
    decision = json.loads(RESULT.read_text(encoding="utf-8"))
    action = decision.get("acao")
    operation = state["operacao"]
    if decision.get("codigo_operacao") != operation.get("codigo"):
        raise RuntimeError("Código da decisão não corresponde à operação ativa.")

    if action == "AJUSTAR_STOP":
        if decision.get("status_conducao") != "ATIVA":
            raise RuntimeError("Status de condução incompatível com AJUSTAR_STOP.")
        operation["stop_atual"] = number(decision.get("novo_stop"))
        if operation["stop_atual"] is None:
            raise RuntimeError("Gemini retornou novo stop inválido.")
    elif action == "ENCERRAR":
        if decision.get("status_conducao") != "FINALIZADA":
            raise RuntimeError("Status de condução incompatível com ENCERRAR.")
        operation["status"] = "FINALIZADA"
        state["conducao_ativa"] = False
    elif action == "MANTER":
        if decision.get("status_conducao") != "ATIVA":
            raise RuntimeError("Status de condução incompatível com MANTER.")
    else:
        raise RuntimeError("Ação desconhecida no resultado do Gemini.")

    state["ultima_decisao"] = decision
    state["atualizado_utc"] = stamp()
    save_state(state)
    print(
        "Decisão aplicada ao estado de condução: " + action
        + " | acompanhamento ativo: "
        + str(state["conducao_ativa"])
    )


def notify_decision():
    state = load_state()
    decision = state.get("ultima_decisao") or {}
    if not decision:
        raise RuntimeError("Não há decisão para notificar.")
    text = decision.get("mensagem_telegram") or decision.get("justificativa")
    if not text:
        raise RuntimeError("Mensagem da decisão vazia.")
    chat_id = str(state.get("chat_id") or "").strip()
    if not chat_id:
        raise RuntimeError("Chat do Gestor não está salvo no estado da condução.")
    # O workflow executa este arquivo diretamente; nesse modo, a pasta
    # gestor já está no sys.path e o módulo deve ser importado pelo nome local.
    from telegram import enviar_mensagem
    enviar_mensagem(
        chat_id,
        text,
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
    )
    print("Decisão da condução enviada ao Telegram.")


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "Uso: python gestor/controle_conducao.py "
            "{comando|preparar|aplicar|notificar}"
        )
    action = sys.argv[1]
    try:
        if action == "comando":
            handle_command()
        elif action == "preparar":
            prepare_context()
        elif action == "aplicar":
            apply_decision()
        elif action == "notificar":
            notify_decision()
        else:
            raise SystemExit("Ação desconhecida: " + action)
    except Exception as error:
        if action == "comando":
            chat_id = os.getenv("GESTOR_CHAT_ID", "").strip()
            message_id = os.getenv("GESTOR_MESSAGE_ID", "").strip() or None
            if chat_id:
                try:
                    send_telegram(
                        "❌ Não foi possível processar o comando de condução: "
                        + str(error)[:700]
                        + "\\nNenhuma ordem foi executada.",
                        message_id,
                    )
                except Exception as notify_error:
                    print("Falha ao enviar retorno do comando:", notify_error)
        raise


if __name__ == "__main__":
    main()
