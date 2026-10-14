import test from 'node:test';
import assert from 'node:assert/strict';
import {CANOPY,designPaint,designLevels,defaultDesignLevel} from '../web/ops/showcase/demo-model.mjs';

// A design shaped like GET /api/canopy/design: every head a colour from its own canopy point, every module its own.
const headRgb=h=>[Math.round(h.x),Math.round(h.y),7];
const MODULE_IDS=[...new Set(CANOPY.heads.map(h=>h.module))];
const moduleRgb=id=>[10*Number(id[1]),10*Number(id[3]),99];
function design(extra={}){
  // the server lists heads per module in lattice order, not the Studio's wire order: reversed here on purpose
  const heads=[...CANOPY.heads].reverse().map(h=>({module:h.module,x:h.x,y:h.y,rgb:headRgb(h)}));
  return {mode:'test',ladder:['canvas','modules'],design:'canvas',heads,modules:MODULE_IDS.map(id=>({module:id,rgb:moduleRgb(id)})),sample_at:'centre',...extra};
}
const rgb=a=>({r:a[0],g:a[1],b:a[2]});

test('head by head paints each light the design colour at its own canopy point, not x / 225',()=>{
  const paint=designPaint(design(),'heads');
  assert.ok(paint);
  for(const h of CANOPY.heads)assert.deepEqual(paint.head({...h,device:null}),rgb(headRgb(h)));
  // two heads in the same column but different rows differ (a 2-D design), which x / width never could
  const [a,b]=[CANOPY.heads.find(h=>h.x===7.5&&h.y===15),CANOPY.heads.find(h=>h.x===7.5&&h.y===115)];
  assert.notDeepEqual(paint.head(a),paint.head(b));
});

test('positions key at half-inch resolution, so float noise from the server never misses a head',()=>{
  const d=design();d.heads=d.heads.map(h=>({...h,x:h.x+0.0001,y:h.y-0.0001}));
  const paint=designPaint(d,'heads');
  assert.ok(paint);
  for(const h of CANOPY.heads)assert.deepEqual(paint.head(h),rgb(headRgb(h)));
});

test('module level paints all 30 lights of a panel its one module-level colour',()=>{
  const paint=designPaint(design(),'modules');
  assert.ok(paint);
  for(const h of CANOPY.heads)assert.deepEqual(paint.head(h),rgb(moduleRgb(h.module)));
  assert.equal(new Set(CANOPY.heads.map(h=>JSON.stringify(paint.head(h)))).size,9);
});

test('a design that misses a head, or lacks the level, paints nothing (the Studio says so and falls back)',()=>{
  const d=design();d.heads=d.heads.slice(1);
  assert.equal(designPaint(d,'heads'),null);
  const m=design();m.modules=m.modules.filter(x=>x.module!=='r2c2');
  assert.equal(designPaint(m,'modules'),null);
  assert.equal(designPaint(design({modules:null}),'modules'),null);
  assert.equal(designPaint(design({heads:null}),'heads'),null);
  assert.equal(designPaint(design(),'strand'),null);
  assert.equal(designPaint(null,'heads'),null);
});

test('the default level is the one the show tries first for the scene',()=>{
  assert.equal(defaultDesignLevel(design()),'heads');
  assert.equal(defaultDesignLevel(design({ladder:['modules','strand'],design:'sample'})),'modules');
  assert.equal(defaultDesignLevel(design({ladder:['modules','strand'],modules:null})),'heads');
  assert.equal(defaultDesignLevel(design({heads:null,modules:null})),null);
  assert.deepEqual(designLevels(design()),['heads','modules']);
  assert.deepEqual(designLevels(design({heads:null})),['modules']);
});

test('the floor glow under each third is the mean of the lights above it',()=>{
  const paint=designPaint(design(),'modules');
  const left=['r0c0','r1c0','r2c0'].map(moduleRgb);
  const mean=k=>Math.round(left.reduce((s,c)=>s+c[k],0)/3);
  assert.deepEqual(paint.spill(.15),{r:mean(0),g:mean(1),b:mean(2)});
});
