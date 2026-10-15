"""슬래시 명령어(bot/commands.py) 테스트."""
import discord
from discord.ext import commands

from bot.commands import register_match_channel, setup_commands
from db.orm import User


def _user(session, discord_id="1490", channel=None) -> User:
    user = User(username="A", discord_id=discord_id, match_channel_id=channel)
    session.add(user)
    session.commit()
    return user


def test_register_match_channel_sets_channel_for_the_caller_only(session):
    me, other = _user(session, "1490"), _user(session, "2222")

    text = register_match_channel(session, 1490, 555, visible_to_everyone=False)

    assert session.get(User, me.id).match_channel_id == "555"
    assert session.get(User, other.id).match_channel_id is None
    assert "매칭 추천 채널로 정했어요" in text and "⚠️" not in text


def test_register_match_channel_warns_on_public_channel_and_mentions_previous(session):
    _user(session, channel="111")
    text = register_match_channel(session, 1490, 555, visible_to_everyone=True)

    assert "이전 채널로는 더 이상" in text
    assert "모든 사람이 볼 수 있어서" in text


def test_register_match_channel_requires_linked_account(session):
    text = register_match_channel(session, 9999, 555, visible_to_everyone=False)
    assert "연결" in text


def test_setup_commands_registers_match_channel_command():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    setup_commands(bot)
    assert [c.name for c in bot.tree.get_commands()] == ["매칭채널"]
