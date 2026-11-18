#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sincronização incremental dos bancos da Central com o repositório Gestor XAUUSD.

HISTÓRICO DE ALTERAÇÕES — 2026-11-18
1. A sincronização foi limitada aos bancos graficos e estatisticas_m5.
2. O envio de ZIP completo foi substituído por lotes JSON incrementais de até
   100 registros, no formato já aceito pelo Gestor.
3. A Central compara SHA-256 por registro e envia apenas registros ainda não
   confirmados. Uma linha alterada gera um novo hash e é enviada como novidade.
4. Cada lote é salvo em data/gestor_checkpoint.json antes do envio. O checkpoint
   só marca os hashes como enviados depois de confirmar o arquivo no GitHub.
5. Se a conexão cair após a gravação, a próxima execução verifica o caminho:
   conteúdo igual é reconhecido como duplicado; conteúdo diferente interrompe
   o processo para evitar sobrescrita ou perda.
6. O envio é direto pela API do GitHub para data/bancos/ no repositório Gestor.
   Não altera o Gestor, não usa Cloudflare e não modifica os bancos de origem.

REVERSÃO: o histórico GitHub conserva a versão anterior deste arquivo. Para
reverter, restaure a versão imediatamente anterior a este commit.
"""
import base64
import gzip
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# Somente estas duas bases são fontes principais da sincronização.
BANCOS = {
    "graficos": "data/graficos.db.gz",
    "estatisticas_m5": "data/estatisticas_m5.db.gz",
}

CHECKPOINT = "data/gestor_checkpoint.json"
GESTOR_REPO = "loucoparasempre00-ship-it/gestor-xauusd"
GESTOR_BRANCH = "main"
TAMANHO_LOTE = 100
FORMATO_LOTE = "gestor-xauusd-lote-v1"
TIPO_PACOTE = "espelho_bancos_leitura"


def agora():
    return datetime.now(timezone.utc).isoformat()


def carregar_checkpoint():
    if not os.path.exists(CHECKPOINT):
        return {
            "versao": 1,
            "enviados": {},
            "lotes": {},
            "pendente": None,
            "ultimo_envio": None,
        }

    with open(CHECKPOINT, "r", encoding="utf-8") as arquivo:
        estado = json.load(arquivo)

    if estado.get("versao") != 1:
        raise RuntimeError("Versão do checkpoint incompatível.")

    estado.setdefault("enviados", {})
    estado.setdefault("lotes", {})
    estado.setdefault("pendente", None)
    estado.setdefault("ultimo_envio", None)
    return estado


def salvar_checkpoint(estado):
    os.makedirs("data", exist_ok=True)
    temporario = CHECKPOINT + ".tmp"
    with open(temporario, "w", encoding="utf-8") as arquivo:
        json.dump(estado, arquivo, ensure_ascii=False, separators=(",", ":"))
        arquivo.flush()
        os.fsync(arquivo.fileno())
    os.replace(temporario, CHECKPOINT)


def serializar(valor):
    if isinstance(valor, (bytes, bytearray, memoryview)):
        return {
            "__tipo__": "blob",
            "base64": base64.b64encode(bytes(valor)).decode("ascii"),
        }
    return valor


def hash_registro(banco, tabela, registro):
    texto = json.dumps(
        registro, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    origem = (banco + "|" + tabela + "|" + texto).encode("utf-8")
    return hashlib.sha256(origem).hexdigest()


def abrir_banco(caminho):
    if not os.path.isfile(caminho) or os.path.getsize(caminho) == 0:
        raise FileNotFoundError("Banco ausente ou vazio: " + caminho)

    temporario = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    nome_temp = temporario.name
    temporario.close()

    try:
        with gzip.open(caminho, "rb") as origem, open(nome_temp, "wb") as destino:
            while True:
                bloco = origem.read(1024 * 1024)
                if not bloco:
                    break
                destino.write(bloco)

        conexao = sqlite3.connect(
            "file:" + os.path.abspath(nome_temp) + "?mode=ro", uri=True
        )
        conexao.row_factory = sqlite3.Row
        return conexao, nome_temp
    except Exception:
        if os.path.exists(nome_temp):
            os.remove(nome_temp)
        raise


def identificador_seguro(valor):
    limpo = "".join(
        caractere if caractere.isalnum() or caractere in "_-" else "_"
        for caractere in str(valor or "").strip()
    )
    if not limpo:
        raise RuntimeError("Identificador de caminho vazio.")
    return limpo[:100]


def caminho_lote(banco, tabela, ciclo_id, numero):
    return (
        "data/bancos/"
        + identificador_seguro(banco)
        + "/"
        + identificador_seguro(tabela)
        + "/"
        + identificador_seguro(ciclo_id)
        + "/lote-"
        + str(numero).zfill(6)
        + ".json"
    )


def github_request(metodo, caminho, corpo=None):
    token = os.environ.get("GESTOR_GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Secret GESTOR_GITHUB_TOKEN não configurado.")

    url = (
        "https://api.github.com/repos/"
        + GESTOR_REPO
        + "/contents/"
        + urllib.parse.quote(caminho, safe="/")
    )
    dados = None
    cabecalhos = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "central-xauusd-incremental",
    }
    if corpo is not None:
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        cabecalhos["Content-Type"] = "application/json"

    requisicao = urllib.request.Request(
        url, data=dados, method=metodo, headers=cabecalhos
    )

    try:
        with urllib.request.urlopen(requisicao, timeout=90) as resposta:
            texto = resposta.read().decode("utf-8")
            return resposta.status, json.loads(texto) if texto else {}
    except urllib.error.HTTPError as erro:
        if erro.code == 404 and metodo == "GET":
            return 404, None
        detalhe = erro.read().decode("utf-8", errors="replace")[:700]
        raise RuntimeError(
            "GitHub respondeu HTTP " + str(erro.code) + ": " + detalhe
        )


def ler_lote_existente(caminho):
    status, arquivo = github_request("GET", caminho)
    if status == 404:
        return None
    try:
        conteudo = base64.b64decode(arquivo["content"]).decode("utf-8")
        return json.loads(conteudo)
    except Exception as erro:
        raise RuntimeError(
            "Não foi possível validar o lote já existente: " + caminho
        ) from erro


def objeto_gestor(payload):
    dados = payload["dados"]
    return {
        "formato": FORMATO_LOTE,
        "somente_leitura": True,
        "ciclo_id": payload["ciclo_id"],
        "banco": dados["banco"],
        "tabela": dados["tabela"],
        "lote": int(dados["lote"]),
        "total_registros_origem": int(dados["total_registros_origem"]),
        "colunas": dados["colunas"],
        "registros": dados["registros"],
        "ultimo_lote": bool(dados.get("ultimo_lote", False)),
    }


def enviar_lote(payload):
    objeto = objeto_gestor(payload)
    caminho = caminho_lote(
        objeto["banco"], objeto["tabela"], objeto["ciclo_id"], objeto["lote"]
    )

    existente = ler_lote_existente(caminho)
    if existente is not None:
        # O Gestor acrescenta recebido_em. Comparamos os dados funcionais.
        if all(existente.get(chave) == valor for chave, valor in objeto.items()):
            print("Lote já estava armazenado; confirmação recuperada:", caminho,
                  flush=True)
            return {"caminho": caminho, "duplicado": True}
        raise RuntimeError(
            "Conflito: o caminho já existe com conteúdo diferente: " + caminho
        )

    corpo = {
        "message": "Sincronizar banco Central: " + caminho,
        "content": base64.b64encode(
            json.dumps(objeto, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("ascii"),
        "branch": GESTOR_BRANCH,
    }
    status, _ = github_request("PUT", caminho, corpo)
    if status not in (200, 201):
        raise RuntimeError("GitHub não confirmou a gravação do lote.")

    # Confirmação de leitura após gravação, antes de atualizar a memória local.
    confirmado = ler_lote_existente(caminho)
    if confirmado is None or not all(
        confirmado.get(chave) == valor for chave, valor in objeto.items()
    ):
        raise RuntimeError("Lote enviado, mas a leitura de confirmação falhou: " + caminho)

    print("Lote armazenado e conferido:", caminho, flush=True)
    return {"caminho": caminho, "duplicado": False}


def confirmar_pendente(estado):
    pendente = estado.get("pendente")
    if not pendente:
        return 0

    resposta = enviar_lote(pendente["payload"])
    chave = pendente["chave"]
    enviados = estado["enviados"].setdefault(chave, [])
    conhecidos = set(enviados)

    for identificador in pendente["hashes"]:
        if identificador not in conhecidos:
            enviados.append(identificador)
            conhecidos.add(identificador)

    estado["lotes"][chave] = max(
        int(estado["lotes"].get(chave, 0)), int(pendente["lote"])
    )
    estado["ultimo_envio"] = agora()
    estado["ultimo_ack"] = {
        "banco": pendente["banco"],
        "tabela": pendente["tabela"],
        "lote": pendente["lote"],
        "confirmado_em": estado["ultimo_envio"],
        "duplicado": resposta["duplicado"],
        "caminho": resposta["caminho"],
        "quantidade": len(pendente["hashes"]),
    }
    estado["pendente"] = None
    salvar_checkpoint(estado)
    print("Checkpoint confirmado:", estado["ultimo_ack"], flush=True)
    return len(pendente["hashes"])


def preparar_payload(banco, tabela, colunas, total, numero, registros, hashes,
                     ultimo_lote):
    ciclo_id = "incremental-" + banco + "-" + hashlib.sha256(
        tabela.encode("utf-8")
    ).hexdigest()[:12]
    return {
        "tipo": TIPO_PACOTE,
        "somente_leitura": True,
        "ciclo_id": ciclo_id,
        "dados": {
            "banco": banco,
            "tabela": tabela,
            "lote": numero,
            "colunas": colunas,
            "total_registros_origem": total,
            "registros": registros,
            "ultimo_lote": ultimo_lote,
        },
    }


def sincronizar_tabela(estado, banco, conexao, tabela):
    nome_sql = '"' + tabela.replace('"', '""') + '"'
    colunas = [
        linha["name"]
        for linha in conexao.execute("PRAGMA table_info(" + nome_sql + ")")
    ]
    if not colunas:
        return 0

    total = conexao.execute(
        "SELECT COUNT(*) FROM " + nome_sql
    ).fetchone()[0]
    linhas = conexao.execute("SELECT * FROM " + nome_sql).fetchall()

    chave = banco + "|" + tabela
    enviados = set(estado["enviados"].get(chave, []))
    novos_registros = []
    novos_hashes = []

    for linha in linhas:
        registro = {k: serializar(v) for k, v in dict(linha).items()}
        identificador = hash_registro(banco, tabela, registro)
        if identificador not in enviados:
            enviados.add(identificador)
            novos_registros.append(registro)
            novos_hashes.append(identificador)

    if not novos_registros:
        print(tabela + ": sem registros novos ou modificados.", flush=True)
        return 0

    print(
        tabela + ": " + str(len(novos_registros)) + " registros novos/modificados.",
        flush=True,
    )
    enviados_agora = 0
    numero = int(estado["lotes"].get(chave, 0)) + 1

    for inicio in range(0, len(novos_registros), TAMANHO_LOTE):
        fim = inicio + TAMANHO_LOTE
        lote_registros = novos_registros[inicio:fim]
        lote_hashes = novos_hashes[inicio:fim]
        payload = preparar_payload(
            banco, tabela, colunas, total, numero,
            lote_registros, lote_hashes,
            fim >= len(novos_registros),
        )

        # O lote fica persistido antes do envio. Se houver falha, será retomado.
        estado["pendente"] = {
            "chave": chave,
            "banco": banco,
            "tabela": tabela,
            "lote": numero,
            "hashes": lote_hashes,
            "payload": payload,
        }
        salvar_checkpoint(estado)
        enviados_agora += confirmar_pendente(estado)
        numero += 1

    return enviados_agora


def transferir():
    estado = carregar_checkpoint()

    # Recupera primeiro qualquer lote salvo por uma execução anterior.
    total_enviados = confirmar_pendente(estado)

    for banco, caminho in BANCOS.items():
        print("\nVerificando banco:", banco, flush=True)
        conexao, temporario = abrir_banco(caminho)
        try:
            tabelas = [
                linha[0]
                for linha in conexao.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                )
            ]
            for tabela in tabelas:
                total_enviados += sincronizar_tabela(
                    estado, banco, conexao, tabela
                )
        finally:
            conexao.close()
            if os.path.exists(temporario):
                os.remove(temporario)

    print(
        "\nSINCRONIZAÇÃO CONCLUÍDA. Registros confirmados nesta execução:",
        total_enviados,
        flush=True,
    )


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "transferir":
        print("Uso: python fila_progressiva_gestor.py transferir")
        raise SystemExit(2)
    transferir()
