"""Create a compact, indexed A4 revision book from the verified course edition."""
from __future__ import annotations

import argparse
from collections import OrderedDict
import hashlib
from html import escape
import io
import re
from pathlib import Path

import kb
from export_course import build_content
from make_review import short_src
from math_format import display


def prose_xml(text):
    value=escape(str(text))
    # SimSun omits these engineering symbols; retain their actual Unicode glyphs.
    for char in ['⁻','∙']:value=value.replace(char,f'<font name="PrintSymbol">{char}</font>')
    return value


def chapter_number(title):
    match=re.search(r'第\s*([0-9一二三四五六七八九十]+)\s*章',title)
    if not match:return 999
    value=match[1]
    if value.isdigit():return int(value)
    return {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,'十一':11}.get(value,999)


def organize(chapters):
    parts=[]
    for label,slide in [('第一部分　教材系统复习',False),('第二部分　课堂课件补充',True)]:
        groups=OrderedDict()
        entries=[(key,items) for key,items in chapters.items() if key[0].lower().endswith('.pptx')==slide]
        entries.sort(key=lambda entry:(chapter_number(entry[0][1]),entry[0][0],entry[0][1]))
        for (source,title),items in entries:
            if slide and chapter_number(title)==3:title='第三章 消防给水设备（课件1、2）'
            else:title=re.sub(r'（续）$','',title).strip()
            groups.setdefault(title,[]).extend(items)
        if groups:parts.append((label,groups))
    return parts


def fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    pdfmetrics.registerFont(TTFont('PrintCN','C:/Windows/Fonts/simsun.ttc',subfontIndex=0))
    pdfmetrics.registerFont(TTFont('PrintHead','C:/Windows/Fonts/simhei.ttf'))
    pdfmetrics.registerFont(TTFont('PrintSymbol','C:/Windows/Fonts/arial.ttf'))


def math_prose(text,style,width,font_size):
    """Keep equations at natural point size, fitting only oversized expressions."""
    from reportlab.platypus import Paragraph,Image,Spacer
    from matplotlib.mathtext import math_to_image
    from matplotlib.font_manager import FontProperties
    result=[];prose=[]
    def flush():
        if prose:result.append(Paragraph(''.join(prose),style));prose.clear()
    for part in re.split(r'(\$[^$]+\$)',text):
        if not part:continue
        if not part.startswith('$'):prose.append(prose_xml(part));continue
        block=any(token in part for token in ['=',r'\geq',r'\leq'])
        points=10 if block else font_size
        asset=kb.LOG_DIR/'print-review-math'/(hashlib.sha256((str(points)+part).encode()).hexdigest()+'.png')
        if not asset.exists():
            stream=io.BytesIO()
            math_to_image(part,stream,prop=FontProperties(size=points),dpi=300,format='png',color='black')
            asset.parent.mkdir(parents=True,exist_ok=True);asset.write_bytes(stream.getvalue())
        im=Image(str(asset));scale=min(72/300,width/im.imageWidth)
        w,h=im.imageWidth*scale,im.imageHeight*scale
        if block:
            flush();im.drawWidth=w;im.drawHeight=h;im.hAlign='LEFT'
            result.extend([Spacer(1,2),im,Spacer(1,2)])
        else:prose.append(f'<img src="{asset.resolve().as_posix()}" width="{w}" height="{h}" valign="middle"/>')
    flush();return result


def write_book(output,info,usable,evidence,chapters,size):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import BaseDocTemplate,Frame,PageTemplate,Paragraph,Spacer,PageBreak,NextPageTemplate
    from reportlab.platypus.tableofcontents import TableOfContents

    fonts();margin=11*mm;gap=6*mm
    page_w,page_h=A4;full_width=page_w-2*margin;column_width=(full_width-gap)/2
    normal=ParagraphStyle('PrintBody',fontName='PrintCN',fontSize=size,leading=size+2.4,wordWrap='CJK',spaceAfter=2,allowWidows=0,allowOrphans=0)
    styles={'body':normal,
        'title':ParagraphStyle('PrintTitle',parent=normal,fontName='PrintHead',fontSize=20,leading=25,spaceAfter=6),
        'part':ParagraphStyle('PrintPart',parent=normal,fontName='PrintHead',fontSize=12,leading=16,spaceBefore=8,spaceAfter=5,keepWithNext=True),
        'chapter':ParagraphStyle('PrintChapter',parent=normal,fontName='PrintHead',fontSize=10.5,leading=14,spaceBefore=7,spaceAfter=4,keepWithNext=True,borderWidth=0.4,borderColor=colors.black,borderPadding=3),
        'point':ParagraphStyle('PrintPoint',parent=normal,fontName='PrintHead',fontSize=size+0.7,leading=size+3,spaceBefore=5,spaceAfter=1,keepWithNext=True),
        'source':ParagraphStyle('PrintSource',parent=normal,fontSize=6.7,leading=8.4,textColor=colors.HexColor('#333333'),spaceAfter=2,keepWithNext=True),
        'quote':ParagraphStyle('PrintQuote',parent=normal,fontSize=7.3,leading=9.4,textColor=colors.HexColor('#333333'),spaceBefore=1,spaceAfter=3,leftIndent=3),
        'small':ParagraphStyle('PrintSmall',parent=normal,fontSize=7.6,leading=10,spaceAfter=3)}
    toc=TableOfContents();toc.dotsMinLevel=0
    toc.levelStyles=[ParagraphStyle('TOCPart',parent=normal,fontName='PrintHead',fontSize=10,leading=14,spaceBefore=5,spaceAfter=3),
                     ParagraphStyle('TOCChapter',parent=normal,fontSize=8.5,leading=12.5,leftIndent=10,firstLineIndent=0,spaceAfter=1)]

    class RevisionDoc(BaseDocTemplate):
        def beforeDocument(self):self.entries=[];self.kp_pages={}
        def afterFlowable(self,flowable):
            if not isinstance(flowable,Paragraph):return
            key=getattr(flowable,'bookmark',None)
            if not key:return
            title=flowable.getPlainText();level=flowable.bookmark_level
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title,key,level=level,closed=True)
            if level<=1:
                self.notify('TOCEntry',(level,title,self.page,key));self.entries.append({'title':title,'page':self.page,'key':key})
            else:self.kp_pages[key]=self.page

    def footer(canvas,doc):
        canvas.saveState();canvas.setFillColor(colors.black);canvas.setFont('PrintCN',7)
        canvas.drawString(margin,6*mm,info['course']+' · 总复习打印版')
        canvas.drawRightString(page_w-margin,6*mm,str(doc.page))
        canvas.restoreState()

    frames=[Frame(margin,12*mm,column_width,page_h-23*mm,id='left',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0),
            Frame(margin+column_width+gap,12*mm,column_width,page_h-23*mm,id='right',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)]
    cover=Frame(margin,12*mm,full_width,page_h-23*mm,id='front',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
    temp=output.with_suffix('.tmp.pdf')
    doc=RevisionDoc(str(temp),pagesize=A4,title=info['course']+' 总复习资料（省纸打印版）',author='FireKB',pageCompression=1)
    doc.addPageTemplates([PageTemplate(id='Front',frames=[cover],onPage=footer),PageTemplate(id='Body',frames=frames,onPage=footer)])
    story=[Paragraph(info['course']+'　总复习资料',styles['title']),
           Paragraph(f"省纸打印版｜正文 {len(usable)} 条｜教材主线 + 课堂课件｜正文 {size:g} 磅 / A4 双栏",normal),
           Paragraph('按章节通读，遮住定义自测，优先复习标有「重点」的条目。数值、公式和适用条件应一起记忆；同名数值差异见末尾对照。',normal),
           Paragraph('本册保留原阅读版全部正文知识点、数值、提示和原文摘录。待确认内容未纳入背诵；AI核验条目保留评分与来源，旧版条款仅代表对应教材口径。',styles['small']),
           Paragraph('打印：A4、100%实际大小、黑白、双面长边翻转。正文已经双栏，不建议再用“每张2页”缩印。',styles['small']),
           Spacer(1,6),Paragraph('目录',styles['part']),toc,
           Spacer(1,10),Paragraph('定位说明：[教]为教材；[课1]至[课6]为课件，完整资料名称列于末尾。书页与PDF物理页、幻灯片号沿用原资料。FIRE-E-KP编号可在电子版中搜索。',styles['small']),
           NextPageTemplate('Body'),PageBreak()]
    source_files=sorted({k['source_file'] for k in usable if k['source_file'].lower().endswith('.pptx')},key=lambda s:(chapter_number(Path(s).stem),s))
    codes={source:f'课{i}' for i,source in enumerate(source_files,1)}
    def heading(title,style,key,level):
        p=Paragraph(escape(title),style);p.bookmark=key;p.bookmark_level=level;return p

    for part_index,(part,groups) in enumerate(organize(chapters)):
        story.append(heading(part,styles['part'],f'part-{part_index}',0))
        for chapter_index,(chapter,members) in enumerate(groups.items()):
            story.append(heading(chapter+f'（{len(members)}条）',styles['chapter'],f'ch-{part_index}-{chapter_index}',1))
            for k in members:
                title=k['name']+('　[重点]' if int(k.get('importance') or 3)>=4 else '')
                story.append(heading(title,styles['point'],k['kp_id'],2))
                full=short_src(k);prefix=Path(k['source_file']).name.split('(')[0].strip()[:34]
                ref=full[len(prefix):].strip() if full.startswith(prefix) else full
                story.append(Paragraph(escape(f"[{codes.get(k['source_file'],'教')}] {ref} · {k['kp_id']}"),styles['source']))
                story.extend(math_prose(display(k),normal,column_width,size))
                if k.get('numbers'):story.append(Paragraph('数值：'+prose_xml('、'.join(map(str,k['numbers']))),normal))
                if k.get('note'):story.append(Paragraph('提示：'+prose_xml(k['note']),normal))
                if k.get('verification_method')=='ai_web':
                    r=k['ai_verification'];scope='旧版规范口径' if r.get('scope')=='historical_standard' else '课程口径'
                    story.append(Paragraph(escape(f"AI联网核验 {r['score']:.1%}（证据评分）· {scope}"),styles['small']))
                    for s in r.get('sources',[]):story.append(Paragraph(f'<link href="{escape(s["url"],quote=True)}">'+escape(s['title'])+'</link>',styles['small']))
                for e in evidence[k['kp_id']][:1]:
                    mark='（本地引文未精确匹配，内容经联网证据核验）' if k.get('verification_method')=='ai_web' and not e.get('quote_verified') else ''
                    story.append(Paragraph('原文：'+prose_xml(e['quote']+' / '+e['page_label']+mark),styles['quote']))
    story.append(heading('第三部分　数值差异与资料定位',styles['part'],'appendix',0))
    story.append(heading('同名知识点数值差异',styles['chapter'],'differences',1))
    story.append(Paragraph('以下保留各条原有数值，差异可能来自条件、版本或摘录粒度，不自动选择某一组作为通用答案。',normal))
    for item in info['numeric_differences']:
        story.append(Paragraph(escape(item['name']),styles['point']))
        for entry in item['entries']:story.append(Paragraph(escape(entry['kp_id']+'：'+'、'.join(map(str,entry['numbers']))+' / '+entry['source']),styles['small']))
    story.append(heading('资料目录与核验范围',styles['chapter'],'sources',1))
    for source in info['sources']:
        code=codes.get(source['source_file'],'教' if source['source_file'].lower().endswith('.pdf') else '练习资料')
        story.append(Paragraph(escape(f"[{code}] {Path(source['source_file']).name}（{source['locations']}个页/段定位）"),styles['small']))
    story.append(Paragraph(escape(f"正文 {len(usable)} 条，其中AI联网核验 {info.get('ai_verified',0)} 条；其余通过本地规则或人工裁决。未入正文的待确认条目 {info['status_counts'].get('pending',0)} 条。录音仅作教学脉络参考。"),styles['small']))
    try:
        doc.multiBuild(story,maxPasses=6);temp.replace(output)
    finally:temp.unlink(missing_ok=True)
    return {'toc':doc.entries,'knowledge_pages':doc.kp_pages,'body_font_pt':size,'layout':'A4双栏，11mm边距，6mm栏间距'}


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--course',required=True);ap.add_argument('--font-size',type=float,default=8.5)
    args=ap.parse_args();course=kb.resolve_course(args.course)
    if not 8<=args.font_size<=10:ap.error('正文请使用8至10磅，以保持打印可读性')
    info,usable,evidence,chapters,pages=build_content(course)
    out=kb.REVIEW_DIR/kb.course_slug(course);original=out/'课程知识点归纳.pdf'
    from pypdf import PdfReader
    original_reader=PdfReader(original);original_text='\n'.join(p.extract_text() or '' for p in original_reader.pages)
    expected={k['kp_id'] for k in usable}
    original_ids=set(re.findall(r'[A-Za-z0-9-]+-KP-\d+',original_text))
    if original_ids!=expected:raise ValueError('原PDF与当前正文知识点不一致，请先对照版本，不能静默换内容')
    output=out/'消防给水工程_总复习资料_省纸打印版.pdf' if course=='消防给水工程' else out/(course+'_总复习资料_省纸打印版.pdf')
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():kb.backup_files([output],'print_review')
    report=write_book(output,info,usable,evidence,chapters,args.font_size)
    reader=PdfReader(output);text='\n'.join(p.extract_text() or '' for p in reader.pages)
    actual=set(re.findall(r'[A-Za-z0-9-]+-KP-\d+',text))
    if actual!=expected or len(report['knowledge_pages'])!=len(expected):raise ValueError('打印版知识点完整性检查未通过')
    report.update({'original_pages':len(original_reader.pages),'pages':len(reader.pages),'knowledge_points':len(expected),
                   'original_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),'output_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
                   'all_knowledge_points_preserved':True,'generated_at':kb.now_iso()})
    kb.write_json(out/'print-review-manifest.json',report)
    print(f"已生成：{output}\n{len(original_reader.pages)} → {len(reader.pages)}页；正文{len(expected)}条完整保留；字号{args.font_size:g}磅")
    return 0


if __name__=='__main__':
    with kb.writer_lock():raise SystemExit(main())
