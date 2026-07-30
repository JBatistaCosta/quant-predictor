import re
with open(r'scripts\dados_historicos.py', 'r', encoding='utf-8', errors='replace') as f:
    text = f.read()

# Check FEATURES_NUMERICAS_V3B and others
for pattern_str, name in [
    (r'FEATURES_NUMERICAS_V3B = .*?\]\nFEATURES_V3B', '--- V3B (titular) ---'),
    (r'COLUNAS_FORMA_EXTRA_POR_RAW = \{.*?\}', '--- COLUNAS_FORMA_EXTRA ---'),
    (r'COLUNAS_SITUACAO_CHUTES\s*=.*?\]', '--- COLUNAS_SITUACAO_CHUTES ---'),
]:
    m = re.search(pattern_str, text, re.DOTALL)
    if m:
        print(name)
        print(m.group()[:600])
        print()
