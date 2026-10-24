import json
import tempfile
import unittest
from pathlib import Path

from estado_persistente import (
    carregar_estado,
    salvar_estado,
    evento_ja_processado,
    registrar_evento,
    registrar_sinal,
)


class TestarEstadoPersistente(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.arquivo = Path(self.tempdir.name) / "estado.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_estado_inicial(self):
        from estado_persistente import estado_inicial

        estado = estado_inicial()

        self.assertEqual(estado["versao"], 1)
        self.assertEqual(estado["eventos_processados"], [])
        self.assertFalse(estado["bot_ligado"])

    def test_salvar_e_carregar(self):
        estado = carregar_estado(self.arquivo)
        estado["bot_ligado"] = True

        salvar_estado(estado, self.arquivo)
        recuperado = carregar_estado(self.arquivo)

        self.assertTrue(recuperado["bot_ligado"])

    def test_evento_novo_e_registrado(self):
        estado = carregar_estado(self.arquivo)

        resultado = registrar_evento("XAUUSD-M5-001", estado)

        self.assertTrue(resultado)
        self.assertTrue(
            evento_ja_processado("XAUUSD-M5-001", estado)
        )

    def test_evento_duplicado_nao_e_registrado(self):
        estado = carregar_estado(self.arquivo)

        primeira = registrar_evento("XAUUSD-M5-002", estado)
        segunda = registrar_evento("XAUUSD-M5-002", estado)

        self.assertTrue(primeira)
        self.assertFalse(segunda)
        self.assertEqual(len(estado["eventos_processados"]), 1)

    def test_sinal_fica_registrado(self):
        estado = carregar_estado(self.arquivo)

        registrar_sinal(
            {"ativo": "XAUUSD", "direcao": "COMPRA"},
            estado
        )

        self.assertEqual(
            estado["ultimo_sinal"]["ativo"],
            "XAUUSD"
        )
        self.assertIn(
            "registrado_em",
            estado["ultimo_sinal"]
        )

    def test_json_corrompido_gera_erro(self):
        self.arquivo.write_text("{json inválido", encoding="utf-8")

        with self.assertRaises(RuntimeError):
            carregar_estado(self.arquivo)

    def test_chave_vazia_e_rejeitada(self):
        estado = carregar_estado(self.arquivo)

        with self.assertRaises(ValueError):
            registrar_evento("", estado)


if __name__ == "__main__":
    unittest.main(verbosity=2)
