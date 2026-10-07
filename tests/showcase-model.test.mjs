import test from 'node:test';
import assert from 'node:assert/strict';
import {LOOKS,buildLook,validateKingston,motionReady,progressForRun,EFFECT_PARAMS,customColor,whiteLook,effectLook,gradientColors,validateTarget,matchingWholeSettings,sharedCapabilities} from '../web/ops/showcase/demo-model.mjs';
test('paired state distinguishes matched settings, partial changes and independent saved slots',()=>{
 const state={reported_controls:{power:'on',brightness:30,color_mode:'sequence',preset:'rainbow-road',speed:0}};
 const pair={'bench-kit':structuredClone(state),'shop-string-1':structuredClone(state)};
 assert.equal(matchingWholeSettings(pair),true);
 pair['shop-string-1'].reported_controls.brightness=60;assert.equal(matchingWholeSettings(pair),false);
 for(const s of Object.values(pair))s.reported_controls={power:'on',brightness:30,color_mode:'individual',slot:'custom-3'};
 assert.equal(matchingWholeSettings(pair),false);
 for(const s of Object.values(pair))s.reported_controls={power:'on',brightness:30,color_mode:'white'};
 assert.equal(matchingWholeSettings(pair),false);
});
test('paired controls expose only shared capabilities',()=>{
 const a={presets:['rainbow-road','fade-3'],rgb:true,cct_range:[2200,6500],speed_range:[-10,10],brightness_range:[1,100]};
 const b={...a,presets:['fade-3'],cct_range:[2700,5000]};
 const caps=sharedCapabilities({'bench-kit':{capabilities:a},'shop-string-1':{capabilities:b}});
 assert.deepEqual(caps.presets,['fade-3']);assert.deepEqual(caps.cct_range,[2700,5000]);
});
test('designer validates color, temperature and supported effect ranges without slot writes',()=>{
 assert.deepEqual(customColor('#00BBAA',60),{kind:'rgb',rgb:[0,187,170],brightness:60});
 assert.throws(()=>customColor('#zzzzzz',60));assert.throws(()=>whiteLook(9000,60));
 assert.deepEqual(effectLook('jump-3',10,60,['jump-3']),{kind:'preset',preset:'jump-3',speed:10,brightness:60});
 assert.throws(()=>effectLook('unknown',0,60,['jump-3']));assert.throws(()=>effectLook('jump-3',11,60,['jump-3']));
 const colors=gradientColors('#ff0000','#0000ff');assert.equal(colors.length,30);assert.deepEqual(colors[0],[255,0,0]);assert.deepEqual(colors[29],[0,0,255]);
});
test('second module identity is checked independently',()=>{
 const d={id:'shop-string-1',label:'String light 1 (shop module)',simulated:false};
 const s={driver:'afero',simulated:false,head_count:{expected:30}};
 assert.doesNotThrow(()=>validateTarget(d,s,'shop-string-1'));
 assert.throws(()=>validateTarget(d,s,'bench-kit'));
 assert.throws(()=>validateTarget(d,{...s,simulated:true},'shop-string-1'));
});
test('presentation whole-look actions never include slot or framebuffer writes',()=>{
 for(const x of LOOKS){const payload=buildLook(x.id,40);assert.equal(payload.brightness,40);assert.ok(['white','rgb','preset'].includes(payload.kind));assert.ok(!('slot' in payload));assert.ok(!JSON.stringify(payload).includes('frameBuffer'));}
 assert.throws(()=>buildLook('unknown',40));assert.throws(()=>buildLook('warm',0));assert.throws(()=>buildLook('warm',101));
});
test('presentation refuses a changed target and simulator pretending to be live',()=>{
 const device={id:'bench-kit',label:'Kingston test (bench kit)',simulated:false};
 const state={simulated:false,driver:'afero',head_count:{expected:30}};
 assert.doesNotThrow(()=>validateKingston(device,state));
 assert.throws(()=>validateKingston({...device,label:'String light 1'},state));
 assert.throws(()=>validateKingston({...device,simulated:true},state));
 assert.throws(()=>validateKingston(device,{...state,driver:'sim'}));
});
test('motion demo requires the commissioned map; ordinary connection is insufficient',()=>{
 const state={simulated:false,t1:{allowed:true},head_count:{mapped:true,reported:60,controller_addresses:Array.from({length:30},(_,i)=>i)}};
 assert.equal(motionReady(state),true);
 assert.equal(motionReady({...state,t1:{allowed:false}}),false);
 assert.equal(motionReady({...state,head_count:{...state.head_count,reported:30}}),false);
 assert.equal(motionReady({...state,head_count:{...state.head_count,controller_addresses:[...state.head_count.controller_addresses].reverse()}}),false);
});
test('demo remains the observed bounded candidate and countdown starts at the observation step',()=>{
 assert.deepEqual(EFFECT_PARAMS,{slot:'custom-3',field:'motionEffect',code:1,speed:30,duration_s:20,pattern:'blocks'});
 assert.equal(progressForRun({steps:[]},10000).phase,'preparing');
 const run={steps:[{t:10,message:'Observe candidate motionEffect 1 for 20 seconds; behavior is unverified.'}]};
 assert.deepEqual(progressForRun(run,15000),{phase:'observing',remaining:15,fraction:.25});
 assert.deepEqual(progressForRun(run,31000),{phase:'restoring',remaining:0,fraction:1});
});
