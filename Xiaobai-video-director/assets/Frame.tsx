import React from "react";
import {AbsoluteFill, Audio, Easing, interpolate, Sequence, staticFile, useCurrentFrame, useVideoConfig} from "remotion";

export type PilotProps = {
  title: string;
  audioSrc: string;
  leadFrames: number;
  playbackRate: number;
  durationInFrames: number;
  cues: Record<string, number>;
  captions: {text: string; startMs: number; endMs: number; timestampMs: null; confidence: null}[];
};

export type SoundTimeline = {
  events: Record<string, {cue?: string; atSec?: number; offsetSec?: number}>;
  sounds?: Record<string, {event: string; kind: string; meaning: string; src: string; gainDb: number; durationSec: number}>;
};

export const P = {paper:"#f4f1e9", ink:"#243b40", muted:"#71817e", line:"#c9d1c6", teal:"#197e70", yellow:"#f1b646", red:"#c65e40"};
const ease = Easing.bezier(.65, 0, .2, 1);
export const track = (t:number, keys:[number,number][]) => interpolate(t, keys.map(k=>k[0]), keys.map(k=>k[1]), {easing:ease, extrapolateLeft:"clamp", extrapolateRight:"clamp"});
export const ramp = (t:number, start:number, duration=.8) => track(t, [[start,0],[start+duration,1]]);

export const SemanticSoundtrack:React.FC<{props:PilotProps; timeline:SoundTimeline}> = ({props,timeline}) => {
  const {fps} = useVideoConfig();
  return <>{Object.entries(timeline.sounds ?? {}).map(([id,sound]) => {
    const event = timeline.events[sound.event];
    const atSec = (event.atSec ?? props.cues[event.cue!]/1000) + (event.offsetSec ?? 0);
    return <Sequence key={id} from={Math.round(atSec*fps)} durationInFrames={Math.max(1,Math.round(sound.durationSec*fps))} layout="none">
      <Audio src={staticFile(sound.src)} volume={10**(sound.gainDb/20)}/>
    </Sequence>;
  })}</>;
};

export const PilotFrame:React.FC<{props:PilotProps; heading:string; headingAt:number; footer?:string; timeline?:SoundTimeline; children:React.ReactNode}> = ({props,heading,headingAt,footer,timeline,children}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame/fps;
  const enter = ramp(t,headingAt,.6);
  const subtitle = props.captions.find(cap=>t*1000>=cap.startMs && t*1000<cap.endMs);
  return <AbsoluteFill style={{backgroundColor:P.paper,color:P.ink,fontFamily:'"PingFang SC", "Noto Sans CJK SC", "Noto Sans SC", "Microsoft YaHei", sans-serif'}}>
    <Sequence from={props.leadFrames} layout="none"><Audio src={staticFile(props.audioSrc)} playbackRate={props.playbackRate}/></Sequence>
    {timeline && <SemanticSoundtrack props={props} timeline={timeline}/>}
    <AbsoluteFill style={{backgroundImage:"radial-gradient(#243b4020 .8px, transparent .8px)",backgroundSize:"26px 26px",opacity:.24}}/>
    <div style={{position:"absolute",left:108,top:72,fontSize:75,fontWeight:650,letterSpacing:-3,opacity:enter,translate:`0 ${18*(1-enter)}px`}}>{heading.replace(/[，。！？：；、,.!?:;]+$/u,"")}</div>
    {children}
    <div style={{position:"absolute",bottom:111,left:100,right:100,textAlign:"center",fontSize:39,fontWeight:500,letterSpacing:.3,minHeight:58}}>{subtitle?.text.replace(/[，。]$/u,"") ?? ""}</div>
    <div style={{position:"absolute",left:110,right:110,bottom:72,height:2,background:P.line}}><div style={{height:2,width:`${100*frame/(props.durationInFrames-1)}%`,background:P.teal}}/></div>
    {footer && <div style={{position:"absolute",left:110,bottom:29,fontSize:18,color:P.muted}}>{footer}</div>}
  </AbsoluteFill>;
};
