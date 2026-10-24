import unittest

from motor_matematico import (
    detectar_cruzamento,
    detectar_pullback,
)


class TestarEntradas(unittest.TestCase):

    def test_cruzamento_de_alta(self):
        sinal = detectar_cruzamento(
            ema6=101,
            ema20=100,
            macd_histograma=0.5,
            preco=102,
            vwap=101,
            ema6_anterior=99,
            ema20_anterior=100,
        )
        self.assertEqual(sinal, "COMPRA")

    def test_cruzamento_de_baixa(self):
        sinal = detectar_cruzamento(
            ema6=99,
            ema20=100,
            macd_histograma=-0.5,
            preco=98,
            vwap=99,
            ema6_anterior=101,
            ema20_anterior=100,
        )
        self.assertEqual(sinal, "VENDA")

    def test_cruzamento_bloqueado_na_consolidacao(self):
        sinal = detectar_cruzamento(
            ema6=101,
            ema20=100,
            macd_histograma=0.5,
            preco=102,
            vwap=101,
            ema6_anterior=99,
            ema20_anterior=100,
            consolidacao=True,
        )
        self.assertIsNone(sinal)

    def test_cruzamento_sem_confirmacao_macd(self):
        sinal = detectar_cruzamento(
            ema6=101,
            ema20=100,
            macd_histograma=-0.1,
            preco=102,
            vwap=101,
            ema6_anterior=99,
            ema20_anterior=100,
        )
        self.assertIsNone(sinal)

    def test_pullback_de_alta(self):
        candles = [
            {"open": 99.0, "high": 100.0, "low": 98.8, "close": 99.5},
            {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.2},
            {"open": 100.1, "high": 101.5, "low": 99.9, "close": 101.2},
        ]

        sinal = detectar_pullback(
            candles=candles,
            ema6=100.5,
            ema20=100.0,
            macd_histograma=0.3,
            vwap=100.0,
            atr=2.0,
        )
        self.assertEqual(sinal, "COMPRA")

    def test_pullback_bloqueado_na_consolidacao(self):
        candles = [
            {"open": 99.0, "high": 100.0, "low": 98.8, "close": 99.5},
            {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.2},
            {"open": 100.1, "high": 101.5, "low": 99.9, "close": 101.2},
        ]

        sinal = detectar_pullback(
            candles=candles,
            ema6=100.5,
            ema20=100.0,
            macd_histograma=0.3,
            vwap=100.0,
            consolidacao=True,
            atr=2.0,
        )
        self.assertIsNone(sinal)


if __name__ == "__main__":
    unittest.main(verbosity=2)
