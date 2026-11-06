
import csv
import json
import os
import sys
import traceback

from pathlib import Path
from datetime import datetime, timezone, timedelta

import acompanhamento_autonomo_xau as acompanhamento

import preparar_contexto_acompanhador
import agente_operacional_xau

from diretor_operacional_xau import calcular_decisao
from leitor_central_autonomo_xau import analisar_preco


PASTA = Path("data")

ARQUIVO_CANDLES = PASTA / "xauusd_m5_fechados.csv"
ARQUIVO_CONTEXTO = (
    PASTA / "contexto_inteligente_acompanhador.json"
)
ARQUIVO_PACOTE = (
    PASTA / "pacote_agente_operacional.json"
)
ARQUIVO_DECISAO = (
    PASTA / "decisao_agente_operacional.json"
)


def salvar_candles(velas):
    PASTA.mkdir(exist_ok=True)

    with ARQUIVO_CANDLES.open(
        "w",
        encoding="utf-8",
        newline=""
    ) as arquivo:
        campos = [
            "timestamp_utc",
            "open",
            "high",
            "low",
            "close"
        ]

        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos
        )

        escritor.writeheader()

        for vela in velas:
            escritor.writerow({
                "timestamp_utc": (
                    vela["horario"].isoformat()
                ),
                "open": vela["open"],
                "high": vela["high"],
                "low": vela["low"],
                "close": vela["close"]
            })


def validar_atualidade(velas):
    if not velas:
        raise RuntimeError(
            "Histórico de candles vazio."
        )

    ultima = velas[-1]["horario"]

    agora = datetime.now(timezone.utc)

    idade = (
        agora - (ultima + timedelta(minutes=5))
    ).total_seconds() / 60

    if idade < 0:
        raise RuntimeError(
            "O último candle ainda não deveria estar fechado."
        )

    if idade > 15:
        raise RuntimeError(
            f"Banco de candles desatualizado: {idade:.1f} minutos."
        )

    return {
        "ultima_vela": ultima.isoformat(),
        "idade_minutos": round(idade, 1),
        "atual": True
    }


def carregar_contexto():
    if not ARQUIVO_CONTEXTO.exists():
        raise RuntimeError(
            "Contexto inteligente não foi produzido."
        )

    contexto = json.loads(
        ARQUIVO_CONTEXTO.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(contexto, dict):
        raise RuntimeError(
            "Contexto inteligente inválido."
        )

    if not contexto.get("grafico_m5", {}).get("serie"):
        raise RuntimeError(
            "Gráfico M5 sem candles."
        )

    return contexto


def montar_decisao_diretor(
    operacao,
    velas,
    contexto
):
    fechamentos = [
        vela["close"] for vela in velas
    ]

    ema9 = acompanhamento.ema(
        fechamentos,
        9
    )

    ema21 = acompanhamento.ema(
        fechamentos,
        21
    )

    valor_rsi = acompanhamento.rsi(
        fechamentos,
        14
    )

    atr = acompanhamento.calcular_atr(
        velas,
        14
    )

    if atr is None:
        raise RuntimeError(
            "ATR indisponível."
        )

    ultima = velas[-1]

    central = acompanhamento.obter_contexto_central(
        operacao
    )

    leitura = analisar_preco(
        ultima["close"],
        central
    )

    decisao = calcular_decisao(
        operacao=operacao,
        vela={
            "close": ultima["close"],
            "high": ultima["high"],
            "low": ultima["low"]
        },
        ema9=ema9,
        ema21=ema21,
        valor_rsi=valor_rsi,
        atr=atr,
        leitura_central=leitura
    )

    return {
        "decisao": decisao,
        "indicadores": {
            "ema9": ema9,
            "ema21": ema21,
            "rsi14": valor_rsi,
            "atr14": atr
        },
        "leitura_central": leitura
    }


def montar_pacote(
    operacao,
    contexto,
    decisao_diretor,
    validacao
):
    return {
        "gerado_utc": datetime.now(
            timezone.utc
        ).isoformat(),

        "operacao": operacao,

        "contexto": contexto,

        "decisao_diretor": decisao_diretor,

        "memoria": (
            operacao.get("memoria_autonoma", {})
        ),

        "validacao_candles": validacao,

        "regras": [
            "A análise é consultiva.",
            "Não executar ordens.",
            "Não modificar entrada, stop ou alvo.",
            "Não ignorar dados desatualizados.",
            "Toda alteração de stop exige confirmação manual."
        ]
    }


def main():
    print("Integração do agente operacional iniciada.")

    acompanhamento.carregar()

    operacao = acompanhamento.estado.get(
        "operacao"
    )

    if not operacao:
        print(
            "Nenhuma operação ativa. "
            "Agente operacional não será consultado."
        )
        return

    if operacao.get("status") != "ATIVA":
        print("Operação não está ativa.")
        return

    velas = acompanhamento.buscar_velas()

    validacao = validar_atualidade(velas)

    horario = velas[-1]["horario"].isoformat()

    if operacao.get("ultima_analise_ia") == horario:
        print(
            "Este candle já foi analisado pelo agente Gemini."
        )
        return

    salvar_candles(velas)

    print("Construindo contexto completo do mercado.")

    preparar_contexto_acompanhador.main()

    contexto = carregar_contexto()

    if contexto.get("candle_utc") != horario:
        raise RuntimeError(
            "O candle do contexto não corresponde "
            "ao último candle validado."
        )

    decisao_diretor = montar_decisao_diretor(
        operacao,
        velas,
        contexto
    )

    pacote = montar_pacote(
        operacao,
        contexto,
        decisao_diretor,
        validacao
    )

    ARQUIVO_PACOTE.write_text(
        json.dumps(
            pacote,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print("Pacote completo preparado.")
    print("Consultando agente Gemini operacional.")

    agente_operacional_xau.main()

    if not ARQUIVO_DECISAO.exists():
        raise RuntimeError(
            "O agente não produziu arquivo de decisão."
        )

    resultado = json.loads(
        ARQUIVO_DECISAO.read_text(
            encoding="utf-8"
        )
    )

    if resultado.get("status") != "SUCESSO":
        raise RuntimeError(
            "Gemini indisponível: "
            + str(resultado.get("motivo"))
        )

    decisao = resultado.get("decisao") or {}

    operacao["ultima_analise_ia"] = horario

    acompanhamento.salvar()

    print("Análise Gemini concluída.")
    print("Candle:", horario)
    print("Preço:", velas[-1]["close"])
    print("Ação sugerida:", decisao.get("acao"))
    print("Confiança:", decisao.get("confianca"))

    mensagem = (
        "🧠 ANÁLISE DO AGENTE OPERACIONAL XAUUSD\n\n"
        f"Operação: {operacao['direcao']}\n"
        f"Candle: {horario}\n"
        f"Preço: {velas[-1]['close']:.3f}\n\n"
        f"Recomendação da IA: {decisao.get('acao')}\n"
        f"Confiança da análise: {decisao.get('confianca')}/100\n\n"
        f"Justificativa:\n{decisao.get('justificativa', '')}\n\n"
        f"Risco detectado:\n{decisao.get('risco_detectado', '')}\n\n"
        "Evidências:\n"
    )

    for evidencia in decisao.get("evidencias", [])[:5]:
        mensagem += f"• {evidencia}\n"

    if decisao.get("divergencia_diretor"):
        mensagem += (
            "\nATENÇÃO: A IA divergiu do diretor operacional.\n"
            + str(decisao.get("motivo_divergencia", ""))
            + "\n"
        )

    if decisao.get("preco_stop_recomendado") is not None:
        mensagem += (
            "\nStop sugerido pela IA: "
            f"{decisao['preco_stop_recomendado']:.3f}\n"
            "Não altere na corretora sem avaliação e confirmação."
        )

    mensagem += (
        "\n\nAnálise consultiva. "
        "Nenhuma ordem foi executada."
    )

    acompanhamento.enviar(mensagem)

    print("Resultado enviado ao Telegram.")


if __name__ == "__main__":
    try:
        main()

    except Exception as erro:
        print("ERRO NA INTEGRAÇÃO OPERACIONAL:", erro)
        traceback.print_exc()
        sys.exit(1)
