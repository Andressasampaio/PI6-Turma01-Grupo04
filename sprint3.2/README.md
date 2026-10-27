# IVIS - Re-treino do detector de etiqueta (YOLO11)

Nesta pasta está o notebook que treina (e re-treina) o detector YOLO11 que acha, na foto do
cartucho, a etiqueta com lote, validade e fabricação, e os dois modelos gerados por ele.

O primeiro modelo (v2) falhava em fotos com mão segurando a caixa. Por isso o dataset no
Roboflow foi aumentado com mais fotos com mão (versão v3) e o modelo foi treinado de novo.

## Arquivos

| Arquivo | O que é |
|---|---|
| `ivis_retreino_colab.ipynb` | Notebook do Google Colab que baixa o dataset do Roboflow, treina o YOLO11n, mede no conjunto de teste, compara o modelo antigo com o novo e testa com fotos suas. |
| `best_v3.pt` | Modelo atual, treinado com o dataset que inclui mais fotos com mão. |

## Como usar o notebook

1. No Colab: **Arquivo -> Fazer upload de notebook** e escolha `ivis_retreino_colab.ipynb`.
2. Ative a GPU: **Ambiente de execução -> Alterar o tipo de ambiente de execução -> T4 GPU**.
3. Guarde a chave do Roboflow nos **Secrets** do Colab (ícone de chave, à esquerda) com o nome
   `ROBOFLOW_API_KEY` e ative "Acesso ao notebook". **Nunca escreva a chave no código.**
4. Na célula do dataset, troque `VERSAO` pelo número da versão do dataset no Roboflow.
5. Rode as células de cima para baixo (cada célula usa o que as anteriores criaram).

O que cada parte faz, na ordem:

1. Instala as bibliotecas (`ultralytics`, `roboflow`).
2. Baixa a versão do dataset no formato YOLOv11.
3. Treina o YOLO11n (50 épocas, `imgsz=512`, `batch=16`).
4. Mede no conjunto de **teste**: precisão, revocação, mAP50 e mAP50-95.
5. Mostra as detecções em imagens do teste.
6. Permite subir fotos suas para testar o modelo novo.
7. Compara o modelo antigo com o novo no mesmo conjunto de teste e imprime uma tabela pronta.
8. Testa o antigo e o novo em fotos com mão e conta em quantas cada um achou alguma etiqueta.
9. Mostra as caixas dos dois modelos em cada foto (vermelho = antigo, verde = novo), para
   conferir visualmente se a caixa está em cima da etiqueta.
10. Baixa o modelo novo como `best_vN.pt`.

## Dados e treino

- Dataset do Roboflow ("PI VI - IVIS"), uma única classe: `medicine box` (a etiqueta).
- As imagens são esticadas para 512x512 (por isso o `imgsz=512`); o aumento de dados é aplicado
  só ao conjunto de treino.
- O conjunto de teste usado na comparação tem 30 imagens reais.

## Resultados

Conjunto de teste (30 imagens, as mesmas para os dois modelos):

| Modelo | Precisão | Revocação | mAP50 | mAP50-95 |
|---|---|---|---|---|
| v2 (antigo) | 0,998 | 1,000 | 0,995 | 0,890 |
| v3 (novo) | 0,998 | 1,000 | 0,995 | 0,912 |

Os dois modelos acharam todas as etiquetas do teste; a diferença está só no mAP50-95 (caixa
mais justa no v3). Como o conjunto de teste é pequeno, esses números sozinhos não separam bem
os modelos. Por isso também foi feito um teste adicional, conferido visualmente, com fotos com
mão de fora do dataset (passos 8 e 9 do notebook): nele o v3 localizou a etiqueta em mais
fotos do que o v2.

## Limitações

- O conjunto de teste é pequeno (30 imagens), então diferenças pequenas nas métricas podem
  ser acaso.
- O teste com fotos com mão foi visual e com poucas fotos.
- O dataset tem fotos parecidas entre si (mesmo tipo de etiqueta e de fundo); o desempenho em
  outras condições de luz e de fundo ainda precisa ser avaliado.
