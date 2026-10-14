import {api} from '/static/app.js';
import {matchingStaticFrames} from './demo-model.mjs?v=show-design-2';
import {previewFrame,previewKey} from '/ops/module/preset-preview.js?v=preview-motion-2';
import {LOOKS,EFFECT_PARAMS,buildLook,validateKingston,motionReady,currentLookId,progressForRun,MODULES,validateTarget,customColor,whiteLook,effectLook,gradientColors,rgbFromHex,PAIR_IDS,matchingWholeSettings,sharedCapabilities,SCENES,sceneLook,sceneFrame,buildCanopy,paintCanopy,designLevels,sceneDesignView,nextDesignLevel,scenePainters} from './demo-model.mjs?v=show-design-2';


const $=id=>document.getElementById(id);
const previewOnly=new URLSearchParams(location.search).get('preview')==='1';
const S={scene:'coastal-tides',sceneColor:1,scenePreview:true,level:null,angle:'perspective',tour:null,device:'both',pair:null,lease:null,session:null,state:null,busy:false,refreshing:false,run:null,runId:null,
  previewOnly,paused:matchMedia('(prefers-reduced-motion: reduce)').matches,previewStart:performance.now(),
  previewKey:null,pausedAt:0,previewPausedTotal:0,raf:null,brightnessDirty:false,cancelPreview:false};
let canopyView=null,canopyKey=null,lastScenePaint=null,gradientBackup={pending:false};
// The show's design per scene id (GET /api/canopy/design): the design, or {error} when it couldn't be loaded.
const designs=new Map();
const moduleName=()=>S.device==='both'?'both modules':MODULES[S.device];
const modulePath=(suffix='')=>'/api/module/'+S.device+suffix;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function notify(message){$('feedback').textContent=message;}
function showError(message){$('alert').textContent=message||'';$('alert').hidden=!message;}
function setBusy(value){S.busy=value;renderControls();}
function active(){return previewOnly||(S.device==='both'?PAIR_IDS.every(id=>S.pair?.[id]?.lease):!!S.lease);}
function renderControls(){
  $('scene-apply').disabled=!active()||S.busy;
  const gradientReady=previewOnly||(S.device==='both'&&PAIR_IDS.every(id=>S.pair?.[id]?.state?.t1?.allowed));
  $('scene-gradient').disabled=!active()||S.busy||!gradientReady;
  $('restore-gradient').hidden=!gradientBackup.pending;
  $('restore-gradient').disabled=!active()||S.busy||S.device!=='both';
  $('gradient-ready').textContent=previewOnly?'Screen preview only.':S.device!=='both'?'Select both modules to apply the scene gradient.':!active()?'Connect both to check gradient readiness.':gradientReady?'30 separate colors along each strand. Custom 1 is backed up; restore it any time.':'String light 1 needs its own LED mapping check before gradients can run on both.';
  updateScenePresentation();
  if(!previewOnly&&!S.lease){$('connection').textContent=S.runId?'Demonstration in progress':'Not connected';$('connection').classList.remove('connected');}
  const disabled=!active()||S.busy;
  for(const id of ['apply-brightness','power-on','power-off','saved-look'])$(id).disabled=disabled;
  $('brightness').disabled=S.busy;
  document.querySelectorAll('.look-button').forEach(b=>{b.disabled=disabled;});
  $('saved-look').disabled=disabled||S.device==='both';
  $('pair-status').hidden=S.device!=='both';
  $('restart-pair').hidden=S.device!=='both';$('restart-pair').disabled=disabled;
  $('sync-note').textContent=S.device==='both'?'Both modules briefly go dark, then start the chosen preset together. Cloud timing can differ; watch the real lights for alignment.':'Built-in presets. Real timing is set by the controller.';
  $('motion-demo').disabled=disabled||S.device!=='bench-kit'||(!previewOnly&&!motionReady(S.state));
  $('module-select').disabled=S.busy||!!S.runId;
  for(const id of ['apply-color','apply-temperature','apply-effect'])$(id).disabled=disabled;
  $('connect').textContent='Connect to '+moduleName()+' ↗';
  $('connect').hidden=active();$('connect').disabled=S.busy;
  $('disconnect').hidden=previewOnly||!S.lease;$('disconnect').disabled=S.busy;
  $('stop-demo').hidden=!S.runId;$('stop-demo').disabled=false;
}
function build(){
  for(const look of LOOKS){
    const b=document.createElement('button');b.className='look-button';b.dataset.look=look.id;b.disabled=true;
    b.setAttribute('aria-pressed','false');b.setAttribute('aria-label',look.name);
    const swatch=document.createElement('span');swatch.className='look-swatch';swatch.style.background=look.swatch;
    const text=document.createElement('span'),name=document.createElement('strong'),note=document.createElement('small');
    name.textContent=look.name;note.textContent=look.note;text.append(name,note);b.append(swatch,text);
    b.addEventListener('click',()=>selectLook(look.id));$('looks').append(b);
  }
  for(let i=0;i<15;i++)$('motion-preview').append(document.createElement('i'));
}
function renderState(state){
  S.state=state;
  const nextKey=previewKey(state.preview?.reported);
  if(S.previewKey!==nextKey){S.previewKey=nextKey;S.previewStart=performance.now();S.previewPausedTotal=0;S.pausedAt=performance.now();}
  const selected=currentLookId(state);
  const c=state.reported_controls||{};
  const color=c.rgb?.['color-rgb']||c.rgb;
  const rgbLabel=color?'#'+['r','g','b'].map(k=>Number(color[k]||0).toString(16).padStart(2,'0')).join('').toUpperCase():'';
  const label=selected==='off'?'Lights off':c.color_mode==='white'?'White · '+c.cct+' K':c.color_mode==='color'?'Custom color · '+rgbLabel:LOOKS.find(l=>l.id===selected)?.name||(c.color_mode==='sequence'?pretty(c.preset):selected==='custom-3'?'Your saved Custom 3':'Current light setting');
  syncDesigner(state);
  $('current-look').textContent=c.color_mode==='individual'&&c.slot==='custom-1'&&gradientBackup.pending?(gradientBackup.label+' gradient'):label;
  $('look-caption').textContent=previewOnly?'SCREEN PREVIEW':'REPORTED LOOK';
  document.querySelectorAll('.look-button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.look===selected)));
  const brightness=Number(state.reported_controls?.brightness);
  if(Number.isFinite(brightness)&&brightness>=1&&brightness<=100&&!S.brightnessDirty){$('brightness').value=brightness;$('brightness-value').textContent=brightness+'%';}
  for(const [id,value] of [['power-on','on'],['power-off','off']])$(id).setAttribute('aria-pressed',String(state.reported_controls?.power===value));
  $('connection').textContent=previewOnly?'Screen preview':'Cloud connected';$('connection').classList.toggle('connected',!previewOnly);
  $('last-read').textContent=previewOnly?'Illustration only · no light commands':'Cloud readback · '+new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  if(state.read_error){showError('The latest read failed. The preview shows the last known settings.');$('connection').textContent='Readback unavailable';$('connection').classList.remove('connected');}
  if(S.device==='both'&&previewOnly){$('pair-kingston').textContent='Kingston test · screen preview';$('pair-string').textContent='String light 1 · screen preview';$('pair-summary').textContent='Shared illustration only · physical timing unverified';}
  renderControls();
}
function frame(now){
  if(!document.hidden){
    const elapsed=((S.paused?S.pausedAt:now)-S.previewStart-S.previewPausedTotal)/1000;
    let colors=S.state?.heads?.map(h=>h.reported)||Array.from({length:30},()=>({r:0,g:0,b:0}));
    const modeled=previewFrame(S.state?.preview?.reported,30,elapsed);
    if(modeled)colors=modeled;
    if(S.scenePreview)colors=sceneFrame(S.scene,30,Number($('brightness').value));
    if(S.run?.phase==='observing'){
      const t=S.paused?0:Math.floor((Date.now()-S.run.observingAt)/450);
      colors=Array.from({length:30},(_,i)=>{const p=(i-t%30+30)%30;return p%10<3?[{r:255,g:0,b:0},{r:0,g:255,b:0},{r:0,g:0,b:255}][Math.floor(p/10)]:{r:0,g:0,b:0};});
    }
    renderCanopy(colors,elapsed);
    if(S.run?.phase==='observing'){
      const remaining=Math.max(0,Math.ceil((S.run.observingAt+20000-Date.now())/1000));
      $('motion-status').textContent=(previewOnly?'Screen illustration · ':'Watch Kingston · ')+remaining+' seconds remaining';
      $('progress').style.width=(100-remaining*5)+'%';
    }
  }
  S.raf=requestAnimationFrame(frame);
}
async function connect(){
  if(S.device==='both')return connectPair();
  if(S.busy||S.lease)return;
  setBusy(true);showError('');notify('Reading '+moduleName()+'…');
  let opened;
  try{
    const devices=(await api('/api/module')).devices;
    const device=devices.find(d=>d.id===S.device);
    if(!device)throw Error('The selected module is unavailable. Check Workshop.');
    opened=await api(modulePath('/open'),{method:'POST',body:{}});
    validateTarget(device,opened.state,S.device);
    S.lease=opened.lease;S.session=opened.state.session_id;renderState(opened.state);
    notify('Ready. Choose a look to change the real lights.');
  }catch(e){
    if(opened?.lease)await api(modulePath('/close'),{method:'POST',body:{lease:opened.lease}}).catch(()=>{});
    S.lease=null;S.session=null;showError(e.message);notify('Not connected. Your lights were not changed.');
  }finally{setBusy(false);}
}
async function release(){
  if(S.pair)return releasePair();
  if(!S.lease)return;
  const lease=S.lease;
  const result=await api(modulePath('/close'),{method:'POST',body:{lease}});
  if(!result.ok)throw Error('Could not release Kingston.');
  S.lease=null;S.session=null;renderControls();
}
async function send(path,body,message){
  if(S.device==='both'){if(path!=='whole'){showError('Saved slots are separate on each module. Select one module.');return;}return sendPair(body,message);}
  if(!active()||S.busy)return;
  setBusy(true);showError('');notify('Sending to '+moduleName()+'…');
  const lease=S.lease;
  try{
    const reply=await api(modulePath('/'+path),{method:'POST',body:{lease,...body}});
    if(S.lease!==lease)return;
    S.brightnessDirty=false;renderState(reply.state);
    if(reply.result?.status!=='applied')throw Error(reply.result?.error||'The requested setting was not confirmed. Check the real lights before retrying.');
    notify(message+' · cloud readback matched. Check the real lights.');
  }catch(e){showError(e.message);notify('Change not confirmed.');if(e.status===409){S.lease=null;S.session=null;}}
  finally{setBusy(false);}
}
async function selectLook(id){
  stopTour();S.scenePreview=false;
  if(S.busy||!active())return;
  const brightness=Number($('brightness').value);
  if(previewOnly){mockLook(id,brightness);notify('Screen preview only. The real lights have not changed.');return;}
  await send('whole',{look:buildLook(id,brightness)},LOOKS.find(l=>l.id===id).name);
}
function mockLook(id,brightness=40){
  if(S.device==='both')S.previewKey=null;
  S.beforeOff=null;
  const look=LOOKS.find(l=>l.id===id),k=brightness/100;
  const rgb=id==='warm'?[255,170,100]:id==='cool'?[226,236,255]:id==='red'?[255,0,0]:[255,140,80];
  const controls={power:'on',brightness,color_mode:look?.kind==='preset'?'sequence':look?.kind==='rgb'?'color':'white',preset:id,cct:look?.cct,rgb:look?.rgb?{r:look.rgb[0],g:look.rgb[1],b:look.rgb[2]}:undefined};
  const modeled=look?.style?{name:id,style:look.style,speed:0,brightness,available:true,animated:look.style!=='gradient'}:null;
  S.brightnessDirty=false;
  renderState({simulated:true,reported_controls:controls,preview:{reported:modeled},heads:Array.from({length:30},(_,index)=>({index,reported:{r:rgb[0]*k,g:rgb[1]*k,b:rgb[2]*k}}))});
}
async function runDemo(){
  stopTour();S.scenePreview=false;
  if(S.busy||!active())return;
  setBusy(true);showError('');$('motion-status').textContent='Preparing the demonstration…';
  if(previewOnly){
    const saved=structuredClone(S.state);S.runId='screen-preview';S.cancelPreview=false;S.run={phase:'observing',observingAt:Date.now()};
    $('current-look').textContent='Moving color blocks';$('progress-track').hidden=false;renderControls();
    while(!S.cancelPreview&&Date.now()-S.run.observingAt<20000)await sleep(200);
    S.run=null;S.runId=null;$('progress-track').hidden=true;renderState(saved);setBusy(false);
    $('motion-status').textContent='Screen demonstration finished. No real lights were changed.';return;
  }
  let completed=false;
  try{
    if(S.device!=='bench-kit'||!motionReady(S.state))throw Error('Kingston’s commissioning must be verified before this demonstration.');
    await release();
    const started=await api('/api/bench/run',{method:'POST',body:{job:'effect_probe',device_id:'bench-kit',params:{...EFFECT_PARAMS}}});
    S.runId=started.run_id;sessionStorage.setItem('canopy-showcase-run',S.runId);renderControls();
    await followRun(S.runId);
    completed=true;
  }catch(e){showError(e.message);$('motion-status').textContent=S.runId?'Connection to the demonstration was lost. The server attempts restoration; use Reconnect to check.':'Demonstration could not start.';}
  finally{
    setBusy(false);
    if(completed&&!S.runId&&!S.lease)await connect();
  }
}
async function followRun(id){
  for(;;){
    const run=await api('/api/bench/runs/'+encodeURIComponent(id));
    if(run.status!=='running'){
      S.run=null;S.runId=null;sessionStorage.removeItem('canopy-showcase-run');$('progress-track').hidden=true;
      const restored=run.result?.observations?.restore?.status==='applied';
      if(!restored)throw Error('The saved pattern’s restoration was not confirmed. Open Workshop to inspect Kingston.');
      const candidate=run.result?.observations?.write?.status==='applied';
      $('motion-status').textContent=candidate?'Demonstration complete · saved Custom 3 restored.':'Demonstration stopped · saved Custom 3 restored.';
      notify('Saved pattern and effect settings restored.');renderControls();return;
    }
    const progress=progressForRun(run,Date.now());
    const step=run.steps.find(x=>x.message.startsWith('Observe candidate'));
    S.run={phase:progress.phase,observingAt:step?.t*1000};
    $('current-look').textContent=progress.phase==='observing'?'Moving color blocks':progress.phase==='restoring'?'Restoring your pattern':'Preparing the lights';
    $('motion-status').textContent=progress.phase==='preparing'?'Preparing · brief white reset and static blocks':progress.phase==='restoring'?'Restoring today’s saved Custom 3…':$('motion-status').textContent;
    $('progress-track').hidden=progress.phase!=='observing';
    await sleep(650);
  }
}
async function reconnectRun(){
  const id=sessionStorage.getItem('canopy-showcase-run');if(!id)return false;
  S.device='bench-kit';$('module-select').value='bench-kit';S.runId=id;setBusy(true);notify('Checking the previous demonstration…');
  try{await followRun(id);return true;}catch(e){showError(e.message);return false;}finally{setBusy(false);}
}
async function refresh(){
  if(S.device==='both')return refreshPair();
  if(document.hidden||S.busy||S.refreshing||!S.lease||previewOnly)return;
  S.refreshing=true;const lease=S.lease,session=S.session;
  try{
    const reply=await api(modulePath('?refresh=1'));
    if(S.lease!==lease||S.session!==session)return;
    if(!reply.state.open||reply.state.session_id!==session){S.lease=null;S.session=null;showError('The module was released or opened elsewhere. Reconnect when ready.');renderControls();return;}
    renderState(reply.state);
  }catch(e){$('connection').textContent='Readback unavailable';$('connection').classList.remove('connected');$('last-read').textContent='Showing last known settings';}
  finally{S.refreshing=false;}
}
function selectedScene(){return SCENES.find(x=>x.id===S.scene);}
// What the scene illustration paints: the show's design at the chosen level (S.level, once someone picks one; until
// then the level the show tries first for the scene), or why it falls back to the palette left to right.
function sceneView(){return sceneDesignView(designs.get(S.scene),S.level);}
const designLoads=new Set();
async function loadDesign(id){
  if((designs.has(id)&&!designs.get(id).error)||designLoads.has(id))return;
  designLoads.add(id);
  try{designs.set(id,await api('/api/canopy/design?mode='+encodeURIComponent(id)));}
  catch(e){designs.set(id,{error:e.message||'the Conductor did not answer'});}
  finally{designLoads.delete(id);}
  if(id===S.scene){lastScenePaint=null;updateScenePresentation();}
}
function sceneNote(view){
  const tail=' Live palette controls below affect only the selected shop modules.';
  if(view.state==='loading')return 'Loading the show’s design for this scene…';
  if(view.state==='error')return 'The show’s design couldn’t be loaded ('+view.error+'), so this shows the palette left to right instead.'+tail;
  if(view.state==='none')return 'This scene has no design over the canopy (one look on every strand), so this shows the palette left to right.'+tail;
  if(view.state==='mismatch')return 'The show’s design doesn’t cover the saved 270-light geometry, so this shows the palette left to right instead.'+tail;
  const d=view.design,ladder=d.ladder||[];
  if(view.level==='modules'){
    const where=d.sample_at==='mean'?'the mean of its 30 lights':'its centre';
    const step=ladder[0]==='modules'?'the way the show sets this scene first':ladder.includes('modules')?'a later step of the show’s ladder for this scene':'not a step of this scene’s ladder';
    return 'Module level · one colour per panel, the design sampled at '+where+': '+step+'.'+tail;
  }
  if(d.design==='canvas')return 'The show’s design, head by head: what the canopy shows when this scene runs with every light painted on its own.'+tail;
  return 'The scene’s design over all 270 lights. The show sets this scene one colour per panel: choose Module level to see what the canopy shows.'+tail;
}
function stopTour(){if(S.tour)clearInterval(S.tour);S.tour=null;$('scene-tour').textContent='Play all scenes';$('scene-tour').setAttribute('aria-pressed','false');}
function renderCanopy(colors,elapsed){
  const scope=S.scenePreview?'canopy':'shop',maps={};
  for(const id of PAIR_IDS){const state=S.device==='both'?S.pair?.[id]?.state:S.device===id?S.state:null;if(state?.map)maps[id]=state.map;}
  const key=JSON.stringify([scope,S.device,S.angle,maps]);
  if(canopyKey!==key){canopyView=buildCanopy($('canopy-view'),{scope,device:S.device,maps,angle:S.angle});canopyKey=key;lastScenePaint=null;}
  if(S.scenePreview){
    const brightness=Number($('brightness').value),view=sceneView(),paintKey=[S.scene,brightness,view.level,view.state].join(':');
    if(lastScenePaint!==paintKey&&view.state!=='loading'){
      // the show's design head for head (or module by module); only if it can't be had, the palette left to right
      const painters=scenePainters(view,S.scene,canopyView.layout.width);
      paintCanopy(canopyView,painters.head,painters.spill,brightness);
      lastScenePaint=paintKey;
      canopyView.svg.setAttribute('aria-label',selectedScene().name+' · full canopy, nine panels, 270 lights'+(view.level==='modules'?' · module level, one colour per panel':''));
    }
  }else{
    lastScenePaint=null;
    const perDevice={};
    for(const id of PAIR_IDS){const state=S.pair?.[id]?.state;if(state)perDevice[id]=previewFrame(state.preview?.reported,30,elapsed)||state.heads?.map(h=>h.reported);}
    paintCanopy(canopyView,h=>(perDevice[h.device]||colors)[h.index]||{r:0,g:0,b:0},()=>colors[15]||{r:0,g:0,b:0});
  }
}
function updateScenePresentation(){
  const scene=selectedScene();if(!scene)return;
  const color=scene.palette[S.sceneColor];
  $('scene-select').value=S.scene;
  $('scene-title').textContent=S.scenePreview?scene.name:$('current-look').textContent;
  $('scene-kicker').textContent=S.scenePreview?'THE FULL CANOPY / '+scene.mood.toUpperCase():previewOnly?'SCREEN-ONLY SHOP SETTINGS':'SHOP LIGHT SETTINGS';
  $('scene-description').textContent=S.scenePreview?scene.description:'The selected shop modules, drawn from their latest reported settings.';
  const view=sceneView(),levels=view.design?designLevels(view.design):[];
  $('scene-preview-note').textContent=S.scenePreview?sceneNote(view):'Saved panel geometry · color and motion are illustrations of readback. Watch the real lights for brightness and timing.';
  $('geometry-label').textContent=S.scenePreview?(view.level==='modules'?'9 panels × 1 colour':'9 panels × 30 lights')+' · 270 total · 18′ 9″ × 10′':(S.device==='both'?'2 modules × 30 lights · 60 total':'1 module × 30 lights')+' · shop view';
  $('design-level').hidden=!S.scenePreview||view.state!=='design'||levels.length<2;$('design-level').setAttribute('aria-pressed',String(view.level==='modules'));
  $('look-caption').textContent=S.scenePreview?'SCENE DESIGN':previewOnly?'SCREEN PREVIEW':'REPORTED SETTINGS';
  $('preview-pause').hidden=S.scenePreview;
  $('show-scene-preview').setAttribute('aria-pressed',String(S.scenePreview));$('show-light-readback').setAttribute('aria-pressed',String(!S.scenePreview));
  $('palette-scene-name').textContent=scene.name;$('scene-color-name').textContent=color.name;
  $('scene-live-plan').textContent='Apply the full palette as a gradient across each 30-light strand, or use '+color.name.toLowerCase()+' as a solid color.';
  document.querySelectorAll('.scene-tile').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.scene===S.scene)));
  document.querySelectorAll('.scene-color').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.color)===S.sceneColor)));
}
function selectScene(id,fromTour=false){
  const scene=SCENES.find(x=>x.id===id);if(!scene)return;if(!fromTour)stopTour();
  S.scene=id;S.sceneColor=scene.defaultColor||0;S.scenePreview=true;S.brightnessDirty=true;loadDesign(id);
  $('brightness').value=scene.brightness;$('brightness-value').textContent=scene.brightness+'% · draft';
  $('scene-colors').replaceChildren();
  scene.palette.forEach((color,index)=>{const b=document.createElement('button');b.className='scene-color';b.dataset.color=index;b.setAttribute('aria-label',color.name);b.setAttribute('aria-pressed',String(index===S.sceneColor));const swatch=document.createElement('i');swatch.style.background=color.hex;const label=document.createElement('span');label.textContent=color.name;b.append(swatch,label);b.addEventListener('click',()=>{stopTour();S.sceneColor=index;updateScenePresentation();});$('scene-colors').append(b);});
  updateScenePresentation();notify(scene.name+' selected in the canopy illustration. Live lights are unchanged.');
}
function buildScenes(){
  SCENES.forEach((scene,index)=>{
    const option=document.createElement('option');option.value=scene.id;option.textContent=scene.name;$('scene-select').append(option);
    const b=document.createElement('button');b.className='scene-tile '+scene.style;b.dataset.scene=scene.id;b.setAttribute('aria-pressed',String(S.scene===scene.id));b.setAttribute('aria-label',scene.name);
    const art=document.createElement('span');art.className='scene-art';art.setAttribute('aria-hidden','true');
    art.style.background='linear-gradient(110deg,'+scene.palette.map(c=>c.hex).join(',')+')';
    const number=document.createElement('small');number.className='scene-number';number.textContent=String(index+1).padStart(2,'0');art.append(number);
    const name=document.createElement('strong');name.textContent=scene.name;const mood=document.createElement('span');mood.className='scene-mood';mood.textContent=scene.mood;b.append(art,name,mood);b.addEventListener('click',()=>selectScene(scene.id));$('scene-list').append(b);
  });
  $('scene-select').addEventListener('change',()=>selectScene($('scene-select').value));
  $('scene-tour').addEventListener('click',()=>{if(S.tour){stopTour();return;}S.scenePreview=true;updateScenePresentation();$('scene-tour').textContent='Pause scene tour';$('scene-tour').setAttribute('aria-pressed','true');S.tour=setInterval(()=>{if(!document.hidden&&!S.busy)selectScene(SCENES[(SCENES.findIndex(s=>s.id===S.scene)+1)%SCENES.length].id,true);},6000);notify('Screen-only scene tour · lights are unchanged.');});
  $('view-angle').addEventListener('click',()=>{S.angle=S.angle==='perspective'?'plan':'perspective';$('view-angle').textContent=S.angle==='plan'?'Perspective':'Ceiling plan';$('view-angle').setAttribute('aria-pressed',String(S.angle==='plan'));});
  $('show-scene-preview').addEventListener('click',()=>{S.scenePreview=true;updateScenePresentation();});
  $('design-level').addEventListener('click',()=>{S.level=nextDesignLevel(sceneView());lastScenePaint=null;updateScenePresentation();});
  $('show-light-readback').addEventListener('click',()=>{stopTour();S.scenePreview=false;updateScenePresentation();});
  $('scene-apply').addEventListener('click',async()=>{
    if(S.busy||!active())return;stopTour();
    const scene=selectedScene(),color=scene.palette[S.sceneColor];
    try{const look=sceneLook(S.scene,S.sceneColor,Number($('brightness').value));
      if(previewOnly){mockWhole(look);notify(scene.name+' · '+color.name+' applied to the screen-only shop view.');}
      else await send('whole',{look},scene.name+' · '+color.name);
    }catch(e){showError(e.message);}
  });
  $('scene-gradient').addEventListener('click',()=>sendGradient('apply'));
  $('restore-gradient').addEventListener('click',()=>sendGradient('restore'));
  selectScene(S.scene);
}

async function readGradientStatus(){
  if(previewOnly)return;
  try{gradientBackup=await api('/api/studio/pair/gradient');renderControls();}catch{}
}
async function sendGradient(action){
  if(S.busy||!active())return;stopTour();
  const scene=selectedScene(),brightness=Number($('brightness').value);
  if(previewOnly){
    if(action==='restore'){gradientBackup={pending:false};if(S.previewBeforeGradient)renderState(S.previewBeforeGradient);}
    else {if(!gradientBackup.pending)S.previewBeforeGradient=structuredClone(S.state);gradientBackup={pending:true,label:scene.name};S.scenePreview=false;S.brightnessDirty=false;renderState({simulated:true,reported_controls:{power:'on',brightness,color_mode:'individual',slot:'custom-1'},preview:{reported:null},heads:sceneFrame(S.scene,30,brightness).map((reported,index)=>({index,reported}))});}
    renderControls();notify('Screen preview only. Real lights are unchanged.');return;
  }
  if(S.device!=='both')return;
  setBusy(true);showError('');notify(action==='restore'?'Restoring both original patterns and previous looks…':'Backing up Custom 1, then applying 30 colors to each strand…');
  const pair=S.pair;
  try{
    const reply=await api('/api/studio/pair/gradient',{method:'POST',body:{action,leases:Object.fromEntries(PAIR_IDS.map(id=>[id,pair[id].lease])),colors:action==='apply'?sceneFrame(S.scene,30,100).map(c=>[c.r,c.g,c.b]):[],brightness,label:scene.name}});
    gradientBackup=reply.gradient;S.scenePreview=false;S.brightnessDirty=false;
    renderPair(Object.fromEntries(PAIR_IDS.map(id=>[id,reply.modules[id].state])),reply.modules);
    if(!reply.ok)throw Error(reply.rolled_back?'The gradient was not confirmed. Previous looks were restored.':'Restoration is incomplete. The backup is saved; inspect readback before retrying.');
    notify(action==='restore'?'Original Custom 1 and previous looks restored on both modules.':scene.name+' gradient · both 30-color frames confirmed by cloud readback. Watch the real lights.');
    if(action==='apply')$('pair-summary').textContent='Matching static gradients · 30 colors per strand';
  }catch(e){showError(e.message);await readGradientStatus();}
  finally{setBusy(false);}
}

function renderPair(states,results=null){
  for(const id of PAIR_IDS)S.pair[id].state=states[id];
  const state={...states['bench-kit'],capabilities:sharedCapabilities(states)};
  renderState(state);
  for(const [id,element] of [['bench-kit','pair-kingston'],['shop-string-1','pair-string']]){
    const result=results?.[id]?.result;
    $(element).textContent=MODULES[id]+' · '+(result?(result.status==='applied'?'cloud readback matched':result.status):states[id].read_error?'read unavailable':'cloud readback received');
  }
  const framesMatch=matchingStaticFrames(states),match=framesMatch||matchingWholeSettings(states);
  $('pair-summary').textContent=framesMatch?'Cloud frames match · check both real gradients':match?'Cloud settings match · check the real lights':'Cloud settings differ. Choose a look to match both modules.';
  if(!match){$('current-look').textContent='Different module settings';document.querySelectorAll('.look-button').forEach(b=>b.setAttribute('aria-pressed','false'));}
  updateScenePresentation();
}
async function connectPair(){
  if(S.busy||S.pair)return;setBusy(true);showError('');notify('Connecting to both shop modules…');
  const opened={};
  try{
    const devices=(await api('/api/module')).devices;
    for(const id of PAIR_IDS){
      const device=devices.find(x=>x.id===id);if(!device)throw Error(MODULES[id]+' is unavailable.');
      const reply=await api('/api/module/'+id+'/open',{method:'POST',body:{}});opened[id]=reply;
      validateTarget(device,reply.state,id);if(reply.state.read_error)throw Error(MODULES[id]+' readback is unavailable.');
    }
    S.pair=opened;S.lease=opened['bench-kit'].lease;S.session=opened['bench-kit'].state.session_id;
    renderPair(Object.fromEntries(PAIR_IDS.map(id=>[id,opened[id].state])));await readGradientStatus();notify('Both connected. Choose a scene gradient or a solid color.');
  }catch(e){await Promise.allSettled(Object.entries(opened).map(([id,r])=>api('/api/module/'+id+'/close',{method:'POST',body:{lease:r.lease}})));S.pair=null;S.lease=null;S.session=null;showError(e.message);notify('Pair not connected. No light commands sent.');}
  finally{setBusy(false);}
}
async function releasePair(){
  const pair=S.pair;if(!pair)return;
  const results=await Promise.allSettled(PAIR_IDS.map(id=>api('/api/module/'+id+'/close',{method:'POST',body:{lease:pair[id].lease}})));
  if(results.some(x=>x.status==='rejected'))throw Error('Could not release both modules. Retry Release module.');
  S.pair=null;S.lease=null;S.session=null;renderControls();
}
async function sendPair(body,message){
  if(S.busy||!active())return;
  setBusy(true);showError('');const pair=S.pair;
  const restart=body.look?.kind==='preset';notify(restart?'Restarting both: brief dark reset, then the shared preset…':'Applying the same setting to both modules…');
  try{
    const reply=await api('/api/studio/pair/whole',{method:'POST',body:{leases:Object.fromEntries(PAIR_IDS.map(id=>[id,pair[id].lease])),...body,restart}});
    if(S.pair!==pair)return;
    S.brightnessDirty=false;if(restart)S.previewKey=null;renderPair(Object.fromEntries(PAIR_IDS.map(id=>[id,reply.modules[id].state])),reply.modules);
    if(!reply.ok)throw Error(reply.phase==='reset'?'The dark reset was not confirmed on both. The shared effect was not started; inspect each module.':'The setting was not confirmed on both modules. Inspect the two status lines before retrying.');
    notify(message+' · cloud readback matched on both modules');
    if(restart)$('pair-summary').textContent='Restart settings matched in the cloud. Watch both lights for alignment.';
  }catch(e){showError(e.message);notify('Pair result needs attention.');}
  finally{setBusy(false);}
}
async function refreshPair(){
  if(document.hidden||S.busy||S.refreshing||!S.pair||previewOnly)return;
  S.refreshing=true;const pair=S.pair;
  try{
    const replies=await Promise.all(PAIR_IDS.map(id=>api('/api/module/'+id+'?refresh=1')));
    if(S.pair!==pair)return;
    if(replies.some((r,i)=>!r.state.open||r.state.session_id!==pair[PAIR_IDS[i]].state.session_id)){
      await releasePair();showError('A paired session ended. Connect both again.');return;
    }
    renderPair(Object.fromEntries(PAIR_IDS.map((id,i)=>[id,replies[i].state])));
  }catch(e){$('connection').textContent='Pair readback unavailable';$('connection').classList.remove('connected');$('pair-summary').textContent='Could not refresh both modules; showing last known settings.';}
  finally{S.refreshing=false;}
}
async function heartbeatPair(){
  const pair=S.pair;
  const replies=await Promise.allSettled(PAIR_IDS.map(id=>api('/api/module/'+id+'/heartbeat',{method:'POST',body:{lease:pair[id].lease,active:!document.hidden}})));
  if(S.pair===pair&&replies.some(x=>x.status==='rejected')){showError('A paired session may have ended. Release and reconnect both modules.');$('connection').textContent='Pair connection needs attention';}
}

const previewPresets=['gradient-rainbow','rainbow-road','fade-3','jump-3'];
const pretty=value=>String(value||'Current effect').split('-').map(w=>w.charAt(0).toUpperCase()+w.slice(1)).join(' ');
let libraryKey='';
function syncDesigner(state){
  const caps=state.capabilities||{},list=previewOnly?previewPresets:(caps.presets||[]);
  const key=S.device+JSON.stringify(list);
  if(key!==libraryKey){
    libraryKey=key;$('effect-select').replaceChildren();
    for(const name of list){const o=document.createElement('option');o.value=name;o.textContent=pretty(name);$('effect-select').append(o);}
    const c=state.reported_controls||{};if(list.includes(c.preset))$('effect-select').value=c.preset;
    const range=caps.cct_range||[2200,6500];$('temperature').min=range[0];$('temperature').max=range[1];
    const speed=caps.speed_range||[-10,10];$('effect-speed').min=speed[0];$('effect-speed').max=speed[1];
  }
}
function mockWhole(look){
  if(S.device==='both'&&look.kind==='preset')S.previewKey=null;
  S.beforeOff=null;S.brightnessDirty=false;
  const c={power:'on',brightness:look.brightness,color_mode:look.kind==='rgb'?'color':look.kind==='white'?'white':'sequence',cct:look.cct,preset:look.preset,speed:look.speed,rgb:look.rgb?{r:look.rgb[0],g:look.rgb[1],b:look.rgb[2]}:undefined};
  const k=look.brightness/100,t=((look.cct||2700)-2200)/4300;
  const color=look.kind==='rgb'?look.rgb:look.kind==='white'?[255-29*t,170+66*t,100+155*t]:[100,100,100];
  const style={'gradient-rainbow':'gradient','rainbow-road':'travel','fade-3':'fade-black','jump-3':'jump'}[look.preset];
  renderState({simulated:true,reported_controls:c,preview:{reported:style?{name:look.preset,style,speed:look.speed||0,brightness:look.brightness,available:true,animated:style!=='gradient'}:null},heads:Array.from({length:30},(_,index)=>({index,reported:{r:color[0]*k,g:color[1]*k,b:color[2]*k}}))});
}
async function applyDesigned(make,label){
  stopTour();S.scenePreview=false;
  if(S.busy||!active())return;
  try{const look=make(Number($('brightness').value));showError('');if(previewOnly){mockWhole(look);notify(label+' · screen preview only');}else await send('whole',{look},label);}catch(e){showError(e.message);}
}
function favorites(){try{const x=JSON.parse(localStorage.getItem('canopy-studio-swatches')||'[]');return Array.isArray(x)?x.filter(v=>/^#[0-9a-f]{6}$/i.test(v)).slice(0,8):[];}catch{return [];}}
function showFavorites(){
  $('favorites').replaceChildren();
  for(const hex of favorites()){const b=document.createElement('button');b.className='favorite';b.style.background=hex;b.title=hex;b.setAttribute('aria-label','Load saved color '+hex);b.addEventListener('click',()=>{$('custom-color').value=hex;$('color-hex').value=hex.toUpperCase();$('color-note').textContent='Swatch loaded. Apply color when ready.';});$('favorites').append(b);}
}
function saveColors(colors){try{colors.forEach(rgbFromHex);localStorage.setItem('canopy-studio-swatches',JSON.stringify([...new Set([...colors.map(x=>x.toLowerCase()),...favorites()])].slice(0,8)));showFavorites();$('color-note').textContent='Saved in this browser.';}catch(e){$('color-note').textContent=e.message||'Browser storage is unavailable.';}}
function renderGradient(){
  const a=$('gradient-from').value,b=$('gradient-to').value;$('gradient-strip').replaceChildren();
  for(const rgb of gradientColors(a,b)){const dot=document.createElement('i');dot.style.background='rgb('+rgb.join(',')+')';$('gradient-strip').append(dot);}
  $('gradient-label').textContent=a.toUpperCase()+' → '+b.toUpperCase()+' · illustration only';
}
function setupDesigner(){
  $('custom-color').addEventListener('input',()=>{$('color-hex').value=$('custom-color').value.toUpperCase();});
  $('color-hex').addEventListener('input',()=>{if(/^#[0-9a-f]{6}$/i.test($('color-hex').value))$('custom-color').value=$('color-hex').value;});
  $('apply-color').addEventListener('click',()=>applyDesigned(b=>customColor($('color-hex').value.trim(),b),'Custom color'));
  $('save-color').addEventListener('click',()=>saveColors([$('color-hex').value.trim()]));
  $('temperature').addEventListener('input',()=>{$('temperature-value').textContent=$('temperature').value+' K';});
  $('apply-temperature').addEventListener('click',()=>applyDesigned(b=>whiteLook(Number($('temperature').value),b,S.state?.capabilities?.cct_range||[2200,6500]),'White temperature'));
  $('effect-speed').addEventListener('input',()=>{$('effect-speed-value').textContent=$('effect-speed').value;});
  $('restart-pair').addEventListener('click',()=>{if(S.device==='both')$('apply-effect').click();});
  $('apply-effect').addEventListener('click',()=>applyDesigned(b=>effectLook($('effect-select').value,Number($('effect-speed').value),b,previewOnly?previewPresets:(S.state?.capabilities?.presets||[]),S.state?.capabilities?.speed_range||[-10,10]),pretty($('effect-select').value)));
  for(const id of ['gradient-from','gradient-to'])$(id).addEventListener('input',renderGradient);
  $('reverse-gradient').addEventListener('click',()=>{const a=$('gradient-from').value;$('gradient-from').value=$('gradient-to').value;$('gradient-to').value=a;renderGradient();});
  $('save-gradient-colors').addEventListener('click',()=>saveColors([$('gradient-from').value,$('gradient-to').value]));
  $('module-select').addEventListener('change',async()=>{
    const next=$('module-select').value,previous=S.device;if(S.busy)return;setBusy(true);
    try{if(S.lease)await release();S.device=next;S.state=null;showError('');libraryKey='';
      $('module-note').textContent=next==='both'?'One look across both shop modules':next==='bench-kit'?'Color and motion demonstrated':'Whole-module controls · individual LED checks pending';
      $('stage-badge').textContent=previewOnly?'SCREEN ILLUSTRATION':next==='both'?'2 × 30 HEADS':'30 LIGHT HEADS';
      $('current-look').textContent='Ready for your next look';$('last-read').textContent='Waiting for connection';
      if(previewOnly)mockLook('warm',60);else notify('Connect to '+moduleName()+' to read its settings.');
    }catch(e){S.device=previous;$('module-select').value=previous;showError(e.message);}finally{setBusy(false);}
  });
  showFavorites();renderGradient();
}

build();setupDesigner();buildScenes();
$('connect').addEventListener('click',async()=>{if(sessionStorage.getItem('canopy-showcase-run')&&!await reconnectRun())return;if(!S.runId)await connect();});
$('disconnect').addEventListener('click',async()=>{if(S.busy)return;setBusy(true);try{await release();$('connection').textContent='Module released';$('connection').classList.remove('connected');notify('The lights keep their current look.');}catch(e){showError(e.message);}finally{setBusy(false);}});
$('brightness').addEventListener('input',()=>{updateScenePresentation();S.brightnessDirty=true;$('brightness-value').textContent=$('brightness').value+'% · draft';});
$('apply-brightness').addEventListener('click',()=>{
  if(S.busy||!active())return;stopTour();
  const value=Number($('brightness').value);
  if(previewOnly){const old=S.state.reported_controls.brightness||100;S.state.heads.forEach(h=>{for(const key of ['r','g','b'])h.reported[key]=h.reported[key]*value/old;});S.state.reported_controls.brightness=value;if(S.state.preview.reported)S.state.preview.reported.brightness=value;S.brightnessDirty=false;renderState(S.state);notify('Screen brightness updated.');}
  else send('whole',{brightness:value},'Brightness '+value+'%');
});
for(const [id,on] of [['power-on',true],['power-off',false]])$(id).addEventListener('click',()=>{
  if(S.busy||!active())return;stopTour();S.scenePreview=false;
  if(previewOnly){if(!on&&S.state.reported_controls.power!=='off'){S.beforeOff=structuredClone(S.state);S.state.heads.forEach(h=>h.reported={r:0,g:0,b:0});S.state.preview.reported=null;S.state.reported_controls.power='off';renderState(S.state);}else if(on&&S.beforeOff){renderState(S.beforeOff);S.beforeOff=null;}notify('Screen preview only.');}
  else send('whole',{power:on},'Lights '+(on?'on':'off'));
});
$('saved-look').addEventListener('click',()=>{
  stopTour();S.scenePreview=false;
  if(S.busy||!active()||S.device==='both')return;
  if(previewOnly){mockLook('warm',40);S.state.preview.reported=null;S.state.reported_controls.color_mode='individual';S.state.reported_controls.slot='custom-3';S.state.heads=Array.from({length:30},(_,index)=>({index,reported:{r:255*(1-index/29),g:0,b:255*(index/29)}}));renderState(S.state);notify('Illustrative saved gradient · screen only.');}
  else send('slot',{slot:'custom-3',action:'select'},'Saved Custom 3 selected');
});
$('motion-demo').addEventListener('click',()=>{if(S.busy||!active())return;$('motion-dialog').returnValue='';$('motion-dialog').showModal();});
$('motion-dialog').addEventListener('close',()=>{if($('motion-dialog').returnValue==='start')runDemo();});
$('stop-demo').addEventListener('click',async()=>{if(!S.runId)return;$('stop-demo').disabled=true;if(previewOnly){S.cancelPreview=true;return;}try{await api('/api/bench/runs/'+S.runId+'/stop',{method:'POST',body:{}});$('motion-status').textContent='Stopping and restoring the saved pattern…';}catch(e){showError(e.message);$('stop-demo').disabled=false;}});
$('fullscreen').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{showError('Full screen is unavailable here. Use your browser’s full-screen control.');}});
$('preview-pause').addEventListener('click',()=>{S.paused=!S.paused;if(S.paused)S.pausedAt=performance.now();else S.previewPausedTotal+=performance.now()-S.pausedAt;$('preview-pause').textContent=S.paused?'Play illustration':'Pause illustration';$('preview-pause').setAttribute('aria-pressed',String(S.paused));});
window.addEventListener('pagehide',()=>{stopTour();if(S.pair&&!previewOnly){for(const id of PAIR_IDS)navigator.sendBeacon('/api/module/'+id+'/close',JSON.stringify({lease:S.pair[id].lease}));}else if(S.lease&&!previewOnly)navigator.sendBeacon(modulePath('/close'),JSON.stringify({lease:S.lease}));cancelAnimationFrame(S.raf);});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
setInterval(refresh,5000);
setInterval(async()=>{if(S.pair&&!S.busy&&!previewOnly){await heartbeatPair();return;}if(S.lease&&!S.busy&&!previewOnly){const lease=S.lease;try{await api(modulePath('/heartbeat'),{method:'POST',body:{lease,active:!document.hidden}});}catch(e){if(S.lease===lease){S.lease=null;S.session=null;showError('The session ended. Reconnect to continue.');renderControls();}}}},25000);
if(previewOnly){document.querySelector('.brand').href='?preview=1';$('preview-banner').hidden=false;$('connect').hidden=true;$('stage-badge').textContent='SCREEN ILLUSTRATION';$('demo-explanation').textContent='This screen-only animation illustrates moving color blocks for 20 seconds. No commands are sent to the physical lights.';$('workshop-link').href='http://localhost:8000/ops/module/';mockLook('warm',60);notify('Choose a scene above to change the full-canopy illustration. Real lights are unchanged.');}
else {renderControls();reconnectRun();}
if(S.paused){S.pausedAt=performance.now();$('preview-pause').textContent='Play illustration';$('preview-pause').setAttribute('aria-pressed','true');}
S.raf=requestAnimationFrame(frame);
