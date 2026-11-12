
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
COLETOR XAUUSD

Origem exclusiva:
Artefato memoria-xauusd-condutor, publicado pelo workflow
Memoria Persistente XAUUSD.

Nao varre a pasta data/ procurando arquivos soltos.
Nao altera os seis bancos oficiais.
Prepara os arquivos recebidos para o Organizador.
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
    ".json",
    ".jsonl",
    ".csv",
    ".txt",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".gz",
}

BANCOS_OFICIAIS = {
    "graficos.db",
    "graficos.db.gz",
    "sinais.db",
    "sinais.db.gz",
    "memoria.db",
    "memoria.db.gz",
    "operacoes.db",
    "operacoes.db.gz",
    "estatisticas_m5.db",
    "estatisticas_m5.db.gz",
    "resultados_operacoes.db",
    "resultados_operacoes.db.gz",
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
            "Falha ao executar: " + " ".join(comando)
        )

    return resultado.stdout


def identificar_run():
    evento = os.getenv("GITHUB_EVENT_NAME", "")
    caminho_evento = os.getenv("GITHUB_EVENT_PATH", "")

    if evento == "workflow_run":
        if not caminho_evento:
            raise RuntimeError(
                "GITHUB_EVENT_PATH nao foi informado."
            )

        dados = json.loads(
            Path(caminho_evento).read_text(encoding="utf-8")
        )

        run_id = (
            dados.get("workflow_run", {}).get("id")
        )

        conclusao = (
            dados.get("workflow_run", {}).get("conclusion")
        )

        if not run_id:
            raise RuntimeError(
                "ID da execucao de origem nao encontrado."
            )

        if conclusao != "success":
            raise RuntimeError(
                "A Memoria Persistente nao terminou com sucesso."
            )

        return str(run_id)

    if evento == "workflow_dispatch":
        saida = executar_comando([
            "gh",
            "run",
            "list",
            "--workflow",
            WORKFLOW_MEMORIA,
            "--status",
            "success",
            "--limit",
            "1",
            "--json",
            "databaseId",
        ])

        execucoes = json.loads(saida)

        if not execucoes:
            raise RuntimeError(
                "Nenhuma execucao bem-sucedida da Memoria foi encontrada."
            )

        run_id = execucoes[0].get("databaseId")

        if not run_id:
            raise RuntimeError(
                "ID da ultima execucao da Memoria nao encontrado."
            )

        return str(run_id)

    raise RuntimeError(
        f"Evento nao suportado pelo Coletor: {evento}"
    )


def baixar_artefato():
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")

    if not token:
        raise RuntimeError(
            "Token GH_TOKEN nao foi disponibilizado ao Coletor."
        )

    repositorio = os.getenv("GITHUB_REPOSITORY")

    if not repositorio:
        raise RuntimeError(
            "GITHUB_REPOSITORY nao foi informado."
        )

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
        "gh",
        "run",
        "download",
        run_id,
        "--repo",
        repositorio,
        "--name",
        ARTEFATO,
        "--dir",
        str(ENTRADA),
    ])

    arquivos = [
        x for x in ENTRADA.rglob("*")
        if x.is_file()
    ]

    if not arquivos:
        raise RuntimeError(
            "O artefato foi baixado, mas nao contem arquivos."
        )

    print("ARQUIVOS RECEBIDOS:", len(arquivos))

    return run_id, arquivos


def classificar(arquivo):
    nome = arquivo.name.lower()

    # OPERACOES E COORDENADOR
    if nome in {
        "acompanhamento_autonomo_estado.json",
        "entrada_recepcao_operacoes.json",
        "recepcao_operacoes_estado.json",
        "decisoes_operacoes_xau.json",
        "interacao_telegram_estado.json",
        "resultado_interacao_telegram.json",
    }:
        return "operacoes"

    # RESULTADOS REAIS DE OPERACOES
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

    # ESTATISTICAS E PROBABILIDADE M5
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

    # SINAIS
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

    # MEMORIA E APRENDIZADO
    if any(x in nome for x in (
        "memoria",
        "gemini",
        "aprendizado",
        "aprendizagem",
        "parametro_adaptativo",
        "estatisticas_consolidacao",
        "indice_arquivos",
        "manifesto_memoria",
        "probabilidade_direcional",
    )):
        return "memoria"

    # GRAFICOS, CANDLES E TABULEIRO
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
    )):
        return "graficos"

    return None


def executar():
    print("COLETOR XAUUSD - INICIANDO")

    run_id, arquivos = baixar_artefato()

    if SAIDA.exists():
        shutil.rmtree(SAIDA)

    for modulo in MODULOS:
        (SAIDA / modulo).mkdir(
            parents=True,
            exist_ok=True,
        )

    relatorio = {
        "versao": 2,
        "origem": "workflow_artifact",
        "workflow_origem": "Memoria Persistente XAUUSD",
        "artefato": ARTEFATO,
        "run_id_origem": run_id,
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
            relatorio["sem_classificacao"].append(nome)
            print("[SEM CLASSIFICACAO]", nome)
            continue

        try:
            destino = SAIDA / modulo / arquivo.name
            destino.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            shutil.copy2(arquivo, destino)

            tamanho = destino.stat().st_size

            relatorio["modulos"][modulo]["arquivos"] += 1
            relatorio["modulos"][modulo]["bytes"] += tamanho

            print(
                f"[{modulo.upper()}] "
                f"{nome} | {tamanho} bytes"
            )

        except Exception as erro:
            relatorio["erros"].append({
                "arquivo": nome,
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
    relatorio["total_erros"] = len(
        relatorio["erros"]
    )

    # Arquivos fundamentais publicados pela Memoria.
    obrigatorios = {
        "memoria_agente.json",
        "historico_decisoes_xau.json",
        "resultados_operacoes.csv",
        "contexto_tabuleiro_m5.json",
        "historico_velas_m5.csv",
        "manifesto_memoria_xau.json",
    }

    nomes_recebidos = {
        arquivo.name for arquivo in arquivos
    }

    ausentes = sorted(
        obrigatorios - nomes_recebidos
    )

    relatorio["arquivos_obrigatorios_ausentes"] = ausentes

    sucesso = (
        total > 0
        and not relatorio["erros"]
        and not ausentes
    )

    relatorio["status"] = (
        "SUCESSO"
        if sucesso
        else "CONCLUIDO_COM_ERROS"
    )

    SAIDA.mkdir(parents=True, exist_ok=True)

    (SAIDA / "manifesto_coletor.json").write_text(
        json.dumps(
            relatorio,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("=" * 60)
    print("ARQUIVOS RECEBIDOS:", len(arquivos))
    print("ARQUIVOS COLETADOS:", total)
    print("SEM CLASSIFICACAO:", len(
        relatorio["sem_classificacao"]
    ))
    print("OBRIGATORIOS AUSENTES:", ausentes)
    print("ERROS:", len(relatorio["erros"]))
    print("STATUS:", relatorio["status"])
    print("=" * 60)

    if not sucesso:
        raise SystemExit(1)


if __name__ == "__main__":
    executar()
