from pathlib import Path
p=Path('projects/midterm_rag_20261104'); old={'#08172B':'#FAFAF8','#122D47':'#EEF2F4','#F3F8FF':'#162B3B','#E8F4FA':'#162B3B','#21C9C3':'#2F6675','#75A9FF':'#4B6C7A','#A9C0CF':'#607884','#36536B':'#D8E0E2'}
for f in (p/'svg_output').glob('*.svg'):
 s=f.read_text(encoding='utf-8')
 for a,b in old.items():s=s.replace(a,b)
 if f.stem not in ('01','03','07','08'):
  s=s.replace('fill="#EEF2F4"','fill="#FAFAF8"')
 f.write_text(s,encoding='utf-8')
for name in ('design_spec.md','spec_lock.md'):
 f=p/name;s=f.read_text(encoding='utf-8')
 for a,b in old.items():s=s.replace(a,b)
 s=s.replace('dark-tech','swiss-minimal').replace('深蓝科技风，信息清晰','浅色简洁课程汇报，信息清晰').replace('深蓝底、青色强调和页码','浅色背景、墨蓝文字和页码').replace('学术技术汇报','简洁课堂汇报')
 f.write_text(s,encoding='utf-8')
