import easyocr
import cv2
import os
import numpy as np
import re
from collections import Counter

# Inicializar o leitor de OCR
reader = easyocr.Reader(['pt', 'en'], gpu=False)

class GerenciadorGabarito:
    """Gere o gabarito dinâmico avaliando uma sequência de itens antes de fixar o padrão."""
    def __init__(self, tamanho_amostra=3):
        self.tamanho_amostra = tamanho_amostra
        self.historico_amostras = []
        self.gabarito_ativo = None

    def avaliar_sequencia(self, textos_detectados):
        """Acumula amostras até atingir o tamanho necessário para definir o gabarito."""
        if self.gabarito_ativo:
            return True 

        assinatura_estrutura = len(textos_detectados)
        self.historico_amostras.append(assinatura_estrutura)
        
        print(f"📊 [GABARITO] Amostra recolhida ({len(self.historico_amostras)}/{self.tamanho_amostra}). Assinatura: {assinatura_estrutura} blocos.")

        if len(self.historico_amostras) >= self.tamanho_amostra:
            contagem = Counter(self.historico_amostras)
            assinatura_mais_comum, frequencia = contagem.most_common(1)[0]
            
            if frequencia >= (self.tamanho_amostra / 2):
                self.gabarito_ativo = {
                    "assinatura_padrao": assinatura_mais_comum,
                    "status": "Definido por Sequência"
                }
                print(f"\n💡 [GABARITO] Padrão validado e fixado após avaliar {self.tamanho_amostra} itens! Assinatura: {assinatura_mais_comum} blocos.")
            else:
                print("\n⚠️ [GABARITO] Instabilidade detetada na sequência. A reiniciar amostras...")
                self.historico_amostras = []

        return self.gabarito_ativo is not None

# Instância do gestor de gabarito
gabarito_lote = GerenciadorGabarito(tamanho_amostra=3)

def avaliar_contraste_imagem(caminho_imagem):
    """Calcula a nitidez usando a variância do Laplaciano."""
    imagem = cv2.imread(caminho_imagem, cv2.IMREAD_GRAYSCALE)
    if imagem is None:
        return False
    return cv2.Laplacian(imagem, cv2.CV_64F).var() < 100.0

def extrair_dados_cartucho(textos_detectados):
    """Extrai Lote, Fabricação e Validade com base em expressões regulares e contexto."""
    lote = None
    fabricacao = None
    validade = None
    
    padrao_data = r'(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)[\/\s]*\d{2,4}|\d{2}/\d{2}/\d{4}'
    padrao_lote = r'^\d{5,8}$'
    
    datas_encontradas = []

    for i, t in enumerate(textos_detectados):
        rotulo_anterior = textos_detectados[i-1].upper() if i > 0 else ""
        
        if re.match(padrao_lote, t) and not lote:
            lote = t
            
        m_data = re.search(padrao_data, t, re.IGNORECASE)
        if m_data:
            data_str = m_data.group(0).replace(" ", "")
            if rotulo_anterior in ['F', 'FAB', 'FABRICAÇÃO', 'FABRICACAO', 'DATA']:
                fabricacao = data_str
            else:
                datas_encontradas.append(data_str)

    for d in datas_encontradas:
        if not validade and d != fabricacao:
            validade = d
        elif not fabricacao and d != validade:
            fabricacao = d

    return lote, fabricacao, validade

def validar_e_classificar_erros(resultados_ocr, caminho_imagem, lote, fabricacao, validade):
    """Atribui códigos de erro industriais, ignorando falsos positivos em rótulos isolados."""
    erros = []
    
    # 1. E-LC-01: Baixo Contraste
    if avaliar_contraste_imagem(caminho_imagem):
        erros.append({
            "codigo": "E-LC-01", 
            "descricao": "Baixo Contraste ou nitidez insuficiente na imagem da etiqueta."
        })
        
    # 2. E-MF-02: Campos em Falta
    if not lote or not fabricacao or not validade:
        erros.append({
            "codigo": "E-MF-02", 
            "descricao": "Campo obrigatório em falta (Lote, Fabricação ou Validade não detetados)."
        })
        
    # 3. E-DC-03: Baixa Confiança da IA (Ignora letras de rótulo isoladas como 'F', 'V', 'L')
    rotulos_ignorados = ['F', 'V', 'L', 'FAB', 'VAL']
    
    for (bbox, text, prob) in resultados_ocr:
        texto_limpo = text.strip().upper()
        if texto_limpo:
            if texto_limpo in rotulos_ignorados:
                continue # Ignora letras curtas/rótulos para evitar falsos alarmes
                
            if 0.0 < prob < 0.60:
                erros.append({
                    "codigo": "E-DC-03", 
                    "descricao": f"Baixa Confiança da IA no texto '{text}' (Confiança: {prob:.2f})."
                })
                break

    return erros

def processar_etiqueta_com_gabarito(caminho_imagem):
    """Pipeline completo de inspeção para um item."""
    if not os.path.exists(caminho_imagem):
        print(f"Erro: A imagem {caminho_imagem} não foi encontrada.")
        return None

    resultados = reader.readtext(caminho_imagem)
    textos_validos = [text.strip() for (_, text, prob) in resultados if text.strip() and prob > 0.0]
    
    print(f"\n--- Processando: {caminho_imagem} ---")

    lote, fabricacao, validade = extrair_dados_cartucho(textos_validos)
    lista_erros = validar_e_classificar_erros(resultados, caminho_imagem, lote, fabricacao, validade)
    
    if not lista_erros and not gabarito_lote.gabarito_ativo:
        gabarito_lote.avaliar_sequencia(textos_validos)

    print(f"Lote       : {lote or 'Não identificado'}")
    print(f"Fabricação : {fabricacao or 'Não identificada'}")
    print(f"Validade   : {validade or 'Não identificada'}")
    
    if lista_erros:
        print("STATUS: ❌ REPROVADO")
        for e in lista_erros:
            print(f" -> [{e['codigo']}] {e['descricao']}")
    else:
        print("STATUS: ✔️ APROVADO")

if __name__ == "__main__":
    # Podes testar com os teus recortes reais da linha
    itens_lote = [
        "saida/recortes/item001_1.png",
        "saida/recortes/item002_1.png",
        "saida/recortes/item003_1.png"
    ]
    
    for item in itens_lote:
        processar_etiqueta_com_gabarito(item)