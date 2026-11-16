
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
INTEGRACAO CENTRAL XAUUSD -> GESTOR XAUUSD

Responsabilidades:
- Ler o pacote preparado pela Central.
- Validar sinal e contexto de mercado.
- Transmitir os dados ao Worker do Gestor.
- Não coleta preços nem recalcula indicadores.
- Não executa ordens.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone


def carregar_pacote(caminho):
    if not os.path.isfile(caminho):
        raise RuntimeError(
            f"Arquivo de pacote não encontrado: {caminho}"
        )

    with open(caminho, "r", encoding="utf-8") as arquivo:
        pacote = json.load(arquivo)

    if not isinstance(pacote, dict):
        raise RuntimeError("Pacote inválido.")

    return pacote


def preparar_pacote(original):
    sinal = original.get("sinal")

    mercado = original.get(
        "dados_mercado",
        original.get("contexto_mercado")
    )

    if not isinstance(sinal, dict):
        raise RuntimeError("Dados do sinal ausentes.")

    aprovado = (
        sinal.get("aprovado") is True
        or sinal.get("status") == "APROVADO"
    )

    if not aprovado:
        raise RuntimeError("Sinal não aprovado. Envio cancelado.")

    if not isinstance(mercado, dict) or not mercado:
        raise RuntimeError(
            "Dados de mercado ausentes. Envio cancelado."
        )

    if not mercado.get("velas"):
        raise RuntimeError(
            "Mercado sem velas. Envio cancelado."
        )

    identificador = (
        original.get("identificador")
        or sinal.get("identificador")
        or sinal.get("codigo")
    )

    if not identificador:
        raise RuntimeError("Identificador do pacote ausente.")

    gerado_em = (
        original.get("gerado_em")
        or datetime.now(timezone.utc).isoformat()
    )

    return {
        "identificador": str(identificador),
        "gerado_em": gerado_em,
        "sinal": sinal,
        "contexto_mercado": mercado
    }


def transmitir(pacote):
    url = os.environ.get("GESTOR_XAUUSD_URL")

    if not url:
        raise RuntimeError(
            "Secret GESTOR_XAUUSD_URL não configurado."
        )

    token = os.environ.get("GESTOR_XAUUSD_TOKEN")

    dados = json.dumps(
        pacote,
        ensure_ascii=False
    ).encode("utf-8")

    cabecalhos = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    if token:
        cabecalhos["X-Central-Secret"] = token

    requisicao = urllib.request.Request(
        url,
        data=dados,
        headers=cabecalhos,
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            requisicao,
            timeout=30
        ) as resposta:
            corpo = resposta.read().decode("utf-8")

            print("STATUS:", resposta.status)
            print("RESPOSTA:", corpo)

            if not 200 <= resposta.status < 300:
                raise RuntimeError(
                    "Worker não confirmou o recebimento."
                )

    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode(
            "utf-8",
            errors="replace"
        )
        raise RuntimeError(
            f"Erro HTTP {erro.code}: {detalhe}"
        )

    except urllib.error.URLError as erro:
        raise RuntimeError(
            f"Falha de conexão com o Gestor: {erro}"
        )


def main():
    caminho = os.environ.get(
        "GESTOR_PACOTE_ARQUIVO",
        "data/pacote_gestor_xauusd.json"
    )

    try:
        original = carregar_pacote(caminho)
        pacote = preparar_pacote(original)

        print("Pacote validado.")
        print("Identificador:", pacote["identificador"])
        print("Transmitindo para o Gestor...")

        transmitir(pacote)

        print("TRANSMISSAO CONCLUIDA.")

    except Exception as erro:
        print("ERRO NA INTEGRACAO:", erro)
        sys.exit(1)


if __name__ == "__main__":
    main()
