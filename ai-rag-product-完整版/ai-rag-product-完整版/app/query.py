"""Scope-aware rules for short product questions and document evidence."""
import re
import unicodedata


HANDOFF = re.compile(
    r'退款|投诉|退货|账户权限|修改权限|注销账户|转人工|找人工|联系人工|人工客服|人工服务|真人客服'
    r'|申请.{0,6}(?:维修|换货|保修)|安排.{0,4}维修|上门维修|预约安装|安装预约|开(?:具)?发票|开票|办理延保|换货'
)
FOLLOWUP = re.compile(r'^(?:那(?:么)?|它(?:的)?|这台(?:的)?|这个(?:的)?|那个(?:的)?|还有|另外)|呢[？?。！!]*$')
AMBIGUOUS = re.compile(r'^(?:那|那么|它|它的|这台|这个|那个|还有|除此之外|另外)?\s*(?:呢|怎么样|怎么办|怎么操作)?[？?。！!]*$')
GENERIC_ASK = re.compile(r'^(?:最多|最少|至少|上限|大概)?\s*(?:多少|多大|多久|多远|多长|多重|多快|是多少|怎么做)[\sA-Za-z°℃℉%0-9]*[？?。！!]*$', re.I)
MODEL_TOKEN = re.compile(r'(?<![A-Z0-9_-])[A-Z][A-Z0-9]{0,10}(?:[-_][A-Z0-9]+)+(?![A-Z0-9_-])', re.I)
MODEL_HEADER = re.compile(r'^\s*(?:#+\s*)?(?:产品|主机|整机|适用产品)?型号\s*[:：]\s*([A-Z0-9]+(?:[-_][A-Z0-9]+)*)', re.I)
COMPONENTS = ('冷冻室', '冷藏室', '变温室', '制冰室')
FIELDS = (
    '额定洗涤容量', '附件上传限制', '附件大小限制', '额定制冷功率', '额定制热功率', '建议制冷面积',
    '冷冻室容积', '冷藏室容积', '变温室容积', '二星级部分容积', '集尘盒容量', '额定电流', '额定电压',
    '额定功率', '额定输入功率', '总容积', '产品净重', '产品毛重', '产品尺寸', '运作水压',
    '无线频段', '电池容量', '充电时间', '噪声', '噪音', '容积', '功率', '电压', '重量', '容量', '标签',
)
AGGREGATE = re.compile(r'分别|逐[项一]|哪[几三四五六二两]种|(?:都|一共)?有哪些|全部|依次|排查步骤')
PROCEDURE = re.compile(r'怎么|如何|哪些步骤|排查|故障|无法|怎么办')
FEATURE = re.compile(r'(?:是否(?:支持|具有|具备|提供)|支持|能否|能不能|能|可以)(.{2,28}?)(?:功能)?(?:吗|么|嘛)?[？?。！!]*$')
FACT_ASK = re.compile(r'多少|多大|多久|多远|多长|几(?:个|本|米|瓦|斤|公斤|秒|分钟|小时|年|天|种|档)')
FACT_REQUIREMENTS = (
    (re.compile(r'卖多少钱|售价|零售价|价格是多少|卖什么价'), re.compile(r'(?:售价|价格|定价|单价)\s*(?:为|是|:)?\s*(?:[￥¥$]\s*)?\d')),
    (re.compile(r'(?:质保|保修).{0,8}(?:多久|几年|几个月|时长|期限|期是多少)'),
     re.compile(r'(?:质保|保修).{0,16}?(?:\d+|[一二三四五六七八九十两]+)\s*(?:年|个月|月|天)')),
    (re.compile(r'安装.{0,15}(?:收费|费用|多少钱)|安装费'), re.compile(r'安装[^\n。;；]{0,35}(?:收费|免费|费用|\d\s*元)|(?:收费|免费)[^\n。;；]{0,16}安装')),
    (re.compile(r'运费|配送费'), re.compile(r'运费|配送费|包邮|免运')),
    (re.compile(r'工作声音|噪[声音].{0,8}(?:多大|多少)'), re.compile(r'噪[声音]|分贝|\bdb\b')),
    (re.compile(r'为什么|为何|什么原因|原因是什么'), re.compile(r'因为|为避免|为了|以免|以便|确保|原因|导致|由于')),
)
MAPPINGS = (
    (r'(?:多重|重量是多少|多少[斤公斤]+)', ('产品净重', '重量')),
    (r'除霜', ('化霜',)),
    (r'(?:能装|能放)多少', ('总容积', '容量')),
    (r'费电', ('耗电量', '功率')),
    (r'多少瓦', ('额定功率', '功率')),
    (r'电流.{0,4}(?:多大|多少)', ('额定电流',)),
    (r'洗.{0,4}多少公斤(?:衣服)?', ('额定洗涤容量',)),
    (r'(?:多少毫安|电池.{0,4}多大)', ('电池容量',)),
    (r'(?:首次|第一次).{0,6}充电', ('首次使用', '充电时间')),
    (r'充电.{0,8}(?:多久|多长时间)', ('充电时间',)),
    (r'(?:多大的空间|放得下)', ('产品尺寸',)),
    (r'能管多大面积', ('建议制冷面积',)),
    (r'遥控器.{0,12}(?:多远|距离)', ('范围内', '信号接收窗')),
    (r'放在多热', ('温度高于', '环境温度')),
    (r'水压太低', ('运作水压',)),
)


def normalize(text):
    return unicodedata.normalize('NFKC', text).translate(str.maketrans('‐‑–', '---'))


def needs_handoff(question):
    return bool(HANDOFF.search(question) or question.strip(' ?？。!！') == '人工')


def model_ids(question):
    return list(dict.fromkeys(match.upper().replace('_', '-') for match in MODEL_TOKEN.findall(normalize(question))
                              if any(c.isdigit() for c in match)))


def field_name(text):
    return next((field for field in FIELDS if field in text), None)


def rewrite(question, previous=''):
    """Resolve explicit follow-ups within a single product/version."""
    if not FOLLOWUP.search(question):
        return question, False
    stem = re.sub(r'^(?:那么?|它(?:的)?|这台(?:的)?|这个(?:的)?|那个(?:的)?|还有|另外)\s*', '', question)
    stem = re.sub(r'呢([？?。！!]*)$', r'\1', stem)
    if AMBIGUOUS.fullmatch(question) or not stem.strip(' ?？。!！'):
        return question, True
    if not previous:
        return question, False
    component = next((name for name in COMPONENTS if name in stem), None)
    previous_field = field_name(previous) or ''
    if component and previous_field.endswith('容积') and not field_name(stem):
        return f'{component}容积是多少？', False
    if field_name(stem) or not GENERIC_ASK.fullmatch(stem):
        return stem, False
    if previous_field:
        return f'{previous_field} {stem}', False
    return f'{previous}；追问：{stem}', False


def synonyms(question, product_id):
    terms = []
    for pattern, labels in MAPPINGS:
        if re.search(pattern, question):
            if labels[0] == '总容积' and not ('fridge' in product_id or '冰箱' in question):
                labels = ('容量',)
            terms.extend(label for label in labels if label not in question)
    return tuple(dict.fromkeys(terms))


def model_sections(text):
    """Yield consecutive spec facts under explicit model headings."""
    model = prefix = None
    remaining = 0
    for raw in text.splitlines():
        line = normalize(raw).strip()
        header = MODEL_HEADER.match(line)
        if header:
            model, prefix = header[1].upper().replace('_', '-'), raw.strip()
            remaining = 20
            continue
        if line.startswith('#') or not line:
            model = prefix = None
        elif model and remaining and re.match(r'^[^。！？:：]{1,32}\s*[:：]\s*\S', line):
            yield model, prefix, raw.strip()
            remaining -= 1
        elif model:
            model = prefix = None


def table_headers(text):
    """Associate Markdown table rows with the header actually above them."""
    headers, ambiguous = {}, set()
    previous = header = ''
    width = 0
    for row in text.splitlines():
        if not (row.startswith('|') and row.endswith('|')):
            previous = header = ''
            continue
        cells = row.strip('|').split('|')
        if previous and re.fullmatch(r'\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+', row.strip('|')):
            header, width = previous, len(cells)
        elif header and len(cells) == width:
            if row in headers and headers[row] != header:
                ambiguous.add(row)
            headers[row] = header
        else:
            header = ''
        previous = row
    return {row: header for row, header in headers.items() if row not in ambiguous}


def required_feature(question):
    if FACT_ASK.search(question):
        return ''
    match = FEATURE.search(question)
    if not match:
        return ''
    topic = re.sub(r'^使用', '', match[1])
    return re.sub(r'\W+', '', normalize(topic)).casefold()


def contains_requested_fact(question, evidence):
    return all(required.search(evidence) for intent, required in FACT_REQUIREMENTS if intent.search(question))
