
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
INTERACAO TELEGRAM XAUUSD

Envia as decisoes produzidas pelo Decisor.

A Recepcao e responsavel por ler os comandos.
Este modulo nao consulta novamente o Telegram.

Nao executa ordens.
Nao altera bancos oficiais.
"""

import json
import os
import urllib.parse
import urllib.request

from pathlib import Path
from datetime import datetime, timezone


BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

ARQUIVO_DECISOES = DATA / "decisoes_operacoes_xau.json"
ARQUIVO_RECEPCAO = DATA / "entrada_recepcao_operacoes.json"
ARQUIVO_ESTADO = DATA / "interacao_telegram_estado.json"
ARQUIVO_SAIDA = DATA / "resultado_interacao_telegram.json"


def carregar_json(caminho, padrao=None):
    if padrao is None:
        padrao = {}

    if not caminho.exists():
        return padrao

    try:
        with caminho.open("r", encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except (OSError, json.JSONDecodeError):
        return padrao


def salvar_json(caminho, dados):
    caminho.parent.mkdir(parents=True, exist_ok=True)

    temporario = caminho.with_suffix(".tmp")

    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(
            dados,
            arquivo,
            ensure_ascii=False,
            indent=2
        )

    temporario.replace(caminho)


def enviar_mensagem(token, chat_id, texto):
    url = (
        f"https://api.telegram.org/bot{token}/sendMessage"
    )

    dados = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": texto
    }).encode("utf-8")

    requisicao = urllib.request.Request(
        url,
        data=dados,
        method="POST"
    )

    with urllib.request.urlopen(
        requisicao,
        timeout=25
    ) as resposta:

        resultado = json.loads(
            resposta.read().decode("utf-8")
        )

    if not resultado.get("ok"):
        raise RuntimeError(
            "Telegram nao confirmou o envio."
        )

    return resultado


def formatar_decisao(decisao, probabilidades):
    codigo = decisao.get(
        "codigo_operacao",
        "SEM_CODIGO"
    )

    acao = decisao.get(
        "decisao",
        "SEM_DECISAO"
    )

    linhas = [
        "XAUUSD | DECISAO DO ACOMPANHAMENTO",
        "",
        f"Codigo da operacao: {codigo}",
        f"Decisao: {acao}",
        f"Direcao da operacao: {decisao.get('direcao_operacao', 'N/D')}",
        "",
        (
            "Probabilidade geral de subir: "
            f"{probabilidades.get('subir', 'N/D')}%"
        ),
        (
            "Probabilidade geral de cair: "
            f"{probabilidades.get('cair', 'N/D')}%"
        ),
        "",
        f"Preco de referencia: {decisao.get('preco_referencia', 'N/D')}",
        f"Stop recomendado: {decisao.get('stop_recomendado', 'N/D')}",
        f"Resultado: {decisao.get('resultado_r', 'N/D')}R",
        "",
        f"Motivo: {decisao.get('motivo', '')}"
    ]

    if decisao.get("confirmacao_manual"):
        linhas.extend([
            "",
            "A decisao exige confirmacao manual.",
            "O sistema nao executa ordens."
        ])

        if acao in ("MUDA_STOP", "REDUZ_RISCO"):
            linhas.append(
                f"Para confirmar: STOP OK {codigo}"
            )

        elif acao == "FECHA":
            linhas.append(
                f"Depois de fechar manualmente, envie CANCELA {codigo}"
            )

    return "\n".join(linhas)


def formatar_evento(evento):
    comando = evento.get("comando")
    codigo = evento.get(
        "parametros", {}
    ).get("codigo_operacao")

    mensagens = {
        "ENTREI": (
            f"Recebido: entrada informada para {codigo}. "
            "A Recepcao registrou o comando."
        ),
        "CANCELA": (
            f"Recebido: pedido de encerramento para {codigo}. "
            "A confirmacao sera processada pelo Coordenador."
        ),
        "STOP_OK": (
            f"Recebido: confirmacao manual do stop para {codigo}. "
            "A confirmacao sera processada pelo Coordenador."
        ),
        "STOP_NAO": (
            f"Recebido: stop recusado para {codigo}. "
            "O stop anterior deve ser preservado."
        ),
        "STATUS": (
            "Comando STATUS recebido. Consulte as operacoes "
            "ativas no resultado da Recepcao."
        ),
        "DESLIGA": (
            "Comando DESLIGA recebido. O estado do acompanhamento "
            "deve ser tratado pelo Coordenador."
        )
    }

    return mensagens.get(comando)


def executar():
    print("=== INTERACAO TELEGRAM XAUUSD ===")

    token = os.getenv(
        "TELEGRAM_ACOMPANHAMENTO_BOT_TOKEN"
    )

    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "Credenciais do Telegram ausentes."
        )

    decisoes = carregar_json(
        ARQUIVO_DECISOES,
        {}
    )

    recepcao = carregar_json(
        ARQUIVO_RECEPCAO,
        {}
    )

    estado = carregar_json(
        ARQUIVO_ESTADO,
        {"decisoes_enviadas": []}
    )

    ja_enviadas = set(
        estado.get("decisoes_enviadas", [])
    )

    probabilidades = decisoes.get(
        "probabilidade_geral",
        {}
    )

    enviadas = []
    erros = []

    # Envia somente decisoes ainda nao comunicadas.
    for decisao in decisoes.get("decisoes", []):

        codigo = decisao.get("codigo_operacao")

        if not codigo:
            continue

        candle = decisao.get("candle_utc", "")

        chave = f"{codigo}|{candle}|{decisao.get('decisao')}"

        if chave in ja_enviadas:
            continue

        mensagem = formatar_decisao(
            decisao,
            probabilidades
        )

        try:
            enviar_mensagem(
                token,
                chat_id,
                mensagem
            )

            ja_enviadas.add(chave)
            enviadas.append(chave)

        except Exception as erro:
            erros.append({
                "codigo_operacao": codigo,
                "erro": str(erro)
            })

    # Confirma recebimento dos comandos que vieram da Recepcao.
    for evento in recepcao.get("eventos_telegram", []):

        update_id = evento.get("update_id")

        if update_id is None:
            continue

        chave_evento = f"EVENTO|{update_id}"

        if chave_evento in ja_enviadas:
            continue

        mensagem = formatar_evento(evento)

        if not mensagem:
            continue

        try:
            enviar_mensagem(
                token,
                chat_id,
                mensagem
            )

            ja_enviadas.add(chave_evento)
            enviadas.append(chave_evento)

        except Exception as erro:
            erros.append({
                "update_id": update_id,
                "erro": str(erro)
            })

    estado["decisoes_enviadas"] = sorted(
        ja_enviadas
    )

    # Limita o tamanho do controle de duplicidade.
    estado["decisoes_enviadas"] = (
        estado["decisoes_enviadas"][-3000:]
    )

    salvar_json(
        ARQUIVO_ESTADO,
        estado
    )

    resultado = {
        "modulo": "interacao_telegram_xau",
        "executado_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "mensagens_enviadas": len(enviadas),
        "erros": erros,
        "execucao_ordens": False
    }

    salvar_json(
        ARQUIVO_SAIDA,
        resultado
    )

    print("Mensagens enviadas:", len(enviadas))
    print("Erros:", len(erros))
    print("INTERACAO CONCLUIDA")


if __name__ == "__main__":
    executar()
