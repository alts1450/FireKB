"""Export a course as a searchable reading edition, Markdown and optional PDF."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from html import escape
import hashlib
import json
from pathlib import Path
import re
import sys

import kb
from make_review import short_src
from verify_kp import label_to_page_no, build_page_index, source_text_of
import review_state
from math_format import display, html_text


def inventory(course, pages):
    grouped = defaultdict(list)
    for page in pages:
        grouped[page['source_file']].append(page)
    sources = []
    for source, rows in sorted(grouped.items()):
        path = kb.ARCHIVE / source
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        with path.open('rb') as stream:
            source_sha1 = hashlib.file_digest(stream, 'sha1').hexdigest()
        expected = {r.get('source_sha1') for r in rows} - {None, ''}
        if expected and expected != {source_sha1}:
            raise ValueError('原始文件已变化，请先解析并处理变更后导出：'+source)
        sources.append({'source_file': source, 'kind': rows[0].get('source_kind'),
                        'locations': len(rows), 'sha256': digest})
    return sources


def build_content(course):
    all_kps = [k for k in kb.load_jsonl(kb.KP_FILE) if k.get('course') == course]
    usable = [k for k in all_kps if k.get('status') == 'verified' and not kb.is_experimental_kp(k)
              and not kb.is_reference_source(k.get('source_file', ''))]
    if not usable:
        raise ValueError('本课程没有可导出的已校验知识点，请先完成 extract / verify')
    ids = {k['kp_id'] for k in usable}
    evidence = defaultdict(list)
    for e in kb.load_jsonl(kb.EVIDENCE_FILE):
        if e.get('kp_id') in ids:
            evidence[e['kp_id']].append(e)
    pages = [p for p in kb.load_jsonl(kb.PAGES_FILE) if p.get('course') == course]
    lookup = {(p['source_file'], p['page_no']): p for p in pages}
    errors = []
    audits=kb.load_jsonl(kb.AUDIT_FILE)
    index = build_page_index()
    for k in usable:
        evs = evidence[k['kp_id']]
        rev = review_state.revision(k, evs, source_text_of(k, evs, index))
        if not review_state.content_decision_accepted(k,audits,rev):
            errors.append(k['kp_id']+': 内容核验记录失效，请先 verify / ai-review')
        if not evs:
            errors.append(k['kp_id'] + ': 缺少证据')
        for e in evs:
            if (e.get('source_file'), label_to_page_no(e.get('page_label'))) not in lookup:
                errors.append(e['evidence_id'] + ': 出处不存在')
            if not review_state.evidence_accepted(e, rev):
                errors.append(e['evidence_id'] + ': 引用未通过校验')
    if errors:
        raise ValueError('导出质量门槛未通过：\n' + '\n'.join(errors[:20]))

    # Preserve differing editions as separate entries; never choose a number by recency guesses.
    by_name = defaultdict(list)
    for k in usable:
        by_name[re.sub(r'\s+', '', k.get('name', ''))].append(k)
    differences = []
    for name, members in sorted(by_name.items()):
        numeric = [m for m in members if m.get('numbers')]
        if len({tuple(sorted(map(str, m['numbers']))) for m in numeric}) > 1:
            differences.append({'name': name, 'entries': [{'kp_id': m['kp_id'], 'numbers': m['numbers'],
                                                           'source': short_src(m)} for m in numeric]})
    chapters = defaultdict(list)
    skeleton = kb.load_json(kb.SKELETON_FILE, {}).get('courses', {}).get(course, {})
    order = {(ch.get('source_file'), ch.get('title')): i for i, ch in enumerate(skeleton.get('chapters', []))}
    usable.sort(key=lambda k: (order.get((k.get('source_file'), k.get('chapter')), 9999),
                               k.get('source_file', ''), k.get('chapter', ''), k['kp_id']))
    for k in usable:
        chapters[(k.get('source_file'), k.get('chapter') or '未分章')].append(k)
    transcript = [t for t in kb.load_jsonl(kb.TRANSCRIPT_FILE) if t.get('course') == course]
    sources = inventory(course, pages)
    info = {'course': course, 'generated_at': kb.now_iso(), 'status_counts': dict(Counter(k.get('status') for k in all_kps)),
            'included': len(usable), 'evidence': sum(map(len, evidence.values())), 'sources': sources,
            'numeric_differences': differences, 'transcript_segments': len(transcript),
            'transcript_policy': '录音仅作教学脉络参考，不作知识点或数值证据',
            'ai_verified':sum(k.get('verification_method')=='ai_web' for k in usable),
            'verification_scope': '本地来源规则校验或AI联网证据核验（有单独评分标记）；不等于人工审定或现行工程设计依据'}
    return info, usable, evidence, chapters, pages


def documents(info, usable, evidence, chapters, pages):
    course = info['course']
    preface = ('本册按提供的教材和课件归纳，用于本课程学习。已校验表示通过本地来源规则校验或带评分标记的AI联网核验，'
               '不等于内容已经人工审定。不同资料中的数值和条件应连同原文一起理解；'
               '旧教材条款不自动视为现行设计要求。待确认条目单独列出，未进入本册正文。')
    md = [f'# {course} · 知识点归纳', '', preface, '',
          f"生成时间：{info['generated_at']}  |  正文 {len(usable)} 条  |  引用 {info['evidence']} 条", '',
          '## 资料目录', '']
    html = [f'<header><p class="eyebrow">FireKB / 课程阅读版</p><h1>{escape(course)}</h1><p>{escape(preface)}</p>',
            f'<p class="meta">正文 {len(usable)} 条 · 引用 {info["evidence"]} 条 · {escape(info["generated_at"])}</p></header>',
            '<nav><a href="01_枝干速览.md">枝干速览</a><a href="02_背诵卡.md">背诵卡</a><a href="03_数字对照表.md">数字表</a><a href="05_anki导入.tsv">Anki</a><a href="00_待确认清单.md">待确认</a></nav>',
            '<input id="search" type="search" placeholder="搜索知识点、原文或出处…" aria-label="搜索课程内容"><p id="count" class="meta"></p>',
            '<details><summary>资料目录与页码说明</summary><ul>']
    for source in info['sources']:
        unit = '段' if source['kind'] == 'docx' else '页/幻灯片'
        md.append(f"- {source['source_file']}（{source['locations']} {unit}）")
        html.append(f'<li>{escape(source["source_file"])} · {source["locations"]} {unit}</li>')
    html.append('</ul><p>PDF 使用原文件物理页，并在已有映射时显示印刷页；PPT 使用幻灯片号；Word 使用段落号。</p></details>')
    if info['numeric_differences']:
        md += ['', '## 同名知识点的数值差异', '', '以下是待对照提示，差异也可能来自适用条件或摘录粒度。各条保留原出处，不自动合并数值。', '']
        html.append('<details open><summary>同名知识点的数值差异（需连同适用条件对照）</summary>')
        for diff in info['numeric_differences']:
            md.append(f"- **{diff['name']}**")
            html.append(f'<p><strong>{escape(diff["name"])}</strong></p><ul>')
            for entry in diff['entries']:
                line = f"{entry['kp_id']}：{'、'.join(map(str, entry['numbers']))} / {entry['source']}"
                md.append('  - ' + line); html.append('<li>' + escape(line) + '</li>')
            html.append('</ul>')
        html.append('</details>')
    for (_, chapter), members in chapters.items():
        md += ['', f'## {chapter}', '']
        html.append(f'<h2>{escape(chapter)}</h2>')
        for k in members:
            source = short_src(k)
            md += [f"### {k['name']}", '', display(k), '',
                   f"出处：{source}  |  {k['kp_id']}  |  重要度 {k.get('importance', 3)}/5", '']
            html += [f'<article><h3>{escape(k["name"])}</h3><p>{html_text(display(k))}</p>',
                     f'<p class="meta">{escape(source)} · {escape(k["kp_id"])} · 重要度 {escape(str(k.get("importance", 3)))}/5</p>']
            if k.get('verification_method')=='ai_web':
                receipt=k['ai_verification']
                note=f"AI 联网核验 {receipt['score']:.1%}（证据评分） · {receipt.get('scope','')} · 有效至 {receipt.get('valid_until','')[:10]}"
                md += [note,''];html.append('<p class="meta">'+escape(note)+'</p><ul>')
                for s in receipt.get('sources',[]):
                    if not kb.is_http_url(s.get('url')):
                        continue
                    md += [f"- [{s['title']}]({s['url']})"]
                    html.append(f'<li><a target="_blank" rel="noopener noreferrer" href="{escape(s["url"],quote=True)}">{escape(s["title"])}</a></li>')
                md+=[''];html.append('</ul>')
            if review_state.human_label(k):
                label=review_state.human_label(k)
                md += [label,''];html.append('<p class="meta">'+escape(label)+'</p>')
            for label, field in [('数值', 'numbers'), ('提示', 'note')]:
                value = k.get(field)
                if value:
                    value = '、'.join(map(str, value)) if isinstance(value, list) else str(value)
                    md += [f'{label}：{value}', '']; html.append(f'<p><b>{label}：</b>{escape(value)}</p>')
            for e in evidence[k['kp_id']][:2]:
                mark=' · 本地引文未精确匹配，内容以联网证据核验' if k.get('verification_method')=='ai_web' and not e.get('quote_verified') else ''
                md += [f"> {e['quote']} / {e['page_label']}{mark}", '']
                html.append(f'<blockquote>{escape(e["quote"])}<small>{escape(e["page_label"]+mark)}</small></blockquote>')
            html.append('</article>')
    exercises = [p for p in pages if p.get('source_kind') == 'docx']
    md += ['', '## 补充练习题索引', '', '以下保留原题和段落定位；没有标准答案的题目不自动编造答案。', '']
    for p in exercises:
        md += [f"- {p['page_label']}：{p['text']}（{p['source_file']}）", '']
    if not exercises: md.append('当前没有已解析的 Word 练习题。')
    html.append('<details><summary>补充练习题索引</summary>')
    for p in exercises:
        html.append(f'<p>{escape(p["page_label"])}：{escape(p["text"])}</p>')
    html.append('</details>')
    style = 'body{font-family:"Microsoft YaHei",system-ui,sans-serif;max-width:920px;margin:48px auto;padding:0 28px;color:#26313e;line-height:1.85;background:#fff}h1{font-size:32px}h2{margin-top:54px;border-bottom:1px solid #dce5ed;padding-bottom:10px}h3{font-size:18px}article{padding:20px 0;border-bottom:1px solid #e8edf2}.meta,small{font-size:12px;color:#637184}.eyebrow{color:#46789c;letter-spacing:2px}blockquote{margin:14px 0;padding:12px 20px;background:#f4f7fa;border-left:3px solid #9ebed3}small{display:block}nav{display:flex;gap:20px;flex-wrap:wrap;margin:24px 0}a{color:#326b94}input{width:100%;box-sizing:border-box;padding:13px;border:1px solid #bacbd8;border-radius:6px;font:inherit}details{margin:22px 0}summary{cursor:pointer;font-weight:600}[hidden]{display:none!important}@media print{nav,input,#count{display:none}body{margin:0;max-width:none}h2{break-before:page}article{break-inside:avoid}}'
    script = 'const items=[...document.querySelectorAll("article")];document.querySelector("#search").addEventListener("input",e=>{let q=e.target.value.trim().toLocaleLowerCase(),n=0;for(const a of items){a.hidden=!a.textContent.toLocaleLowerCase().includes(q);if(!a.hidden)n++}document.querySelector("#count").textContent=`找到 ${n} 条知识点`});'
    return '\n'.join(md), '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(course)+'</title><link rel="stylesheet" href="math-assets/katex.min.css"><style>'+style+'</style>'+''.join(html)+'<script src="math-assets/katex.min.js"></script><script>document.querySelectorAll(".math-tex").forEach(e=>katex.render(e.textContent,e,{throwOnError:true,trust:false}));</script><script>'+script+'</script></html>'


def pdf_definition(text, styles):
    import io, base64
    from reportlab.platypus import Paragraph, Image, Spacer
    from matplotlib.mathtext import math_to_image
    from matplotlib.font_manager import FontProperties
    result, prose = [], []
    def flush():
        if prose:
            result.append(Paragraph(''.join(prose), styles['Normal']))
            prose.clear()
    for part in re.split(r'(\$[^$]+\$)', text):
        if not part: continue
        if not part.startswith('$'):
            prose.append(escape(part)); continue
        block = any(token in part for token in ['=', r'\geq', r'\leq'])
        buf=io.BytesIO()
        math_to_image(part,buf,prop=FontProperties(size=15 if block else 9),dpi=240,format='png',color='#243448')
        buf.seek(0)
        im=Image(buf); scale=min(0.30,490/im.imageWidth)
        width,height=im.imageWidth*scale,im.imageHeight*scale
        if block:
            flush(); im.drawWidth=width;im.drawHeight=height;im.hAlign='LEFT'
            result.extend([Spacer(1,5),im,Spacer(1,5)])
        else:
            asset=kb.LOG_DIR/'pdf_math'/(hashlib.sha256(part.encode()).hexdigest()+'.png')
            asset.parent.mkdir(parents=True,exist_ok=True)
            asset.write_bytes(buf.getvalue())
            uri=asset.resolve().as_posix()
            prose.append(f'<img src="{uri}" width="{width}" height="{height}" valign="middle"/>')
    flush()
    return result


def write_pdf(path, info, chapters, evidence):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, KeepTogether
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        pdfmetrics.registerFont(TTFont('FireKBCJK', str(font), subfontIndex=0))
    else:
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    face = 'FireKBCJK' if font.exists() else 'STSong-Light'
    styles = getSampleStyleSheet()
    for name in ['Normal', 'Title', 'Heading1', 'Heading2', 'BodyText']:
        styles[name].fontName = face
        styles[name].wordWrap = 'CJK'
    styles['Normal'].fontSize = 9; styles['Normal'].leading = 15
    styles['Title'].fontSize = 25; styles['Title'].leading = 38
    styles['Heading1'].fontSize = 17; styles['Heading1'].leading = 26
    styles['Heading2'].fontSize = 12; styles['Heading2'].leading = 19
    styles.add(ParagraphStyle('Source', fontName=face, fontSize=7.5, leading=12, textColor=colors.HexColor('#627287'), wordWrap='CJK'))
    styles.add(ParagraphStyle('QuoteCN', parent=styles['Normal'], fontSize=8, leading=13, leftIndent=12, textColor=colors.HexColor('#526678')))
    story = [Spacer(1, 90), Paragraph(escape(info['course']), styles['Title']),
             Paragraph('课程知识点归纳', styles['Heading1']), Spacer(1, 25),
             Paragraph(f"正文 {info['included']} 条 / 引用 {info['evidence']} 条", styles['Normal']), Spacer(1, 15),
             Paragraph('本册依据所提供的教材与课件归纳。条目通过本地来源规则校验或带评分标记的AI联网核验。AI评分不是统计正确概率；旧教材数值不自动视为现行设计要求。待确认条目未进入正文。', styles['Normal']),
             Spacer(1, 15), Paragraph(escape(info['generated_at']), styles['Source']), PageBreak(),
             Paragraph('阅读路径与资料', styles['Heading1'])]
    for source in info['sources']:
        story += [Paragraph(escape(source['source_file']), styles['Normal']), Paragraph(f"{source['locations']} 个页/段定位", styles['Source']), Spacer(1, 8)]
    if info['numeric_differences']:
        story += [Spacer(1, 15), Paragraph('同名知识点存在数值差异', styles['Heading2']),
                  Paragraph('请先在 HTML 阅读版查看数值差异提示，并连同原文条件核对。正文保留不同来源各自的记录。', styles['Normal'])]
    for (_, chapter), members in chapters.items():
        story += [PageBreak(), Paragraph(escape(chapter), styles['Heading1'])]
        for k in members:
            story.append(KeepTogether([Spacer(1, 12), Paragraph(escape(k['name']), styles['Heading2']),
                                       Paragraph(escape(short_src(k))+' / '+escape(k['kp_id']), styles['Source'])]))
            story.extend(pdf_definition(display(k), styles))
            if k.get('numbers'):
                story.append(Paragraph('数值：'+escape('、'.join(map(str, k['numbers']))), styles['Normal']))
            if k.get('note'):
                story.append(Paragraph('提示：'+escape(k['note']), styles['Normal']))
            if k.get('verification_method')=='ai_web':
                receipt=k['ai_verification']
                story.append(Paragraph(escape(f"AI 联网核验 {receipt['score']:.1%}（证据评分） · {receipt.get('scope','')}"),styles['Source']))
                for s in receipt.get('sources',[]):
                    if not kb.is_http_url(s.get('url')):
                        continue
                    story.append(Paragraph(f'<link href="{escape(s["url"],quote=True)}" color="#326b94">'+escape(s['title'])+'</link>',styles['Source']))
            if review_state.human_label(k):story.append(Paragraph(escape(review_state.human_label(k)),styles['Source']))
            for e in evidence[k['kp_id']][:1]:
                mark=' · 本地引文未精确匹配，内容以联网证据核验' if k.get('verification_method')=='ai_web' and not e.get('quote_verified') else ''
                story += [Spacer(1, 5), Paragraph(escape(e['quote'])+' / '+escape(e['page_label']+mark), styles['QuoteCN'])]
    def footer(canvas, doc):
        canvas.setFont(face, 7); canvas.setFillColor(colors.HexColor('#718093'))
        canvas.drawString(42, 25, info['course'] + ' · FireKB')
        canvas.drawRightString(A4[0]-42, 25, str(doc.page))
    temp = path.with_suffix('.tmp.pdf')
    try:
        SimpleDocTemplate(str(temp), pagesize=A4, rightMargin=42, leftMargin=42, topMargin=40, bottomMargin=42,
                          title=info['course']+' 知识点归纳', author='FireKB').build(story, onFirstPage=footer, onLaterPages=footer)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--course', required=True)
    ap.add_argument('--pdf', action='store_true')
    args = ap.parse_args()
    course = kb.resolve_course(args.course)
    info, usable, evidence, chapters, pages = build_content(course)
    out = kb.REVIEW_DIR/kb.course_slug(course)
    import shutil
    assets = Path(__file__).resolve().parent/'assets/katex'
    out.mkdir(parents=True,exist_ok=True)
    shutil.copytree(assets, out/'math-assets', dirs_exist_ok=True)
    md, html = documents(info, usable, evidence, chapters, pages)
    kb.atomic_text(out/'06_课程知识点归纳.md', md)
    kb.atomic_text(out/'课程阅读版.html', html)
    kb.write_json(out/'course-manifest.json', info)
    kb.write_json(out/'math-display.json', {k['kp_id']:{'source_definition':k.get('definition',''),'formatted':display(k)} for k in kb.load_jsonl(kb.KP_FILE) if k.get('course')==course})
    transcript = [t for t in kb.load_jsonl(kb.TRANSCRIPT_FILE) if t.get('course') == course]
    lines = ['# 课堂录音转写索引', '', '自动转写只作教学脉络参考，不作为知识点、数字表或引用证据。A/B/C 为模型置信度分档。', '']
    grouped = defaultdict(list)
    for t in transcript: grouped[t['source_file']].append(t)
    def clock(seconds):
        seconds = int(seconds or 0)
        return f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}'
    for source, rows in sorted(grouped.items()):
        lines += [f'## {source}', '', f'共 {len(rows)} 段，时间戳相对于该文件起点。', '']
        for t in sorted(rows, key=lambda r: r['start']):
            lines += [f"[{clock(t['start'])}-{clock(t['end'])}] {t.get('confidence','?')} | {t['text']}", '']
    if not transcript: lines.append('本课程暂无已完成的录音转写。可运行 transcribe --course <课程ID>。')
    kb.atomic_text(out/'07_课堂录音索引.md', '\n'.join(lines))
    if args.pdf:
        write_pdf(out/'课程知识点归纳.pdf', info, chapters, evidence)
    print(f"已导出 {course}：{len(usable)} 条知识点，{info['evidence']} 条引用；{out}")
    return 0


if __name__ == '__main__':
    with kb.writer_lock():
        sys.exit(main())
