"""Extract source-backed FAQ entries; generated questions never invent answers."""
from __future__ import annotations

import re

QUESTION = re.compile(r'^(?:Q(?:uestion)?|问(?:题)?)\s*[:：.、]\s*(.+)$', re.IGNORECASE)
ANSWER = re.compile(r'^(?:A(?:nswer)?|答(?:案)?)\s*[:：.、]\s*(.*)$', re.IGNORECASE)
INLINE = re.compile(r'^(?:Q(?:uestion)?|问(?:题)?)\s*[:：.、]\s*(.+?)\s*(?:A(?:nswer)?|答(?:案)?)\s*[:：.、]\s*(.+)$', re.IGNORECASE)
STEP = re.compile(r'^(?:\d{1,2}[.、)）]|[①-⑳]|[-*•●]\s)')
PARAMETER = re.compile(r'(?:型号|尺寸|重量|净重|毛重|容量|功率(?:输入)?|电压|电流|频率|噪[声音]|温度|用水量|续航|时长|转速|水压|压力|分辨率|等级|范围|制冷量|制热量)$')
MEASUREMENT = re.compile(r'\d\s*(?:[a-zA-Z°℃℉%]|[毫厘千米立升克瓦伏安赫分秒小度])')
PROCEDURE = re.compile(r'(?:连接|配对|重置|重设|开机|关机|充电|安装|清洁|更换|设置|调节|开启|关闭|启动|导出)')
INSTRUCTION = re.compile(r'^(?:请[在将先按]|先|然后|接着|打开|扫描|长按|短按|点击|选择|根据|本产品仅支[持援])|(?:下载并安装|根据.{0,16}提示)')
BLOCK_BOUNDARY = re.compile(r'^(?:#|故障现象|解决方法|产品型号|程序说明|(?:(?:注意|提示|警告)\s*[:：])|[QqAa][:：])')
FAULT = re.compile(r'^(?:故障现象|问题现象|故障描述)\s*[:：]\s*(.*)$')
REMEDY = re.compile(r'^(?:解决方法|处理方法|解决办法|排查步骤)\s*[:：]\s*(.*)$')
SECTION_QUESTIONS = {
    '产品介绍': ('什么是{product}？', '产品介绍'),
    '产品简介': ('什么是{product}？', '产品介绍'),
    '产品概述': ('什么是{product}？', '产品介绍'),
    '概述': ('什么是{product}？', '产品介绍'),
    '简介': ('什么是{product}？', '产品介绍'),
    '产品概览与硬件结构': ('{product}有哪些主要硬件结构？', '产品介绍'),
    '接口与包装': ('{product}的接口与包装有哪些说明？', '产品介绍'),
    '主要功能': ('{product}有哪些功能？', '功能介绍'),
    '核心功能': ('{product}有哪些功能？', '功能介绍'),
    '核心功能与内置软件': ('{product}有哪些主要功能？', '功能介绍'),
    '功能介绍': ('{product}有哪些功能？', '功能介绍'),
    '功能': ('{product}有哪些功能？', '功能介绍'),
    '首次开机': ('{product}如何首次开机？', '操作步骤'),
    '手势与按键': ('{product}有哪些常用手势和按键操作？', '操作步骤'),
    '系统设置与个性化': ('如何设置{product}？', '操作步骤'),
    '使用方法': ('如何使用{product}？', '操作步骤'),
    '操作步骤': ('如何使用{product}？', '操作步骤'),
    '快速入门': ('如何使用{product}？', '操作步骤'),
    '产品参数': ('{product}有哪些主要参数？', '参数限制'),
    '规格参数': ('{product}有哪些主要参数？', '参数限制'),
    '技术规格': ('{product}有哪些主要参数？', '参数限制'),
}


def _fact_body(line: str, product_name: str) -> str:
    body = line.strip()
    if product_name and body.startswith(product_name):
        body = body[len(product_name):].lstrip()
        body = re.sub(r'^\d+(?:\.\d+)*\s*', '', body)
        body = body.removeprefix('的').lstrip()
    return body.rstrip('。.')


def _rule_question(line: str, product_name: str):
    body = _fact_body(line, product_name)
    if not body or '?' in body or '？' in body:
        return None
    if product_name and line.startswith(product_name) and re.match(r'^(?:是一个|是一款|是一种|主要用于|用于)', body):
        return f'什么是{product_name}？', '产品介绍'
    limit = re.search(r'(.{0,25}?)最多支持\s*\d+(?:\.\d+)?\s*([^，。；]+)', body)
    if limit:
        return f'{limit.group(1)}最多支持多少{limit.group(2).strip()}？', '参数限制'
    size = re.search(r'(.{2,25}?(?:限制|上限))\s*(?:为|是|:|：)\s*.+?\d+\s*(?:MB|GB|KB|个|条|次)', body, re.IGNORECASE)
    if size:
        return f'{size.group(1)}是多少？', '参数限制'
    feature = re.match(r'^(?:不支持|支持)(.{2,70})$', body)
    if feature:
        return f'是否支持{feature.group(1).strip()}？', '功能支持'
    if '忘记密码' in body:
        return '忘记密码怎么办？', '故障处理'
    if re.search(r'(?:价格|售价)\s*(?:为|是|:|：)\s*\d+', body):
        return '价格是多少？', '价格'
    if re.search(r'(?:保修期|质保期)\s*(?:为|是|:|：)\s*.+', body):
        return '保修期是多久？', '售后保修'
    if body.startswith('适用于') and len(body) > 5:
        return '适用于哪些场景？', '适用场景'
    if body.startswith('兼容') and len(body) > 4:
        return '兼容哪些系统？', '兼容性'
    if '即可' in body and '导出' in body:
        format_name = re.search(r'\b(?:PDF|Word|Excel|CSV)\b', body, re.IGNORECASE)
        return f'如何导出{(" " + format_name.group(0)) if format_name else ""}？', '操作步骤'
    return None


def _section_faqs(lines, product_name):
    """Make broad common questions only from explicitly titled sections."""
    if not product_name:
        return []
    result, current, facts = [], None, []

    def flush():
        if current and facts:
            answer = '\n'.join(line for _, line in facts)
            if len(answer) >= 8:
                question, category = current
                result.append({'question': question.format(product=product_name),
                               'answer': answer, 'category': category, 'kind': 'auto',
                               'source_line': facts[0][0], 'source_end_line': facts[-1][0]})

    for index, line in enumerate(lines, 1):
        if line.startswith('#'):
            flush()
            heading = re.sub(r'^[\d一二三四五六七八九十.、\s]+', '', line.lstrip('#').strip())
            current = SECTION_QUESTIONS.get(heading)
            facts = []
            continue
        if not current or QUESTION.match(line) or ANSWER.match(line) or line.endswith(('?', '？')):
            continue
        if len(facts) < 5 and sum(len(value) for _, value in facts) + len(line) <= 600:
            facts.append((index, line))
    flush()
    return result


def extract_faqs(text: str, product_name: str):
    """Return question/answer/category/kind rows from cleaned document text."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    found, explicit_answer_lines, question, answer_lines = [], set(), None, []

    def flush():
        nonlocal question, answer_lines
        if question and answer_lines:
            found.append({'question': question.rstrip('？?') + '？',
                          'answer': '\n'.join(line for _, line in answer_lines), 'category': '文档 FAQ', 'kind': 'explicit',
                          'source_line': answer_lines[0][0], 'source_end_line': answer_lines[-1][0]})
        question, answer_lines = None, []

    for index, line in enumerate(lines):
        if line.startswith('#'):
            flush()
            continue
        inline = INLINE.match(line)
        if inline:
            flush()
            found.append({'question': inline.group(1).strip().rstrip('？?') + '？',
                          'answer': inline.group(2).strip(), 'category': '文档 FAQ', 'kind': 'explicit',
                          'source_line': index + 1, 'source_end_line': index + 1})
            explicit_answer_lines.add(index)
            continue
        q = QUESTION.match(line)
        a = ANSWER.match(line)
        if q or (line.endswith(('?', '？')) and len(line) <= 120):
            flush()
            question = (q.group(1) if q else line).strip()
            continue
        if a and question:
            answer = a.group(1).strip()
            if answer:
                answer_lines.append((index + 1, answer))
            explicit_answer_lines.add(index)
        elif question:
            if answer_lines and answer_lines[-1][1].endswith(('。', '！', '!', '.')) and not (STEP.match(line) or INSTRUCTION.search(line)):
                flush()
                continue
            answer_lines.append((index + 1, line))
            explicit_answer_lines.add(index)
    flush()

    for index, line in enumerate(lines):
        if line.startswith('#') or index in explicit_answer_lines or QUESTION.match(line) or ANSWER.match(line):
            continue
        result = _rule_question(line, product_name)
        if result:
            generated_question, category = result
            found.append({'question': generated_question, 'answer': line,
                          'category': category, 'kind': 'rule', 'source_line': index + 1, 'source_end_line': index + 1})
            if category == '参数限制' and '上传限制' in generated_question and re.search(r'\d+\s*(?:MB|GB|KB)', line, re.IGNORECASE):
                found.append({'question': generated_question.replace('上传限制', '大小限制'),
                              'answer': line, 'category': category, 'kind': 'rule',
                              'source_line': index + 1, 'source_end_line': index + 1})

    found.extend(_section_faqs(lines, product_name))

    # Keep conflicting explicit answers visible for review and handoff.
    unique, seen = [], set()
    for item in found:
        key = (re.sub(r'\s+', '', item['question']).lower(), item['answer'] if item['kind'] == 'explicit' else '')
        if key not in seen and item['answer'].strip():
            seen.add(key)
            unique.append(item)
    return unique


def extract_import_faqs(text, product_name):
    """Extract reviewable parameter and procedure QAs from normal manuals."""
    lines = text.splitlines()
    candidates = extract_faqs(text, product_name)
    explicit_lines = {index for faq in candidates if faq['kind'] == 'explicit'
                      for index in range(faq['source_line'], faq['source_end_line'] + 1)}
    structured_lines = set(explicit_lines)
    for index, line in enumerate(lines):
        if index + 1 in explicit_lines:
            continue
        fault = FAULT.match(line)
        if fault:
            fix = extract_remedy(lines, index, fault.group(1), product_name)
            if fix:
                candidates.append(fix)
                structured_lines.update(range(index + 1, fix['source_end_line'] + 1))
        body = re.sub(r'^[-*•●]\s+', '', line)
        label_value = re.fullmatch(r'([^:：|]{2,28})\s*[:：]\s*(\S.{0,180})', body)
        cells = [cell.strip() for cell in line.strip('|').split('|')] if line.startswith('|') else []
        if len(cells) == 2:
            label, value = cells
        elif label_value:
            label, value = label_value.groups()
        else:
            label, value = '', ''
        label = label.strip()
        if PARAMETER.search(label) and (MEASUREMENT.search(value) or ('型号' in label and re.search(r'[A-Za-z]+[-\w]*\d', value))):
            qualifier = re.fullmatch(r'(?:\(App\s*内\)|(?:洗涤)?程序)\s*[:：]\s*(.{1,30})', lines[index - 1], re.IGNORECASE) if index else None
            stem = f'{product_name}的{qualifier.group(1)}程序的{label}' if qualifier else f'{product_name}的{label}'
            candidates.append({'question': stem + ('是什么？' if '型号' in label else '是多少？'),
                               'answer': '\n'.join(lines[index - 1:index + 1]) if qualifier else line,
                               'category': '参数限制', 'kind': 'parameter',
                               'source_line': index if qualifier else index + 1, 'source_end_line': index + 1})
            structured_lines.add(index + 1)

        title = line.lstrip('#').strip().rstrip(':：')
        if (not 2 <= len(title) <= 28 or not (PROCEDURE.search(title) or title.endswith(('步骤', '准备')))
                or STEP.match(title) or QUESTION.match(title) or BLOCK_BOUNDARY.match(line) and not line.startswith('#')
                or re.search(r'[。.!！?？;；:：，,]|拆卸电池|维修|报废', title)):
            continue
        section, step_count = [], 0
        for next_line in lines[index + 1:index + 17]:
            if BLOCK_BOUNDARY.match(next_line):
                if (section and re.fullmatch(r'(?:注意|提示|警告)[:：]', next_line)):
                    section.append(next_line)
                    continue
                break
            is_step = bool(STEP.match(next_line) or INSTRUCTION.search(next_line))
            is_context = next_line.startswith(('本产品', '首次', '请勿', '请确保')) or (section and section[-1].endswith((':', '：')))
            if not (is_step or is_context):
                break
            section.append(next_line)
            step_count += is_step
        while section and re.fullmatch(r'(?:注意|提示|警告)[:：]', section[-1]):
            section.pop()
        if step_count and len(section) < 16 and sum(map(len, section)) + len(section) <= 1200:
            topic = re.sub(r'^(?:如何|怎样)', '', title).removesuffix('步骤')
            answer = '\n'.join(section)
            procedure = {'question': f'{product_name}如何{topic}？', 'answer': answer,
                         'category': '操作步骤', 'kind': 'procedure',
                         'source_line': index + 2, 'source_end_line': index + 1 + len(section)}
            candidates.append(procedure)
            if re.match(r'连接.*app', topic, re.IGNORECASE) and re.search(r'连接[^\n]{0,32}Wi-Fi', answer, re.IGNORECASE):
                candidates.append({**procedure, 'question': f'{product_name}如何连接 Wi-Fi？'})

    candidates.extend(faq for faq in extract_training_faqs(text, product_name)
                      if faq['source_line'] not in structured_lines)
    seen, by_question, result = set(), {}, []
    for faq in candidates:
        key = (re.sub(r'[\W_]+', '', faq['question']).casefold(), faq['answer'])
        if key in seen:
            continue
        seen.add(key)
        by_question.setdefault(key[0], []).append(faq)
        result.append(faq)
    for items in by_question.values():
        if len(items) > 1:
            for item in items:
                item['conflict'] = True
    return result


def extract_remedy(lines, index, topic, product_name):
    for offset, line in enumerate(lines[index + 1:index + 4], index + 1):
        if FAULT.match(line) or line.startswith('#'):
            return None
        marker = REMEDY.match(line)
        if marker:
            break
        if not topic:
            topic = line
    else:
        return None
    if not 2 <= len(topic) <= 70:
        return None
    first = marker.group(1)
    start = offset + 1 if first else offset + 2
    answer = [first] if first else []
    for detail in lines[offset + 1:offset + 13]:
        if BLOCK_BOUNDARY.match(detail) or FAULT.match(detail) or REMEDY.match(detail):
            break
        if first and not (STEP.match(detail) or INSTRUCTION.search(detail)):
            break
        if len(detail) < 8 and not STEP.match(detail):
            break
        answer.append(detail)
    if answer and len(answer) < 12 and sum(map(len, answer)) <= 1200:
        return {'question': f'{product_name}{topic.rstrip("。.!！?？")}怎么办？', 'answer': '\n'.join(answer),
                'category': '故障处理', 'kind': 'troubleshooting', 'source_line': start, 'source_end_line': start + len(answer) - 1}
    return None


def extract_training_faqs(text: str, product_name: str):
    """Build focused customer questions from named facts and procedures.

    Answers are copied verbatim from one source line. This complements the broad
    section questions created at import time, so training has useful new work.
    """
    result, seen = [], set()
    for index, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#') or QUESTION.match(line) or ANSWER.match(line):
            continue
        body = re.sub(r'^[•●\-*]\s*', '', line).strip()
        if len(body) < 12 or len(body) > 800 or body.endswith(('?', '？')):
            continue
        match = re.match(r'^([^：:]{2,24})[：:]\s*(.{8,})$', body)
        if not match:
            continue
        topic, detail = match.group(1).strip(), match.group(2).strip()
        if not topic or re.search(r'[。；，?？]', topic) or not detail:
            continue
        if any(word in topic for word in ('资料核对', '阅读提示', '产品线提示', '来源', '参考资料', '故障现象', '程序说明', '注意', '警告')):
            continue
        if any(word in topic for word in ('无法', '故障', '黑屏', '死机', '发热', '忘记', '异常')):
            question, category = f'{product_name}{topic}怎么办？', '故障处理'
        elif any(word in topic for word in ('价格', '售价', '费用')):
            question, category = f'{product_name}{topic}是多少？', '价格'
        elif any(word in topic for word in ('保修', '质保')):
            question, category = f'{product_name}{topic}是多久？', '售后保修'
        elif any(word in topic for word in ('参数', '容量', '限制', '上限', '尺寸', '规格')):
            question, category = f'{product_name}{topic}是多少？', '参数限制'
        elif '版本' in topic:
            question, category = f'{product_name}的{topic}是什么？', '参数限制'
        elif 'SIM' in topic.upper():
            question, category = f'{product_name}如何安装或设置{topic}？', '操作步骤'
        elif 'Face ID' in topic:
            question, category = f'{product_name}如何设置{topic}？', '操作步骤'
        elif any(word in topic for word in ('顶部', '底部', '左侧', '右侧', '背面', '屏幕')):
            question, category = f'{product_name}的{topic}有什么？', '产品介绍'
        elif any(word in topic for word in ('设置', '开机', '关机', '重启', '截图', '连接', '配对', '导出', '清洁', '充电', '操作', '使用')) or '→' in detail:
            question, category = f'{product_name}如何{topic}？', '操作步骤'
        elif any(word in topic for word in ('功能', '支持', '兼容', '接口')):
            question, category = f'{product_name}的{topic}有哪些？', '功能介绍'
        else:
            question, category = f'{product_name}的{topic}有什么说明？', '功能介绍'
        key = re.sub(r'\s+', '', question).lower()
        if key not in seen:
            seen.add(key)
            result.append({'question': question, 'answer': detail,
                           'category': category, 'kind': 'trained', 'source_line': index, 'source_end_line': index})
    return result
