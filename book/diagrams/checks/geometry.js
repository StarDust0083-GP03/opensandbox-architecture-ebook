(() => {
 const root=document.querySelector('#stage svg');
 if(!root) return JSON.stringify({error:'no svg'});
 const rr=root.getBoundingClientRect();
 const out=[];
 const inside=(b,r,m)=> b.left>=r.left-m && b.top>=r.top-m && b.right<=r.right+m && b.bottom<=r.bottom+m;
 const texts=[...root.querySelectorAll('text')].filter(t=>{const b=t.getBoundingClientRect();return b.width>0&&b.height>0;});
 for(const t of texts){
   const b=t.getBoundingClientRect();
   if(!inside(b,rr,2)) out.push({t:'out-of-canvas',txt:t.textContent.slice(0,26),box:[b.left-rr.left,b.top-rr.top,b.width,b.height].map(Math.round)});
 }
 const nodes=[...root.querySelectorAll('g.node, g[data-box]')].map(g=>({g,rect:g.querySelector('rect')})).filter(o=>o.rect);
 const nodeRects=nodes.map(o=>({id:o.g.id,r:o.rect.getBoundingClientRect()}));
 for(const o of nodes){
   const r=o.rect.getBoundingClientRect();
   for(const t of o.g.querySelectorAll('text')){
     const b=t.getBoundingClientRect(); if(b.width===0) continue;
     if(!inside(b,r,2)) out.push({t:'label-outside-node',node:o.g.id,txt:t.textContent.slice(0,26)});
   }
 }
 for(const el of root.querySelectorAll('g.edgeLabel')){
   const b=el.getBoundingClientRect(); if(b.width===0) continue;
   for(const n of nodeRects){
     if(inside(b,n.r,0)) out.push({t:'edgeLabel-overlaps-node',edge:el.textContent.trim().slice(0,24),node:n.id});
   }
 }
 for(let i=0;i<nodeRects.length;i++)for(let j=i+1;j<nodeRects.length;j++){
   const a=nodeRects[i].r,b=nodeRects[j].r;
   if(a.width&&b.width&&a.left<b.right-1&&b.left<a.right-1&&a.top<b.bottom-1&&b.top<a.bottom-1)
     out.push({t:'node-overlap',a:nodeRects[i].id,b:nodeRects[j].id});
 }
 return JSON.stringify({canvas:[Math.round(rr.width),Math.round(rr.height)],nodes:nodeRects.length,texts:texts.length,issues:out.length,detail:out.slice(0,10)});
})()
