
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import gzip
import hashlib
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
ARQUIVO = DATA / "arquivo_memoria"
DIAS_ATIVOS = int(os.getenv("DIAS_VELAS_ATIVAS", "90"))

MESTRE = DATA / "cadastro_mestre_operacoes.json"
VELAS_ATIVAS = DATA / "velas_m5_ativas_90d.csv"
VELAS_ANTIGAS = ARQUIVO / "velas_m5_anteriores.csv.gz"
INDICE = DATA / "indice_arquivos_memoria.json"
RELATORIO = DATA / "relatorio_organizador_memoria.json"


def ler_json(path, padrao):
    if not path.exists() or path.stat().st_size == 0:
        return padrao
    return json.loads(path.read_text(encoding="utf-8-sig"))


def salvar_json(path, dados):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(dados, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )
    temp.replace(path)


def numero(valor):
    try:
        return f"{float(valor):.5f}"
    except (TypeError, ValueError):
        return ""


def campo(registro, nomes):
    if not isinstance(registro, dict):
        return None

    for nome in nomes:
        if registro.get(nome) not in (None, ""):
            return registro[nome]

    for chave in (
        "diagnostico_original",
        "memoria_autonoma",
        "sinal",
        "operacao"
    ):
        filho = registro.get(chave)
        if isinstance(filho, dict):
            valor = campo(filho, nomes)
            if valor not in (None, ""):
                return valor

    return None


def identidade(registro):
    sinal_id = campo(
        registro,
        ("id_sinal", "signal_id", "codigo_sinal")
    )

    if sinal_id:
        base = "SINAL:" + str(sinal_id).strip()
    else:
        base = "|".join([
            str(campo(registro, (
                "horario_sinal_utc",
                "fechamento_candle_utc",
                "candle_utc",
                "ativada_em"
            )) or "SEM_HORARIO"),
            str(campo(registro, (
                "direcao",
                "direcao_sinal",
                "decisao_motor"
            )) or "SEM_DIRECAO").upper(),
            numero(campo(registro, (
                "entrada_sinal", "entrada", "preco_entrada"
            ))),
            numero(campo(registro, (
                "stop", "stop_loss", "stop_original"
            ))),
            numero(campo(registro, (
                "alvo", "take_profit"
            )))
        ])

    codigo = hashlib.sha256(
        base.encode("utf-8")
    ).hexdigest()[:20].upper()

    return "OP-XAU-" + codigo, base, sinal_id


def chave_ocorrencia(origem, registro):
    conteudo = json.dumps(
        registro,
        ensure_ascii=False,
        sort_keys=True,
        default=str
    )
    return hashlib.sha256(
        (origem + "|" + conteudo).encode("utf-8")
    ).hexdigest()


def organizar_operacoes():
    antigos = ler_json(
        MESTRE,
        {"operacoes": []}
    )

    grupos = {}

    # Recupera o cadastro anterior.
    # Nenhum histórico já organizado é descartado.
    for item in antigos.get("operacoes", []):
        codigo = item.get("codigo_interno")
        if not codigo:
            continue

        grupos[codigo] = item

    def adicionar(registro, origem):
        if not isinstance(registro, dict):
            return

        codigo, base, sinal_id = identidade(registro)

        item = grupos.setdefault(codigo, {
            "codigo_interno": codigo,
            "identidade": base,
            "ids_externos": [],
            "ocorrencias": [],
            "status_observados": [],
            "resultados_preservados": []
        })

        if sinal_id:
            sinal_id = str(sinal_id)
            if sinal_id not in item["ids_externos"]:
                item["ids_externos"].append(sinal_id)

        status = campo(
            registro,
            ("status", "resultado", "tipo_avaliacao")
        )

        if status and str(status) not in item["status_observados"]:
            item["status_observados"].append(str(status))

        resultado = {
            chave: registro[chave]
            for chave in (
                "status",
                "resultado_r",
                "horario_resultado_utc",
                "encerrada_em",
                "motivo"
            )
            if registro.get(chave) not in (None, "")
        }

        if resultado and resultado not in item["resultados_preservados"]:
            item["resultados_preservados"].append(resultado)

        chave = chave_ocorrencia(origem, registro)

        existentes = {
            x.get("chave")
            for x in item["ocorrencias"]
        }

        if chave not in existentes:
            item["ocorrencias"].append({
                "chave": chave,
                "origem": origem,
                "registro_original": registro
            })

    # Fonte JSON das operações.
    registros = ler_json(
        DATA / "resultados_operacoes.json",
        []
    )

    if isinstance(registros, list):
        for registro in registros:
            adicionar(
                registro,
                "resultados_operacoes.json"
            )

    # Fonte CSV de resultados e avaliações.
    csv_path = DATA / "resultados_operacoes.csv"

    if csv_path.exists() and csv_path.stat().st_size:
        with csv_path.open(
            encoding="utf-8-sig",
            newline=""
        ) as arquivo:
            leitor = csv.DictReader(
                arquivo,
                delimiter=";"
            )

            for linha in leitor:
                if not linha.get("id_sinal"):
                    continue

                adicionar(
                    linha,
                    "resultados_operacoes.csv"
                )

    operacoes = sorted(
        grupos.values(),
        key=lambda x: x["codigo_interno"]
    )

    for item in operacoes:
        item["quantidade_ocorrencias"] = len(
            item["ocorrencias"]
        )

    salvar_json(MESTRE, {
        "versao": 1,
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "total_operacoes": len(operacoes),
        "operacoes": operacoes
    })

    return {
        "total_operacoes": len(operacoes),
        "ocorrencias": sum(
            len(x["ocorrencias"]) for x in operacoes
        )
    }


def ler_velas(path):
    if not path.exists() or path.stat().st_size == 0:
        return []

    with path.open(
        encoding="utf-8-sig",
        newline=""
    ) as arquivo:
        leitor = csv.DictReader(arquivo)

        if not leitor.fieldnames or "timestamp_utc" not in leitor.fieldnames:
            raise RuntimeError(
                f"Cabeçalho inválido: {path}"
            )

        linhas = []

        for linha in leitor:
            timestamp = (linha.get("timestamp_utc") or "").strip()

            if not timestamp:
                continue

            try:
                datetime.fromisoformat(
                    timestamp.replace("Z", "+00:00")
                )
            except ValueError:
                continue

            linhas.append(linha)

        return linhas


def ler_arquivo_antigo():
    if not VELAS_ANTIGAS.exists():
        return []

    with gzip.open(
        VELAS_ANTIGAS,
        "rt",
        encoding="utf-8",
        newline=""
    ) as arquivo:
        return list(csv.DictReader(arquivo))


def escrever_csv(path, campos, linhas):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")

    with temp.open(
        "w",
        encoding="utf-8",
        newline=""
    ) as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos,
            extrasaction="ignore"
        )
        escritor.writeheader()
        escritor.writerows(linhas)

    temp.replace(path)


def escrever_gzip(path, campos, linhas):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")

    with gzip.open(
        temp,
        "wt",
        encoding="utf-8",
        newline=""
    ) as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=campos,
            extrasaction="ignore"
        )
        escritor.writeheader()
        escritor.writerows(linhas)

    # Verifica o arquivo antes de aceitá-lo.
    with gzip.open(temp, "rt", encoding="utf-8") as teste:
        teste.read(1)

    temp.replace(path)


def organizar_velas():
    fontes = [
        DATA / "historico_velas_m5.csv",
        DATA / "xauusd_m5_fechados.csv",
        DATA / "xauusd_m5.csv"
    ]

    todas = ler_arquivo_antigo()
    campos = [
        "timestamp_utc",
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]

    for fonte in fontes:
        linhas = ler_velas(fonte)

        if linhas:
            campos = list(linhas[0].keys())
            todas.extend(linhas)

    if not todas:
        return {
            "total": 0,
            "ativas": 0,
            "arquivadas": 0,
            "duplicatas": 0
        }

    # Uma vela por timestamp.
    # A fonte lida por último tem prioridade.
    unicas = {}
    duplicatas = 0

    for linha in todas:
        timestamp = linha["timestamp_utc"].strip()

        if timestamp in unicas:
            duplicatas += 1

        unicas[timestamp] = linha

    ordenadas = sorted(
        unicas.values(),
        key=lambda x: x["timestamp_utc"]
    )

    datas = []

    for linha in ordenadas:
        instante = datetime.fromisoformat(
            linha["timestamp_utc"].replace("Z", "+00:00")
        )

        if instante.tzinfo is None:
            instante = instante.replace(tzinfo=timezone.utc)

        datas.append(instante)

    agora = datetime.now(timezone.utc)
    ultima = max(datas)

    # Se a fonte estiver atrasada, usa a última vela disponível
    # para não produzir uma área ativa vazia.
    if agora - ultima > timedelta(days=DIAS_ATIVOS):
        referencia = ultima
    else:
        referencia = agora

    corte = referencia - timedelta(days=DIAS_ATIVOS)

    ativas = []
    antigas = []

    for linha, instante in zip(ordenadas, datas):
        if instante >= corte:
            ativas.append(linha)
        else:
            antigas.append(linha)

    escrever_csv(
        VELAS_ATIVAS,
        campos,
        ativas
    )

    escrever_gzip(
        VELAS_ANTIGAS,
        campos,
        antigas
    )

    return {
        "total": len(ordenadas),
        "ativas": len(ativas),
        "arquivadas": len(antigas),
        "duplicatas": duplicatas,
        "corte_utc": corte.isoformat()
    }


def inventariar():
    arquivos = []

    for path in sorted(DATA.rglob("*")):
        if not path.is_file():
            continue

        if path in (INDICE, RELATORIO):
            continue

        nome = path.name.lower()

        if "operac" in nome or "acompanhamento" in nome:
            categoria = "operacoes"
        elif "vela" in nome or "xauusd_m5" in nome:
            categoria = "velas"
        elif any(x in nome for x in (
            "tabuleiro", "regiao", "borda",
            "transicao", "sequencia"
        )):
            categoria = "tabuleiro"
        elif "gemini" in nome or "memoria" in nome:
            categoria = "memoria_ia"
        else:
            categoria = "outros"

        arquivos.append({
            "arquivo": str(path.relative_to(BASE)),
            "categoria": categoria,
            "bytes": path.stat().st_size
        })

    salvar_json(INDICE, {
        "versao": 1,
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "total_arquivos": len(arquivos),
        "arquivos": arquivos
    })

    return len(arquivos)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    ARQUIVO.mkdir(parents=True, exist_ok=True)

    operacoes = organizar_operacoes()
    velas = organizar_velas()
    arquivos = inventariar()

    relatorio = {
        "versao": 1,
        "executado_em_utc": datetime.now(timezone.utc).isoformat(),
        "politica": {
            "velas_ativas_dias": DIAS_ATIVOS,
            "velas_antigas": "Compactadas em gzip",
            "operacoes": "Histórico e resultados preservados",
            "modo": "NAO_DESTRUTIVO"
        },
        "operacoes": operacoes,
        "velas": velas,
        "arquivos_inventariados": arquivos,
        "originais_removidos": 0
    }

    salvar_json(RELATORIO, relatorio)

    print(json.dumps(
        relatorio,
        ensure_ascii=False,
        indent=2
    ))

    print("ORGANIZADOR_MEMORIA_XAU=OK")


if __name__ == "__main__":
    main()
