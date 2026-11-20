#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Consulta o banco oficial XAUUSD em modo somente leitura e responde via Gemini."""
import json
import os
import sqlite3
import urllib.error
import urllib.parse
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
            temporal = next((c for c in ("data_hora", "atualizado_em", "data", "timestamp")
                             if c in ordem), None)
            sql = "SELECT * FROM " + seguro
            if temporal:
                sql += ' ORDER BY "' + temporal.replace('"', '""') + '" DESC'
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
            for item in contexto["tabelas"].values():
                item["amostra_recente"] = item["amostra_recente"][:1]
            bruto = json.dumps(contexto, ensure_ascii=False, default=str)
        if len(bruto) > MAX_CONTEXT:
            raise RuntimeError("O esquema e as amostras mínimas do banco excederam o limite seguro de contexto.")
        return bruto
    finally:
        con.close()


def requisicao_json(url, chave, metodo="GET", body=None):
    dados = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"x-goog-api-key": chave}
    if dados is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=dados, headers=headers, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detalhe = error.read().decode("utf-8", "replace")[:900]
        raise RuntimeError("Gemini HTTP " + str(error.code) + ": " + detalhe)


def listar_modelos(chave):
    """Descobre os modelos realmente disponíveis para esta chave e API."""
    modelos = []
    token = None
    for _ in range(5):
        url = API + "/models?pageSize=100"
        if token:
            url += "&pageToken=" + urllib.parse.quote(token)
        dados = requisicao_json(url, chave)
        for item in dados.get("models", []):
            metodos = item.get("supportedGenerationMethods", [])
            nome = item.get("name", "")
            if nome and "generateContent" in metodos:
                base = nome.rsplit("/", 1)[-1].lower()
                if not any(x in base for x in ("embedding", "image", "tts", "audio", "live", "robotics")):
                    modelos.append(nome)
        token = dados.get("nextPageToken")
        if not token:
            break
    if not modelos:
        raise RuntimeError("A API Gemini não retornou nenhum modelo de texto compatível com generateContent para esta chave.")
    preferido = os.getenv("GESTOR_GEMINI_MODEL", "").strip()
    def prioridade(nome):
        base = nome.rsplit("/", 1)[-1].lower()
        if preferido and base == preferido.rsplit("/", 1)[-1].lower():
            return (0, base)
        if "flash-lite" in base:
            return (1, base)
        if "flash" in base:
            return (2, base)
        if "pro" in base:
            return (3, base)
        return (4, base)
    return sorted(set(modelos), key=prioridade)


def consultar_gemini(pergunta, contexto):
    chave = os.getenv("GEMINI_API_KEY", "").strip()
    if not chave:
        raise RuntimeError("Secret GEMINI_API_KEY não configurado no repositório.")

    prompt = (
        "Você é o Gestor de Mercado XAUUSD. Responda em português brasileiro, "
        "com clareza e objetividade, à mensagem do usuário. Use o banco oficial "
        "fornecido como fonte factual. Diferencie dados observados de inferências. "
        "Não invente registros, preços, indicadores ou resultados. Se a amostra "
        "não permitir responder, diga exatamente qual dado não está disponível. "
        "O banco é somente leitura: não solicite nem proponha alterar seus registros. "
        "Não execute ordens e não prometa execução automática. Se a pergunta for "
        "conversa geral, responda normalmente sem forçar uma análise de mercado.\\n\\n"
        "MENSAGEM DO USUÁRIO:\\n" + pergunta + "\\n\\n"
        "CONTEXTO EXTRAÍDO DO SQLITE OFICIAL (somente leitura):\\n" + contexto
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2500}
    }

    modelos = listar_modelos(chave)
    print("Modelos Gemini compatíveis encontrados: " + str(len(modelos)))
    falhas = []
    for modelo in modelos:
        try:
            resultado = requisicao_json(API + "/" + modelo + ":generateContent", chave,
                                        metodo="POST", body=body)
            parts = resultado.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            resposta = "\\n".join(p.get("text", "") for p in parts if p.get("text")).strip()
            if resposta:
                print("Modelo Gemini selecionado automaticamente: " + modelo)
                return resposta
            falhas.append(modelo + ": resposta vazia")
        except Exception as erro:
            falhas.append(modelo + ": " + str(erro)[:220])
            print("Modelo indisponível para esta solicitação; tentando próximo candidato.")

    resumo = " | ".join(falhas[:5])
    raise RuntimeError("Nenhum modelo Gemini listado conseguiu gerar a resposta. Tentativas: " + resumo)


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
