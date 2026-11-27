#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Calcula um indicador auxiliar de saúde da operação e anexa à mensagem Telegram.

Não altera a decisão do Gemini, o banco oficial ou os parâmetros da operação.
Usa o preço M5 e os indicadores já organizados pelo redator matemático.
"""
import json
import math
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "gestor_operacao_contexto.json"
REPORT = ROOT / "data" / "conclusao_matematica.json"
STATE = ROOT / "data" / "gestor_conducao_estado.json"


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def load_json(path):
    if not path.is_file():
        raise FileNotFoundError("Arquivo necessário ausente: " + str(path.relative_to(ROOT)))
    return json.loads(path.read_text(encoding="utf-8"))


def clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def momentum_score(direction, indicators):
    scores = []
    ema = indicators.get("relacao_ema")
    if ema:
        aligned = (
            (direction == "LONG" and ema == "EMA9_ACIMA_EMA21")
            or (direction == "SHORT" and ema == "EMA9_ABAIXO_EMA21")
        )
        scores.append(100.0 if aligned else 0.0)

    rsi = number(indicators.get("rsi14"))
    if rsi is not None:
        if direction == "LONG":
            scores.append(100.0 if rsi >= 55 else 0.0 if rsi <= 45 else 50.0)
        else:
            scores.append(100.0 if rsi <= 45 else 0.0 if rsi >= 55 else 50.0)

    macd = number(indicators.get("macd"))
    if macd is not None:
        aligned = (direction == "LONG" and macd > 0) or (direction == "SHORT" and macd < 0)
        scores.append(100.0 if aligned else 0.0)

    return sum(scores) / len(scores) if scores else None


def calculate(operation, report):
    direction = str(operation.get("direcao", "")).upper()
    if direction not in ("LONG", "SHORT"):
        return None, "Direção da operação inválida."

    entry = number(operation.get("preco_entrada"))
    stop = number(operation.get("stop_atual"))
    target = number(operation.get("alvo"))
    market = report.get("trajetoria_dos_precos", {}).get("M5", {})
    price = number(market.get("ultimo_fechamento"))
    if None in (entry, stop, target, price):
        return None, "Dados de entrada, stop, alvo ou preço M5 indisponíveis."

    if direction == "LONG":
        initial_risk = entry - stop
        target_distance = target - entry
        remaining_risk = price - stop
        progress = (price - entry) / target_distance if target_distance > 0 else None
    else:
        initial_risk = stop - entry
        target_distance = entry - target
        remaining_risk = stop - price
        progress = (entry - price) / target_distance if target_distance > 0 else None

    if initial_risk <= 0 or target_distance <= 0 or progress is None:
        return None, "Relação entre entrada, stop e alvo inválida."

    # Segurança: parcela do risco inicial que ainda resta até o stop.
    safety = clamp(remaining_risk / initial_risk * 100.0)
    # Progresso: 50 na entrada, sobe no sentido do alvo e cai no sentido do stop.
    progress_score = clamp(50.0 + 50.0 * progress)

    indicators = report.get("medias_rsi_macd_atr", {}).get("M5", {})
    momentum = momentum_score(direction, indicators)
    if momentum is None:
        return None, "Sem indicadores M5 suficientes para calcular a saúde."

    health = round(0.60 * safety + 0.20 * progress_score + 0.20 * momentum)
    health = int(clamp(health))
    return {
        "pontuacao": health,
        "seguranca_stop": round(safety),
        "progresso_alvo": round(progress_score),
        "momentum_m5": round(momentum),
        "preco_m5": price,
        "referencia_m5": market.get("referencia"),
    }, None


def health_label(score):
    if score >= 80:
        return "SAUDÁVEL"
    if score >= 60:
        return "ATENÇÃO"
    if score >= 40:
        return "FRÁGIL"
    return "CRÍTICA"


def health_bar(score):
    filled = max(0, min(10, round(score / 10)))
    return "█" * filled + "░" * (10 - filled)


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="saude_operacao_", suffix=".json", dir=str(STATE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, STATE)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


def main():
    context = load_json(CONTEXT)
    report = load_json(REPORT)
    state = load_json(STATE)
    operation = context.get("operacao") or {}
    decision = state.get("ultima_decisao") or {}
    if not decision:
        raise RuntimeError("Não existe decisão aplicada para complementar.")

    active_operation = state.get("operacao") or {}
    if operation.get("codigo") != active_operation.get("codigo"):
        raise RuntimeError("O contexto e o estado não correspondem à mesma operação.")

    # Usa o stop atualizado pelo Gemini nesta rodada, sem alterar a decisão.
    result, reason = calculate(active_operation, report)
    if result is None:
        message = "🩺 Saúde operacional: SEM DADOS — " + reason
        print(message)
    else:
        score = result["pontuacao"]
        message = (
            "🩺 Saúde operacional: " + str(score) + "/100 — "
            + health_label(score) + "\n"
            + health_bar(score) + "\n"
            + "Stop: " + str(result["seguranca_stop"]) + "/100"
            + " | Progresso: " + str(result["progresso_alvo"]) + "/100"
            + " | Momentum M5: " + str(result["momentum_m5"]) + "/100"
            + "\nPreço de referência M5: " + str(result["preco_m5"])
            + " | " + str(result["referencia_m5"])
        )
        print(message)

    # Evita duplicar o bloco caso a etapa seja repetida no mesmo estado.
    base = decision.get("mensagem_telegram") or decision.get("justificativa") or ""
    marker = "\n\n🩺 Saúde operacional:"
    if marker in base:
        base = base.split(marker, 1)[0]
    decision["mensagem_telegram"] = base.rstrip() + "\n\n" + message
    state["ultima_decisao"] = decision
    save_state(state)
    print("Indicador auxiliar anexado à mensagem da condução; decisão do Gemini preservada.")


if __name__ == "__main__":
    main()
