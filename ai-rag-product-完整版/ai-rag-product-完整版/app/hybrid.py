"""Intent-aware candidate fusion and source-backed procedure assembly."""
from functools import lru_cache
import re

from . import query
from .faq import STEP


FAULT = re.compile(r'^(?:常见故障|故障现象|故障描述|故障提示|问题现象|故障代码|故障码|现象)\s*[:：]\s*(.*)$')
REMEDY = re.compile(r'^(?:解决方法|处理方法|解决办法|排查步骤|解决措施)\s*[:：]\s*(.*)$')
NOTE = re.compile(r'^(?:注意|提示|警告|可能的原因|可能原因|原因)\s*[:：]?')
ACTION = re.compile(r'请|应(?:先|当)|必须|不要|尽量|建议|先.+再|可(?:以)?(?:通过|将|用)|清理|清[洁洗]|'
                    r'检查|确认|打开|关闭|开启|点击|选择|重[设启置]|联系|拔下|放入|切断|按下|提交|挂失|登记|登录|携带|领取')
PROCEDURE = re.compile(r'怎么|如何|怎样|步骤|排查|故障|无法|不能连接|连不上|'
                       r'(?:哪里|哪儿|何处).{0,8}(?:申请|领取|办理|设置|开启|查看|提交)')
PRECISE = re.compile(r'多少|多大|多久|多长|多重|几[个分钟秒瓦年]|(?:型号|规格|参数|故障码|错误码|代码).{0,10}(?:是什么|含义|表示|说明)')
FAULT_CODE = re.compile(r'(?<![A-Z0-9])[A-Z]{1,3}\d{1,4}(?![A-Z0-9])', re.I)
COMPACT_MODEL = re.compile(r'(?<![A-Z0-9])[A-Z]{2,8}\d[A-Z0-9]{5,23}(?![A-Z0-9])')
DECIMAL_START = re.compile(r'^\d+\.\d')


def is_procedure(question):
    return bool(PROCEDURE.search(question) and not PRECISE.search(question))


def identifiers(question):
    text = query.normalize(question)
    codes = FAULT_CODE.findall(text) if re.search(r'故障|报错|代码|报码|显示', text) else []
    models = [name for name in COMPACT_MODEL.findall(text) if sum(c.isdigit() for c in name) >= 3]
    return tuple(dict.fromkeys(token.casefold() for token in (*codes, *models)))


def contains_identifiers(terms, text):
    normalized = query.normalize(text).casefold()
    return all(re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', normalized)
               for term in terms)


def canonical(text):
    text = query.normalize(text).casefold()
    text = re.sub(r'wi[\s-]*fi|无线网[络路]', 'wifi', text)
    text = re.sub(r'连接不上|连不上|不能连接|连接失败', '无法连接', text)
    text = re.sub(r'吸力(?:变小|变弱|下降|变低|不足)', '吸力减弱', text)
    text = re.sub(r'停电时的处理|停电后的处理|断电后|停电后', '停电', text)
    text = re.sub(r'弄丢|丢了|丢失', '遗失', text)
    text = re.sub(r'常见故障|故障现象|应该|怎么办|怎么处理|怎么|如何|怎样|排查|有什么办法|故障', '', text)
    return re.sub(r'[^\w\u3400-\u9fff]+', '', text.replace('产品', ''))


def overlap(question, topic):
    asked, target = canonical(question), canonical(topic)
    if len(target) < 2:
        return 0.0
    if target in asked:
        return 1.0
    grams = {target[i:i+2] for i in range(len(target) - 1)}
    return sum(gram in asked for gram in grams) / len(grams)


def heading(line):
    if line.startswith('#'):
        return True
    if not 2 <= len(line) <= 35 or FAULT.match(line) or REMEDY.match(line) or NOTE.match(line):
        return False
    if STEP.match(line) or re.search(r'[,，。.!！;；?？:：]', line):
        return False
    return bool(re.match(r'^(?:连接|清洁|重设|重置|停止使用|外出度假|安装|拆卸|开启|关闭|恢复)', line)
                or line.endswith(('的处理', '方法', '步骤', '准备')))


def is_step(line):
    return STEP.match(line) and not DECIMAL_START.match(line)


def has_action(text):
    return bool(ACTION.search(text) or any(is_step(line) and len(line) > 7 for line in text.splitlines()))


@lru_cache(maxsize=16)
def procedure_blocks(text):
    """Build bounded passages from document text, preserving original scopes and line ranges."""
    lines = text.splitlines()
    blocks = []
    for index, raw in enumerate(lines):
        line = raw.strip()
        fault = FAULT.match(line)
        if not fault and not heading(line):
            continue
        topic = (fault[1] if fault else line.lstrip('#').strip()).rstrip(':：')
        start = index + 1
        if not topic and start < len(lines):
            topic = lines[start].strip()
            start += 1
        if not 2 <= len(topic) <= 75:
            continue
        end = start
        size = 0
        for raw_body in lines[start:start + 16]:
            body = raw_body.strip()
            if FAULT.match(body) or heading(body):
                break
            if size + len(body) > 1100:
                break
            size += len(body)
            end += 1
        body = '\n'.join(lines[start:end]).strip()
        if body and has_action(body) and (not fault or any(REMEDY.match(row.strip()) for row in body.splitlines())):
            blocks.append((topic, body, index + 1, end, bool(fault)))
    return tuple(blocks)


def matched_blocks(question, documents):
    hits = []
    terms = identifiers(question)
    for document in documents:
        for title, body, start, end, fault in procedure_blocks(document['text']):
            match = overlap(question, title)
            if match < .6 or not contains_identifiers(terms, title + '\n' + body):
                continue
            hits.append({'chunk_id': None, 'document_id': document['id'], 'source': document['source'],
                         'category': '故障处理' if fault else '操作步骤', 'text': title + '\n' + body,
                         'topic': title, 'procedure_body': body, 'source_line': start, 'source_end_line': end,
                         'score': round(.5 + .5 * match, 4), 'match_mode': 'procedure'})
    return sorted(hits, key=lambda hit: (hit['score'], '故障' in hit['category'], len(hit['procedure_body'])),
                  reverse=True)


def rank_candidates(question, lexical, dense, passages, top_k):
    procedural = is_procedure(question)
    terms = identifiers(question)
    ranked = {}
    for mode, candidates, weight in (('bm25', lexical, 1.0), ('chroma', dense, 1.35 if procedural else .55)):
        for index, hit in enumerate(candidates, 1):
            if not contains_identifiers(terms, hit['text']):
                continue
            key = hit['chunk_id']
            if key not in ranked:
                ranked[key] = [dict(hit, match_mode=mode), 0.0]
            elif procedural and mode == 'chroma':
                ranked[key][0] = dict(hit, match_mode=mode)
            ranked[key][1] += weight / (4 + index)
    candidates = sorted(ranked.values(), key=lambda entry: entry[1], reverse=True)
    if procedural and dense:
        first = dense[0]['chunk_id']
        candidates.sort(key=lambda entry: entry[0]['chunk_id'] == first and
                        entry[0]['score'] >= .35 and actionable_evidence(question, entry[0]), reverse=True)
    elif lexical:
        # On quoted specifications and other short facts, retain the lexical winner.
        first = lexical[0]['chunk_id']
        candidates.sort(key=lambda entry: entry[0]['chunk_id'] == first and entry[0]['score'] >= .16,
                        reverse=True)
    if not procedural and PRECISE.search(question) and query.field_name(question):
        candidates.sort(key=lambda entry: bool(format_parameter(question, entry[0])), reverse=True)
    return (passages + [entry[0] for entry in candidates])[:top_k]


def actionable_evidence(question, source):
    if source.get('procedure_body'):
        return True
    body = source['text'].strip()
    if not has_action(body) or len(body) < 12:
        return False
    asked, text = canonical(question), canonical(body)
    technical = ('wifi', 'bluetooth', 'pdf', 'usb', 'ios', 'android')
    if any(term in asked and term not in text for term in technical):
        return False
    specific = set(re.findall(r'[a-z]{3,}|[\u3400-\u9fff]{2,}', asked))
    grams = {term[i:i+2] for term in specific for i in range(len(term)-1)
             if term[i:i+2] not in ('可以', '是否', '步骤', '处理', '请问', '使用')}
    return not grams or sum(gram in text for gram in grams) >= min(2, len(grams))


def format_parameter(question, source):
    field = query.field_name(question)
    if not field or not PRECISE.search(question):
        return None
    for line in source['text'].splitlines():
        label, separator, value = query.normalize(line).strip().partition(':')
        if separator and label.strip() == field and value.strip().strip('/—-'):
            return f'{field}为 {value.strip().rstrip("。.")}。'
    return None


def format_procedure(source):
    """Keep source order, conditions and existing step numbers in the displayed answer."""
    lines = [line.strip() for line in source['procedure_body'].splitlines() if line.strip()]
    paragraphs = []
    for line in lines:
        if (paragraphs and not is_step(line) and not REMEDY.match(line) and not NOTE.match(line)
                and not paragraphs[-1].endswith(('。', '！', '？', '.', '!', '?', '；', ';', ':', '：'))
                and not ACTION.search(line)):
            paragraphs[-1] += ' ' + line
        else:
            paragraphs.append(line)
    if not any(is_step(line) for line in paragraphs):
        paragraphs = [line if REMEDY.match(line) and line.endswith((':', '：')) else '- ' + line
                      for line in paragraphs]
    return source['topic'] + '\n' + '\n'.join(paragraphs)
