import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {CANOPY,designPaint,designLevels,defaultDesignLevel,sceneDesignView,nextDesignLevel,scenePainters,sceneColorAt} from '../web/ops/showcase/demo-model.mjs';

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
  assert.equal(defaultDesignLevel(design({ladder:['stepped','modules','strand'],design:'sample'})),'modules');  // D65: Coastal Tides
  assert.equal(defaultDesignLevel(design({ladder:['canvas','slot','stepped','modules','strand']})),'heads');  // D65: Golden Hour
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

test('the scene view paints the design at the level chosen, else the level the show tries first',()=>{
  const d=design();
  for(const [chosen,level] of [[null,'heads'],['heads','heads'],['modules','modules'],['strand','heads']]){
    const view=sceneDesignView(d,chosen);
    assert.equal(view.state,'design');assert.equal(view.level,level);
    for(const h of CANOPY.heads)assert.deepEqual(view.paint.head(h),rgb(level==='modules'?moduleRgb(h.module):headRgb(h)));
  }
  // a modules-first scene opens at module level and the toggle still reaches head by head
  const sample=design({ladder:['modules','strand'],design:'sample'});
  assert.equal(sceneDesignView(sample,null).level,'modules');
  assert.equal(sceneDesignView(sample,'heads').level,'heads');
  // a level the design lacks falls back to the one it has
  assert.equal(sceneDesignView(design({modules:null}),'modules').level,'heads');
});

test('the scene view says loading, error, none or mismatch instead of painting',()=>{
  assert.deepEqual(sceneDesignView(undefined,null),{state:'loading',level:null,paint:null});
  assert.deepEqual(sceneDesignView({error:'no answer'},'heads'),{state:'error',level:null,paint:null,error:'no answer'});
  const none=sceneDesignView(design({heads:null,modules:null}),null);
  assert.equal(none.state,'none');assert.equal(none.paint,null);
  const short=design();short.heads=short.heads.slice(1);
  const mismatch=sceneDesignView(short,'heads');
  assert.equal(mismatch.state,'mismatch');assert.equal(mismatch.level,'heads');assert.equal(mismatch.paint,null);
});

test('the toggle goes from the level shown to the other one',()=>{
  assert.equal(nextDesignLevel(sceneDesignView(design(),null)),'modules');
  assert.equal(nextDesignLevel(sceneDesignView(design(),'modules')),'heads');
  assert.equal(nextDesignLevel(sceneDesignView(design({ladder:['modules'],design:'sample'}),null)),'heads');
});

test('the illustration paints from the design when it has one, the palette left to right only without',()=>{
  const id='coastal-tides',view=sceneDesignView(design(),null),painters=scenePainters(view,id);
  assert.equal(painters.source,'design');
  for(const h of CANOPY.heads)assert.deepEqual(painters.head(h),rgb(headRgb(h)));
  assert.deepEqual(painters.spill(.5),view.paint.spill(.5));
  const modules=scenePainters(sceneDesignView(design(),'modules'),id);
  for(const h of CANOPY.heads)assert.deepEqual(modules.head(h),rgb(moduleRgb(h.module)));
  const short=design();short.heads=short.heads.slice(1);
  for(const fallback of [sceneDesignView({error:'x'},null),sceneDesignView(short,'heads'),sceneDesignView(design({heads:null,modules:null}),null)]){
    const p=scenePainters(fallback,id,CANOPY.width);
    assert.equal(p.source,'palette');
    for(const h of CANOPY.heads)assert.deepEqual(p.head(h),sceneColorAt(id,h.x/CANOPY.width));
    assert.deepEqual(p.spill(.25),sceneColorAt(id,.25));
  }
});

// studio.js needs a browser (DOM, /static imports), so its wiring is checked in the source: the pure parts above are
// what it calls, and these pin that it calls them.
test('studio.js loads the design for the scene, views it at S.level and paints through it',()=>{
  const js=readFileSync(new URL('../web/ops/showcase/studio.js',import.meta.url),'utf8').replace(/(^|\s)\/\/.*$/gm,'');
  const body=name=>{const i=js.indexOf('function '+name+'(');assert.ok(i>=0,name+' is missing');const j=js.indexOf('\nfunction ',i+1);return js.slice(i,j<0?undefined:j);};
  assert.match(body('sceneView'),/return sceneDesignView\(designs\.get\(S\.scene\),S\.level\);/);
  assert.match(body('loadDesign'),/designs\.set\(id,await api\('\/api\/canopy\/design\?mode='\+encodeURIComponent\(id\)\)\)/);
  assert.match(body('selectScene'),/S\.scene=id;[^\n]*loadDesign\(id\);/);
  const scene=body('renderCanopy');
  assert.match(scene,/view=sceneView\(\)/);
  assert.match(scene,/const painters=scenePainters\(view,S\.scene,canopyView\.layout\.width\);\s*paintCanopy\(canopyView,painters\.head,painters\.spill,brightness\);/);
  assert.equal((js.match(/paintCanopy\(/g)||[]).length,1,'the Studio paints once, through scenePainters (D67: it is screen only)');
  assert.match(js,/\$\('design-level'\)\.addEventListener\('click',\(\)=>\{S\.level=nextDesignLevel\(sceneView\(\)\);/);
});
