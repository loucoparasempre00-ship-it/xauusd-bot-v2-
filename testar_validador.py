import unittest
from datetime import datetime, timedelta, timezone

from validador_mercado import validar_candles


class TestarValidador(unittest.TestCase):

    def criar_candles(self, minutos_atras=5):
        agora = datetime.now(timezone.utc)
        ultimo = agora - timedelta(minutes=minutos_atras)
        anterior = ultimo - timedelta(minutes=5)

        return [
            {
                "timestamp": anterior.isoformat(),
                "close": 4000.0
            },
            {
                "timestamp": ultimo.isoformat(),
                "close": 4005.0
            }
        ]

    def test_candles_validos(self):
        candles = self.criar_candles()
        resultado = validar_candles(candles)

        self.assertTrue(resultado["valido"])
        self.assertEqual(resultado["status"], "DADOS_VALIDOS")

    def test_preco_zero_e_rejeitado(self):
        candles = self.criar_candles()
        candles[1]["close"] = 0

        resultado = validar_candles(candles)

        self.assertFalse(resultado["valido"])

    def test_dados_antigos_sao_rejeitados(self):
        candles = self.criar_candles(minutos_atras=30)
        resultado = validar_candles(candles)

        self.assertFalse(resultado["valido"])
        self.assertEqual(resultado["status"], "DADOS_INVALIDOS")

    def test_candle_futuro_e_rejeitado(self):
        candles = self.criar_candles()
        candles[1]["timestamp"] = (
            datetime.now(timezone.utc) + timedelta(minutes=10)
        ).isoformat()

        resultado = validar_candles(candles)

        self.assertFalse(resultado["valido"])

    def test_horarios_fora_de_ordem_sao_rejeitados(self):
        candles = self.criar_candles()
        candles.reverse()

        resultado = validar_candles(candles)

        self.assertFalse(resultado["valido"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
