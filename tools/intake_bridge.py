"""JSON stdin/stdout bridge for the existing Electron desktop application."""
import json
from pathlib import Path
import sys
import kb
import timetable as tt


def handle(action, data):
    if action=='state':return {'timetable':tt.load(),'courses':kb.load_courses()}
    if action=='save':return {'timetable':tt.save(data['timetable'],data['courses']),'courses':kb.load_courses()}
    if action=='parse':
        config,courses=tt.parse_import(data['text'],data['name'],data['timetable'],data['courses'])
        return {'timetable':config,'courses':courses}
    if action=='preview':
        config=tt.load();files=[];seen=set()
        for raw in data.get('paths',[]):
            p=Path(raw).resolve()
            if not p.exists():raise ValueError('素材路径不存在：'+str(p))
            for file in p.rglob('*') if p.is_dir() else [p]:
                if file.is_file() and file.suffix.lower() in tt.AUDIO|tt.DOCS and str(file) not in seen:
                    seen.add(str(file))
                    files.append({'name':file.name,'path':str(file),'last_modified':file.stat().st_mtime*1000,'size':file.stat().st_size})
                    if len(files)>5000:raise ValueError('每批最多 5000 个文件')
        if not config['semester_start']:
            return {'files':[{**x,'course_id':None,'candidates':[],'status':'unmatched','reason':'请先设置课表，也可手动指定课程','kind':'录音' if Path(x['name']).suffix.lower() in tt.AUDIO else '课件'} for x in files]}
        return {'files':[tt.classify(x,config) for x in files]}
    if action=='import':
        rows=data['files'];results=[]
        if len(rows)>5000:raise ValueError('每批最多 5000 个文件')
        for row in rows:
            try:
                source=Path(row['path']).resolve()
                if not source.is_file():raise ValueError('源文件不存在')
                result=tt.archive_file(source,source.name,row['course_id'],row['kind'])
                results.append({'source':str(source),**result})
            except (ValueError,OSError,RuntimeError) as exc:
                results.append({'source':row.get('path'),'status':'error','error':str(exc)})
        return {'files':results}
    raise ValueError('未知归课操作')


def main():
    try:
        request=json.load(sys.stdin)
        result=handle(request['action'],request.get('data',{}))
        print(json.dumps({'success':True,'data':result},ensure_ascii=False))
        return 0
    except (ValueError,KeyError,OSError,RuntimeError) as exc:
        print(json.dumps({'success':False,'error':str(exc)},ensure_ascii=False))
        return 2


if __name__=='__main__':
    # Read actions remain available while a long-running pipeline owns the writer.
    sys.exit(main())
