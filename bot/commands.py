"""Discord 슬래시 명령어.

/매칭채널 — 명령을 실행한 채널을 실행한 사람의 매칭 추천 채널(users.match_channel_id)로 정한다.
  사람은 Discord 계정(users.discord_id, 웹에서 연결)으로 찾는다. 남이 대신 정할 수 없다.
  모두가 볼 수 있는 채널이면 설정은 하되, 점수가 다른 사람에게 보인다고 경고한다.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from db.orm import User
from shared.config import get_settings
from shared.db import get_session

logger = logging.getLogger(__name__)


def register_match_channel(session, discord_user_id: int, channel_id: int, visible_to_everyone: bool) -> str:
    """discord_user_id의 사용자에게 channel_id를 매칭 채널로 저장하고 응답 문구를 돌려준다. 커밋까지 한다."""
    user = session.query(User).filter_by(discord_id=str(discord_user_id)).first()
    if user is None:
        return ("아직 Matchwise 계정에 이 Discord 계정이 연결돼 있지 않아요. "
                "웹에서 로그인한 뒤 Discord 연결을 먼저 해주세요.")

    previous = user.match_channel_id
    user.match_channel_id = str(channel_id)
    session.commit()
    logger.info("[command] user_id=%s 매칭 채널 설정 (이전 %s)", user.id, "있음" if previous else "없음")

    text = "✅ 이 채널을 매칭 추천 채널로 정했어요. 내일 아침(8시 수집이 끝난 뒤)부터 새 추천이 여기로 와요."
    if previous and previous != str(channel_id):
        text += "\n(이전 채널로는 더 이상 보내지 않아요.)"
    if visible_to_everyone:
        text += "\n⚠️ 이 채널은 서버의 모든 사람이 볼 수 있어서 내 점수가 보여요. 나만 보는 비공개 채널을 추천해요."
    return text


def setup_commands(bot: commands.Bot) -> None:
    """bot에 슬래시 명령어를 등록하고, 접속할 때 Discord에 명령어 목록을 올린다.

    DISCORD_GUILD_ID가 있으면 그 서버에만 바로 올리고(즉시 반영), 없으면 전체에 올린다(반영에 최대 1시간).
    """

    @bot.tree.command(name="매칭채널", description="이 채널을 내 매칭 추천 채널로 정해요")
    async def match_channel(interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("서버의 채널에서 실행해주세요.", ephemeral=True)
            return
        everyone = interaction.channel.permissions_for(interaction.guild.default_role)
        session = get_session()
        try:
            text = register_match_channel(
                session, interaction.user.id, interaction.channel_id, everyone.view_channel,
            )
        finally:
            session.close()
        await interaction.response.send_message(text, ephemeral=True)

    async def sync_commands():
        guild_id = get_settings().discord.guild_id
        if guild_id:
            guild = discord.Object(id=guild_id)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
        else:
            synced = await bot.tree.sync()
        logger.info("슬래시 명령어 %d개 등록", len(synced))

    bot.setup_hook = sync_commands
