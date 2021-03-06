import asyncio
import dataclasses
import logging
import os
import ssl
import typing

import asyncpg
import coolname
import websockets

import messages
import stages


logger = logging.getLogger(__name__)


@dataclasses.dataclass(unsafe_hash=True)
class User:
    id: str
    socket: typing.Any = dataclasses.field(compare=False, repr=False)

    name: str = dataclasses.field(init=False, compare=False, repr=False)
    age: int = dataclasses.field(init=False, compare=False, repr=False)


class Server:
    def __init__(self):
        self.pg_conn = None
        self.stage = None

    async def set_stage(self, stage_class):
        logger.info(
            "Switching from %s to %s",
            self.stage.__class__.__name__,
            stage_class.__name__,
        )
        self.stage = stage_class(self, self.stage.users)
        await self.stage.start()

    async def setup(self):
        self.stage = stages.TellAboutYourself(self, [])
        await self.stage.start()

        pg_params = {
            "user": os.getenv("PG_USERNAME"),
            "password": os.getenv("PG_PASSWORD"),
            "database": os.getenv("PG_DATABASE"),
            "host": os.getenv("PG_HOST"),
            "port": os.getenv("PG_PORT"),
        }

        cadata = os.getenv("PG_CADATA")
        if cadata:
            pg_params["ssl"] = ssl.create_default_context(cadata=cadata)

        self.pg_conn = await asyncpg.connect(**pg_params)

    async def serve(self, socket, path):
        user = User(coolname.generate_slug(2), socket)
        logger.info("%s connected", user)

        while True:
            try:
                data = await user.socket.recv()
            except (
                websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.ConnectionClosedOK,
            ) as e:
                logger.info("%s closed connection (%s)", user, e)
                await self.stage.on_disconnect(user)
                break

            message = messages.deserialize(data)
            logger.info("Received %s from %s", message, user)

            if isinstance(message, messages.AuthCode):
                await self.stage.on_auth_code(user, message)
            elif isinstance(message, messages.CountCode):
                await self.stage.on_count_code(user, message)
            elif isinstance(message, messages.QuestionAnswers):
                await self.stage.on_question_answers(user, message)
            else:
                raise NotImplementedError(f"Unsupported message {message}")

    async def send(self, user, message):
        await self.send_many(message, [user])

    async def send_many(self, message, users):
        sockets = [u.socket for u in users if u.socket.open]
        logger.info(
            "Sending %s to %s", message, ", ".join(str(u) for u in users) or "Nobody"
        )
        if sockets:
            data = messages.serialize(message)
            await asyncio.gather(*(s.send(data) for s in sockets))


def main():
    logging.basicConfig(level=logging.WARN)
    logging.getLogger(__name__).level = logging.DEBUG

    logger.info("Starting websocket server")
    server = Server()
    start_server = websockets.serve(server.serve, "0.0.0.0", 8000)
    asyncio.get_event_loop().run_until_complete(server.setup())
    asyncio.get_event_loop().run_until_complete(start_server)
    asyncio.get_event_loop().run_forever()


if __name__ == "__main__":
    main()
