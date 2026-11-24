"""Three-way local Prompt comparison with fixed, source-backed evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
from 复现实验 import ROOT, SOURCES, WASH, VAC, ROBOT, FRIDGE, AC, contains, unit_text, checksum

REFUSAL = '现有产品资料中暂未找到相关说明'
B_ADDITION = """2. 资料未写明的内容一律不回答，包括原因、原理、目的、效果、价格、库存、承诺。
   尤其禁止用「为了提高效率」「这样可以延长寿命」这类推断补足回答。
3. 不得以页码、章节号、页眉页脚作为依据；引用必须是资料正文中的原句或原数据。"""
C = """你是产品说明问答助手。只能依据用户消息中「资料」部分的内容回答产品事实。

【回答依据】
1. 每个事实陈述后必须标注对应的资料编号，如 [1]；编号只能取自本次提供的资料。
2. 资料未写明的内容一律不回答，包括原因、原理、目的、效果、价格、库存、承诺。
   尤其禁止用「为了提高效率」「这样可以延长寿命」这类推断补足回答。
3. 不得以页码、章节号、页眉页脚作为依据；引用必须是资料正文中的原句或原数据。
4. 数值与单位按资料原文写法输出，不改写、不换算（资料写 600L 就写 600L，不写 600 升）。

【拒答与转交】
5. 资料没有明确答案时，只输出这一句：现有产品资料中暂未找到相关说明。
   不要追加建议、不要推荐联系渠道、不要解释为什么没有。
6. 若问题属于退款、退货、投诉、保修、维修、换货、上门服务、开票等售后事项，
   直接说明需要人工处理，不尝试从资料中拼凑答案。

【表达要求】
7. 先给结论，再按需补充限制条件；总长度不超过三句。
8. 资料中并列多项时（如多个程序、多个部件），逐项列出，不概括为「等」。
9. 不使用「您好」「希望对您有帮助」等客套话，不重复用户的问题。
10. 不确定时宁可拒答，不要给出可能性描述（如「一般来说」「通常」）。"""

# Order and evidence are fixed across groups, including related passages in all no-answer items.
CASES = [
    ('p01',WASH,'额定洗涤容量是多少？',('9 kg',),('额定洗涤容量：9 kg',)),
    ('p02',WASH,'米家洗衣机的净重和毛重分别是多少？',('55 kg','59 kg'),('产品净重：55 kg','产品毛重：59 kg')),
    ('p03',FRIDGE,'BCD-601WGHSS17B8U1 的总容积是多少？',('601L',),('型号：BCD-601WGHSS17B8U1','总容积：601L'),),
    ('p04',AC,'使用遥控器应该离空调多远？',('8 米',),('遥控器与空调器 8 米范围内',)),
    ('p05',VAC,'内置滤网清洗后至少要晾晒多久？',('24 小时',),('将内置滤网晾晒至少 24 小时',)),
    ('p06',ROBOT,'这款扫拖机器人支持什么 Wi-Fi 频段？',('2.4 GHz',),('本产品仅支援连接2.4 GHz',)),
    ('p07',VAC,'说明书列了哪三种需用清水冲洗的集尘部件？',('前置过滤网','旋风系统组件','集尘桶'),('使用清水冲洗前置过滤网、旋风系统组件及集尘桶',)),
    ('p08',ROBOT,'额定功率多少？',('45 W',),('额定功率：45 W',)),
    ('n01',WASH,'为什么厂商选择把额定洗涤容量设计成 9 kg？',(),('额定洗涤容量：9 kg',)),
    ('n02',FRIDGE,'为什么厂商把这个冰箱的总容积设计成 601L？',(),('型号：BCD-601WGHSS17B8U1','总容积：601L'),),
    ('n03',ROBOT,'边刷安装后工作效率可以提高百分之多少？',(),('取出边刷，安装至图示扣位处',)),
    ('n04',AC,'遥控器在水下几米还能用？',(),('遥控器与空调器 8 米范围内',)),
    ('n05',ROBOT,'扫拖机器人内置 4K 摄像头的分辨率是多少？',(),('本产品仅支援连接2.4 GHz',)),
    ('n06',FRIDGE,'这款冰箱的建议零售价是多少？',(),('型号：BCD-601WGHSS17B8U1','总容积：601L'),),
    ('n07',VAC,'为什么内置滤网需要晾晒 24 小时而不是 23 小时？',(),('将内置滤网晾晒至少 24 小时',)),
]


def evidence(product, markers):
    lines = SOURCES[product].read_text(encoding='utf-8').splitlines()
    if product==FRIDGE:
        start=next(i for i,line in enumerate(lines) if line.strip()==markers[0])
        block='\n'.join(lines[start:start+7]).strip()
        assert all(m in block for m in markers)
        return [block]
    return [next(line.strip() for line in lines if marker in line) for marker in markers]


def row_metrics(answer, terms, documents):
    refused = REFUSAL in answer
    # Preserve actual output, including any added clauses or questionable citations.
    references = [int(i) for i in re.findall(r'\[(\d+)\]',answer)]
    citation_range_ok = bool(references and all(1<=n<=len(documents) for n in references))
    answer_ok = all(contains(unit_text(answer),unit_text(t)) for t in terms) if terms else refused
    return {'passed':bool(answer_ok and (not refused if terms else True)), 'refused':refused,
            'exact_refusal':answer.strip().rstrip('。')==REFUSAL,
            'citation_range_ok':citation_range_ok}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model-dir', type=Path,required=True)
    ap.add_argument('--output',type=Path,default=Path(__file__).with_name('提示词逐题结果.json'))
    args=ap.parse_args()
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, __version__
    torch.set_num_threads(4)
    torch.manual_seed(42)
    tokenizer=AutoTokenizer.from_pretrained(str(args.model_dir),local_files_only=True)
    model=AutoModelForCausalLM.from_pretrained(str(args.model_dir),local_files_only=True,torch_dtype=torch.float32).eval()
    a=(ROOT/'prompts/answer.txt').read_text(encoding='utf-8').strip()
    prompts={'A':a,'B':a+'\n'+B_ADDITION.strip(),'C':C.strip()}
    result={'model':'Qwen/Qwen2.5-0.5B-Instruct','model_revision':'7ae557604adf67be50417f59c2c2f167def9a775',
            'weights_sha256':checksum(args.model_dir/'model.safetensors'),
            'transformers':__version__,'torch':torch.__version__,'settings':{'greedy':True,'max_new_tokens':88,'device':'cpu','dtype':'float32'},
            'prompt_sha256':{key:hashlib.sha256(text.encode()).hexdigest() for key,text in prompts.items()},
            'inputs':[],'groups':{key:[] for key in prompts}}
    for case_id,product,question,terms,markers in CASES:
        snippets=evidence(product,markers)
        context='\n\n'.join(f'[{i}] {line}' for i,line in enumerate(snippets,1))
        result['inputs'].append({'id':case_id,'source':SOURCES[product].name,'question':question,
                                 'expected_terms':list(terms),'evidence':snippets})
        for variant,prompt in prompts.items():
            messages=[{'role':'system','content':prompt},{'role':'user','content':f'问题：{question}\n\n资料：\n{context}'}]
            encoded=tokenizer.apply_chat_template(messages,add_generation_prompt=True,tokenize=True,return_dict=True,return_tensors='pt')
            started=time.monotonic()
            with torch.inference_mode():
                output=model.generate(**encoded,do_sample=False,max_new_tokens=88,pad_token_id=tokenizer.pad_token_id)
            generated=output[0][encoded['input_ids'].shape[-1]:]
            answer=tokenizer.decode(generated,skip_special_tokens=True).strip()
            result['groups'][variant].append({'id':case_id,'answer':answer,**row_metrics(answer,terms,snippets),
                                              'max_token_limit_reached':len(generated)==88,
                                              'latency_s':round(time.monotonic()-started,2)})
        print('prompt cases finished:',case_id,flush=True)
    result['counts']={}
    for variant,rows in result['groups'].items():
        positives=[r for r in rows if r['id'].startswith('p')]
        negatives=[r for r in rows if r['id'].startswith('n')]
        result['counts'][variant]={'total':len(rows),
          'positive_n':len(positives),'positive_keyword_correct':sum(r['passed'] for r in positives),
          'positive_refused':sum(r['refused'] for r in positives),
          'negative_n':len(negatives),'negative_refused':sum(r['refused'] for r in negatives),
          'negative_exact_refusal':sum(r['exact_refusal'] for r in negatives),
          'positive_valid_citation_format':sum(r['citation_range_ok'] and not r['refused'] for r in positives),
          'token_limit':sum(r['max_token_limit_reached'] for r in rows)}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result['counts'],ensure_ascii=False))

if __name__=='__main__':
    main()
