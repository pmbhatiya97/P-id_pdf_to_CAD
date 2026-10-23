const fs = require('fs');
const path = require('path');
const sharp = require('sharp');

const source = fs.readFileSync(path.join(__dirname, '../../lineaforge/agentic.py'), 'utf8').split('AGENT_CATALOG = [')[1].split('ROLE_MAP =')[0];
const roles = [...source.matchAll(/_role\("([^"]+)", "([^"]+)", "([^"]+)", "[^"]*"(?:, "([^"]+)")?/g)].map((m, i) => ({ id: m[1], name: m[2], group: m[3], type: m[4] || 'vlm', number: String(i + 1).padStart(2, '0') }));
if (roles.length !== 28) throw new Error('Expected all 28 agent roles');
const counts = roles.reduce((a, r) => ((a[r.type] = (a[r.type] || 0) + 1), a), {});
if (counts.vlm !== 19 || counts.llm !== 4 || counts.deterministic !== 5) throw new Error('Catalog execution types changed');

const esc = s => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const parts = [];
const add = s => parts.push(s);
const rect = (x,y,w,h,fill,stroke='none',rx=14) => add(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}"/>`);
const text = (x,y,s,size=24,fill='#19314C',weight=400,extra='') => add(`<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" font-weight="${weight}" ${extra}>${esc(s)}</text>`);
const colors = {vlm:['#E1F6F4','#007C79','V'],llm:['#EEE7FF','#6845B5','L'],deterministic:['#E3ECFC','#285A9D','P']};
const role = (r,x,y) => {
  const [bg, fg, code] = colors[r.type];
  text(x,y,r.number,20,'#668097',600);
  text(x+39,y,r.name+(r.id==='repair'?'*':''),22,'#1C344E',550);
  rect(x+446,y-20,35,27,bg,'none',7);
  text(x+463.5,y,code,18,fg,750,'text-anchor="middle"');
};
const group = (name, kind, x, y, h, caption) => {
  const rs = roles.filter(r=>r.group===kind);
  rect(x,y,540,h,'#FFFFFF','#D5E2EC',16);
  rect(x,y,7,h,'#008B89','none',3);
  text(x+22,y+34,name,23,'#0A4057',750);
  text(x+514,y+34,String(rs.length),23,'#008784',750,'text-anchor="end"');
  rs.forEach((r,i)=>role(r,x+22,y+71+i*28));
  if (caption) text(x+22,y+h-18,caption,18,'#688093');
};

add('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1500" viewBox="0 0 1200 1500" role="img" aria-labelledby="title desc">');
add('<title id="title">LineaForge Studio Beta — P&amp;ID agent ecosystem</title><desc id="desc">Social post graphic showing all 28 specialist roles, configured models, reviewed graph workflow and offline DXF or MCP native CAD connections.</desc>');
add('<defs><linearGradient id="hero" x2="1" y2="1"><stop stop-color="#0A223F"/><stop offset="1" stop-color="#0C4D60"/></linearGradient></defs>');
add('<style>text{font-family:"Segoe UI",Arial,sans-serif}</style>');
rect(0,0,1200,1500,'#F3F7FA','none',0);
rect(0,0,1200,204,'url(#hero)','none',0);
rect(990,42,162,43,'#D9F7EA','none',22);
text(1071,71,'BETA VERSION',18,'#075A4D',750,'text-anchor="middle"');
text(48,91,'LineaForge Studio',53,'#FFFFFF',750);
text(48,144,'The P&ID AI ecosystem',33,'#FFFFFF',600);
text(48,180,'28 specialist roles. Configurable models. Human-reviewed CAD.',23,'#C5E5ED');

rect(48,226,1104,74,'#E7F0FA','#D3E1EF');
text(70,256,'REACT WORKBENCH  →  PYTHON / FASTAPI',24,'#1C466A',750);
text(70,284,'P&ID Studio • Agent Studio • Model configuration • Jobs • Shared evidence',21,'#43647E');
rect(48,316,1104,50,'#DDF2EE','#B7DCD5');
text(70,349,'01  Job orchestrator',24,'#075D5C',750);
text(426,349,'Python • Dependencies • Budgets • Up to 4 parallel calls',22,'#245D63');

group('UNDERSTAND','understand',48,386,180,'Full-sheet overview, layout and visible conventions');
group('CLEAN & PRESERVE','clean',612,386,180,'Tiled evidence • Mask checks • Preserve source content');
group('EXTRACT SYMBOLS & TEXT','extract',48,584,270);
group('TRACE CONNECTIONS','connect',612,584,270,'Explicit routes and references; uncertainty stays visible');
group('VALIDATE THE GRAPH','validate',48,872,238);
group('PLAN, BUILD & VERIFY','deliver',612,872,238,'CAD QA combines Python checks with VLM comparison');

text(48,1147,'V  VLM: 19 vision roles',21,'#007C79',650);
text(416,1147,'L  LLM: 4 reasoning roles',21,'#6845B5',650);
text(815,1147,'P  Python: 5 control roles',21,'#285A9D',650);
text(48,1176,'*Repair is opt-in. 27 roles enabled by default. External data transfer requires consent.',18,'#667F93');

rect(48,1198,1104,62,'#FFF0D8','#EAD2A3');
text(600,1236,'Human review  →  Reviewed graph  →  CAD build  →  QA  →  Release review',23,'#765018',650,'text-anchor="middle"');

rect(48,1280,1104,91,'#FFFFFF','#CEDFE9');
text(70,1314,'CAD BACKENDS',21,'#0B5264',750);
text(286,1314,'Offline DXF (ezdxf)  OR  MCP client → Windows CAD bridge',23,'#244A65',600);
text(70,1349,'AutoCAD / SOLIDWORKS 2D • Local stdio / authenticated HTTPS • Editable DXF / DWG',21,'#43647E');

text(48,1414,'BUILT WITH REACT + FASTAPI',21,'#0B5264',750);
text(48,1451,'Windows local • Server deployment • Model accuracy requires validation',21,'#536F85');
rect(1018,1394,134,62,'#DDF2EE','none',10);
text(1085,1421,'P&ID AI',23,'#076B69',750,'text-anchor="middle"');
text(1085,1444,'IN BETA',17,'#076B69',650,'text-anchor="middle"');
add('</svg>');

const svg = parts.join('\n');
const stem = path.join(__dirname, 'lineaforge-linkedin-beta-1200x1500');
fs.writeFileSync(stem+'.svg', svg);
Promise.all([
  sharp(Buffer.from(svg)).flatten({background:'#FFFFFF'}).png().toFile(stem+'.png'),
  sharp(Buffer.from(svg)).flatten({background:'#FFFFFF'}).jpeg({quality:95,chromaSubsampling:'4:4:4'}).toFile(stem+'.jpg')
]).then(result=>process.stdout.write(JSON.stringify({roles:roles.length,counts,files:result}))).catch(e=>{process.stderr.write(String(e));process.exitCode=1});
