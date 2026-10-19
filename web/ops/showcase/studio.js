// The Studio: a screen-only preview of the six scenes over the full canopy (D67). It sends no light command: no
// connect, no shop-kit lease, no module, pair, bench or motion-demo call. The Design Lab (lab.js, #lab) is the only
// live control. The illustration paints the show's own design per scene (GET /api/canopy/design, a read); the chips,
// tiles and copy are the scenes as the show runs them (GET /api/canopy/scenes, a read: the live looks, D68), re-read
// while the page is visible so a publish never leaves the page mixing two looks. demo-model's SCENES is the fallback.
import {api} from '/static/app.js';
import {SCENES,buildCanopy,paintCanopy,designLevels,sceneDesignView,nextDesignLevel,scenePainters} from './demo-model.mjs?v=show-design-4';
import {liveScenes,scenesChanged,palettePainters,LIVE_CAPTION,FALLBACK_CAPTION,REFRESH_MS} from './studio-model.mjs?v=screen-2';

const $=id=>document.getElementById(id);
const S={scene:'coastal-tides',sceneColor:1,level:null,angle:'perspective',tour:null,raf:null};
let canopyView=null,canopyKey=null,lastScenePaint=null;
// The show's design per scene id (GET /api/canopy/design): the design, or {error} when it couldn't be loaded.
const designs=new Map();
// The scenes shown: demo-model's six until the show's live looks are read ({scenes, live, generation}).
let LIVE={scenes:SCENES,live:false,generation:null};
function showError(message){$('alert').textContent=message||'';$('alert').hidden=!message;}
function selectedScene(){return LIVE.scenes.find(x=>x.id===S.scene);}
// What the scene illustration paints: the show's design at the chosen level (S.level, once someone picks one; until
// then the level the show tries first for the scene), or why it falls back to the palette left to right.
function sceneView(){return sceneDesignView(designs.get(S.scene),S.level);}
const designLoads=new Set();
let designEpoch=0;
async function loadDesign(id){
  if((designs.has(id)&&!designs.get(id).error)||designLoads.has(id))return;
  designLoads.add(id);const epoch=designEpoch;
  let design;
  try{design=await api('/api/canopy/design?mode='+encodeURIComponent(id));}
  catch(e){design={error:e.message||'the Conductor did not answer'};}
  finally{designLoads.delete(id);}
  if(epoch!==designEpoch){loadDesign(id);return;} // the looks changed while it loaded: read it again
  designs.set(id,design);
  if(id===S.scene){lastScenePaint=null;updateScenePresentation();}
}
// The scenes as the show runs them; a newer generation (a publish, a revert) drops every cached design.
let scenesLoading=false;
async function loadScenes(){
  if(scenesLoading)return;scenesLoading=true;
  let next;
  try{next=liveScenes(SCENES,await api('/api/canopy/scenes'));}
  catch{next={scenes:SCENES,live:false,generation:null};}
  finally{scenesLoading=false;}
  const changed=scenesChanged(LIVE,next)||(LIVE.live&&!next.live);
  if(!changed&&LIVE.live===next.live)return;
  LIVE=next;
  if(changed){designEpoch++;designs.clear();}
  renderSceneList();selectScene(S.scene,true,true);
}
function sceneNote(view){
  const tail=' Screen only: nothing here changes the lights.';
  if(view.state==='loading')return 'Loading the show’s design for this scene…';
  if(view.state==='error')return 'The show’s design couldn’t be loaded ('+view.error+'), so this shows the palette left to right instead.'+tail;
  if(view.state==='none')return 'This scene has no design over the canopy (one look on every strand), so this shows the palette left to right.'+tail;
  if(view.state==='mismatch')return 'The show’s design doesn’t cover the saved 270-light geometry, so this shows the palette left to right instead.'+tail;
  const d=view.design,ladder=d.ladder||[];
  if(view.level==='modules'){
    const where=d.sample_at==='mean'?'the mean of its 30 lights':'its centre';
    const step=ladder[0]==='modules'?'the way the show sets this scene first':ladder[0]==='stepped'?'the still look the show steps this scene from, and holds after each run':ladder.includes('modules')?'a later step of the show’s ladder for this scene':'not a step of this scene’s ladder';
    return 'Module level · one colour per panel, the design sampled at '+where+': '+step+'.'+tail;
  }
  if(d.design==='canvas')return 'The show’s design, head by head: what the canopy shows when this scene runs with every light painted on its own.'+tail;
  return 'The scene’s design over all 270 lights. The show sets this scene one colour per panel: choose Module level to see what the canopy shows.'+tail;
}
function stopTour(){if(S.tour)clearInterval(S.tour);S.tour=null;$('scene-tour').textContent='Play all scenes';$('scene-tour').setAttribute('aria-pressed','false');}
function renderCanopy(){
  const key=JSON.stringify([S.angle]);
  if(canopyKey!==key){canopyView=buildCanopy($('canopy-view'),{scope:'canopy',angle:S.angle});canopyKey=key;lastScenePaint=null;}
  const brightness=Number($('brightness').value),view=sceneView(),paintKey=[S.scene,brightness,view.level,view.state].join(':');
  if(lastScenePaint!==paintKey&&view.state!=='loading'){
    // the show's design head for head (or module by module); only if it can't be had, the palette left to right
    const painters=view.paint?scenePainters(view,S.scene,canopyView.layout.width):palettePainters(selectedScene(),canopyView.layout.width);
    paintCanopy(canopyView,painters.head,painters.spill,brightness);
    lastScenePaint=paintKey;
    canopyView.svg.setAttribute('aria-label',selectedScene().name+' · full canopy, nine panels, 270 lights'+(view.level==='modules'?' · module level, one colour per panel':''));
  }
}
function frame(){
  if(!document.hidden)renderCanopy();
  S.raf=requestAnimationFrame(frame);
}
function updateScenePresentation(){
  const scene=selectedScene();if(!scene)return;
  const color=scene.palette[Math.min(S.sceneColor,scene.palette.length-1)];
  $('scene-live-plan').textContent=LIVE.live?LIVE_CAPTION:FALLBACK_CAPTION;
  $('scene-select').value=S.scene;
  $('scene-title').textContent=scene.name;
  $('scene-kicker').textContent='THE FULL CANOPY / '+scene.mood.toUpperCase();
  $('scene-description').textContent=scene.description;
  const view=sceneView(),levels=view.design?designLevels(view.design):[];
  $('scene-preview-note').textContent=sceneNote(view);
  $('geometry-label').textContent=(view.level==='modules'?'9 panels × 1 colour':'9 panels × 30 lights')+' · 270 total · 18′ 9″ × 10′';
  $('design-level').hidden=view.state!=='design'||levels.length<2;$('design-level').setAttribute('aria-pressed',String(view.level==='modules'));
  $('palette-scene-name').textContent=scene.name;$('scene-color-name').textContent=color.name;
  document.querySelectorAll('.scene-tile').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.scene===S.scene)));
  document.querySelectorAll('.scene-color').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.color)===S.sceneColor)));
}
function selectScene(id,fromTour=false,keepColor=false){
  const scene=LIVE.scenes.find(x=>x.id===id);if(!scene)return;if(!fromTour)stopTour();
  S.scene=id;S.sceneColor=keepColor?Math.min(S.sceneColor,scene.palette.length-1):scene.defaultColor||0;loadDesign(id);lastScenePaint=null;
  $('brightness').value=scene.brightness;$('brightness-value').textContent=scene.brightness+'%';
  $('scene-colors').replaceChildren();
  scene.palette.forEach((color,index)=>{const b=document.createElement('button');b.className='scene-color';b.dataset.color=index;b.setAttribute('aria-label',color.name);b.setAttribute('aria-pressed',String(index===S.sceneColor));const swatch=document.createElement('i');swatch.style.background=color.hex;const label=document.createElement('span');label.textContent=color.name;b.append(swatch,label);b.addEventListener('click',()=>{stopTour();S.sceneColor=index;updateScenePresentation();});$('scene-colors').append(b);});
  updateScenePresentation();
}
function renderSceneList(){
  $('scene-select').replaceChildren();$('scene-list').replaceChildren();
  LIVE.scenes.forEach((scene,index)=>{
    const option=document.createElement('option');option.value=scene.id;option.textContent=scene.name;$('scene-select').append(option);
    const b=document.createElement('button');b.className='scene-tile '+scene.style;b.dataset.scene=scene.id;b.setAttribute('aria-pressed',String(S.scene===scene.id));b.setAttribute('aria-label',scene.name);
    const art=document.createElement('span');art.className='scene-art';art.setAttribute('aria-hidden','true');
    art.style.background='linear-gradient(110deg,'+scene.palette.map(c=>c.hex).join(',')+')';
    const number=document.createElement('small');number.className='scene-number';number.textContent=String(index+1).padStart(2,'0');art.append(number);
    const name=document.createElement('strong');name.textContent=scene.name;const mood=document.createElement('span');mood.className='scene-mood';mood.textContent=scene.mood;b.append(art,name,mood);b.addEventListener('click',()=>selectScene(scene.id));$('scene-list').append(b);
  });
}
function buildScenes(){
  renderSceneList();
  $('scene-select').addEventListener('change',()=>selectScene($('scene-select').value));
  $('scene-tour').addEventListener('click',()=>{if(S.tour){stopTour();return;}updateScenePresentation();$('scene-tour').textContent='Pause scene tour';$('scene-tour').setAttribute('aria-pressed','true');S.tour=setInterval(()=>{if(!document.hidden)selectScene(LIVE.scenes[(LIVE.scenes.findIndex(s=>s.id===S.scene)+1)%LIVE.scenes.length].id,true);},6000);});
  $('view-angle').addEventListener('click',()=>{S.angle=S.angle==='perspective'?'plan':'perspective';$('view-angle').textContent=S.angle==='plan'?'Perspective':'Ceiling plan';$('view-angle').setAttribute('aria-pressed',String(S.angle==='plan'));});
  $('design-level').addEventListener('click',()=>{S.level=nextDesignLevel(sceneView());lastScenePaint=null;updateScenePresentation();});
  selectScene(S.scene);
}

buildScenes();
loadScenes();
// re-read when the page comes back, when the Lab (#lab, where publishing happens) closes, and slowly while visible
document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadScenes();});
window.addEventListener('focus',()=>loadScenes());
window.addEventListener('hashchange',()=>{if(location.hash!=='#lab')loadScenes();});
setInterval(()=>{if(!document.hidden)loadScenes();},REFRESH_MS);
$('brightness').addEventListener('input',()=>{$('brightness-value').textContent=$('brightness').value+'%';});
$('fullscreen').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{showError('Full screen is unavailable here. Use your browser’s full-screen control.');}});
window.addEventListener('pagehide',()=>{stopTour();cancelAnimationFrame(S.raf);});
S.raf=requestAnimationFrame(frame);
