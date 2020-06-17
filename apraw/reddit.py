import asyncio
import configparser
import logging
from datetime import datetime, timedelta

from .endpoints import API_PATH, BASE_URL
from .models import ListingGenerator, Redditor, Subreddit, User


class Reddit:

    def __init__(self, praw_key="", username="", password="", client_id="", client_secret="",
                 user_agent="aPRAW by Dan6erbond"):
        if praw_key != "":
            config = configparser.ConfigParser()
            config.read("praw.ini")

            self.user = User(self,
                             config[praw_key]["username"],
                             config[praw_key]["password"],
                             config[praw_key]["client_id"],
                             config[praw_key]["client_secret"],
                             config[praw_key]["user_agent"] if "user_agent" in config[praw_key] else user_agent)
        else:
            self.user = User(self,
                             username,
                             password,
                             client_id,
                             client_secret,
                             user_agent)

        self.comment_kind = "t1"
        self.account_kind = "t2"
        self.link_kind = "t3"
        self.message_kind = "t4"
        self.subreddit_kind = "t5"
        self.award_kind = "t6"
        self.modaction_kind = "modaction"

        self.subreddits = ListingGenerator(self, API_PATH["subreddits_new"])
        self.request_handler = RequestHandler(self.user)

    async def get_request(self, endpoint="", **kwargs):
        return await self.request_handler.get_request(endpoint, **kwargs)

    async def post_request(self, endpoint="", url="", data={}, **kwargs):
        return await self.request_handler.post_request(endpoint, url, data, **kwargs)

    def get_listing_generator(self, endpoint, max_wait=16, kind_filter=[]):
        return ListingGenerator.get_listing_generator(
            self, endpoint, max_wait, kind_filter)

    async def subreddit(self, display_name):
        resp = await self.get_request(API_PATH["subreddit_about"].format(sub=display_name))
        try:
            return Subreddit(self, resp["data"])
        except Exception as e:
            logging.error(e)
            return None

    async def info(self, id="", ids=[], url=""):
        listing_generator = self.get_listing_generator(API_PATH["info"])

        if id:
            async for i in listing_generator(id=id):
                yield i
        elif ids:
            async for i in listing_generator(None, id=",".join(ids)):
                yield i
        elif url:
            async for i in listing_generator(url=url):
                yield i
        else:
            yield None

    async def submission(self, id="", url=""):
        if id != "":
            id = self.link_kind + "_" + id.replace(self.link_kind + "_", "")
            async for link in self.info(id):
                return link
        elif url != "":
            async for link in self.info(url=url):
                return link
        return None

    async def comment(self, id="", url=""):
        if id != "":
            id = self.comment_kind + "_" + \
                id.replace(self.comment_kind + "_", "")
            async for comment in self.info(id):
                return comment
        elif url != "":
            async for comment in self.info(url=url):
                return comment
        return None

    async def redditor(self, username):
        resp = await self.get_request(API_PATH["user_about"].format(user=username))
        try:
            return Redditor(self, resp["data"])
        except Exception as e:
            # print("No Redditor data loaded for {}.".format(username))
            return None

    async def message(self, to, subject, text, from_sr=""):
        data = {
            "subject": subject,
            "text": text,
            "to": to
        }
        if from_sr != "":
            data["from_sr"] = from_sr
        resp = await self.post_request(API_PATH["compose"], data=data)
        return resp["success"]


class RequestHandler:

    def __init__(self, user):
        self.user = user
        self.queue = []

    async def get_request_headers(self):
        if self.user.token_expires <= datetime.now():
            url = "https://www.reddit.com/api/v1/access_token"
            session = await self.user.get_auth_session()

            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self.user.user_agent
            }

            resp = await session.post(url, data=self.user.password_grant, headers=headers)

            async with resp:
                if resp.status == 200:
                    self.user.access_data = await resp.json()
                    self.user.token_expires = datetime.now()
                    + timedelta(seconds=self.user.access_data["expires_in"])
                else:
                    raise Exception("Invalid user data.")

        return {
            "Authorization": "{} {}".format(self.user.access_data["token_type"], self.user.access_data["access_token"]),
            "User-Agent": self.user.user_agent
        }

    def update(self, data):
        self.user.ratelimit_remaining = int(
            float(data["x-ratelimit-remaining"]))
        self.user.ratelimit_used = int(data["x-ratelimit-used"])

        self.user.ratelimit_reset = datetime.now()
        + timedelta(seconds=int(data["x-ratelimit-reset"]))

    def check_ratelimit(func):
        async def execute_request(self, *args, **kwargs):
            id = datetime.now().strftime('%Y%m%d%H%M%S')
            self.queue.append(id)

            if (self.user.ratelimit_remaining < 5):
                execution_time = self.user.ratelimit_reset
                + timedelta(seconds=len(self.queue))
                wait_time = (execution_time - datetime.now()).total_seconds()
                asyncio.sleep(wait_time)

            result = await func(self, *args, **kwargs)
            self.queue.remove(id)
            return result

        return execute_request

    @check_ratelimit
    async def get_request(self, endpoint="", **kwargs):
        kwargs["raw_json"] = 1
        params = ["{}={}".format(k, kwargs[k]) for k in kwargs]

        url = BASE_URL.format(endpoint, "&".join(params))

        headers = await self.get_request_headers()
        session = await self.user.get_client_session()
        resp = await session.get(url, headers=headers)

        async with resp:
            self.update(resp.headers)
            return await resp.json()

    @check_ratelimit
    async def post_request(self, endpoint="", url="", data={}, **kwargs):
        kwargs["raw_json"] = 1
        params = ["{}={}".format(k, kwargs[k]) for k in kwargs]

        if endpoint != "":
            url = BASE_URL.format(endpoint, "&".join(params))
        elif url != "":
            url = "{}?{}".format(url, "&".join(params))

        headers = await self.get_request_headers()
        session = await self.user.get_client_session()
        resp = await session.post(url, data=data, headers=headers)

        async with resp:
            self.update(resp.headers)
            return await resp.json()
