# Implementar Calibração Obrigatória no Pipeline de Treino Custom

## Contexto

Leia `CONTEXTO_PROJETO.md` inteiro antes de começar.

Este sistema (quant-predictor) treina modelos ML para previsão de odds justas em futebol. O pipeline atual **não inclui calibração de probabilidades como etapa obrigatória**, o que é um problema estrutural: todos os modelos tree-based têm coeficiente Platt `a` entre 0.34–0.45 (documentado no CONTEXTO), confirmando overconfidence sistemático. Sem calibração, a odd justa calculada como `1/p` está errada antes mesmo de chegar ao usuário.

## Diagnóstico rápido do estado atual

- `model_calibration` — tabela no Supabase já existe com infraestrutura de calibração
- `api/model-maintenance.js` — tem `?tarefa=calibracao` já implementado como endpoint manual
- `scripts/modelos_ml.py` — treina os modelos mas **não executa calibração no mesmo pipeline**
- `src/pages/TreinoCustom.jsx` — interface de modelos custom; calibração não aparece no fluxo

O problema: calibração é disparada manualmente depois do treino, não como parte dele. Para modelos custom criados pelo usuário no `TreinoCustom.jsx`, a calibração nunca é executada.

## Tarefa

Integrar calibração (Platt Scaling como padrão, Isotonic Regression como opção) como **etapa automática e obrigatória** ao final de cada treino de modelo custom, com avaliação antes/depois.

### Requisitos funcionais

1. **Ao finalizar treino de modelo custom**, executar automaticamente:
   - Fit de `CalibratedClassifierCV` com `method='sigmoid'` (Platt) nos dados de calibração held-out
   - Salvar coeficientes calibrados junto ao modelo (ou na tabela `model_calibration`)
   - Registrar Brier Score pré-calibração e pós-calibração nos metadados do modelo

2. **Divisão de dados correta** — a calibração precisa de conjunto próprio, separado do treino e do teste:
   - Modo simples: 60% treino / 20% calibração / 20% teste
   - Modo Walk-Forward CV: dentro de cada fold, reservar 20% do validation set para calibração

3. **Probabilidades servidas pela API** devem sempre passar pelo calibrador antes de calcular a odd justa. Verificar todos os endpoints que consultam probabilidades do modelo e garantir que aplicam a calibração.

4. **UI (`TreinoCustom.jsx`)**: mostrar nas métricas do modelo treinado:
   - Brier Score antes da calibração
   - Brier Score depois da calibração
   - Coeficiente Platt `a` (se `a < 0.5`, alertar overconfidence severo)

### Requisitos técnicos

- Usar `sklearn.calibration.CalibratedClassifierCV` ou fit manual de regressão logística nos logits
- Para modelos com múltiplos targets (1X2 tem 3 classes), calibrar por classe (OvR) ou usar `calibrate_probas` por coluna
- Não usar Isotonic Regression por padrão — com ~8-10k amostras e split 60/20/20, o conjunto de calibração (~2k linhas) é pequeno demais; Platt (2 parâmetros) é mais estável
- Persistir calibrador junto ao modelo no Supabase (serializado em base64 ou como JSON de coeficientes)

## Arquivos-chave para modificar

| Arquivo | O que mudar |
|---------|-------------|
| `scripts/modelos_ml.py` | Adicionar etapa de calibração ao pipeline de treino; ajustar split de dados |
| `api/treinar-modelo.js` (ou equivalente que chama o script Python) | Passar flag de calibração; salvar resultado no Supabase |
| `src/pages/TreinoCustom.jsx` | Exibir métricas de calibração na visualização de modelo treinado |
| `model_calibration` (tabela Supabase) | Verificar schema; adicionar coluna para coeficiente Platt `a` se não existir |

## Validação

Após implementar, verificar:

1. Treinar um modelo 1X2 no `TreinoCustom.jsx` e confirmar que métricas pré/pós calibração aparecem
2. Consultar `model_calibration` no Supabase e ver coeficientes salvos
3. Checar que probabilidades expostas pela API para esse modelo passam pelo calibrador

## Restrições do projeto

- Limite de 12 serverless functions no Vercel Hobby — não criar novo arquivo em `api/`; usar `api/model-maintenance.js` com dispatch por `?tarefa=` se precisar de endpoint novo
- Qualquer tabela com >1000 linhas precisa de paginação explícita com `.range()` (o Supabase corta silenciosamente em 1000)
- Branch de trabalho: `claude/odds-prediction-model-params-72sn8h`
- Depois de implementar: commit, push, abrir PR draft, aguardar deploy da preview
