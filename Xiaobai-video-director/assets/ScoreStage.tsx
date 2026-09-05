import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';

// This is the only temporal boundary. Sprites consume compiled poses; they cannot invent time.
const palette:Record<string,string>={paper:'#f6f3e9',ink:'#203b40',teal:'#087f78',gold:'#e8a827',red:'#c86245',line:'#c4d3cb'};
export type CompiledScore={version:number;props:any;score:any;frames:any[];actions:any[];sounds:any[];durationInFrames:number;mixedAudio?:string};
const Sprite:React.FC<{object:any;pose:any;state:any}>=({object:o,pose:d,state})=>{
  const [x,y,w,h]=d.box;const color=palette[o.color];const enabled=o.enabledField?state[o.enabledField]:1;const valid=o.validField?state[o.validField]:1;const isTicket=o.shape==='ticket';
  return <g data-object-id={d.id} transform={`translate(${x+w/2} ${y+h/2}) scale(${d.scale}) translate(${-w/2} ${-h/2})`}>
    <defs><clipPath id={`value-${d.id}`}><rect x={0} y={0} width={w*(d.valueReveal??1)} height={h}/></clipPath></defs>
    {o.shape==='market'&&<>
      <rect x={4} y={9} width={w-8} height={h} rx={25} fill={palette.ink} opacity={.07}/>
      <rect width={w} height={h} rx={25} fill='#fffdf7' stroke={color} strokeWidth={4}/>
      <rect x={26} y={25} width={w-52} height={100} rx={16} fill={color} opacity={.10}/>
      <path d={`M28 ${h-30}H${w-28}`} stroke={palette.line} strokeWidth={3}/>
      {o.value&&d.value!==null&&<text clipPath={`url(#value-${d.id})`} x={w/2} y={80} textAnchor='middle' dominantBaseline='middle' fontSize={d.valueFontSize} fontWeight={700} fill={color}>{d.valueText}</text>}
    </>}
    {o.shape==='robot'&&<>
      <circle cx={w/2} cy={65} r={63} fill={palette.ink}/>
      <rect x={w/2-43} y={42} width={86} height={43} rx={15} fill='#fffdf7'/>
      <ellipse cx={w/2-22} cy={63} rx={8} ry={2+enabled*6} fill={enabled>.5?palette.teal:palette.red}/><ellipse cx={w/2+22} cy={63} rx={8} ry={2+enabled*6} fill={enabled>.5?palette.teal:palette.red}/>
      <rect x={0} y={140} width={w} height={h-145} rx={28} fill='#fffdf7' stroke={enabled>.5?palette.teal:palette.red} strokeWidth={4}/>
      {o.enabledField&&<g transform={`translate(${w/2} ${h-65})`} stroke={enabled>.5?palette.teal:palette.red} strokeWidth={7} fill='none'><path d='M-17 -17a27 27 0 1 0 34 0' opacity={.5+.5*enabled}/><path d='M0 -33V-4'/></g>}
    </>}
    {isTicket&&<>
      <rect x={3} y={6} width={w} height={h} rx={18} fill={palette.ink} opacity={.09}/>
      <rect width={w} height={h} rx={18} fill={o.color==='red'?'#fae6de':o.color==='gold'?'#fff0c6':'#d9eee8'} stroke={color} strokeWidth={4}/>
      {o.statusField&&state[o.statusField]>=.999&&<g transform={`translate(${w-16} ${h-14})`}><circle r={17} fill={color}/><path d='M-8 0l6 7 12-15' stroke='white' strokeWidth={3} fill='none'/></g>}
      {o.value&&d.value!==null&&<text clipPath={`url(#value-${d.id})`} x={w/2} y={h*.66} textAnchor='middle' dominantBaseline='middle' fontSize={d.valueFontSize} fontWeight={750} fill={color}>{d.valueText}</text>}
    </>}
    {o.shape==='meter'&&<>
      <circle cx={w/2} cy={h/2} r={Math.min(w,h)/2-6} fill='#fffdf7' stroke={color} strokeWidth={4}/>
      {o.validField&&<g transform={`translate(${w/2} ${h/2})`} fill='none' strokeWidth={10} strokeLinecap='round'><path d='M-32 0l22 24 46-52' stroke={palette.teal} opacity={valid}/><path d='M-26 -26l52 52m0-52l-52 52' stroke={palette.red} opacity={1-valid}/></g>}
      {o.value&&d.value!==null&&<text clipPath={`url(#value-${d.id})`} x={w/2} y={h/2} textAnchor='middle' dominantBaseline='middle' fontSize={d.valueFontSize} fontWeight={700} fill={color}>{d.valueText}</text>}
    </>}
    {o.shape==='brace'&&<path d={`M15 5V${h/2}Q${w/2} ${h/2} ${w/2} ${h-10}Q${w/2} ${h/2} ${w-15} ${h/2}V5`} fill='none' stroke={color} strokeWidth={4}/>}
    <text data-label-for={d.id} x={w/2} y={o.labelPlacement==='above'?-27:30} textAnchor='middle' dominantBaseline='middle' fontSize={32} fontWeight={650} fill={palette.ink}>{o.label}</text>
  </g>;
};
export const ScoreStage:React.FC<CompiledScore>=(compiled)=>{
  const f=useCurrentFrame();const frame=compiled.frames[f];if(!frame) throw Error('Missing compiled frame');
  const {score,props}=compiled;const camera=frame.camera;const t=f/30;
  const clause=score.clauses.find((c:any)=>c.id===frame.clause);
  const cap=props.captions.find((c:any)=>c.startMs<=t*1000&&t*1000<c.endMs);
  const moving=compiled.actions.filter((a:any)=>['move','transfer','connect'].includes(a.kind)&&a.start<=t&&(a.kind==='connect'||t<=a.end));
  const byId=Object.fromEntries(frame.objects.map((p:any)=>[p.id,p]));
  return <AbsoluteFill style={{backgroundColor:palette.paper,fontFamily:'"PingFang SC", "Noto Sans CJK SC", "Noto Sans SC", "Microsoft YaHei", sans-serif',color:palette.ink}}>
    <AbsoluteFill style={{backgroundImage:'radial-gradient(#243b4016 .7px,transparent .7px)',backgroundSize:'24px 24px'}}/>
    <svg width={1920} height={1080} viewBox='0 0 1920 1080'>
      <text x={112} y={139} fontSize={65} fontWeight={700} fill={palette.ink}>{clause?.heading??score.title}</text>
      <g transform={`translate(960 540) scale(${camera.zoom}) translate(${-camera.x} ${-camera.y})`}>
        {(clause?.relations??[]).filter((r:any)=>r.type==='produces').map((r:any)=>{
          const a=byId[r.from],b=byId[r.to]; if(!a||!b||a.scale<.99||b.scale<.99)return null;
          // A source above two targets is a causal branch. Transfers use their own port-to-port trace.
          if(a.box[1]+a.box[3]>=b.box[1])return null;
          const x=a.box[0]+a.box[2]/2,y=a.box[1]+a.box[3]+8;
          const tx=b.box[0]+b.box[2]/2,ty=b.box[1]-10;
          return <g key={r.from+r.to} stroke={palette.line} strokeWidth={4} fill='none'><path d={`M${x} ${y}Q${tx} ${y} ${tx} ${ty}`}/><path d={`M${tx-8} ${ty-10}L${tx} ${ty}L${tx+8} ${ty-10}`}/></g>;
        })}
        {moving.map((a:any)=>{
          if(a.kind==='connect'){
            const one=byId[a.targets[0]],two=byId[a.targets[1]];if(!one||!two)return null;
            const u=Math.min(1,(t-a.start)/a.duration); const x1=one.box[0]+one.box[2]/2,x2=two.box[0]+two.box[2]/2;
            return <g key={a.id}><path d={`M${x1} ${one.box[1]+one.box[3]+30}H${x1+(x2-x1)*u}`} fill='none' stroke={palette.teal} strokeWidth={5}/>{u>.65&&<text x={(x1+x2)/2} y={one.box[1]+one.box[3]+90} textAnchor='middle' fontSize={44} fontWeight={700} fill={palette.teal}>{`${a.valueLabel??''}${Number(frame.state[a.valueField].toFixed(1))}${a.valueSuffix??''}`}</text>}</g>;
          }
          const p=a.fromBox,q=a.toBox;
          return <path key={a.id} d={`M${p[0]+p[2]/2} ${p[1]+p[3]/2}L${q[0]+q[2]/2} ${q[1]+q[3]/2}`} stroke={palette.line} strokeWidth={4} strokeDasharray='7 12'/>;
        })}
        {[...frame.objects].sort((a:any,b:any)=>(score.objects[a.id].layer??0)-(score.objects[b.id].layer??0)).map((d:any)=><Sprite key={d.id} object={score.objects[d.id]} pose={d} state={frame.state}/>)}
        {compiled.actions.filter((a:any)=>a.kind==='change'&&a.start<=t&&t<a.end).flatMap((a:any)=>a.targets.map((oid:string)=>{
          const d=byId[oid];if(!d)return null; const [x,y,w,h]=d.box,u=(t-a.start)/a.duration;
          if(d.value===null||!d.changeDirection)return null;const downward=d.changeDirection<0;
          return <path key={a.id+oid} d={`M${x+w+22} ${y+h*.72}V${y+h*.72+(downward?1:-1)*h*.5*u}m-9 ${downward?-10:10}l9 ${downward?10:-10} 9 ${downward?-10:10}`} fill='none' stroke={palette[score.objects[oid].color]} strokeWidth={5} strokeLinecap='round'/>;
        }))}
      </g>
      <text x={960} y={951} textAnchor='middle' fontSize={40} fontWeight={500} fill={palette.ink}>{cap?.text.replace(/[，。]$/u,'')??''}</text>
    </svg>
  </AbsoluteFill>;
};
