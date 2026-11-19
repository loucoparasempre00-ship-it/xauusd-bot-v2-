# Módulo Gestor XAUUSD

Módulo interno responsável pela conversa com o usuário e pelo acompanhamento consultivo das operações.

## Fronteiras

- O Gestor será independente do Gemini Sinal da Central.
- Não gera nem altera sinais da Central.
- Não envia ordens à corretora; recomendações dependem de confirmação manual pelo Telegram.
- Consome os dados oficiais do projeto, sem criar cópias ou bancos espelhados.
- A integração com o banco oficial `data/xauusd.db`, o Testador/Seletor Gemini e o Telegram será feita nas próximas etapas.
- Memória conversacional e decisões do Gestor permanecem separadas das do Gemini Sinal.

## Organização prevista

- `gemini_gestor.js`: inteligência e instruções próprias do Gestor.
- `conversador.js`: fluxo de mensagens e respostas.
- `acompanhamento.js`: avaliação consultiva de operações abertas.
- `memoria.js`: memória conversacional independente.
- `mercado.js`: leitura e organização do contexto oficial de mercado.
- `validadores.js`: validação de mensagens, sinais e contexto recebido.
- `telegram.js`: comunicação com o Telegram.

Os arquivos serão incorporados com suas dependências verificadas; não serão criados bancos, espelhos de dados ou workflows de sincronização paralelos nesta pasta.

## Estado

Estrutura inicial criada. O módulo ainda não está conectado à Central, ao Worker ou ao Telegram.
