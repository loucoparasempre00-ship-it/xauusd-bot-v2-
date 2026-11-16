
def transmitir_tabela(
    conexao,
    url,
    token,
    ciclo_id,
    banco,
    tabela
):
    identificador = nome_seguro(tabela)

    colunas = [
        linha["name"]
        for linha in conexao.execute(
            f"PRAGMA table_info({identificador})"
        ).fetchall()
    ]

    total = conexao.execute(
        f"SELECT COUNT(*) FROM {identificador}"
    ).fetchone()[0]

    proximo_lote = [0]

    if tabela == "candles" and "timeframe" in colunas:
        for timeframe in ("M5", "M15", "H1"):
            cursor = conexao.execute(
                f"""
                SELECT *
                FROM {identificador}
                WHERE timeframe = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (timeframe, LIMITE_CANDLES_POR_TF)
            )

            linhas = cursor.fetchall()

            registros = [
                normalizar_registro(linha)
                for linha in reversed(linhas)
            ]

            extras = {
                "timeframe": timeframe,
                "registros_enviados": len(registros)
            }

            transmitir_registros(
                url,
                token,
                ciclo_id,
                banco,
                tabela,
                colunas,
                total,
                iter(registros),
                proximo_lote,
                extras
            )

            print(
                f"{banco}.{tabela}.{timeframe}: "
                f"{len(registros)} velas processadas."
            )

        return

    # arquivos_origem: transmitir somente metadados.
    # O BLOB conteudo não será enviado.
    if tabela == "arquivos_origem":
        colunas_envio = [
            coluna for coluna in (
                "caminho",
                "nome",
                "hash_sha256",
                "tamanho_bytes",
                "data_importacao"
            )
            if coluna in colunas
        ]
    else:
        colunas_envio = colunas

    selecao = ", ".join(
        nome_seguro(coluna)
        for coluna in colunas_envio
    )

    cursor = conexao.execute(
        f"SELECT {selecao} FROM {identificador}"
    )

    def gerar_registros():
        while True:
            linhas = cursor.fetchmany(200)

            if not linhas:
                break

            for linha in linhas:
                yield normalizar_registro(linha)

    transmitir_registros(
        url,
        token,
        ciclo_id,
        banco,
        tabela,
        colunas_envio,
        total,
        gerar_registros(),
        proximo_lote
    )

    print(
        f"{banco}.{tabela}: "
        f"{total} registros processados."
    )
