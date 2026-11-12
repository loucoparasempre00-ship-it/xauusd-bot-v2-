
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
RECEPCAO DE OPERACOES XAUUSD

Entradas exclusivas:
1. Telegram: comandos manuais.
2. data/operacoes.db ou data/operacoes.db.gz:
   tabela operacoes, campo payload_json.

Nao consulta o Gerador de Sinais.
Nao consulta resultados_operacoes.json.
Nao consulta resultados_operacoes.db.
Nao grava diretamente nos bancos oficiais.
Nao executa ordens.
"""

import gzip
import json
import os
import sqlite3
import tempfile
import urllib.parse
import urllib.request

from pathlib import Path
from datetime import datetime, timezone


BASE_DIR = Path(__file__).resolve().parent / "data"

BANCO_NORMAL = BASE_DIR / "operacoes.db"
BANCO_COMPACTADO = BASE_DIR / "operacoes.db.gz"

ARQUIVO_ESTADO = BASE_DIR / "recepcao_operacoes_estado.json"
ARQUIVO_SAIDA = BASE_DIR / "entrada_recepcao_operacoes.json"


def agora():
    return datetime.now(timezone.utc).isoformat()


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

    temporario = caminho.with_suffix(caminho.suffix + ".tmp")

    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(
            dados,
            arquivo,
            ensure_ascii=False,
            indent=2
        )

    temporario.replace(caminho)


def conectar_banco_operacoes():
    temporario = None

    if BANCO_NORMAL.is_file():
        caminho = BANCO_NORMAL

    elif BANCO_COMPACTADO.is_file():
        temporario = tempfile.TemporaryDirectory(
            prefix="recepcao_operacoes_"
        )

        caminho = Path(temporario.name) / "operacoes.db"

        with gzip.open(BANCO_COMPACTADO, "rb") as origem:
            caminho.write_bytes(origem.read())

    else:
        raise FileNotFoundError(
            "Banco oficial de operações não encontrado: "
            "data/operacoes.db ou data/operacoes.db.gz"
        )

    conexao = sqlite3.connect(
        caminho.resolve().as_uri() + "?mode=ro",
        uri=True
    )

    conexao.row_factory = sqlite3.Row

    return conexao, temporario


def identificar_codigo(operacao):
    codigo = (
        operacao.get("codigo_operacao")
        or operacao.get("codigo")
        or operacao.get("id")
    )

    return str(codigo).strip() if codigo else None


def operacao_ativa(operacao):
    status = str(
        operacao.get("status")
        or operacao.get("estado")
        or operacao.get("situacao")
        or ""
    ).strip().upper()

    return status in ("ATIVA", "ABERTA", "EM_ANDAMENTO")


def carregar_operacoes_banco():
    conexao, temporario = conectar_banco_operacoes()

    try:
        tabelas = {
            linha["name"]
            for linha in conexao.execute("""
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
            """)
        }

        if "operacoes" not in tabelas:
            raise RuntimeError(
                "O banco oficial não possui a tabela operacoes."
            )

        colunas = {
            linha["name"]
            for linha in conexao.execute(
                "PRAGMA table_info(operacoes)"
            )
        }

        if "payload_json" not in colunas:
            raise RuntimeError(
                "A tabela operacoes não possui payload_json."
            )

        linhas = conexao.execute("""
            SELECT payload_json
            FROM operacoes
            ORDER BY id
        """).fetchall()

        resultado = {}

        for linha in linhas:
            try:
                operacao = json.loads(linha["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue

            if not isinstance(operacao, dict):
                continue

            codigo = identificar_codigo(operacao)

            if not codigo:
                continue

            if not operacao_ativa(operacao):
                continue

            # Uma operação oficial por código.
            resultado[codigo] = operacao

        return resultado

    finally:
        conexao.close()

        if temporario is not None:
            temporario.cleanup()


def solicitar_telegram(url, parametros):
    dados = urllib.parse.urlencode(parametros).encode("utf-8")

    requisicao = urllib.request.Request(
        url,
        data=dados,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        method="POST"
    )

    with urllib.request.urlopen(requisicao, timeout=25) as resposta:
        return json.loads(resposta.read().decode("utf-8"))


def interpretar_comando(texto, update_id=None):
    original = texto.strip()
    partes = original.split()

    if not partes:
        return None

    comando = partes[0].upper()

    if comando == "ENTREI":
        if len(partes) < 3:
            return {
                "update_id": update_id,
                "comando": "ENTREI",
                "parametros": {
                    "codigo_operacao": (
                        partes[1] if len(partes) > 1 else None
                    ),
                    "preco_entrada": None
                },
                "validacao": "FORMATO_INVALIDO",
                "recebido_em": agora()
            }

        try:
            preco = float(partes[2].replace(",", "."))
        except ValueError:
            preco = None

        return {
            "update_id": update_id,
            "comando": "ENTREI",
            "parametros": {
                "codigo_operacao": partes[1],
                "preco_entrada": preco
            },
            "recebido_em": agora()
        }

    if comando in ("CANCELA", "CANCELAR"):
        return {
            "update_id": update_id,
            "comando": "CANCELA",
            "parametros": {
                "codigo_operacao": (
                    partes[1] if len(partes) > 1 else None
                )
            },
            "recebido_em": agora()
        }

    if comando == "STOP" and len(partes) > 1:
        resposta = partes[1].upper()

        if resposta == "OK":
            nome = "STOP_OK"
        elif resposta in ("NAO", "NÃO"):
            nome = "STOP_NAO"
        else:
            nome = "DESCONHECIDO"

        return {
            "update_id": update_id,
            "comando": nome,
            "parametros": {
                "codigo_operacao": (
                    partes[2] if len(partes) > 2 else None
                )
            },
            "recebido_em": agora()
        }

    if comando in ("STATUS", "DESLIGA"):
        return {
            "update_id": update_id,
            "comando": comando,
            "parametros": {},
            "recebido_em": agora()
        }

    return {
        "update_id": update_id,
        "comando": "DESCONHECIDO",
        "parametros": {
            "texto_original": original
        },
        "recebido_em": agora()
    }


def consultar_telegram():
    token = os.getenv("TELEGRAM_ACOMPANHAMENTO_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return {
            "disponivel": False,
            "eventos": [],
            "proximo_update_id": None,
            "motivo": "Credenciais do Telegram não configuradas."
        }

    estado = carregar_json(ARQUIVO_ESTADO, {})
    ultimo_update = int(estado.get("ultimo_update_id", 0))

    url = f"https://api.telegram.org/bot{token}/getUpdates"

    try:
        resposta = solicitar_telegram(
            url,
            {
                "offset": ultimo_update + 1,
                "timeout": 0,
                "allowed_updates": json.dumps(["message"])
            }
        )

        if not resposta.get("ok"):
            raise RuntimeError(
                "Telegram retornou resposta inválida."
            )

        eventos = []
        maior_update = ultimo_update

        for item in resposta.get("result", []):
            update_id = int(item.get("update_id", 0))
            maior_update = max(maior_update, update_id)

            mensagem = item.get("message", {})
            remetente = mensagem.get("chat", {})

            if str(remetente.get("id")) != str(chat_id):
                continue

            texto = mensagem.get("text", "")

            if not texto:
                continue

            evento = interpretar_comando(texto, update_id)

            if evento:
                eventos.append(evento)

        return {
            "disponivel": True,
            "eventos": eventos,
            "proximo_update_id": maior_update,
            "motivo": None
        }

    except Exception as erro:
        return {
            "disponivel": False,
            "eventos": [],
            "proximo_update_id": None,
            "motivo": str(erro)
        }


def validar_eventos(eventos, operacoes):
    validos = []

    for evento in eventos:
        parametros = evento.get("parametros", {})
        comando = evento.get("comando")
        codigo = parametros.get("codigo_operacao")

        if comando in (
            "ENTREI",
            "CANCELA",
            "STOP_OK",
            "STOP_NAO"
        ):
            if not codigo:
                evento["validacao"] = "SEM_CODIGO_OPERACAO"

            elif codigo not in operacoes:
                evento["validacao"] = "OPERACAO_NAO_ENCONTRADA_NO_BANCO"

            elif comando == "ENTREI" and (
                parametros.get("preco_entrada") is None
                or parametros.get("preco_entrada") <= 0
            ):
                evento["validacao"] = "PRECO_ENTRADA_INVALIDO"

            else:
                evento["validacao"] = "OPERACAO_IDENTIFICADA"

        else:
            evento["validacao"] = "COMANDO_RECEBIDO"

        validos.append(evento)

    return validos


def aplicar_entrada_manual(operacoes, eventos, estado):
    """
    Aplica o preço informado pelo usuário somente na saída JSON.
    Não altera diretamente o banco oficial.
    """

    entradas = estado.get("entradas_manuais", {})

    if not isinstance(entradas, dict):
        entradas = {}

    for evento in eventos:
        if (
            evento.get("comando") != "ENTREI"
            or evento.get("validacao") != "OPERACAO_IDENTIFICADA"
        ):
            continue

        parametros = evento.get("parametros", {})
        codigo = parametros.get("codigo_operacao")
        preco = parametros.get("preco_entrada")

        if codigo and preco is not None:
            entradas[codigo] = {
                "preco_entrada": preco,
                "update_id": evento.get("update_id"),
                "recebido_em": evento.get("recebido_em")
            }

    estado["entradas_manuais"] = entradas

    for codigo, operacao in operacoes.items():
        entrada = entradas.get(codigo)

        if not entrada:
            continue

        preco = entrada.get("preco_entrada")

        if preco is None:
            continue

        operacao["entrada_real"] = preco
        operacao["preco_entrada"] = preco
        operacao["entrada_confirmada_telegram"] = True
        operacao["entrada_confirmada_em"] = entrada.get(
            "recebido_em"
        )

    return operacoes


def executar_recepcao():
    BASE_DIR.mkdir(parents=True, exist_ok=True)

    estado = carregar_json(ARQUIVO_ESTADO, {})

    # ENTRADA 1: banco oficial de operações.
    operacoes = carregar_operacoes_banco()

    # ENTRADA 2: Telegram.
    telegram = consultar_telegram()

    eventos = validar_eventos(
        telegram.get("eventos", []),
        operacoes
    )

    operacoes = aplicar_entrada_manual(
        operacoes,
        eventos,
        estado
    )

    resultado = {
        "modulo": "recepcao_operacoes_xau",
        "versao": 2,
        "executado_em": agora(),

        "fontes": {
            "telegram": telegram.get("disponivel", False),
            "banco_operacoes": (
                "operacoes.db ou operacoes.db.gz"
            ),
            "tabela": "operacoes",
            "campo": "payload_json"
        },

        "quantidade_operacoes_ativas": len(operacoes),
        "operacoes_ativas": operacoes,

        "eventos_telegram": eventos,
        "telegram_erro": telegram.get("motivo"),

        "execucao_ordens": False,
        "gravacao_banco_oficial": False
    }

    salvar_json(ARQUIVO_SAIDA, resultado)

    if telegram.get("proximo_update_id") is not None:
        estado["ultimo_update_id"] = telegram[
            "proximo_update_id"
        ]

    estado["atualizado_em"] = agora()

    salvar_json(ARQUIVO_ESTADO, estado)

    print("RECEPCAO DE OPERACOES CONCLUIDA")
    print("Operações ativas:", len(operacoes))
    print("Comandos recebidos:", len(eventos))
    print("Banco consultado: operacoes.db")
    print("Saída:", ARQUIVO_SAIDA)


if __name__ == "__main__":
    executar_recepcao()
