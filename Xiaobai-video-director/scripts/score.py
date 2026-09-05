"""Executable visual score: compile once, render and validate the same frame states.
No topic-specific React, credentials, network calls, or evaluation of arbitrary code.
"""
from __future__ import annotations
import array
import copy
import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from PIL import Image, ImageChops, ImageStat

ROOT = Path(__file__).resolve().parents[1]

class ScoreError(ValueError):
    pass

def need(ok, message):
    if not ok:
        raise ScoreError(message)

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n')

def read(path):
    return json.loads(Path(path).read_text())

def local(base, name):
    p = (Path(base)/name).resolve()
    need(Path(base).resolve() in p.parents and p.is_file(), f'缺少 run 内文件或路径越界：{name}')
    return p

def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

def expression(expr, state):
    if finite(expr):
        return float(expr)
    if isinstance(expr, str):
        need(expr in state, f'未知模型字段：{expr}')
        return state[expr]
    need(isinstance(expr, list) and len(expr) == 3 and expr[0] in ('+', '-', '*'), '只接受有界加减乘模型表达式')
    a, b = expression(expr[1], state), expression(expr[2], state)
    return {'+': lambda:a+b, '-':lambda:a-b, '*':lambda:a*b}[expr[0]]()

def state_at(score, actions, seconds):
    state = dict(score['model']['initial'])
    discrete=score['model'].get('discrete',[])
    need(set(discrete).issubset(state),'离散字段必须声明初态')
    for action in actions:
        if seconds < action['start'] or (action['kind']=='transfer' and seconds<action['end']):
            continue
        p = min(1., (seconds-action['start'])/action['duration'])
        p = p*p*(3-2*p)
        for field, change in action.get('effects', {}).items():
            state[field] = (change['to'] if p>=1 else change['from']) if field in discrete else change['from']+(change['to']-change['from'])*p
    for name, expr in score['model'].get('derived', {}).items():
        state[name] = expression(expr, state)
    return state

def inside(inner, outer, pad=0):
    x,y,w,h=inner; a,b,c,d=outer
    return x>=a+pad-.01 and y>=b+pad-.01 and x+w<=a+c-pad+.01 and y+h<=b+d-pad+.01

def intersects(a,b,pad=0):
    return a[0]<b[0]+b[2]+pad and b[0]<a[0]+a[2]+pad and a[1]<b[1]+b[3]+pad and b[1]<a[1]+a[3]+pad

def label_box(obj, box):
    x,y,w,h=box
    return [x,y-46,w,38] if obj['labelPlacement']=='above' else [x+12,y+12,w-24,34]

def receiver(obj):
    x,y,w,h=obj['box']; r=obj.get('receiver')
    need(r is not None and len(r)==4 and all(finite(v) for v in r), f'{obj["label"]} 缺少接收区')
    box=[x+r[0],y+r[1],r[2],r[3]]
    need(inside(box,obj['box']),f'{obj["label"]} 接收区超出自身')
    return box

def centered(obj, receptor):
    w,h=obj['box'][2:]
    box=[receptor[0]+(receptor[2]-w)/2,receptor[1]+(receptor[3]-h)/2,w,h]
    need(inside(box,receptor),f'{obj["label"]} 不能完整进入接收区')
    return box

def compile_score(score, props):
    score=copy.deepcopy(score)
    need(score.get('version')==2,'需要 version=2 的可执行 score；旧声明表不能交付')
    need(props.get('durationInFrames',0)>0 and props['durationInFrames']<=30*600,'时长必须在 10 分钟以内')
    fps=30; duration=props['durationInFrames']/fps
    clauses=score.get('clauses',[]); captions=props['captions']
    need(len(clauses)==len(captions)>0,'每条真实口播必须对应一条语义分句，不能漏句')
    objects=score.get('objects',{})
    need(objects and isinstance(objects,dict),'缺少对象词典')
    for oid,o in objects.items():
        need(re.fullmatch(r'[a-z][a-z0-9_-]*',oid) and o.get('label') and o.get('noun'),'对象必须有稳定身份、主标签和口播名词')
        need(o['label']==o['noun'],'标签必须使用同一个口播名词；缩写先在文稿中明确')
        need(o.get('shape') in {'market','robot','ticket','meter','brace'},f'未验证的图形能力：{o.get("shape")}')
        need(o.get('labelPlacement') in {'above','inside'},'必须给出明确文字槽')
        need(len(o.get('box',[]))==4 and all(finite(v) for v in o['box']) and min(o['box'][2:])>0,'无效对象边界')
        need(o.get('color') in {'teal','gold','red','ink'},'只接受当前调色板颜色')
        need(len(o['label'])*30 <= o['box'][2]-20,'文字槽过小，先简化对象名或加大对象')
    initial=score.get('model',{}).get('initial',{})
    need(initial and all(finite(v) for v in initial.values()),'机制模型需要有限数值初态')
    # Evaluate in declared dependency order: forward references and cycles fail before recursion.
    state_at(score,[],0)
    actions=copy.deepcopy(score.get('actions',[])); ids=[a.get('id') for a in actions]
    need(len(ids)==len(set(ids)) and all(isinstance(i,str) for i in ids),'动作 id 缺失或重复')
    by_action={a['id']:a for a in actions}
    for a in actions:
        need(set(a).issubset({'id','kind','cue','targets','duration','delay','after','requires','effects','source','destination','fromBox','toBox','reason','valueField','valueLabel','valueSuffix','stagingFor'}),'动作包含未实现字段，禁止在模型外偷偷改变标签或状态')
        need(a.get('cue') in props['cues'],'动作必须绑定原生语音 cue')
        need(a.get('kind') in {'reveal','transfer','change','exit','connect','hold','move'},'动作没有已实现的执行器')
        need(finite(a.get('duration')) and .25<=a['duration']<=4,'动作需 .25–4 秒可辨认过程')
        need(finite(a.get('delay',0)) and 0<=a.get('delay',0)<=4,'不允许用负偏移抢先于语音')
        a['start']=props['cues'][a['cue']]/1000+a.get('delay',0); a['end']=a['start']+a['duration']
        need(a['end']<=duration,'动作超过视频时长')
        need(a.get('targets') and all(x in objects for x in a['targets']),'动作目标不存在')
        if a['kind']=='change':
            need(a.get('effects'),'change 必须更新机制模型，不能只写结果文本')
        if a['kind']=='hold':
            need(a.get('reason') and a['duration']<=2,'停留需说明已看懂的状态和有限阅读时间')
        if a['kind'] in {'transfer','move'}:
            need(len(a['targets'])==1,'每条运输动作只操作一个有身份的对象')
            if a['kind']=='transfer':
                need(a.get('source') in objects and a.get('destination') in objects,'运输必须有明确来源与目的地')
                actor=objects[a['targets'][0]]
                a['fromBox']=centered(actor,receiver(objects[a['source']]))
                a['toBox']=centered(actor,receiver(objects[a['destination']]))
            else:
                need(a.get('fromBox') and a.get('toBox') and a.get('reason'),'移动需明确路径和注意力理由')
        for field,change in a.get('effects',{}).items():
            need(field in initial and set(change)=={'from','to'} and all(finite(v) for v in change.values()),'模型更新必须指向真实字段')
            need(change['from']!=change['to'],'禁止用无变化模型更新冒充过程')
    actions.sort(key=lambda a:(a['start'],a['id']))
    field_updates={field:[] for field in initial}
    for a in actions:
        for dep in a.get('after',[]):
            need(dep in by_action and by_action[dep]['end']<=a['start']+.001,'动作必须等前置事件完成后才能触发')
        at_start=state_at(score,[x for x in actions if x['end']<=a['start']],a['start'])
        for field,expected in a.get('requires',{}).items():
            need(field in at_start and abs(at_start[field]-expected)<1e-5,'动作前置状态不成立：'+a['id'])
        for field,change in a.get('effects',{}).items():
            prior=field_updates[field]
            need(not prior or prior[-1]['end']<=a['start'],'同一模型字段存在重叠写入')
            need(abs(at_start[field]-change['from'])<1e-5,'状态更新从错误初态出发：'+field)
            prior.append(a)
    def dependencies(expr):
        if isinstance(expr,str):
            if expr in score['model'].get('derived',{}):
                return dependencies(score['model']['derived'][expr])
            return {expr}
        if isinstance(expr,list): return dependencies(expr[1]) | dependencies(expr[2])
        return set()
    for a in actions:
        if a['kind']!='change': continue
        consumed=set()
        for oid in a['targets']:
            o=objects[oid]
            if o.get('value'):
                consumed |= dependencies(o['value']['field'])
                if o['value'].get('showField'):consumed.add(o['value']['showField'])
            for attr in ['statusField','enabledField','validField']:
                if o.get(attr):consumed.add(o[attr])
        need(set(a['effects']).issubset(consumed),'状态变化没有绑定可见对象属性：'+a['id'])
    for name in score['model'].get('derived',{}):
        need(name not in initial,'派生值不能覆盖基础字段')
    clause_ids=[c['id'] for c in clauses]
    need(len(set(clause_ids))==len(clause_ids),'重复语义分句')
    claimed=set()
    for i,(c,cap) in enumerate(zip(clauses,captions)):
        need(c['text']==cap['text'],'语义分句与真实配音文本不一致')
        need(len(re.sub(r'\s+','',c['text']))<=32,'单句负担过大：先拆短口播再设计动作')
        need(c.get('focus') in objects and c['focus'] in c.get('visible',[]),'缺少当前注意力主角')
        need(1<=len(c['visible'])<=5 and len(set(c['visible']))==len(c['visible']),'含背景在内最多五个逻辑对象；超出需局部聚焦')
        need(set(c['visible']).issubset(objects),'注意力引用未知对象')
        need(c.get('relations') is not None,'必须明确对象关系，不能只并排摆放')
        reached={c['focus']}
        for _ in objects:
            for r in c['relations']:
                need(set(r)=={'from','to','type'} and r['from'] in objects and r['to'] in objects,'关系端点无效')
                need(r['type'] in {'observes','executes','contains','compares','offsets','produces'},'未知关系含义')
                if r['from'] in reached or r['to'] in reached:
                    reached.update([r['from'],r['to']])
        need(set(c['visible']).issubset(reached),'存在没有关系的背景对象')
        # Every character is accounted for; grammatical context cannot hide a whole claim.
        parts=c.get('coverage',[])
        need(''.join(p['text'] for p in parts)==c['text'],'口播覆盖必须逐字完整，不能遗漏名词、动作或结论')
        need(sum(len(p['text']) for p in parts if p['kind']=='context')<=len(c['text'])*.55,'过多正文被排除为上下文')
        evidence_count=0
        next_start=captions[i+1]['startMs']/1000 if i+1<len(clauses) else duration
        for p in parts:
            need(p['kind'] in {'context','entity','action'},'覆盖分类无效')
            if p['kind']=='context':
                need(p.get('reason'),'非视觉上下文需说明原因')
            elif p['kind']=='entity':
                need(p.get('object') in c['visible'],'口播实体没有在当前镜头中')
                need(objects[p['object']]['noun'] in p['text'],'可见名称与口播实体不一致')
                if p.get('action'):
                    aid=p['action']; need(aid in by_action and p['object'] in by_action[aid]['targets'], '实体出生未绑定对应动作')
                    a=by_action[aid]
                    need(cap['startMs']/1000<=a['start']<next_start and a['end']<=next_start+.001,'实体动作越出口播句界')
                    claimed.add(aid); evidence_count+=1
            else:
                aid=p.get('action'); need(aid in by_action,'动词没有真实执行动作')
                a=by_action[aid]
                need(cap['startMs']/1000<=a['start']<next_start and a['end']<=next_start+.001,'语义动作没有在本句内完成')
                need(set(a['targets']).issubset(c['visible']) or a['kind']=='exit','动作对象不在镜头中')
                claimed.add(aid); evidence_count+=1
        need(evidence_count>=1,'每句核心含义需要可见过程或有理由的状态停留')
        c['start']=cap['startMs']/1000; c['end']=next_start
        need(set(c.get('camera',{}))=={'x','y','zoom'} and all(finite(v) for v in c['camera'].values()),'镜头需显式关注点')
        need(.65<=c['camera']['zoom']<=1.3,'镜头缩放越出已验证范围')
    for a in actions:
        if not a.get('stagingFor'):continue
        need(a['kind'] in {'move','exit'} and a.get('reason') and not a.get('effects'),'注意力调度不能冒充机制动作')
        need(a['stagingFor'] in claimed,'注意力调度必须服务已有语义动作')
        target=by_action[a['stagingFor']]
        clause=next((c for c in clauses if c['start']<=target['start']<c['end']),None)
        need(clause and clause['start']<=a['start'] and a['end']<=clause['end'],'注意力调度越过所属语义分句')
        claimed.add(a['id'])
    need(set(ids)==claimed,'有动作未绑定口播含义：'+','.join(set(ids)-claimed))
    # Logical state and visual state are different: every object's life needs an exit.
    life={oid:[a for a in actions if oid in a['targets']] for oid in objects}
    for oid,ops in life.items():
        births=[a for a in ops if a['kind']=='reveal']; deaths=[a for a in ops if a['kind']=='exit']
        need(len(births)==len(deaths)==1,f'{oid} 必须有且只有一次出生和完整退场')
        need(births[0]['end']<deaths[0]['start'],f'{oid} 生命周期无效')
        for a in ops:
            need(a['start']>=births[0]['start'] and a['end']<=deaths[0]['end'],'对象在生命期外动作')
    frames=[]
    for f in range(props['durationInFrames']):
        t=f/fps
        c=next((c for c in clauses if c['start']<=t<c['end']),None)
        prev=clauses[max(0,clauses.index(c)-1)] if c else clauses[0]
        cam=(c or prev)['camera']; mix=min(1,max(0,(t-(c or prev)['start'])/.85)); mix=mix*mix*(3-2*mix)
        camera={k:prev['camera'][k]*(1-mix)+cam[k]*mix for k in cam}
        state=state_at(score,actions,t)
        drawn=[]
        for oid,o in objects.items():
            ops=life[oid]; birth=next(a for a in ops if a['kind']=='reveal'); death=next(a for a in ops if a['kind']=='exit')
            if t<birth['start'] or t>=death['end']:
                continue
            b=list(o['box']); scale=1.; active=[]; progress=0
            for a in ops:
                if t<a['start']: continue
                u=min(1,(t-a['start'])/a['duration']); smooth=u*u*(3-2*u)
                if a['start']<=t<a['end']: active.append(a['id']); progress=u
                if a['kind']=='reveal': scale=smooth+math.sin(math.pi*u)*.065
                if a['kind']=='exit': scale=1-smooth
                if a['kind'] in {'move','transfer'}:
                    b=[v+(a['toBox'][j]-v)*smooth for j,v in enumerate(a['fromBox'])]
            visible=c and oid in c['visible']
            outgoing=death['start']<=t<death['end']
            need(visible or outgoing or t<clauses[0]['start'],f'{oid} 在 {t:.2f}s 未经注意力合同继续留场')
            label=label_box(o,b)
            for attr in ['enabledField','validField']:
                if o.get(attr):need(o[attr] in state and 0<=state[o[attr]]<=1,'可见开关字段必须存在且处于0–1')
            value=None
            reveal=state[o['value']['showField']] if o.get('value',{}).get('showField') else 1.
            if o.get('value') and reveal>0:
                value=expression(o['value']['field'],state)
            typography={'valueReveal':reveal}
            if value is not None:
                spec=o['value']; decimals=spec.get('decimals',0); rounded=0. if abs(value)<.5*10**(-decimals) else value; number=f'{rounded:.{decimals}f}'
                if spec.get('decimals',0): number=number.rstrip('0').rstrip('.')
                text=spec.get('prefix','')+('+' if spec.get('signed') and value>0 else '')+number+spec.get('suffix','')
                # ponytail: conservative glyph widths; replace with font metrics if new scripts need them.
                units=sum(.65 if ord(ch)<128 else 1 for ch in text)
                size=min(62 if o['shape']=='market' else 48,(b[2]-44)/max(1,units),b[3]*.33)
                need(size>=26,'数值文字过密，先增大对象或简化数值单位：'+oid)
                typography={'valueText':text,'valueFontSize':size}
            changing=next((a for a in ops if a['id'] in active and a['kind']=='change'),None)
            if changing and o.get('value'):
                before=expression(o['value']['field'],state_at(score,actions,changing['start']))
                after=expression(o['value']['field'],state_at(score,actions,changing['end']))
                typography['changeDirection']=1 if after>before else -1 if after<before else 0
            drawn.append({'id':oid,'box':b,'scale':round(scale,5),'labelBox':label,'value':value,'active':active,'progress':progress,**typography})
        for invariant in score['model'].get('invariants',[]):
            if t<by_action[invariant['after']]['end']: continue
            val=expression(invariant['expression'],state)
            need(abs(val-invariant['equals'])<1e-4, f'机制不变量失败：{invariant["name"]} @ {t:.2f}s')
        # All visible shapes and labels are projected through the SAME camera as React.
        def screen(box):
            x,y,w,h=box; z=camera['zoom']
            return [960+(x-camera['x'])*z,540+(y-camera['y'])*z,w*z,h*z]
        for d in drawn:
            if d['scale']<.98: continue
            need(inside(screen(d['box']),[70,200,1780,650]),f'对象超出画面安全区：{d["id"]}@{t:.2f}')
            need(inside(screen(d['labelBox']),[70,200,1780,650]),f'文字超出画面安全区：{d["id"]}@{t:.2f}')
            for other in drawn:
                if d['id']==other['id'] or other['scale']<.98: continue
                # Labels can belong to own container only. Moving tokens never cover other labels.
                known_container=any(a['kind']=='transfer' and other['id'] in [a['source'],a['destination']] for a in life[d['id']])
                receptor=receiver(objects[other['id']]) if known_container else None
                contained=known_container and d['labelBox'][1]>=receptor[1] and d['labelBox'][1]+d['labelBox'][3]<=receptor[1]+receptor[3]
                need(not intersects(d['labelBox'],other['labelBox']),f'两个文字槽重叠：{d["id"]}/{other["id"]}@{t:.2f}')
                need(contained or not intersects(d['labelBox'],other['box']),f'文字与图形冲突：{d["id"]}/{other["id"]}@{t:.2f}')
                if intersects(d['box'],other['box']) and d['box'][2]*d['box'][3] <= other['box'][2]*other['box'][3]:
                    allowed=[a for a in life[d['id']] if a['kind']=='transfer' and other['id'] in [a['source'],a['destination']]]
                    need(allowed,f'未声明包含关系的图形重叠：{d["id"]}/{other["id"]}@{t:.2f}')
        frames.append({'clause':c['id'] if c else None,'camera':camera,'state':state,'objects':drawn})
    for oid,ops in life.items():
        previous_box=objects[oid]['box']
        for a in ops:
            if a['kind'] not in {'move','transfer'}: continue
            need(all(abs(x-y)<.01 for x,y in zip(previous_box,a['fromBox'])),'运动从错误位置起跳：'+oid)
            previous_box=a['toBox']
    for a in actions:
        if a['kind'] not in {'change','connect','transfer','move'}: continue
        mid=frames[min(len(frames)-1,round((a['start']+a['duration']*.5)*fps))]
        need(any(d['id'] in a['targets'] and a['id'] in d['active'] for d in mid['objects']),'缺少真实可执行中间态')
    need(not frames[-1]['objects'],'片尾仍有对象残留')
    return {'version':2,'scoreHash':fingerprint(score),'propsHash':fingerprint(props),'props':props,'score':score,'actions':actions,'frames':frames,'fps':fps,'durationInFrames':props['durationInFrames']}


def pcm(path, filters=''):
    cmd=['ffmpeg','-v','error','-i',str(path)]
    if filters: cmd+=['-af',filters]
    raw=subprocess.run(cmd+['-ac','1','-ar','24000','-f','f32le','-'],capture_output=True,check=True).stdout
    values=array.array('f');values.frombytes(raw);return values

def level(values):
    need(len(values)>0,'空音轨')
    rms=math.sqrt(sum(v*v for v in values)/len(values))
    return 20*math.log10(max(rms,1e-10))

def sound_mix(compiled, base):
    props=compiled['props']; voice=pcm(local(base,props['audioSrc']),f'atempo={props["playbackRate"]}')
    lead=round(props['leadFrames']/30*24000); voice=array.array('f',[0])*lead+voice
    global_voice=level(voice); result=[]
    actions={a['id']:a for a in compiled['actions']}
    for s in compiled['score'].get('sounds',[]):
        need(s.get('action') in actions and s.get('phase') in {'start','end'},'音效必须绑定实际动作起止')
        a=actions[s['action']]; t=a[s['phase']]; source=pcm(local(base,s['src']))
        rel=s.get('relativeDb',-10)
        need(finite(rel) and -16<=rel<=-6,'语义音效必须比附近口播低 6–16 dB；不能重复衰减')
        start=max(0,round((t-.3)*24000)); end=min(len(voice),round((t+.7)*24000))
        reference=max(level(voice[start:end]),global_voice-3)
        gain=reference+rel-level(source)
        need(max(abs(v)*10**(gain/20) for v in source)<.7,'所需音效增益会削波，改用动态范围较小的素材')
        need(.08<=len(source)/24000<=1,'音效过长或过短')
        result.append({**s,'at':t,'volume':10**(gain/20),'duration':len(source)/24000,'sourceHash':sha(local(base,s['src'])),'measuredRelativeDb':rel,'gainDb':gain})
    times=sorted(s['at'] for s in result)
    need(all(b-a>=.8 for a,b in zip(times,times[1:])),'语义音效太密')
    return result


def renderer_hash():
    return fingerprint({p:sha(ROOT/p) for p in ['assets/ScoreStage.tsx','assets/ScoreEntry.tsx','scripts/score.py']})

def compile_file(score_path,props_path):
    score=read(score_path); props=read(props_path)
    # Rebuild cue/caption timing from the saved provider alignment, not Agent-written offsets.
    import director
    from speech import SpeechAlignment, SpeechAlignmentSpan, probe_audio_duration
    metadata=read(local(score_path.parent,'alignment.json'))
    need(sha(local(score_path.parent,props['audioSrc']))==metadata['audio_sha256'],'原生配音缓存不匹配')
    raw=metadata['alignment']
    alignment=SpeechAlignment(raw['source'],raw['granularity'],tuple(SpeechAlignmentSpan(**v) for v in raw['spans']))
    rebuilt=director.make_props(read(local(score_path.parent,'storyboard.json')),alignment,probe_audio_duration(local(score_path.parent,props['audioSrc']).read_bytes()),props['playbackRate'])
    need(rebuilt==props,'props 与原生语音时间轴不一致，必须重新 prepare')
    result=compile_score(score,props)
    result['sounds']=sound_mix(result,score_path.parent)
    result['audioHash']=sha(local(score_path.parent,props['audioSrc']))
    result['rendererHash']=renderer_hash()
    rules=director.load_learning_rules(read(local(score_path.parent,'brief.json'))['profile'])
    supported={'verb-visible-evidence','progressive-reveal','complete-exit','label-artwork-separation','semantic-motion-destination','continuous-state-world','container-and-spacing','context-neutrality','transition-variety-with-purpose','executable-world-model','actor-layer-visibility','semantic-motion-endpoints','horizontal-lane-consistency','single-meaning-object','exact-spoken-label-binding','direct-metaphor','typed-object-relations','state-derived-result','attention-isolation','causal-transition-process'}
    need({r['id'] for r in rules}.issubset(supported),'新经验尚未接入可执行检查或真实帧复核；禁止静默忽略')
    result['learnedRules']=rules
    return result


def render(score_path,props_path,out):
    need(not out.exists(),'候选文件已存在；使用新版本路径')
    need(out.parent.resolve()==score_path.parent.resolve(),'候选片需放在当前 run 内')
    from check_score_render import check
    check()  # Shared renderer regressions block new candidates before audio/render work.
    compiled=compile_file(score_path,props_path)
    mixed=out.with_suffix('.mix.wav')
    write_mix(compiled,out.parent,mixed)
    compiled['mixedAudio']=mixed.name; compiled['mixedHash']=sha(mixed)
    compiled_path=out.with_suffix('.compiled.json'); save(compiled_path,compiled)
    subprocess.run(['pnpm','exec','remotion','render','assets/ScoreEntry.tsx','DirectedScore',str(out.resolve()),'--props='+str(compiled_path.resolve()),'--public-dir='+str(out.parent.resolve()),'--codec=h264','--pixel-format=yuv420p','--crf=19','--concurrency=4','--log=error'],cwd=ROOT,check=True)
    # Remotion's audio stitching introduced 1024 samples at 24 kHz in the observed run.
    # Encode the single master once with ffmpeg, whose AAC priming metadata is preserved.
    with tempfile.TemporaryDirectory(prefix='director-mux-',dir=out.parent) as temp:
        muxed=Path(temp)/'candidate.mp4'
        subprocess.run(['ffmpeg','-v','error','-i',str(out),'-i',str(mixed),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-ar','48000','-t',str(compiled['durationInFrames']/30),'-movflags','+faststart',str(muxed)],check=True)
        verify_audio(compiled,muxed,mixed)
        muxed.replace(out)
    receipt={'version':2,'candidateHash':sha(out),'compiledHash':sha(compiled_path),'rendererHash':renderer_hash(),'scoreHash':compiled['scoreHash'],'propsHash':compiled['propsHash'],'compiled':compiled_path.name}
    save(out.with_suffix('.receipt.json'),receipt)
    return {'render':'pass','candidate':str(out),'reviewRequired':True}


def verify_receipt(score_path,props_path,video):
    receipt=read(video.with_suffix('.receipt.json'))
    compiled_path=local(video.parent,receipt['compiled']); compiled=read(compiled_path)
    need(receipt['version']==2 and sha(video)==receipt['candidateHash'],'候选片与受控渲染记录不匹配')
    need(sha(compiled_path)==receipt['compiledHash'],'逐帧执行数据已变化')
    need(renderer_hash()==receipt['rendererHash'],'渲染器修改后必须重渲染')
    fresh=compile_file(score_path,props_path)
    mixed=local(video.parent,compiled['mixedAudio'])
    need(sha(mixed)==compiled['mixedHash'],'实际混音已改变')
    fresh['mixedAudio']=compiled['mixedAudio']; fresh['mixedHash']=sha(mixed)
    need(fingerprint(fresh)==fingerprint(compiled),'配音、模型或音效改变后不能复用旧视频与验收')
    return compiled


def review_pack(score_path,props_path,video):
    compiled=verify_receipt(score_path,props_path,video)
    folder=video.parent/(video.stem+'-review');folder.mkdir(exist_ok=True)
    required=[]
    for a in compiled['actions']:
        for phase,fraction in [('before',-.08/a['duration']),('mid',.5),('settled',1.02)]:
            seconds=max(0,min(compiled['durationInFrames']/30-1/30,a['start']+fraction*a['duration']))
            name=f'{a["id"]}-{phase}.png';dest=folder/name
            subprocess.run(['ffmpeg','-v','error','-y','-ss',str(seconds),'-i',str(video),'-frames:v','1',str(dest)],check=True)
            with Image.open(dest) as im: im.verify()
            required.append({'action':a['id'],'phase':phase,'seconds':seconds,'image':str(dest.relative_to(video.parent)),'sha256':sha(dest)})
    pixel_result=pixel_gate(compiled,video,required)
    report={'pixelChecks':pixel_result,'version':3,'candidateHash':sha(video),'scoreHash':compiled['scoreHash'],'evidence':required,'observations':[],
            'learnedChecks':[{'id':r['id'],'rule':r['rule'],'status':'pending','actions':[],'note':''} for r in compiled['learnedRules']],
            'playback':{'observed':False,'relationsClear':False,'attentionClear':False,'audioAudible':False,'note':''},'verdict':'pending'}
    path=folder/'review.json'; need(not path.exists(),'已有复核记录，保留它并使用新候选版本')
    save(path,report)
    return {'pack':str(path),'frames':len(required),'verdict':'pending'}


def review_gate(compiled,video,qa):
    need(qa.get('version')==3 and qa.get('candidateHash')==sha(video) and qa.get('scoreHash')==compiled['scoreHash'],'审查必须绑定当前候选片和模型哈希')
    expected={(a['id'],phase) for a in compiled['actions'] for phase in ['before','mid','settled']}
    evidence=qa.get('evidence',[])
    need({(x['action'],x['phase']) for x in evidence}==expected,'缺少动作前、中、落定真实帧')
    for e in evidence:
        p=local(video.parent,e['image']);need(sha(p)==e['sha256'],'证据图片已被替换')
        with Image.open(p) as im: im.verify()
    observations=qa.get('observations',[])
    need({o.get('clause') for o in observations}=={c['id'] for c in compiled['score']['clauses']},'每个分句需要具体的视觉观察，不接受自动填充 reviewed=true')
    need(all(o.get('status')=='pass' and len(o.get('observed',''))>=12 and len(o.get('meaning',''))>=8 for o in observations),'缺少“实际看到了什么”和“为何解释这句话”的记录')
    need(len({o['observed'] for o in observations})==len(observations),'不能把同一条通过文案复制给所有镜头')
    checks=qa.get('learnedChecks',[])
    need(len(checks)==len(compiled['learnedRules']) and {v.get('id') for v in checks}=={r['id'] for r in compiled['learnedRules']},'历史经验未逐项复核；不能只检查新改片段')
    for item in checks:
        need(item.get('status')=='pass' and item.get('actions') and set(item['actions']).issubset({a['id'] for a in compiled['actions']}) and len(item.get('note',''))>=12,'经验复核缺少对应动作及具体观察：'+str(item.get('id')))
    p=qa.get('playback',{})
    need(all(p.get(k) is True for k in ['observed','relationsClear','attentionClear','audioAudible']) and len(p.get('note',''))>=20,'连续播放与听感复核尚未完成')
    need(qa.get('verdict')=='pass','观感复核未通过')
    return {'evidenceFrames':len(evidence),'clauseReviews':len(observations)}


def release(score_path,props_path,video,qa_path,final):
    need(not final.exists() and final.resolve()!=video.resolve(),'最终文件不能覆盖已有产物')
    compiled=verify_receipt(score_path,props_path,video)
    qa=read(qa_path)
    result=review_gate(compiled,video,qa)
    result['pixelChecks']=pixel_gate(compiled,video,qa['evidence'])
    # Evidence must be the actual candidate frame; a valid but unrelated PNG is not evidence.
    with tempfile.TemporaryDirectory(prefix='score-proof-') as temp:
        for e in qa['evidence']:
            check=Path(temp)/'frame.png'
            subprocess.run(['ffmpeg','-v','error','-y','-ss',str(e['seconds']),'-i',str(video),'-frames:v','1',str(check)],check=True)
            with Image.open(check) as actual, Image.open(local(video.parent,e['image'])) as claimed:
                need(actual.size==claimed.size and ImageChops.difference(actual.convert('RGB'),claimed.convert('RGB')).getbbox() is None,'抽帧证据与视频不匹配')
    result['audioCheck']=verify_audio(compiled,video)
    subprocess.run(['ffmpeg','-v','error','-i',str(video),'-f','null','-'],check=True)
    mixed=pcm(video)
    need(max(abs(v) for v in mixed)<.98,'最终混音削波')
    shutil.copy2(video,final)
    result.update({'release':'pass','final':str(final),'candidateHash':sha(video),'interpretation':'执行与审查证据通过；不能替代用户观感或跨材料盲测'})
    save(final.with_suffix('.release.json'),result)
    return result


def write_mix(compiled,base,destination):
    """One measured PCM master; Remotion cannot apply a second hidden SFX attenuation."""
    p=compiled['props'];n=compiled['durationInFrames']*800
    mixed=array.array('f',[0])*n
    narration=pcm(local(base,p['audioSrc']),f'atempo={p["playbackRate"]}')
    tracks=[(p['leadFrames']*800,narration,1.)]
    tracks.extend((round(s['at']*30)*800,pcm(local(base,s['src'])),s['volume']) for s in compiled['sounds'])
    for start,track,gain in tracks:
        for i,v in enumerate(track[:max(0,n-start)]):mixed[start+i]+=v*gain
    need(max(abs(v) for v in mixed)<.95,'人声与音效叠加后削波，必须重新混音')
    integers=array.array('h',(round(v*32767) for v in mixed))
    with wave.open(str(destination),'wb') as w:
        w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(integers.tobytes())


def verify_audio(compiled,video,master=None):
    expected=pcm(master or local(video.parent,compiled['mixedAudio']));actual=pcm(video)
    n=min(len(actual),len(expected));need(abs(len(actual)-len(expected))<2400,'最终音轨时长漂移')
    def relative_error(start,end):
        a=actual[start:end];b=expected[start:end]
        energy=sum(v*v for v in b)
        need(energy>1e-8,'目标音频窗口无声')
        return sum((x-y)**2 for x,y in zip(a,b))/energy
    need(relative_error(0,n)<.08,'最终音轨与已检查混音不匹配')
    # Isolate SFX from the known narration: total-window error can hide a missing quiet effect.
    p=compiled['props'];base=Path(master).parent if master else video.parent
    voice=pcm(local(base,p['audioSrc']),f'atempo={p["playbackRate"]}')
    lead=p['leadFrames']*800
    def narration_sample(i):return voice[i-lead] if lead<=i<lead+len(voice) else 0.
    for sound in compiled['sounds']:
        start=round(sound['at']*30)*800;end=min(n,start+round(sound['duration']*24000))
        effect_energy=sum((expected[i]-narration_sample(i))**2 for i in range(start,end))
        need(effect_energy>1e-7,'混音中缺少声明的语义音效')
        error=sum((actual[i]-expected[i])**2 for i in range(start,end))/effect_energy
        need(error<.25,'成片语义音效丢失或改变')
    return {'masterMatch':True,'checkedSoundEvents':len(compiled['sounds']),'relativeDb':[s['measuredRelativeDb'] for s in compiled['sounds']]}


def pixel_gate(compiled,video,evidence):
    """Pixel presence plus trace geometry. This does NOT infer semantic understanding."""
    checks=0;labels=0
    paper=(246,243,233);ink=(32,59,64)
    def fraction(image,box,kind):
        x,y,w,h=box
        crop=image.crop((round(x),round(y),round(x+w),round(y+h))).convert('RGB')
        values=list(crop.getdata())
        if not values:return 0
        test=(lambda p:math.dist(p,paper)>65) if kind=='foreground' else (lambda p:math.dist(p,ink)<90)
        return sum(test(v) for v in values)/len(values)
    for e in evidence:
        index=min(len(compiled['frames'])-1,round(e['seconds']*30));f=compiled['frames'][index];cam=f['camera']
        def project(b):
            x,y,w,h=b;z=cam['zoom'];return [960+(x-cam['x'])*z,540+(y-cam['y'])*z,w*z,h*z]
        with Image.open(local(video.parent,e['image'])) as im:
            need(im.size==(1920,1080),'真实帧尺寸不正确')
            for box in [[68,200,3,650],[1848,200,3,650],[70,198,1780,3],[70,848,1780,3]]:
                need(fraction(im,box,'foreground')<.025,f'画面安全区边缘被裁切：{e["action"]}/{e["phase"]}')
            for obj in f['objects']:
                if obj['scale']<.99 or obj['active']:continue
                need(fraction(im,project(obj['box']),'foreground')>.006,'成片缺少执行模型中的对象：'+obj['id'])
                need(fraction(im,project(obj['labelBox']),'ink')>.012,'成片文字槽不可辨认：'+obj['id'])
                labels+=1
        checks+=1
    return {'sampledFrames':checks,'labelPresenceChecks':labels,'limits':'像素存在、几何与边界；语义关系与观感仍需连续片段复核'}
