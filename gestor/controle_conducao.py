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
        r"ENTREI\s+([0-9]+(?:[.,][0-9]+)?)\s+(OP-[0-9]{6,})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        raise RuntimeError(
            "Comando inválido. Use: ENTREI 3591.22 OP-000266 ou STOP."
        )

    price = number(match.group(1))
    code = match.group(2).upper()
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


def closed_market_context(con):
    current = now()
    m5_rows = [
        dict(row) for row in con.execute(
            "SELECT * FROM m5 ORDER BY data_hora DESC LIMIT 60"
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

    market = {
        "preco_atual": price,
        "ultima_vela_m5_fechada": latest,
        "m5": list(reversed(closed[:20])),
    }
    for table in ("m15", "h1"):
        market[table] = [
            dict(row) for row in con.execute(
                f"SELECT * FROM {table} ORDER BY data_hora DESC LIMIT 15"
            ).fetchall()
        ]

    market["indicadores"] = {}
    for timeframe in ("M5", "M15", "H1"):
        market["indicadores"][timeframe] = [
            dict(row) for row in con.execute(
                """SELECT * FROM indicadores WHERE timeframe = ?
                   ORDER BY data DESC, hora DESC LIMIT 12""",
                (timeframe,),
            ).fetchall()
        ]

    board = [
        dict(row) for row in con.execute(
            "SELECT * FROM tabuleiro ORDER BY data_hora DESC LIMIT 25"
        ).fetchall()
    ]
    for row in board:
        raw = row.get("sequencia_json")
        if raw:
            try:
                row["sequencia_json"] = json.loads(raw)
            except Exception:
                pass
    market["tabuleiro"] = board
    market["estatisticas"] = {
        "regioes_m5": [
            dict(row) for row in con.execute(
                """SELECT * FROM estatisticas_regioes
                   WHERE timeframe='M5' ORDER BY regiao LIMIT 40"""
            ).fetchall()
        ],
        "transicoes_m5": [
            dict(row) for row in con.execute(
                """SELECT * FROM estatisticas_transicoes
                   WHERE timeframe='M5' ORDER BY origem, destino LIMIT 40"""
            ).fetchall()
        ],
    }
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
    from gestor.telegram import enviar_mensagem
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


if __name__ == "__main__":
    main()
