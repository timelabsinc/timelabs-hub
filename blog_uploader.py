#!/usr/bin/env python3
"""Blog uploader — Shopify tool. Renders /var/www/ops/blog-uploader.html.

Writes posts straight into the store: pick a blog, write the post in the shared
rich-text editor, save it as a draft or publish it. Existing posts open in the
same editor, so this is also how you finish the four drafts already sitting
unpublished on the store.

Distinct from Blog builder (/ops/blog.html), which is the *topic plan* — that
one is read-only and just shows the 27-topic queue. This is the thing that
actually puts words on the storefront.
"""
import os
import sys

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, _appnav, hub_header, hub_footer, WHOAMI_JS, RTE_JS

OUT = "/var/www/ops/blog-uploader.html"


def build():
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Blog uploader — Labs OS</title>
<style>{HUB_STYLE}
  .bu-grid{{display:grid;grid-template-columns:270px 1fr;gap:18px;align-items:start;}}
  @media(max-width:860px){{.bu-grid{{grid-template-columns:1fr;}}}}
  .bu-card{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
    box-shadow:var(--shadow);overflow:hidden;}}
  .bu-head{{display:flex;align-items:center;gap:8px;padding:11px 13px;border-bottom:1px solid var(--border);}}
  .bu-head b{{font-size:13.5px;flex:1;}}
  .bu-new{{font-size:12.5px;font-weight:650;border:none;background:var(--ink);color:var(--bg);
    border-radius:7px;padding:6px 12px;cursor:pointer;}}
  .bu-list{{max-height:520px;overflow-y:auto;}}
  .bu-item{{padding:10px 13px;border-bottom:1px solid var(--border);cursor:pointer;}}
  .bu-item:last-child{{border-bottom:none;}}
  .bu-item:hover{{background:var(--card-2);}}
  .bu-item.on{{background:var(--accent-bg);}}
  .bu-item b{{display:block;font-size:13px;color:var(--ink);line-height:1.35;}}
  .bu-item small{{font-size:11.5px;color:var(--muted);}}
  .st{{display:inline-block;font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
    padding:1px 6px;border-radius:4px;margin-right:5px;}}
  .st.live{{color:var(--good);background:var(--good-bg);}}
  .st.draft{{color:var(--accent);background:var(--accent-bg);}}
  .field{{margin-bottom:13px;}}
  .field label{{display:block;font-size:11.5px;font-weight:650;color:var(--muted);
    text-transform:uppercase;letter-spacing:.04em;margin-bottom:5px;}}
  .pin{{width:100%;font-size:14px;border:1px solid var(--border);border-radius:var(--r-s);
    background:var(--bg);color:var(--ink);padding:10px 12px;font-family:inherit;}}
  .pin:focus{{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}}
  textarea.pin{{min-height:64px;resize:vertical;line-height:1.5;}}
  .row2{{display:grid;grid-template-columns:1fr 1fr;gap:12px;}}
  @media(max-width:560px){{.row2{{grid-template-columns:1fr;}}}}
  .seg{{display:inline-flex;border:1px solid var(--border);border-radius:var(--r-s);overflow:hidden;}}
  .seg button{{border:none;background:var(--card);color:var(--muted);font:inherit;font-size:13px;
    font-weight:600;padding:9px 15px;cursor:pointer;}}
  .seg button.active{{background:var(--ink);color:var(--bg);}}
  .seg button+button{{border-left:1px solid var(--border);}}
  .acts{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:14px;}}
  .btn{{font-size:13.5px;font-weight:650;border-radius:var(--r-s);padding:11px 18px;cursor:pointer;
    border:1px solid var(--border);background:var(--card);color:var(--ink);text-decoration:none;
    display:inline-flex;align-items:center;gap:7px;}}
  .btn:hover{{border-color:var(--border-2);text-decoration:none;}}
  .btn.primary{{background:var(--ink);color:var(--bg);border-color:var(--ink);}}
  .btn[disabled]{{opacity:.5;cursor:not-allowed;}}
  .hint{{font-size:12px;color:var(--muted);margin-top:6px;line-height:1.5;}}
  .empty2{{padding:22px;text-align:center;color:var(--muted);font-size:13.5px;}}
</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">Blog uploader</h1>
      <p class="page-sub">Write a post and put it on the store — save it as a draft or publish it live. Existing posts open here too, so you can finish the drafts already sitting on the store.</p>
    </div>
    <div class="bu-grid">
      <div class="bu-card">
        <div class="bu-head"><b>Posts</b><button class="bu-new" id="newBtn">New</button></div>
        <div class="field" style="padding:11px 13px 0;margin:0">
          <select id="blogSel" class="pin"></select>
        </div>
        <div class="bu-list" id="list"><div class="empty2">Loading…</div></div>
      </div>
      <div class="bu-card" style="padding:18px" id="editor"><div class="empty2">Loading…</div></div>
    </div>
    {hub_footer()}
  </main>
</div>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>{RTE_JS}</script>
<script>
'use strict';
var API='/ops/agent/api';
function $(id){{return document.getElementById(id);}}
function esc(s){{var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}}
var toastT;
function toast(m){{var t=$('toast');t.textContent=m;t.classList.add('show');clearTimeout(toastT);toastT=setTimeout(function(){{t.classList.remove('show');}},3000);}}
async function api(p,o){{
  var r=await fetch(API+p,o);
  if(r.status===403){{location.href='/oauth2/start?rd=/ops/blog-uploader.html';throw new Error('auth');}}
  var d=await r.json().catch(function(){{return {{}};}});
  if(!r.ok)throw new Error(d.error||'failed');
  return d;
}}
var BLOGS=[], current=null, bodyRTE=null, published=false;

async function boot(){{
  try{{
    var b=await api('/shopify/blogs'); BLOGS=b.blogs||[];
    $('blogSel').innerHTML='<option value="">All blogs</option>'+
      BLOGS.map(function(x){{return '<option value="'+esc(x.id)+'">'+esc(x.title)+'</option>';}}).join('');
    $('blogSel').onchange=loadList;
    $('newBtn').onclick=function(){{ openEditor(null); }};
    await loadList();
    openEditor(null);
  }}catch(e){{ if(e.message!=='auth') $('editor').innerHTML='<div class="empty2">'+esc(e.message)+'</div>'; }}
}}

async function loadList(){{
  $('list').innerHTML='<div class="empty2">Loading…</div>';
  try{{
    var sel=$('blogSel').value;
    var d=await api('/shopify/articles'+(sel?'?blog='+encodeURIComponent(sel):''));
    if(!d.articles.length){{ $('list').innerHTML='<div class="empty2">No posts yet.</div>'; return; }}
    $('list').innerHTML=d.articles.map(function(a){{
      return '<div class="bu-item" data-id="'+esc(a.id)+'">'+
        '<b>'+esc(a.title)+'</b><small><span class="st '+(a.published?'live':'draft')+'">'+
        (a.published?'live':'draft')+'</span>'+esc(a.author||'')+'</small></div>';
    }}).join('');
    $('list').querySelectorAll('.bu-item').forEach(function(el){{
      el.onclick=function(){{ openEditor(el.dataset.id); }};
    }});
  }}catch(e){{ if(e.message!=='auth') $('list').innerHTML='<div class="empty2">'+esc(e.message)+'</div>'; }}
}}

function editorShell(a){{
  var blogOpts=BLOGS.map(function(b){{
    var sel=(a&&a.blog_id===b.id)?' selected':'';
    return '<option value="'+esc(b.id)+'"'+sel+'>'+esc(b.title)+'</option>';
  }}).join('');
  $('editor').innerHTML=
    '<div class="field"><label>Title</label><input id="f-title" class="pin" placeholder="What the post is called"></div>'+
    '<div class="row2">'+
      '<div class="field"><label>Blog</label><select id="f-blog" class="pin"'+(a?' disabled':'')+'>'+blogOpts+'</select></div>'+
      '<div class="field"><label>Author</label><input id="f-author" class="pin" placeholder="Timelabs"></div>'+
    '</div>'+
    '<div class="field"><label>Summary <small style="text-transform:none;font-weight:400">(excerpt shown in listings)</small></label>'+
      '<textarea id="f-summary" class="pin"></textarea></div>'+
    '<div class="field"><label>Post</label><div id="f-body"></div></div>'+
    '<div class="row2">'+
      '<div class="field"><label>Tags <small style="text-transform:none;font-weight:400">(comma-separated)</small></label>'+
        '<input id="f-tags" class="pin" placeholder="seiko mod, nh35, guide"></div>'+
      '<div class="field"><label>Cover image URL</label><input id="f-image" class="pin" placeholder="https://…"></div>'+
    '</div>'+
    '<div class="field"><label>Status</label><div class="seg" id="pubseg">'+
      '<button data-p="0">Draft</button><button data-p="1">Published</button></div>'+
      '<div class="hint">Draft saves to Shopify but stays hidden from readers until you publish.</div></div>'+
    '<div class="acts"><button class="btn primary" id="save">Save</button>'+
      '<span id="viewwrap"></span></div>';
  bodyRTE=LabsRTE.mount({{mount:'f-body', html:(a&&a.body)||'',
    placeholder:'Write the post — headings, lists and links all work.'}});
  $('pubseg').querySelectorAll('button').forEach(function(b){{
    b.onclick=function(){{
      $('pubseg').querySelectorAll('button').forEach(function(x){{x.classList.remove('active');}});
      b.classList.add('active'); published=b.dataset.p==='1'; updateSave();
    }};
  }});
  $('save').onclick=save;
}}
function updateSave(){{ $('save').textContent = published ? 'Save & publish' : 'Save draft'; }}

async function openEditor(id){{
  $('list').querySelectorAll('.bu-item').forEach(function(x){{x.classList.toggle('on', x.dataset.id===id);}});
  if(!id){{
    current=null; published=false; editorShell(null);
    $('pubseg').querySelector('[data-p="0"]').classList.add('active'); updateSave(); return;
  }}
  $('editor').innerHTML='<div class="empty2">Loading post…</div>';
  try{{
    var a=await api('/shopify/article/detail?id='+encodeURIComponent(id));
    current=a; published=!!a.published;
    editorShell(a);
    $('f-title').value=a.title||''; $('f-author').value=a.author||'';
    $('f-summary').value=a.summary||''; $('f-tags').value=(a.tags||[]).join(', ');
    $('f-image').value=a.image||'';
    $('pubseg').querySelector('[data-p="'+(published?1:0)+'"]').classList.add('active');
    updateSave();
    if(a.handle){{
      var blog=BLOGS.filter(function(b){{return b.id===a.blog_id;}})[0];
      if(blog) $('viewwrap').innerHTML='<a class="btn" target="_blank" rel="noopener" href="https://timelabsco.in/blogs/'+
        esc(blog.handle)+'/'+esc(a.handle)+'">View on store</a>';
    }}
  }}catch(e){{ if(e.message!=='auth') $('editor').innerHTML='<div class="empty2">'+esc(e.message)+'</div>'; }}
}}

async function save(){{
  var title=($('f-title').value||'').trim();
  if(!title){{ toast('Give the post a title'); return; }}
  var body=bodyRTE?bodyRTE.getHTML():'';
  var payload={{title:title, body:body, summary:$('f-summary').value,
    tags:$('f-tags').value, author:$('f-author').value, published:published,
    image:($('f-image').value||'').trim()}};
  if(current) payload.id=current.id; else payload.blog_id=$('f-blog').value;
  if(!current && !payload.blog_id){{ toast('Pick a blog'); return; }}
  if(published && !confirm('Publish "'+title+'" live on the store?')) return;
  var b=$('save'); b.disabled=true; var lbl=b.textContent; b.textContent='Saving…';
  try{{
    var d=await api('/shopify/article/save',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});
    toast('Post '+d.action+(published?' and published':' as draft'));
    await loadList();
    openEditor(d.article&&d.article.id?d.article.id:null);
  }}catch(e){{ toast(e.message); }}
  b.disabled=false; b.textContent=lbl;
}}
boot();
{WHOAMI_JS}
</script>
</body></html>"""
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        f.write(doc)
    os.replace(tmp, OUT)
    os.system(f"chown www-data:www-data {OUT}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
