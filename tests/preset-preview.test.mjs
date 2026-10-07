import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {previewFrame, previewKey, previewNotice} from '../web/ops/module/preset-preview.js';

const spec = style => ({name: style, style, available: true, animated: true, brightness: 100, speed: 0});
test('RGB jumps advance through the real three primary colors', () => {
  assert.deepEqual(previewFrame(spec('jump'), 2, 0), [{r:255,g:0,b:0},{r:255,g:0,b:0}]);
  assert.deepEqual(previewFrame(spec('jump'), 1, 8), [{r:0,g:255,b:0}]);
  assert.deepEqual(previewFrame(spec('jump'), 1, 16), [{r:0,g:0,b:255}]);
});
test('observed Fade 3 behavior includes a dark interval', () => {
  assert.deepEqual(previewFrame(spec('fade-black'), 1, 8), [{r:0,g:0,b:0}]);
  assert.deepEqual(previewFrame(spec('fade-black'), 1, 12), [{r:0,g:255,b:0}]);
});
test('rainbow gradient stays static in pairs while travel changes over time', () => {
  const gradient = previewFrame(spec('gradient'), 60, 0);
  assert.deepEqual(gradient, previewFrame(spec('gradient'), 60, 30));
  for(let i=0;i<60;i+=2) assert.deepEqual(gradient[i],gradient[i+1]);
  assert.notDeepEqual(previewFrame(spec('travel'),60,0),previewFrame(spec('travel'),60,4));
});
test('speed slows animation, unknown models have no fabricated frame', () => {
  assert.deepEqual(previewFrame({...spec('jump'),speed:-3},1,8),[{r:255,g:0,b:0}]);
  assert.equal(previewFrame({...spec('unavailable'),available:false},30,1),null);
  assert.match(previewNotice({...spec('jump')},false),/not synchronized/);
});
test('travel moves the same colors to the next LED without fading their brightness', () => {
  const before = previewFrame(spec('travel'), 30, 0);
  const after = previewFrame(spec('travel'), 30, 0.81);
  for (let i = 0; i < 30; i++) assert.deepEqual(after[(i + 1) % 30], before[i]);
});
test('periodic state refresh preserves animation phase and paused state', () => {
  const source = fs.readFileSync(new URL('../web/ops/module/module.js',import.meta.url),'utf8')
    .replace(/^import .*;$/gm,'').replace(/\nmain\(\);\s*$/, '');
  const elements = new Map();
  let now = 1000;
  const context = {previewFrame,previewKey,previewNotice,performance:{now:()=>now},
    window:{matchMedia:()=>({matches:true})},document:{hidden:false,getElementById:id=>{
      if(!elements.has(id))elements.set(id,{});return elements.get(id);}},
    cancelAnimationFrame:()=>{},requestAnimationFrame:()=>1,setSimBadge:()=>{}};
  vm.createContext(context);
  vm.runInContext(source + '\nbuildControls=()=>{S.built=true}; renderAll=()=>{}; globalThis.exposed={S,applyState,setPreviewRate,previewSeconds};',context);
  const {S,applyState,setPreviewRate,previewSeconds} = context.exposed;
  S.lease='test';
  const state={heads:[],preview:{reported:spec('travel'),desired:spec('travel')}};
  applyState(state);
  assert.equal(S.previewStarted,1000);
  assert.equal(S.previewPaused,true);
  now=6000;
  applyState({...state,reported_at:123456});
  assert.equal(S.previewStarted,1000);
  assert.match(elements.get('preview-note').textContent,/paused/);
  applyState({...state,preview:{reported:{...spec('travel'),speed:5}}});
  assert.equal(S.previewStarted,6000);
  S.previewPaused=false;
  now=7000;
  assert.equal(previewSeconds(now),1);
  setPreviewRate(2);
  assert.equal(previewSeconds(now),1, 'Changing viewport speed preserves phase');
  now=8000;
  assert.equal(previewSeconds(now),3, 'Only the local preview clock advances faster');
  setPreviewRate(100);
  assert.equal(S.previewRate,2,'Out-of-range correction is refused');
});
