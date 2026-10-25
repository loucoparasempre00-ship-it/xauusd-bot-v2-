import json
import os
from datetime import datetime, timezone
from pathlib import Path


class RegistroSinais:
    """
    Registro local de sinais em JSON.
    Não armazena tokens nem executa operações.
    """

    def __init__(self, caminho="dados_v2/registro_sinais.json"):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)

    def _ler(self):
        if not self.caminho.exists():
            return []

        try:
            with self.caminho.open("r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except (json.JSONDecodeError, OSError):
            raise ValueError(
                "Registro inválido ou inacessível; não será sobrescrito."
            )

        if not isinstance(dados, list):
            raise ValueError("O formato do registro deve ser uma lista.")

        return dados

    def existe(self, id_sinal):
        if not isinstance(id_sinal, str) or not id_sinal.strip():
            raise ValueError("Identificador do sinal inválido.")

        return any(
            item.get("id") == id_sinal
            for item in self._ler()
            if isinstance(item, dict)
        )

    def registrar(self, sinal):
        if not isinstance(sinal, dict):
            raise ValueError("O sinal deve ser um dicionário.")

        id_sinal = sinal.get("id")

        if not isinstance(id_sinal, str) or not id_sinal.strip():
            raise ValueError("O sinal precisa de um ID único.")

        registros = self._ler()

        if any(
            item.get("id") == id_sinal
            for item in registros
            if isinstance(item, dict)
        ):
            return {
                "registrado": False,
                "motivo": "Sinal duplicado.",
                "id": id_sinal,
            }

        registro = {
            "id": id_sinal,
            "timestamp_registro": datetime.now(
                timezone.utc
            ).isoformat(),
            "estado": "CRIADO",
            "sinal": sinal,
        }

        registros.append(registro)

        temporario = self.caminho.with_suffix(".tmp")

        try:
            with temporario.open("w", encoding="utf-8") as arquivo:
                json.dump(
                    registros,
                    arquivo,
                    ensure_ascii=False,
                    indent=2,
                )
                arquivo.flush()
                os.fsync(arquivo.fileno())

            os.replace(temporario, self.caminho)

        except OSError:
            try:
                temporario.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        return {
            "registrado": True,
            "motivo": "Sinal registrado.",
            "id": id_sinal,
        }

    def atualizar_estado(self, id_sinal, novo_estado):
        estados_validos = {
            "CRIADO",
            "APROVADO",
            "PUBLICADO",
            "EM_ABERTO",
            "ALVO",
            "STOP",
            "INDETERMINADO",
            "REJEITADO",
            "CANCELADO",
        }

        if novo_estado not in estados_validos:
            raise ValueError("Estado de sinal inválido.")

        registros = self._ler()
        encontrado = False

        for item in registros:
            if item.get("id") == id_sinal:
                item["estado"] = novo_estado
                item["timestamp_atualizacao"] = datetime.now(
                    timezone.utc
                ).isoformat()
                encontrado = True
                break

        if not encontrado:
            return {
                "atualizado": False,
                "motivo": "Sinal não encontrado.",
            }

        temporario = self.caminho.with_suffix(".tmp")

        try:
            with temporario.open("w", encoding="utf-8") as arquivo:
                json.dump(
                    registros,
                    arquivo,
                    ensure_ascii=False,
                    indent=2,
                )
                arquivo.flush()
                os.fsync(arquivo.fileno())

            os.replace(temporario, self.caminho)

        except OSError:
            try:
                temporario.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        return {
            "atualizado": True,
            "id": id_sinal,
            "estado": novo_estado,
        }


if __name__ == "__main__":
    print("Modulo de registro de sinais carregado.")
    print("Nenhum registro foi criado ou enviado.")
