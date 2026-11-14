CLICK = """((t, sel) => { const els=[...document.querySelectorAll(sel||'button,[role=tab],a,label')];
 const e = els.find(x => x.textContent.trim() === t) || els.find(x => x.textContent.trim().startsWith(t)) || els.find(x => x.textContent.includes(t));
 if(!e) return 'NOTFOUND:'+t; e.scrollIntoView({block:'center',behavior:'instant'}); e.click(); return 'ok:'+e.textContent.trim().slice(0,40); })"""
def click(b, text, sel=None):
    import json
    return b.js(f"{CLICK}({json.dumps(text)}, {json.dumps(sel)})")
