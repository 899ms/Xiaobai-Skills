"""Failure injection; optional --candidate checks real media. No TTS or render calls."""
import copy
import json
from pathlib import Path
import score as s
import director as d


def main():
    from check_score_render import check
    print(json.dumps(check(),ensure_ascii=False))
    fixture=json.loads((s.ROOT/'assets/score.example.json').read_text())
    original=copy.deepcopy(fixture)
    good=s.compile_score(fixture['score'],fixture['props'])
    assert fixture==original,'Compiler must not mutate its input'
    checks=[]
    def rejects(name,mutate):
        data=copy.deepcopy(fixture);mutate(data)
        try:s.compile_score(data['score'],data['props'])
        except (s.ScoreError,KeyError):checks.append(name);return
        raise AssertionError('Expected blocked: '+name)
    def action(data,name):return next(a for a in data['score']['actions'] if a['id']==name)
    rejects('legacy declaration format',lambda d:d['score'].update(version=1))
    rejects('noun label drift',lambda d:d['score']['objects']['left'].update(label='项目'))
    rejects('unaccounted narration',lambda d:d['score']['clauses'][0]['coverage'].pop())
    rejects('claim without executor',lambda d:d['score']['clauses'][0]['coverage'][1].update(action='not-implemented'))
    rejects('early reveal',lambda d:action(d,'show-right').update(delay=-1))
    rejects('hedge before short filled',lambda d:action(d,'issue-long').update(cue='short'))
    rejects('wrong model initial state',lambda d:action(d,'buy')['effects']['longPosition'].update(**{'from':2}))
    rejects('broken hedge conservation',lambda d:action(d,'buy')['effects']['longPosition'].update(to=2))
    rejects('unrelated backdrop remains',lambda d:action(d,'leave-market').update(cue='closing',delay=1.35))
    rejects('no final exit',lambda d:d['score']['actions'].remove(action(d,'close')))
    rejects('unknown label-changing shortcut',lambda d:action(d,'buy').update(label='资金费'))
    rejects('offscreen receiver',lambda d:d['score']['objects']['right'].update(receiver=[800,800,240,140]))
    rejects('discontinuous position jump',lambda d:action(d,'focus-short').update(fromBox=[800,750,180,100]))
    rejects('text collision',lambda d:d['score']['objects']['price'].update(box=[410,485,170,170]))
    rejects('paragraph overloaded',lambda d:(d['props']['captions'][0].update(text='一个句子里的内容太多了'*4),d['score']['clauses'][0].update(text='一个句子里的内容太多了'*4)))
    rejects('cyclic model dependency',lambda d:d['score']['model']['derived'].update(leftPrice='leftPrice'))
    rejects('numeric value too wide',lambda d:d['score']['objects']['price']['value'].update(suffix='一个塞不进圆圈的非常长的数字单位'))
    rejects('unknown visible power state',lambda d:d['score']['objects']['robot'].update(enabledField='nonexistent'))
    rejects('transaction disguised as staging',lambda d:action(d,'buy').update(stagingFor='close'))
    rejects('unknown discrete state',lambda d:d['score']['model'].update(discrete=['unknown-counter']))
    discrete=copy.deepcopy(fixture);discrete['score']['model']['discrete']=['rise']
    dc=s.compile_score(discrete['score'],discrete['props']);rise=next(a for a in dc['actions'] if a['id']=='rise-two')
    assert dc['frames'][round((rise['start']+rise['duration']*.5)*30)]['state']['rise']==0
    assert dc['frames'][round((rise['end']+.1)*30)]['state']['rise']==2
    # The model, graphical states and simultaneous positions are independently inspected.
    at=lambda t:good['frames'][round(t*30)]
    buy=next(a for a in good['actions'] if a['id']=='buy')
    assert at(buy['end']-.1)['state']['longPosition']==0,'Transportation commits only on arrival'
    assert at(buy['end']+.1)['state']['shortPosition']==1
    assert at(buy['end']+.1)['state']['longPosition']==1
    assert {'short','long'}.issubset(o['id'] for o in at(15)['objects'])
    assert not {'left','right','robot'}.intersection(o['id'] for o in at(15)['objects'])
    assert at(19)['state']['shortPnl']==-2 and at(19)['state']['longPnl']==2
    assert all(abs(f['state']['netPnl'])<1e-6 for f in good['frames'] if f['state']['longPosition']==1)
    assert not good['frames'][-1]['objects']
    print(json.dumps({'pass':True,'negativeCases':checks,'frameStates':len(good['frames']),'simultaneousPositions':True,'clearAfterFocus':True,'pnlConservation':True},ensure_ascii=False))

def media_checks(video):
    import tempfile,shutil
    base=video.parent;sp=base/'score.json';pp=base/'props.json'
    compiled=s.verify_receipt(sp,pp,video)
    audio=s.verify_audio(compiled,video)
    qa=s.read(base/(video.stem+'-review')/'review.json')
    blocked=[]
    with tempfile.TemporaryDirectory(prefix='director-negative-proof-') as tmp:
        temp=Path(tmp);final=temp/'must-not-exist.mp4'
        def rejects(name,call):
            try:call()
            except s.ScoreError as e:blocked.append({'case':name,'reason':str(e)});assert not final.exists();return
            raise AssertionError('Expected media blocked: '+name)
        quiet=temp/'without-sfx.wav';s.write_mix({**compiled,'sounds':[]},base,quiet)
        rejects('missing audible SFX',lambda:s.verify_audio(compiled,quiet,video.with_suffix('.mix.wav')))
        import wave
        delayed=temp/'delayed.wav'
        with wave.open(str(video.with_suffix('.mix.wav')),'rb') as source:
            raw=source.readframes(source.getnframes());params=source.getparams()
        with wave.open(str(delayed),'wb') as target:
            target.setparams(params);target.writeframes(bytes(2048)+raw[:-2048])
        rejects('43 ms audio delay',lambda:s.verify_audio(compiled,delayed,video.with_suffix('.mix.wav')))
        rejects('pending review cannot release',lambda:s.release(sp,pp,video,base/(video.stem+'-review')/'review.json',final))
        clone=temp/video.name
        for suffix in ['.mp4','.receipt.json','.compiled.json','.mix.wav']:
            shutil.copy2(video.with_suffix(suffix),clone.with_suffix(suffix))
        for suffix in ['.mp4','.compiled.json','.mix.wav']:
            target=clone.with_suffix(suffix);original=target.read_bytes();target.write_bytes(original+(b' ' if suffix=='.compiled.json' else b'tamper'))
            rejects('tampered '+suffix,lambda:s.verify_receipt(sp,pp,clone))
            target.write_bytes(original)
        # Adversarial fixture, never a production observation: even plausible assertions
        # and a real PNG with its correct hash cannot authorize an unrelated frame.
        fake=copy.deepcopy(qa)
        fake['observations']=[{'clause':c['id'],'status':'pass','observed':'TEST ONLY fabricated distinct observation '+str(i),'meaning':'TEST ONLY not a genuine visual review'} for i,c in enumerate(compiled['score']['clauses'])]
        fake['learnedChecks']=[{'id':r['id'],'status':'pass','actions':[compiled['actions'][0]['id']],'note':'TEST ONLY fabricated rule observation'} for r in compiled['learnedRules']]
        fake['playback']={k:True for k in ['observed','relationsClear','attentionClear','audioAudible']}
        fake['playback']['note']='TEST ONLY fabricated playback declaration; must not be released'
        fake['verdict']='pass'
        incomplete=copy.deepcopy(fake);incomplete['learnedChecks'].pop()
        rejects('missing historical gate',lambda:s.review_gate(compiled,video,incomplete))
        wrong=next(e for e in fake['evidence'] if e['action']=='show-left' and e['phase']=='mid')
        target=next(e for e in fake['evidence'] if e['action']=='show-left' and e['phase']=='settled')
        target.update(image=wrong['image'],sha256=wrong['sha256'])
        bad=temp/'adversarial-review.json';s.save(bad,fake)
        rejects('unrelated valid PNG cannot release',lambda:s.release(sp,pp,video,bad,final))
    print(__import__('json').dumps({'mediaChecks':True,'audio':audio,'blocked':blocked},ensure_ascii=False))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--candidate',type=Path);args=parser.parse_args()
    main()
    if args.candidate:media_checks(args.candidate.resolve())
