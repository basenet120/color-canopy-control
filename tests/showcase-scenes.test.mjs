import test from 'node:test';
import assert from 'node:assert/strict';
import {SCENES,sceneLook,sceneFrame,sceneColorAt} from '../web/ops/showcase/demo-model.mjs';
import {CANOPY,displayLayout} from '../web/ops/showcase/demo-model.mjs';
import {matchingStaticFrames} from '../web/ops/showcase/demo-model.mjs';

test('all six reference palettes produce distinct bounded scene previews',()=>{
  assert.equal(SCENES.length,6);assert.equal(new Set(SCENES.map(s=>s.id)).size,6);
  const frames=SCENES.map(s=>sceneFrame(s.id,270,100));
  assert.equal(new Set(frames.map(x=>JSON.stringify(x))).size,6);
  for(const frame of frames){assert.equal(frame.length,270);for(const rgb of frame)for(const c of Object.values(rgb))assert.ok(Number.isInteger(c)&&c>=0&&c<=255);}
  assert.deepEqual(sceneColorAt('lowcountry-calm',0),{r:247,g:242,b:231});
  assert.deepEqual(sceneColorAt('lowcountry-calm',.5),{r:209,g:234,b:227});
  assert.deepEqual(sceneColorAt('coastal-tides',0),{r:101,g:155,b:213});
  assert.deepEqual(sceneColorAt('goodnight-glow',1),{r:196,g:214,b:243});
});
test('solid palette actions stay whole-strand RGB without slot selection or writes',()=>{
  for(const scene of SCENES)scene.palette.forEach((color,i)=>{
    const look=sceneLook(scene.id,i,30);
    assert.deepEqual(Object.keys(look).sort(),['brightness','kind','rgb']);
    assert.equal(look.kind,'rgb');assert.equal(look.brightness,30);
    assert.equal('#'+look.rgb.map(c=>c.toString(16).padStart(2,'0')).join('').toUpperCase(),color.hex);
  });
  assert.throws(()=>sceneLook('missing',0,30));assert.throws(()=>sceneLook(SCENES[0].id,99,30));
});
test('gradient payloads use 30 distinct RGB steps and match actual static head colors',()=>{
  for(const scene of SCENES){const colors=sceneFrame(scene.id,30,100);assert.equal(colors.length,30);assert.ok(new Set(colors.map(c=>JSON.stringify(c))).size>=15);}
  const state={reported_controls:{power:'on',brightness:30,color_mode:'individual',slot:'custom-1'},slots:[{slot:'custom-1',effects:{motionEffect:0,brightnessEffect:0}}],heads:sceneFrame('golden-hour',30,30).map(reported=>({reported}))};
  const pair={'bench-kit':structuredClone(state),'shop-string-1':structuredClone(state)};
  assert.equal(matchingStaticFrames(pair),true);
  pair['shop-string-1'].heads[15].reported.r+=1;assert.equal(matchingStaticFrames(pair),false);
  pair['shop-string-1']=structuredClone(state);pair['shop-string-1'].slots[0].effects.motionEffect=1;assert.equal(matchingStaticFrames(pair),false);
});
test('full canopy uses saved nine-panel geometry, with 30 heads per panel',()=>{
  const view=displayLayout();assert.equal(view.heads.length,270);assert.equal(view.panels.length,9);
  assert.equal(view.width,225);assert.equal(view.height,120);
  assert.deepEqual(view.heads.map(({device,...h})=>h),CANOPY.heads);
  for(const p of view.panels){const heads=view.heads.filter(h=>h.module===p.id);assert.equal(heads.length,30);assert.equal(new Set(heads.map(h=>h.index)).size,30);}
  assert.ok(view.heads.every(h=>h.device===null),'planned panels must not be labeled as live bench devices');
});
test('shop view uses each module map independently and shows only selected hardware',()=>{
  assert.equal(displayLayout('shop','both').heads.length,60);
  assert.equal(displayLayout('shop','bench-kit').heads.length,30);
  const custom=CANOPY.bench['shop-string-1'].map(h=>({...h,x:75-h.x,y:40-h.y}));
  const view=displayLayout('shop','both',{'shop-string-1':{heads:custom}});
  assert.deepEqual(view.heads.filter(h=>h.device==='shop-string-1').map(h=>({index:h.index,x:h.x-85,y:h.y})),custom);
  assert.equal(new Set(view.heads.map(h=>h.module)).size,2);
});
