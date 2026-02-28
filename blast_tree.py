import os
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in {'__pycache__','.git','venv'}]
    level = root.replace('.', '').count(os.sep)
    indent = '  ' * level
    print(f"{indent}{os.path.basename(root)}/")
    for f in sorted(files):
        if f.endswith('.py'):
            print(f"{indent}  {f}")