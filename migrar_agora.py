import os
import shutil
from mapa_dados import PONTE_ARQUIVOS, preparar_pastas

preparar_pastas()

for nome_antigo, novo_caminho in PONTE_ARQUIVOS.items():
    caminho_antigo = os.path.join("data", nome_antigo)
    if os.path.exists(caminho_antigo) and not os.path.exists(novo_caminho):
        shutil.move(caminho_antigo, novo_caminho)
        print(f"Movido: {nome_antigo} -> {novo_caminho}")
