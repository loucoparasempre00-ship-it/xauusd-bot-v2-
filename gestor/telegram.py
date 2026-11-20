#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Envio de respostas do Gestor diretamente pelo GitHub Actions ao Telegram."""

import json
import os
import urllib.parse
import urllib.request


def dividir_mensagem(texto, limite=4000):
    partes = []
    restante = str(texto or "").strip()
    while len(restante) > limite:
        corte = restante.rfind("\n", 0, limite)
        if corte < limite // 2:
            corte = restante.rfind(" ", 0, limite)
        if corte < limite // 2:
            corte = limite
        partes.append(restante[:corte])
        restante = restante[corte:].lstrip()
    if restante:
        partes.append(restante)
    return partes


def enviar_mensagem(chat_id, texto, token, reply_to_message_id=None):
    if not token:
        raise RuntimeError("Secret TELEGRAM_BOT_TOKEN não configurado.")
    if not chat_id:
        raise RuntimeError("chat_id não recebido pelo workflow.")
    if not texto or not texto.strip():
        raise RuntimeError("Texto de resposta vazio.")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for indice, parte in enumerate(dividir_mensagem(texto)):
        dados = {"chat_id": str(chat_id), "text": parte, "disable_web_page_preview": "true"}
        if indice == 0 and reply_to_message_id:
            dados["reply_to_message_id"] = str(reply_to_message_id)
        corpo = urllib.parse.urlencode(dados).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=corpo,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "GestorXAU-GitHubActions/1.0"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            resultado = json.loads(response.read().decode("utf-8"))
        if not resultado.get("ok"):
            raise RuntimeError("Telegram recusou o envio da mensagem.")
    print("Resposta enviada diretamente pelo GitHub Actions ao Telegram.")


def main():
    enviar_mensagem(
        os.environ.get("GESTOR_CHAT_ID"),
        os.environ.get("GESTOR_REPLY_TEXT", ""),
        os.environ.get("TELEGRAM_BOT_TOKEN"),
        os.environ.get("GESTOR_MESSAGE_ID") or None
    )


if __name__ == "__main__":
    main()
