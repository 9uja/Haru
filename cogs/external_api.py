"""외부 API 연동 예시. Open-Meteo(키 불필요) 기반 /weather 명령어."""
from __future__ import annotations

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather code → 한국어 설명 (자주 쓰이는 코드만)
WMO_CODES = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "서리 안개",
    51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비",
    71: "약한 눈", 73: "눈", 75: "강한 눈",
    80: "약한 소나기", 81: "소나기", 82: "강한 소나기",
    95: "뇌우", 96: "우박 동반 뇌우", 99: "강한 우박 뇌우",
}


class ExternalAPI(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        await self.session.close()

    @app_commands.command(name="weather", description="도시의 현재 날씨를 조회합니다.")
    @app_commands.describe(city="도시 이름 (예: Seoul, 서울, Tokyo)")
    async def weather(self, interaction: discord.Interaction, city: str) -> None:
        await interaction.response.defer()

        async with self.session.get(
            GEOCODE_URL, params={"name": city, "count": 1, "language": "ko", "format": "json"}
        ) as resp:
            geo = await resp.json()

        results = geo.get("results")
        if not results:
            await interaction.followup.send(f"'{city}' 위치를 찾을 수 없습니다.", ephemeral=True)
            return

        place = results[0]
        lat, lon = place["latitude"], place["longitude"]
        label = ", ".join(filter(None, [place.get("name"), place.get("admin1"), place.get("country")]))

        async with self.session.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                "timezone": "auto",
            },
        ) as resp:
            data = await resp.json()

        current = data["current"]
        condition = WMO_CODES.get(current["weather_code"], "알 수 없음")

        embed = discord.Embed(title=f"{label} 날씨", description=condition, color=discord.Color.blue())
        embed.add_field(name="기온", value=f"{current['temperature_2m']}°C")
        embed.add_field(name="습도", value=f"{current['relative_humidity_2m']}%")
        embed.add_field(name="풍속", value=f"{current['wind_speed_10m']} m/s")
        embed.set_footer(text="데이터 제공: Open-Meteo")
        await interaction.followup.send(embed=embed)

    @weather.error
    async def weather_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        msg = f"날씨 조회 중 오류가 발생했습니다: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ExternalAPI(bot))
