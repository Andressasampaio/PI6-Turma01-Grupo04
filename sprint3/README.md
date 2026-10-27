# IVIS - Sprint 3: captura e individualização

Projeto Integrador VI (PUC-Campinas) - IVIS: sistema de visão computacional para
inspeção de cartuchos farmacêuticos.

Esta pasta contém a parte da Sprint 3: ler as imagens da esteira, separar cada
cartucho e testar o detector YOLO que acha a etiqueta (lote, validade, fabricação).

## Arquivos

| Arquivo | O que faz |
|---|---|
| `captura.py` | Lê imagens de uma pasta (simula a esteira) ou da câmera e usa o YOLO para achar a etiqueta em cada item. |
| `individualizacao.py` | Com OpenCV (sem IA), acha cada cartucho na foto, recorta e salva em `saida/recortes/`, e gera `saida/resultados.json` para a interface. As imagens `saida/debug/` mostram os retângulos desenhados. |
| `webcam_teste.py` | Teste ao vivo com a webcam: mostra o retângulo na etiqueta detectada. |
| `ivis_teste_colab.ipynb` | Teste no Google Colab: sobe fotos (ou tira uma foto com a webcam) e vê a detecção. |
| `best.pt` | Pesos do YOLO11n treinado (classe `medicine box`). |

## Como rodar

```
pip install -r requirements.txt

python individualizacao.py pasta_de_imagens/
python captura.py best.pt pasta_de_imagens/
python webcam_teste.py best.pt
```

Depois de rodar o `individualizacao.py`, abra as imagens em `saida/debug/` para
conferir se os retângulos estão certos. Se pegar ruído ou perder cartuchos, ajuste
`AREA_MIN` e o tamanho do kernel em `preprocessar()`.

## Sobre o modelo

- YOLO11n treinado no Colab com o dataset do Roboflow (projeto "PI VI - IVIS"),
  versão v2: 648 imagens de treino (com aumento de dados), 62 de validação e 30 de teste.
- As imagens foram esticadas para 512x512, por isso os scripts fazem o mesmo antes de detectar.
- Resultado na validação (62 imagens): precisão 0,999, revocação 1,0, mAP50 0,995.
  O resultado no conjunto de teste ainda precisa ser registrado.

## Limitações conhecidas

- **Status dos testes:** `captura.py` foi testado com fotos reais e o YOLO recortou a etiqueta com o texto legível. `individualizacao.py` só funciona com fundo uniforme: em fotos com fundo bagunçado (mão, parede) o retângulo pegou a foto inteira. Próximo passo: melhorar a separação ou usar o YOLO para individualizar.
- O dataset tem fotos muito parecidas (mesmo fundo azul, mesma etiqueta). Em foto de
  webcam com outra luz a confiança foi menor (0,64 em um teste) e o texto ficou borrado.
- A separação dos cartuchos com OpenCV assume fundo uniforme; numa esteira real
  vai precisar de ajuste.
- O OCR (leitura de lote e validade) ainda não está nesta pasta.
