import asyncio
import random
from collections import defaultdict

import messages


COLORS = (  # https://visme.co/blog/wp-content/uploads/2016/09/website.jpg
    "#E27D60",
    "#085DCB",
    "#E8A87C",
    "#C38D93",
    "#41B3A3",
)


class Stage:
    def __init__(self, server, users):
        self.server = server
        self.users = set(users)

    async def start(self):
        pass

    async def on_auth_code(self, user, message):
        code = await self.server.pg_conn.fetchval(
            'SELECT code FROM public."Image" WHERE code = $1;', message.code
        )

        if message.code == 9999:
            await self.server.set_stage(TellAboutYourself)
            await self.server.send_many(messages.Reset(), self.users)
        elif not code:
            await self.server.send(user, messages.AuthCodeInvalid())
            await self.on_auth_code_invalid(user, message)
        else:
            await self.server.send(user, messages.AuthCodeOk())
            await self.on_auth_code_ok(user, message)

    async def on_auth_code_ok(self, user, message):
        pass

    async def on_auth_code_invalid(self, user, message):
        pass

    async def on_question_answers(self, user, message):
        user.age = message.age
        user.name = message.name
        await self.on_question_answered(user, message)

    async def on_questions_answered(self, user, message):
        pass


class TellAboutYourself(Stage):
    async def start(self):
        self.users_to_answer = set(self.users)
        self.users_answered = set()

    async def on_disconnect(self, user):
        self.users.discard(user)
        self.users_to_answer.discard(user)
        self.users_answered.discard(user)
        await self.send_lobby_count()

    async def on_auth_code_ok(self, user, message):
        self.users.add(user)
        self.users_to_answer.add(user)
        await self.send_lobby_count()

    async def on_auth_code_invalid(self, user, message):
        if message.code == 9998:
            await self.server.set_stage(CountingDown)

    async def on_question_answered(self, user, message):
        self.users_to_answer.discard(user)
        self.users_answered.add(user)
        await self.send_lobby_count()

    async def send_lobby_count(self):
        await self.server.send_many(
            messages.LobbyCount(
                connected=len(self.users), done=len(self.users_answered),
            ),
            self.users_answered,
        )


class CountingDown(Stage):
    async def start(self):
        self.count = 5
        asyncio.create_task(self.count_down())

    async def on_disconnect(self, user):
        self.users.discard(user)

    async def on_question_answered(self, user, message):
        self.users.add(user)
        await self.server.send(user, messages.Countdown(self.count))

    async def count_down(self):
        for i in range(5, 0, -1):
            self.count = i
            await self.server.send_many(messages.Countdown(i), self.users)
            await asyncio.sleep(1)
        await self.server.set_stage(FindingGroup)


class FindingGroup(Stage):
    def add_user_to_random_group(self, user):
        color = random.choice(COLORS[:2])
        self.groups[color].add(user)
        return color

    def get_group_name_and_group_by_user(self, user):
        for group_name, group in self.groups.items():
            if user in group:
                return group_name, group
        raise Exception("No group found for user")

    async def start(self):
        self.groups = defaultdict(set)
        self.done_groups = set()
        for user in self.users:
            self.add_user_to_random_group(user)

        blas = []
        for color, group in self.groups.items():
            for user in group:
                blas.append(self.server.send(user, messages.ShowCountCode(color=color)))
        await asyncio.gather(*blas)

    async def on_question_answered(self, user, message):
        self.users.add(user)
        color = self.add_user_to_random_group(user)
        await self.server.send(user, messages.ShowCountCode(color))

    async def on_count_code(self, user, message):
        group_name, group = self.get_group_name_and_group_by_user(user)
        if message.code == len(group):
            # Code guessed correctly
            self.done_groups.add(group_name)
            if len(self.done_groups) == len(self.groups):
                # All groups finished
                await self.server.set_stage(Success)
            else:
                # Not all groups finished, update finished users with progress
                all_done_users = set()
                for group_name in self.done_groups:
                    all_done_users |= self.groups[group_name]
                await self.server.send_many(
                    messages.WaitForGroups(
                        done=len(self.done_groups), total=len(self.groups)
                    ),
                    all_done_users,
                )
        else:
            await self.server.send(user, messages.CountCodeInvalid())

    async def on_disconnect(self, user):
        self.users.discard(user)
        for group in self.groups.values():
            group.discard(user)


class Success(Stage):
    async def start(self):
        await self.server.send_many(messages.ShowSuccess(), self.users)
        asyncio.create_task(self.go_to_next_stage())

    async def on_disconnect(self, user):
        self.users.discard(user)

    async def on_question_answered(self, user, message):
        self.users.add(user)

    async def go_to_next_stage(self):
        await asyncio.sleep(2)
        await self.server.set_stage(CountingDown)
