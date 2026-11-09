import io
import json
import zipfile


# =========================================================
# LOCALIZAR ÚLTIMO SINAL REALMENTE ENVIADO
# =========================================================

def ultimo_sinal(github_get, github, repo):
    url = (
        f"{github}/repos/{repo}/actions/workflows/"
        "agente-gemini-xau.yml/runs"
    )

    dados = github_get(
        url + "?status=success&per_page=20"
    ).json()

    execucoes = dados.get("workflow_runs", [])

    if not execucoes:
        raise RuntimeError(
            "Não encontrei execução bem-sucedida do Agente Gemini."
        )

    for execucao in execucoes:
        run_id = execucao["id"]

        artefatos = github_get(
            f"{github}/repos/{repo}/actions/runs/"
            f"{run_id}/artifacts"
        ).json().get("artifacts", [])

        artefato = next(
            (
                item for item in artefatos
                if item["name"] == "resultado-agente-gemini"
                and not item.get("expired", False)
            ),
            None
        )

        if not artefato:
            continue

        conteudo = github_get(
            f"{github}/repos/{repo}/actions/artifacts/"
            f"{artefato['id']}/zip"
        ).content

        with zipfile.ZipFile(io.BytesIO(conteudo)) as arquivo:
            caminho = next(
                (
                    nome for nome in arquivo.namelist()
                    if nome.endswith("decisao_gemini.json")
                ),
                None
            )

            if not caminho:
                continue

            diagnostico = json.loads(
                arquivo.read(caminho).decode("utf-8")
            )

        direcao = str(
            diagnostico.get("decisao_gemini", "")
        ).upper()

        enviado = diagnostico.get("envio_telegram") is True

        if direcao not in ("COMPRA", "VENDA"):
            continue

        if not enviado:
            continue

        diagnostico["_run_id"] = run_id

        return diagnostico

    raise RuntimeError(
        "Não encontrei sinal COMPRA ou VENDA realmente enviado "
        "nas últimas 20 execuções."
    )


# =========================================================
# LOCALIZAR CAMPOS DO DIAGNÓSTICO
# =========================================================

def campo(dados, nomes):
    for nome in nomes:
        if nome in dados and dados[nome] is not None:
            return dados[nome]

    return None


# =========================================================
# PREPARAR DADOS DO SINAL
# =========================================================

def preparar_sinal(dados):
    direcao = campo(
        dados,
        ["decisao_gemini", "direcao", "decisao", "sinal"]
    )

    entrada = campo(
        dados,
        ["entrada", "preco_entrada", "entry"]
    )

    stop = campo(
        dados,
        ["stop_loss", "stop", "sl"]
    )

    alvo = campo(
        dados,
        ["take_profit", "alvo", "target", "tp"]
    )

    if not direcao or stop is None or alvo is None:
        raise RuntimeError(
            "Sinal sem direção, stop ou alvo."
        )

    direcao = str(direcao).upper()

    if "COMPRA" in direcao or "BUY" in direcao:
        direcao = "COMPRA"

    elif "VENDA" in direcao or "SELL" in direcao:
        direcao = "VENDA"

    else:
        raise RuntimeError(
            f"Direção não reconhecida: {direcao}"
        )

    return {
        "direcao": direcao,
        "entrada_sinal": (
            float(entrada) if entrada is not None else None
        ),
        "stop": float(stop),
        "alvo": float(alvo)
    }
