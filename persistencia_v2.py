"""
Persistência local da V2.

Salva e recupera dados JSON.
Não envia sinais e não executa operações.
"""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


PASTA_DADOS = Path("dados_v2")


def salvar_json(dados, caminho):
    """Salva um dicionário ou lista JSON de forma atômica."""
    if not isinstance(dados, (dict, list)):
        raise ValueError("Os dados precisam ser um dicionário ou uma lista.")

    arquivo = Path(caminho)
    arquivo.parent.mkdir(parents=True, exist_ok=True)

    conteudo = json.dumps(
        dados,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )

    temporario = None

    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(arquivo.parent),
            delete=False,
        ) as f:
            temporario = f.name
            f.write(conteudo)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temporario, arquivo)
        return True

    finally:
        if temporario and os.path.exists(temporario):
            os.remove(temporario)


def carregar_json(caminho, padrao=None):
    """Carrega JSON; se o arquivo não existir, retorna o padrão."""
    arquivo = Path(caminho)

    if not arquivo.exists():
        return padrao

    try:
        with arquivo.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as erro:
        raise ValueError(
            f"Não foi possível carregar o arquivo: {arquivo}"
        ) from erro


def salvar_estado(estado):
    """Salva o estado geral da V2."""
    caminho = PASTA_DADOS / "estado_v2.json"
    return salvar_json(estado, caminho)


def carregar_estado():
    """Recupera o estado geral da V2."""
    caminho = PASTA_DADOS / "estado_v2.json"
    estado = carregar_json(caminho, padrao={})

    if not isinstance(estado, dict):
        raise ValueError("O estado salvo precisa ser um dicionário.")

    return estado


if __name__ == "__main__":
    print("Módulo de persistência carregado.")
    print("Nenhum arquivo foi alterado.")
    print("Persistência entre execuções ainda depende da hospedagem.")
