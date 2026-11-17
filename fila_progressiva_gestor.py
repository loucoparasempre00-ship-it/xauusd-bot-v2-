#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transfere incrementalmente os seis bancos oficiais ao Gestor.

A Central permanece como fonte. O checkpoint registra hashes enviados e um
lote pendente completo para que uma falha/reexecução não perca nem duplique
dados. Nenhum banco original é alterado.
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
import urllib.request
from datetime import datetime, timezone

BANCOS = {
    "graficos": "data/graficos.db.gz",
    "estatisticas_m5": "data/estatisticas_m5.db.gz",
    "memoria": "data/memoria.db.gz",
    "operacoes": "data/operacoes.db.gz",
    "resultados_operacoes": "data/resultados_operacoes.db.gz",
    "sinais": "data/sinais.db.gz",
}
CHECKPOINT = "data/gestor_checkpoint.json"
TAMANHO_LOTE = 100
TIPO_PACOTE = "espelho_bancos_leitura"


def agora():
    return datetime.now(timezone.utc).isoformat()


def hash_registro(banco, tabela, registro):
    bruto = json.dumps(registro, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
    origem = (banco + "|" + tabela + "|" + bruto).encode("utf-8")
    return hashlib.sha256(origem).hexdigest(), bruto


def serializar(valor):
    if isinstance(valor, (bytes, bytearray, memoryview)):
        return {"__tipo__": "blob",
                "base64": base64.b64encode(bytes(valor)).decode("ascii")}
    return valor


def carregar_checkpoint():
    if not os.path.exists(CHECKPOINT):
        return {"versao": 1, "enviados": {}, "lotes": {}, "pendente": None,
                "ultimo_envio": None}
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
        conexao = sqlite3.connect("file:" + os.path.abspath(nome_temp) + "?mode=ro",
                                  uri=True)
        conexao.row_factory = sqlite3.Row
        return conexao, nome_temp
    except Exception:
        if os.path.exists(nome_temp):
            os.remove(nome_temp)
        raise


def ler_tabela(caminho, tabela):
    conexao, temporario = abrir_banco(caminho)
    try:
        nome = '"' + tabela.replace('"', '""') + '"'
        colunas = [r["name"] for r in conexao.execute(
            "PRAGMA table_info(" + nome + ")").fetchall()]
        if not colunas:
            return [], [], 0
        total = conexao.execute("SELECT COUNT(*) FROM " + nome).fetchone()[0]
        registros = []
        for linha in conexao.execute("SELECT * FROM " + nome):
            registros.append({k: serializar(v) for k, v in dict(linha).items()})
        return colunas, registros, total
    finally:
        conexao.close()
        if os.path.exists(temporario):
            os.remove(temporario)


def listar_tabelas(caminho):
    conexao, temporario = abrir_banco(caminho)
    try:
        return [r[0] for r in conexao.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
    finally:
        conexao.close()
        if os.path.exists(temporario):
            os.remove(temporario)


def url_receptor():
    base = os.environ.get("GESTOR_XAUUSD_URL", "").strip().rstrip("/")
    token = os.environ.get("GESTOR_XAUUSD_TOKEN", "").strip()
    if not base or not token:
        raise RuntimeError("Configure GESTOR_XAUUSD_URL e GESTOR_XAUUSD_TOKEN nos secrets.")
    if not base.endswith("/central/bancos"):
        base += "/central/bancos"
    return base, token


def enviar(payload):
    url, token = url_receptor()
    corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    requisicao = urllib.request.Request(
        url, data=corpo, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 "X-Central-Secret": token})
    try:
        with urllib.request.urlopen(requisicao, timeout=90) as resposta:
            texto = resposta.read().decode("utf-8")
            dados = json.loads(texto) if texto else {}
            if resposta.status < 200 or resposta.status >= 300:
                raise RuntimeError("Gestor respondeu HTTP " + str(resposta.status))
    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError("Gestor respondeu HTTP " + str(erro.code) + ": " + detalhe)
    if dados.get("recebido") is not True or dados.get("armazenado") is not True:
        raise RuntimeError("Gestor não confirmou armazenamento: " +
                           json.dumps(dados, ensure_ascii=False)[:500])
    return dados


def confirmar_pendente(estado):
    pendente = estado.get("pendente")
    if not pendente:
        return
    resposta = enviar(pendente["payload"])
    chave = pendente["chave"]
    enviados = estado["enviados"].setdefault(chave, [])
    conjunto = set(enviados)
    for identificador in pendente["hashes"]:
        if identificador not in conjunto:
            enviados.append(identificador)
            conjunto.add(identificador)
    estado["lotes"][chave] = max(
        int(estado["lotes"].get(chave, 0)), int(pendente["lote"])
    )
    estado["ultimo_envio"] = agora()
    estado["ultimo_ack"] = {
        "banco": pendente["banco"], "tabela": pendente["tabela"],
        "lote": pendente["lote"], "recebido_em": estado["ultimo_envio"],
        "duplicado": bool(resposta.get("duplicado")),
        "caminho": resposta.get("caminho")
    }
    estado["pendente"] = None
    salvar_checkpoint(estado)
    print("ACK:", estado["ultimo_ack"], flush=True)


def preparar_payload(estado, banco, tabela, colunas, registros, total):
    chave = banco + "|" + tabela
    enviados = set(estado["enviados"].get(chave, []))
    pendentes = []
    hashes = []
    for registro in registros:
        identificador, _ = hash_registro(banco, tabela, registro)
        if identificador not in enviados:
            pendentes.append(registro)
            hashes.append(identificador)

    if not pendentes:
        return None

    numero = int(estado["lotes"].get(chave, 0)) + 1
    ciclo_id = "incremental-" + banco + "-" + hashlib.sha256(
        tabela.encode("utf-8")).hexdigest()[:12]
    payload = {
        "tipo": TIPO_PACOTE,
        "somente_leitura": True,
        "ciclo_id": ciclo_id,
        "dados": {
            "banco": banco, "tabela": tabela, "lote": numero,
            "colunas": colunas, "total_registros_origem": total,
            "registros": pendentes[:TAMANHO_LOTE],
            "ultimo_lote": len(pendentes) <= TAMANHO_LOTE
        }
    }
    selecionados = hashes[:TAMANHO_LOTE]
    estado["pendente"] = {
        "chave": chave, "banco": banco, "tabela": tabela, "lote": numero,
        "hashes": selecionados, "payload": payload
    }
    salvar_checkpoint(estado)
    return True


def transferir():
    estado = carregar_checkpoint()
    confirmar_pendente(estado)

    total_enviados = 0
    for banco, caminho in BANCOS.items():
        print("Verificando banco:", banco, flush=True)
        tabelas = listar_tabelas(caminho)
        for tabela in tabelas:
            colunas, registros, total = ler_tabela(caminho, tabela)
            chave = banco + "|" + tabela
            enviados = set(estado["enviados"].get(chave, []))
            novos = []
            hashes = []
            for registro in registros:
                identificador, _ = hash_registro(banco, tabela, registro)
                if identificador not in enviados:
                    novos.append(registro)
                    hashes.append(identificador)
            if not novos:
                print(tabela + ": sem registros novos", flush=True)
                continue

            print(tabela + ": " + str(len(novos)) + " registros novos", flush=True)
            while novos:
                numero = int(estado["lotes"].get(chave, 0)) + 1
                ciclo_id = "incremental-" + banco + "-" + hashlib.sha256(
                    tabela.encode("utf-8")).hexdigest()[:12]
                lote_registros = novos[:TAMANHO_LOTE]
                lote_hashes = hashes[:TAMANHO_LOTE]
                payload = {
                    "tipo": TIPO_PACOTE, "somente_leitura": True,
                    "ciclo_id": ciclo_id,
                    "dados": {
                        "banco": banco, "tabela": tabela, "lote": numero,
                        "colunas": colunas, "total_registros_origem": total,
                        "registros": lote_registros,
                        "ultimo_lote": len(novos) <= TAMANHO_LOTE
                    }
                }
                estado["pendente"] = {
                    "chave": chave, "banco": banco, "tabela": tabela,
                    "lote": numero, "hashes": lote_hashes, "payload": payload
                }
                salvar_checkpoint(estado)
                confirmar_pendente(estado)
                total_enviados += len(lote_registros)
                novos = novos[TAMANHO_LOTE:]
                hashes = hashes[TAMANHO_LOTE:]

    print("TRANSFERÊNCIA CONCLUÍDA. Novos registros enviados:", total_enviados)


def main():
    if len(sys.argv) != 2 or sys.argv[1] != "transferir":
        print("Uso: python fila_progressiva_gestor.py transferir")
        raise SystemExit(2)
    transferir()


if __name__ == "__main__":
    main()
