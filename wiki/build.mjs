import {readFileSync,writeFileSync,mkdirSync,readdirSync,copyFileSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {execFileSync} from 'node:child_process';
import {marked} from 'marked';
import hljs from 'highlight.js';

const root=resolve(import.meta.dirname,'..');
const out=join(root,'wiki');
const book=join(root,'book');
const chapterDir=join(book,'chapters');
const diagramDir=join(book,'diagrams');
const title='OpenSandbox 架构解剖';
const subtitle='从一次创建请求到沙箱里的进程';
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const slug=s=>String(s).toLowerCase().replace(/[^\p{Letter}\p{Number}]+/gu,'-').replace(/^-|-$/g,'');
mkdirSync(join(out,'chapters'),{recursive:true});
mkdirSync(join(out,'assets','diagrams'),{recursive:true});
const diagramFiles=readdirSync(diagramDir).filter(f=>f.endsWith('.svg'));
for(const f of diagramFiles) copyFileSync(join(diagramDir,f),join(out,'assets','diagrams',f));

// 附录 C 由 examples/ 同步生成，避免代码与书稿各写一份。
const codeFiles=['mini_osb.py','test_mini_osb.py'];
let appendix='# 附录 C：配套教学实现\n\n以下代码由构建器从 `examples/` 同步。它是一个标准库写的迷你沙箱平台：生命周期服务、两个运行时后端、带 `/proxy/{port}` 的 execd 替身、egress 策略求值、暂停与恢复。用它验证本书的控制面/数据面分工，而不是当成 OpenSandbox 的替代实现。\n';
for(const name of codeFiles) appendix+=`\n## ${name}\n\n\`\`\`${name.endsWith('.py')?'python':'text'}\n${readFileSync(join(root,'examples',name),'utf8')}\`\`\`\n`;
writeFileSync(join(chapterDir,'appendix-code.md'),appendix);

const files=readdirSync(chapterDir).filter(f=>f.endsWith('.md')).sort((a,b)=>{
 const order=['appendix-map.md','appendix-sources.md','appendix-code.md'];
 return (a.startsWith('appendix')?100+order.indexOf(a):parseInt(a,10))-(b.startsWith('appendix')?100+order.indexOf(b):parseInt(b,10));
});
const entries=files.map(file=>{const md=readFileSync(join(chapterDir,file),'utf8');return {slug:file.slice(0,-3),title:md.match(/^# (.+)$/m)[1],md};});
writeFileSync(join(book,'SUMMARY.md'),'# 目录\n\n'+entries.map((e,i)=>`${i+1}. [${e.title}](chapters/${e.slug}.md)`).join('\n')+'\n');

marked.use({gfm:true,renderer:{
 heading({tokens,depth,text}){return `<h${depth} id="${slug(text)}">${this.parser.parseInline(tokens)}</h${depth}>`;},
 code({text,lang}){const language=(lang||'').split(/\s/)[0];return `<div class="table-wrap"><pre tabindex="0"><code class="hljs">${hljs.getLanguage(language)?hljs.highlight(text,{language}).value:esc(text)}</code></pre></div>`;}
}});

function diagramFigure(name,caption,mode){
 const raw=readFileSync(join(diagramDir,name+'.svg'),'utf8');
 const vb=(raw.match(/viewBox="([^"]+)"/)||[,'0 0 1000 700'])[1].trim().split(/[\s,]+/).map(Number);
 const w=Math.round(vb[2]), h=Math.round(vb[3]);
 const src=mode==='single'?'data:image/svg+xml;base64,'+Buffer.from(raw).toString('base64'):`../assets/diagrams/${name}.svg`;
 return `<figure class="diagram"><img src="${src}" alt="${esc(caption)}" width="${w}" height="${h}"><figcaption>${esc(caption)}</figcaption></figure>`;
}

function convert(md,mode){
 md=md.replace(/\{\{diagram:([a-z0-9-]+)\|([^}]*)\}\}/g,(_m,name,caption)=>diagramFigure(name,caption,mode));
 md=md.replace(/\]\(([^)]+)\.md(#[^)]*)?\)/g,(match,p,hash='')=>/^[a-z]+:\/\//i.test(p)?match:`](${mode==='single'?'#'+p.split('/').pop():p+'.html'+hash})`);
 let result=marked.parse(md);
 if(mode==='single'){
  result=result.replace(/id="([^"]+)"/g,(_m,id)=>`id="${currentSlug}-${id}"`);
 }
 return result;
}
let currentSlug='';
const css=readFileSync(join(out,'assets/wiki.css'),'utf8');
const js=readFileSync(join(out,'assets/wiki.js'),'utf8');
function shell(pageTitle,body,depth='',inline=false){
 return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="基于 OpenSandbox 源码与官方文档编写的中文架构电子书"><title>${esc(pageTitle)} · ${title}</title>${inline?`<style>${css}</style>`:`<link rel="stylesheet" href="${depth}assets/wiki.css">`}</head><body><a class="skip" href="#main">跳到正文</a><header class="topbar"><a class="brand" href="${inline?'#contents':depth+'index.html'}">${title}</a><span class="spacer"></span><a class="control" href="${inline?'#book-search':depth+'search.html'}">搜索</a><button class="control" data-size="-1" aria-label="减小字号">A−</button><button class="control" data-size="1" aria-label="增大字号">A＋</button></header>${body}${inline?`<script>${js}</script>`:`<script src="${depth}assets/wiki.js"></script>`}</body></html>`;
}
const rows=(single=false)=>entries.map(e=>`<a class="chapter-row" href="${single?'#'+e.slug:'chapters/'+e.slug+'.html'}"><span class="num">${e.slug.startsWith('appendix')?'附':e.slug.slice(0,2)}</span><span>${esc(e.title)}</span><span class="arrow" aria-hidden="true">→</span></a>`).join('');
const introduction=`<h1 class="home-title">${title}</h1><p class="deck">${subtitle}</p><p class="deck">从一次 <code>POST /v1/sandboxes</code> 出发，沿着控制面、运行时后端、execd 数据面、入向与出向网络，一路读到沙箱里的那个进程。</p><p class="meta">20 篇正文 · 3 份附录 · 上游快照 c2f55b20e945<br>源码阅读与官方文档核对，未做生产压测</p>`;
const home=`<main id="main" class="book">${introduction}<section class="resume" data-resume hidden><p class="meta">上次读到</p><a href="#">继续阅读</a></section><nav class="chapters" aria-label="全书目录">${rows()}</nav><p class="source-note">下载：<a href="opensandbox-architecture.epub">EPUB</a> · <a href="book.html">单文件离线阅读</a> · <a href="https://github.com/StarDust0083-GP03/opensandbox-architecture-ebook/blob/main/book/OpenSandbox%E6%9E%B6%E6%9E%84%E8%A7%A3%E5%89%96.md">合并书稿</a></p><p class="source-note">本书按 Apache-2.0 上游仓库的公开代码与文档独立撰写，不复制其文档正文；引用处给出提交固定的路径。静态阅读不等于安全审计或性能承诺。</p></main>`;
writeFileSync(join(out,'index.html'),shell('目录',home));

const searchDocs=[],epubChapters=[];
for(let i=0;i<entries.length;i++){
 const e=entries[i]; currentSlug=e.slug;
 const html=convert(e.md,'site');
 const headings=[...e.md.replace(/```[\s\S]*?```/g,'').matchAll(/^## (.+)$/gm)].map(m=>m[1]);
 const toc=`<details class="toc"><summary>本篇目录</summary><ol>${headings.map(h=>`<li><a href="#${slug(h)}">${esc(h)}</a></li>`).join('')}</ol></details>`;
 const prev=entries[i-1],next=entries[i+1];
 const pager=`<nav class="pager" aria-label="篇章导航">${prev?`<a href="${prev.slug}.html"><small>上一篇</small>${esc(prev.title)}</a>`:'<span></span>'}${next?`<a href="${next.slug}.html"><small>下一篇</small>${esc(next.title)}</a>`:'<a href="../index.html">返回目录</a>'}</nav>`;
 const body=`<main id="main" class="book" data-chapter="${e.slug}">${toc}<article class="content">${html}</article>${pager}</main>`;
 writeFileSync(join(out,'chapters',e.slug+'.html'),shell(e.title,body,'../'));
 const text=e.md.replace(/```[\s\S]*?```/g,' ').replace(/<[^>]*>/g,' ').replace(/!?(\[[^\]]*\])\([^)]*\)/g,'$1').replace(/[#*`>|\[\]]/g,' ').replace(/\s+/g,' ').trim();
 searchDocs.push({title:e.title,slug:e.slug,text});
 epubChapters.push({title:e.title,slug:e.slug,html});
}
const data=JSON.stringify(searchDocs).replace(/</g,'\\u003c');
writeFileSync(join(out,'assets','search-index.json'),JSON.stringify(searchDocs));
function searchUI(single=false){return `<section id="book-search" class="search"><h2>全书搜索</h2><label for="query">输入概念、组件或函数名</label><input id="query" class="search-input" type="search" placeholder="比如：SandboxService、nftables、Fastlet、pause"><p id="search-status" class="meta" aria-live="polite">输入关键词，搜索标题与正文。</p><div id="results"></div></section><script>(()=>{const docs=${data};const input=document.querySelector('#query'),out=document.querySelector('#results'),status=document.querySelector('#search-status');input.addEventListener('input',()=>{const q=input.value.trim().toLowerCase();out.replaceChildren();if(!q){status.textContent='输入关键词，搜索标题与正文。';return}const hits=docs.filter(d=>(d.title+' '+d.text).toLowerCase().includes(q));status.textContent='找到 '+hits.length+' 章';for(const d of hits){const row=document.createElement('article');row.className='result';const a=document.createElement('a');a.href=${single?"'#'+d.slug":"'chapters/'+d.slug+'.html'"};a.textContent=d.title;const p=document.createElement('p');const pos=d.text.toLowerCase().indexOf(q);p.textContent=d.text.slice(Math.max(0,pos-40),Math.max(0,pos-40)+180);row.append(a,p);out.append(row)}})})();</script>`;}
writeFileSync(join(out,'search.html'),shell('搜索',`<main id="main">${searchUI()}</main>`));

let single=`<main id="main"><section class="book" id="contents">${introduction}<nav class="chapters" aria-label="全书目录">${rows(true)}</nav></section>${searchUI(true)}`;
for(const e of entries){currentSlug=e.slug;single+=`<section class="book single-chapter" id="${e.slug}"><article class="content">${convert(e.md,'single')}</article><p><a href="#contents">返回全书目录</a></p></section>`;}
single+='</main>';
writeFileSync(join(out,'book.html'),shell('离线全书',single,'',true));

writeFileSync(join(book,'OpenSandbox架构解剖.md'),`# ${title}\n\n${subtitle}\n\n`+entries.map(e=>e.md.replace(/\{\{diagram:([a-z0-9-]+)\|([^}]*)\}\}/g,(_m,name,caption)=>`![${caption}](diagrams/${name}.svg)`).replace(/\]\(([^)]+)\.md(#[^)]*)?\)/g,(match,p)=>/^[a-z]+:\/\//i.test(p)?match:`](#${slug(entries.find(x=>x.slug===p)?.title||p)})`)).join('\n\n---\n\n'));
writeFileSync(join(out,'epub-input.json'),JSON.stringify({title,subtitle,chapters:epubChapters},null,2));
execFileSync('python3',[join(out,'export_epub.py')],{stdio:'inherit',cwd:root});
copyFileSync(join(root,'exports/opensandbox-architecture.epub'),join(out,'opensandbox-architecture.epub'));
console.log(`Built ${entries.length} chapters, offline HTML, merged Markdown and EPUB.`);
