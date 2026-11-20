#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Consulta o banco oficial XAUUSD em modo somente leitura e responde via Gemini."""
import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

DB = Path("data/xauusd.db")
API = "https://generativelanguage.googleapis.com/v1beta"
MAX_CONTEXT = 22000
ROWS_PER_TABLE = 8


def carregar_contexto(pergunta):
    if not DB.is_file():
        raise RuntimeError("Banco oficial data/xauusd.db não está presente no checkout do repositório.")
    uri = "file:" + str(DB.resolve()) + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        tabelas = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        contexto = {
            "origem": "Banco oficial Central XAUUSD",
            "arquivo": "data/xauusd.db",
            "modo": "somente leitura",
            "pergunta_usuario": pergunta,
            "tabelas": {}
        }
        for tabela in tabelas:
            seguro = '"' + tabela.replace('"', '""') + '"'
            colunas = [dict(r) for r in con.execute("PRAGMA table_info(" + seguro + ")")]
            ordem = [r["name"] for r in colunas]
            # Usa a coluna temporal quando existir; caso contrário, amostra os primeiros registros.
            temporal = next((c for c in ("data_hora", "atualizado_em", "data", "timestamp")
                             if c in ordem), None)
            sql = "SELECT * FROM " + seguro
            if temporal:
                sql += " ORDER BY \"" + temporal.replace('"', '""') + "\" DESC"
            sql += " LIMIT ?"
            linhas = [dict(r) for r in con.execute(sql, (ROWS_PER_TABLE,))]
            contexto["tabelas"][tabela] = {
                "colunas": ordem,
                "amostra_recente": linhas,
                "quantidade_total": con.execute("SELECT COUNT(*) FROM " + seguro).fetchone()[0]
            }
        bruto = json.dumps(contexto, ensure_ascii=False, default=str)
        if len(bruto) > MAX_CONTEXT:
            for item in contexto["tabelas"].values():
                item["amostra_recente"] = item["amostra_recente"][:3]
            bruto = json.dumps(contexto, ensure_ascii=False, default=str)
        if len(bruto) > MAX_CONTEXT:
            # Preserva esquema e contagens; remove amostras grandes sem alterar o banco.
            for item in contexto["tabelas"].values():
                item["amostra_recente"] = item["amostra_recente"][:1]
            bruto = json.dumps(contexto, ensure_ascii=False, default=str)
        if len(bruto) > MAX_CONTEXT:
            raise RuntimeError("O esquema e as amostras mínimas do banco excederam o limite seguro de contexto.")
        return bruto
    finally:
        con.close()


def consultar_gemini(pergunta, contexto):
    chave = os.getenv("GEMINI_API_KEY", "").strip()
    if not chave:
        raise RuntimeError("Secret GEMINI_API_KEY não configurado no repositório.")
    modelo = os.getenv("GESTOR_GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
    prompt = (
        "Você é o Gestor de Mercado XAUUSD. Responda em português brasileiro, "
        "com clareza e objetividade, à mensagem do usuário. Use o banco oficial "
        "fornecido como fonte factual. Diferencie dados observados de inferências. "
        "Não invente registros, preços, indicadores ou resultados. Se a amostra "
        "não permitir responder, diga exatamente qual dado não está disponível. "
        "O banco é somente leitura: não solicite nem proponha alterar seus registros. "
        "Não execute ordens e não prometa execução automática. Se a pergunta for "
        "conversa geral, responda normalmente sem forçar uma análise de mercado.\n\n"
        "MENSAGEM DO USUÁRIO:\n" + pergunta + "\n\n"
        "CONTEXTO EXTRAÍDO DO SQLITE OFICIAL (somente leitura):\n" + contexto
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2500}
    }
    req = urllib.request.Request(
        API + "/" + modelo + ":generateContent",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": chave},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detalhe = error.read().decode("utf-8", "replace")[:700]
        raise RuntimeError("Gemini HTTP " + str(error.code) + ": " + detalhe)
    parts = result.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    resposta = "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()
    if not resposta:
        raise RuntimeError("Gemini retornou uma resposta vazia.")
    return resposta


def main():
    pergunta = os.getenv("GESTOR_TEXTO", "").strip()
    if not pergunta:
        raise RuntimeError("Mensagem do Telegram não recebida.")
    contexto = carregar_contexto(pergunta)
    print("Consulta SQLite oficial concluída em modo somente leitura.")
    resposta = consultar_gemini(pergunta, contexto)
    Path("gestor_resposta.txt").write_text(resposta, encoding="utf-8")
    print("Resposta do Gestor gerada pelo Gemini.")


if __name__ == "__main__":
    main()
