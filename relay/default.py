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
    ul {{ display: flex; flex-flow: wrap column; max-height: 500px; }}
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
<p>
To report abuse please contact <a href="https://social.homunyan.com/@animeirl">@animeirl</a>
</p>
</section><section>
<h4>{count} registered instances</h4>
<ul>{targets}</ul>
</section>
</body></html>

""".format(host=host, note=note, targets=targets, count=len(inboxes)))

app.router.add_get('/', default)
