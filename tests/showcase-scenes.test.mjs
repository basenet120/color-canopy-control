import test from 'node:test';
import assert from 'node:assert/strict';
import {SCENES,sceneFrame,sceneColorAt} from '../web/ops/showcase/demo-model.mjs';
import {CANOPY,displayLayout} from '../web/ops/showcase/demo-model.mjs';

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
test('full canopy uses saved nine-panel geometry, with 30 heads per panel',()=>{
  const view=displayLayout();assert.equal(view.heads.length,270);assert.equal(view.panels.length,9);
  assert.equal(view.width,225);assert.equal(view.height,120);
  assert.deepEqual(view.heads.map(({device,...h})=>h),CANOPY.heads);
  for(const p of view.panels){const heads=view.heads.filter(h=>h.module===p.id);assert.equal(heads.length,30);assert.equal(new Set(heads.map(h=>h.index)).size,30);}
  assert.ok(view.heads.every(h=>h.device===null),'planned panels must not be labeled as live bench devices');
});
test('the Studio draws only the full canopy (D67: no shop-kit view; scope and device change nothing)',()=>{
  const view=displayLayout();assert.equal(view.heads.length,270);
  assert.deepEqual(displayLayout('shop','bench-kit'),view,'the arguments of the old shop view are ignored');
  assert.ok(!('bench' in CANOPY),'no shop-kit maps in the geometry');
  for(const scene of SCENES){const colors=sceneFrame(scene.id,30,100);assert.equal(colors.length,30);assert.ok(new Set(colors.map(c=>JSON.stringify(c))).size>=15);}
});
