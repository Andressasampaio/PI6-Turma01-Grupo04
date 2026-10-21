import easyocr
import cv2
import os
import re

# 1. Inicializar o leitor de OCR (suporta português/inglês)
reader = easyocr.Reader(['pt', 'en'], gpu=False)

def extrair_dados_cartucho(textos):
    """Aplica expressões regulares (Regex) para encontrar Lote e Validade."""
    lote = None
    validade = None
    
    # Junta todos os textos detetados numa única string para facilitar a busca global
    texto_completo = " ".join(textos)
    
    # Padrão para validade (ex: JUL/2027, 07/2027, etc.)
    padrao_validade = r'(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)[\/\s]*\d{2,4}|\d{2}/\d{2}/\d{4}'
    match_val = re.search(padrao_validade, texto_completo, re.IGNORECASE)
    if match_val:
        validade = match_val.group(0)
        
    # Padrão para lote (procura sequências numéricas típicas de lote, ex: 5 a 8 dígitos)
    for t in textos:
        t_clean = t.strip()
        # Se encontrar um bloco apenas com números entre 5 e 8 dígitos
        if re.match(r'^\d{5,8}$', t_clean):
            lote = t_clean
            break
            
    return lote, validade

def ler_etiqueta(caminho_imagem):
    """Lê o texto de uma imagem recortada de um cartucho e extrai lote/validade."""
    if not os.path.exists(caminho_imagem):
        print(f"Erro: A imagem {caminho_imagem} não foi encontrada.")
        return None

    # Executar o OCR
    resultados = reader.readtext(caminho_imagem)
    
    print(f"\n--- Resultados para: {caminho_imagem} ---")
    texto_detectado = []
    
    for (bbox, text, prob) in resultados:
        print(f"Texto detetado: {text} (Confiança: {prob:.2f})")
        texto_detectado.append(text)
        
    # Processar os dados com Regex
    lote, validade = extrair_dados_cartucho(texto_detectado)
    
    print("\nDADOS EXTRAÍDOS")
    print(f"Lote: {lote if lote else 'Não identificado'}")
    print(f"Validade: {validade if validade else 'Não identificada'}")
    
    return {"lote": lote, "validade": validade, "textos": texto_detectado}

# Exemplo de teste
if __name__ == "__main__":
    imagem_teste = "saida/recortes/item001_1.png"
    ler_etiqueta(imagem_teste)