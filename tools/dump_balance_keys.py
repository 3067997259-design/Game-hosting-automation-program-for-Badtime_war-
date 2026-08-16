import re, io

files = ['engine/m9/talents/t6.py', 'engine/m9/talents/g4.py',
         'engine/m9/talents/g6.py', 'engine/m9/talents/t4.py',
         'engine/m9/talents/g2.py', 'engine/m9/talents/g3.py',
         'engine/m9/talents/g7.py', 'engine/m9/talents/t7.py',
         'engine/m9/scoring.py']
for f in files:
    print(f"===== {f} =====")
    try:
        src = io.open(f, encoding='utf-8').read()
    except OSError:
        print("  (missing)")
        continue
    for m in re.finditer(
            r'bget\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]'
            r'\s*,\s*(?:default\s*=\s*)?([^,\)]+)\)', src):
        print(f"  bget {m.group(1)}.{m.group(2)} = {m.group(3).strip()}")
    for m in re.finditer(
            r'_(t|g)\d\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*([^\)]+)\)', src):
        print(f"  helper_{m.group(1)} {m.group(2)} = {m.group(3).strip()}")
