"""Discord 봇 상시 프로세스.

docker-compose의 bot 서비스가 이 모듈을 실행한다.
  CMD ["python", "-m", "bot.main"]

버튼 인터랙션 처리 및 슬래시 커맨드를 담당한다.
(버튼 핸들러는 bot/interactions.py)
"""
import logging

import discord
from discord.ext import commands

from bot.interactions import setup_interactions
from shared.config import get_settings
from shared.logger import setup_logging

logger = logging.getLogger(__name__)


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        logger.info("봇 로그인 완료: %s (id=%s)", bot.user, bot.user.id)

    setup_interactions(bot)
    return bot


def main() -> None:
    setup_logging("bot")  # 콘솔 + logs/bot.log(최근 7일 보관)
    settings = get_settings()
    token = settings.discord.bot_token.get_secret_value()
    bot = create_bot()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
