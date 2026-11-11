"""Verify/restore hashed backups; report reproducible runtime versions."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import kb


def verify(backup):
    backup=Path(backup).resolve()
    manifest=json.loads((backup/'manifest.json').read_text(encoding='utf-8-sig'))
    paths=[]
    for entry in manifest['files']:
        source=(backup/'files'/entry['path']).resolve()
        target=(kb.ROOT/entry['path']).resolve()
        if not source.is_relative_to(backup/'files') or not target.is_relative_to(kb.ROOT.resolve()):
            raise ValueError('备份清单包含越界路径')
        with source.open('rb') as f:
            if hashlib.file_digest(f,'sha256').hexdigest()!=entry['sha256']:
                raise ValueError('备份校验失败：'+entry['path'])
        paths.append((source,target))
    return paths


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--verify',type=Path)
    ap.add_argument('--restore',type=Path)
    ap.add_argument('--yes',action='store_true')
    args=ap.parse_args()
    if args.verify or args.restore:
        paths=verify(args.verify or args.restore)
        print('备份清单校验通过：',len(paths),'个文件')
        if args.restore:
            if not args.yes:
                print('恢复预览：将覆盖以上清单对应文件；加 --yes 执行，执行前自动备份当前版本。')
                return 0
            kb.backup_files([target for _,target in paths],'pre_restore')
            for source,target in paths:
                # Binary-safe atomic replacement; original raw archive is never removed.
                import os,tempfile,shutil
                target.parent.mkdir(parents=True,exist_ok=True)
                fd,tmp=tempfile.mkstemp(dir=target.parent)
                os.close(fd)
                try:
                    shutil.copy2(source,tmp);os.replace(tmp,target)
                finally:Path(tmp).unlink(missing_ok=True)
            print('已恢复清单中的文件；新增文件未删除。')
        return 0
    print('Python',sys.version.split()[0],sys.executable)
    for n in ['pypdf','pypdfium2','python-pptx','rapidocr-onnxruntime','faster-whisper','ctranslate2','onnxruntime','reportlab','tzdata']:
        try:print(n,importlib.metadata.version(n))
        except importlib.metadata.PackageNotFoundError:print(n,'MISSING')
    return 0


if __name__=='__main__':
    with kb.writer_lock():sys.exit(main())
