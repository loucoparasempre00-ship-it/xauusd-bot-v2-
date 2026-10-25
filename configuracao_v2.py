"""
Configuracao central do projeto XAUUSD V2.
Nao armazena tokens, senhas ou chaves de API.
"""

TIMEFRAME = "M5"
ATIVO = "XAUUSD"

# Indicadores técnicos
EMA_RAPIDA = 6
EMA_LENTA = 20
MACD_RAPIDO = 12
MACD_LENTO = 26
MACD_SINAL = 9
ATR_PERIODO = 14

# Gestão de risco
MULTIPLICADOR_STOP_ATR = 1.5
RISCO_RETORNO = 1.5

# Estrutura de mercado
LADO_PIVO = 2
TOLERANCIA_ZONA = 1.0

# Validação dos dados
MINIMO_CANDLES_INTEGRACAO = 35
MAXIMO_ATRASO_CANDLE_MINUTOS = 12

# Segurança e publicação
ENVIAR_TELEGRAM = False
EXECUTAR_ORDENS = False

# O decisor deve aprovar explicitamente antes da publicação.
EXIGIR_APROVACAO_FINAL = True
