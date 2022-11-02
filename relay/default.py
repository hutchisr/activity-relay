import aiohttp.web
from urllib.parse import urlsplit
from . import app, CONFIG
from .database import DATABASE

host = CONFIG['ap']['host']
note = CONFIG['note']

inboxes = DATABASE.get('relay-list', [])

async def default(request):
    targets = ''
    for target in inboxes:
        parsed = urlsplit(target)
        targets += '<li><a href="{}://{}">{}</a></li>'.format(parsed.scheme, parsed.netloc, parsed.hostname)
    return aiohttp.web.Response(
        status=200,
        content_type="text/html",
        charset="utf-8",
        text="""
<html><head>
 <title>ActivityPub Relay at {host}</title>
 <style>
  {{ color: #9baec8; font-family: Roboto, sans-serif; font-size: 16px; font-weight: 400;}}
  body {{ background-color: #16191f; color: #9baec8; font-family: Roboto, sans-serif; font-size: 16px; font-weight: 400;}}
  a {{ color: #2b90d9; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  @media only screen and (min-width : 1025px) {{
    ul.instances {{ column-count: 4; }}
  }}
  </style>
</head>
<body>
<h1>This is an Activity Relay for fediverse instances.</h1>
<section>
<p>{note}</p>
<p>For Mastodon instances, you may subscribe to this relay with the address: <a href="https://{host}/inbox">https://{host}/inbox</a></p>
<p>For Pleroma and other instances, you may subscribe to this relay with the address: <a href="https://{host}/actor">https://{host}/actor</a></p>
<p>To host your own relay, you may download the code at this address: <a href="https://git.pleroma.social/pleroma/relay">https://git.pleroma.social/pleroma/relay</a></p>
<h4>Disclaimer</h4>
<p>The servers below are not endorsed by the owners of this relay nor are they actively monitored.</p>
<p>To report abuse please contact <a href="https://social.homunyan.com/@animeirl">@animeirl</a></p>
<h4>Donations</h4>
<p>This server costs time and money to maintain. Donations are accepted.</p>
<ul>
<li>XLM: <code class="fake-money">GBQAJJAOGIUY6N355D4EPIMNDGJSEJVOALFPQ2BHIF5WAHVBPGTIFBCJ</code></li>
<li>BTC: <code class="fake-money">bc1qegdtds7jtqd82rfnst783543tztpeljtqkjnul</code></li>
<li>ETH: <code class="fake-money">0x343eB67F6e7d00d88Bd2a033eb90ff7264698879</code></li>
<li>XMR: <code class="fake-money">89TNsVHs1pChcRUjsrB355hTuhiMCYgQqhchUVioHLikBqn3XrAqAfmZZxmD6g1K66XzbxRw1jnGCZry8xep9FQh1UiBpdp</code></li>
<li>ADA: <code class="fake-money">addr1qx4z9qtfnpg5duefmc03lgq0u9xg7axrukwypxya4nl92kpntyu6p952szu80scwuxq6vmy8rzfa2yq2xw5cqqu42rgsr888sm</code></li>
</ul>
</section><section>
<h4>{count} registered instances</h4>
<ul class="instances">{targets}</ul>
</section>
</body></html>

""".format(host=host, note=note, targets=targets, count=len(inboxes)))

app.router.add_get('/', default)
