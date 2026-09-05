"""Offline regressions against actual shared JSX output, not a second drawing implementation.

Only Remotion's frame/context wrapper is stubbed. This checks SVG text and straight
decorative strokes; actual video pixels, curved artwork and semantics still need review.
"""
import copy
import json
import math
import re
import subprocess
import xml.etree.ElementTree as ET
import score as s


def svg_gate(markup):
    tree=ET.fromstring(markup)
    for parent in tree.iter():
        texts=[e for e in parent if e.tag=='text' and re.search(r'\d',''.join(e.itertext()))]
        for text in texts:
            content=''.join(text.itertext())
            s.need(not re.search(r'\d\.\d{7,}',content),'渲染文本暴露浮点尾数')
            size=float(text.get('font-size','32')); x=float(text.get('x','0')); y=float(text.get('y','0'))
            half=sum(.65 if ord(ch)<128 else 1 for ch in content)*size/2
            for path in parent.findall('path'):
                line=re.fullmatch(r'M\s*([-\d.]+)[ ,]+([-\d.]+)\s*H\s*([-\d.]+)',path.get('d',''))
                if not line:continue
                a,b,c=map(float,line.groups());stroke=float(path.get('stroke-width','1'))
                overlap=min(a,c)<x+half and max(a,c)>x-half and abs(b-y)<size*.5+stroke/2
                s.need(not overlap,'水平装饰线穿过数字文字槽')
    return tree


def check():
    fixture=s.read(s.ROOT/'assets/score.example.json')
    compiled=s.compile_score(fixture['score'],fixture['props'])
    action=next(a for a in compiled['actions'] if a['id']=='offset')
    frame=math.ceil((action['end']+.02)*30)
    # Renderer stress input: a floating intermediate and the compact ticket from the observed failure.
    compiled['frames'][frame]['state']['netPnl']=.1+.2
    action.update(valueLabel='合计 ',valueSuffix=' 美元')
    pose=next(d for d in compiled['frames'][frame]['objects'] if d['id']=='short')
    pose['box'][2:]=[220,120];pose['valueFontSize']=39.6
    js=r"""
const fs=require('fs'),vm=require('vm'),ts=require('typescript'),React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const exports={};
const context={exports,require:(name)=>name==='remotion'?{
  useCurrentFrame:()=>input.frame,
  AbsoluteFill:({children,...props})=>React.createElement('div',props,children),
}:require(name)};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('assets/ScoreStage.tsx','utf8'),{
  compilerOptions:{jsx:ts.JsxEmit.React,module:ts.ModuleKind.CommonJS,esModuleInterop:true}
}).outputText,context);
process.stdout.write(renderToStaticMarkup(React.createElement(exports.ScoreStage,input.compiled)));
"""
    result=subprocess.run(['node','-e',js],cwd=s.ROOT,input=json.dumps({'frame':frame,'compiled':compiled}),text=True,capture_output=True)
    s.need(result.returncode==0,'共享 JSX 回归无法执行：'+result.stderr[-500:])
    tree=svg_gate(result.stdout)
    label=next((e for e in tree.iter('text') if ''.join(e.itertext()).startswith('合计 ')),None)
    s.need(label is not None and label.text=='合计 0.3 美元','连接数值的格式或单位回归')
    negative=copy.deepcopy(tree)
    next(e for e in negative.iter('text') if (e.text or '').startswith('合计 ')).text='合计 '+str(.1+.2)+' 美元'
    decorated=copy.deepcopy(tree)
    ticket=next(e for e in decorated.iter('g') if e.get('data-object-id')=='short')
    ET.SubElement(ticket,'path',{'d':'M18 93H202','stroke-width':'3'})
    for name,bad in [('floating-tail',negative),('decoration-through-value',decorated)]:
        try:svg_gate(ET.tostring(bad,encoding='unicode'))
        except s.ScoreError:continue
        raise s.ScoreError('旧错误未被阻断：'+name)
    return {'sharedSvgRegression':'pass','negativeCases':2,'scope':'实际 JSX 的数值格式与水平装饰线；不代替实片和语义复核'}


if __name__=='__main__':print(json.dumps(check(),ensure_ascii=False))
