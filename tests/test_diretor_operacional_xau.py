import unittest

from diretor_operacional_xau import calcular_decisao
from leitor_central_autonomo_xau import calcular_regiao


class TestDiretorOperacional(unittest.TestCase):

    def operacao_compra(self):
        return {
            "direcao": "COMPRA",
            "entrada_real": 4000.0,
            "stop_original": 3990.0,
            "stop": 3990.0,
            "alvo": 4015.0,
            "sequencia_reversao": 0
        }

    def test_regiao_estavel(self):
        resultado = calcular_regiao(4090.0)

        self.assertEqual(resultado["regiao"], 2726)
        self.assertEqual(
            resultado["limite_inferior"],
            4089.0
        )
        self.assertEqual(
            resultado["limite_superior"],
            4090.5
        )

    def test_stop_vai_para_entrada_ao_atingir_1r(self):
        operacao = self.operacao_compra()

        decisao = calcular_decisao(
            operacao=operacao,
            vela={"close": 4010.0},
            ema9=4008.0,
            ema21=4005.0,
            valor_rsi=60.0,
            atr=2.0
        )

        self.assertEqual(
            decisao["acao"],
            "MUDA_STOP"
        )

        self.assertEqual(
            decisao["novo_stop"],
            4000.0
        )

    def test_stop_nao_pode_afrouxar(self):
        operacao = self.operacao_compra()
        operacao["stop"] = 4002.0

        decisao = calcular_decisao(
            operacao=operacao,
            vela={"close": 4005.0},
            ema9=4004.0,
            ema21=4002.0,
            valor_rsi=55.0,
            atr=2.0
        )

        self.assertGreaterEqual(
            decisao["novo_stop"],
            4002.0
        )

    def test_continua_quando_nao_ha_gatilho(self):
        operacao = self.operacao_compra()

        decisao = calcular_decisao(
            operacao=operacao,
            vela={"close": 4003.0},
            ema9=4002.0,
            ema21=4001.0,
            valor_rsi=55.0,
            atr=2.0
        )

        self.assertEqual(
            decisao["acao"],
            "CONTINUA"
        )


if __name__ == "__main__":
    unittest.main()
