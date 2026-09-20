(() => {
  const root=document.documentElement;
  if(window.self!==window.top) root.classList.add('embedded');

  const input=document.querySelector('#docs-search');
  const count=document.querySelector('[data-search-count]');
  const noResults=document.querySelector('[data-no-results]');
  const sections=[...document.querySelectorAll('.searchable-section')];
  const settings=[...document.querySelectorAll('.setting')];

  function normalize(v){return (v||'').toLowerCase().trim();}
  function runSearch(){
    const q=normalize(input?.value);
    let visibleSettings=0;
    sections.forEach(section=>{
      const title=normalize(section.dataset.sectionTitle);
      const sectionMatch=q && title.includes(q);
      const local=[...section.querySelectorAll('.setting')];
      if(local.length){
        let localVisible=0;
        local.forEach(item=>{
          const hit=!q || sectionMatch || normalize(item.dataset.search).includes(q);
          item.classList.toggle('search-hidden',!hit);
          item.classList.toggle('search-hit',Boolean(q&&hit));
          if(hit){localVisible++; visibleSettings++;}
        });
        section.classList.toggle('search-hidden',Boolean(q)&&localVisible===0&&!sectionMatch);
      }else{
        const text=normalize(section.textContent);
        const hit=!q || text.includes(q);
        section.classList.toggle('search-hidden',!hit);
      }
    });
    const visibleSections=sections.filter(s=>!s.classList.contains('search-hidden')).length;
    if(count) count.textContent=q ? `${visibleSettings} settings · ${visibleSections} sections` : '';
    if(noResults) noResults.hidden=!q || visibleSections>0;
  }
  input?.addEventListener('input',runSearch);
  document.addEventListener('keydown',event=>{
    if(event.key==='/' && document.activeElement!==input){
      event.preventDefault(); input?.focus(); input?.select();
    } else if(event.key==='Escape' && document.activeElement===input){
      input.value=''; input.blur(); runSearch();
    }
  });

  const nav=document.querySelector('[data-docs-nav]');
  document.querySelector('[data-mobile-nav]')?.addEventListener('click',()=>nav?.classList.toggle('open'));
  nav?.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>nav.classList.remove('open')));

  const links=new Map([...document.querySelectorAll('[data-nav]')].map(a=>[a.dataset.nav,a]));
  const observer=new IntersectionObserver(entries=>{
    const visible=entries.filter(e=>e.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top);
    if(!visible.length) return;
    links.forEach(a=>a.classList.remove('active'));
    links.get(visible[0].target.id)?.classList.add('active');
  },{rootMargin:'-118px 0px -70% 0px',threshold:[0,1]});
  sections.forEach(section=>observer.observe(section));
})();