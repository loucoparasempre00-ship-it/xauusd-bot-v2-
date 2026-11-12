
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
COLETOR XAUUSD

Recebe o artefato da Memoria Persistente.
Nao altera os seis bancos oficiais.
Ausencias e falhas sao registradas, sem interromper a coleta.
"""

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ENTRADA = DATA / "entrada_workflow"
SAIDA = DATA / "coletor_saida"

ARTEFATO = "memoria-xauusd-condutor"
WORKFLOW_MEMORIA = "memoria-persistente-xauusd.yml"

MODULOS = (
    "graficos",
    "sinais",
    "memoria",
    "operacoes",
    "estatisticas_m5",
    "resultados_operacoes",
)

EXTENSOES_ACEITAS = {
    ".json", ".jsonl", ".csv", ".txt",
    ".db", ".sqlite", ".sqlite3", ".gz",
}

BANCOS_OFICIAIS = {
    f"{nome}{ext}"
    for nome in MODULOS
    for ext in (".db", ".db.gz")
}


def agora():
    return datetime.now(timezone.utc).isoformat()


def executar_comando(comando):
    resultado = subprocess.run(
        comando,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(resultado.stdout)

    if resultado.returncode != 0:
        raise RuntimeError(
            f"Comando terminou com codigo {resultado.returncode}: "
            + " ".join(comando)
        )

    return resultado.stdout


def identificar_run():
    evento = os.getenv("GITHUB_EVENT_NAME", "")
    caminho_evento = os.getenv("GITHUB_EVENT_PATH", "")

    if evento == "workflow_run" and caminho_evento:
        dados = json.loads(
            Path(caminho_evento).read_text(encoding="utf-8")
        )
        run = dados.get("workflow_run", {})
        run_id = run.get("id")

        if run_id:
            return str(run_id)

    if evento == "workflow_dispatch":
        saida = executar_comando([
            "gh", "run", "list",
            "--workflow", WORKFLOW_MEMORIA,
            "--status", "success",
            "--limit", "1",
            "--json", "databaseId",
        ])

        execucoes = json.loads(saida)

        if execucoes:
            run_id = execucoes[0].get("databaseId")
            if run_id:
                return str(run_id)

    raise RuntimeError(
        "Nao foi possivel identificar uma execucao de origem."
    )


def baixar_artefato():
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    repositorio = os.getenv("GITHUB_REPOSITORY")

    if not token:
        raise RuntimeError("Token GH_TOKEN/GITHUB_TOKEN ausente.")

    if not repositorio:
        raise RuntimeError("GITHUB_REPOSITORY ausente.")

    run_id = identificar_run()

    print("=" * 60)
    print("COLETOR XAUUSD - RECEBENDO WORKFLOW")
    print("Repositorio:", repositorio)
    print("Execucao da Memoria:", run_id)
    print("Artefato:", ARTEFATO)
    print("=" * 60)

    if ENTRADA.exists():
        shutil.rmtree(ENTRADA)

    ENTRADA.mkdir(parents=True, exist_ok=True)

    os.environ["GH_TOKEN"] = token

    executar_comando([
        "gh", "run", "download", run_id,
        "--repo", repositorio,
        "--name", ARTEFATO,
        "--dir", str(ENTRADA),
    ])

    arquivos = [
        item for item in ENTRADA.rglob("*")
        if item.is_file()
    ]

    print("ARQUIVOS RECEBIDOS:", len(arquivos))
    return run_id, arquivos


def classificar(arquivo):
    nome = arquivo.name.lower()

    if nome in {
        "acompanhamento_autonomo_estado.json",
        "entrada_recepcao_operacoes.json",
        "recepcao_operacoes_estado.json",
        "decisoes_operacoes_xau.json",
        "interacao_telegram_estado.json",
        "resultado_interacao_telegram.json",
    }:
        return "operacoes"

    if any(x in nome for x in (
        "resultados_operacoes",
        "resultado_operacoes",
        "resumo_resultados_m5",
        "eventos_operacao",
        "cadastro_mestre",
        "historico_resultados",
        "resultado_condutor",
    )):
        return "resultados_operacoes"

    if any(x in nome for x in (
        "probabilidade_direcional_xau",
        "estatisticas_regioes_m5",
        "proximas_regioes_m5",
        "regioes_estaveis_m5",
        "resultados_eventos_m5",
        "resumo_estatistico_m5",
        "transicoes_estaveis_m5",
        "transicoes_probabilidade_m5",
        "sequencias_provaveis_m5",
    )):
        return "estatisticas_m5"

    if any(x in nome for x in (
        "resultado-agente-gemini",
        "historico-agente-gemini",
        "decisao_gemini",
        "historico_decisoes",
        "analise_tecnica_contexto",
        "decisao_final",
        "ranking_sinais",
        "marcadores_sinais",
        "setup_matematico",
        "decisao_sinal",
        "pacote_memoria_condutor",
    )):
        return "sinais"

    if any(x in nome for x in (
        "memoria",
        "gemini",
        "aprendizado",
        "aprendizagem",
        "parametro_adaptativo",
        "estatisticas_consolidacao",
        "indice_arquivos",
        "manifesto_memoria",
    )):
        return "memoria"

    if any(x in nome for x in (
        "xauusd_m5",
        "historico_velas",
        "vela",
        "grafico",
        "tabuleiro",
        "regiao",
        "borda",
        "transicao",
        "sequencia",
        "ohlc",
        "entrega_tabuleiro",
        "controle_tabuleiro",
        "relatorio_tabuleiro",
        "contexto_tabuleiro",
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
        "versao": 3,
        "origem": "workflow_artifact",
        "workflow_origem": "Memoria Persistente XAUUSD",
        "artefato": ARTEFATO,
        "data_utc": agora(),
        "status": "INICIADO",
        "run_id_origem": None,
        "modulos": {},
        "sem_classificacao": [],
        "erros": [],
        "avisos": [],
    }

    for modulo in MODULOS:
        relatorio["modulos"][modulo] = {
            "arquivos": 0,
            "bytes": 0,
        }

    arquivos = []

    try:
        run_id, arquivos = baixar_artefato()
        relatorio["run_id_origem"] = run_id

    except Exception as erro:
        mensagem = f"{type(erro).__name__}: {erro}"
        relatorio["avisos"].append({
            "etapa": "recebimento_artefato",
            "mensagem": mensagem,
        })
        print("[AVISO] Nao foi possivel receber o artefato:", mensagem)

    for arquivo in sorted(arquivos):
        nome = arquivo.name

        if nome.startswith("."):
            continue

        if nome in BANCOS_OFICIAIS:
            continue

        if arquivo.suffix.lower() not in EXTENSOES_ACEITAS:
            continue

        modulo = classificar(arquivo)

        if modulo is None:
            relatorio["sem_classificacao"].append(
                arquivo.relative_to(ENTRADA).as_posix()
            )
            print("[SEM CLASSIFICACAO]", nome)
            continue

        try:
            destino = SAIDA / modulo / nome
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(arquivo, destino)

            tamanho = destino.stat().st_size
            relatorio["modulos"][modulo]["arquivos"] += 1
            relatorio["modulos"][modulo]["bytes"] += tamanho

            print(f"[{modulo.upper()}] {nome} | {tamanho} bytes")

        except Exception as erro:
            relatorio["erros"].append({
                "arquivo": nome,
                "erro": f"{type(erro).__name__}: {erro}",
            })

    obrigatorios = {
        "memoria_agente.json",
        "historico_decisoes_xau.json",
        "resultados_operacoes.csv",
        "contexto_tabuleiro_m5.json",
        "historico_velas_m5.csv",
        "manifesto_memoria_xau.json",
    }

    nomes_recebidos = {arquivo.name for arquivo in arquivos}
    ausentes = sorted(obrigatorios - nomes_recebidos)

    relatorio["arquivos_obrigatorios_ausentes"] = ausentes
    relatorio["total_arquivos_recebidos"] = len(arquivos)
    relatorio["total_arquivos_coletados"] = sum(
        item["arquivos"] for item in relatorio["modulos"].values()
    )
    relatorio["total_sem_classificacao"] = len(
        relatorio["sem_classificacao"]
    )
    relatorio["total_erros"] = len(relatorio["erros"])

    if ausentes:
        relatorio["avisos"].append({
            "etapa": "validacao_fontes",
            "arquivos_ausentes": ausentes,
        })

    if relatorio["total_arquivos_coletados"] == 0:
        relatorio["avisos"].append({
            "etapa": "coleta",
            "mensagem": "Nenhum arquivo foi coletado.",
        })

    relatorio["status"] = (
        "CONCLUIDO_COM_ERROS"
        if relatorio["erros"]
        else (
            "CONCLUIDO_COM_AVISOS"
            if relatorio["avisos"] or ausentes
            else "SUCESSO"
        )
    )

    manifesto = SAIDA / "manifesto_coletor.json"
    manifesto.write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=" * 60)
    print("ARQUIVOS RECEBIDOS:", len(arquivos))
    print("ARQUIVOS COLETADOS:", relatorio["total_arquivos_coletados"])
    print("OBRIGATORIOS AUSENTES:", ausentes)
    print("ERROS:", relatorio["total_erros"])
    print("STATUS:", relatorio["status"])
    print("=" * 60)

    return relatorio


if __name__ == "__main__":
    executar()
