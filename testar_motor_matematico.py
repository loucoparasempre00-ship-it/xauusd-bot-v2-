import unittest

from motor_matematico import (
    calcular_ema,
    calcular_macd,
    calcular_vwap_ancorada,
    detectar_consolidacao,
    calcular_niveis_risco,
)


class TestarMotorMatematico(unittest.TestCase):

    def test_ema_retorna_valor_numerico(self):
        valores = [100, 101, 102, 103, 104, 105]

        resultado = calcular_ema(valores, periodo=3)

        self.assertIsInstance(resultado, (int, float))
        self.assertAlmostEqual(resultado, 104.03125, places=4)

    def test_macd_retorna_linha_sinal_e_histograma(self):
        valores = [100 + i for i in range(40)]

        resultado = calcular_macd(
            valores,
            periodo_rapido=12,
            periodo_lento=26,
            periodo_sinal=9,
        )

        self.assertIn("linha", resultado)
        self.assertIn("sinal", resultado)
        self.assertIn("histograma", resultado)

        self.assertIsInstance(resultado["linha"], (int, float))
        self.assertIsInstance(resultado["sinal"], (int, float))
        self.assertIsInstance(resultado["histograma"], (int, float))

    def test_vwap_ancorada_exige_volume_positivo(self):
        candles = [
            {
                "timestamp": "2026-10-22T13:30:00+00:00",
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 100,
            },
            {
                "timestamp": "2026-10-22T13:35:00+00:00",
                "high": 103,
                "low": 101,
                "close": 102,
                "volume": 300,
            },
        ]

        resultado = calcular_vwap_ancorada(
            candles,
            abertura_nova_york="2026-10-22T09:30:00",
        )

        self.assertIsInstance(resultado, (int, float))
        self.assertGreater(resultado, 0)

    def test_filtro_identifica_consolidacao(self):
        candles = []

        for i in range(30):
            preco = 2000 + (0.05 if i % 2 == 0 else -0.05)
            candles.append({
                "open": preco,
                "high": preco + 0.08,
                "low": preco - 0.08,
                "close": preco,
                "volume": 100,
            })

        resultado = detectar_consolidacao(candles)

        self.assertIsInstance(resultado, bool)
        self.assertTrue(resultado)

    def test_niveis_de_risco_compra(self):
        resultado = calcular_niveis_risco(
            direcao="COMPRA",
            preco_entrada=4000.0,
            atr=10.0,
            stop_estrutura=3985.0,
            multiplo_atr=1.5,
        )

        self.assertLess(resultado["stop"], 4000.0)
        self.assertGreater(resultado["alvo_1_5R"], 4000.0)
        self.assertGreater(resultado["alvo_2R"], resultado["alvo_1_5R"])

        risco = 4000.0 - resultado["stop"]

        self.assertAlmostEqual(
            resultado["alvo_1_5R"] - 4000.0,
            risco * 1.5,
            places=5,
        )

        self.assertAlmostEqual(
            resultado["alvo_2R"] - 4000.0,
            risco * 2.0,
            places=5,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
