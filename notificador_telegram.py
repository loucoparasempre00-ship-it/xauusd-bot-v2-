
import os
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def enviar_telegram(mensagem, decisao, timeout=10):
    """
    Envia uma mensagem ao Telegram somente com aprovação explícita.
    O token e o chat ID são lidos de variáveis de ambiente.
    Nunca executa operações de mercado.
    """

    if not isinstance(mensagem, str) or not mensagem.strip():
        raise ValueError("A mensagem está vazia ou inválida.")

    if not isinstance(decisao, dict):
        raise ValueError("A decisão final deve ser um dicionário.")

    if decisao.get("decisao") != "APROVADO":
        return {
            "enviado": False,
            "motivo": "Não existe aprovação final explícita.",
        }

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return {
            "enviado": False,
            "motivo": "Credenciais do Telegram não configuradas.",
        }

    if not token.strip() or not chat_id.strip():
        return {
            "enviado": False,
            "motivo": "Credenciais do Telegram vazias.",
        }

    if timeout <= 0:
        raise ValueError("O timeout deve ser positivo.")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = json.dumps({
        "chat_id": chat_id,
        "text": mensagem,
    }).encode("utf-8")

    request = Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))

        if dados.get("ok") is True:
            return {
                "enviado": True,
                "motivo": "Telegram confirmou o envio.",
            }

        return {
            "enviado": False,
            "motivo": "Telegram não confirmou o envio.",
        }

    except HTTPError as erro:
        return {
            "enviado": False,
            "motivo": f"Erro HTTP do Telegram: {erro.code}.",
        }

    except (URLError, TimeoutError, json.JSONDecodeError):
        return {
            "enviado": False,
            "motivo": "Falha de conexão ou resposta inválida do Telegram.",
        }


if __name__ == "__main__":
    print("Modulo de notificacao Telegram carregado.")
    print("Nenhuma mensagem foi enviada.")
