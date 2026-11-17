#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Organiza e envia o pacote dos seis bancos diretamente ao repositorio Gestor.

A fila registra o ultimo pacote publicado com sucesso. Cloudflare nao participa.
"""
import hashlib
import json
import os
import subprocess
import sys
import zipfile
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
PACOTE = "bancos-xauusd.zip"
GESTOR_REPO = "loucoparasempre00-ship-it/gestor-xauusd"


def agora():
    return datetime.now(timezone.utc).isoformat()


def carregar_checkpoint():
    if not os.path.exists(CHECKPOINT):
        return {"versao": 1, "ultimo_arquivo_enviado": None}
    with open(CHECKPOINT, "r", encoding="utf-8") as f:
        estado = json.load(f)
    if estado.get("versao") != 1:
        raise RuntimeError("Versao do checkpoint incompatível.")
    return estado


def salvar_checkpoint(estado):
    os.makedirs("data", exist_ok=True)
    temporario = CHECKPOINT + ".tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporario, CHECKPOINT)


def preparar_pacote():
    hash_pacote = hashlib.sha256()
    conteudos = {}
    for nome, caminho in BANCOS.items():
        if not os.path.isfile(caminho) or os.path.getsize(caminho) == 0:
            raise FileNotFoundError("Banco ausente ou vazio: " + caminho)
        import gzip
        with gzip.open(caminho, "rb") as f:
            while f.read(1024 * 1024):
                pass
        with open(caminho, "rb") as f:
            conteudo = f.read()
        nome_arquivo = os.path.basename(caminho)
        hash_pacote.update(nome_arquivo.encode("utf-8"))
        hash_pacote.update(b"\\0")
        hash_pacote.update(conteudo)
        conteudos[nome_arquivo] = conteudo
        print("Banco validado:", caminho, flush=True)

    identificador = hash_pacote.hexdigest()
    with zipfile.ZipFile(PACOTE, "w", compression=zipfile.ZIP_STORED) as z:
        for nome in sorted(conteudos):
            info = zipfile.ZipInfo(nome, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, conteudos[nome])
    with zipfile.ZipFile(PACOTE, "r") as z:
        erro = z.testzip()
        esperados = sorted(os.path.basename(p) for p in BANCOS.values())
        recebidos = sorted(z.namelist())
        if erro or recebidos != esperados:
            raise RuntimeError("Pacote ZIP invalido ou diferente dos seis bancos.")
    print("Pacote preparado:", PACOTE, "SHA256 dos bancos:", identificador,
          flush=True)
    return identificador


def transferir():
    token = os.environ.get("GESTOR_GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Secret GESTOR_GITHUB_TOKEN nao configurado.")
    estado = carregar_checkpoint()
    identificador = preparar_pacote()
    ultimo = estado.get("ultimo_arquivo_enviado") or {}

    if ultimo.get("sha256_bancos") == identificador:
        print("Nenhum arquivo novo. O pacote atual ja foi enviado ao Gestor.",
              flush=True)
        return

    run_id = os.environ.get("GITHUB_RUN_ID", "manual")
    tentativa = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    tag = "bancos-xauusd-" + run_id + "-" + tentativa

    ambiente = os.environ.copy()
    ambiente["GH_TOKEN"] = token
    comando = [
        "gh", "release", "create", tag, PACOTE,
        "--repo", GESTOR_REPO,
        "--title", "Transferencia dos seis bancos XAUUSD",
        "--notes", "Pacote enviado diretamente pela Central XAUUSD.",
        "--target", "main",
    ]
    print("Enviando pacote ao Gestor:", tag, flush=True)
    subprocess.run(comando, check=True, env=ambiente)

    estado["ultimo_arquivo_enviado"] = {
        "nome": PACOTE,
        "sha256_bancos": identificador,
        "tag": tag,
        "data_hora_utc": agora(),
        "arquivos_incluidos": sorted(os.path.basename(p) for p in BANCOS.values()),
        "destino": GESTOR_REPO,
    }
    salvar_checkpoint(estado)
    print("ENVIO CONFIRMADO PELO GITHUB. Checkpoint atualizado:",
          json.dumps(estado["ultimo_arquivo_enviado"], ensure_ascii=False),
          flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "transferir":
        print("Uso: python fila_progressiva_gestor.py transferir")
        raise SystemExit(2)
    transferir()
