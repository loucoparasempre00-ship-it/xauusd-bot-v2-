
import io
import json
import zipfile


# =========================================================
# RECUPERAR SINAL POR ID EXATO DA EXECUÇÃO
# Aceita o novo Gerador e mantém compatibilidade antiga
# =========================================================

def sinal_por_id(github_get, github, repo, sinal_id):
    sinal_id = str(sinal_id).strip()

    if not sinal_id.isdigit():
        raise RuntimeError("ID do sinal inválido.")

    run_id = int(sinal_id)

    url = (
        f"{github}/repos/{repo}/actions/runs/"
        f"{run_id}/artifacts"
    )

    dados = github_get(url).json()
    artefatos = dados.get("artifacts", [])

    # Primeiro procura o artefato do novo Gerador
    artefato = next(
        (
            item for item in artefatos
            if item.get("name") == "resultado-gerador-sinal-xau"
            and not item.get("expired", False)
        ),
        None
    )

    arquivo_json = "decisao_final.json"
    formato_novo = artefato is not None

    # Compatibilidade com sinais do Agente Gemini antigo
    if artefato is None:
        artefato = next(
            (
                item for item in artefatos
                if item.get("name") == "resultado-agente-gemini"
                and not item.get("expired", False)
            ),
            None
        )
        arquivo_json = "decisao_gemini.json"

    if not artefato:
        raise RuntimeError(
            f"Não encontrei o artefato do sinal {sinal_id}."
        )

    conteudo = github_get(
        f"{github}/repos/{repo}/actions/artifacts/"
        f"{artefato['id']}/zip"
    ).content

    with zipfile.ZipFile(io.BytesIO(conteudo)) as arquivo:
        caminho = next(
            (
                nome for nome in arquivo.namelist()
                if nome.endswith(arquivo_json)
            ),
            None
        )

        if not caminho:
            raise RuntimeError(
                f"O artefato não contém {arquivo_json}."
            )

        diagnostico = json.loads(
            arquivo.read(caminho).decode("utf-8")
        )

    if formato_novo:
        id_registrado = diagnostico.get("id_sinal")

        if str(id_registrado) != str(run_id):
            raise RuntimeError(
                "O ID registrado no sinal não corresponde "
                "à execução informada."
            )

        direcao = str(
            diagnostico.get("decisao_final", "")
        ).upper()

        # Compatibilidade com o restante do Acompanhador
        diagnostico["decisao_gemini"] = direcao

    else:
        direcao = str(
            diagnostico.get("decisao_gemini", "")
        ).upper()

    enviado = diagnostico.get("envio_telegram") is True

    if direcao not in ("COMPRA", "VENDA"):
        raise RuntimeError(
            "A execução informada não contém sinal COMPRA ou VENDA."
        )

    if not enviado:
        raise RuntimeError(
            "O sinal informado não foi enviado ao Telegram."
        )

    diagnostico["_run_id"] = run_id

    return diagnostico


# =========================================================
# LOCALIZAR ÚLTIMO SINAL REALMENTE ENVIADO
# Mantido para compatibilidade com o código atual
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

        try:
            diagnostico = sinal_por_id(
                github_get,
                github,
                repo,
                run_id
            )

            return diagnostico

        except Exception:
            continue

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
        [
            "decisao_gemini",
            "decisao_final",
            "direcao",
            "decisao",
            "sinal"
        ]
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
