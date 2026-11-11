"""Merge an exported FireKB ZIP into the local knowledge library."""
import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

import kb
import review_state
from verify_kp import source_text_of


def read_rows(bundle, entry):
    with bundle.open(entry) as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f'请检查包内数据 {entry}')
    return rows


def unique_rows(rows, key):
    seen = set()
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str) or not value or value in seen:
            raise ValueError(f'请检查包内编号 {key}')
        seen.add(value)
    return seen


def source_target(entry, archive_root):
    prefix = '引用源文件/'
    if not isinstance(entry, str) or not entry.startswith(prefix):
        raise ValueError('源文件目录格式有误')
    name = entry[len(prefix):]
    parts = name.split('/')
    for part in parts:
        if (not part or part in {'.', '..'} or part.endswith((' ', '.'))
                or re.search(r'[<>:"\\|?*\x00-\x1f]', part)
                or re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]', part.split('.')[0].rstrip(), re.I)):
            raise ValueError('请检查包内源文件路径')
    target = archive_root.joinpath(*parts).resolve()
    if not target.is_relative_to(archive_root):
        raise ValueError('请检查包内源文件路径')
    return target


def read_package(bundle):
    entries = bundle.namelist()
    if len(set(entries)) != len(entries):
        raise ValueError('ZIP 内有重复文件名')
    if '导出清单.json' not in entries:
        raise ValueError('请选择 FireKB 导出的知识点 ZIP')
    manifest = json.loads(bundle.read('导出清单.json'))
    if not isinstance(manifest, dict) or manifest.get('version') != 1:
        raise ValueError('请选择 FireKB 导出的知识点 ZIP')
    courses = manifest.get('courses')
    if not isinstance(courses, list) or not courses:
        raise ValueError('ZIP 内缺少科目清单')
    points, evidence = [], []
    for course in courses:
        folder = course['directory']
        parts = PurePosixPath(folder).parts
        if len(parts) != 2 or parts[0] != '科目' or parts[1] in {'.', '..'} or '\\' in folder:
            raise ValueError('请检查包内科目目录')
        rows = read_rows(bundle, folder + '/知识点.jsonl')
        if any((row.get('course') or '未分类') != course['course'] for row in rows):
            raise ValueError('包内知识点与科目清单不一致')
        points.extend(rows)
        evidence.extend(read_rows(bundle, folder + '/引用证据.jsonl'))
    point_ids = unique_rows(points, 'kp_id')
    if not point_ids:
        raise ValueError('ZIP 内暂无知识点')
    unique_rows(evidence, 'evidence_id')
    pages = read_rows(bundle, '原文定位.jsonl') if '原文定位.jsonl' in entries else []
    audits = read_rows(bundle, '核验记录.jsonl') if '核验记录.jsonl' in entries else []
    unique_rows(audits, 'audit_id')
    if any(row.get('kp_id') not in point_ids for row in evidence + audits):
        raise ValueError('包内引用或核验记录的知识点编号无效')
    sources = manifest.get('source_entries', {})
    if not isinstance(sources, dict):
        raise ValueError('请检查包内源文件清单')
    return points, evidence, pages, audits, sources


def import_knowledge(input_path):
    archive_root = kb.ARCHIVE.resolve()
    staged = {}
    published = []
    with ZipFile(input_path) as bundle, kb.writer_lock():
        points, evidence, pages, audits, source_entries = read_package(bundle)
        for row in points + evidence + pages:
            source = row.get('source_file')
            if source:
                if not isinstance(source, str):
                    raise ValueError('请检查包内引用路径')
                source_target('引用源文件/' + source.replace('\\', '/'), archive_root)
        current_points = kb.load_jsonl(kb.KP_FILE)
        current_ids = {row['kp_id'] for row in current_points}
        additions = [row for row in points if row['kp_id'] not in current_ids]
        ids = {row['kp_id'] for row in additions}
        result = {'added': len(additions), 'skipped': len(points) - len(additions),
                  'source_count': 0, 'source_conflicts': 0, 'pending_count': 0}
        if not additions:
            return result

        new_evidence = [row for row in evidence if row['kp_id'] in ids]
        new_audits = [row for row in audits if row['kp_id'] in ids]
        current_evidence = kb.load_jsonl(kb.EVIDENCE_FILE)
        current_audits = kb.load_jsonl(kb.AUDIT_FILE)
        for existing, incoming, key in [(current_evidence, new_evidence, 'evidence_id'), (current_audits, new_audits, 'audit_id')]:
            if {row.get(key) for row in existing} & {row[key] for row in incoming}:
                raise ValueError(f'包内记录编号与当前知识库冲突 {key}')
        by_point = defaultdict(list)
        for row in new_evidence:
            by_point[row['kp_id']].append(row)
        needed_sources = {row['source_file'] for row in additions + new_evidence if row.get('source_file')}
        conflicts = set()
        current_pages = kb.load_jsonl(kb.PAGES_FILE)
        page_index = {(row.get('source_file'), row.get('page_no')): row for row in current_pages}
        new_pages = []
        try:
            for source in sorted(needed_sources & source_entries.keys()):
                entry = source_entries[source]
                target = source_target(entry, archive_root)
                if target != (archive_root / source).resolve():
                    raise ValueError('包内源文件路径与引用不一致')
                if target in staged:
                    continue
                with bundle.open(entry) as incoming:
                    if target.exists():
                        with target.open('rb') as existing:
                            if hashlib.file_digest(incoming, 'sha256').digest() != hashlib.file_digest(existing, 'sha256').digest():
                                conflicts.add(source)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.firekb-import-', delete=False) as temp:
                        staged[target] = Path(temp.name)
                        shutil.copyfileobj(incoming, temp, length=1024 * 1024)
            for row in pages:
                source = row.get('source_file')
                if source not in needed_sources or source in conflicts:
                    continue
                key = (source, row.get('page_no'))
                previous = page_index.get(key)
                if previous is None:
                    page_index[key] = row
                    new_pages.append(row)
                elif previous.get('text') != row.get('text'):
                    conflicts.add(source)
            if conflicts:
                new_pages = [row for row in new_pages if row['source_file'] not in conflicts]
            for row in additions:
                references = by_point[row['kp_id']]
                row['evidence_ids'] = [entry['evidence_id'] for entry in references]
                source_text = source_text_of(row, references, page_index)
                revision = review_state.revision(row, references, source_text)
                source_conflict = any(record.get('source_file') in conflicts for record in [row, *references])
                if row.get('status') == 'verified' and (source_conflict or not source_text or not review_state.content_decision_accepted(row, new_audits, revision)):
                    row.update(status='pending', requires_content_review=True, verification_method='human')
                    row.pop('human_review', None)
                    row.pop('verified_at', None)
                    result['pending_count'] += 1
            outputs = {kb.KP_FILE: current_points + additions, kb.EVIDENCE_FILE: current_evidence + new_evidence,
                       kb.AUDIT_FILE: current_audits + new_audits, kb.PAGES_FILE: current_pages + new_pages}
            backup = kb.backup_files(outputs, 'knowledge_import')
            conflicting_paths = {(archive_root / source).resolve() for source in conflicts}
            for target, temp in staged.items():
                if target in conflicting_paths:
                    continue
                # Both operations publish without replacing an existing source.
                if os.name == 'nt':
                    temp.rename(target)
                else:
                    os.link(temp, target)
                published.append(target)
            kb.atomic_dataset(outputs)
        except BaseException:
            if not (kb.ROOT / '.firekb-transaction.json').exists():
                for target in published:
                    target.unlink(missing_ok=True)
            raise
        finally:
            for temp in staged.values():
                temp.unlink(missing_ok=True)
        result.update(source_count=len(published), source_conflicts=len(conflicts), backup=str(backup))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    args = parser.parse_args()
    try:
        result = import_knowledge(args.input)
    except (BadZipFile, KeyError, TypeError, OSError, ValueError, RuntimeError) as error:
        parser.exit(2, f'导入失败：{error}\n')
    print(json.dumps(result, ensure_ascii=False))
