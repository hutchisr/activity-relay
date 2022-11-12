import aiohttp.web
from urllib.parse import urlsplit
import datetime
from . import app, CONFIG
from .database import DATABASE

host = CONFIG["ap"]["host"]
note = CONFIG["note"]
pause_days = datetime.timedelta(days=CONFIG["pause_days"])

inboxes = DATABASE.get("relay-list", [])
errors = DATABASE.get("errors", [])

with open("index.tpl") as f:
    index_tpl = f.read()


async def default(request):
    targets = ""
    now = datetime.datetime.utcnow()
    for target in inboxes:
        if target in errors:
            if (now - datetime.datetime.fromisoformat(errors[target])) >= pause_days:
                status = "🔴"
            else:
                status = "🟠"
        else:
            status = "🟢"
        parsed = urlsplit(target)
        targets += '<li>{} <a href="{}://{}">{}</a></li>'.format(
            status, parsed.scheme, parsed.netloc, parsed.hostname
        )

    return aiohttp.web.Response(
        status=200,
        content_type="text/html",
        charset="utf-8",
        text=index_tpl.format(
            host=host, note=note, targets=targets, count=len(inboxes)
        ),
    )


app.router.add_get("/", default)
