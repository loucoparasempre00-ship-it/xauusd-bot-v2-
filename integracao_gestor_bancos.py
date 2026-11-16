
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import base64
import urllib.request
import urllib.error

from datetime import datetime, timezone

import fila_progressiva_gestor as fila


LIMITE_REQUISICAO = 500_000


def decodificar_valores(registro):
    resultado = {}

    for chave, valor in registro.items():
        if (
            isinstance(valor, dict)
            and valor.get("__tipo__") == "blob"
            and "base64" in valor
        ):
            bruto = base64.b64decode(valor["base64"])
            resultado[chave] = bruto.hex()
        else:
            resultado[chave] = valor

    return resultado


def serializar(pacote):
    return json.dumps(
        pacote,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")


def criar_pacote(lote, registros, ultimo_lote):
    identificador = (
        f"fila-{lote['banco']}-"
        f"{lote['tabela']}-"
        f"{lote['ids'][0]}"
    )

    return {
        "tipo": "espelho_bancos_leitura",
        "versao": 1,
        "ciclo_id": identificador,
        "somente_leitura": True,
        "dados": {
            "banco": lote["banco"],
            "tabela": lote["tabela"],
            "colunas": lote["colunas"],
            "total_registros_origem": lote["total_origem"],
            "lote": lote["ids"][0],
            "registros": registros,
            "ultimo_lote": ultimo_lote,
        },
    }


def enviar(url, token, pacote):
    corpo = serializar(pacote)

    if len(corpo) > LIMITE_REQUISICAO:
        raise RuntimeError(
            f"Lote excedeu o limite de {LIMITE_REQUISICAO} bytes."
        )

    requisicao = urllib.request.Request(
        url,
        data=corpo,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "CentralXAUUSD-BancoSync/2.0",
            "X-Central-Secret": token,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            requisicao,
            timeout=60,
        ) as resposta:

            retorno = resposta.read().decode(
                "utf-8",
                errors="replace",
            )

            if not 200 <= resposta.status < 300:
                raise RuntimeError(
                    f"HTTP {resposta.status}: {retorno}"
                )

            print(
                "GESTOR CONFIRMOU O BLOCO",
                "| HTTP:",
                resposta.status,
                "| Resposta:",
                retorno[:500],
                flush=True,
            )

            return resposta.status

    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode(
            "utf-8",
            errors="replace",
        )
        raise RuntimeError(
            f"Erro HTTP {erro.code}: {detalhe}"
        )

    except urllib.error.URLError as erro:
        raise RuntimeError(f"Falha de conexão: {erro}")


def montar_bloco(lote):
    registros = []
    ids = []

    for registro, identificador in zip(
        lote["registros"],
        lote["ids"],
    ):
        normalizado = decodificar_valores(registro)
        tentativa = registros + [normalizado]

        pacote_teste = criar_pacote(
            lote,
            tentativa,
            False,
        )

        if len(serializar(pacote_teste)) <= LIMITE_REQUISICAO:
            registros.append(normalizado)
            ids.append(identificador)
            continue

        if not registros:
            raise RuntimeError(
                "Um registro individual excede o limite HTTP. "
                "Ele foi mantido na fila, sem exclusão. "
                "É necessário suporte a fragmentação no Worker "
                "para transmitir esse registro."
            )

        break

    if not registros:
        return None

    ultimo_lote = (
        lote["ultimo_lote"]
        and len(registros) == len(lote["registros"])
    )

    pacote = criar_pacote(
        lote,
        registros,
        ultimo_lote,
    )

    return pacote, ids


def main():
    print("TRANSMISSÃO PROGRESSIVA CENTRAL -> GESTOR")
    print("Modo: somente leitura")
    print("Máximo: um bloco por execução")

    url = os.environ.get(
        "GESTOR_XAUUSD_URL",
        "",
    ).strip()

    token = os.environ.get(
        "GESTOR_XAUUSD_TOKEN",
        "",
    ).strip()

    if not url or not token:
        raise RuntimeError(
            "GESTOR_XAUUSD_URL ou GESTOR_XAUUSD_TOKEN ausente."
        )

    lote = fila.obter_lote()

    if not lote["quantidade"]:
        print("FILA VAZIA: nenhum bloco para transmitir.")
        return

    print(
        f"Bloco selecionado: {lote['banco']}."
        f"{lote['tabela']} | "
        f"{lote['quantidade']} registros",
        flush=True,
    )

    resultado = montar_bloco(lote)

    if resultado is None:
        print("Nenhum registro disponível.")
        return

    pacote, ids_confirmaveis = resultado

    # A fila só é consumida depois da confirmação HTTP 2xx.
    enviar(url, token, pacote)

    removidos = fila.confirmar_lote(ids_confirmaveis)

    if removidos != len(ids_confirmaveis):
        raise RuntimeError(
            "O Gestor confirmou, mas a quantidade removida da fila "
            f"foi diferente: {removidos}/{len(ids_confirmaveis)}. "
            "O bloco poderá ser reenviado na próxima execução."
        )

    print(
        f"BLOCO CONSUMIDO: {removidos} registros confirmados.",
        flush=True,
    )

    fila.resumo_fila()


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        print("ERRO NA TRANSMISSÃO:", erro, flush=True)
        sys.exit(1)
