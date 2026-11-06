import ast
from pathlib import Path
p=Path('projects/midterm_rag_20261104');tree=ast.parse((p/'build_midterm.py').read_text(encoding='utf-8-sig'));a=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='slides' for x in n.targets))
(p/'notes'/'total.md').write_text('\n\n---\n\n'.join(f'# {i:02}_{x[0]}\n\n{x[3]}' for i,x in enumerate(a,1)),encoding='utf-8')
