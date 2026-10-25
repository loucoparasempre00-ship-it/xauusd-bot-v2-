# XAUUSD Bot V2

Sistema experimental de análise e geração de sinais para XAUUSD
no timeframe M5.

## Objetivo

Construir uma arquitetura modular para análise de mercado,
validação de sinais, gestão de risco e acompanhamento de resultados.

O projeto é informativo e não deve executar ordens na corretora.

## Regras do projeto

- Analisar candles M5.
- Utilizar candles fechados para decisões.
- Evitar sinais duplicados.
- Calcular stop e alvo com base no risco.
- Considerar uma relação risco-retorno planejada de 1:1,5.
- Registrar decisões e resultados para auditoria.
- Manter envio de mensagens separado da análise.
- Não armazenar chaves de API no código.
- Não executar operações na corretora.

## Módulos principais

### Dados e análise
- `coletar_candles.py`
- `filtro_candles_fechados.py`
- `monitor_saude.py`
- `leitura_mercado.py`
- `integrar_leitura.py`
- `padroes_estrutura.py`
- `motor_matematico.py`
- `validador_mercado.py`

### Candidatos e decisões
- `gerador_candidatos.py`
- `validador_sinais_ia.py`
- `agente_macro.py`
- `calendario_economico.py`
- `decisor_final.py`

### Risco e acompanhamento
- `gestao_sinais.py`
- `acompanhamento_resultados.py`
- `controle_duplicidade.py`
- `registro_sinais.py`
- `auditoria_sinais.py`
- `metricas_desempenho.py`
- `relatorio_v2.py`

### Persistência e comunicação
- `persistencia_v2.py`
- `estado_persistente.py`
- `formatador_mensagem.py`
- `notificador_telegram.py`
- `regras_seguranca.py`

### Coordenação
- `configuracao_v2.py`
- `principal_v2.py`
- `orquestrador_v2.py`

## Backtest e testes existentes

O repositório também contém arquivos de testes e backtest.
Eles devem ser revisados antes de considerar a V2 validada.

## Estado atual

**Etapa: criação da estrutura preliminar.**

A existência dos arquivos não significa que os módulos estejam
integrados, testados ou prontos para gerar sinais reais.

Ainda é necessário:

1. Conferir a compatibilidade entre as interfaces dos módulos.
2. Integrar o fluxo de análise e decisão.
3. Garantir que apenas candles fechados sejam usados.
4. Definir persistência adequada entre execuções do GitHub Actions.
5. Verificar controle de duplicidade e auditoria.
6. Validar cálculos de risco e resultados.
7. Executar os testes e corrigir os erros encontrados.
8. Comparar os sinais com os dados da plataforma utilizada.
9. Manter o envio ao Telegram desativado até concluir as verificações.

## Segurança

As chaves de API e o token do Telegram devem ser configurados
como segredos do ambiente de execução, nunca incluídos no código.

O projeto não deve receber permissões de negociação da corretora.

## Aviso

O sistema é experimental. Resultados de backtests não garantem
desempenho futuro. Spread, slippage, custos e qualidade dos dados
podem alterar significativamente os resultados.
