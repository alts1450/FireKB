"""Export a snapshot of the knowledge library and its cited files as a ZIP."""
import argparse
import json
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

import kb
from verify_kp import label_to_page_no, page_numbers_from_labels


STATUS_LABELS = {'verified': '已验证', 'pending': '待确认', 'rejected': '已拒绝'}


def jsonl(rows):
    return ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)


def archive_source(source, archive_root):
    """Resolve only regular files within the original-material archive."""
    try:
        path = (archive_root / source).resolve(strict=True)
        return path if path.is_relative_to(archive_root) and path.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


def source_line(record, source_entries):
    source = record.get('source_file') or ''
    entry = source_entries.get(source)
    location = record.get('page_label') or record.get('page_label_start') or ''
    end = record.get('page_label_end')
    if end and location != end:
        location = f'{location}–{end}' if location else end
    label = source or '来源待补充'
    link = f'[打开源文件](../../{quote(entry)})' if entry else ''
    return ' · '.join(part for part in [label, location, link] if part)


def course_markdown(course, points, evidence_by_point, source_entries):
    lines = [f'# {course} · 知识点', '', f'共 {len(points)} 条', '']
    for point in points:
        kp_id = point['kp_id']
        title = point.get('name') or point.get('title') or kp_id
        definition = point.get('definition') or point.get('content') or ''
        status = STATUS_LABELS.get(point.get('status'), point.get('status', ''))
        details = [f'编号 {kp_id}', status]
        if point.get('importance'):
            details.append(f"重要度 {point['importance']}/5")
        if point.get('status') == 'verified':
            method = {'human': '人工核验', 'ai_web': 'AI 联网核验'}.get(point.get('verification_method'))
            if method:
                details.append(method)
        lines.extend([f'## {title}', '', definition, '', ' · '.join(filter(None, details))])
        section = ' / '.join(str(point[key]) for key in ['chapter', 'section'] if point.get(key))
        if section:
            lines.append(section)
        if point.get('note'):
            lines.extend(['', f"**批注** {point['note']}"])
        if point.get('numbers'):
            lines.append('**数值** ' + '、'.join(map(str, point['numbers'])))
        lines.extend(['', '**来源** ' + source_line(point, source_entries)])
        for evidence in evidence_by_point.get(kp_id, []):
            lines.extend(['', '- ' + source_line(evidence, source_entries)])
            if evidence.get('quote'):
                lines.extend([''] + ['  > ' + line for line in evidence['quote'].split('\n')])
        for url in point.get('source_urls') or []:
            if isinstance(url, str) and kb.is_http_url(url):
                lines.append(f'- {url}')
            elif isinstance(url, dict) and kb.is_http_url(url.get('url')):
                lines.append(f"- {url.get('title') or '网页来源'} · {url['url']}")
        lines.extend(['', '---', ''])
    return '\n'.join(lines)


def export_knowledge(output, course=None, include_sources=False):
    output = Path(output).resolve()
    archive_root = kb.ARCHIVE.resolve()
    course = course or None
    if output.suffix.lower() != '.zip':
        raise ValueError('请选择 ZIP 文件名')
    with kb.writer_lock():
        all_points = kb.load_jsonl(kb.KP_FILE)
        if course and not any(row.get('course') == course for row in all_points):
            course = kb.resolve_course(course)
        points = [row for row in all_points if course is None or row.get('course') == course]
        if not points:
            raise ValueError('所选范围暂无知识点')
        point_ids = {row['kp_id'] for row in points}
        evidence = [row for row in kb.load_jsonl(kb.EVIDENCE_FILE) if row.get('kp_id') in point_ids]
        audits = [row for row in kb.load_jsonl(kb.AUDIT_FILE) if row.get('kp_id') in point_ids]
        pages = kb.load_jsonl(kb.PAGES_FILE)

    source_entries = {}
    files = {}
    missing = []
    sources = sorted({row['source_file'] for row in points + evidence if row.get('source_file')})
    if output.is_relative_to(archive_root) and any(archive_source(source, archive_root) == output for source in sources):
        raise ValueError('请选择与引用源文件不同的保存位置')
    if include_sources:
        for source in sources:
            path = archive_source(source, archive_root)
            if path is None:
                missing.append(source)
                continue
            entry = '引用源文件/' + path.relative_to(archive_root).as_posix()
            source_entries[source] = entry
            files[entry] = path

    by_course = defaultdict(list)
    evidence_by_point = defaultdict(list)
    for row in points:
        by_course[row.get('course') or '未分类'].append(row)
    for row in evidence:
        evidence_by_point[row['kp_id']].append(row)
    locations = {(row.get('source_file'), label_to_page_no(row.get('page_label'))) for row in evidence}
    for point in points:
        labels = [point.get('page_label_start'), point.get('page_label_end')]
        labels.extend(row.get('page_label') for row in evidence_by_point[point['kp_id']])
        numbers = page_numbers_from_labels(*labels)
        if numbers:
            locations.update((point.get('source_file'), number) for number in range(min(numbers), max(numbers) + 1))
    pages = [row for row in pages if (row.get('source_file'), row.get('page_no')) in locations]

    sections = []
    manifest = {'version': 1, 'exported_at': kb.now_iso(), 'course': course, 'include_sources': include_sources,
                'point_count': len(points), 'evidence_count': len(evidence), 'source_count': len(files),
                'courses': sections, 'source_entries': source_entries, 'missing_sources': missing}
    index = ['# 知识点导出', '', f'{len(by_course)} 个科目 · {len(points)} 条知识点 · {len(files)} 份引用源文件', '']
    # Publish the final filename only after the ZIP central directory is complete.
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.firekb-export-', suffix='.tmp', delete=False) as temp:
        staging = Path(temp.name)
    try:
        with ZipFile(staging, 'w', compression=ZIP_DEFLATED, compresslevel=1) as bundle:
            for i, (name, rows) in enumerate(sorted(by_course.items()), 1):
                safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:75].rstrip(' .')
                folder = f'科目/{i:02d}_{safe_name or "未分类"}'
                markdown = f'{folder}/知识点.md'
                bundle.writestr(markdown, course_markdown(name, rows, evidence_by_point, source_entries))
                bundle.writestr(f'{folder}/知识点.jsonl', jsonl(rows))
                bundle.writestr(f'{folder}/引用证据.jsonl', jsonl(e for row in rows for e in evidence_by_point[row['kp_id']]))
                sections.append({'course': name, 'directory': folder, 'point_count': len(rows)})
                index.append(f'- [{name}]({quote(markdown)}) · {len(rows)} 条')
            for entry, path in files.items():
                bundle.write(path, entry)
            bundle.writestr('原文定位.jsonl', jsonl(pages))
            bundle.writestr('核验记录.jsonl', jsonl(audits))
            if missing:
                index.extend(['', '## 待补齐的源文件', ''] + [f'- {source}' for source in missing])
            bundle.writestr('目录.md', '\n'.join(index) + '\n')
            bundle.writestr('导出清单.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        os.replace(staging, output)
    finally:
        staging.unlink(missing_ok=True)

    return {'path': str(output), 'course_count': len(by_course), 'point_count': len(points),
            'source_count': len(files), 'missing_count': len(missing)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--course')
    parser.add_argument('--include-sources', action='store_true')
    args = parser.parse_args()
    try:
        result = export_knowledge(args.output, args.course, args.include_sources)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(2, f'{error}\n')
    print(json.dumps(result, ensure_ascii=False))
