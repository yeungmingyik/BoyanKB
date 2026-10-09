import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { Presentation, PresentationFile } from '@oai/artifact-tool';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(root, 'branding/templates/ppt-v2');
const build = path.join(root, 'temp/ppt-design-chinese/build');
const skill = process.env.PRESENTATIONS_SKILL_ROOT;
const python = process.env.ARTIFACT_PYTHON;
if (!skill || !python) throw new Error('PRESENTATIONS_SKILL_ROOT and ARTIFACT_PYTHON are required');
const { finalizePresentation } = await import(pathToFileURL(path.join(skill, 'container_tools/artifact_tool_utils.mjs')).href);
const font = 'Microsoft YaHei';
const c = { navy:'#1F4884', blue:'#2D64B0', sky:'#60B9E8', teal:'#379CB7', ink:'#182A43', muted:'#596B80', light:'#EAF6FD', line:'#D9E4F0', amber:'#F4B34A' };
const logo = new Uint8Array(await fs.readFile(path.join(root, 'branding/logo.png')));
const scenes = {
  teaching:{name:'科创课堂',accent:c.blue,tint:'#EAF6FD',cover:'让好奇心\n走进课堂',subtitle:'从一个问题出发\n在探索中理解，在实践中成长',overview:'今天，我们一起完成三件事',points:['认识本节课的关键问题','动手实践，观察并记录结果','分享作品，说出自己的发现'],heading:'先观察，再提出你的问题',body:'观察材料或演示中的变化。\n\n说一说：你看到了什么？\n哪一个现象最让你好奇？\n\n把问题写下来，再用课堂任务验证自己的想法。',stepsHeading:'把想法变成一次完整的实践',steps:['观察任务\n明确目标与所需材料','动手尝试\n按步骤操作并记录','交流发现\n展示结果并解释思路'],stepNote:'具体知识点、器材与操作要求以已确认的课程内容为准。',questionHeading:'停下来，想一想',question:'如果结果与预期不同，\n你会先检查哪一步？',hint:'先独立思考，再与同伴交流。\n用观察记录支持自己的判断。',compareHeading:'从“做出来”到“讲明白”',left:'操作与观察',right:'表达与反思',leftBody:'说明自己做了什么\n记录过程中的变化\n保留关键观察结果',rightBody:'解释结果与预期的差异\n说出可以改进的地方\n提出下一次探索的问题',tableHeading:'课堂任务与学习记录',table:[['环节','任务','学习产出'],['导入','观察现象，提出问题','问题记录'],['实践','完成任务，记录过程','作品与观察记录'],['分享','说明发现，讨论改进','学习收获']],closing:'把今天的发现，\n带进下一次探索',summary:'我认识了什么？\n我完成了什么？\n下一次，我还想试试什么？',note:'具体课程事实须从当前授权知识库检索并引用。'},
  business:{name:'合作提案',accent:c.navy,tint:'#F0F5FB',cover:'连接需求\n共创价值',subtitle:'围绕科创教育场景\n明确合作内容、实施条件与下一步',overview:'从需求出发，把合作谈清楚',points:['确认服务对象与真实需求','匹配有资料支持的业务内容','明确实施条件与待确认事项'],heading:'先对齐需求，再展开方案',body:'围绕服务对象与使用场景，梳理当前需求。\n\n区分已有事实、方案建议与待确认条件。\n\n业务范围以授权知识库为依据；价格和承诺以总部书面确认为准。',stepsHeading:'让每一步都有明确的输出',steps:['需求沟通\n确认对象、目标与约束','方案确认\n核实内容、职责与条件','实施准备\n落实安排并明确下一步'],stepNote:'实际周期、费用、责任人与合作条款由双方确认。',questionHeading:'先确认这个关键问题',question:'这份方案，\n要解决对方的什么问题？',hint:'用具体的服务对象、使用场景和需求描述回答。',compareHeading:'把需求与实施条件放在一起看',left:'需求与目标',right:'内容与条件',leftBody:'服务对象与使用场景\n已有问题与目标\n尚需核实的约束',rightBody:'资料支持的业务范围\n实施所需人员与资源\n需要双方确认的事项',tableHeading:'合作事项确认表',table:[['事项','讨论内容','确认状态'],['业务范围','依据授权知识库填写','待确认'],['实施安排','核实周期、人员与条件','待确认'],['商务条款','以总部书面确认为准','待确认']],closing:'对齐下一步，\n让合作开始发生',summary:'确认本次沟通的共识\n记录仍需核实的事项\n明确下一步安排与责任人',note:'模板不预置价格、客户案例、独家权益或成果承诺。'},
  operations:{name:'活动执行',accent:c.teal,tint:'#EBF6F8',cover:'有序准备\n从容执行',subtitle:'围绕目标统筹安排\n让流程、分工与预算彼此对应',overview:'执行之前，先把三件事落实',points:['确认目标、人群、时间与规模','核实场地、人员与物料条件','逐项检查预算、风险与应对'],heading:'让方案可以被现场团队使用',body:'把活动目标拆成清晰的准备和执行任务。\n\n每项任务明确负责人、完成时间与所需资源。\n\n现场记录问题与处理结果，为复盘保留依据。',stepsHeading:'按阶段推进，逐项确认完成',steps:['前期准备\n核实场地、人员和物料','现场执行\n按议程推进并记录问题','成果复盘\n整理结果与改进事项'],stepNote:'未确认的场地、报价、人员和时间应明确标为待核实。',questionHeading:'准备好应对现场变化了吗？',question:'如果关键资源临时不可用，\n谁来处理，如何调整？',hint:'写出备选安排、联络人和触发条件。',compareHeading:'把正常流程与应急安排一起准备',left:'执行安排',right:'风险应对',leftBody:'关键任务与时间节点\n负责人及协作人员\n现场记录与交付要求',rightBody:'可能影响执行的风险\n应对方式与责任人\n需要升级处理的条件',tableHeading:'阶段任务与分工',table:[['阶段','主要任务','负责人'],['准备','核实场地、人员与物料','待指定'],['执行','按议程开展并记录问题','待指定'],['复盘','整理结果与改进事项','待指定']],closing:'把执行经验，\n变成下一次的进步',summary:'整理现场记录与产出\n核对资源使用和预算\n确定下一次的改进动作',note:'当地要求、供应商报价与执行条件均需另行核实。'}
};
function rect(s,name,x,y,w,h,fill) {
  return s.shapes.add({name,geometry:'rect',position:{left:x,top:y,width:w,height:h},fill,line:{fill:'none',width:0}});
}
function txt(s,name,value,x,y,w,h,size=28,color=c.ink,bold=false) {
  const shape=rect(s,name,x,y,w,h,'none');
  shape.text=value;
  shape.text.style={typeface:font,fontSize:size,color,bold,wrap:true,verticalAlignment:'top',insets:0};
  return shape;
}
function picture(s,blob,x,y,w,h,alt) {
  s.images.add({blob,contentType:'image/png',alt,fit:'contain',position:{left:x,top:y,width:w,height:h}});
}
function page(p,scene,index,title) {
  const s=p.slides.add();
  s.background.fill='#FFFFFF';
  picture(s,logo,64,30,58,34,'博研文化标志');
  txt(s,'brand','博研文化',136,33,200,30,22,c.navy,true);
  txt(s,'section-label',scene.name,993,37,223,26,18,c.muted);
  rect(s,'top-accent',64,84,50,5,scene.accent);
  txt(s,'heading',title,64,108,1148,100,40,scene.accent,true);
  rect(s,'footer-rule',64,659,1152,1,c.line);
  txt(s,'page',String(index).padStart(2,'0'),1167,675,48,24,15,c.muted);
  s.speakerNotes.text=scene.note;
  return s;
}
await fs.mkdir(path.join(build,'validated'),{recursive:true});
const templates=[];
for (const [key,scene] of Object.entries(scenes)) {
  const art=new Uint8Array(await fs.readFile(path.join(out,'assets',key+'.png')));
  const questionArt=new Uint8Array(await fs.readFile(path.join(out,'assets/question.png')));
  const p=Presentation.create({slideSize:{width:1280,height:720}});
  p.theme.defaultFont=font;
  const s=p.slides.add();
  const dark=key==='business';
  s.background.fill=dark?c.navy:scene.tint;
  rect(s,'cover-panel',746,0,534,720,dark?'#DDECF7':'#FFFFFF');
  rect(s,'cover-sky-line',0,0,1280,9,c.sky);
  picture(s,logo,72,50,65,39,'博研文化标志');
  txt(s,'brand','博研文化',154,54,380,40,26,dark?'#FFFFFF':c.navy,true);
  txt(s,'cover-title',scene.cover,72,194,650,184,60,dark?'#FFFFFF':c.navy,true);
  rect(s,'cover-title-rule',76,385,56,5,dark?c.sky:scene.accent);
  txt(s,'cover-subtitle',scene.subtitle,76,412,620,100,24,dark?'#DCEFFA':c.muted);
  txt(s,'cover-context',scene.name+'  ·  博研文化',76,616,1050,28,18,dark?'#DCEFFA':c.navy);
  picture(s,art,698,163,568,458,scene.name+'原创插画');
  s.speakerNotes.text=scene.note;
  let q=page(p,scene,2,scene.overview);
  for (let i=0;i<3;i++) {
    txt(q,'number-'+i,String(i+1).padStart(2,'0'),64,235+i*116,78,64,44,scene.accent,true);
    txt(q,'point-'+i,scene.points[i],160,239+i*116,650,82,28);
    if(i<2) rect(q,'overview-rule-'+i,160,335+i*116,650,1,c.line);
  }
  picture(q,art,843,253,377,314,scene.name+'原创插画');
  q=page(p,scene,3,scene.heading);
  txt(q,'body',scene.body,64,232,704,338,28);
  rect(q,'illustration-panel',816,224,400,392,scene.tint);
  picture(q,art,820,251,395,322,scene.name+'原创插画');
  q=page(p,scene,4,scene.stepsHeading);
  for(let i=0;i<3;i++) {
    const x=64+i*398;
    txt(q,'step-num-'+i,String(i+1).padStart(2,'0'),x,227,330,70,52,scene.accent,true);
    rect(q,'step-line-'+i,x,290,348,3,i===1?c.sky:scene.accent);
    txt(q,'step-title-'+i,scene.steps[i],x,306,348,200,28);
  }
  txt(q,'process-note',scene.stepNote,64,556,1148,52,22,c.muted);
  q=page(p,scene,5,scene.questionHeading);
  rect(q,'question-mark-line',64,231,5,211,c.sky);
  txt(q,'question-body',scene.question,72,236,768,210,34,scene.accent,true);
  txt(q,'question-hint',scene.hint,76,496,754,94,24,c.muted);
  picture(q,questionArt,874,230,331,369,'思考中的科创机器人');
  q=page(p,scene,6,scene.compareHeading);
  rect(q,'left-rule',64,217,526,4,scene.accent);
  rect(q,'right-rule',692,217,526,4,c.sky);
  txt(q,'left-heading',scene.left,64,230,526,58,30,scene.accent,true);
  txt(q,'right-heading',scene.right,692,230,526,58,30,scene.accent,true);
  rect(q,'compare-separator',640,230,1,361,c.line);
  txt(q,'left-body',scene.leftBody,64,308,526,280,26);
  txt(q,'right-body',scene.rightBody,692,308,526,280,26);
  q=page(p,scene,7,scene.tableHeading);
  const table=q.tables.add({name:'content-table',rows:4,columns:3,left:64,top:228,width:1152,height:356,columnWidths:[192,704,256],values:scene.table});
  table.borders.assign({style:'solid',fill:c.line,width:1});
  for(let r=0;r<4;r++) for(let column=0;column<3;column++) {
    const cell=table.getCell(r,column);
    cell.fill=r===0?scene.accent:r%2===0?scene.tint:'#FFFFFF';
    cell.text.style={typeface:font,fontSize:r===0?26:25,color:r===0?'#FFFFFF':c.ink,bold:r===0,verticalAlignment:'middle',insets:16};
  }
  txt(q,'table-note',scene.note,64,603,1152,36,18,c.muted);
  q=page(p,scene,8,scene.closing);
  txt(q,'summary-body',scene.summary,72,246,790,290,30,c.ink);
  txt(q,'closing-note',scene.note,76,575,790,50,20,c.muted);
  rect(q,'closing-panel',937,236,278,339,scene.tint);
  picture(q,logo,983,327,188,110,'博研文化标志');
  txt(q,'closing-brand','博研文化',984,466,202,40,29,c.navy,true);
  const candidate=path.join(build,key+'-candidate.pptx');
  await (await PresentationFile.exportPptx(p)).save(candidate);
  const finalPath=path.join(build,'validated',key+'.pptx');
  await finalizePresentation({workspaceDir:root,candidatePath:candidate,finalPath,pythonExecutable:python,integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-heading-fit','--require-native-table-slide','7'],requiredNativeTableOwnerSlides:[7],fontPolicy:{basis:'design',families:[font]},verifyArtifactToolImport:true,receiptPath:path.join(build,key+'-validation.json')});
  const data=await fs.readFile(finalPath);
  await fs.writeFile(path.join(out,key+'.pptx'),data);
  templates.push({id:key+'-pptx',scene:key,format:'pptx',file:key+'.pptx',sha256:crypto.createHash('sha256').update(data).digest('hex')});
  console.log(JSON.stringify({scene:key,slides:p.slides.items.length,sha256:templates.at(-1).sha256}));
}
await fs.writeFile(path.join(out,'manifest.json'),JSON.stringify({schema_version:1,version:'boyan-v2',brand_id:'boyan-culture',brand_name:'博研文化',font_family:font,font_fallback:'Noto Sans CJK SC',templates},null,2)+'\n');
