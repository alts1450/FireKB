"""Course-scoped, fail-fast pipeline with content-checked resume."""
import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path
import kb

ORDER = ['parse', 'ocr', 'transcribe', 'skeleton', 'skeleton_review', 'extract', 'verify', 'quality', 'review', 'export']
CODE = Path(__file__).resolve().parents[1]


def digest(paths):
    out = {}
    for p in paths:
        if p.is_file():
            with p.open('rb') as f:
                out[str(p)] = hashlib.file_digest(f, 'sha256').hexdigest()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--course')
    ap.add_argument('--skip-ocr', action='store_true')
    ap.add_argument('--with-asr', action='store_true', help='包含本机课堂录音转写（已有文件跳过）')
    ap.add_argument('--quality-ai', action='store_true', help='增加 60 条分层 AI 内容复核；按复核结果重新校验')
    ap.add_argument('--pdf', action='store_true', help='课程导出时生成 PDF')
    ap.add_argument('--only', choices=ORDER)
    ap.add_argument('--from', dest='start', choices=ORDER)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--extract-limit', type=int)
    ap.add_argument('--skeleton-mode', choices=['reuse', 'rebuild'], default='reuse')
    ap.add_argument('--review-skeleton', action='store_true')
    ap.add_argument('--resume', action='store_true')
    args = ap.parse_args()
    course = kb.resolve_course(args.course)
    if args.only in {'export','quality'} and not course:
        ap.error('该环节需要 --course')
    todo = [s for s in ORDER if not args.only or s == args.only]
    if args.start:
        todo = [s for s in todo if ORDER.index(s) >= ORDER.index(args.start)]
    if args.skeleton_mode == 'reuse' and 'skeleton' in todo:
        print('保留已有骨架，为新文件追加章节；完整重建使用 --skeleton-mode rebuild')
    if not args.review_skeleton and args.only != 'skeleton_review':
        todo = [s for s in todo if s != 'skeleton_review']
    if args.skip_ocr:
        todo = [s for s in todo if s != 'ocr']
    if not args.with_asr and args.only != 'transcribe':
        todo = [s for s in todo if s != 'transcribe']
    elif args.only != 'transcribe':
        from transcribe import AUDIO_EXTS
        audio = [p for p in (kb.ARCHIVE/'录音').rglob('*') if p.is_file() and p.suffix.lower() in AUDIO_EXTS and (not course or course in p.parts)]
        if not audio: todo = [s for s in todo if s != 'transcribe']
    if not args.quality_ai and args.only != 'quality':
        todo = [s for s in todo if s != 'quality']
    if not course:
        todo = [s for s in todo if s not in {'quality','export'}]
    print('课程：', course or '全部', '；计划：', ' → '.join(todo))
    if args.dry_run:
        return 0
    params = {k:v for k,v in vars(args).items() if k not in ('resume', 'dry_run')}
    inputs = digest([*kb.ARCHIVE.rglob('*'), *kb.CONFIG_DIR.glob('*.json'), *CODE.glob('tools/*.py')])
    state_file = kb.LOG_DIR/'pipeline'/f'{kb.course_slug(course) if course else "all"}.json'
    state = {'run_id': kb.new_run_id('PIPE'), 'params': params, 'inputs': inputs, 'completed': [], 'plan': todo}
    artifacts = lambda: digest([p for d in (kb.TEXT_DIR, kb.KP_DIR) for p in d.glob('*')])
    if args.resume:
        old = kb.load_json(state_file, {})
        if old.get('params') != params or old.get('inputs') != inputs or old.get('artifacts') != artifacts():
            print('[STOP] 没有匹配的续跑记录，或输入/产物已变化；去掉 --resume 开始新运行。')
            return 3
        state = old
        todo = [s for s in state['plan'] if s not in state['completed']]
    for stage in todo:
        cmd = [sys.executable, '-B', '-m', 'firekb', 'review-skel' if stage == 'skeleton_review' else stage]
        if course:
            cmd += ['--course', course]
        if stage == 'skeleton_review':
            cmd += ['--apply']
        if stage == 'skeleton' and args.skeleton_mode == 'reuse': cmd += ['--append-new']
        if stage == 'extract' and args.extract_limit:
            cmd += ['--limit', str(args.extract_limit)]
        if stage == 'transcribe': cmd += ['--no-vad']
        if stage == 'quality' and args.quality_ai: cmd += ['--ai']
        if stage == 'export' and args.pdf: cmd += ['--pdf']
        proc = subprocess.run(cmd, cwd=CODE, env={**os.environ, 'FIREKB_ROOT': str(kb.ROOT)})
        if stage == 'quality' and args.quality_ai and proc.returncode == 0:
            proc = subprocess.run([sys.executable, '-B', '-m', 'firekb', 'verify', '--course', course],
                                  cwd=CODE, env={**os.environ, 'FIREKB_ROOT': str(kb.ROOT)})
        state['artifacts'] = artifacts()
        state['last_stage'] = stage
        state['exit_code'] = proc.returncode
        if proc.returncode == 0:
            state['completed'].append(stage)
        kb.write_json(state_file, state)
        if proc.returncode != 0:
            print(f'[STOP] {stage} 失败（{proc.returncode}），后续步骤停止；记录：{state_file}')
            return proc.returncode
    print('流水线完成；记录：', state_file)
    return 0


if __name__ == '__main__':
    with kb.writer_lock():
        sys.exit(main())
