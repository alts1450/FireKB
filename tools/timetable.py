"""Local timetable, timestamp matching, and non-destructive material intake."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import csv
import hashlib
import io
import json
import re
import shutil
import uuid
import kb

FILE = kb.CONFIG_DIR / 'timetable.json'
AUDIO = {'.m4a', '.mp3', '.wav', '.aac', '.opus', '.amr', '.flac', '.wma', '.ogg', '.mp4'}
DOCS = {'.pdf', '.pptx', '.epub', '.docx', '.doc', '.ppt', '.txt', '.md', '.xlsx'}


def empty():
    return {'version': 1, 'semester_start': '', 'total_weeks': 17, 'timezone': 'Asia/Hong_Kong', 'tolerance_minutes': 15, 'slots': [], 'exceptions': []}


def load():
    return kb.load_json(FILE, None) or empty()


def weeks(value, total):
    if not value:
        return list(range(1, total + 1))
    if isinstance(value, list):
        result = [int(x) for x in value]
    else:
        s = str(value).replace('周', '').replace('，', ',').replace('、', ',').replace('—', '-').replace('~', '-')
        parity = 'odd' if '单' in s else 'even' if '双' in s else None
        s = re.sub(r'[单双()（）\s]', '', s)
        result = []
        for part in s.split(','):
            a, _, b = part.partition('-')
            result.extend(range(int(a), int(b or a) + 1))
        if parity:
            result = [x for x in result if x % 2 == (1 if parity == 'odd' else 0)]
    if not result or any(x < 1 or x > total for x in result):
        raise ValueError(f'周次必须在 1—{total} 内')
    return sorted(set(result))


def minutes(s):
    if not re.fullmatch(r'\d{2}:\d{2}', str(s)):
        raise ValueError('上课时间必须为 HH:MM')
    h, m = map(int, s.split(':'))
    if h > 23 or m > 59:
        raise ValueError('无效时间')
    return h * 60 + m


def validate(data, courses=None):
    courses = kb.load_courses() if courses is None else courses
    ids = {c['id'] for c in courses}
    out = {**empty(), **data}
    start = date.fromisoformat(out['semester_start'])
    if start.weekday() != 0:
        raise ValueError('学期第 1 周起始日须为周一')
    out['total_weeks'] = int(out['total_weeks'])
    if not 1 <= out['total_weeks'] <= 60:
        raise ValueError('总周数须为 1—60')
    out['tolerance_minutes'] = int(out['tolerance_minutes'])
    if not 0 <= out['tolerance_minutes'] <= 60:
        raise ValueError('时间容差须为 0—60 分钟')
    ZoneInfo(out['timezone'])
    for row in [*out['slots'], *out['exceptions']]:
        if row.get('course_id') not in ids:
            raise ValueError('课程 ID 不在课表中：' + str(row.get('course_id')))
        row['start'] = str(row['start'])
        row['end'] = str(row['end'])
        if minutes(row['start']) >= minutes(row['end']):
            raise ValueError('结束时间必须晚于开始时间')
    for row in out['slots']:
        row['weekday'] = int(row['weekday'])
        if not 1 <= row['weekday'] <= 7:
            raise ValueError('星期取值为 1—7')
        row['weeks'] = weeks(row.get('weeks'), out['total_weeks'])
    for row in out['exceptions']:
        date.fromisoformat(row['date'])
        if row.get('action', 'add') not in ('add', 'cancel'):
            raise ValueError('例外 action 须为 add 或 cancel')
    return out


def save(data, courses=None):
    courses = kb.load_courses() if courses is None else courses
    used_ids, used_names = set(), set()
    for c in courses:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', c.get('id', '')) or not c.get('name', '').strip():
            raise ValueError('课程须有唯一英文 ID 和名称')
        name = c['name']
        if re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name in {'.','..'} or name != name.strip(' .'):
            raise ValueError('课程名称不能包含路径分隔符、无效文件名字符或首尾空格')
        if c['id'] in used_ids or c['name'] in used_names:
            raise ValueError('课程 ID 或名称重复')
        used_ids.add(c['id']); used_names.add(c['name'])
    old = {c['id']: c for c in kb.load_courses()}
    # Existing IDs/names identify archive folders and knowledge rows.
    for cid, c in old.items():
        current = next((x for x in courses if x['id'] == cid), None)
        if current is None or current['name'] != c['name']:
            raise ValueError('已有课程 ID 和名称需保持稳定；请新增课程或修改别名')
    data = validate(data, courses)
    with kb.writer_lock():
        kb.backup_files([FILE, kb.CONFIG_DIR/'courses.json'], 'timetable')
        config = kb.load_json(kb.CONFIG_DIR/'courses.json', {})
        config['courses'] = [{**old.get(c['id'], {}), **c} for c in courses]
        kb.atomic_dataset({FILE: data, kb.CONFIG_DIR/'courses.json': config})
    return data


def sessions(day, config):
    start = date.fromisoformat(config['semester_start'])
    week = (day - start).days // 7 + 1
    found = [dict(s) for s in config['slots'] if s['weekday'] == day.isoweekday() and week in weeks(s.get('weeks'), config['total_weeks'])]
    for ex in config.get('exceptions', []):
        if ex['date'] != day.isoformat():
            continue
        if ex.get('action') == 'cancel':
            found = [s for s in found if not (s['course_id'] == ex['course_id'] and s['start'] == ex['start'])]
        else:
            found.append(dict(ex))
    return found


def file_time(item, config):
    """Filename time wins; copied-file mtime is a visible, lower-trust fallback."""
    name = item['name']
    full = re.search(r'(?<!\d)(20\d{2})[年._/-]?(\d{1,2})[月._/-]?(\d{1,2})(?:日)?', name)
    short = re.search(r'(\d{1,2})月(\d{1,2})日', name)
    match = full or short
    if match:
        if full:
            y, m, d = map(int, full.groups())
        else:
            m, d = map(int, short.groups())
            start = date.fromisoformat(config['semester_start']); end = start + timedelta(weeks=config['total_weeks'])
            options = []
            for y0 in {start.year, end.year}:
                try:
                    candidate = date(y0, m, d)
                    if start <= candidate < end:
                        options.append(y0)
                except ValueError:
                    pass
            if len(options) != 1:
                return None, False, '标题无年份，无法唯一落入学期'
            y = options[0]
        tail = name[match.end():]
        tm = re.search(r'(\d{1,2})[点:：._时](\d{2})(?:分)?', tail)
        if not tm:
            tm = re.search(r'^[T_\s-]+(\d{2})(\d{2})(?:\d{2})?', tail)
        try:
            hour = int(tm[1]) if tm else 12
            if tm and re.search(r'下午|晚上|午后|\bPM\b', tail, re.I) and hour < 12:
                hour += 12
            if tm and re.search(r'上午|凌晨|\bAM\b', tail, re.I) and hour == 12:
                hour = 0
            dt = datetime(y, m, d, hour, int(tm[2]) if tm else 0, tzinfo=ZoneInfo(config['timezone']))
        except ValueError:
            return None, False, '标题含无效日期或时间'
        return dt, bool(tm), '标题日期时间' if tm else '标题只有日期'
    stamp = item.get('last_modified')
    if stamp:
        dt = datetime.fromtimestamp(float(stamp) / 1000, ZoneInfo(config['timezone']))
        tm = re.search(r'(\d{1,2})[点:：时](\d{2})(?:分)?', name)
        if tm:
            try:
                dt = dt.replace(hour=int(tm[1]), minute=int(tm[2]))
                return dt, True, '标题时间 + 文件日期（需确认）'
            except ValueError:
                return None, False, '标题时间无效'
        return dt, True, '文件修改时间（复制后可能变化，需确认）'
    return None, False, '没有可用日期'


def classify(item, config, courses=None):
    courses = kb.load_courses() if courses is None else courses
    config = validate(config, courses)
    title = item['name'].casefold()
    named = {c['id'] for c in courses if any(str(x).casefold() in title for x in [c['name'], *c.get('aliases', [])] if len(str(x)) >= 2)}
    # IDs use token boundaries so FIRE-A cannot accidentally match FIRE-ABC.
    named |= {c['id'] for c in courses if re.search(r'(?<![\w-])' + re.escape(c['id']) + r'(?![\w-])', item['name'], re.I)}
    dt, timed, reason = file_time(item, config)
    matched = set()
    if dt:
        tolerance = config['tolerance_minutes']
        for s in sessions(dt.date(), config):
            if not timed or minutes(s['start']) - tolerance <= dt.hour * 60 + dt.minute < minutes(s['end']) + tolerance:
                matched.add(s['course_id'])
    conflict = len(named) > 1 or len(matched) > 1 or bool(named and matched and named != matched)
    choices = sorted(named | matched)
    chosen = choices[0] if len(choices) == 1 and not conflict else None
    status = 'conflict' if conflict else 'matched' if chosen and (named or (reason == '标题日期时间' and timed)) else 'review' if chosen else 'unmatched'
    return {**item, 'course_id': chosen, 'candidates': choices, 'status': status, 'date': dt.isoformat() if dt else None, 'reason': ('课程名/别名；' if named else '') + reason + ('；课表冲突' if conflict else ''), 'kind': '录音' if Path(item['name']).suffix.lower() in AUDIO else '课件'}


def parse_import(text, filename, current, courses):
    suffix = Path(filename).suffix.lower()
    if suffix == '.json':
        data = json.loads(text.lstrip('\ufeff'))
        return validate(data.get('timetable', data), data.get('courses', courses)), data.get('courses', courses)
    if suffix == '.csv':
        rows = list(csv.DictReader(io.StringIO(text.lstrip('\ufeff'))))
        out = {**current, 'slots': []}
        names = {x['name']: x['id'] for x in courses}
        for r in rows:
            def field(en, cn): return r.get(en) or r.get(cn) or ''
            cid = field('course_id', '课程'); cid = names.get(cid, cid)
            wd = field('weekday', '星期').replace('星期', '').replace('周', '')
            wd = str('一二三四五六日'.index(wd) + 1) if wd in '一二三四五六日' and wd else wd
            out['slots'].append({'course_id': cid, 'weekday': int(wd), 'start': field('start', '开始'), 'end': field('end', '结束'), 'weeks': field('weeks', '周次')})
        return validate(out, courses), courses
    if suffix != '.ics':
        raise ValueError('支持 JSON、CSV、ICS；可先导出 CSV 模板')
    # Explicitly support weekly ICS; unsupported recurrence is rejected, not truncated.
    unfolded = re.sub(r'\r?\n[ \t]', '', text)
    events = re.findall(r'BEGIN:VEVENT\s*(.*?)END:VEVENT', unfolded, re.S)
    out = {**current, 'slots': [], 'exceptions': []}
    tz = ZoneInfo(out['timezone']); term = date.fromisoformat(out['semester_start'])
    limit = term + timedelta(weeks=out['total_weeks'])
    byday = ['MO','TU','WE','TH','FR','SA','SU']
    def stamp(value, key=''):
        is_utc = value.endswith('Z'); value = value.rstrip('Z')
        dt = datetime.strptime(value, '%Y%m%dT%H%M%S' if len(value) > 8 else '%Y%m%d')
        zone_match = re.search(r'TZID=([^;:]+)', key)
        zone = timezone.utc if is_utc else ZoneInfo(zone_match[1]) if zone_match else tz
        return dt.replace(tzinfo=zone).astimezone(tz)
    for block in events:
        fields = {}; keys = {}
        for line in block.splitlines():
            if ':' in line:
                k,v = line.split(':',1); base=k.split(';')[0];fields.setdefault(base,[]).append(v);keys[base]=k
        if {'RECURRENCE-ID','RDATE','DURATION'} & set(fields):
            raise ValueError('ICS 包含暂不支持的重复例外或时长字段，请导入 CSV 或 JSON，避免遗漏调课')
        summary = fields.get('SUMMARY', [''])[0]
        hits = [c for c in courses if summary in [c['name'], c['id'], *c.get('aliases', [])] or c['name'] in summary]
        if len(hits) != 1:
            raise ValueError('ICS 课程无法唯一匹配，请先添加课程或别名：' + summary)
        start = stamp(fields['DTSTART'][0], keys['DTSTART']); end = stamp(fields['DTEND'][0], keys['DTEND'])
        if start.date() != end.date():
            raise ValueError('不支持跨日或全天课程')
        rule = dict(x.split('=',1) for x in fields.get('RRULE',[''])[0].split(';') if '=' in x)
        if rule and (rule.get('FREQ') != 'WEEKLY' or set(rule)-{'FREQ','INTERVAL','BYDAY','UNTIL','COUNT','WKST'}):
            raise ValueError('ICS 仅支持每周重复；请导入 CSV 或 JSON')
        excluded = {stamp(x,keys.get('EXDATE','')).date() for v in fields.get('EXDATE',[]) for x in v.split(',')}
        days = [byday.index(x) for x in rule.get('BYDAY', byday[start.weekday()]).split(',')]
        last = min(limit - timedelta(days=1), stamp(rule['UNTIL']).date() if rule.get('UNTIL') else limit)
        count=0; day=start.date()
        while day <= last:
            elapsed = (day - (start.date()-timedelta(days=start.weekday()))).days // 7
            occurrence = (bool(rule) and day.weekday() in days and elapsed % int(rule.get('INTERVAL',1)) == 0) or (not rule and day==start.date())
            if occurrence:
                count += 1
                if count > int(rule.get('COUNT', '10000')): break
                if term <= day < limit and day not in excluded:
                    out['exceptions'].append({'course_id': hits[0]['id'], 'date': day.isoformat(), 'start': start.strftime('%H:%M'), 'end': end.strftime('%H:%M'), 'action': 'cancel' if fields.get('STATUS')==['CANCELLED'] else 'add'})
            day += timedelta(days=1)
    if not events:
        raise ValueError('ICS 没有 VEVENT 课程记录')
    return validate(out, courses), courses


def archive_file(source, name, course_id, kind):
    """Copy only; content dedup and collision-safe names, with intake provenance."""
    course = next((c for c in kb.load_courses() if c['id']==course_id), None)
    if not course: raise ValueError('请选择有效课程')
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')
    ext = Path(name).suffix.lower()
    if ext not in AUDIO | DOCS: raise ValueError('不支持此文件类型')
    kind = '录音' if ext in AUDIO else kind
    if kind not in ['录音','课件','规范教材']: raise ValueError('无效素材类别')
    with kb.writer_lock():
        destdir = kb.ARCHIVE/kind/course['name']
        if not destdir.resolve().is_relative_to((kb.ARCHIVE/kind).resolve()) or destdir.resolve() == (kb.ARCHIVE/kind).resolve(): raise ValueError('课程名称含无效路径')
        destdir.mkdir(parents=True, exist_ok=True)
        with Path(source).open('rb') as f: sha=hashlib.file_digest(f,'sha256').hexdigest()
        for p in destdir.iterdir():
            if p.is_file() and p.stat().st_size == Path(source).stat().st_size:
                with p.open('rb') as f: existing=hashlib.file_digest(f,'sha256').hexdigest()
                if existing == sha: return {'status':'duplicate','path':str(p),'sha256':sha}
        dest=destdir/name
        if dest.exists(): dest=destdir/(Path(name).stem+'__'+sha[:10]+ext)
        tmp=destdir/('.intake-'+uuid.uuid4().hex+'.tmp')
        try:
            shutil.copy2(source,tmp)
            with tmp.open('rb') as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=sha: raise ValueError('复制校验失败')
            tmp.replace(dest)
        finally: tmp.unlink(missing_ok=True)
        kb.append_jsonl(kb.LOG_DIR/'intake.jsonl',[{'at':kb.now_iso(),'name':name,'source':str(source),'destination':str(dest),'sha256':sha,'course_id':course_id,'kind':kind}])
        return {'status':'imported','path':str(dest),'sha256':sha,'parse_supported':ext in AUDIO|{'.pdf','.pptx','.epub','.docx'}}
