"""
Controle de duplicidade de sinais da V2.

Mantém um registro local dos sinais já processados.
Não envia mensagens e não executa operações.
"""

import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


PASTA_DADOS = Path("dados_v2")
ARQUIVO_REGISTRO = PASTA_DADOS / "sinais_processados.json"


def criar_id_sinal(timestamp, direcao, entrada, stop, alvo):
    """Cria um identificador estável para os parâmetros do sinal."""
    conteudo = "|".join(
        [
            str(timestamp),
            str(direcao).upper(),
            str(entrada),
            str(stop),
            str(alvo),
        ]
    )

    return hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def carregar_registro(caminho=ARQUIVO_REGISTRO):
    """Carrega os IDs já processados, ou retorna uma lista vazia."""
    arquivo = Path(caminho)

    if not arquivo.exists():
        return []

    try:
        with arquivo.open("r", encoding="utf-8") as f:
            dados = json.load(f)
    except (OSError, json.JSONDecodeError):
        raise ValueError(
            "Não foi possível ler o registro de sinais. "
            "O arquivo precisa ser verificado antes de continuar."
        )

    if not isinstance(dados, list) or not all(
        isinstance(item, str) for item in dados
    ):
        raise ValueError("Formato inválido no registro de sinais.")

    return dados


def sinal_ja_processado(id_sinal, caminho=ARQUIVO_REGISTRO):
    """Informa se um identificador já consta no registro."""
    if not isinstance(id_sinal, str) or not id_sinal:
        raise ValueError("ID do sinal inválido.")

    return id_sinal in carregar_registro(caminho)


def registrar_sinal(id_sinal, caminho=ARQUIVO_REGISTRO):
    """Registra um ID sem duplicá-lo e grava o arquivo de forma atômica."""
    if not isinstance(id_sinal, str) or not id_sinal:
        raise ValueError("ID do sinal inválido.")

    arquivo = Path(caminho)
    arquivo.parent.mkdir(parents=True, exist_ok=True)

    registro = carregar_registro(arquivo)

    if id_sinal in registro:
        return False

    registro.append(id_sinal)

    temporario = None

    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(arquivo.parent),
            delete=False,
        ) as f:
            temporario = f.name
            json.dump(registro, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temporario, arquivo)
        return True

    finally:
        if temporario and os.path.exists(temporario):
            os.remove(temporario)


if __name__ == "__main__":
    print("Controle de duplicidade carregado.")
    print("Nenhum sinal foi registrado ou enviado.")
