"""The gate worklist page. Kept apart from the server so the markup stays legible."""
from __future__ import annotations

import html


def _safe_url(u: str) -> str:
    """Only http(s) survives into an href.

    html.escape stops an attacker breaking OUT of the attribute, but a
    `javascript:` or `data:` value needs no quotes to be dangerous. Gate URLs
    come from our own source clients today; this keeps that assumption from
    becoming load-bearing if a future source echoes back a supplied URL.
    """
    return u if u.lower().startswith(("http://", "https://")) else "#"

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --ink:#12100E; --paper:#F7F4EC; --stamp:#8B2F1D; --verified:#2E5E4E;
  --rule:#C9C2B2; --muted:#6E6759; --card:#FFFDF7;
  --shadow:0 1px 0 var(--rule), 0 12px 28px -20px rgba(18,16,14,.5);
}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:"Newsreader",Georgia,"Times New Roman",serif;
  font-size:17px; line-height:1.55;
  background-image:linear-gradient(var(--paper) 0 0);
}
.mono{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace}

/* ---- masthead: a docket header, not a dashboard hero ---- */
header{
  border-bottom:2px solid var(--ink); padding:28px 24px 18px;
  max-width:1080px; margin:0 auto;
}
.kicker{
  font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.22em;
  text-transform:uppercase; color:var(--stamp); margin:0 0 10px;
}
h1{font-size:clamp(30px,5vw,46px); line-height:1.02; margin:0; font-weight:600; letter-spacing:-.02em}
.standfirst{margin:12px 0 0; max-width:60ch; color:var(--muted); font-size:18px}
.count{font-variant-numeric:tabular-nums}

main{max-width:1080px; margin:0 auto; padding:28px 24px 8px}

/* ---- the card is a case file ---- */
.gate{
  background:var(--card); border:1px solid var(--rule); border-left:4px solid var(--stamp);
  box-shadow:var(--shadow); margin:0 0 26px; padding:22px 24px 20px;
}
.gate[data-done="1"]{border-left-color:var(--verified); opacity:.62}
.gate-top{display:flex; flex-wrap:wrap; gap:10px 16px; align-items:baseline; justify-content:space-between}
.seeking{font-size:21px; line-height:1.28; margin:0; max-width:52ch; font-weight:500}
.age{font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--muted); white-space:nowrap}
.meta{margin:14px 0 0; padding:0; list-style:none; display:grid; gap:6px 22px;
      grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); font-size:13px}
.meta dt{font-family:"IBM Plex Mono",monospace; font-size:10.5px; letter-spacing:.14em;
         text-transform:uppercase; color:var(--muted)}
.meta dd{margin:2px 0 0; word-break:break-word}
.dupe{display:inline-block; margin-left:8px; padding:2px 8px; background:var(--stamp);
      color:var(--paper); font-family:"IBM Plex Mono",monospace; font-size:10.5px;
      letter-spacing:.08em; text-transform:uppercase}

/* ---- the two-step: open, then hand back ---- */
.steps{display:grid; gap:14px; grid-template-columns:minmax(230px,.62fr) 1fr; margin-top:20px}
@media(max-width:720px){.steps{grid-template-columns:1fr}}
.step{border:1px solid var(--rule); padding:14px 16px 16px; background:var(--paper)}
.step.act{background:var(--card); border-color:var(--ink)}
.step h3{margin:0 0 8px; font-family:"IBM Plex Mono",monospace; font-size:11px;
         letter-spacing:.16em; text-transform:uppercase; color:var(--muted); font-weight:500}
.btn{
  display:inline-block; border:1.5px solid var(--ink); background:var(--ink); color:var(--paper);
  padding:10px 18px; font:inherit; font-size:15px; cursor:pointer; text-decoration:none;
  transition:background .12s ease,color .12s ease;
}
.btn:hover{background:transparent; color:var(--ink)}
.btn.ghost{background:transparent; color:var(--ink)}
.btn.ghost:hover{background:var(--ink); color:var(--paper)}
.btn:focus-visible,.drop:focus-visible,summary:focus-visible{outline:3px solid var(--stamp); outline-offset:2px}
.hint{margin:10px 0 0; font-size:13.5px; color:var(--muted)}
.linkish{border:0; background:none; padding:0; font:inherit; color:var(--stamp);
         text-decoration:underline; text-underline-offset:2px; cursor:pointer}
.linkish:hover{color:var(--ink)}
.linkish:focus-visible{outline:3px solid var(--stamp); outline-offset:2px}

.drop{
  border:1.5px dashed var(--stamp); background:var(--card); padding:18px; min-height:118px;
  display:flex; align-items:center; justify-content:center; text-align:center;
  font-size:14px; color:var(--muted); cursor:text; transition:border-color .12s,background .12s;
}
.drop:hover,.drop.over{border-color:var(--ink); background:#fff; color:var(--ink)}
.drop.over{border-style:solid}

.verdict{margin:14px 0 0; padding:12px 14px; font-size:14.5px; border-left:3px solid var(--rule); display:none}
.verdict.ok{display:block; border-left-color:var(--verified); background:rgba(46,94,78,.07)}
.verdict.bad{display:block; border-left-color:var(--stamp); background:rgba(139,47,29,.07)}
.verdict .sha{display:block; margin-top:6px; font-family:"IBM Plex Mono",monospace;
              font-size:11.5px; color:var(--muted); word-break:break-all}

.empty{border:1px solid var(--rule); padding:44px 28px; text-align:center; background:var(--card)}
.empty h2{margin:0 0 8px; font-size:26px; font-weight:500}
.empty p{margin:0; color:var(--muted)}
footer{max-width:1080px; margin:0 auto; padding:20px 24px 48px; color:var(--muted); font-size:13px;
       border-top:1px solid var(--rule)}
footer code{font-family:"IBM Plex Mono",monospace; background:var(--card);
            border:1px solid var(--rule); padding:1px 5px}
@media(prefers-reduced-motion:reduce){*{transition:none!important; animation:none!important}}
"""

JS = """
async function hand(token, text, source){
  const card = document.querySelector(`[data-token="${token}"]`);
  const v = card.querySelector('.verdict');
  v.className = 'verdict'; v.textContent = 'Checking\\u2026'; v.classList.add('ok');
  const r = await fetch('/resume', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({token, body:text, source})
  });
  const d = await r.json();
  v.className = 'verdict ' + (d.ok ? 'ok' : 'bad');
  v.textContent = d.message;
  if (d.ok){
    card.dataset.done = '1';
    const s = document.createElement('span');
    s.className = 'sha';
    s.textContent = d.sha ? ('archived \\u00b7 sha256 ' + d.sha) : '';
    v.appendChild(s);
    card.querySelectorAll('button,.drop').forEach(el => el.setAttribute('disabled',''));
  }
}
function wire(card){
  const token = card.dataset.token;
  const drop = card.querySelector('.drop');
  drop.addEventListener('paste', e => {
    e.preventDefault();
    hand(token, (e.clipboardData||window.clipboardData).getData('text'), 'paste');
  });
  ['dragenter','dragover'].forEach(n => drop.addEventListener(n, e => {
    e.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave','drop'].forEach(n => drop.addEventListener(n, e => {
    e.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop', async e => {
    const f = e.dataTransfer.files[0];
    if (f) hand(token, await f.text(), 'file:' + f.name);
  });
  card.querySelector('.pick').addEventListener('click', () => {
    const i = document.createElement('input');
    i.type = 'file'; i.accept = '.html,.htm,.txt';
    i.onchange = async () => { if (i.files[0]) hand(token, await i.files[0].text(), 'file:' + i.files[0].name); };
    i.click();
  });
}
document.querySelectorAll('.gate').forEach(wire);
"""


def render(gates: list[dict]) -> str:
    if gates:
        n = len(gates)
        cards = "\n".join(_card(g) for g in gates)
        body = f"""<main>{cards}</main>"""
        stand = (f'<p class="standfirst">{n} search{"es" if n != 1 else ""} '
                 f'automated up to a wall only a person can pass. '
                 f'Clear one and the pipeline picks it back up.</p>')
    else:
        body = """<main><div class="empty">
          <h2>Nothing is waiting on you.</h2>
          <p>Gates appear here when a search hits a challenge a browser cannot clear.</p>
        </div></main>"""
        stand = '<p class="standfirst">The queue is empty.</p>'

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gates awaiting a human · evidence-search</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<header>
  <p class="kicker">evidence-search · humanomation</p>
  <h1>Gates awaiting a human</h1>
  {stand}
</header>
{body}
<footer>
  <p>Each capture is archived with a SHA-256 and a manifest line, exactly as
  <code>gate resume</code> would. Close this window when you are done —
  the queue lives in the shared store, not in the page.</p>
</footer>
<script>{JS}</script></body></html>"""


def _card(g: dict) -> str:
    e = html.escape
    dupe = (f'<span class="dupe">solve once · clears {len(g["also"]) + 1}</span>'
            if g["also"] else "")
    return f"""
<article class="gate" data-token="{e(g['token'])}">
  <div class="gate-top">
    <p class="seeking">{e(g['seeking'])}{dupe}</p>
    <span class="age">parked {e(g['age'])}</span>
  </div>
  <dl class="meta">
    <div><dt>Source</dt><dd class="mono">{e(g['source'])}</dd></div>
    <div><dt>Gate</dt><dd>{e(g['gate'])}</dd></div>
    <div><dt>Bring back</dt><dd>{e(g['capture'][0])}</dd></div>
    <div><dt>Token</dt><dd class="mono">{e(g['token'])}</dd></div>
  </dl>
  <div class="steps">
    <div class="step">
      <h3>1 · Pass the challenge</h3>
      <a class="btn" href="{e(_safe_url(g['url']))}" target="_blank" rel="noopener">Open the search</a>
      <p class="hint">Opens in a new tab. Solve it, and let the results load.</p>
    </div>
    <div class="step act">
      <h3>2 · Hand the page back</h3>
      <div class="drop" tabindex="0" role="button"
           aria-label="Paste the page here, or drop a saved file">
        Select all on that tab, copy, and paste here.<br>Or drop the saved file.
      </div>
      <p class="hint">No clipboard? <button class="linkish pick" type="button">choose a saved file</button>.</p>
    </div>
  </div>
  <div class="verdict" role="status" aria-live="polite"></div>
</article>"""
