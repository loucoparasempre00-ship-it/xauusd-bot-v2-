
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SAIDA = DATA / "coletor_saida"

MODULOS = (
    "graficos",
    "sinais",
    "memoria",
    "operacoes",
    "estatisticas_m5",
    "resultados_operacoes",
)

BANCOS = {
    "graficos.db", "graficos.db.gz",
    "sinais.db", "sinais.db.gz",
    "memoria.db", "memoria.db.gz",
    "operacoes.db", "operacoes.db.gz",
    "estatisticas_m5.db", "estatisticas_m5.db.gz",
    "resultados_operacoes.db",
    "resultados_operacoes.db.gz",
}

RELATORIOS_IGNORADOS = {
    "relatorio_organizador_xauusd.json",
    "relatorio_coletor_xauusd.json",
}


def agora():
    return datetime.now(timezone.utc).isoformat()


def classificar(arquivo):
    nome = arquivo.name.lower()

    if nome == "acompanhamento_autonomo_estado.json":
        return "operacoes"

    if any(x in nome for x in (
        "resultado-agente-gemini",
        "historico-agente-gemini",
        "decisao_gemini",
        "decisão_gemini",
        "historico_decisoes",
        "historico_decisões",
        "analise_tecnica_contexto",
        "sinal",
        "ranking",
        "marcador",
        "setup_matematico",
        "setup_matemático",
        "decisao_sinal",
        "decisão_sinal",
        "organizado_02_gerador",
    )):
        return "sinais"

    if any(x in nome for x in (
        "estatisticas_regioes_m5",
        "proximas_regioes_m5",
        "regioes_estaveis_m5",
        "resultados_eventos_m5",
        "resumo_estatistico_m5",
        "transicoes_estaveis_m5",
        "transicoes_probabilidade_m5",
        "organizado_06_estatisticas",
    )):
        return "estatisticas_m5"

    if any(x in nome for x in (
        "resultados_operacoes",
        "resultado_operacoes",
        "resumo_resultados_m5",
        "eventos_operacao",
        "cadastro_mestre",
        "condutor",
        "acompanhamento",
        "acompanhador",
        "agente_operacional",
        "decisao_agente_operacional",
        "organizado_04_condutor",
        "organizado_05_organizador",
    )):
        return "resultados_operacoes"

    if any(x in nome for x in (
        "memoria",
        "memória",
        "gemini",
        "aprendizado",
        "aprendizagem",
        "parametro_adaptativo",
        "parâmetro_adaptativo",
        "estatisticas_consolidacao",
        "estatísticas_consolidação",
        "indice_arquivos",
        "índice_arquivos",
        "organizado_03_memoria",
        "relatorio_organizador_memoria",
    )):
        return "memoria"

    if any(x in nome for x in (
        "xauusd_m5",
        "vela",
        "grafico",
        "gráfico",
        "tabuleiro",
        "regiao",
        "região",
        "borda",
        "transicao",
        "transição",
        "sequencia",
        "sequência",
        "ohlc",
        "organizado_01_graficos",
        "relatorio_tabuleiro",
    )):
        return "graficos"

    return None


def executar():
    print("COLETOR XAUUSD - INICIANDO")

    if SAIDA.exists():
        shutil.rmtree(SAIDA)

    for modulo in MODULOS:
        (SAIDA / modulo).mkdir(parents=True, exist_ok=True)

    relatorio = {
        "versao": 1,
        "data_utc": agora(),
        "status": "INICIADO",
        "modulos": {},
        "sem_classificacao": [],
        "erros": [],
    }

    for modulo in MODULOS:
        relatorio["modulos"][modulo] = {
            "arquivos": 0,
            "bytes": 0,
        }

    arquivos = sorted(
        arquivo for arquivo in DATA.rglob("*")
        if arquivo.is_file()
    )

    for arquivo in arquivos:
        relativo = arquivo.relative_to(DATA)

        if SAIDA in arquivo.parents:
            continue

        if arquivo.name.startswith("."):
            continue

        if arquivo.name in BANCOS:
            continue

        if arquivo.name in RELATORIOS_IGNORADOS:
            continue

        if arquivo.suffix.lower() not in (
            ".json", ".jsonl", ".csv", ".gz",
            ".txt", ".db", ".sqlite", ".sqlite3"
        ):
            continue

        modulo = classificar(arquivo)

        if modulo is None:
            relatorio["sem_classificacao"].append(
                relativo.as_posix()
            )
            continue

        try:
            destino = SAIDA / modulo / relativo
            destino.parent.mkdir(parents=True, exist_ok=True)

            shutil.copy2(arquivo, destino)

            tamanho = destino.stat().st_size

            relatorio["modulos"][modulo]["arquivos"] += 1
            relatorio["modulos"][modulo]["bytes"] += tamanho

            print(
                f"[{modulo.upper()}] "
                f"{relativo.as_posix()} | {tamanho} bytes"
            )

        except Exception as erro:
            relatorio["erros"].append({
                "arquivo": relativo.as_posix(),
                "erro": str(erro),
            })

    total = sum(
        item["arquivos"]
        for item in relatorio["modulos"].values()
    )

    relatorio["total_arquivos"] = total
    relatorio["total_sem_classificacao"] = len(
        relatorio["sem_classificacao"]
    )
    relatorio["total_erros"] = len(relatorio["erros"])

    relatorio["status"] = (
        "SUCESSO"
        if not relatorio["erros"]
        and not relatorio["sem_classificacao"]
        else "CONCLUIDO_COM_PENDENCIAS"
    )

    (SAIDA / "manifesto_coletor.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("ARQUIVOS COLETADOS:", total)
    print("SEM CLASSIFICAÇÃO:", len(relatorio["sem_classificacao"]))
    print("ERROS:", len(relatorio["erros"]))
    print("STATUS:", relatorio["status"])

    if relatorio["status"] != "SUCESSO":
        raise SystemExit(1)


if __name__ == "__main__":
    executar()
