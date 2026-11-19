#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Consulta limitada e somente leitura ao banco oficial data/xauusd.db."""
import json, os, re, sqlite3
from pathlib import Path

DB = Path("data/xauusd.db")
OUT = Path("gestor_contexto.json")
MAX_TABLES, MAX_ROWS, MAX_CHARS = 12, 12, 24000

def ident(name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("Identificador SQLite inesperado.")
    return '"' + name + '"'

def main():
    if not DB.is_file():
        raise RuntimeError("Banco oficial data/xauusd.db não encontrado.")
    con = sqlite3.connect("file:" + str(DB.resolve()) + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT ?", (MAX_TABLES,))]
        data = {"origem":"CENTRAL_XAUUSD","banco":"data/xauusd.db",
                "modo":"somente_leitura","request_id":os.getenv("GESTOR_REQUEST_ID"),
                "tabelas":{}}
        for table in tables:
            q = ident(table)
            cols = [r["name"] for r in con.execute("PRAGMA table_info(" + q + ")")]
            rows = con.execute("SELECT * FROM " + q + " LIMIT ?", (MAX_ROWS,)).fetchall()
            data["tabelas"][table] = {"colunas":cols,
                "registros_amostra":[dict(row) for row in rows]}
        raw = json.dumps(data, ensure_ascii=False, default=str)
        if len(raw) > MAX_CHARS:
            for item in data["tabelas"].values():
                item["registros_amostra"] = item["registros_amostra"][:3]
            raw = json.dumps(data, ensure_ascii=False, default=str)
        if len(raw) > MAX_CHARS:
            raise RuntimeError("Contexto excedeu o limite de tamanho.")
        OUT.write_text(raw, encoding="utf-8")
        print("Consulta somente leitura concluída; tabelas:", len(tables))
    finally:
        con.close()

if __name__ == "__main__":
    main()
