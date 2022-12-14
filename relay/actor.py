import aiohttp
import aiohttp.web
import asyncio
import logging
import uuid
import re
import simplejson as json
import cgi
import datetime

from urllib.parse import urlsplit
from Crypto.PublicKey import RSA
from cachetools import LFUCache

from . import app, CONFIG, __version__
from .database import DATABASE
from .http_debug import http_debug
from .remote_actor import fetch_actor
from .http_signatures import sign_headers, generate_body_digest

pause_days = datetime.timedelta(days=CONFIG["pause_days"])

# generate actor keys if not present
if "actorKeys" not in DATABASE:
    logging.info("No actor keys present, generating 4096-bit RSA keypair.")

    privkey = RSA.generate(4096)
    pubkey = privkey.publickey()

    DATABASE["actorKeys"] = {
        "publicKey": pubkey.exportKey("PEM").decode("utf-8"),
        "privateKey": privkey.exportKey("PEM").decode("utf-8"),
    }


PRIVKEY = RSA.importKey(DATABASE["actorKeys"]["privateKey"])
PUBKEY = PRIVKEY.publickey()
AP_CONFIG = CONFIG["ap"]
CACHE_SIZE = CONFIG.get("cache-size", 16384)
CACHE = LFUCache(CACHE_SIZE)
PAUSE_DAYS = CONFIG.get("pause_days", 7)
REMOVE_DAYS = CONFIG.get("remove_days", 30)

sem = asyncio.Semaphore(500)


async def actor(request):
    data = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "endpoints": {"sharedInbox": "https://{}/inbox".format(request.host)},
        "followers": "https://{}/followers".format(request.host),
        "following": "https://{}/following".format(request.host),
        "inbox": "https://{}/inbox".format(request.host),
        "name": "ActivityRelay",
        "type": "Application",
        "id": "https://{}/actor".format(request.host),
        "publicKey": {
            "id": "https://{}/actor#main-key".format(request.host),
            "owner": "https://{}/actor".format(request.host),
            "publicKeyPem": DATABASE["actorKeys"]["publicKey"],
        },
        "summary": "ActivityRelay bot",
        "preferredUsername": "relay",
        "url": "https://{}/actor".format(request.host),
    }
    return aiohttp.web.json_response(data, content_type="application/activity+json")


app.router.add_get("/actor", actor)
get_actor_inbox = lambda actor: actor.get("endpoints", {}).get(
    "sharedInbox", actor["inbox"]
)


async def push_message_to_actor(actor, message, our_key_id):
    inbox = get_actor_inbox(actor)
    if inbox in DATABASE["errors"]:
        last = datetime.datetime.fromisoformat(DATABASE["errors"][inbox])
        now = datetime.datetime.utcnow()
        if (now - last) >= pause_days:
            logging.warning("Skipped delivery to %s due to excessive delivery failures", inbox)
            return
    url = urlsplit(inbox)

    # XXX: Digest
    data = json.dumps(message)
    headers = {
        "(request-target)": "post {}".format(url.path),
        "Content-Length": str(len(data)),
        "Content-Type": "application/activity+json",
        "User-Agent": "ActivityRelay/{}".format(__version__),
        "Host": url.netloc,
        "Digest": "SHA-256={}".format(generate_body_digest(data)),
        "Date": datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
    }
    headers["signature"] = sign_headers(headers, PRIVKEY, our_key_id)
    headers.pop("(request-target)")
    headers.pop("Host")

    logging.debug("%r >> %r", inbox, message)

    async with sem:
        try:
            async with aiohttp.ClientSession(trace_configs=[http_debug()]) as session:
                async with session.post(inbox, data=data, headers=headers) as resp:
                    if resp.status == 202:
                        pass
                    else:
                        resp_payload = await resp.text()
                        logging.debug("%r >> resp %r", inbox, resp_payload)
                    if inbox in DATABASE["errors"]:
                        DATABASE["errors"].pop(inbox)
        except Exception as e:
            logging.info("Caught %r while pushing to %r.", e, inbox)
            if inbox not in DATABASE["errors"]:
                DATABASE["errors"][inbox] = str(datetime.datetime.utcnow())


async def fetch_nodeinfo(domain):
    headers = {"Accept": "application/json"}
    nodeinfo_url = None

    wk_nodeinfo = await fetch_actor(
        f"https://{domain}/.well-known/nodeinfo", headers=headers
    )

    if not wk_nodeinfo:
        return

    for link in wk_nodeinfo.get("links", ""):
        if link["rel"] == "http://nodeinfo.diaspora.software/ns/schema/2.0":
            nodeinfo_url = link["href"]
            break

    if not nodeinfo_url:
        return

    nodeinfo_data = await fetch_actor(nodeinfo_url, headers=headers)
    software = nodeinfo_data.get("software")

    return software.get("name") if software else None


async def follow_remote_actor(actor_uri):
    actor = await fetch_actor(actor_uri)

    if not actor:
        logging.info("failed to fetch actor at: %r", actor_uri)
        return

    if (
        AP_CONFIG["whitelist_enabled"] is True
        and urlsplit(actor_uri).hostname not in AP_CONFIG["whitelist"]
    ):
        logging.info("refusing to follow non-whitelisted actor: %r", actor_uri)
        return

    logging.info("following: %r", actor_uri)

    message = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Follow",
        "to": [actor["id"]],
        "object": actor["id"],
        "id": "https://{}/activities/{}".format(AP_CONFIG["host"], uuid.uuid4()),
        "actor": "https://{}/actor".format(AP_CONFIG["host"]),
    }
    await push_message_to_actor(
        actor, message, "https://{}/actor#main-key".format(AP_CONFIG["host"])
    )


async def unfollow_remote_actor(actor_uri):
    actor = await fetch_actor(actor_uri)
    if not actor:
        logging.info("failed to fetch actor at: %r", actor_uri)
        return

    logging.info("unfollowing: %r", actor_uri)

    message = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Undo",
        "to": [actor["id"]],
        "object": {
            "type": "Follow",
            "object": actor_uri,
            "actor": actor["id"],
            "id": "https://{}/activities/{}".format(AP_CONFIG["host"], uuid.uuid4()),
        },
        "id": "https://{}/activities/{}".format(AP_CONFIG["host"], uuid.uuid4()),
        "actor": "https://{}/actor".format(AP_CONFIG["host"]),
    }
    await push_message_to_actor(
        actor, message, "https://{}/actor#main-key".format(AP_CONFIG["host"])
    )


tag_re = re.compile(r"(<!--.*?-->|<[^>]*>)")


def strip_html(data):
    no_tags = tag_re.sub("", data)
    return cgi.escape(no_tags)


def distill_inboxes(actor, object_id):
    global DATABASE

    origin_hostname = urlsplit(object_id).hostname

    inbox = get_actor_inbox(actor)
    targets = [target for target in DATABASE.get("relay-list", []) if target != inbox]
    targets = [
        target for target in targets if urlsplit(target).hostname != origin_hostname
    ]
    hostnames = [urlsplit(target).hostname for target in targets]

    assert inbox not in targets
    assert origin_hostname not in hostnames

    return targets


def distill_object_id(activity):
    logging.debug(">> determining object ID for %r", activity["object"])
    obj = activity["object"]

    if isinstance(obj, str):
        return obj

    return obj["id"]


async def handle_relay(actor, data, request):
    global CACHE

    object_id = distill_object_id(data)

    if object_id in CACHE:
        logging.debug(">> already relayed %r as %r", object_id, CACHE[object_id])
        return

    activity_id = "https://{}/activities/{}".format(request.host, uuid.uuid4())

    message = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Announce",
        "to": ["https://{}/followers".format(request.host)],
        "actor": "https://{}/actor".format(request.host),
        "object": object_id,
        "id": activity_id,
    }

    logging.debug(">> relay: %r", message)

    inboxes = distill_inboxes(actor, object_id)

    futures = [
        push_message_to_actor(
            {"inbox": inbox}, message, "https://{}/actor#main-key".format(request.host)
        )
        for inbox in inboxes
    ]
    asyncio.ensure_future(asyncio.gather(*futures))

    CACHE[object_id] = activity_id


async def handle_forward(actor, data, request):
    object_id = distill_object_id(data)

    logging.debug(">> Relay %r", data)

    inboxes = distill_inboxes(actor, object_id)

    futures = [
        push_message_to_actor(
            {"inbox": inbox}, data, "https://{}/actor#main-key".format(request.host)
        )
        for inbox in inboxes
    ]
    asyncio.ensure_future(asyncio.gather(*futures))


async def handle_follow(actor, data, request):
    global DATABASE

    following = DATABASE.get("relay-list", [])
    inbox = get_actor_inbox(actor)

    if urlsplit(inbox).hostname in AP_CONFIG["blocked_instances"]:
        return

    if inbox not in following:
        following += [inbox]
        DATABASE["relay-list"] = following

        asyncio.ensure_future(follow_remote_actor(actor["id"]))

    message = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Accept",
        "to": [actor["id"]],
        "actor": "https://{}/actor".format(request.host),
        # this is wrong per litepub, but mastodon < 2.4 is not compliant with that profile.
        "object": {
            "type": "Follow",
            "id": data["id"],
            "object": "https://{}/actor".format(request.host),
            "actor": actor["id"],
        },
        "id": "https://{}/activities/{}".format(request.host, uuid.uuid4()),
    }

    asyncio.ensure_future(
        push_message_to_actor(
            actor, message, "https://{}/actor#main-key".format(request.host)
        )
    )


async def handle_undo(actor, data, request):
    global DATABASE

    child = data["object"]
    if child["type"] == "Follow":
        following = DATABASE.get("relay-list", [])

        inbox = get_actor_inbox(actor)

        if inbox in following:
            following.remove(inbox)
            DATABASE["relay-list"] = following

        await unfollow_remote_actor(actor["id"])


processors = {
    "Announce": handle_relay,
    "Create": handle_relay,
    "Delete": handle_forward,
    "Follow": handle_follow,
    "Undo": handle_undo,
    "Update": handle_forward,
}


async def inbox(request):
    data = await request.json()
    instance = urlsplit(data["actor"]).hostname

    if AP_CONFIG["blocked_software"]:
        software = await fetch_nodeinfo(instance)

        if software and software.lower() in AP_CONFIG["blocked_software"]:
            raise aiohttp.web.HTTPUnauthorized(text="relays have been blocked")

    if "actor" not in data or not request["validated"]:
        raise aiohttp.web.HTTPUnauthorized(text="access denied")

    elif (
        data["type"] != "Follow"
        and "https://{}/inbox".format(instance) not in DATABASE["relay-list"]
    ):
        raise aiohttp.web.HTTPUnauthorized(text="access denied")

    elif (
        AP_CONFIG["whitelist_enabled"] is True
        and instance not in AP_CONFIG["whitelist"]
    ):
        raise aiohttp.web.HTTPUnauthorized(text="access denied")

    actor = await fetch_actor(data["actor"])
    # actor_uri = "https://{}/actor".format(request.host)

    logging.debug(">> payload %r", data)

    inbox = get_actor_inbox(actor)
    if inbox in DATABASE["errors"]:
        logging.debug(">> Unpausing %s", inbox)
        DATABASE["errors"].pop(inbox)

    processor = processors.get(data["type"], None)
    if processor:
        await processor(actor, data, request)

    return aiohttp.web.Response(body=b"{}", content_type="application/activity+json")


app.router.add_post("/inbox", inbox)
