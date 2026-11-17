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
MAX_PAYLOAD_BYTES = 512 * 1024
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
    if os.path.getsize(CHECKPOINT) == 0:
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
    ultimo_erro = None
    for tentativa in range(4):
        try:
            with urllib.request.urlopen(requisicao, timeout=180) as resposta:
                texto = resposta.read().decode("utf-8")
                dados = json.loads(texto) if texto else {}
                if resposta.status < 200 or resposta.status >= 300:
                    raise RuntimeError("Gestor respondeu HTTP " + str(resposta.status))
            ultimo_erro = None
            break
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError("Gestor respondeu HTTP " + str(erro.code) + ": " + detalhe)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as erro:
            ultimo_erro = erro
            if tentativa < 3:
                import time
                espera = 5 * (2 ** tentativa)
                print("Falha temporária no envio; nova tentativa em", espera,
                      "segundos:", str(erro)[:200], flush=True)
                time.sleep(espera)
    if ultimo_erro is not None:
        raise RuntimeError("Não foi possível concluir o envio após 4 tentativas: " +
                           str(ultimo_erro))
    if dados.get("recebido") is not True or dados.get("armazenado") is not True:
        raise RuntimeError("Gestor não confirmou armazenamento: " +
                           json.dumps(dados, ensure_ascii=False)[:500])
    return dados


def confirmar_pendente(estado):
    pendente = estado.get("pendente")
    if not pendente:
        return

    tamanho_pendente = len(json.dumps(
        pendente["payload"], ensure_ascii=False,
        separators=(",", ":")).encode("utf-8"))

    # Recupera com segurança um lote antigo que tenha sido salvo antes da
    # adoção do limite por bytes. O repositório Gestor não recebeu esse lote
    # na execução que falhou; cada fragmento ganha seu próprio número.
    if tamanho_pendente > MAX_PAYLOAD_BYTES:
        registros = pendente["payload"]["dados"]["registros"]
        hashes = pendente["hashes"]
        banco = pendente["banco"]
        tabela = pendente["tabela"]
        chave = pendente["chave"]
        ciclo_id = pendente["payload"]["ciclo_id"]
        total = pendente["payload"]["dados"]["total_registros_origem"]
        colunas = pendente["payload"]["dados"]["colunas"]
        estado["pendente"] = None
        salvar_checkpoint(estado)

        while registros:
            numero = int(estado["lotes"].get(chave, 0)) + 1
            grupo = []
            grupo_hashes = []
            for registro, identificador in zip(registros, hashes):
                tentativa = grupo + [registro]
                teste = {
                    "tipo": TIPO_PACOTE, "somente_leitura": True,
                    "ciclo_id": ciclo_id,
                    "dados": {
                        "banco": banco, "tabela": tabela, "lote": numero,
                        "colunas": colunas, "total_registros_origem": total,
                        "registros": tentativa, "ultimo_lote": False
                    }
                }
                tamanho = len(json.dumps(
                    teste, ensure_ascii=False,
                    separators=(",", ":")).encode("utf-8"))
                if grupo and tamanho > MAX_PAYLOAD_BYTES:
                    break
                grupo.append(registro)
                grupo_hashes.append(identificador)
                if tamanho > MAX_PAYLOAD_BYTES:
                    print("Aviso: registro individual excede 512 KiB durante recuperação.",
                          flush=True)
                    break

            restante = len(registros) - len(grupo)
            payload = {
                "tipo": TIPO_PACOTE, "somente_leitura": True,
                "ciclo_id": ciclo_id,
                "dados": {
                    "banco": banco, "tabela": tabela, "lote": numero,
                    "colunas": colunas, "total_registros_origem": total,
                    "registros": grupo, "ultimo_lote": restante == 0
                }
            }
            estado["pendente"] = {
                "chave": chave, "banco": banco, "tabela": tabela,
                "lote": numero, "hashes": grupo_hashes, "payload": payload
            }
            salvar_checkpoint(estado)
            confirmar_pendente(estado)
            registros = registros[len(grupo):]
            hashes = hashes[len(grupo):]
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

                # Monta lote por tamanho real do JSON (incluindo blobs em base64),
                # e não apenas por quantidade de linhas.
                lote_registros = []
                lote_hashes = []
                for registro, identificador in zip(novos, hashes):
                    candidato = lote_registros + [registro]
                    payload_teste = {
                        "tipo": TIPO_PACOTE, "somente_leitura": True,
                        "ciclo_id": ciclo_id,
                        "dados": {
                            "banco": banco, "tabela": tabela, "lote": numero,
                            "colunas": colunas, "total_registros_origem": total,
                            "registros": candidato,
                            "ultimo_lote": len(candidato) == len(novos)
                        }
                    }
                    tamanho = len(json.dumps(
                        payload_teste, ensure_ascii=False,
                        separators=(",", ":")).encode("utf-8"))
                    if lote_registros and tamanho > MAX_PAYLOAD_BYTES:
                        break
                    lote_registros.append(registro)
                    lote_hashes.append(identificador)
                    if tamanho > MAX_PAYLOAD_BYTES:
                        print("Aviso: registro individual excede 512 KiB:",
                              banco, tabela, "bytes=", tamanho, flush=True)
                        break

                payload = {
                    "tipo": TIPO_PACOTE, "somente_leitura": True,
                    "ciclo_id": ciclo_id,
                    "dados": {
                        "banco": banco, "tabela": tabela, "lote": numero,
                        "colunas": colunas, "total_registros_origem": total,
                        "registros": lote_registros,
                        "ultimo_lote": len(lote_registros) == len(novos)
                    }
                }
                tamanho_final = len(json.dumps(
                    payload, ensure_ascii=False,
                    separators=(",", ":")).encode("utf-8"))
                print("Enviando lote", numero, banco, tabela,
                      "registros=", len(lote_registros),
                      "bytes=", tamanho_final, flush=True)

                estado["pendente"] = {
                    "chave": chave, "banco": banco, "tabela": tabela,
                    "lote": numero, "hashes": lote_hashes, "payload": payload
                }
                salvar_checkpoint(estado)
                confirmar_pendente(estado)
                total_enviados += len(lote_registros)
                novos = novos[len(lote_registros):]
                hashes = hashes[len(lote_hashes):]

    print("TRANSFERÊNCIA CONCLUÍDA. Novos registros enviados:", total_enviados)


def main():
    if len(sys.argv) != 2 or sys.argv[1] != "transferir":
        print("Uso: python fila_progressiva_gestor.py transferir")
        raise SystemExit(2)
    transferir()


if __name__ == "__main__":
    main()
