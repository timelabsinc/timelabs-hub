#!/usr/bin/env python3
"""Labs Command — the primary Hermes workspace.

This is deliberately additive while the interface proves itself: the existing
/ops/agent/ chat remains available as a fallback. The page reuses the current
role-checked chat, upload, session and event endpoints; no second agent backend
or parallel source of business truth is introduced.
"""
import os
import time

from hub_shell import HUB_STYLE, WHOAMI_JS, hub_footer, hub_header

OUT = "/var/www/ops/command.html"


def build():
    generated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    doc = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Command — Labs OS</title>
<style>""" + HUB_STYLE + r"""
html,body{height:100%;overflow:hidden}.wrap{height:100%;display:flex;flex-direction:column}
.appnav{flex:none}.cmd{flex:1;min-height:0;display:grid;grid-template-columns:250px minmax(0,1fr) 290px;
  border-top:1px solid var(--border);background:var(--bg)}
.rail{min-width:0;background:var(--card);display:flex;flex-direction:column;overflow:hidden}
.rail.left{border-right:1px solid var(--border)}.rail.right{border-left:1px solid var(--border)}
.rail-head{height:56px;flex:none;display:flex;align-items:center;justify-content:space-between;padding:0 15px;
  border-bottom:1px solid var(--border)}
.rail-title{font-size:11px;font-weight:750;letter-spacing:.09em;text-transform:uppercase;color:var(--muted)}
.new-btn,.icon-btn{border:1px solid var(--border);background:var(--card);color:var(--ink);cursor:pointer;
  border-radius:9px;font:inherit}.new-btn{padding:7px 10px;font-size:12px;font-weight:700}
.new-btn:hover,.icon-btn:hover{border-color:var(--accent);background:var(--accent-bg)}
.session-list{padding:8px;overflow:auto;flex:1}.session{width:100%;display:block;text-align:left;border:1px solid transparent;
  background:transparent;color:var(--ink);border-radius:9px;padding:10px;cursor:pointer;margin-bottom:3px}
.session:hover{background:var(--card-2)}.session.on{background:var(--accent-bg);border-color:var(--accent)}
.session b{display:block;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.session span{display:block;font-size:11px;color:var(--muted);margin-top:4px}
.rail-foot{padding:10px;border-top:1px solid var(--border)}
.fallback{display:block;text-align:center;color:var(--muted);font-size:11px;text-decoration:none;padding:7px}
.fallback:hover{color:var(--accent)}
.workspace{min-width:0;display:flex;flex-direction:column;background:var(--bg)}
.work-head{height:56px;flex:none;display:flex;align-items:center;gap:10px;padding:0 16px;border-bottom:1px solid var(--border);
  background:color-mix(in srgb,var(--bg) 88%,transparent)}
.work-title{min-width:0;flex:1}.work-title b{font-size:14px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.work-title span{font-size:11px;color:var(--muted)}
.status{display:inline-flex;align-items:center;gap:6px;color:var(--good);font-size:11px;font-weight:650}
.status i{width:7px;height:7px;border-radius:50%;background:currentColor}
.mobile-toggle{display:none;width:36px;height:36px}
.thread{flex:1;min-height:0;overflow:auto;padding:24px clamp(14px,4vw,48px);scroll-behavior:smooth}
.empty{max-width:720px;margin:8vh auto 0}.empty-mark{width:48px;height:48px;border-radius:13px;
  background:linear-gradient(135deg,var(--accent),#d8a94c);display:grid;place-items:center;color:white;
  font-size:20px;font-weight:850;box-shadow:var(--shadow-lg)}
.empty h1{font-size:clamp(25px,3vw,38px);letter-spacing:-.035em;margin:18px 0 8px;color:var(--ink)}
.empty>p{color:var(--muted);font-size:14px;line-height:1.6;max-width:580px}
.starts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:24px}
.start{padding:13px;text-align:left;border:1px solid var(--border);border-radius:11px;background:var(--card);
  color:var(--ink);font:inherit;font-size:13px;line-height:1.35;cursor:pointer}
.start small{display:block;color:var(--muted);margin-top:5px}.start:hover{border-color:var(--accent);transform:translateY(-1px)}
.msg{max-width:820px;margin:0 auto 18px}.msg-label{font-size:10px;font-weight:750;letter-spacing:.08em;
  text-transform:uppercase;color:var(--muted);margin:0 0 6px 2px}
.msg.user .bubble{margin-left:auto;max-width:78%;background:var(--ink);color:var(--bg);border-radius:14px 14px 4px 14px;
  padding:11px 14px;white-space:pre-wrap;font-size:14px;line-height:1.5}
.msg.agent .bubble{background:var(--card);border:1px solid var(--border);border-radius:4px 14px 14px 14px;
  padding:16px 18px;color:var(--ink);font-size:14px;line-height:1.6;box-shadow:var(--shadow)}
.md h1,.md h2,.md h3{margin:12px 0 6px;line-height:1.25}.md h1{font-size:20px}.md h2{font-size:17px}.md h3{font-size:15px}
.md p{margin:7px 0}.md ul,.md ol{margin:7px 0;padding-left:20px}.md pre{overflow:auto;background:var(--bg);
  border:1px solid var(--border);padding:10px;border-radius:8px}.md code{font-size:.9em;background:var(--bg);padding:1px 4px;border-radius:4px}
.md table{display:block;overflow:auto;border-collapse:collapse;margin:10px 0}.md th,.md td{border:1px solid var(--border);padding:6px 9px;text-align:left}
.md a{color:var(--accent)}.msg-tools{display:flex;gap:5px;margin-top:10px;padding-top:9px;border-top:1px solid var(--border)}
.msg-tools button{border:0;background:none;color:var(--muted);font:inherit;font-size:11px;cursor:pointer;padding:3px 6px}
.msg-tools button:hover{color:var(--accent)}
.thinking{max-width:820px;margin:0 auto 18px;color:var(--muted);font-size:12px;display:flex;gap:8px;align-items:center}
.dots{display:flex;gap:3px}.dots i{width:6px;height:6px;background:var(--accent);border-radius:50%;animation:pulse 1s infinite}
.dots i:nth-child(2){animation-delay:.15s}.dots i:nth-child(3){animation-delay:.3s}
@keyframes pulse{50%{opacity:.25;transform:translateY(-2px)}}
.compose-wrap{flex:none;padding:10px clamp(12px,4vw,48px) calc(12px + env(safe-area-inset-bottom))}
.attachments{max-width:820px;margin:0 auto 7px;display:flex;gap:6px;flex-wrap:wrap}
.attachment{display:flex;align-items:center;gap:6px;padding:5px 7px;border:1px solid var(--border);background:var(--card);
  border-radius:8px;font-size:11px}.attachment img{width:28px;height:28px;border-radius:5px;object-fit:cover}
.attachment button{border:0;background:none;color:var(--bad);cursor:pointer}
.compose{max-width:820px;margin:0 auto;background:var(--card);border:1px solid var(--border);border-radius:14px;
  box-shadow:var(--shadow-lg);padding:8px;display:grid;grid-template-columns:auto 1fr auto;align-items:end;gap:7px}
.compose:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg),var(--shadow-lg)}
.compose textarea{border:0;outline:0;resize:none;background:transparent;color:var(--ink);font:inherit;font-size:14px;
  line-height:1.45;padding:8px 4px;min-height:38px;max-height:150px}
.compose button{width:38px;height:38px;border-radius:10px}.attach-btn{border:0;background:transparent;color:var(--muted)}
.send-btn{border:0;background:var(--ink);color:var(--bg)}.send-btn:disabled{opacity:.4}
.compose-note{max-width:820px;margin:6px auto 0;text-align:center;font-size:10.5px;color:var(--muted)}
.context{overflow:auto;padding:13px}.context-block{margin-bottom:20px}.context h3{font-size:11px;text-transform:uppercase;
  letter-spacing:.08em;color:var(--muted);margin:0 0 8px}
.mode{border:1px solid var(--accent);background:var(--accent-bg);border-radius:10px;padding:10px}
.mode b{display:block;font-size:13px}.mode span{display:block;font-size:11px;color:var(--muted);margin-top:3px;line-height:1.4}
.cap-list{display:grid;gap:5px}.cap{display:flex;align-items:center;justify-content:space-between;padding:8px 9px;
  border:1px solid var(--border);border-radius:8px;background:var(--card);font-size:12px}.cap span{color:var(--muted);font-size:10px}
.event{padding:9px 1px;border-bottom:1px solid var(--border)}.event:last-child{border-bottom:0}
.event b{font-size:11.5px;display:block}.event p{font-size:11px;color:var(--muted);line-height:1.35;margin:3px 0}
.event time{font-size:9.5px;color:var(--muted)}
.shade{display:none}
@media(max-width:1050px){.cmd{grid-template-columns:230px minmax(0,1fr)}.rail.right{display:none}}
@media(max-width:720px){
  html,body{overflow:hidden}.topbar,.appnav{display:none}.cmd{display:block;position:relative}
  .workspace{height:100%}.mobile-toggle{display:inline-grid;place-items:center}
  .rail.left{position:absolute;inset:0 auto 0 0;width:min(84vw,300px);z-index:30;transform:translateX(-101%);
    transition:transform .18s}.rail.left.open{transform:none}.shade{position:absolute;inset:0;background:rgba(0,0,0,.35);z-index:29}
  .shade.on{display:block}.thread{padding:18px 12px}.empty{margin-top:3vh}.starts{grid-template-columns:1fr}
  .compose-wrap{padding-left:8px;padding-right:8px}.msg.user .bubble{max-width:90%}
}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}.dots i{animation:none}}
</style></head><body>
<div class="wrap">""" + hub_header("chat") + r"""
<div class="cmd">
  <aside class="rail left" id="leftRail">
    <div class="rail-head"><span class="rail-title">Conversations</span><button class="new-btn" id="newBtn">+ New</button></div>
    <div class="session-list" id="sessions"></div>
    <div class="rail-foot"><a class="fallback" href="/ops/agent/">Open classic Labs Chat</a></div>
  </aside>
  <div class="shade" id="shade"></div>
  <section class="workspace">
    <div class="work-head">
      <button class="icon-btn mobile-toggle" id="menuBtn" aria-label="Show conversations">☰</button>
      <div class="work-title"><b id="threadTitle">New command</b><span>Hermes · Timelabs business operator</span></div>
      <span class="status"><i></i><span id="agentStatus">Ready</span></span>
    </div>
    <div class="thread" id="thread">
      <div class="empty" id="empty">
        <div class="empty-mark">L</div>
        <h1>What should we move forward?</h1>
        <p>Ask a question or give Hermes a job. It can inspect the business, work across Labs OS, and prepare sensitive changes for your approval.</p>
        <div class="starts">
          <button class="start" data-prompt="Give me today's operating brief: new orders, anything blocked, and the three actions that matter most."><b>Run today’s brief</b><small>Orders, blockers and priorities</small></button>
          <button class="start" data-prompt="Show me every order that needs attention and explain why."><b>Review orders</b><small>Find work that needs intervention</small></button>
          <button class="start" data-prompt="What changed across Labs OS since yesterday?"><b>What changed?</b><small>Read the business activity feed</small></button>
          <button class="start" data-prompt="Check the health of Hermes and Labs OS. Report problems only, with the safest next action."><b>Check the system</b><small>Services, jobs and integrations</small></button>
        </div>
      </div>
    </div>
    <div class="compose-wrap">
      <div class="attachments" id="attachments"></div>
      <div class="compose">
        <button class="attach-btn" id="attachBtn" aria-label="Attach photos" title="Attach photos">＋</button>
        <input id="fileInput" type="file" accept="image/jpeg,image/png,image/webp" multiple hidden>
        <textarea id="input" rows="1" placeholder="Ask, decide, or tell Hermes what to do…" aria-label="Command"></textarea>
        <button class="send-btn" id="sendBtn" aria-label="Send">↑</button>
      </div>
      <div class="compose-note">Reads run directly. Business writes remain dry-run or approval-gated.</div>
    </div>
  </section>
  <aside class="rail right">
    <div class="rail-head"><span class="rail-title">Business context</span></div>
    <div class="context">
      <div class="context-block"><h3>Active operator</h3><div class="mode"><b>Hermes</b><span>One operator across orders, store, suppliers, content, files and system operations.</span></div></div>
      <div class="context-block"><h3>Control policy</h3><div class="cap-list">
        <div class="cap">Inspect &amp; analyse <span>Direct</span></div>
        <div class="cap">Prepare changes <span>Dry run</span></div>
        <div class="cap">Sensitive actions <span>Approval</span></div>
      </div></div>
      <div class="context-block"><h3>Recent activity</h3><div id="events"><div class="event"><p>Loading activity…</p></div></div></div>
    </div>
  </aside>
</div>
""" + hub_footer("Labs Command · generated " + generated) + r"""
</div>
<script>
(function(){
const API='/ops/agent/api', threadEl=document.getElementById('thread'), empty=document.getElementById('empty');
const input=document.getElementById('input'), sendBtn=document.getElementById('sendBtn');
const statusEl=document.getElementById('agentStatus'), sessionsEl=document.getElementById('sessions');
const attachEl=document.getElementById('attachments'); let busy=false, attachments=[], lastAgentCount=0;
let sid=parseInt(localStorage.getItem('labs_command_session')||localStorage.getItem('tl_session')||'1',10);
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function inline(s){return s.replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')
 .replace(/\[([^\]]+)\]\((https?:[^)\s]+|\/[^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>')}
function md(src){let ls=esc(src).split('\n'),h='',i=0;while(i<ls.length){let l=ls[i];
 if(/^```/.test(l)){let b=[];i++;while(i<ls.length&&!/^```/.test(ls[i]))b.push(ls[i++]);i++;h+='<pre><code>'+b.join('\n')+'</code></pre>';continue}
 if(!l.trim()){i++;continue}if(/^#{1,3}\s/.test(l)){let n=l.match(/^#+/)[0].length;h+='<h'+n+'>'+inline(l.replace(/^#+\s*/,''))+'</h'+n+'>';i++;continue}
 if(/^\s*([-*]|\d+\.)\s+/.test(l)){let o=/^\s*\d+\./.test(l),a=[];while(i<ls.length&&/^\s*([-*]|\d+\.)\s+/.test(ls[i]))a.push('<li>'+inline(ls[i++].replace(/^\s*([-*]|\d+\.)\s+/,''))+'</li>');h+=(o?'<ol>':'<ul>')+a.join('')+(o?'</ol>':'</ul>');continue}
 if(/\|/.test(l)&&i+1<ls.length&&/^\s*\|?[\s:|-]+\|/.test(ls[i+1])){let row=x=>x.replace(/^\s*\|/,'').replace(/\|\s*$/,'').split('|').map(x=>inline(x.trim()));let hd=row(l);i+=2;let b=[];while(i<ls.length&&/\|/.test(ls[i]))b.push(row(ls[i++]));h+='<table><thead><tr>'+hd.map(x=>'<th>'+x+'</th>').join('')+'</tr></thead><tbody>'+b.map(r=>'<tr>'+r.map(x=>'<td>'+x+'</td>').join('')+'</tr>').join('')+'</tbody></table>';continue}
 let b=[l];i++;while(i<ls.length&&ls[i].trim()&&!/^(#{1,3}\s|```|\s*([-*]|\d+\.)\s)/.test(ls[i]))b.push(ls[i++]);h+='<p>'+inline(b.join('<br>'))+'</p>'}return h}
function bubble(role,text){if(empty&&empty.parentNode)empty.remove();let m=document.createElement('div');m.className='msg '+role;
 m.innerHTML='<div class="msg-label">'+(role==='user'?'You':'Hermes')+'</div><div class="bubble"></div>';let b=m.querySelector('.bubble');
 if(role==='agent'){b.innerHTML='<div class="md">'+md(text)+'</div><div class="msg-tools"><button>Copy</button></div>';b.querySelector('button').onclick=e=>navigator.clipboard.writeText(text).then(()=>{e.target.textContent='Copied';setTimeout(()=>e.target.textContent='Copy',1000)})}
 else b.textContent=text;threadEl.appendChild(m);threadEl.scrollTop=threadEl.scrollHeight}
function thinking(){let d=document.createElement('div');d.className='thinking';d.innerHTML='<span class="dots"><i></i><i></i><i></i></span><span>Hermes is working with live business context…</span>';threadEl.appendChild(d);threadEl.scrollTop=threadEl.scrollHeight;return d}
async function api(path,opt){let r=await fetch(API+path,opt);let d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||'Request failed');return d}
async function loadSessions(){try{let d=await api('/sessions');sessionsEl.innerHTML='';if(d.sessions.length&&!d.sessions.some(x=>x.id===sid))sid=d.sessions[0].id;
 d.sessions.forEach(s=>{let b=document.createElement('button');b.className='session'+(s.id===sid?' on':'');b.innerHTML='<b>'+esc(s.title)+'</b><span>'+s.n+' messages · '+esc((s.updated_at||'').slice(0,16).replace('T',' '))+'</span>';b.onclick=()=>switchSession(s.id);sessionsEl.appendChild(b)});
 let cur=d.sessions.find(x=>x.id===sid);document.getElementById('threadTitle').textContent=cur?cur.title:'New command'}catch(e){}}
async function loadHistory(){try{let d=await api('/history?session='+sid);threadEl.innerHTML='';if(!d.messages.length){threadEl.appendChild(empty)}else d.messages.forEach(m=>bubble(m.role,m.text));lastAgentCount=d.messages.filter(m=>m.role==='agent').length}catch(e){}}
async function switchSession(id){sid=id;localStorage.setItem('labs_command_session',sid);closeMenu();await loadHistory();await loadSessions();input.focus()}
async function newSession(){try{let d=await api('/session/new',{method:'POST'});await switchSession(d.id)}catch(e){alert(e.message)}}
async function send(){let text=input.value.trim();if(busy||(!text&&!attachments.length))return;let at=attachments.slice();attachments=[];renderAttachments();input.value='';autosize();bubble('user',text||(at.length+' photo'+(at.length===1?'':'s')));busy=true;sendBtn.disabled=true;statusEl.textContent='Working';let wait=thinking();
 try{let d=await api('/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sid,message:text,images:at.map(x=>({path:x.path,name:x.name}))})});
  if(d.reply){wait.remove();bubble('agent',d.reply)}else if(d.pending){await poll(wait)}await loadSessions();loadEvents()
 }catch(e){wait.remove();bubble('agent',e.message)}finally{busy=false;sendBtn.disabled=false;statusEl.textContent='Ready';input.focus()}}
async function poll(wait){for(let n=0;n<500;n++){await new Promise(r=>setTimeout(r,6000));let d=await api('/history?session='+sid),agents=d.messages.filter(m=>m.role==='agent');if(agents.length>lastAgentCount){wait.remove();bubble('agent',agents[agents.length-1].text);lastAgentCount=agents.length;return}}wait.remove();bubble('agent','The job is still running. Its result will remain in this conversation.')}
async function upload(f){if(!f||attachments.length>=8)return;let form=new FormData();form.append('image',f);try{let d=await api('/upload',{method:'POST',body:form}),a={path:d.path,name:f.name,url:''};attachments.push(a);renderAttachments();let rd=new FileReader();rd.onload=e=>{a.url=e.target.result;renderAttachments()};rd.readAsDataURL(f)}catch(e){alert(e.message)}}
function renderAttachments(){attachEl.innerHTML='';attachments.forEach((a,i)=>{let c=document.createElement('div');c.className='attachment';c.innerHTML=(a.url?'<img src="'+a.url+'" alt="">':'')+'<span>'+esc(a.name)+'</span><button aria-label="Remove">×</button>';c.querySelector('button').onclick=()=>{attachments.splice(i,1);renderAttachments()};attachEl.appendChild(c)})}
async function loadEvents(){try{let d=await api('/events'),el=document.getElementById('events');el.innerHTML='';(d.events||[]).slice(0,8).forEach(x=>{let v=document.createElement('div');v.className='event';v.innerHTML='<b>'+esc((x.app||'Labs')+' · '+(x.kind||'activity').replace(/_/g,' '))+'</b><p>'+esc(x.detail||'')+'</p><time>'+esc((x.created_at||'').slice(0,16).replace('T',' '))+'</time>';el.appendChild(v)});if(!el.children.length)el.innerHTML='<div class="event"><p>No recent activity.</p></div>'}catch(e){}}
function autosize(){input.style.height='auto';input.style.height=Math.min(input.scrollHeight,150)+'px'}
document.querySelectorAll('.start').forEach(b=>b.onclick=()=>{input.value=b.dataset.prompt;send()});document.getElementById('newBtn').onclick=newSession;
document.getElementById('attachBtn').onclick=()=>document.getElementById('fileInput').click();document.getElementById('fileInput').onchange=async e=>{for(let f of e.target.files)await upload(f);e.target.value=''};
sendBtn.onclick=send;input.oninput=autosize;input.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}};
document.addEventListener('paste',e=>{for(let x of (e.clipboardData&&e.clipboardData.items)||[])if(x.kind==='file'&&x.type.indexOf('image/')===0)upload(x.getAsFile())});
let rail=document.getElementById('leftRail'),shade=document.getElementById('shade');function closeMenu(){rail.classList.remove('open');shade.classList.remove('on')}document.getElementById('menuBtn').onclick=()=>{rail.classList.add('open');shade.classList.add('on')};shade.onclick=closeMenu;
loadSessions().then(loadHistory);loadEvents();input.focus();
})();
""" + WHOAMI_JS + r"""
</script></body></html>"""
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, OUT)
    os.system(f"chown www-data:www-data {OUT}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
