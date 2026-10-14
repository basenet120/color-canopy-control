export const LOOKS = [
  {id:'warm',name:'Warm white',note:'Soft & welcoming',swatch:'#f2d2a2',kind:'white',cct:2200},
  {id:'cool',name:'Cool white',note:'Clear & bright',swatch:'#dce8f1',kind:'white',cct:6500},
  {id:'red',name:'Signature red',note:'Bold & saturated',swatch:'#c95c42',kind:'rgb',rgb:[255,0,0]},
  {id:'gradient-rainbow',name:'Color pairs',note:'A still spectrum',swatch:'conic-gradient(#d28457,#cfc381,#8aaa71,#719da4,#8b80a4,#d28457)',kind:'preset',preset:'gradient-rainbow',speed:0,style:'gradient'},
  {id:'rainbow-road',name:'Rainbow road',note:'Traveling color',swatch:'linear-gradient(135deg,#ce7770,#afaa6c,#6c9a95,#887da6)',kind:'preset',preset:'rainbow-road',speed:0,style:'travel'},
  {id:'fade-3',name:'Three-color fade',note:'A slower rhythm',swatch:'linear-gradient(135deg,#b5716b,#789574,#798aaa)',kind:'preset',preset:'fade-3',speed:0,style:'fade-black'},
];
// Palettes verified in the user's signed-in shared scene study on 2026-10-06.
// Scene design is illustrative. Live gradients use a backed-up Custom 1 on
// independently commissioned modules; solid palette colors use whole-strand RGB.
export const SCENES=[
  {id:'lowcountry-calm',name:'Lowcountry Calm',mood:'Settle in',description:'Soft ivory with a pale sea-glass center for a calm, airy setting.',style:'calm',brightness:70,
    palette:[{name:'Soft ivory',hex:'#F7F2E7'},{name:'Sea glass',hex:'#D1EAE3'}]},
  {id:'rainbow-row-sunset',name:'Rainbow Row Sunset',mood:'Stay a little longer',description:'Pastel coral, blush pink and lilac, inspired by Charleston’s colorful facades.',style:'sunset',brightness:85,
    palette:[{name:'Pastel coral',hex:'#FF9E96'},{name:'Blush pink',hex:'#FFD2E0'},{name:'Lilac',hex:'#C4B3EC'}]},
  {id:'garden-gathering',name:'Garden Gathering',mood:'Bring everyone together',description:'Sage green softens into pale lime and ivory for a fresh garden glow.',style:'garden',brightness:75,
    palette:[{name:'Sage green',hex:'#8FBF9D'},{name:'Pale lime',hex:'#E6EED1'},{name:'Ivory',hex:'#F7F2E7'}]},
  {id:'coastal-tides',name:'Coastal Tides',mood:'Find your coast',description:'Ocean blue, aqua, seafoam and foam white, inspired by the Lowcountry coastline.',style:'coastal',brightness:85,
    palette:[{name:'Ocean blue',hex:'#659BD5'},{name:'Aqua',hex:'#79CED4'},{name:'Seafoam',hex:'#B2E8D2'},{name:'Foam white',hex:'#E9F3E7'}],defaultColor:1},
  {id:'golden-hour',name:'Golden Hour',mood:'Let the evening glow',description:'Soft gold, peach, sunset pink, rose and lavender. Pinks lead the sunset.',style:'golden',brightness:95,
    palette:[{name:'Soft gold',hex:'#FFDB9B'},{name:'Peach',hex:'#FFB69D'},{name:'Sunset pink',hex:'#FF9BC4'},{name:'Rose',hex:'#E779B0'},{name:'Lavender',hex:'#B59AE2'}],defaultColor:2},
  {id:'goodnight-glow',name:'The Goodnight Glow',mood:'Slow the night down',description:'Dim periwinkle, lavender and moonlight blue for a restful evening mood.',style:'night',brightness:28,
    palette:[{name:'Periwinkle',hex:'#8B8BD1'},{name:'Lavender',hex:'#BEA9E6'},{name:'Moonlight blue',hex:'#C4D6F3'}]},
];
export function sceneLook(id,colorIndex,brightness){
  const scene=SCENES.find(x=>x.id===id),color=scene?.palette[colorIndex];
  if(!color)throw Error('Choose a scene and one of its palette colors.');
  return customColor(color.hex,brightness);
}
export function sceneColorAt(id,position,brightness=100){
  const scene=SCENES.find(x=>x.id===id);if(!scene)return {r:0,g:0,b:0};
  const colors=scene.palette.map(c=>rgbFromHex(c.hex)),level=Math.max(0,Math.min(100,brightness))/100;
  let p=Math.max(0,Math.min(1,position));
  if(scene.style==='calm')p=1-Math.abs(p*2-1);
  const at=p*(colors.length-1),a=Math.floor(at),b=Math.min(a+1,colors.length-1),t=at-a;
  const rgb=colors[a].map((v,c)=>Math.round((v*(1-t)+colors[b][c]*t)*level));
  return {r:rgb[0],g:rgb[1],b:rgb[2]};
}
export function sceneFrame(id,count,brightness=60){
  return Array.from({length:count},(_,i)=>sceneColorAt(id,i/Math.max(1,count-1),brightness));
}
// The show's own design for the canopy illustration (GET /api/canopy/design): 'heads' paints each head the design's
// colour at its canopy point (keyed at half-inch resolution, as the server's lattice), 'modules' paints every head
// of a panel its module-level (T3) colour. sceneColorAt/sceneFrame stay the 30-light wire rule of the paired shop
// gradient. Null when the design can't paint every one of `heads` at that level (the caller says so and falls back).
export function designLevels(design){return ['heads','modules'].filter(level=>Array.isArray(design?.[level])&&design[level].length);}
export function defaultDesignLevel(design){
  const levels=designLevels(design);
  return design?.ladder?.[0]==='modules'&&levels.includes('modules')?'modules':levels[0]||null;
}
export function designPaint(design,level,heads=CANOPY.heads,width=CANOPY.width){
  if(!designLevels(design).includes(level))return null;
  const key=h=>level==='modules'?h.module:Math.round(2*h.x)+','+Math.round(2*h.y),rgb=a=>({r:a[0],g:a[1],b:a[2]});
  const known=new Map(level==='modules'?design.modules.map(m=>[m.module,m.rgb]):design.heads.map(h=>[key(h),h.rgb]));
  const colors=heads.map(h=>known.get(key(h)));
  if(colors.some(c=>!Array.isArray(c)||c.length!==3))return null;
  // the floor glow under each third of the canopy: the mean of the heads above it
  const spill=p=>{
    const near=colors.filter((_,i)=>Math.abs(heads[i].x/width-p)<=1/6),pool=near.length?near:colors;
    return rgb([0,1,2].map(k=>Math.round(pool.reduce((s,c)=>s+c[k],0)/pool.length)));
  };
  return {head:h=>rgb(known.get(key(h))||[0,0,0]),spill};
}
export const EFFECT_PARAMS=Object.freeze({slot:'custom-3',field:'motionEffect',code:1,speed:30,duration_s:20,pattern:'blocks'});
export const MODULES={'bench-kit':'Kingston test','shop-string-1':'String light 1'};
export const PAIR_IDS=Object.freeze(['bench-kit','shop-string-1']);
export function matchingStaticFrames(states){
  const keys=PAIR_IDS.map(id=>{const s=states[id],c=s?.reported_controls,slot=s?.slots?.find(x=>x.slot===c?.slot);
    if(s?.read_error||c?.power!=='on'||c.color_mode!=='individual'||slot?.effects?.motionEffect!==0||slot?.effects?.brightnessEffect!==0||s?.heads?.length!==30)return null;
    return JSON.stringify([Number(c.brightness),s.heads.map(h=>h.reported)]);
  });return keys[0]!==null&&keys[0]===keys[1];
}
export function matchingWholeSettings(states){
  function key(state){const c=state?.reported_controls;if(!c||state.read_error)return null;
    if(c.power==='off')return 'off';
    if(c.power!=='on'||c.brightness==null||!Number.isFinite(Number(c.brightness)))return null;
    const base=[c.power,Number(c.brightness),c.color_mode];
    if(c.color_mode==='white')return c.cct!=null&&Number.isFinite(Number(c.cct))?JSON.stringify([...base,Number(c.cct)]):null;
    if(c.color_mode==='color'){const rgb=c.rgb?.['color-rgb']||c.rgb;return rgb&&['r','g','b'].every(k=>rgb[k]!=null&&Number.isFinite(Number(rgb[k])))?JSON.stringify([...base,...['r','g','b'].map(k=>Number(rgb[k]))]):null;}
    if(c.color_mode==='sequence')return c.preset&&c.speed!=null&&Number.isFinite(Number(c.speed))?JSON.stringify([...base,c.preset,Number(c.speed)]):null;
    return null;
  }
  const keys=PAIR_IDS.map(id=>key(states[id]));return keys[0]!==null&&keys[0]===keys[1];
}
export function sharedCapabilities(states){
  const a=states[PAIR_IDS[0]].capabilities,b=states[PAIR_IDS[1]].capabilities;
  const range=k=>a[k]&&b[k]?[Math.max(a[k][0],b[k][0]),Math.min(a[k][1],b[k][1])]:null;
  return {...a,presets:a.presets.filter(x=>b.presets.includes(x)),rgb:a.rgb&&b.rgb,cct_range:range('cct_range'),speed_range:range('speed_range'),brightness_range:range('brightness_range')};
}
export function rgbFromHex(hex){
  if(!/^#[0-9a-f]{6}$/i.test(hex))throw Error('Enter a six-digit color, for example #A855F7.');
  return [1,3,5].map(i=>parseInt(hex.slice(i,i+2),16));
}
export function customColor(hex,brightness){
  buildLook('red',brightness);return {kind:'rgb',rgb:rgbFromHex(hex),brightness};
}
export function whiteLook(kelvin,brightness,range=[2200,6500]){
  buildLook('warm',brightness);
  if(!Number.isInteger(kelvin)||kelvin<range[0]||kelvin>range[1])throw Error('Choose a supported white temperature.');
  return {kind:'white',cct:kelvin,brightness};
}
export function effectLook(preset,speed,brightness,allowed,range=[-10,10]){
  buildLook('warm',brightness);
  if(!allowed.includes(preset)||!Number.isInteger(speed)||speed<range[0]||speed>range[1])throw Error('Choose a supported effect and speed.');
  return {kind:'preset',preset,speed,brightness};
}
export function gradientColors(from,to,count=30){
  const a=rgbFromHex(from),b=rgbFromHex(to);
  return Array.from({length:count},(_,i)=>a.map((v,c)=>Math.round(v+(b[c]-v)*i/Math.max(1,count-1))));
}
export function validateTarget(device,state,id){
  if(!MODULES[id]||device?.id!==id||!device.label?.startsWith(MODULES[id]))throw Error('The selected shop module has changed. Check Workshop.');
  if(device.simulated||state?.simulated||state?.driver!=='afero')throw Error('A real shop module is required.');
  if(state?.head_count?.expected!==30)throw Error('This module does not have the expected 30-head setup.');
}
export function buildLook(id,brightness){
  const x=LOOKS.find(v=>v.id===id);
  if(!x)throw Error('Unknown look');
  if(!Number.isInteger(brightness)||brightness<1||brightness>100)throw Error('Choose brightness from 1 to 100.');
  return {kind:x.kind,brightness,...(x.kind==='white'?{cct:x.cct}:x.kind==='rgb'?{rgb:[...x.rgb]}:{preset:x.preset,speed:x.speed})};
}
export function validateKingston(device,state){
  if(device?.id!=='bench-kit'||!/^Kingston test\b/.test(device.label||''))throw Error('The bench selection has changed. Select Kingston in the workshop before using this presentation.');
  if(device.simulated||state?.simulated||state?.driver!=='afero')throw Error('This live presentation requires the real Kingston module. Use Screen preview for illustration.');
  if(state?.head_count?.expected!==30)throw Error('The selected module does not have the expected 30-head setup.');
}
export function motionReady(state){
  const h=state?.head_count;
  return state?.t1?.allowed===true&&!state.simulated&&h?.mapped===true&&h.reported===60&&
    h.controller_addresses?.length===30&&h.controller_addresses.every((v,i)=>v===i);
}
export function currentLookId(state){
  const c=state?.reported_controls||{};
  if(c.power==='off')return 'off';
  if(c.color_mode==='sequence')return c.preset;
  if(c.color_mode==='white')return Number.parseInt(c.cct,10)<=3000?'warm':'cool';
  const rgb=c.rgb?.['color-rgb']||c.rgb;
  if(c.color_mode==='color'&&Number(rgb?.r)===255&&Number(rgb?.g)===0&&Number(rgb?.b)===0)return 'red';
  if(c.color_mode==='individual')return c.slot;
  return null;
}
export function progressForRun(run,now){
  const step=run.steps?.find(x=>x.message.startsWith('Observe candidate'));
  if(!step)return {phase:'preparing',remaining:null,fraction:0};
  const remaining=Math.max(0,Math.ceil((step.t*1000+20000-now)/1000));
  return {phase:remaining?'observing':'restoring',remaining,fraction:Math.max(0,Math.min(1,(now-step.t*1000)/20000))};
}

// BEGIN GENERATED CANOPY GEOMETRY
// Generated from the existing workshop geometry; no device identifiers or credentials.
export const CANOPY = {"source":"config/canopy.yaml and the saved workshop module maps","width":225,"height":120,"panelWidth":75,"panelHeight":40,"scheme":"p14","heads":[{"module":"r0c0","row":0,"col":0,"orientation":"A","index":0,"x":2.5,"y":5},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":1,"x":12.5,"y":5},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":2,"x":22.5,"y":5},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":3,"x":32.5,"y":5},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":4,"x":42.5,"y":5},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":5,"x":52.5,"y":5},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":6,"x":62.5,"y":5},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":7,"x":72.5,"y":5},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":8,"x":67.5,"y":15},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":9,"x":57.5,"y":15},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":10,"x":47.5,"y":15},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":11,"x":37.5,"y":15},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":12,"x":27.5,"y":15},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":13,"x":17.5,"y":15},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":14,"x":7.5,"y":15},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":15,"x":2.5,"y":25},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":16,"x":12.5,"y":25},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":17,"x":22.5,"y":25},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":18,"x":32.5,"y":25},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":19,"x":42.5,"y":25},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":20,"x":52.5,"y":25},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":21,"x":62.5,"y":25},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":22,"x":72.5,"y":25},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":23,"x":67.5,"y":35},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":24,"x":57.5,"y":35},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":25,"x":47.5,"y":35},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":26,"x":37.5,"y":35},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":27,"x":27.5,"y":35},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":28,"x":17.5,"y":35},{"module":"r0c0","row":0,"col":0,"orientation":"A","index":29,"x":7.5,"y":35},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":0,"x":147.5,"y":35},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":1,"x":137.5,"y":35},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":2,"x":127.5,"y":35},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":3,"x":117.5,"y":35},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":4,"x":107.5,"y":35},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":5,"x":97.5,"y":35},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":6,"x":87.5,"y":35},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":7,"x":77.5,"y":35},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":8,"x":82.5,"y":25},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":9,"x":92.5,"y":25},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":10,"x":102.5,"y":25},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":11,"x":112.5,"y":25},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":12,"x":122.5,"y":25},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":13,"x":132.5,"y":25},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":14,"x":142.5,"y":25},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":15,"x":147.5,"y":15},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":16,"x":137.5,"y":15},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":17,"x":127.5,"y":15},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":18,"x":117.5,"y":15},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":19,"x":107.5,"y":15},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":20,"x":97.5,"y":15},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":21,"x":87.5,"y":15},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":22,"x":77.5,"y":15},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":23,"x":82.5,"y":5},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":24,"x":92.5,"y":5},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":25,"x":102.5,"y":5},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":26,"x":112.5,"y":5},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":27,"x":122.5,"y":5},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":28,"x":132.5,"y":5},{"module":"r0c1","row":0,"col":1,"orientation":"B","index":29,"x":142.5,"y":5},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":0,"x":152.5,"y":5},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":1,"x":162.5,"y":5},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":2,"x":172.5,"y":5},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":3,"x":182.5,"y":5},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":4,"x":192.5,"y":5},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":5,"x":202.5,"y":5},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":6,"x":212.5,"y":5},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":7,"x":222.5,"y":5},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":8,"x":217.5,"y":15},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":9,"x":207.5,"y":15},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":10,"x":197.5,"y":15},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":11,"x":187.5,"y":15},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":12,"x":177.5,"y":15},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":13,"x":167.5,"y":15},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":14,"x":157.5,"y":15},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":15,"x":152.5,"y":25},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":16,"x":162.5,"y":25},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":17,"x":172.5,"y":25},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":18,"x":182.5,"y":25},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":19,"x":192.5,"y":25},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":20,"x":202.5,"y":25},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":21,"x":212.5,"y":25},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":22,"x":222.5,"y":25},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":23,"x":217.5,"y":35},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":24,"x":207.5,"y":35},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":25,"x":197.5,"y":35},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":26,"x":187.5,"y":35},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":27,"x":177.5,"y":35},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":28,"x":167.5,"y":35},{"module":"r0c2","row":0,"col":2,"orientation":"A","index":29,"x":157.5,"y":35},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":0,"x":2.5,"y":45},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":1,"x":12.5,"y":45},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":2,"x":22.5,"y":45},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":3,"x":32.5,"y":45},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":4,"x":42.5,"y":45},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":5,"x":52.5,"y":45},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":6,"x":62.5,"y":45},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":7,"x":72.5,"y":45},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":8,"x":67.5,"y":55},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":9,"x":57.5,"y":55},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":10,"x":47.5,"y":55},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":11,"x":37.5,"y":55},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":12,"x":27.5,"y":55},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":13,"x":17.5,"y":55},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":14,"x":7.5,"y":55},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":15,"x":2.5,"y":65},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":16,"x":12.5,"y":65},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":17,"x":22.5,"y":65},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":18,"x":32.5,"y":65},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":19,"x":42.5,"y":65},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":20,"x":52.5,"y":65},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":21,"x":62.5,"y":65},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":22,"x":72.5,"y":65},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":23,"x":67.5,"y":75},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":24,"x":57.5,"y":75},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":25,"x":47.5,"y":75},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":26,"x":37.5,"y":75},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":27,"x":27.5,"y":75},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":28,"x":17.5,"y":75},{"module":"r1c0","row":1,"col":0,"orientation":"A","index":29,"x":7.5,"y":75},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":0,"x":147.5,"y":75},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":1,"x":137.5,"y":75},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":2,"x":127.5,"y":75},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":3,"x":117.5,"y":75},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":4,"x":107.5,"y":75},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":5,"x":97.5,"y":75},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":6,"x":87.5,"y":75},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":7,"x":77.5,"y":75},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":8,"x":82.5,"y":65},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":9,"x":92.5,"y":65},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":10,"x":102.5,"y":65},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":11,"x":112.5,"y":65},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":12,"x":122.5,"y":65},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":13,"x":132.5,"y":65},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":14,"x":142.5,"y":65},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":15,"x":147.5,"y":55},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":16,"x":137.5,"y":55},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":17,"x":127.5,"y":55},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":18,"x":117.5,"y":55},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":19,"x":107.5,"y":55},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":20,"x":97.5,"y":55},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":21,"x":87.5,"y":55},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":22,"x":77.5,"y":55},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":23,"x":82.5,"y":45},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":24,"x":92.5,"y":45},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":25,"x":102.5,"y":45},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":26,"x":112.5,"y":45},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":27,"x":122.5,"y":45},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":28,"x":132.5,"y":45},{"module":"r1c1","row":1,"col":1,"orientation":"B","index":29,"x":142.5,"y":45},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":0,"x":152.5,"y":45},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":1,"x":162.5,"y":45},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":2,"x":172.5,"y":45},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":3,"x":182.5,"y":45},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":4,"x":192.5,"y":45},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":5,"x":202.5,"y":45},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":6,"x":212.5,"y":45},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":7,"x":222.5,"y":45},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":8,"x":217.5,"y":55},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":9,"x":207.5,"y":55},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":10,"x":197.5,"y":55},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":11,"x":187.5,"y":55},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":12,"x":177.5,"y":55},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":13,"x":167.5,"y":55},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":14,"x":157.5,"y":55},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":15,"x":152.5,"y":65},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":16,"x":162.5,"y":65},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":17,"x":172.5,"y":65},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":18,"x":182.5,"y":65},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":19,"x":192.5,"y":65},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":20,"x":202.5,"y":65},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":21,"x":212.5,"y":65},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":22,"x":222.5,"y":65},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":23,"x":217.5,"y":75},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":24,"x":207.5,"y":75},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":25,"x":197.5,"y":75},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":26,"x":187.5,"y":75},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":27,"x":177.5,"y":75},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":28,"x":167.5,"y":75},{"module":"r1c2","row":1,"col":2,"orientation":"A","index":29,"x":157.5,"y":75},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":0,"x":2.5,"y":85},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":1,"x":12.5,"y":85},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":2,"x":22.5,"y":85},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":3,"x":32.5,"y":85},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":4,"x":42.5,"y":85},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":5,"x":52.5,"y":85},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":6,"x":62.5,"y":85},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":7,"x":72.5,"y":85},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":8,"x":67.5,"y":95},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":9,"x":57.5,"y":95},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":10,"x":47.5,"y":95},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":11,"x":37.5,"y":95},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":12,"x":27.5,"y":95},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":13,"x":17.5,"y":95},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":14,"x":7.5,"y":95},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":15,"x":2.5,"y":105},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":16,"x":12.5,"y":105},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":17,"x":22.5,"y":105},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":18,"x":32.5,"y":105},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":19,"x":42.5,"y":105},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":20,"x":52.5,"y":105},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":21,"x":62.5,"y":105},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":22,"x":72.5,"y":105},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":23,"x":67.5,"y":115},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":24,"x":57.5,"y":115},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":25,"x":47.5,"y":115},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":26,"x":37.5,"y":115},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":27,"x":27.5,"y":115},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":28,"x":17.5,"y":115},{"module":"r2c0","row":2,"col":0,"orientation":"A","index":29,"x":7.5,"y":115},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":0,"x":147.5,"y":115},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":1,"x":137.5,"y":115},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":2,"x":127.5,"y":115},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":3,"x":117.5,"y":115},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":4,"x":107.5,"y":115},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":5,"x":97.5,"y":115},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":6,"x":87.5,"y":115},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":7,"x":77.5,"y":115},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":8,"x":82.5,"y":105},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":9,"x":92.5,"y":105},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":10,"x":102.5,"y":105},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":11,"x":112.5,"y":105},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":12,"x":122.5,"y":105},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":13,"x":132.5,"y":105},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":14,"x":142.5,"y":105},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":15,"x":147.5,"y":95},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":16,"x":137.5,"y":95},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":17,"x":127.5,"y":95},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":18,"x":117.5,"y":95},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":19,"x":107.5,"y":95},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":20,"x":97.5,"y":95},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":21,"x":87.5,"y":95},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":22,"x":77.5,"y":95},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":23,"x":82.5,"y":85},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":24,"x":92.5,"y":85},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":25,"x":102.5,"y":85},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":26,"x":112.5,"y":85},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":27,"x":122.5,"y":85},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":28,"x":132.5,"y":85},{"module":"r2c1","row":2,"col":1,"orientation":"B","index":29,"x":142.5,"y":85},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":0,"x":152.5,"y":85},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":1,"x":162.5,"y":85},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":2,"x":172.5,"y":85},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":3,"x":182.5,"y":85},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":4,"x":192.5,"y":85},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":5,"x":202.5,"y":85},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":6,"x":212.5,"y":85},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":7,"x":222.5,"y":85},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":8,"x":217.5,"y":95},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":9,"x":207.5,"y":95},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":10,"x":197.5,"y":95},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":11,"x":187.5,"y":95},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":12,"x":177.5,"y":95},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":13,"x":167.5,"y":95},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":14,"x":157.5,"y":95},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":15,"x":152.5,"y":105},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":16,"x":162.5,"y":105},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":17,"x":172.5,"y":105},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":18,"x":182.5,"y":105},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":19,"x":192.5,"y":105},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":20,"x":202.5,"y":105},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":21,"x":212.5,"y":105},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":22,"x":222.5,"y":105},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":23,"x":217.5,"y":115},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":24,"x":207.5,"y":115},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":25,"x":197.5,"y":115},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":26,"x":187.5,"y":115},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":27,"x":177.5,"y":115},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":28,"x":167.5,"y":115},{"module":"r2c2","row":2,"col":2,"orientation":"A","index":29,"x":157.5,"y":115}],"bench":{"bench-kit":[{"index":0,"x":2.5,"y":5.0},{"index":1,"x":12.5,"y":5.0},{"index":2,"x":22.5,"y":5.0},{"index":3,"x":32.5,"y":5.0},{"index":4,"x":42.5,"y":5.0},{"index":5,"x":52.5,"y":5.0},{"index":6,"x":62.5,"y":5.0},{"index":7,"x":72.5,"y":5.0},{"index":8,"x":67.5,"y":15.0},{"index":9,"x":57.5,"y":15.0},{"index":10,"x":47.5,"y":15.0},{"index":11,"x":37.5,"y":15.0},{"index":12,"x":27.5,"y":15.0},{"index":13,"x":17.5,"y":15.0},{"index":14,"x":7.5,"y":15.0},{"index":15,"x":2.5,"y":25.0},{"index":16,"x":12.5,"y":25.0},{"index":17,"x":22.5,"y":25.0},{"index":18,"x":32.5,"y":25.0},{"index":19,"x":42.5,"y":25.0},{"index":20,"x":52.5,"y":25.0},{"index":21,"x":62.5,"y":25.0},{"index":22,"x":72.5,"y":25.0},{"index":23,"x":67.5,"y":35.0},{"index":24,"x":57.5,"y":35.0},{"index":25,"x":47.5,"y":35.0},{"index":26,"x":37.5,"y":35.0},{"index":27,"x":27.5,"y":35.0},{"index":28,"x":17.5,"y":35.0},{"index":29,"x":7.5,"y":35.0}],"shop-string-1":[{"index":0,"x":2.5,"y":5.0},{"index":1,"x":12.5,"y":5.0},{"index":2,"x":22.5,"y":5.0},{"index":3,"x":32.5,"y":5.0},{"index":4,"x":42.5,"y":5.0},{"index":5,"x":52.5,"y":5.0},{"index":6,"x":62.5,"y":5.0},{"index":7,"x":72.5,"y":5.0},{"index":8,"x":67.5,"y":15.0},{"index":9,"x":57.5,"y":15.0},{"index":10,"x":47.5,"y":15.0},{"index":11,"x":37.5,"y":15.0},{"index":12,"x":27.5,"y":15.0},{"index":13,"x":17.5,"y":15.0},{"index":14,"x":7.5,"y":15.0},{"index":15,"x":2.5,"y":25.0},{"index":16,"x":12.5,"y":25.0},{"index":17,"x":22.5,"y":25.0},{"index":18,"x":32.5,"y":25.0},{"index":19,"x":42.5,"y":25.0},{"index":20,"x":52.5,"y":25.0},{"index":21,"x":62.5,"y":25.0},{"index":22,"x":72.5,"y":25.0},{"index":23,"x":67.5,"y":35.0},{"index":24,"x":57.5,"y":35.0},{"index":25,"x":47.5,"y":35.0},{"index":26,"x":37.5,"y":35.0},{"index":27,"x":27.5,"y":35.0},{"index":28,"x":17.5,"y":35.0},{"index":29,"x":7.5,"y":35.0}]}};
// END GENERATED CANOPY GEOMETRY

// All positions originate in the saved workshop configuration, in panel inches.
export function displayLayout(scope='canopy',device='both',maps={}){
  if(scope==='canopy')return {width:CANOPY.width,height:CANOPY.height,heads:CANOPY.heads.map(h=>({...h,device:null})),panels:Array.from({length:9},(_,i)=>({id:`r${Math.floor(i/3)}c${i%3}`,x:(i%3)*CANOPY.panelWidth,y:Math.floor(i/3)*CANOPY.panelHeight,label:`${i+1}`}))};
  const ids=device==='both'?PAIR_IDS:[device];
  return {width:ids.length*CANOPY.panelWidth+(ids.length-1)*10,height:CANOPY.panelHeight,
    panels:ids.map((id,i)=>({id,x:i*(CANOPY.panelWidth+10),y:0,label:MODULES[id]})),
    heads:ids.flatMap((id,i)=>(maps[id]?.heads||CANOPY.bench[id]).map(h=>({...h,x:h.x+i*(CANOPY.panelWidth+10),module:id,device:id})))};
}
const NS='http://www.w3.org/2000/svg';
function element(tag,attrs={},text){const e=document.createElementNS(NS,tag);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);if(text)e.textContent=text;return e;}
export function buildCanopy(container,{scope,device,maps={},angle='perspective'}){
  const layout=displayLayout(scope,device,maps);
  const svg=element('svg',{viewBox:'0 0 720 405',role:'img',class:'canopy-svg','aria-label':`${layout.heads.length} lights, 30 per panel, using the saved workshop geometry`});
  const defs=element('defs');defs.innerHTML='<filter id="scene-halo"><feGaussianBlur stdDeviation="3"/></filter><filter id="scene-spill"><feGaussianBlur stdDeviation="18"/></filter>';svg.append(defs);
  const top=angle==='plan';
  if(!top){
    svg.append(element('path',{d:'M0 0H720V300L552 252H158L0 300Z',class:'canopy-room'}),element('path',{d:'M0 300L158 252H552L720 300V405H0Z',class:'canopy-floor'}),element('path',{d:'M158 0V252H552V0M0 300L158 252M552 252L720 300',class:'canopy-room-lines'}));
  }
  const scale=top?Math.min(620/layout.width,280/layout.height):550/(layout.width+layout.height*.65);
  const origin=top?[(720-layout.width*scale)/2,(370-layout.height*scale)/2]:[(720-(layout.width+layout.height*.65)*scale)/2,65];
  const project=(x,y)=>top?[origin[0]+x*scale,origin[1]+y*scale]:[origin[0]+(x+y*.65)*scale,origin[1]+(y*.65+x*.06)*scale];
  const spills=[];
  if(!top){
    for(const p of [.15,.5,.85]){const e=element('ellipse',{cx:170+p*390,cy:321,rx:100,ry:36,filter:'url(#scene-spill)',opacity:.15});spills.push({element:e,position:p});svg.append(e);}
    svg.append(element('path',{d:'M225 300L485 308L540 353L253 349Z',class:'canopy-rug'}),element('path',{d:'M235 271L365 274L377 322L244 319ZM244 319L249 334M377 322L382 337M430 292L485 295L496 332L439 329Z',class:'canopy-furniture'}),element('ellipse',{cx:409,cy:336,rx:26,ry:8,class:'canopy-furniture'}));
  }
  const panels=element('g',{class:'canopy-panels','aria-hidden':'true'});
  for(const panel of layout.panels){
    const pts=[[panel.x,panel.y],[panel.x+CANOPY.panelWidth,panel.y],[panel.x+CANOPY.panelWidth,panel.y+CANOPY.panelHeight],[panel.x,panel.y+CANOPY.panelHeight]].map(([x,y])=>project(x,y));
    panels.append(element('polygon',{points:pts.map(p=>p.join(',')).join(' '),class:'canopy-panel'}));
    const [tx,ty]=project(panel.x+CANOPY.panelWidth*.5,panel.y);
    panels.append(element('text',{x:tx,y:ty-7,'text-anchor':'middle',class:'canopy-panel-label'},panel.label));
  }
  svg.append(panels);
  const bulbs=element('g',{class:'canopy-bulbs','aria-hidden':'true'}),nodes=[];
  for(const h of layout.heads){
    const [x,y]=project(h.x,h.y),drop=top?0:8;
    const group=element('g',{'data-module':h.module,'data-head':h.index});
    if(!top)group.append(element('line',{x1:x,y1:y,x2:x,y2:y+drop,class:'canopy-drop'}));
    const halo=element('circle',{cx:x,cy:y+drop,r:top?7:8,filter:'url(#scene-halo)',opacity:.5});
    const bulb=element('ellipse',{cx:x,cy:y+drop,rx:top?3.4:3,ry:top?3.4:3.7,class:'canopy-bulb'});
    group.append(halo,bulb,element('title',{},`${h.device?MODULES[h.device]:'Panel '+(h.row*3+h.col+1)} · light ${h.index+1}`));bulbs.append(group);nodes.push({head:h,halo,bulb});
  }
  svg.append(bulbs);
  container.replaceChildren(svg);
  return {layout,svg,nodes,spills};
}
export function paintCanopy(view,colorForHead,colorForSpill,level=100){
  for(const n of view.nodes){const c=colorForHead(n.head);const rgb=`rgb(${c.r},${c.g},${c.b})`;n.bulb.setAttribute('fill',rgb);n.halo.setAttribute('fill',rgb);n.bulb.setAttribute('opacity',.3+.7*level/100);n.halo.setAttribute('opacity',.1+.6*level/100);}
  for(const n of view.spills){const c=colorForSpill(n.position);n.element.setAttribute('fill',`rgb(${c.r},${c.g},${c.b})`);n.element.setAttribute('opacity',.04+.15*level/100);}
}
