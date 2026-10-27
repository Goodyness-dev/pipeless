import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const runtime = 'C:/Users/DELL/.cache/codex-runtimes/codex-primary-runtime/dependencies';
process.env.RUNTIME_NODE_MODULES=runtime+'/node/node_modules';
const skill = 'C:/Users/DELL/.codex/plugins/cache/openai-primary-runtime/presentations/26.1025.11240/skills/presentations';
const {Presentation, PresentationFile} = await import(pathToFileURL(runtime+'/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs'));
const root=process.cwd(), build=path.join(root,'data/pitch-build'), out=path.join(root,'submission');
await fs.mkdir(build,{recursive:true});await fs.mkdir(out,{recursive:true});
const p=Presentation.create({slideSize:{width:1280,height:720}});
p.theme.defaultFont='Arial';
const content=[
 ['Pipeless','Data answers with receipts','A data investigation agent that exposes\nthe queries and checks behind its answers.'],
 ['The analyst’s risk','A valid query can still hide records','A join can remove unmapped records.\nA later filter can also undo a LEFT JOIN.\nThe analyst needs the missing keys and an auditable query.'],
 ['The audit workflow','Executed evidence drives the review','Planner defines the task.\nA tool service inspects Zetaris sources.\nInvestigator writes read-only SQL.\nVerifier collects evidence for a separate Skeptic.\nInvestigator revises the query when challenged.'],
 ['Sponsor integration','Queries in Zetaris, models through Nightshift','Zetaris executes the query and verification probes.\nMeterless Nightshift routes the real model calls.\nNVIDIA Nemotron Lightning handles planning and SQL.\nNemotron Ultra reviews evidence and finalizes the answer.'],
 ['Verification boundaries','Output retention needs a measured check','The verifier compares distinct source keys with actual output keys.\nIt catches record loss through filters even when every ID matches.\nMatching IDs does not establish semantic compatibility.\nDate coverage stays unverified until values and gaps are checked.'],
 ['Prototype scope','A local demonstration on sponsor samples','Current demo: Zetaris taxi reference datasets.\nSQL uses a builder identity for read-only operations.\nThe viewer displays recorded outputs with provenance.\nNext: verify reader permissions, date ranges and broader sources.']
];
const repairPath=path.join(root,'data/demo/repair-evaluation.json');
try {
 const run=JSON.parse(await fs.readFile(repairPath,'utf8'));
 const first=run.result.revision_history[0].checks.find(c=>c.id==='join_loss');
 const last=run.result.evidence.checks.find(c=>c.id==='join_loss');
 if(first?.evidence?.source_keys_retained!==undefined && last?.evidence?.source_keys_retained!==undefined){
  content.splice(5,0,['Observed key retention repair',`${first.evidence.source_keys_retained} of ${first.evidence.left_total} keys before, ${last.evidence.source_keys_retained} after`,
   `The initial query drops LocationID 1 through a filter.\nThe Skeptic requests a revision using executed evidence.\nThe Investigator revises SQL, and Zetaris rechecks key retention.\nLabelled evaluation: the test harness supplies the first flawed query.`]);
 }
} catch(error) {if(error.code!=='ENOENT')throw error;}
for(const [section,title,body] of content){
 const s=p.slides.add();s.background.fill='#0B181E';
 function text(t,top,size,color,height){const sh=s.shapes.add({geometry:'textbox',position:{left:80,top,width:1120,height},fill:'none',line:{fill:'none',width:0}});sh.text=t;sh.text.style={typeface:'Arial',fontSize:size,color};return sh;}
 text(section,55,26,'#66DFBA',65);text(title,150,54,'#FFFFFF',150);text(body,340,31,'#CDDCDF',310);
 s.speakerNotes.text='Project implementation and recorded evidence in genAi. Official event rules: https://hackathon.genai.works/event/open-agent-hackathon-2026. Prototype limitations are intentional disclosures.';
}
const screenshotPath=path.join(root,'data/demo/evidence-screenshot.png');
try {const bytes=await fs.readFile(screenshotPath);const s=p.slides.add();s.background.fill='#0B181E';const heading=s.shapes.add({geometry:'textbox',position:{left:80,top:42,width:1120,height:75},fill:'none',line:{fill:'none',width:0}});heading.text='Recorded execution evidence';heading.text.style={typeface:'Arial',fontSize:42,color:'#FFFFFF'};s.images.add({blob:bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),contentType:'image/png',alt:'Pipeless evidence viewer showing a recorded real query-repair run',fit:'contain',position:{left:80,top:130,width:1120,height:530}});s.speakerNotes.text='Original screenshot from the local Pipeless evidence viewer. The viewer displays real recorded runs and identifies the deliberately seeded evaluation input.';}catch(error){if(error.code!=='ENOENT')throw error;}
for(let i=0;i<p.slides.items.length;i++){const blob=await p.export({slide:p.slides.items[i],format:'png',scale:1});await fs.writeFile(path.join(build,`slide-${i+1}.png`),new Uint8Array(await blob.arrayBuffer()));}
const candidate=path.join(build,'candidate.pptx');await(await PresentationFile.exportPptx(p)).save(candidate);
const {finalizePresentation}=await import(pathToFileURL(skill+'/container_tools/artifact_tool_utils.mjs'));
const result=await finalizePresentation({workspaceDir:root,candidatePath:candidate,finalPath:path.join(out,`Pipeless-pitch-${process.argv[2]||'final'}.pptx`),pythonExecutable:runtime+'/python/python.exe',integrityValidatorPath:skill+'/container_tools/inspect_presentation_package_integrity.py',layoutValidatorPath:skill+'/container_tools/inspect_presentation_layout_geometry.py',layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-heading-fit'],fontPolicy:{basis:'design',families:['Arial']},verifyArtifactToolImport:true,receiptPath:path.join(build,'validation-final.json')});
console.log(JSON.stringify(result));
