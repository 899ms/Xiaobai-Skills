import React from 'react';
import {Composition,registerRoot} from 'remotion';
import {ScoreStage,type CompiledScore} from './ScoreStage';
const defaults={version:2,props:{},score:{},frames:[],actions:[],sounds:[],durationInFrames:1} as CompiledScore;
registerRoot(()=> <Composition id='DirectedScore' component={ScoreStage} defaultProps={defaults} width={1920} height={1080} fps={30} durationInFrames={1} calculateMetadata={({props})=>({durationInFrames:props.durationInFrames})}/>);
